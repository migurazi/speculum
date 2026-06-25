"""db_field_provider 단위 테스트 — M1-A T50 field-resolution 레지스트리.

테스트 매트릭스:
1. financial scalar — latest active fiscal_period value
2. financial scalar — 정정공시 chain (active row 만, stale 0)
3. financial series — strict (정확히 n 개)
4. financial series — n 개 미만이면 빈 sequence (partial 금지)
5. financial annual — trailing 4 분기 strict 합
6. financial annual — 4 분기 미만이면 N/A
7. financial period_begin — 직전 분기말 (분기초 자본)
8. financial period_begin — 직전 분기 부재 시 N/A
9. close_price_adjusted — 직전 영업일 보정 종가
10. close_price_adjusted — corporate action 보정 (read-time)
11. close_price_adjusted — 가격 결손 시 None
12. unsupported_m0 field (shares / market_cap / dividend) → None / 빈 series
19. trading_value_20d_avg — KRX 20 영업일 평균 (full / 결손 N/A / 캘린더 부족 N/A)
13. unknown field → UnknownFieldError
14. PIT — effective_date > as_of 인 record 미사용 (look-ahead 0)
15. ifrs_type 필터 — separate 는 consolidated field 에서 제외
16. request-scoped 캐싱 — 같은 field 재호출 시 repository 1 회만
17. as_of 보유 — FieldProvider Protocol invariant
18. end-to-end — FactorEvaluator + DbFieldProvider 로 실제 EPS 산출
20. ECOS macro_indicator — ecos_base_rate / ecos_cpi fetch_latest.value 해소
21. ECOS macro_indicator — macro_repo 미주입 (None) 이면 정식 N/A
22. ECOS vintage PIT — as_of < vintage_date 인 record 제외 (look-ahead 0)
23. ECOS 잠정→확정 재현 — as_of 기준 맞는 vintage 선택
24. ECOS field 를 입력으로 쓰는 mini factor end-to-end 평가
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeDividendRepository,
    FakeFinancialRepository,
    FakeMacroIndicatorRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    CorporateActionRecord,
    FinancialRecord,
    MacroIndicatorRecord,
    MarketCapRecord,
    PriceRecord,
    TreasurySharesRecord,
)
from app.services.db_field_provider import (
    FIELD_RESOLUTIONS,
    DbFieldProvider,
)
from app.services.factor_evaluator import FactorEvaluator, UnknownFieldError
from app.services.krx_calendar import DEFAULT_CALENDAR
from app.services.total_return_adjuster import TotalReturnAdjuster

_CODE = "005930"
_LINEAGE = UUID("00000000-0000-0000-0000-0000000000aa")
_CITATION = UUID("00000000-0000-0000-0000-0000000000bb")
_NET_INCOME_ACCOUNT = "net_income_attributable_to_owners"
_EQUITY_ACCOUNT = "equity_attributable_to_owners"
_EPS_ACCOUNT = "basic_eps"


# =============================================================================
# Builders
# =============================================================================

def _fin(
    *,
    account: str,
    fiscal_period: str,
    value: str,
    effective_date: date,
    ifrs_type: str = "consolidated",
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> FinancialRecord:
    return FinancialRecord(
        id=record_id or uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        account=account,
        value=Decimal(value),
        unit="krw",
        ifrs_type=ifrs_type,
        citation_id=_CITATION,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


def _price(*, d: date, close: str, trading_value: str = "1000000") -> PriceRecord:
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
        trading_value=Decimal(trading_value),
        close_adjusted=Decimal(close),
        citation_id=_CITATION,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _mc(
    *,
    d: date,
    market_cap: str,
    shares_outstanding: int,
    shares_treasury: int | None = None,
    created_at: datetime | None = None,
) -> MarketCapRecord:
    return MarketCapRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=d,
        market_cap=Decimal(market_cap),
        shares_outstanding=shares_outstanding,
        shares_treasury=shares_treasury,
        citation_id=_CITATION,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


def _treasury(
    *,
    fiscal_period: str,
    effective_date: date,
    shares_treasury: int,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> TreasurySharesRecord:
    return TreasurySharesRecord(
        id=record_id or uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        shares_treasury=shares_treasury,
        citation_id=_CITATION,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


def _div(
    *,
    effective_date: date,
    announced_date: date | None = None,
    cash_amount: str | None = None,
    per_share_detail: str | None = None,
    record_id: UUID | None = None,
    superseded_by: UUID | None = None,
    created_at: datetime | None = None,
) -> CorporateActionRecord:
    """cash_dividend record builder — total return / 주당배당 집계 테스트용."""
    details: dict[str, object] = {}
    if per_share_detail is not None:
        details["per_share"] = per_share_detail
    return CorporateActionRecord(
        id=record_id or uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        action_type="cash_dividend",
        announced_date=announced_date or effective_date,
        effective_date=effective_date,
        payment_date=None,
        ratio=None,
        cash_amount=Decimal(cash_amount) if cash_amount is not None else None,
        details=details,
        citation_id=_CITATION,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


def _provider(
    *,
    as_of: date,
    financials: list[FinancialRecord] | None = None,
    prices: list[PriceRecord] | None = None,
    actions: list[CorporateActionRecord] | None = None,
    market_caps: list[MarketCapRecord] | None = None,
    treasury: list[TreasurySharesRecord] | None = None,
    macro_records: list[MacroIndicatorRecord] | None = None,
    dividends: list[CorporateActionRecord] | None = None,
    total_return_adjuster: TotalReturnAdjuster | None = None,
    factor_pack: object | None = None,
    evaluator: FactorEvaluator | None = None,
) -> DbFieldProvider:
    return DbFieldProvider(
        code=_CODE,
        as_of=as_of,
        price_repo=FakePriceRepository(records=prices or []),
        financial_repo=FakeFinancialRepository(records=financials or []),
        corporate_action_repo=FakeCorporateActionRepository(records=actions or []),
        market_cap_repo=(
            FakeMarketCapRepository(records=market_caps)
            if market_caps is not None
            else None
        ),
        treasury_repo=(
            FakeTreasurySharesRepository(records=treasury)
            if treasury is not None
            else None
        ),
        macro_repo=(
            FakeMacroIndicatorRepository(records=macro_records)
            if macro_records is not None
            else None
        ),
        dividend_repo=(
            FakeDividendRepository(records=dividends)
            if dividends is not None
            else None
        ),
        # M7 #4 fix5 both-or-neither — production 배선은 dividend_repo 와
        # total_return_adjuster 가 항상 함께. helper 도 dividends 주입 시 adjuster
        # 자동 배선(명시 override 가능). dividend_per_share_trailing_annual 처럼
        # adjuster 를 안 쓰는 field 도 production 과 동일하게 둘 다 배선된 상태로
        # 평가(both-or-neither assert 충족). 둘 다 None(미주입)은 그대로 N/A.
        total_return_adjuster=(
            total_return_adjuster
            if total_return_adjuster is not None
            else (TotalReturnAdjuster() if dividends is not None else None)
        ),
        factor_pack=factor_pack,
        evaluator=evaluator,
    )


# 4 분기 net_income series — 2023Q1~Q4, 공시일 보수 추정 (분기 종료 +45/90 일).
#
# **B1 (ROADMAP_v2 V1b)**: net_income 은 FLOW 계정이므로 DART 분기보고서는
# 회계연도 **누적(YTD)** 값으로 보고한다(1분기=3M, 반기=6M, 3분기=9M, 사업보고서=12M).
# 따라서 fixture 를 누적값으로 seed 한다: 의도한 standalone(분기단독) = [100,200,300,400]
# 이 나오도록 누적 = [100, 300, 600, 1000] 을 seed → `_resolve_financial_series` 가
# 누적→standalone 변환(누적[q] − 누적[q-1], 1분기는 누적=standalone)하여
# (100, 200, 300, 400) 반환, TTM 합 = 1000.
def _four_quarter_net_income() -> list[FinancialRecord]:
    return [
        _fin(account=_NET_INCOME_ACCOUNT, fiscal_period="2023Q1",
             value="100", effective_date=date(2023, 5, 15)),
        _fin(account=_NET_INCOME_ACCOUNT, fiscal_period="2023Q2",
             value="300", effective_date=date(2023, 8, 14)),
        _fin(account=_NET_INCOME_ACCOUNT, fiscal_period="2023Q3",
             value="600", effective_date=date(2023, 11, 14)),
        _fin(account=_NET_INCOME_ACCOUNT, fiscal_period="2023Q4",
             value="1000", effective_date=date(2024, 3, 30)),
    ]


# =============================================================================
# 1-2. financial scalar
# =============================================================================

def test_financial_scalar_latest_period() -> None:
    """가장 최근 fiscal_period 의 active value."""
    financials = [
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q3",
             value="5000", effective_date=date(2023, 11, 14)),
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
             value="6000", effective_date=date(2024, 3, 30)),
    ]
    provider = _provider(as_of=date(2024, 5, 1), financials=financials)
    value = provider.get_scalar(
        "equity_attributable_to_common_consolidated_ifrs"
    )
    assert value == Decimal("6000")


def test_financial_scalar_uses_active_chain_not_stale() -> None:
    """정정공시 chain — as_of 시점 active row 만 (stale 0).

    2023Q4 원 row (value=6000, eff 2024-03-30) 가 정정 row (value=6500,
    eff 2024-04-20) 로 supersede. as_of=2024-05-01 이면 정정 row 가 active.
    """
    old_id = UUID("00000000-0000-0000-0000-000000000010")
    new_id = UUID("00000000-0000-0000-0000-000000000011")
    financials = [
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
             value="6000", effective_date=date(2024, 3, 30),
             record_id=old_id, superseded_by=new_id),
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
             value="6500", effective_date=date(2024, 4, 20),
             record_id=new_id),
    ]
    provider = _provider(as_of=date(2024, 5, 1), financials=financials)
    value = provider.get_scalar(
        "equity_attributable_to_common_consolidated_ifrs"
    )
    # 정정 row 가 active.
    assert value == Decimal("6500")


def test_financial_scalar_active_chain_before_correction() -> None:
    """정정 공시 전 시점 (as_of < 정정 effective_date) 은 원 row active."""
    old_id = UUID("00000000-0000-0000-0000-000000000010")
    new_id = UUID("00000000-0000-0000-0000-000000000011")
    financials = [
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
             value="6000", effective_date=date(2024, 3, 30),
             record_id=old_id, superseded_by=new_id),
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
             value="6500", effective_date=date(2024, 4, 20),
             record_id=new_id),
    ]
    # as_of=2024-04-10 — 정정 (4-20) 전이므로 원 row active.
    provider = _provider(as_of=date(2024, 4, 10), financials=financials)
    value = provider.get_scalar(
        "equity_attributable_to_common_consolidated_ifrs"
    )
    assert value == Decimal("6000")


# =============================================================================
# 3-4. financial series (strict)
# =============================================================================

def test_financial_series_strict_exact_n() -> None:
    """정확히 n 분기 — fiscal_period asc."""
    provider = _provider(
        as_of=date(2024, 5, 1), financials=_four_quarter_net_income(),
    )
    series = provider.get_quarterly_series(
        "net_income_attributable_consolidated_ifrs", n=4,
    )
    assert series == (
        Decimal("100"), Decimal("200"), Decimal("300"), Decimal("400"),
    )


def test_financial_series_insufficient_returns_empty() -> None:
    """n 개 미만이면 빈 sequence (partial 금지)."""
    financials = _four_quarter_net_income()[:2]  # 2 분기만
    provider = _provider(as_of=date(2024, 5, 1), financials=financials)
    series = provider.get_quarterly_series(
        "net_income_attributable_consolidated_ifrs", n=4,
    )
    assert series == ()


# =============================================================================
# 5-6. financial annual (trailing 4 분기 strict 합)
# =============================================================================

def test_financial_annual_sums_four_quarters() -> None:
    provider = _provider(
        as_of=date(2024, 5, 1), financials=_four_quarter_net_income(),
    )
    value = provider.get_scalar(
        "net_income_attributable_consolidated_ifrs_annual"
    )
    assert value == Decimal("1000")  # 100+200+300+400


def test_financial_annual_insufficient_returns_none() -> None:
    """4 분기 미만이면 N/A (None)."""
    financials = _four_quarter_net_income()[:3]
    provider = _provider(as_of=date(2024, 5, 1), financials=financials)
    value = provider.get_scalar(
        "net_income_attributable_consolidated_ifrs_annual"
    )
    assert value is None


# =============================================================================
# 7-8. financial period_begin (직전 분기말)
# =============================================================================

def test_financial_period_begin_uses_prior_quarter() -> None:
    """period_begin = 직전 분기말 (분기초 자본)."""
    financials = [
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q3",
             value="5000", effective_date=date(2023, 11, 14)),
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
             value="6000", effective_date=date(2024, 3, 30)),
    ]
    provider = _provider(as_of=date(2024, 5, 1), financials=financials)
    value = provider.get_scalar(
        "equity_attributable_to_common_consolidated_ifrs_period_begin"
    )
    # 최근 분기 (2023Q4) 의 직전 (2023Q3) 값.
    assert value == Decimal("5000")


def test_financial_period_begin_no_prior_quarter_returns_none() -> None:
    """직전 분기 부재 시 N/A."""
    financials = [
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
             value="6000", effective_date=date(2024, 3, 30)),
    ]
    provider = _provider(as_of=date(2024, 5, 1), financials=financials)
    value = provider.get_scalar(
        "equity_attributable_to_common_consolidated_ifrs_period_begin"
    )
    assert value is None


# =============================================================================
# 9-11. close_price_adjusted
# =============================================================================

def test_close_price_adjusted_latest_trading_day() -> None:
    """직전 영업일 (as_of 범위 최신) 의 보정 종가 — 보정 사건 없으면 raw 최신."""
    prices = [
        _price(d=date(2024, 4, 29), close="70000"),
        _price(d=date(2024, 4, 30), close="71000"),
    ]
    provider = _provider(as_of=date(2024, 5, 2), prices=prices)
    value = provider.get_scalar("close_price_adjusted")
    assert value == Decimal("71000")


def test_close_price_adjusted_read_time_adjustment() -> None:
    """corporate action read-time 보정 — 최신 일자 factor 는 1 (그 이후 사건 없음).

    최신 종가 자체는 보정 factor 1 이므로 raw 와 동일. 본 테스트는 보정 사건이
    있어도 가장 최근 종가는 raw 와 같음을 확인 (as_of 시점 시가 의미).
    """
    prices = [
        _price(d=date(2024, 4, 29), close="70000"),
        _price(d=date(2024, 4, 30), close="71000"),
    ]
    # 액면분할 2:1 — announce / effective 모두 as_of 이전. 과거 가격 보정 대상이나
    # 최신 (4-30) 은 사건 이후 거래일 (사건 4-15) 이라 factor 미적용.
    actions = [
        CorporateActionRecord(
            id=uuid4(), code=_CODE, code_lineage_id=_LINEAGE,
            action_type="split", announced_date=date(2024, 4, 1),
            effective_date=date(2024, 4, 15), payment_date=None,
            ratio=Decimal("2"), cash_amount=None, details={"split_ratio": 2},
            citation_id=_CITATION, superseded_by=None,
            created_at=datetime(2024, 4, 1, 9, 0, tzinfo=UTC),
        ),
    ]
    provider = _provider(
        as_of=date(2024, 5, 2), prices=prices, actions=actions,
    )
    value = provider.get_scalar("close_price_adjusted")
    # 최신 종가 = raw (사건 이후 거래일).
    assert value == Decimal("71000")


def test_close_price_adjusted_no_prices_returns_none() -> None:
    provider = _provider(as_of=date(2024, 5, 2), prices=[])
    assert provider.get_scalar("close_price_adjusted") is None


def test_close_price_adjusted_excludes_lookahead() -> None:
    """PIT — as_of 이후 거래일 종가 미사용 (look-ahead 0)."""
    prices = [
        _price(d=date(2024, 4, 30), close="71000"),
        _price(d=date(2024, 5, 3), close="99999"),  # as_of 이후 — 제외돼야.
    ]
    provider = _provider(as_of=date(2024, 5, 2), prices=prices)
    value = provider.get_scalar("close_price_adjusted")
    assert value == Decimal("71000")


# =============================================================================
# 12. unsupported_m0 fields — 여전히 미보유 (Phase B 에서 market_cap/shares 이동)
# =============================================================================

@pytest.mark.parametrize("field", [
    "dividend_per_share_trailing_annual",
])
def test_unsupported_scalar_returns_none(field: str) -> None:
    """미보유 field 는 정식 N/A (None) — placeholder 아님."""
    provider = _provider(as_of=date(2024, 5, 1))
    assert provider.get_scalar(field) is None


def test_unsupported_series_returns_empty() -> None:
    provider = _provider(as_of=date(2024, 5, 1))
    assert provider.get_quarterly_series(
        "dividend_per_share_trailing_annual", n=4,
    ) == ()


# =============================================================================
# 12c. dividend_per_share_trailing_annual — 실연결 (M7 #4, cash_dividend 집계)
# =============================================================================

def test_dividend_per_share_annual_na_when_repo_none() -> None:
    """dividend_repo 미주입(None) → 정식 N/A (하위호환)."""
    provider = _provider(as_of=date(2024, 5, 1))
    assert provider.get_scalar("dividend_per_share_trailing_annual") is None


def test_dividend_per_share_annual_sums_trailing_12_months() -> None:
    """직전 12 개월 cash_dividend per_share 합 (cash_amount Decimal)."""
    provider = _provider(
        as_of=date(2024, 5, 1),
        dividends=[
            # 직전 12 개월 안 (window_start=2023-05-02 < ex <= 2024-05-01).
            _div(effective_date=date(2023, 8, 15), cash_amount="361"),
            _div(effective_date=date(2024, 2, 15), cash_amount="361"),
            # 12 개월 밖 (이전) — 제외.
            _div(effective_date=date(2023, 3, 15), cash_amount="9999"),
        ],
    )
    value = provider.get_scalar("dividend_per_share_trailing_annual")
    assert value == Decimal("722")


def test_dividend_per_share_annual_details_per_share_coalesce() -> None:
    """cash_amount None → details['per_share'] coalesce (#3 패턴)."""
    provider = _provider(
        as_of=date(2024, 5, 1),
        dividends=[
            _div(effective_date=date(2024, 2, 15), per_share_detail="500"),
        ],
    )
    assert provider.get_scalar(
        "dividend_per_share_trailing_annual",
    ) == Decimal("500")


def test_dividend_per_share_annual_na_when_no_dividend_in_window() -> None:
    """직전 12 개월 배당 0 (이전 배당만) → 정식 N/A (배당 결손 vs 0 의 §2.1 구별)."""
    provider = _provider(
        as_of=date(2024, 5, 1),
        dividends=[
            _div(effective_date=date(2022, 8, 15), cash_amount="361"),
        ],
    )
    assert provider.get_scalar("dividend_per_share_trailing_annual") is None


# =============================================================================
# 12d. total_return_trailing_1y — 실연결 (M7 #4, 배당 재투자 1년 총수익률)
# =============================================================================

def test_total_return_trailing_1y_na_when_both_repos_none() -> None:
    """dividend_repo / total_return_adjuster 둘 다 None → 정식 N/A (정상 경로)."""
    p1 = _provider(as_of=date(2024, 5, 1), prices=[_price(d=date(2024, 5, 1), close="100")])
    assert p1.get_scalar("total_return_trailing_1y") is None


def test_total_return_trailing_1y_both_or_neither_assert() -> None:
    """M7 #4 fix5 — dividend_repo / total_return_adjuster 한쪽만 주입 = 배선 버그.

    하나만 None 이면 total_return_trailing_1y 가 silent N/A 로 빠지는 §2.10
    reproducibility 위험 → ValueError fail-loud. 둘 다 None·둘 다 주입은 정상.
    """
    # 한쪽만 주입은 항상 배선 버그이므로 helper 를 우회해 DbFieldProvider 직접 구성.
    base_kwargs = dict(
        code=_CODE,
        as_of=date(2024, 5, 1),
        price_repo=FakePriceRepository(records=[_price(d=date(2024, 5, 1), close="100")]),
        financial_repo=FakeFinancialRepository(records=[]),
    )
    # adjuster 만 주입, dividend_repo None → ValueError.
    with pytest.raises(ValueError, match="both-or-neither"):
        DbFieldProvider(**base_kwargs, total_return_adjuster=TotalReturnAdjuster())
    # dividend_repo 만 주입, adjuster None → ValueError.
    with pytest.raises(ValueError, match="both-or-neither"):
        DbFieldProvider(
            **base_kwargs, dividend_repo=FakeDividendRepository(records=[]),
        )


def test_total_return_trailing_1y_price_only_ratio() -> None:
    """배당 없는 1 년 — 순수 가격 변동 ratio. 100→121 = 0.21 (배당 0).

    첫 거래일 2023-05-10 은 fetch window(as_of-365=2023-05-03 이후) 내이면서
    as_of(2024-05-02)로부터 358 일 전 = min-coverage(340 일) 충족 → 1 년 history
    인정 (M7 #4 fix3).
    """
    provider = _provider(
        as_of=date(2024, 5, 2),
        prices=[
            _price(d=date(2023, 5, 10), close="100"),
            _price(d=date(2024, 1, 15), close="110"),
            _price(d=date(2024, 5, 2), close="121"),
        ],
        dividends=[],
        total_return_adjuster=TotalReturnAdjuster(),
    )
    value = provider.get_scalar("total_return_trailing_1y")
    # TRI[-1] = 121/100 = 1.21 → ratio = 0.21.
    assert value == Decimal("0.21")


def test_total_return_trailing_1y_includes_dividend_reinvestment() -> None:
    """배당락일 재투자 반영 — 같은 가격 경로라도 배당 있으면 총수익률이 더 큼."""
    prices = [
        # 첫 거래일 2023-05-10 = min-coverage(340 일) 충족 (M7 #4 fix3).
        _price(d=date(2023, 5, 10), close="100"),
        _price(d=date(2024, 1, 15), close="100"),
        _price(d=date(2024, 5, 2), close="100"),
    ]
    # 배당락일 2024-01-15 에 per_share=5 현금배당 (close_raw==close_adj 라 factor=1).
    div = _div(effective_date=date(2024, 1, 15), cash_amount="5")
    provider = _provider(
        as_of=date(2024, 5, 2),
        prices=prices,
        dividends=[div],
        total_return_adjuster=TotalReturnAdjuster(),
    )
    value = provider.get_scalar("total_return_trailing_1y")
    # TRI: 1 * (100+5)/100 * 100/100 = 1.05 → ratio = 0.05 (가격 무변동, 배당만).
    assert value == Decimal("0.05")


def test_total_return_trailing_1y_na_when_history_under_one_year() -> None:
    """M7 #4 fix3 (§2.1) — 1 년 미만 history → N/A (부분 window 를 1Y 라벨 금지).

    첫 거래일이 as_of 로부터 약 6 개월 전(상장 1 년 미만)이면 직전 1 년을 cover
    하지 못하므로, ~6 개월짜리 수익률을 "1 년 총수익률"로 라벨하지 않고 N/A 반환
    (데이터 의존 lookback 차단 — strict-or-N/A 패턴).
    """
    provider = _provider(
        as_of=date(2024, 5, 2),
        prices=[
            # 첫 거래일 2023-11-01 = as_of 로부터 183 일 전 → min-coverage(340 일)
            # 미충족 → 1 년 history 부재 → N/A.
            _price(d=date(2023, 11, 1), close="100"),
            _price(d=date(2024, 5, 2), close="130"),
        ],
        dividends=[],
        total_return_adjuster=TotalReturnAdjuster(),
    )
    assert provider.get_scalar("total_return_trailing_1y") is None


# =============================================================================
# 12b. market_cap / shares — Phase B 영구화 해소
# =============================================================================

def test_market_cap_krx_official_resolves() -> None:
    """market_cap_krx_official → market_caps.market_cap (최신 effective_date)."""
    provider = _provider(
        as_of=date(2024, 5, 2),
        market_caps=[
            _mc(d=date(2024, 5, 1), market_cap="400000000000000",
                shares_outstanding=5_969_782_550),
            _mc(d=date(2024, 5, 2), market_cap="410000000000000",
                shares_outstanding=5_969_782_550),
        ],
    )
    assert provider.get_scalar("market_cap_krx_official") == Decimal(
        "410000000000000"
    )


def test_shares_issued_resolves_as_decimal() -> None:
    """shares_issued → market_caps.shares_outstanding (Decimal 변환)."""
    provider = _provider(
        as_of=date(2024, 5, 2),
        market_caps=[
            _mc(d=date(2024, 5, 2), market_cap="410000000000000",
                shares_outstanding=5_969_782_550),
        ],
    )
    value = provider.get_scalar("shares_issued")
    assert value == Decimal(5_969_782_550)
    assert isinstance(value, Decimal)


def test_market_cap_pit_excludes_future_effective_date() -> None:
    """effective_date > as_of 인 row 미사용 (look-ahead 0)."""
    provider = _provider(
        as_of=date(2024, 5, 1),
        market_caps=[
            _mc(d=date(2024, 4, 30), market_cap="390000000000000",
                shares_outstanding=5_969_782_550),
            # as_of 초과 — 미사용.
            _mc(d=date(2024, 5, 2), market_cap="410000000000000",
                shares_outstanding=6_000_000_000),
        ],
    )
    assert provider.get_scalar("market_cap_krx_official") == Decimal(
        "390000000000000"
    )
    assert provider.get_scalar("shares_issued") == Decimal(5_969_782_550)


def test_market_cap_na_when_repo_none() -> None:
    """market_cap_repo 미주입 (None) 이면 정식 N/A."""
    provider = _provider(as_of=date(2024, 5, 1))  # market_caps 미주입 → repo None
    assert provider.get_scalar("market_cap_krx_official") is None
    assert provider.get_scalar("shares_issued") is None


def test_market_cap_na_when_no_data() -> None:
    """repo 주입됐으나 종목 데이터 없으면 N/A."""
    provider = _provider(as_of=date(2024, 5, 1), market_caps=[])
    assert provider.get_scalar("market_cap_krx_official") is None
    assert provider.get_scalar("shares_issued") is None


# =============================================================================
# 12c. shares_treasury — DART treasury_shares 영구화 해소
# =============================================================================

def test_shares_treasury_resolves_as_decimal() -> None:
    """shares_treasury → treasury_shares.shares_treasury (최신 active, Decimal)."""
    provider = _provider(
        as_of=date(2024, 5, 1),
        treasury=[
            _treasury(fiscal_period="2023Q4", effective_date=date(2024, 3, 30),
                      shares_treasury=538_000_000),
        ],
    )
    value = provider.get_scalar("shares_treasury")
    assert value == Decimal(538_000_000)
    assert isinstance(value, Decimal)


def test_shares_treasury_zero_is_valid() -> None:
    """자사주 0 (명시적 없음) 은 유효 값 — None 과 구별."""
    provider = _provider(
        as_of=date(2024, 5, 1),
        treasury=[
            _treasury(fiscal_period="2023Q4", effective_date=date(2024, 3, 30),
                      shares_treasury=0),
        ],
    )
    assert provider.get_scalar("shares_treasury") == Decimal(0)


def test_shares_treasury_pit_excludes_future() -> None:
    """effective_date > as_of 인 자사주 row 미사용 (look-ahead 0)."""
    provider = _provider(
        as_of=date(2024, 4, 1),
        treasury=[
            _treasury(fiscal_period="2023Q4", effective_date=date(2024, 3, 30),
                      shares_treasury=100),
            # as_of 초과 — 미사용.
            _treasury(fiscal_period="2024Q1", effective_date=date(2024, 5, 15),
                      shares_treasury=200),
        ],
    )
    assert provider.get_scalar("shares_treasury") == Decimal(100)


def test_shares_treasury_supersede_chain() -> None:
    """정정공시 chain — active row 만 (stale 0)."""
    old_id = UUID("00000000-0000-0000-0000-000000000020")
    new_id = UUID("00000000-0000-0000-0000-000000000021")
    provider = _provider(
        as_of=date(2024, 5, 1),
        treasury=[
            _treasury(fiscal_period="2023Q4", effective_date=date(2024, 3, 30),
                      shares_treasury=100, record_id=old_id,
                      superseded_by=new_id,
                      created_at=datetime(2024, 3, 30, tzinfo=UTC)),
            _treasury(fiscal_period="2023Q4", effective_date=date(2024, 4, 20),
                      shares_treasury=150, record_id=new_id,
                      created_at=datetime(2024, 4, 20, tzinfo=UTC)),
        ],
    )
    assert provider.get_scalar("shares_treasury") == Decimal(150)


def test_shares_treasury_na_when_repo_none() -> None:
    """treasury_repo 미주입 (None) 이면 정식 N/A."""
    provider = _provider(as_of=date(2024, 5, 1))  # treasury 미주입 → repo None
    assert provider.get_scalar("shares_treasury") is None


def test_shares_treasury_na_when_no_data() -> None:
    """repo 주입됐으나 종목 데이터 없으면 N/A."""
    provider = _provider(as_of=date(2024, 5, 1), treasury=[])
    assert provider.get_scalar("shares_treasury") is None


def test_market_cap_ex_treasury_na_without_pack_injection() -> None:
    """파생 필드 `market_cap_ex_treasury` 는 pack/evaluator 미주입 시 정식 N/A.

    derived_factor kind 의 해소는 factor_pack + evaluator 주입을 요구. 비-stocks
    컨텍스트 / 단위 테스트에서 미주입 시 정식 None (데이터 일상의 N/A).
    primitive shares_treasury 는 무관하게 해소됨.
    """
    provider = _provider(
        as_of=date(2024, 5, 2),
        market_caps=[
            _mc(d=date(2024, 5, 2), market_cap="410000000000000",
                shares_outstanding=5_969_782_550),
        ],
        treasury=[
            _treasury(fiscal_period="2024Q1", effective_date=date(2024, 5, 1),
                      shares_treasury=538_000_000),
        ],
    )
    # pack/evaluator 미주입 → 파생 필드 정식 N/A.
    assert provider.get_scalar("market_cap_ex_treasury") is None
    # primitive shares_treasury 는 해소됨.
    assert provider.get_scalar("shares_treasury") == Decimal(538_000_000)


def test_factor_market_cap_ex_treasury_evaluates_with_treasury_injection() -> None:
    """end-to-end — factor `market-cap:ex-treasury` 가 자사주 주입 시 실평가.

    formula = (shares_issued - shares_treasury) × close_price_adjusted.
    primitive 입력이 모두 해소되면 evaluator 가 자동 산출 (provider 변경 불필요 —
    파생 composite 메커니즘 아님). DEFAULT_DISPLAY_FACTORS 의 카드가 표시됨.
    """
    from app.services.factor_pack import DEFAULT_PACK

    as_of = date(2024, 5, 2)
    shares_issued = 5_969_782_550
    shares_treasury = 538_000_000
    close = Decimal("80000")
    provider = _provider(
        as_of=as_of,
        prices=[_price(d=date(2024, 5, 2), close=str(close))],
        market_caps=[
            _mc(d=date(2024, 5, 2), market_cap="410000000000000",
                shares_outstanding=shares_issued),
        ],
        treasury=[
            _treasury(fiscal_period="2024Q1", effective_date=date(2024, 5, 1),
                      shares_treasury=shares_treasury),
        ],
    )
    factor = next(
        f for f in DEFAULT_PACK.body["factors"]
        if f["canonical_id"] == "market-cap:ex-treasury"
    )
    result = FactorEvaluator().evaluate(factor, provider, as_of=as_of)
    expected = (Decimal(shares_issued) - Decimal(shares_treasury)) * close
    assert result.value == expected
    assert result.is_na is False


def test_factor_market_cap_ex_treasury_na_without_treasury() -> None:
    """자사주 미주입 (treasury_repo None) 시 factor 는 missing_input N/A.

    shares_treasury 가 None → evaluator 가 missing_input:shares_treasury.
    """
    from app.services.factor_pack import DEFAULT_PACK

    as_of = date(2024, 5, 2)
    provider = _provider(
        as_of=as_of,
        prices=[_price(d=date(2024, 5, 2), close="80000")],
        market_caps=[
            _mc(d=date(2024, 5, 2), market_cap="410000000000000",
                shares_outstanding=5_969_782_550),
        ],
        # treasury 미주입 → shares_treasury N/A.
    )
    factor = next(
        f for f in DEFAULT_PACK.body["factors"]
        if f["canonical_id"] == "market-cap:ex-treasury"
    )
    result = FactorEvaluator().evaluate(factor, provider, as_of=as_of)
    assert result.value is None
    assert result.is_na is True
    assert result.na_reason == "missing_input:shares_treasury"


# =============================================================================
# 12d. 파생 필드 (derived_factor) 해소 — market_cap_ex_treasury (Option B)
# =============================================================================
#
# market_cap_ex_treasury 는 primitive DB field 가 아니라 factor
# `market-cap:ex-treasury` 의 출력 (= (shares_issued - shares_treasury) ×
# close_price_adjusted). provider 가 대응 factor 를 evaluator 로 평가해 그 값을
# 반환 (provider 하드코딩 composite 금지 — Fidelity 단일 출처). 따라서
# get_scalar("market_cap_ex_treasury") == evaluate(market-cap:ex-treasury).value.


def _full_data_provider(
    *,
    as_of: date,
    shares_issued: int,
    shares_treasury: int,
    close: str,
    net_income_quarters: list[str] | None = None,
    equity_common: str | None = None,
    evaluator: FactorEvaluator | None = None,
) -> DbFieldProvider:
    """PER/PBR full 데이터 시나리오 provider — pack + evaluator 주입.

    market-cap:ex-treasury 입력 (shares_issued / shares_treasury / 종가) +
    선택적으로 net_income 4 분기 (PER) / equity_common (PBR) 주입.
    """
    from app.services.factor_pack import DEFAULT_PACK

    financials: list[FinancialRecord] = []
    if net_income_quarters is not None:
        # 4 분기 net_income — fiscal_period asc, 공시일 보수 추정.
        # net_income 은 FLOW 계정이므로 인자(net_income_quarters)를 분기단독
        # (standalone) 값으로 받아 DART 누적(YTD) 으로 변환 후 seed (B1 reality).
        # 누적[q] = standalone[0..q] 합 → `_resolve_financial_series` 가 다시
        # standalone 으로 복원하여 원 인자 값을 평가에 사용 (TTM 합 == sum(인자)).
        eff = [
            date(2023, 5, 15), date(2023, 8, 14),
            date(2023, 11, 14), date(2024, 3, 30),
        ]
        cumulative = 0
        for fp, val, e in zip(
            ("2023Q1", "2023Q2", "2023Q3", "2023Q4"),
            net_income_quarters, eff, strict=True,
        ):
            cumulative += int(val)
            financials.append(
                _fin(account=_NET_INCOME_ACCOUNT, fiscal_period=fp,
                     value=str(cumulative), effective_date=e)
            )
    if equity_common is not None:
        financials.append(
            _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
                 value=equity_common, effective_date=date(2024, 3, 30))
        )
    return _provider(
        as_of=as_of,
        prices=[_price(d=as_of, close=close)],
        market_caps=[
            _mc(d=as_of, market_cap="410000000000000",
                shares_outstanding=shares_issued),
        ],
        treasury=[
            _treasury(fiscal_period="2023Q4", effective_date=date(2024, 3, 30),
                      shares_treasury=shares_treasury),
        ],
        financials=financials,
        factor_pack=DEFAULT_PACK,
        evaluator=evaluator or FactorEvaluator(),
    )


