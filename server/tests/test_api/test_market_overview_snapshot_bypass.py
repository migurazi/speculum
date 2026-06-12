"""market_overview precompute read-bypass 테스트 (ⓓ slice 2b).

snapshot 으로 평가를 대체하는 serve-vs-recompute 로직(`_serve_overview_from_snapshot`)
+ compute_market_overview 통합(serve hit 시 live 평가 skip, 값이 집계에 반영)을 검증.

핵심 불변:
    - 전 overview factor hit + data_versions 일치 시에만 serve(all-or-nothing per code).
    - 일부 miss / data_versions stale / repo·dv 미주입 → None(live fallback, 회귀 0).
    - snapshot value None(미산정)은 na 로 집계(live is_na 와 동일).
    - market_overview 는 live observation(reproduce 없음) — §2.10 byte-reproduce 무관.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from app.api.routes.market import (
    _serve_overview_from_snapshot,
    compute_market_overview,
)
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
    StockMasterRecord,
    StockSnapshotRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.services.factor_pack import LoadedPack

_AS_OF = date(2024, 5, 7)
_CITATION = UUID("00000000-0000-0000-0000-0000000000ff")
_F_EPS = UUID("018f9b00-0001-7000-8000-00000000ee01")
_F_PER = UUID("018f9b00-0001-7000-8000-00000000ee02")
_DV = {"factor_pack_content_hash": "sha256:aa", "krx_batch_id": "k1",
       "evaluator_version": "1.0"}


def _snap(
    *, code: str, factor_uuid: UUID, value: Decimal | None,
    data_versions: dict | None = None,
) -> StockSnapshotRecord:
    return StockSnapshotRecord(
        stock_code=code, as_of_date=_AS_OF, factor_uuid=factor_uuid,
        value=value, value_unit="ratio", inputs={}, citation_id=_CITATION,
        computed_at=datetime(2024, 5, 7, 18, 0, tzinfo=UTC),
        data_versions=data_versions if data_versions is not None else dict(_DV),
    )


_OVERVIEW_IDS = ["eps:x", "per:x"]
_OVERVIEW_UUIDS = {"eps:x": _F_EPS, "per:x": _F_PER}


# =============================================================================
# _serve_overview_from_snapshot — serve-vs-recompute predicate
# =============================================================================

def test_serve_all_hit_and_dv_match() -> None:
    repo = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_F_EPS, value=Decimal("8000")),
        _snap(code="005930", factor_uuid=_F_PER, value=Decimal("12.5")),
    ])
    served = _serve_overview_from_snapshot(
        code="005930", as_of=_AS_OF, overview_ids=_OVERVIEW_IDS,
        overview_uuids=_OVERVIEW_UUIDS, snapshot_repo=repo,
        current_data_versions=dict(_DV),
    )
    assert served == {"eps:x": Decimal("8000"), "per:x": Decimal("12.5")}


def test_serve_value_none_preserved() -> None:
    """미산정(value None)도 serve — na 로 집계되도록 None 보존."""
    repo = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_F_EPS, value=Decimal("8000")),
        _snap(code="005930", factor_uuid=_F_PER, value=None),
    ])
    served = _serve_overview_from_snapshot(
        code="005930", as_of=_AS_OF, overview_ids=_OVERVIEW_IDS,
        overview_uuids=_OVERVIEW_UUIDS, snapshot_repo=repo,
        current_data_versions=dict(_DV),
    )
    assert served == {"eps:x": Decimal("8000"), "per:x": None}


def test_serve_partial_miss_returns_none() -> None:
    """일부 factor snapshot 부재 → None(전체 live, 부분 serve 안 함)."""
    repo = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_F_EPS, value=Decimal("8000")),
        # per snapshot 없음.
    ])
    served = _serve_overview_from_snapshot(
        code="005930", as_of=_AS_OF, overview_ids=_OVERVIEW_IDS,
        overview_uuids=_OVERVIEW_UUIDS, snapshot_repo=repo,
        current_data_versions=dict(_DV),
    )
    assert served is None


def test_serve_stale_data_versions_returns_none() -> None:
    """data_versions 불일치(정책/batch 세대 갱신) → None(recompute)."""
    repo = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_F_EPS, value=Decimal("8000"),
              data_versions={"krx_batch_id": "OLD"}),
        _snap(code="005930", factor_uuid=_F_PER, value=Decimal("12.5"),
              data_versions={"krx_batch_id": "OLD"}),
    ])
    served = _serve_overview_from_snapshot(
        code="005930", as_of=_AS_OF, overview_ids=_OVERVIEW_IDS,
        overview_uuids=_OVERVIEW_UUIDS, snapshot_repo=repo,
        current_data_versions=dict(_DV),  # 현재 dv 와 다름.
    )
    assert served is None


def test_serve_no_repo_or_dv_returns_none() -> None:
    """repo·current_dv 미주입(Fake-only/세션없음) → None(live, 회귀 0)."""
    repo = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_F_EPS, value=Decimal("8000")),
        _snap(code="005930", factor_uuid=_F_PER, value=Decimal("12.5")),
    ])
    assert _serve_overview_from_snapshot(
        code="005930", as_of=_AS_OF, overview_ids=_OVERVIEW_IDS,
        overview_uuids=_OVERVIEW_UUIDS, snapshot_repo=None,
        current_data_versions=dict(_DV),
    ) is None
    assert _serve_overview_from_snapshot(
        code="005930", as_of=_AS_OF, overview_ids=_OVERVIEW_IDS,
        overview_uuids=_OVERVIEW_UUIDS, snapshot_repo=repo,
        current_data_versions=None,
    ) is None


# =============================================================================
# compute_market_overview 통합 — serve hit 시 live 평가 skip + 값 집계 반영
# =============================================================================

class _RaisingEvaluator:
    """evaluate 호출 시 raise — serve 가 평가를 skip 했음을 입증하는 spy."""

    def evaluate(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("evaluate 가 호출됨 — snapshot serve 실패(평가 skip 안 됨)")


def _pack() -> LoadedPack:
    body = {
        "factors": [
            {"canonical_id": "eps:x", "uuid": str(_F_EPS), "name": "EPS",
             "description": "eps", "unit": "krw",
             "formula": {"ast": {"field": "shares_issued"},
                         "inputs": ["shares_issued"]}},
            {"canonical_id": "per:x", "uuid": str(_F_PER), "name": "PER",
             "description": "per", "unit": "ratio",
             "formula": {"ast": {"field": "shares_issued"},
                         "inputs": ["shares_issued"]}},
        ]
    }
    return LoadedPack(
        body=body, computed_hash="h", factor_count=2,
        pack_slug="t", version="1.0.0",
    )


def _stock(code: str) -> StockMasterRecord:
    return StockMasterRecord(
        id=UUID(int=int(code)), current_code=code, current_name=code,
        market="KOSPI", listing_date=date(2000, 1, 1), delisting_date=None,
        fiscal_month=12,
        code_history=(CodeHistoryEntry(code, date(2000, 1, 1), None,
                                       "initial_listing"),),
    )


def _monkeypatch_overview_ids(monkeypatch) -> None:
    """DEFAULT_DISPLAY_FACTORS 를 테스트 pack 의 2 factor 로 한정."""
    import app.api.routes.market as market_mod
    monkeypatch.setattr(market_mod, "DEFAULT_DISPLAY_FACTORS", ("eps:x", "per:x"))


def test_compute_serve_skips_live_evaluation(monkeypatch) -> None:
    """전 종목·전 factor snapshot hit → live evaluate 호출 0(RaisingEvaluator 미발동),
    snapshot value 가 집계에 반영."""
    _monkeypatch_overview_ids(monkeypatch)
    snaps = []
    for code in ("000001", "000002"):
        snaps.append(_snap(code=code, factor_uuid=_F_EPS, value=Decimal("8000")))
        snaps.append(_snap(code=code, factor_uuid=_F_PER, value=Decimal("10")))
    out = compute_market_overview(
        as_of=_AS_OF,
        stocks_repo=FakeStocksMasterRepository(records=[
            _stock("000001"), _stock("000002"),
        ]),
        pack=_pack(),
        evaluator=_RaisingEvaluator(),  # serve 실패 시 raise.
        price_repo=FakePriceRepository(records=[]),
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=FakeMarketCapRepository(records=[]),
        treasury_repo=FakeTreasurySharesRepository(records=[]),
        macro_repo=FakeMacroIndicatorRepository(records=[]),
        snapshot_repo=FakeStockSnapshotRepository(snaps),
        current_data_versions=dict(_DV),
    )
    assert out.universe_count == 2
    by_id = {f.canonical_id: f for f in out.factors}
    # snapshot value 8000 두 종목 → eps mean/min/max 8000.
    assert by_id["eps:x"].count == 2
    assert by_id["eps:x"].min == "8000"
    assert by_id["per:x"].count == 2
    assert by_id["per:x"].min == "10"


def test_compute_stale_falls_back_to_live(monkeypatch) -> None:
    """data_versions stale → live 평가(RaisingEvaluator 발동 → raise)로 fallback 확인."""
    import pytest
    _monkeypatch_overview_ids(monkeypatch)
    snaps = [
        _snap(code="000001", factor_uuid=_F_EPS, value=Decimal("8000"),
              data_versions={"krx_batch_id": "OLD"}),
        _snap(code="000001", factor_uuid=_F_PER, value=Decimal("10"),
              data_versions={"krx_batch_id": "OLD"}),
    ]
    with pytest.raises(AssertionError, match="evaluate 가 호출됨"):
        compute_market_overview(
            as_of=_AS_OF,
            stocks_repo=FakeStocksMasterRepository(records=[_stock("000001")]),
            pack=_pack(),
            evaluator=_RaisingEvaluator(),
            price_repo=FakePriceRepository(records=[]),
            financial_repo=FakeFinancialRepository(records=[]),
            corporate_action_repo=FakeCorporateActionRepository(records=[]),
            market_cap_repo=FakeMarketCapRepository(records=[]),
            treasury_repo=FakeTreasurySharesRepository(records=[]),
            macro_repo=FakeMacroIndicatorRepository(records=[]),
            snapshot_repo=FakeStockSnapshotRepository(snaps),
            current_data_versions=dict(_DV),  # snapshot 의 OLD 와 불일치 → live.
        )
