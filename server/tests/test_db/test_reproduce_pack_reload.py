"""ADR-0025 D5 — reproduce 의 frozen data_versions 기반 pack 재로드 회귀.

reproduce_run 은 운영 활성 pack(DEFAULT_PACK)을 무조건 사용하지 않고, frozen
data_versions 의 factor_pack_slug/version/content_hash 로 PackRegistry 가 그 시점
pack 을 재로드한다. 본 테스트는 그 재로드 경로의 정합성과 fail-loud 를 검증:

1. frozen 빌트인 pack 재로드 hash == frozen data_versions["factor_pack_content_hash"]
   + byte-동일 재현 (matches=True) — AC-M2-C-05 의 pack 재로드판.
2. frozen content_hash 변조 → matches=False + reproduce_note (fail-loud, pack 변조).
3. 미지원 custom slug → matches=False + reproduce_note (frozen pack 재로드 불가).
4. pack 키 부재 (구 run) → fallback pack 으로 정상 재현 (하위호환).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS
from app.repositories.batch_run_repository import (
    BatchCutoff,
    CitationBatch,
    FakeBatchRunRepository,
)
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeFinancialRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    FinancialRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.schemas.screen import ConditionIn, OpEnum
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK, compute_pack_hash
from app.services.pack_registry import PackRegistry
from app.services.reproduce import reproduce_run
from app.services.screen_run import ScreenRunBuilder
from app.services.snapshot_versions import collect_active_policy_versions

_CODE: Final[str] = "005930"
_EPS_ACCOUNT: Final[str] = "basic_eps"
_EPS_FACTOR_ID: Final[str] = "eps:basic-ttm-consolidated-ifrs"
_AS_OF: Final[date] = date(2024, 5, 7)
_DART_BATCH1: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000a1")
_DART_BATCH2: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000a2")
_C1: Final[UUID] = UUID("00000000-0000-0000-0000-0000000001a1")
_C2: Final[UUID] = UUID("00000000-0000-0000-0000-0000000001a2")


def _eps(
    *,
    fiscal_period: str,
    value: str,
    effective_date: date,
    citation_id: UUID,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> FinancialRecord:
    return FinancialRecord(
        id=record_id or uuid4(),
        code=_CODE,
        code_lineage_id=UUID(int=int(_CODE)),
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        account=_EPS_ACCOUNT,
        value=Decimal(value),
        unit="krw",
        ifrs_type="consolidated",
        citation_id=citation_id,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


def _build_scenario() -> dict:
    """EPS 정정 시나리오 공용 fixture — frozen batch1 = EPS 4000, batch2 = 정정.

    Returns dict: common(repos+pack+evaluator), batch_repo, frozen_codes.
    """
    citation_runs = {
        _C1: CitationBatch(
            started_at=datetime(2024, 4, 1, 9, tzinfo=UTC),
            batch_id=_DART_BATCH1, source="DART",
        ),
        _C2: CitationBatch(
            started_at=datetime(2024, 5, 5, 9, tzinfo=UTC),
            batch_id=_DART_BATCH2, source="DART",
        ),
    }
    periods = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    eff = [date(2023, 5, 15), date(2023, 8, 14), date(2023, 11, 14),
           date(2024, 3, 30)]
    records: list[FinancialRecord] = []
    last_q4_id = None
    for fp, e in zip(periods, eff, strict=True):
        rid = uuid4()
        if fp == "2023Q4":
            last_q4_id = rid
        records.append(_eps(
            fiscal_period=fp, value="1000", effective_date=e,
            citation_id=_C1, record_id=rid,
        ))
    correction = _eps(
        fiscal_period="2023Q4", value="100", effective_date=date(2024, 3, 30),
        citation_id=_C2, created_at=datetime(2024, 5, 5, tzinfo=UTC),
    )
    assert last_q4_id is not None
    records = [
        _eps(
            fiscal_period=r.fiscal_period, value=str(r.value),
            effective_date=r.effective_date, citation_id=r.citation_id,
            record_id=r.id,
            superseded_by=correction.id if r.id == last_q4_id else None,
            created_at=r.created_at,
        )
        for r in records
    ]
    records.append(correction)

    fin_repo = FakeFinancialRepository(records, citation_runs=citation_runs)
    stocks_repo = FakeStocksMasterRepository(records=[
        StockMasterRecord(
            id=UUID(int=int(_CODE)), current_code=_CODE, current_name="삼성전자",
            market="KOSPI", listing_date=date(2000, 1, 1), delisting_date=None,
            fiscal_month=12,
            code_history=(CodeHistoryEntry(_CODE, date(2000, 1, 1), None,
                                           "initial_listing"),),
        ),
    ])
    common = dict(
        stocks_repo=stocks_repo,
        pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository(records=(), citation_runs=citation_runs),
        financial_repo=fin_repo,
        corporate_action_repo=FakeCorporateActionRepository(records=()),
        market_cap_repo=FakeMarketCapRepository(records=(), citation_runs=citation_runs),
        treasury_repo=FakeTreasurySharesRepository(records=(), citation_runs=citation_runs),
    )

    from app.api.routes.screen import screen_active_codes
    cutoff1 = BatchCutoff(started_at=datetime(2024, 4, 1, 9, tzinfo=UTC), id=_DART_BATCH1)
    cond = [ConditionIn(factor=_EPS_FACTOR_ID, op=OpEnum.GT, value="3999")]
    frozen_codes = screen_active_codes(
        as_of=_AS_OF, conditions=cond, dart_batch_cutoff=cutoff1, **common,
    )
    assert frozen_codes == ("005930",)

    batch_repo = FakeBatchRunRepository()
    batch_repo.start(
        run_id=_DART_BATCH1, market=None, source="DART",
        started_at=datetime(2024, 4, 1, 9, tzinfo=UTC),
    )
    batch_repo.finalize(
        run_id=_DART_BATCH1, ended_at=datetime(2024, 4, 1, 10, tzinfo=UTC),
        success_count=1, status=BATCH_STATUS_SUCCESS,
    )
    return {"common": common, "batch_repo": batch_repo, "frozen_codes": frozen_codes}


def _data_versions_with_pack(extra: dict[str, str]) -> dict[str, str]:
    """현재 active 정책 12 키 + batch_id 키 merge (factor_pack 3 키 포함)."""
    merged = dict(collect_active_policy_versions())
    merged.update(extra)
    return merged


# =============================================================================
# 1. frozen 빌트인 pack 재로드 hash == frozen hash + byte-동일 재현
# =============================================================================

def test_frozen_builtin_pack_reload_hash_matches_and_byte_identical() -> None:
    """frozen data_versions 의 factor_pack_content_hash == 재로드 pack hash + 재현.

    AC-M2-C-05 의 pack 재로드판 — reproduce 가 registry 로 재로드한 pack 의
    computed_hash 가 frozen data_versions["factor_pack_content_hash"] 와 동일하고,
    재현 result_codes 가 저장값과 byte-동일 (matches=True).
    """
    sc = _build_scenario()
    data_versions = _data_versions_with_pack({
        "dart_batch_id": str(_DART_BATCH1), "krx_batch_id": "",
    })
    # frozen hash 가 실제 빌트인 pack hash 와 동일함을 명시 (전제).
    assert data_versions["factor_pack_content_hash"] == DEFAULT_PACK.computed_hash
    assert data_versions["factor_pack_slug"] == "speculum-builtin"

    snapshot = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": _EPS_FACTOR_ID, "op": ">", "value": "3999"}],
        selected_factors=[_EPS_FACTOR_ID],
        as_of=_AS_OF,
        result_codes=sc["frozen_codes"],
        data_versions=data_versions,
    )
    registry = PackRegistry()
    # registry 가 재로드한 pack 의 hash == frozen hash.
    reloaded = registry.resolve("speculum-builtin", "1.0.0")
    assert reloaded is not None
    assert reloaded.computed_hash == data_versions["factor_pack_content_hash"]

    result = reproduce_run(
        snapshot, batch_run_repo=sc["batch_repo"], pack_registry=registry,
        **sc["common"],
    )
    assert result.matches is True
    assert result.result_codes == ("005930",)
    assert result.reproduce_note is None


# =============================================================================
# 2. frozen content_hash 변조 → matches=False + reproduce_note (fail-loud)
# =============================================================================

def test_tampered_frozen_pack_hash_fails_loud() -> None:
    """frozen factor_pack_content_hash 변조 → matches=False + note (pack 변조)."""
    sc = _build_scenario()
    data_versions = _data_versions_with_pack({
        "dart_batch_id": str(_DART_BATCH1), "krx_batch_id": "",
        "factor_pack_content_hash": "sha256:" + "0" * 64,  # 변조.
    })
    snapshot = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": _EPS_FACTOR_ID, "op": ">", "value": "3999"}],
        selected_factors=[_EPS_FACTOR_ID],
        as_of=_AS_OF,
        result_codes=sc["frozen_codes"],
        data_versions=data_versions,
    )
    result = reproduce_run(
        snapshot, batch_run_repo=sc["batch_repo"], pack_registry=PackRegistry(),
        **sc["common"],
    )
    assert result.matches is False
    assert result.result_codes == ()
    assert result.reproduce_note is not None
    assert "content_hash" in result.reproduce_note


# =============================================================================
# 3. custom slug + export body 부재 → matches=False + reproduce_note (body 필요)
# =============================================================================

def test_custom_slug_without_body_fails_with_note() -> None:
    """frozen factor_pack_slug 가 custom 인데 export body 부재 → matches=False + note.

    Phase 2c (ADR-0025 D5) — custom run 재현은 export body 의 pack 정의를 요구한다
    (user/DB 무관 자기완결, ADR-0021 D5). reproduce_run 에 custom_pack_body 미전달
    (구 export / body 미동봉)이면 그 slug 의 정본을 재구성할 근거가 없어 재현 불가.
    """
    sc = _build_scenario()
    data_versions = _data_versions_with_pack({
        "dart_batch_id": str(_DART_BATCH1), "krx_batch_id": "",
        "factor_pack_slug": "user/custom-deferred",
        "factor_pack_version": "1.0.0",
        # custom slug + body 미전달 → "body 필요" 로 matches=False.
    })
    snapshot = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": _EPS_FACTOR_ID, "op": ">", "value": "3999"}],
        selected_factors=[_EPS_FACTOR_ID],
        as_of=_AS_OF,
        result_codes=sc["frozen_codes"],
        data_versions=data_versions,
    )
    result = reproduce_run(
        snapshot, batch_run_repo=sc["batch_repo"], pack_registry=PackRegistry(),
        **sc["common"],
    )
    assert result.matches is False
    assert result.result_codes == ()
    assert result.reproduce_note is not None
    assert "body" in result.reproduce_note


# =============================================================================
# 4. pack 키 부재 (구 run) → fallback pack 으로 정상 재현 (하위호환)
# =============================================================================

def test_missing_pack_keys_uses_fallback_pack() -> None:
    """factor_pack 키 부재 구 run → fallback pack(DEFAULT_PACK)으로 재현 (하위호환).

    test_reproduction.py 의 기존 시나리오와 동형 — data_versions 에 batch_id 만,
    factor_pack 키 없음. registry 재로드 skip, 전달 pack 사용 → matches=True.
    """
    sc = _build_scenario()
    snapshot = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": _EPS_FACTOR_ID, "op": ">", "value": "3999"}],
        selected_factors=[_EPS_FACTOR_ID],
        as_of=_AS_OF,
        result_codes=sc["frozen_codes"],
        data_versions={"dart_batch_id": str(_DART_BATCH1), "krx_batch_id": ""},
    )
    result = reproduce_run(
        snapshot, batch_run_repo=sc["batch_repo"], pack_registry=PackRegistry(),
        **sc["common"],
    )
    assert result.matches is True
    assert result.result_codes == ("005930",)
    assert result.reproduce_note is None


# =============================================================================
# 5. custom slug + export body 동봉 → resolve_from_body 재구성 byte-동일 (Phase 2c)
# =============================================================================

def _custom_pack_body_from_eps() -> dict:
    """빌트인 EPS factor 정의를 custom slug pack 으로 재포장 (봉인 hash 채움).

    _build_scenario 의 financial repo 가 EPS 를 그대로 평가하므로, 같은 factor
    정의(canonical_id=_EPS_FACTOR_ID)를 custom slug 로 감싸면 빌트인 run 과
    동일 result_codes 를 산출(custom 재현의 byte-동일 검증용).
    """
    eps_factor = next(
        f for f in DEFAULT_PACK.body["factors"] if f["canonical_id"] == _EPS_FACTOR_ID
    )
    body = {
        "$schema": "https://speculum.dev/schemas/factor-pack-v1.json",
        "type": "factor-pack",
        "pack_slug": "user/repro-eps",
        "version": "1.0.0",
        "publisher": "tester",
        "license": "MIT",
        "created_at": "2026-01-01",
        "citation": {"title": "Repro EPS Custom Pack", "publisher": "tester"},
        "factors": [eps_factor],
        "content_hash": "sha256:" + "0" * 64,
    }
    body["content_hash"] = compute_pack_hash(body)
    return body


def _custom_data_versions(body: dict) -> dict[str, str]:
    """custom pack body 의 slug/version/content_hash 로 frozen data_versions 구성."""
    merged = dict(collect_active_policy_versions())
    merged.update({
        "dart_batch_id": str(_DART_BATCH1), "krx_batch_id": "",
        "factor_pack_slug": body["pack_slug"],
        "factor_pack_version": body["version"],
        "factor_pack_content_hash": body["content_hash"],
    })
    return merged


def test_custom_slug_with_export_body_reproduces_byte_identical() -> None:
    """custom slug frozen run + export body 동봉 → resolve_from_body 재현 matches=True."""
    sc = _build_scenario()
    body = _custom_pack_body_from_eps()
    data_versions = _custom_data_versions(body)
    snapshot = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": _EPS_FACTOR_ID, "op": ">", "value": "3999"}],
        selected_factors=[_EPS_FACTOR_ID],
        as_of=_AS_OF,
        result_codes=sc["frozen_codes"],
        data_versions=data_versions,
    )
    result = reproduce_run(
        snapshot, batch_run_repo=sc["batch_repo"], pack_registry=PackRegistry(),
        custom_pack_body=body,
        **sc["common"],
    )
    assert result.matches is True
    assert result.result_codes == ("005930",)
    assert result.reproduce_note is None


def test_custom_slug_tampered_export_body_fails_loud() -> None:
    """custom export body 변조(content_hash 불일치) → matches=False fail-loud."""
    sc = _build_scenario()
    body = _custom_pack_body_from_eps()
    data_versions = _custom_data_versions(body)
    snapshot = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": _EPS_FACTOR_ID, "op": ">", "value": "3999"}],
        selected_factors=[_EPS_FACTOR_ID],
        as_of=_AS_OF,
        result_codes=sc["frozen_codes"],
        data_versions=data_versions,
    )
    # body 변조 — content_hash 는 그대로 두고 publisher 변경 → validate_hash 불일치.
    tampered = {**body, "publisher": "tampered-attacker"}
    result = reproduce_run(
        snapshot, batch_run_repo=sc["batch_repo"], pack_registry=PackRegistry(),
        custom_pack_body=tampered,
        **sc["common"],
    )
    assert result.matches is False
    assert result.result_codes == ()
    assert result.reproduce_note is not None