def test_derived_field_consistency_with_factor_eval() -> None:
    """일관성 (Fidelity 단일 출처) — get_scalar 값 == evaluate(target factor) 값.

    동일 데이터에서 provider.get_scalar("market_cap_ex_treasury") 와
    evaluator.evaluate(market-cap:ex-treasury, 별도 provider) 의 값이 동일해야.
    provider 가 composite 를 하드코딩하지 않고 factor 를 평가하므로, 두 경로의
    산출은 정의상 같다 (pack 공식이 단일 출처).
    """
    from app.services.factor_pack import DEFAULT_PACK

    as_of = date(2024, 5, 2)
    shares_issued = 5_969_782_550
    shares_treasury = 538_000_000
    close = "80000"

    derived_provider = _full_data_provider(
        as_of=as_of, shares_issued=shares_issued,
        shares_treasury=shares_treasury, close=close,
    )
    derived_value = derived_provider.get_scalar("market_cap_ex_treasury")

    # 별도 provider 로 target factor 직접 평가 (동일 데이터).
    direct_provider = _full_data_provider(
        as_of=as_of, shares_issued=shares_issued,
        shares_treasury=shares_treasury, close=close,
    )
    factor = next(
        f for f in DEFAULT_PACK.body["factors"]
        if f["canonical_id"] == "market-cap:ex-treasury"
    )
    direct = FactorEvaluator().evaluate(factor, direct_provider, as_of=as_of)

    expected = (Decimal(shares_issued) - Decimal(shares_treasury)) * Decimal(close)
    assert derived_value == expected
    assert derived_value == direct.value


