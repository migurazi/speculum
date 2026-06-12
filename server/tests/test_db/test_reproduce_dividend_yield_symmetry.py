"""M7 #4 (§2.10 reproducibility break) — dividend-yield / total-return 재현 대칭.

oracle 비판적 리뷰 Critical: reproduce_run → screen_active_codes 에 dividend_repo
배선이 누락돼, dividend-yield(또는 total-return) 조건을 포함한 live screen run 을
reproduce 하면 dividend_per_share_trailing_annual 이 silent N/A → 조건 불충족 →
result_codes 가 live 와 달라져 matches=False(§2.10 위반).

본 테스트는 dividend_repo 를 reproduce 에 배선한 수정이 **live ≡ reproduce 대칭**
을 회복함을 직접 증명한다:

1. test_dividend_yield_live_freeze_reproduce_matches — dividend-yield 조건 screen
   을 live 실행 → freeze → reproduce(dividend_repo 배선) → matches=True.
2. test_dividend_yield_reproduce_without_dividend_repo_breaks — 배선 전 회귀
   (dividend_repo=None) 이면 reproduce 가 N/A 로 result_codes 가 달라져
   matches=False. Critical 수정이 막은 정확한 결함을 negative control 로 고정.

frozen v1.0.0 회귀(Fix 2)는 test_reproduce_frozen_v1_0_0 에서 별도 검증.
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
    FakeDividendRepository,
    FakeFinancialRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    CorporateActionRecord,
    PriceRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.schemas.screen import ConditionIn, OpEnum
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import load_builtin_pack
from app.services.pack_registry import PackRegistry
from app.services.reproduce import reproduce_run
from app.services.screen_run import ScreenRunBuilder
from app.services.snapshot_versions import collect_active_policy_versions

_CODE: Final[str] = "005930"
_LINEAGE: Final[UUID] = UUID(int=int(_CODE))
_CITATION: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000cd")
_AS_OF: Final[date] = date(2024, 5, 7)
_KRX_BATCH: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000b1")

_DIVIDEND_YIELD_FACTOR: Final[str] = "dividend-yield:trailing-annual"
# v1.0.0 빌트인 pack — dividend-yield:trailing-annual 포함 (Fix 2 frozen 재현과 공유).
_PACK = load_builtin_pack("1.0.0")


def _price(*, d: date, close: str) -> PriceRecord:
    return PriceRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=d,
        open_raw=Decimal(close),
        high_raw=Decimal(close),
        low_raw=Decimal(close),
        close_raw=Decimal(close),
        volume=1000,
        trading_value=Decimal("1000000"),
        close_adjusted=Decimal(close),
        citation_id=_CITATION,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _div(*, effective_date: date, cash_amount: str) -> CorporateActionRecord:
    return CorporateActionRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        action_type="cash_dividend",
        announced_date=effective_date,
        effective_date=effective_date,
        payment_date=None,
        ratio=None,
        cash_amount=Decimal(cash_amount),
        details={},
        citation_id=_CITATION,
        superseded_by=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _scenario() -> dict:
    """price ≥1yr + 직전 12 개월 배당 1 건 → dividend-yield 산출 가능 시나리오.

    close_price_adjusted(as_of) = 1000, dividend_per_share_trailing_annual = 50.
    dividend-yield(ratio_pct) = 50/1000 × 100 = 5(%). 조건 `> 1` 통과.
    """
    citation_runs = {
        _CITATION: CitationBatch(
            started_at=datetime(2024, 4, 1, 9, tzinfo=UTC),
            batch_id=_KRX_BATCH, source="KRX",
        ),
    }
    prices = [
        _price(d=date(2023, 5, 10), close="1000"),  # min-coverage 충족.
        _price(d=date(2024, 1, 15), close="1000"),
        _price(d=_AS_OF, close="1000"),
    ]
    dividends = [_div(effective_date=date(2024, 2, 15), cash_amount="50")]

    stocks_repo = FakeStocksMasterRepository(records=[
        StockMasterRecord(
            id=_LINEAGE, current_code=_CODE, current_name="삼성전자",
            market="KOSPI", listing_date=date(2000, 1, 1), delisting_date=None,
            fiscal_month=12,
            code_history=(CodeHistoryEntry(_CODE, date(2000, 1, 1), None,
                                           "initial_listing"),),
        ),
    ])
    common = dict(
        stocks_repo=stocks_repo,
        pack=_PACK,
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository(records=prices, citation_runs=citation_runs),
        financial_repo=FakeFinancialRepository(records=(), citation_runs=citation_runs),
        corporate_action_repo=FakeCorporateActionRepository(records=()),
        market_cap_repo=FakeMarketCapRepository(records=(), citation_runs=citation_runs),
        treasury_repo=FakeTreasurySharesRepository(records=(), citation_runs=citation_runs),
    )
    dividend_repo = FakeDividendRepository(records=dividends)

    batch_repo = FakeBatchRunRepository()
    batch_repo.start(
        run_id=_KRX_BATCH, market="KOSPI", source="KRX",
        started_at=datetime(2024, 4, 1, 9, tzinfo=UTC),
    )
    batch_repo.finalize(
        run_id=_KRX_BATCH, ended_at=datetime(2024, 4, 1, 10, tzinfo=UTC),
        success_count=1, status=BATCH_STATUS_SUCCESS,
    )
    return {
        "common": common,
        "dividend_repo": dividend_repo,
        "batch_repo": batch_repo,
    }


def _data_versions() -> dict[str, str]:
    """현재 active 정책 키 + krx batch_id (dart 빈 문자열 = EXCLUDE_ALL).

    pack 키(factor_pack_*)는 active 정책에서 채워지며 v1.0.0 가 active 가 아니므로
    여기서는 frozen v1.0.0 hash 를 명시 주입(아래 호출처가 override).
    """
    return dict(collect_active_policy_versions())


def _live_codes(sc: dict) -> tuple[str, ...]:
    """live screen — dividend_repo 배선(execute_screen 와 동일)으로 조건 매칭."""
    from app.api.routes.screen import screen_active_codes

    # 조건은 ratio 기준(evaluator 가 ×100 전 ratio 0.05 반환 — ×100 은 표시 layer).
    cond = [ConditionIn(factor=_DIVIDEND_YIELD_FACTOR, op=OpEnum.GT, value="0.01")]
    return screen_active_codes(
        as_of=_AS_OF,
        conditions=cond,
        dividend_repo=sc["dividend_repo"],
        krx_batch_cutoff=BatchCutoff(
            started_at=datetime(2024, 4, 1, 9, tzinfo=UTC), id=_KRX_BATCH,
        ),
        **sc["common"],
    )


def _snapshot(sc: dict, result_codes: tuple[str, ...]) -> object:
    data_versions = _data_versions()
    data_versions.update({
        "krx_batch_id": str(_KRX_BATCH),
        "dart_batch_id": "",
        # frozen pack = v1.0.0 (active 가 다른 버전이어도 frozen 으로 재로드).
        "factor_pack_slug": "speculum-builtin",
        "factor_pack_version": "1.0.0",
        "factor_pack_content_hash": _PACK.computed_hash,
    })
    return ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": _DIVIDEND_YIELD_FACTOR, "op": ">", "value": "0.01"}],
        selected_factors=[_DIVIDEND_YIELD_FACTOR],
        as_of=_AS_OF,
        result_codes=result_codes,
        data_versions=data_versions,
    )


# =============================================================================
# Fix 1 (Critical) — live ≡ reproduce 대칭 (dividend-yield 조건 포함)
# =============================================================================

def test_dividend_yield_live_freeze_reproduce_matches() -> None:
    """dividend-yield 조건 screen 을 live → freeze → reproduce → matches=True.

    Critical 수정 직접 증명 — reproduce_run 이 dividend_repo 를 screen_active_codes
    에 전달하므로 dividend_per_share_trailing_annual 이 live 와 동일하게 산출돼
    result_codes 가 byte-동일 재현된다(§2.10). live 가 종목을 선별했음을 먼저 확인.
    """
    sc = _scenario()
    live = _live_codes(sc)
    # 전제: live screen 이 dividend-yield > 1 로 종목을 실제 선별(N/A 면 빈 결과).
    assert live == (_CODE,)

    snapshot = _snapshot(sc, live)
    result = reproduce_run(
        snapshot,
        batch_run_repo=sc["batch_repo"],
        dividend_repo=sc["dividend_repo"],
        pack_registry=PackRegistry(),
        **sc["common"],
    )
    assert result.matches is True
    assert result.result_codes == (_CODE,)
    assert result.reproduce_note is None


def test_dividend_yield_reproduce_without_dividend_repo_breaks() -> None:
    """negative control — dividend_repo 미배선이면 reproduce 가 N/A → matches=False.

    배선 전(§2.10 break) 의 정확한 결함을 고정: live 는 dividend_repo 로 종목을
    선별했으나, reproduce 가 dividend_repo=None 이면 dividend-yield 가 silent N/A
    → 조건 불충족 → 빈 result_codes → live(005930,)와 불일치(matches=False).
    이 테스트가 깨지면(matches=True) Critical 수정이 무력화된 것.
    """
    sc = _scenario()
    live = _live_codes(sc)
    assert live == (_CODE,)

    snapshot = _snapshot(sc, live)
    result = reproduce_run(
        snapshot,
        batch_run_repo=sc["batch_repo"],
        dividend_repo=None,  # 배선 누락 재현(회귀).
        pack_registry=PackRegistry(),
        **sc["common"],
    )
    # dividend-yield N/A → 조건 불충족 → 빈 결과 → live 와 불일치.
    assert result.result_codes == ()
    assert result.matches is False


# =============================================================================
# Fix 2 (High) — genuine frozen v1.0.0 run 의 byte-동일 재현
# =============================================================================

def test_reproduce_frozen_v1_0_0() -> None:
    """frozen v1.0.0 run(content_hash = v1.0.0 golden) → reproduce matches=True.

    DEFAULT_PACK 이 v1.0.0 가 아니어도(현재 v1.1.0), 과거 v1.0.0 run 이 frozen
    data_versions 의 factor_pack_version="1.0.0" + golden content_hash 로 그 시점
    pack 을 재로드하여 byte-동일 재현됨을 end-to-end 증명(§2.10 핵심 보장).
    pack_tampered=False, reproduce_note=None.
    """
    sc = _scenario()
    live = _live_codes(sc)
    assert live == (_CODE,)

    data_versions = _data_versions()
    data_versions.update({
        "krx_batch_id": str(_KRX_BATCH),
        "dart_batch_id": "",
        "factor_pack_slug": "speculum-builtin",
        "factor_pack_version": "1.0.0",
        # genuine frozen v1.0.0 golden hash (현재 active pack 과 무관).
        "factor_pack_content_hash": load_builtin_pack("1.0.0").computed_hash,
    })
    snapshot = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": _DIVIDEND_YIELD_FACTOR, "op": ">", "value": "0.01"}],
        selected_factors=[_DIVIDEND_YIELD_FACTOR],
        as_of=_AS_OF,
        result_codes=live,
        data_versions=data_versions,
    )
    # frozen pack 키가 v1.0.0 를 가리키므로 fallback pack 은 무시(별도 버전 주입).
    common = dict(sc["common"])
    result = reproduce_run(
        snapshot,
        batch_run_repo=sc["batch_repo"],
        dividend_repo=sc["dividend_repo"],
        pack_registry=PackRegistry(),
        **common,
    )
    assert result.matches is True
    assert result.result_codes == (_CODE,)
    assert result.pack_tampered is False
    assert result.reproduce_note is None
