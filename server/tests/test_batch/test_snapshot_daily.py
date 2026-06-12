"""SnapshotDailyBatch 단위 테스트 — Fake repo + (optional) FakeSession.

stock_snapshots precompute 일배치의 producer 계약을 입증:
    1. 정상 run — universe × factor 만큼 snapshot 저장 (snapshot_repo fetch 확인).
    2. PRECOMPUTE citation + data_versions freeze + value_unit (session 주입 경로).
    3. idempotent — 같은 observed_date 재run → UPSERT (중복 안 쌓임).
    4. 빈 universe — snapshots_saved 0, run raise 안 함.
    5. 종목 평가 실패 격리 — 한 종목 evaluate raise 해도 나머지 진행.

평가 단순화: 작은 테스트 pack(factor 2개 — shares_issued field + 상수 없는 단순
field)을 직접 구성하고 market_cap 데이터만 채워 평가가 결정적이도록 한다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.models.source_citation import SourceKind
from app.repositories.batch_run_repository import FakeBatchRunRepository
from app.repositories.citation_repository import FakeCitationRepository
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeFinancialRepository,
    FakeMacroIndicatorRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeStockSnapshotRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    MarketCapRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import LoadedPack
from batch.snapshot_daily import SnapshotBatchSummary, SnapshotDailyBatch

OBSERVED = date(2024, 5, 7)

# 테스트 factor uuid (안정 — snapshot factor_uuid 매칭).
_F1_UUID = "018f9b00-0001-7000-8000-0000000000a1"
_F2_UUID = "018f9b00-0001-7000-8000-0000000000a2"


# =============================================================================
# Helpers
# =============================================================================

def _make_pack() -> LoadedPack:
    """factor 2개짜리 작은 테스트 pack — 둘 다 단순 field 참조(shares_issued)."""
    body = {
        "factors": [
            {
                "canonical_id": "test:shares-a",
                "uuid": _F1_UUID,
                "name": "shares A",
                "description": "발행주식수 A",
                "formula": {
                    "ast": {"field": "shares_issued"},
                    "inputs": ["shares_issued"],
                },
                "unit": "shares",
            },
            {
                "canonical_id": "test:shares-b",
                "uuid": _F2_UUID,
                "name": "shares B",
                "description": "발행주식수 B (다른 unit)",
                "formula": {
                    "ast": {"field": "shares_issued"},
                    "inputs": ["shares_issued"],
                },
                "unit": "count",
            },
        ]
    }
    return LoadedPack(
        body=body,
        computed_hash="testhash00000000",
        factor_count=2,
        pack_slug="test-snapshot-pack",
        version="1.0.0",
    )


def _stock(code: str, name: str, security_type: str = "common") -> StockMasterRecord:
    return StockMasterRecord(
        id=UUID(int=int(code)),
        current_code=code,
        current_name=name,
        market="KOSPI",
        listing_date=date(2000, 1, 1),
        delisting_date=None,
        fiscal_month=12,
        code_history=(
            CodeHistoryEntry(code, date(2000, 1, 1), None, "initial_listing"),
        ),
        security_type=security_type,
    )


def _market_cap(code: str, shares: int) -> MarketCapRecord:
    return MarketCapRecord(
        id=UUID(int=int(code) + 9_000_000),
        code=code,
        code_lineage_id=UUID(int=int(code)),
        effective_date=OBSERVED,
        market_cap=Decimal(shares * 10),
        shares_outstanding=shares,
        shares_treasury=None,
        citation_id=uuid4(),
        created_at=datetime.now(UTC),
    )


class _FakeNestedContext:
    """SAVEPOINT 흉내 — 항상 성공 (rollback 은 with-block 예외 전파로 충분)."""

    def __enter__(self) -> _FakeNestedContext:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        # 예외를 흡수하지 않음 — 호출자(run loop)의 except 가 종목 격리 처리.
        return False


class _EmptyScalars:
    """session.execute(...).scalars() 흉내 — 항상 빈 결과."""

    def first(self) -> None:
        return None


class _EmptyResult:
    def scalars(self) -> _EmptyScalars:
        return _EmptyScalars()


class _FakeSession:
    """session.flush / begin_nested / execute / get 최소 구현.

    collect_run_data_versions 의 batch_versions 조회(session.execute)는 빈 결과를
    반환하여 정책-only 키만 freeze 되게 한다(테스트 결정성 — DB 없이도 data_versions
    비어있지 않음 검증 가능). ecos 테스트 FakeSession 의 begin_nested/flush 동형.
    """

    def __init__(self) -> None:
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1

    def begin_nested(self) -> _FakeNestedContext:
        return _FakeNestedContext()

    def execute(self, *_args: Any, **_kwargs: Any) -> _EmptyResult:
        return _EmptyResult()

    def get(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _build_batch(
    *,
    stocks: list[StockMasterRecord],
    market_caps: list[MarketCapRecord],
    session: Any = None,
    citation_repo: FakeCitationRepository | None = None,
    snapshot_repo: FakeStockSnapshotRepository | None = None,
    evaluator: FactorEvaluator | None = None,
    batch_run_repo: Any = None,
) -> tuple[SnapshotDailyBatch, FakeStockSnapshotRepository, FakeCitationRepository]:
    snap = snapshot_repo if snapshot_repo is not None else FakeStockSnapshotRepository(records=[])
    cit = citation_repo if citation_repo is not None else FakeCitationRepository()
    batch = SnapshotDailyBatch(
        session=session,
        stocks_repo=FakeStocksMasterRepository(records=stocks),
        price_repo=FakePriceRepository(records=[]),
        financial_repo=FakeFinancialRepository(records=[]),
        market_cap_repo=FakeMarketCapRepository(records=market_caps),
        treasury_repo=FakeTreasurySharesRepository(records=[]),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        macro_repo=FakeMacroIndicatorRepository(records=[]),
        dividend_repo=None,
        snapshot_repo=snap,
        citation_repo=cit,
        pack=_make_pack(),
        evaluator=evaluator,
        batch_run_repo=batch_run_repo,
    )
    return batch, snap, cit


# =============================================================================
# 1. 정상 run — universe × factor 저장
# =============================================================================

def test_run_saves_universe_times_factor_snapshots() -> None:
    stocks = [_stock("005930", "삼성전자"), _stock("000660", "SK하이닉스")]
    mcs = [_market_cap("005930", 500), _market_cap("000660", 300)]
    batch, snap, _cit = _build_batch(stocks=stocks, market_caps=mcs)

    summary = batch.run(observed_date=OBSERVED)

    assert isinstance(summary, SnapshotBatchSummary)
    assert summary.universe_count == 2
    assert summary.factor_count == 2
    assert summary.success_count == 2
    assert summary.failure_count == 0
    assert summary.snapshots_saved == 2 * 2  # universe × factor

    # snapshot_repo 에서 종목별 factor 수만큼 fetch 확인.
    s1 = snap.fetch_snapshots_for_code("005930", as_of=OBSERVED)
    s2 = snap.fetch_snapshots_for_code("000660", as_of=OBSERVED)
    assert len(s1) == 2
    assert len(s2) == 2

    # value = shares_issued (factor 둘 다 shares_issued field).
    values_005930 = {r.factor_uuid: r.value for r in s1}
    assert values_005930[UUID(_F1_UUID)] == Decimal(500)
    assert values_005930[UUID(_F2_UUID)] == Decimal(500)


# =============================================================================
# 2. PRECOMPUTE citation + data_versions + value_unit (session 경로)
# =============================================================================

def test_run_precompute_citation_and_data_versions() -> None:
    # session 주입 — citation save 경로 + flush 활성화.
    session = _FakeSession()
    stocks = [_stock("005930", "삼성전자")]
    mcs = [_market_cap("005930", 500)]
    cit = FakeCitationRepository()
    batch, snap, _cit = _build_batch(
        stocks=stocks, market_caps=mcs, session=session, citation_repo=cit,
        batch_run_repo=FakeBatchRunRepository(),
    )

    summary = batch.run(observed_date=OBSERVED)

    # PRECOMPUTE citation 1개 save 됨.
    saved = cit.fetch_by_batch(summary.batch_id)
    assert len(saved) == 1
    citation = saved[0]
    assert citation.source is SourceKind.PRECOMPUTE
    assert citation.batch_id == summary.batch_id
    assert citation.effective_date == OBSERVED
    assert str(summary.batch_id) in citation.identifier
    assert OBSERVED.isoformat() in citation.identifier

    # 저장된 snapshot 의 citation_id = PRECOMPUTE citation.id.
    records = snap.fetch_snapshots_for_code("005930", as_of=OBSERVED)
    assert len(records) == 2
    for r in records:
        assert r.citation_id == citation.id
        # data_versions 비어있지 않음 (정책키 존재 — 정책-only 14키 이상).
        assert r.data_versions
        assert "factor_pack_content_hash" in r.data_versions

    # value_unit == factor["unit"].
    by_uuid = {r.factor_uuid: r for r in records}
    assert by_uuid[UUID(_F1_UUID)].value_unit == "shares"
    assert by_uuid[UUID(_F2_UUID)].value_unit == "count"

    # session.flush 가 종목별로 호출됨 (1종목 → 1회).
    assert session.flush_count == 1


# =============================================================================
# 3. idempotent — 같은 observed_date 재run → UPSERT (중복 안 쌓임)
# =============================================================================

def test_run_idempotent_upsert() -> None:
    stocks = [_stock("005930", "삼성전자")]
    mcs = [_market_cap("005930", 500)]
    snap = FakeStockSnapshotRepository(records=[])
    batch, _snap, _cit = _build_batch(
        stocks=stocks, market_caps=mcs, snapshot_repo=snap,
    )

    batch.run(observed_date=OBSERVED)
    # 2번째 run — 새 batch (citation_repo append-only, 새 batch_id 라 충돌 없음).
    batch2, _s2, _c2 = _build_batch(
        stocks=stocks, market_caps=mcs, snapshot_repo=snap,
    )
    batch2.run(observed_date=OBSERVED)

    # UPSERT — (code, as_of, factor) 키 overwrite. factor 수만큼만.
    records = snap.fetch_snapshots_for_code("005930", as_of=OBSERVED)
    assert len(records) == 2


# =============================================================================
# 4. 빈 universe — snapshots_saved 0, raise 안 함
# =============================================================================

def test_run_empty_universe() -> None:
    batch, snap, _cit = _build_batch(stocks=[], market_caps=[])

    summary = batch.run(observed_date=OBSERVED)

    assert summary.universe_count == 0
    assert summary.success_count == 0
    assert summary.failure_count == 0
    assert summary.snapshots_saved == 0


# =============================================================================
# 5. 종목 평가 실패 격리
# =============================================================================

class _FlakyEvaluator(FactorEvaluator):
    """특정 종목코드 평가 시 raise — 종목 격리 검증용."""

    def __init__(self, *, fail_code: str) -> None:
        super().__init__()
        self._fail_code = fail_code

    def evaluate(self, factor: dict, provider: Any, *, as_of: date, universe_distribution: Any = None) -> Any:  # type: ignore[override]
        if getattr(provider, "_code", None) == self._fail_code:
            raise RuntimeError("synthetic evaluate failure")
        return super().evaluate(factor, provider, as_of=as_of, universe_distribution=universe_distribution)


def test_run_isolates_failing_stock() -> None:
    stocks = [_stock("005930", "삼성전자"), _stock("000660", "SK하이닉스")]
    mcs = [_market_cap("005930", 500), _market_cap("000660", 300)]
    batch, snap, _cit = _build_batch(
        stocks=stocks, market_caps=mcs,
        evaluator=_FlakyEvaluator(fail_code="005930"),
    )

    summary = batch.run(observed_date=OBSERVED)

    # 실패 1종목 격리, 나머지 1종목 정상 진행.
    assert summary.failure_count == 1
    assert summary.success_count == 1
    assert summary.failures[0][0] == "005930"
    assert "RuntimeError" in summary.failures[0][1]

    # 성공 종목만 snapshot 저장 (factor 2개).
    assert snap.fetch_snapshots_for_code("000660", as_of=OBSERVED)
    assert not snap.fetch_snapshots_for_code("005930", as_of=OBSERVED)
    assert summary.snapshots_saved == 2  # 성공 1종목 × factor 2