def test_derived_field_na_propagation_missing_treasury() -> None:
    """N/A 전파 — shares_treasury 결측 → market_cap_ex_treasury N/A → PER/PBR N/A.

    treasury 미주입 (repo None) → target factor missing_input:shares_treasury →
    파생 필드 None → PER/PBR factor 도 missing_input:market_cap_ex_treasury N/A.
    """
    from app.services.factor_pack import DEFAULT_PACK

    as_of = date(2024, 5, 2)
    evaluator = FactorEvaluator()
    # treasury 미주입 → shares_treasury N/A → 파생 N/A.
    provider = _provider(
        as_of=as_of,
        prices=[_price(d=as_of, close="80000")],
        market_caps=[
            _mc(d=as_of, market_cap="410000000000000",
                shares_outstanding=5_969_782_550),
        ],
        financials=[
            _fin(account=_NET_INCOME_ACCOUNT, fiscal_period="2023Q1",
                 value="100", effective_date=date(2023, 5, 15)),
            _fin(account=_NET_INCOME_ACCOUNT, fiscal_period="2023Q2",
                 value="200", effective_date=date(2023, 8, 14)),
            _fin(account=_NET_INCOME_ACCOUNT, fiscal_period="2023Q3",
                 value="300", effective_date=date(2023, 11, 14)),
            _fin(account=_NET_INCOME_ACCOUNT, fiscal_period="2023Q4",
                 value="400", effective_date=date(2024, 3, 30)),
            _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
                 value="100000000000000", effective_date=date(2024, 3, 30)),
        ],
        factor_pack=DEFAULT_PACK,
        evaluator=evaluator,
    )
    # 파생 필드 자체 N/A.
    assert provider.get_scalar("market_cap_ex_treasury") is None

    per = next(f for f in DEFAULT_PACK.body["factors"]
               if f["canonical_id"] == "per:ttm-consolidated-ifrs")
    pbr = next(f for f in DEFAULT_PACK.body["factors"]
               if f["canonical_id"] == "pbr:consolidated-ifrs")
    per_result = evaluator.evaluate(per, provider, as_of=as_of)
    pbr_result = evaluator.evaluate(pbr, provider, as_of=as_of)
    assert per_result.is_na is True
    assert per_result.na_reason == "missing_input:market_cap_ex_treasury"
    assert pbr_result.is_na is True
    assert pbr_result.na_reason == "missing_input:market_cap_ex_treasury"


def test_derived_field_per_pbr_real_values() -> None:
    """실값 — 전 입력 주입 시 PER/PBR 수치 산출 (파생 필드 경유).

    market_cap_ex_treasury = (shares_issued - shares_treasury) × close.
    PER = market_cap_ex_treasury / sum(net_income 4Q).
    PBR = market_cap_ex_treasury / equity_common.
    """
    from app.services.factor_pack import DEFAULT_PACK

    as_of = date(2024, 5, 2)
    shares_issued = 1_000_000
    shares_treasury = 100_000
    close = "50000"
    net_income = ["1000000000", "1000000000", "1000000000", "1000000000"]
    equity_common = "30000000000"
    evaluator = FactorEvaluator()
    provider = _full_data_provider(
        as_of=as_of, shares_issued=shares_issued,
        shares_treasury=shares_treasury, close=close,
        net_income_quarters=net_income, equity_common=equity_common,
        evaluator=evaluator,
    )

    mcap = (Decimal(shares_issued) - Decimal(shares_treasury)) * Decimal(close)
    assert provider.get_scalar("market_cap_ex_treasury") == mcap

    per = next(f for f in DEFAULT_PACK.body["factors"]
               if f["canonical_id"] == "per:ttm-consolidated-ifrs")
    pbr = next(f for f in DEFAULT_PACK.body["factors"]
               if f["canonical_id"] == "pbr:consolidated-ifrs")
    per_result = evaluator.evaluate(per, provider, as_of=as_of)
    pbr_result = evaluator.evaluate(pbr, provider, as_of=as_of)

    net_total = sum(Decimal(q) for q in net_income)
    assert per_result.is_na is False
    assert per_result.value == mcap / net_total
    assert pbr_result.is_na is False
    assert pbr_result.value == mcap / Decimal(equity_common)


def test_derived_field_cycle_detection_raises() -> None:
    """순환 탐지 — target factor 가 자기 자신을 field 로 참조 → UnknownFieldError.

    합성 pack: derived field 의 target factor AST 가 `market_cap_ex_treasury`
    자체를 field 로 참조 (self-cycle). pack 무결성 위반 → fail-loud
    (UnknownFieldError, 데이터 N/A 아님).
    """
    class _CyclicPack:
        # body["factors"] 만 필요 — LoadedPack 의 최소 duck-typing.
        body = {
            "factors": [
                {
                    "canonical_id": "market-cap:ex-treasury",
                    "uuid": "018f9b00-0001-7000-8000-0000000000ff",
                    "formula": {
                        # AST 가 market_cap_ex_treasury 를 다시 field 로 참조 — cycle.
                        "ast": {"field": "market_cap_ex_treasury"},
                        "inputs": ["market_cap_ex_treasury"],
                    },
                },
            ],
        }

    provider = _provider(
        as_of=date(2024, 5, 2),
        factor_pack=_CyclicPack(),
        evaluator=FactorEvaluator(),
    )
    with pytest.raises(UnknownFieldError, match="cycle"):
        provider.get_scalar("market_cap_ex_treasury")


def test_derived_field_unknown_target_factor_raises() -> None:
    """target factor 가 pack 에 없음 → UnknownFieldError (registry/pack drift)."""
    class _EmptyPack:
        body = {"factors": []}

    provider = _provider(
        as_of=date(2024, 5, 2),
        factor_pack=_EmptyPack(),
        evaluator=FactorEvaluator(),
    )
    with pytest.raises(UnknownFieldError, match="target factor"):
        provider.get_scalar("market_cap_ex_treasury")


def test_derived_field_caching_single_subfetch() -> None:
    """캐싱 — PER + PBR 이 market_cap_ex_treasury 를 2 번 참조해도 sub-fetch 1 회.

    파생 결과는 _scalar_cache 로 1 회만 계산. market-cap:ex-treasury 의 입력
    중 treasury fetch_latest_active 호출 수로 검증 (파생 평가 1 회 → treasury
    sub-fetch 1 회). 두 번째 참조는 캐시 hit.
    """
    from app.services.factor_pack import DEFAULT_PACK

    as_of = date(2024, 5, 2)
    evaluator = FactorEvaluator()

    treasury_repo = FakeTreasurySharesRepository(records=[
        _treasury(fiscal_period="2023Q4", effective_date=date(2024, 3, 30),
                  shares_treasury=100_000),
    ])
    calls = {"n": 0}
    orig = treasury_repo.fetch_latest_active

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return orig(*args, **kwargs)

    treasury_repo.fetch_latest_active = _counting  # type: ignore[method-assign]

    provider = DbFieldProvider(
        code=_CODE, as_of=as_of,
        price_repo=FakePriceRepository(records=[_price(d=as_of, close="50000")]),
        financial_repo=FakeFinancialRepository(records=[
            _fin(account=_NET_INCOME_ACCOUNT, fiscal_period="2023Q1",
                 value="1000000000", effective_date=date(2023, 5, 15)),
            _fin(account=_NET_INCOME_ACCOUNT, fiscal_period="2023Q2",
                 value="1000000000", effective_date=date(2023, 8, 14)),
            _fin(account=_NET_INCOME_ACCOUNT, fiscal_period="2023Q3",
                 value="1000000000", effective_date=date(2023, 11, 14)),
            _fin(account=_NET_INCOME_ACCOUNT, fiscal_period="2023Q4",
                 value="1000000000", effective_date=date(2024, 3, 30)),
            _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
                 value="30000000000", effective_date=date(2024, 3, 30)),
        ]),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=FakeMarketCapRepository(records=[
            _mc(d=as_of, market_cap="410000000000000",
                shares_outstanding=1_000_000),
        ]),
        treasury_repo=treasury_repo,
        factor_pack=DEFAULT_PACK,
        evaluator=evaluator,
    )

    per = next(f for f in DEFAULT_PACK.body["factors"]
               if f["canonical_id"] == "per:ttm-consolidated-ifrs")
    pbr = next(f for f in DEFAULT_PACK.body["factors"]
               if f["canonical_id"] == "pbr:consolidated-ifrs")
    # PER + PBR 둘 다 market_cap_ex_treasury 를 참조.
    per_result = evaluator.evaluate(per, provider, as_of=as_of)
    pbr_result = evaluator.evaluate(pbr, provider, as_of=as_of)
    assert per_result.is_na is False
    assert pbr_result.is_na is False
    # 파생 평가 1 회 → treasury sub-fetch 1 회 (두 번째 참조는 _scalar_cache hit).
    assert calls["n"] == 1


# =============================================================================
# 19. trading_value_20d_avg — KRX 20 영업일 평균 (T51)
# =============================================================================

_TV_AS_OF = date(2024, 6, 28)  # 2024 verified 범위 + 직전 20 영업일 충분 (금요일).


def _last_n_business_days(as_of: date, n: int) -> list[date]:
    """as_of 이하 최근 n KRX 영업일 (오름차순) — resolver 와 동일 산출 (검증용)."""
    cursor = DEFAULT_CALENDAR.latest_business_day(as_of)
    days = [cursor]
    for _ in range(n - 1):
        cursor = DEFAULT_CALENDAR.previous_business_day(cursor)
        days.append(cursor)
    return sorted(days)


def test_trading_value_20d_avg_full_window() -> None:
    """20 영업일 모두 가격 존재 → trading_value 평균. 100+200+...+2000=21000 /20."""
    days = _last_n_business_days(_TV_AS_OF, 20)
    prices = [
        _price(d=d, close="50000", trading_value=str((i + 1) * 100))
        for i, d in enumerate(days)
    ]
    provider = _provider(as_of=_TV_AS_OF, prices=prices)
    assert provider.get_scalar("trading_value_20d_avg") == Decimal("1050")


def test_trading_value_20d_avg_missing_day_is_na() -> None:
    """20 영업일 중 하나라도 가격 결손 (거래정지 등) → strict N/A (부분 평균 금지)."""
    days = _last_n_business_days(_TV_AS_OF, 20)
    prices = [
        _price(d=d, close="50000", trading_value="1000")
        for i, d in enumerate(days) if i != 10  # 중간 1 일 누락.
    ]
    provider = _provider(as_of=_TV_AS_OF, prices=prices)
    assert provider.get_scalar("trading_value_20d_avg") is None


def test_trading_value_20d_avg_near_calendar_start_is_na() -> None:
    """캘린더 verified 범위 시작 근처 — 20 영업일 미확보 → N/A (fail-soft)."""
    as_of = date(2024, 1, 15)  # 2024 min_date 부터 20 영업일 미만.
    prices = [
        _price(d=date(2024, 1, d), close="50000", trading_value="1000")
        for d in (2, 3, 4, 5, 8, 9, 10, 11, 12, 15)
    ]
    provider = _provider(as_of=as_of, prices=prices)
    assert provider.get_scalar("trading_value_20d_avg") is None


def test_trading_value_20d_avg_ignores_non_business_days() -> None:
    """window 는 KRX 영업일만 — 주말 가격 row 는 평균에 미포함."""
    days = _last_n_business_days(_TV_AS_OF, 20)
    prices = [_price(d=d, close="50000", trading_value="1000") for d in days]
    # 토요일 (2024-06-22, 비영업일) 가격 추가 — window 밖이라 평균 불변.
    prices.append(_price(d=date(2024, 6, 22), close="50000", trading_value="999999"))
    provider = _provider(as_of=_TV_AS_OF, prices=prices)
    assert provider.get_scalar("trading_value_20d_avg") == Decimal("1000")


# =============================================================================
# 13. unknown field
# =============================================================================

def test_unknown_scalar_field_raises() -> None:
    provider = _provider(as_of=date(2024, 5, 1))
    with pytest.raises(UnknownFieldError, match="unknown field"):
        provider.get_scalar("nonexistent_field_xyz")


def test_unknown_series_field_raises() -> None:
    provider = _provider(as_of=date(2024, 5, 1))
    with pytest.raises(UnknownFieldError, match="unknown field"):
        provider.get_quarterly_series("nonexistent_field_xyz", n=4)


# =============================================================================
# 14. PIT — look-ahead 0 (재무)
# =============================================================================

def test_financial_excludes_lookahead_record() -> None:
    """effective_date > as_of 인 record 미사용."""
    financials = [
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
             value="6000", effective_date=date(2024, 3, 30)),
        # 미래 공시 — as_of 시점에 알 수 없음.
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2024Q1",
             value="7000", effective_date=date(2024, 5, 15)),
    ]
    provider = _provider(as_of=date(2024, 5, 1), financials=financials)
    value = provider.get_scalar(
        "equity_attributable_to_common_consolidated_ifrs"
    )
    # 2024Q1 은 as_of (5-1) 이후 공시 (5-15) 라 제외 → 2023Q4 가 최신.
    assert value == Decimal("6000")


# =============================================================================
# 15. ifrs_type 필터
# =============================================================================

def test_ifrs_type_filter_excludes_separate() -> None:
    """consolidated field 는 separate row 제외."""
    financials = [
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
             value="6000", effective_date=date(2024, 3, 30),
             ifrs_type="consolidated"),
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
             value="5500", effective_date=date(2024, 3, 30),
             ifrs_type="separate"),
    ]
    provider = _provider(as_of=date(2024, 5, 1), financials=financials)
    value = provider.get_scalar(
        "equity_attributable_to_common_consolidated_ifrs"
    )
    assert value == Decimal("6000")


# =============================================================================
# 16. request-scoped 캐싱
# =============================================================================

def test_scalar_cached_within_provider() -> None:
    """같은 field 재호출 시 동일 결과 + repository 1 회 fetch."""
    calls = {"n": 0}
    financials = [
        _fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
             value="6000", effective_date=date(2024, 3, 30)),
    ]
    base = FakeFinancialRepository(records=financials)
    orig_fetch = base.fetch_financials

    def _counting_fetch(*args, **kwargs):
        calls["n"] += 1
        return orig_fetch(*args, **kwargs)

    base.fetch_financials = _counting_fetch  # type: ignore[method-assign]
    provider = DbFieldProvider(
        code=_CODE, as_of=date(2024, 5, 1),
        price_repo=FakePriceRepository(records=[]),
        financial_repo=base,
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
    )
    field = "equity_attributable_to_common_consolidated_ifrs"
    v1 = provider.get_scalar(field)
    v2 = provider.get_scalar(field)
    assert v1 == v2 == Decimal("6000")
    assert calls["n"] == 1  # 캐싱으로 fetch 1 회.


# =============================================================================
# 17. as_of invariant
# =============================================================================

def test_provider_holds_as_of() -> None:
    provider = _provider(as_of=date(2024, 5, 7))
    assert provider.as_of == date(2024, 5, 7)


# =============================================================================
# 18. end-to-end — FactorEvaluator + DbFieldProvider
# =============================================================================

def test_end_to_end_eps_ttm() -> None:
    """EPS (basic, TTM) factor 를 실제 산출 — sum_last_n_quarters(4).

    basic_eps 는 FLOW 계정 — DART 누적(YTD)으로 seed. 의도 standalone
    [500,600,700,800](TTM 2600)을 위해 누적 [500,1100,1800,2600] seed →
    B1 변환 후 standalone 합 = 2600.
    """
    financials = [
        _fin(account=_EPS_ACCOUNT, fiscal_period="2023Q1",
             value="500", effective_date=date(2023, 5, 15)),
        _fin(account=_EPS_ACCOUNT, fiscal_period="2023Q2",
             value="1100", effective_date=date(2023, 8, 14)),
        _fin(account=_EPS_ACCOUNT, fiscal_period="2023Q3",
             value="1800", effective_date=date(2023, 11, 14)),
        _fin(account=_EPS_ACCOUNT, fiscal_period="2023Q4",
             value="2600", effective_date=date(2024, 3, 30)),
    ]
    provider = _provider(as_of=date(2024, 5, 1), financials=financials)
    evaluator = FactorEvaluator()
    factor = {
        "canonical_id": "eps:basic-ttm-consolidated-ifrs",
        "uuid": "018f9b00-0001-7000-8000-000000000008",
        "formula": {
            "ast": {
                "op": "sum_last_n_quarters", "n": 4,
                "field": "basic_eps_consolidated_ifrs",
            },
            "inputs": ["basic_eps_consolidated_ifrs"],
        },
    }
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert not result.is_na
    assert result.value == Decimal("2600")  # standalone [500,600,700,800] 합


def test_end_to_end_na_when_insufficient() -> None:
    """4 분기 미만 → N/A (insufficient_series)."""
    financials = [
        _fin(account=_EPS_ACCOUNT, fiscal_period="2023Q3",
             value="700", effective_date=date(2023, 11, 14)),
        _fin(account=_EPS_ACCOUNT, fiscal_period="2023Q4",
             value="800", effective_date=date(2024, 3, 30)),
    ]
    provider = _provider(as_of=date(2024, 5, 1), financials=financials)
    evaluator = FactorEvaluator()
    factor = {
        "canonical_id": "eps:basic-ttm-consolidated-ifrs",
        "uuid": "018f9b00-0001-7000-8000-000000000008",
        "formula": {
            "ast": {
                "op": "sum_last_n_quarters", "n": 4,
                "field": "basic_eps_consolidated_ifrs",
            },
            "inputs": ["basic_eps_consolidated_ifrs"],
        },
    }
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.is_na
    assert result.na_reason.startswith("insufficient_series:")


# =============================================================================
# 20-24. ECOS 매크로 지표 해소 — macro_indicator (M2 T81)
# =============================================================================

# ECOS indicator_id 상수 — _RESOLUTIONS 의 매핑과 일치.
_BASE_RATE_ID = "722Y001/0101000"
_CPI_ID = "901Y009/0"


def _macro(
    *,
    indicator_id: str,
    reference_date: date,
    vintage_date: date,
    value: str,
) -> MacroIndicatorRecord:
    """MacroIndicatorRecord 생성 헬퍼 — 테스트 반복 최소화."""
    from uuid import uuid4
    return MacroIndicatorRecord(
        id=uuid4(),
        indicator_id=indicator_id,
        reference_date=reference_date,
        value=Decimal(value),
        unit="percent",
        vintage_date=vintage_date,
        citation_id=_CITATION,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def test_ecos_base_rate_resolves_to_value() -> None:
    """ecos_base_rate → FakeMacroIndicatorRepository.fetch_latest.value."""
    record = _macro(
        indicator_id=_BASE_RATE_ID,
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 10),
        value="3.50",
    )
    provider = _provider(as_of=date(2024, 5, 1), macro_records=[record])
    assert provider.get_scalar("ecos_base_rate") == Decimal("3.50")


def test_ecos_cpi_resolves_to_value() -> None:
    """ecos_cpi → FakeMacroIndicatorRepository.fetch_latest.value."""
    record = _macro(
        indicator_id=_CPI_ID,
        reference_date=date(2024, 3, 1),
        vintage_date=date(2024, 4, 5),
        value="113.5",
    )
    provider = _provider(as_of=date(2024, 5, 1), macro_records=[record])
    assert provider.get_scalar("ecos_cpi") == Decimal("113.5")


def test_ecos_macro_na_when_repo_none() -> None:
    """macro_repo 미주입 (None) 이면 정식 N/A — 기능 미동작이나 silent 아님."""
    # macro_records 미주입 → _provider 가 macro_repo=None 로 생성.
    provider = _provider(as_of=date(2024, 5, 1))
    assert provider.get_scalar("ecos_base_rate") is None
    assert provider.get_scalar("ecos_cpi") is None


def test_ecos_macro_na_when_no_data() -> None:
    """repo 주입됐으나 해당 indicator 데이터 없으면 N/A."""
    # 다른 indicator_id 데이터만 존재.
    record = _macro(
        indicator_id="other/999",
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 10),
        value="1.0",
    )
    provider = _provider(as_of=date(2024, 5, 1), macro_records=[record])
    assert provider.get_scalar("ecos_base_rate") is None


def test_ecos_vintage_pit_excludes_future_vintage() -> None:
    """vintage PIT — vintage_date > as_of 인 record 제외 (look-ahead 0).

    as_of=2024-05-01 이면 vintage_date=2024-06-01 인 record 는 as_of 시점에
    알 수 없는 관측 → 제외. fetch_latest 가 vintage_date<=as_of 강제.
    """
    # as_of 이후 vintage — 미래 공표값, 제외돼야.
    future = _macro(
        indicator_id=_BASE_RATE_ID,
        reference_date=date(2024, 5, 1),
        vintage_date=date(2024, 6, 1),
        value="3.75",
    )
    # as_of 이전 vintage — as_of 시점 알 수 있는 최신값.
    current = _macro(
        indicator_id=_BASE_RATE_ID,
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 10),
        value="3.50",
    )
    provider = _provider(as_of=date(2024, 5, 1), macro_records=[future, current])
    # future vintage(6-1) 가 as_of(5-1) 초과 → 제외. current(4-10) 만 통과.
    assert provider.get_scalar("ecos_base_rate") == Decimal("3.50")


def test_ecos_vintage_provisional_to_final_reproduction() -> None:
    """잠정→확정 재현 — as_of 에 맞는 vintage 선택.

    같은 reference_date 에 잠정 vintage(v_prov=2024-04-10)와 확정
    vintage(v_final=2024-05-20)가 있을 때:
    - as_of ∈ [v_prov, v_final) → 잠정값 반환.
    - as_of >= v_final → 확정값 반환.
    """
    prov = _macro(
        indicator_id=_BASE_RATE_ID,
        reference_date=date(2024, 3, 1),
        vintage_date=date(2024, 4, 10),  # 잠정 공표.
        value="3.50",
    )
    final = _macro(
        indicator_id=_BASE_RATE_ID,
        reference_date=date(2024, 3, 1),
        vintage_date=date(2024, 5, 20),  # 확정 개정.
        value="3.55",
    )
    records = [prov, final]

    # as_of = 확정 공표 전 → 잠정값.
    p_before = _provider(as_of=date(2024, 5, 1), macro_records=records)
    assert p_before.get_scalar("ecos_base_rate") == Decimal("3.50")

    # as_of = 확정 공표 후 → 확정값.
    p_after = _provider(as_of=date(2024, 5, 25), macro_records=records)
    assert p_after.get_scalar("ecos_base_rate") == Decimal("3.55")


def test_ecos_field_in_mini_factor_end_to_end() -> None:
    """ECOS field 를 입력으로 쓰는 mini factor 를 evaluator 로 end-to-end 평가.

    mini factor: ecos_base_rate 를 ratio_pct (백분율) 나누기 100 = fraction.
    formula: ratio_1 / ratio_2 where ratio_1=ecos_base_rate, ratio_2=const 100.
    사실상 ecos_base_rate / 100.
    """
    record = _macro(
        indicator_id=_BASE_RATE_ID,
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 10),
        value="3.50",
    )
    as_of = date(2024, 5, 1)
    provider = _provider(as_of=as_of, macro_records=[record])
    evaluator = FactorEvaluator()

    # mini factor: ecos_base_rate / 100 (div op — evaluator 가 지원).
    # formula inputs 에 ecos_base_rate 가 있어야 provider.get_scalar 가 호출됨.
    # binary op 의 키는 left / right (factor_evaluator.py 스키마 기준).
    mini_factor = {
        "canonical_id": "test:ecos-base-rate-fraction",
        "uuid": "00000000-0000-7000-8000-000000000099",
        "formula": {
            "ast": {
                "op": "div",
                "left": {"field": "ecos_base_rate"},
                "right": {"const": 100},
            },
            "inputs": ["ecos_base_rate"],
        },
    }
    result = evaluator.evaluate(mini_factor, provider, as_of=as_of)
    assert result.is_na is False
    assert result.value == Decimal("3.50") / Decimal("100")


def test_ecos_field_na_propagation_when_macro_repo_none() -> None:
    """macro_repo 미주입 → ecos_base_rate N/A → mini factor missing_input N/A.

    mini factor 가 ecos_base_rate 를 입력으로 쓸 때, macro_repo=None 이면
    evaluator 가 missing_input:ecos_base_rate N/A 로 보고 (기능 미동작 인지 가능).
    """
    as_of = date(2024, 5, 1)
    provider = _provider(as_of=as_of)  # macro_records 미주입 → repo None.
    evaluator = FactorEvaluator()

    mini_factor = {
        "canonical_id": "test:ecos-base-rate-fraction",
        "uuid": "00000000-0000-7000-8000-000000000099",
        "formula": {
            "ast": {
                "op": "div",
                "left": {"field": "ecos_base_rate"},
                "right": {"const": 100},
            },
            "inputs": ["ecos_base_rate"],
        },
    }
    result = evaluator.evaluate(mini_factor, provider, as_of=as_of)
    assert result.is_na is True
    assert result.na_reason == "missing_input:ecos_base_rate"


def test_ecos_macro_request_scoped_caching() -> None:
    """request-scoped 캐싱 — 같은 field 재호출 시 fetch_latest 1 회."""
    record = _macro(
        indicator_id=_BASE_RATE_ID,
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 10),
        value="3.50",
    )
    repo = FakeMacroIndicatorRepository(records=[record])
    calls = {"n": 0}
    orig = repo.fetch_latest

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return orig(*args, **kwargs)

    repo.fetch_latest = _counting  # type: ignore[method-assign]

    provider = DbFieldProvider(
        code=_CODE,
        as_of=date(2024, 5, 1),
        price_repo=FakePriceRepository(records=[]),
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        macro_repo=repo,
    )
    v1 = provider.get_scalar("ecos_base_rate")
    v2 = provider.get_scalar("ecos_base_rate")
    assert v1 == v2 == Decimal("3.50")
    assert calls["n"] == 1  # _scalar_cache 로 2 번째 호출은 repo 미접촉.


# =============================================================================
# 19. 레지스트리 무결성 — factor pack 의 모든 input field 가 등록됨
# =============================================================================

# =============================================================================
# 25-27. KOSIS 매크로 지표 해소 — M9 #2 (ADR-0036 D5/D6)
# =============================================================================

# KOSIS indicator_id 상수 — _RESOLUTIONS 의 매핑과 일치 (라이브 검증 2026-06-18).
_KOSIS_UNEMPLOYMENT_ID = "kosis/101/DT_1DA7001S/T80"
_KOSIS_EMPLOYMENT_ID = "kosis/101/DT_1DA7001S/T90"
_KOSIS_INDUSTRIAL_ID = "kosis/101/DT_1JH20201/T1"
_KOSIS_LEADING_ID = "kosis/101/DT_1C8015/T1"


def test_kosis_unemployment_rate_resolves_to_value() -> None:
    """kosis_unemployment_rate → FakeMacroIndicatorRepository.fetch_latest.value."""
    record = _macro(
        indicator_id=_KOSIS_UNEMPLOYMENT_ID,
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 20),
        value="3.0",
    )
    provider = _provider(as_of=date(2024, 5, 1), macro_records=[record])
    assert provider.get_scalar("kosis_unemployment_rate") == Decimal("3.0")


def test_kosis_employment_rate_resolves_to_value() -> None:
    """kosis_employment_rate → FakeMacroIndicatorRepository.fetch_latest.value."""
    record = _macro(
        indicator_id=_KOSIS_EMPLOYMENT_ID,
        reference_date=date(2024, 3, 1),
        vintage_date=date(2024, 4, 15),
        value="62.5",
    )
    provider = _provider(as_of=date(2024, 5, 1), macro_records=[record])
    assert provider.get_scalar("kosis_employment_rate") == Decimal("62.5")


def test_kosis_industrial_production_resolves_to_value() -> None:
    """kosis_industrial_production → FakeMacroIndicatorRepository.fetch_latest.value."""
    record = _macro(
        indicator_id=_KOSIS_INDUSTRIAL_ID,
        reference_date=date(2024, 2, 1),
        vintage_date=date(2024, 4, 10),
        value="108.3",
    )
    provider = _provider(as_of=date(2024, 5, 1), macro_records=[record])
    assert provider.get_scalar("kosis_industrial_production") == Decimal("108.3")


def test_kosis_leading_index_resolves_to_value() -> None:
    """kosis_leading_index → FakeMacroIndicatorRepository.fetch_latest.value (M9 #2)."""
    record = _macro(
        indicator_id=_KOSIS_LEADING_ID,
        reference_date=date(2024, 1, 1),
        vintage_date=date(2024, 4, 5),
        value="101.2",
    )
    provider = _provider(as_of=date(2024, 5, 1), macro_records=[record])
    assert provider.get_scalar("kosis_leading_index") == Decimal("101.2")


def test_kosis_macro_na_when_repo_none() -> None:
    """macro_repo 미주입(None) → KOSIS field 정식 N/A — ECOS 선례 동일."""
    provider = _provider(as_of=date(2024, 5, 1))
    assert provider.get_scalar("kosis_unemployment_rate") is None
    assert provider.get_scalar("kosis_employment_rate") is None
    assert provider.get_scalar("kosis_industrial_production") is None
    assert provider.get_scalar("kosis_leading_index") is None


def test_kosis_macro_na_when_no_data() -> None:
    """repo 주입됐으나 해당 KOSIS indicator 데이터 없으면 N/A."""
    record = _macro(
        indicator_id="kosis/101/OTHER/X",
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 20),
        value="1.0",
    )
    provider = _provider(as_of=date(2024, 5, 1), macro_records=[record])
    assert provider.get_scalar("kosis_unemployment_rate") is None


def test_kosis_resolve_macro_indicator_unchanged() -> None:
    """_resolve_macro_indicator 무수정 확인 — KOSIS/ECOS 동일 경로.

    KOSIS field 와 ECOS field 가 동일 _resolve_macro_indicator 경로로
    처리됨을 확인 (ADR-0036 D1): ECOS ecos_base_rate 와 KOSIS unemployment
    를 같은 repo 에 함께 seed → 각각 독립 해소.
    """
    ecos_rec = _macro(
        indicator_id=_BASE_RATE_ID,
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 10),
        value="3.50",
    )
    kosis_rec = _macro(
        indicator_id=_KOSIS_UNEMPLOYMENT_ID,
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 20),
        value="2.8",
    )
    provider = _provider(as_of=date(2024, 5, 1), macro_records=[ecos_rec, kosis_rec])
    assert provider.get_scalar("ecos_base_rate") == Decimal("3.50")
    assert provider.get_scalar("kosis_unemployment_rate") == Decimal("2.8")


# =============================================================================
# macro indicator_id 교차검증 — 3개 수기 리스트 drift 방지 (code-review #1)
# =============================================================================
# macro 지표 identity 는 3곳에 수기 중복된다: _RESOLUTIONS(factor 해소 경로)·
# market._MACRO_INDICATORS(표시 경로)·batch._KOSIS_INDICATORS(적재 경로). 일치를
# 강제하는 코드가 없어 한 곳의 id 오타가 나면 배치는 한 id 로 적재하는데 표시/factor
# 는 다른 id 로 조회 → 런타임 에러 없이 조용히 half-wired. 아래 두 테스트가 그 drift
# 를 fail-loud 로 잡는다 (ADR-0036 D6 — 표시 + factor field 동일 집합 불변식).


def test_macro_field_ids_match_market_overview_display() -> None:
    """모든 macro_indicator factor field 의 indicator_id 집합 == 표시 목록 id 집합.

    한 곳의 id 가 오타로 갈라지면 display 에는 값이 보이는데 factor 평가는 N/A
    (또는 반대)로 어긋난다. ADR-0036 D6: 매크로는 표시 + factor field 둘 다 등록 →
    두 집합이 동일해야 한다. 의도적 단일경로 추가 시 본 테스트를 함께 갱신(문서화).
    """
    from app.api.routes.market import _MACRO_INDICATORS

    factor_macro_ids = {
        r.indicator_id
        for r in FIELD_RESOLUTIONS.values()
        if r.kind == "macro_indicator"
    }
    display_ids = {indicator_id for indicator_id, _ in _MACRO_INDICATORS}
    assert factor_macro_ids == display_ids, (
        f"macro factor id 집합과 표시 id 집합 불일치 — "
        f"factor만: {sorted(factor_macro_ids - display_ids)}, "
        f"표시만: {sorted(display_ids - factor_macro_ids)}"
    )


def test_kosis_batch_indicator_ids_match_field_resolutions() -> None:
    """배치 _KOSIS_INDICATORS 가 생성하는 indicator_id 가 KOSIS factor field 와 일치.

    배치가 적재하는 id 와 field/표시가 조회하는 id 가 달라지면 적재된 row 를 영영
    찾지 못한다(silent half-wire). id 규약은 kosis_adapter.py:425
    `f"kosis/{org_id}/{tbl_id}/{itm_id}"` — 여기서도 동일 규약으로 재구성해 대조.
    """
    from batch.kosis_daily import _KOSIS_INDICATORS

    batch_ids = {
        f"kosis/{ind.org_id}/{ind.tbl_id}/{ind.itm_id}" for ind in _KOSIS_INDICATORS
    }
    kosis_factor_ids = {
        r.indicator_id
        for r in FIELD_RESOLUTIONS.values()
        if r.kind == "macro_indicator" and r.indicator_id.startswith("kosis/")
    }
    assert batch_ids == kosis_factor_ids, (
        f"배치 KOSIS id 와 factor field KOSIS id 불일치 — "
        f"배치만: {sorted(batch_ids - kosis_factor_ids)}, "
        f"field만: {sorted(kosis_factor_ids - batch_ids)}"
    )


def test_registry_covers_all_builtin_pack_inputs() -> None:
    """빌트인 factor pack 의 모든 formula.inputs field 가 레지스트리에 있음.

    누락 시 endpoint 가 UnknownFieldError 로 500 — pack 과 provider schema 의
    drift 를 단위 테스트로 사전 차단.
    """
    import json
    from pathlib import Path

    pack_path = (
        Path(__file__).resolve().parents[1]
        / "builtin-packs" / "factors" / "speculum-builtin-v1.0.0.json"
    )
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    all_inputs: set[str] = set()
    for factor in pack["factors"]:
        all_inputs.update(factor["formula"]["inputs"])
    missing = all_inputs - set(FIELD_RESOLUTIONS.keys())
    assert not missing, f"레지스트리 미등록 field: {sorted(missing)}"


# =============================================================================
# B1 (ROADMAP_v2 V1b) — DART 분기 누적값 → standalone 변환 (FLOW 계정)
# =============================================================================

def test_b1_flow_cumulative_to_standalone_series() -> None:
    """FLOW 계정(basic_eps) 누적값을 standalone 으로 차분 — 005930 실데이터 형태.

    DART 분기보고서 누적(YTD): Q1=3M, Q2=6M, Q3=9M, Q4=연간. 최근 4분기 standalone =
    [Q2-Q1, Q3-Q2, Q4-Q3, 다음Q1(=standalone)]. 삼성 2024-06-28: TTM EPS = 2900.
    """
    as_of = date(2024, 6, 28)
    # 누적값(005930 실측): Q1=206 Q2=228 Q3=810 Q4=2131 / 2024Q1=975
    records = [
        _fin(account="basic_eps", fiscal_period="2023Q1", value="206",
             effective_date=date(2023, 5, 15)),
        _fin(account="basic_eps", fiscal_period="2023Q2", value="228",
             effective_date=date(2023, 8, 14)),
        _fin(account="basic_eps", fiscal_period="2023Q3", value="810",
             effective_date=date(2023, 11, 14)),
        _fin(account="basic_eps", fiscal_period="2023Q4", value="2131",
             effective_date=date(2024, 3, 12)),
        _fin(account="basic_eps", fiscal_period="2024Q1", value="975",
             effective_date=date(2024, 5, 16)),
    ]
    prov = _provider(as_of=as_of, financials=records)
    series = prov._resolve_financial_series(
        FIELD_RESOLUTIONS["basic_eps_consolidated_ifrs"], n=4,
    )
    # 최근 4분기 [Q2'23,Q3'23,Q4'23,Q1'24] standalone.
    assert series == (
        Decimal("22"),    # 228-206
        Decimal("582"),   # 810-228
        Decimal("1321"),  # 2131-810
        Decimal("975"),   # Q1=standalone(직전 차감 없음)
    )
    assert sum(series, Decimal(0)) == Decimal("2900")


def test_b1_q1_is_standalone_no_prior_subtraction() -> None:
    """회계연도 1분기는 누적=3M=standalone — 직전(전년 Q4) 차감 안 함."""
    as_of = date(2024, 6, 28)
    # 2개 회계연도 1분기 — 각 Q1 은 직전 차감 없이 그대로.
    records = [
        _fin(account="basic_eps", fiscal_period="2023Q4", value="2131",
             effective_date=date(2024, 3, 12)),
        _fin(account="basic_eps", fiscal_period="2024Q1", value="975",
             effective_date=date(2024, 5, 16)),
    ]
    prov = _provider(as_of=as_of, financials=records)
    series = prov._resolve_financial_series(
        FIELD_RESOLUTIONS["basic_eps_consolidated_ifrs"], n=2,
    )
    # Q4'23 standalone 은 Q3'23 결손이라 산출 불가 → 전체 strict 빈 tuple.
    assert series == ()


def test_b1_missing_prior_quarter_strict_na() -> None:
    """직전 분기(q>1) 결손이면 standalone 산출 불가 → strict 빈 tuple."""
    as_of = date(2024, 6, 28)
    # Q1 결손 + Q2(6M 누적) — Q2 standalone 은 Q1 차감 필요 → 결손 → 빈 tuple.
    records = [
        _fin(account="basic_eps", fiscal_period="2023Q2", value="228",
             effective_date=date(2023, 8, 14)),
        _fin(account="basic_eps", fiscal_period="2023Q3", value="810",
             effective_date=date(2023, 11, 14)),
    ]
    prov = _provider(as_of=as_of, financials=records)
    series = prov._resolve_financial_series(
        FIELD_RESOLUTIONS["basic_eps_consolidated_ifrs"], n=2,
    )
    # Q2 standalone 의 직전(Q1) 결손 → strict.
    assert series == ()
