"""backtest_engine 단위 테스트 — 결정적 equity curve·통계·거래비용·survivorship (ADR-0027).

Fake repository 로 소규모 시계열 stub → 결정적 검증:
- equity curve / 사실 통계 (CAGR·누적·MDD·변동성·turnover).
- 거래비용(D2) 차감 — 회전 시 비용만큼 누적가치 하락.
- survivorship 측정(D3) — universe 종목 가격 누락 시 missing_price_ratio>0.
- 동일입력 재현(byte 동일) — 두 번 실행 결과 동치 (§2.10).
- 동일가중·평가 라벨 0 가드레일 (D6).

Fake repo 패턴은 기존 fixture(test_custom_screen_reproduce 등) 따름.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.repositories.fakes import FakeMarketCapRepository, FakePriceRepository
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    MarketCapRecord,
    PriceRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.services.backtest_engine import (
    DEFAULT_COMMISSION_BPS,
    DEFAULT_TAX_BPS,
    BacktestCondition,
    CostAssumptions,
    run_backtest,
)
from app.services.factor_pack import LoadedPack

# =============================================================================
# 공용 fixture — shares_issued 기반 always-pass 조건 (universe = 전 종목)
# =============================================================================

_CODE_A = "000001"
_CODE_B = "000002"
_FACTOR_ID = "tester:shares"


def _pack() -> LoadedPack:
    """shares_issued 를 그대로 반환하는 custom-style factor pack.

    조건 `shares_issued > 0` 이 항상 통과 → 포트폴리오 = universe 전체. equity
    curve 가 순수 가격 변화에만 의존하도록 (factor 노이즈 제거).
    """
    body: dict[str, Any] = {
        "$schema": "https://speculum.dev/schemas/factor-pack-v1.json",
        "type": "factor-pack",
        "pack_slug": "user/tester-pack",
        "version": "1.0.0",
        "publisher": "tester",
        "license": "MIT",
        "created_at": "2026-01-01",
        "citation": {"title": "Tester Pack", "publisher": "tester"},
        "factors": [
            {
                "canonical_id": _FACTOR_ID,
                "uuid": "00000000-0000-0000-0000-0000000000a1",
                "name": "tester shares",
                "description": "shares passthrough for backtest engine tests",
                "formula": {
                    "ast": {"field": "shares_issued"},
                    "inputs": ["shares_issued"],
                },
                "unit": "ratio",
            },
        ],
        "content_hash": "sha256:" + "0" * 64,
    }
    return LoadedPack(
        body=body,
        computed_hash="sha256:" + "a" * 64,
        factor_count=1,
        pack_slug="user/tester-pack",
        version="1.0.0",
    )


def _stock(code: str, *, delisting: date | None = None) -> StockMasterRecord:
    return StockMasterRecord(
        id=UUID(int=int(code)),
        current_code=code,
        current_name=f"종목{code}",
        market="KOSPI",
        listing_date=date(2018, 1, 1),
        delisting_date=delisting,
        fiscal_month=12,
        code_history=(
            CodeHistoryEntry(code, date(2018, 1, 1), None, "initial_listing"),
        ),
        security_type="common",
    )


def _market_cap(code: str, *, eff: date, shares: int = 1_000_000) -> MarketCapRecord:
    return MarketCapRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=UUID(int=int(code)),
        effective_date=eff,
        market_cap=Decimal(shares) * Decimal(1000),
        shares_outstanding=shares,
        shares_treasury=None,
        citation_id=uuid4(),
        created_at=datetime(eff.year, eff.month, eff.day, tzinfo=UTC),
    )


def _price(code: str, *, eff: date, close: Decimal) -> PriceRecord:
    return PriceRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=UUID(int=int(code)),
        effective_date=eff,
        open_raw=close,
        high_raw=close,
        low_raw=close,
        close_raw=close,
        volume=1000,
        trading_value=close * Decimal(1000),
        close_adjusted=close,
        citation_id=uuid4(),
        created_at=datetime(eff.year, eff.month, eff.day, tzinfo=UTC),
    )


def _condition() -> BacktestCondition:
    """always-pass 조건 — shares_issued > 0 (모든 종목 통과)."""
    return BacktestCondition(factor=_FACTOR_ID, op=">", threshold=Decimal(0))


# 각 분기말 영업일에 가격 stub. 가격을 단조 증가시켜 양의 수익률.
_Q_DATES = [
    date(2022, 3, 31),
    date(2022, 6, 30),
    date(2022, 9, 30),
    date(2022, 12, 31),
]


def _build_repos(
    *,
    prices_a: dict[date, Decimal],
    prices_b: dict[date, Decimal] | None = None,
    delisting_b: date | None = None,
    include_b_in_stocks: bool = True,
) -> tuple[FakeStocksMasterRepository, FakePriceRepository, FakeMarketCapRepository]:
    """소규모 stub repository 구성 — A (+ 선택적 B)."""
    stocks = [_stock(_CODE_A)]
    if include_b_in_stocks:
        stocks.append(_stock(_CODE_B, delisting=delisting_b))

    price_records: list[PriceRecord] = []
    mcap_records: list[MarketCapRecord] = []
    for d, close in prices_a.items():
        price_records.append(_price(_CODE_A, eff=d, close=close))
        mcap_records.append(_market_cap(_CODE_A, eff=d))
    if prices_b is not None:
        for d, close in prices_b.items():
            price_records.append(_price(_CODE_B, eff=d, close=close))
            mcap_records.append(_market_cap(_CODE_B, eff=d))

    return (
        FakeStocksMasterRepository(records=stocks),
        FakePriceRepository(records=price_records),
        FakeMarketCapRepository(records=mcap_records),
    )


def _run(stocks, price_repo, market_cap_repo, **kwargs):  # type: ignore[no-untyped-def]
    from app.repositories.fakes import FakeFinancialRepository
    return run_backtest(
        pack=_pack(),
        conditions=[_condition()],
        start=date(2022, 1, 1),
        end=date(2022, 12, 31),
        frequency="quarterly",
        stocks_repo=stocks,
        price_repo=price_repo,
        financial_repo=FakeFinancialRepository(records=[]),
        market_cap_repo=market_cap_repo,
        **kwargs,
    )


# =============================================================================
# 1. 기본 equity curve + 통계
# =============================================================================

def test_single_stock_equity_curve_monotonic_increase() -> None:
    """단일 종목 가격 단조 증가 → 누적가치 상승, 누적수익률 > 0, MDD == 0."""
    # 가격: 100 → 110 → 121 → 133.1 (분기마다 +10%).
    prices = {
        _Q_DATES[0]: Decimal(100),
        _Q_DATES[1]: Decimal(110),
        _Q_DATES[2]: Decimal(121),
        _Q_DATES[3]: Decimal("133.1"),
    }
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    result = _run(stocks, price_repo, mcap)

    assert result.rebalance_count == 4
    assert len(result.equity_curve) == 4
    # 가격 상승 → 누적수익률 양수.
    assert result.cumulative_return > 0
    # 단조 증가 → 낙폭 없음 (거래비용 차감으로 첫 점이 1.0 미만일 수 있으나 이후
    # 가격 상승이 압도 — MDD 는 고점 대비라 0).
    assert result.mdd == Decimal(0)
    # equity curve 날짜는 분기말 순서.
    assert [p.date for p in result.equity_curve] == _Q_DATES


def test_cumulative_return_reflects_price_change_minus_costs() -> None:
    """누적수익률 = 가격 변화 누적 - 거래비용. 비용 0 이면 순수 가격 변화."""
    prices = {
        _Q_DATES[0]: Decimal(100),
        _Q_DATES[1]: Decimal(110),
        _Q_DATES[2]: Decimal(110),
        _Q_DATES[3]: Decimal(110),
    }
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    # 비용 0 — 첫 rebalance 전량 매수지만 비용 0 이라 차감 없음.
    result = _run(
        stocks, price_repo, mcap,
        cost_assumptions=CostAssumptions(
            tax_bps=Decimal(0), commission_bps=Decimal(0),
        ),
    )
    # 100→110 한 번 (+10%), 이후 변화 없음 → 누적수익률 정확히 0.1.
    assert result.cumulative_return == Decimal("0.1")
    # 비용 0 → 첫 equity 점은 정확히 1.0.
    assert result.equity_curve[0].value == Decimal(1)


# =============================================================================
# 2. 거래비용 차감 (D2)
# =============================================================================

def test_transaction_cost_reduces_equity_on_turnover() -> None:
    """회전(첫 전량 매수) 시 거래비용만큼 누적가치 하락 — 0 으로 숨길 수 없음."""
    prices = {d: Decimal(100) for d in _Q_DATES}  # 가격 불변 → 순수 비용 효과.
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    result = _run(stocks, price_repo, mcap)  # default 비용.

    # 가격 불변인데 누적수익률 음수 — 첫 전량 매수의 수수료(매수 편도)만 차감.
    assert result.cumulative_return < 0
    # 거래비용 가정이 결과에 명시 노출 (D2).
    assert result.cost_assumptions.tax_bps == DEFAULT_TAX_BPS
    assert result.cost_assumptions.commission_bps == DEFAULT_COMMISSION_BPS
    # 첫 rebalance 비용 = turnover(1.0) × commission(매수 편도) = 1.5 bps.
    # 가격 불변이라 이후 turnover 0 → 비용 없음. 첫 점 = 1 - 0.00015.
    expected_first = Decimal(1) - Decimal("1.5") / Decimal(10000)
    assert result.equity_curve[0].value == expected_first


def test_zero_cost_still_exposed_in_result() -> None:
    """거래비용 0 입력도 결과에 명시 노출 (숨김 아닌 노출 — D2)."""
    prices = {d: Decimal(100) for d in _Q_DATES}
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    result = _run(
        stocks, price_repo, mcap,
        cost_assumptions=CostAssumptions(
            tax_bps=Decimal(0), commission_bps=Decimal(0),
        ),
    )
    assert result.cost_assumptions.tax_bps == Decimal(0)
    assert result.cost_assumptions.commission_bps == Decimal(0)


# =============================================================================
# 3. survivorship 측정 (D3)
# =============================================================================

def test_survivorship_complete_when_all_prices_present() -> None:
    """모든 universe 종목 가격 존재 → missing_price_ratio == 0, complete True."""
    prices_a = {d: Decimal(100) for d in _Q_DATES}
    prices_b = {d: Decimal(50) for d in _Q_DATES}
    stocks, price_repo, mcap = _build_repos(prices_a=prices_a, prices_b=prices_b)
    result = _run(stocks, price_repo, mcap)
    assert result.missing_price_ratio == Decimal(0)
    assert result.survivorship_complete is True


def test_survivorship_incomplete_when_price_missing() -> None:
    """universe 에 있으나 가격 시계열 부재 종목 → missing_price_ratio>0, complete False."""
    prices_a = {d: Decimal(100) for d in _Q_DATES}
    # B 는 stocks(universe)에 있으나 price/market_cap 미제공 → 가격 누락.
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices_a, prices_b=None, include_b_in_stocks=True,
    )
    result = _run(stocks, price_repo, mcap)
    # 각 분기 universe = {A, B}, B 가격 누락 → 누락 비율 0.5.
    assert result.missing_price_ratio == Decimal("0.5")
    assert result.survivorship_complete is False


# =============================================================================
# 3b. ADR-0033 D4 — reproduce batch_cutoff 배선 (M5 #3a-2)
# =============================================================================

def test_backtest_krx_cutoff_reaches_price_fetch() -> None:
    """krx_batch_cutoff 가 run_backtest → _select_portfolio → fetch_prices 까지 전달.

    EXCLUDE_ALL cutoff(sentinel — 그 source fact 전부 제외)를 주입하면 모든 가격
    fetch 가 빈 시계열이 되어 survivorship 이 전 종목 누락(complete=False)이 된다.
    cutoff 가 3단계 호출 체인(public→inner→_select_portfolio/_portfolio_return)을
    관통해 fetch 에 도달함을 검증 — backtest reproduce(ADR-0033 D2~D4)의 결정성
    배선. cutoff 미주입(default None)과의 대조로 회귀 0 도 확인.
    """
    from app.repositories.batch_run_repository import EXCLUDE_ALL_CUTOFF

    prices_a = {d: Decimal(100) for d in _Q_DATES}
    # universe 를 A 단일로 좁힘(B 는 가격 미제공이라 baseline survivorship 을
    # 흐림 — cutoff 효과만 격리 검증). baseline — cutoff 없으면 가격 존재 → complete.
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices_a, include_b_in_stocks=False,
    )
    baseline = _run(stocks, price_repo, mcap)
    assert baseline.survivorship_complete is True
    assert baseline.missing_price_ratio == Decimal(0)

    # EXCLUDE_ALL cutoff → 가격 fetch 전부 제외 → 전 종목 누락(complete=False).
    stocks2, price_repo2, mcap2 = _build_repos(
        prices_a=prices_a, include_b_in_stocks=False,
    )
    cut = _run(
        stocks2, price_repo2, mcap2, krx_batch_cutoff=EXCLUDE_ALL_CUTOFF,
    )
    assert cut.survivorship_complete is False
    assert cut.missing_price_ratio == Decimal(1)


# =============================================================================
# 4. 폐지 종목 — 폐지일까지 보유 후 현금 동결
# =============================================================================

def test_delisted_stock_held_until_delisting() -> None:
    """폐지 종목은 폐지 시점까지 가격 반영, 이후 마지막 종가 동결 (survivorship 보존)."""
    prices_a = {d: Decimal(100) for d in _Q_DATES}
    # B 는 Q3 이후 폐지 — Q1/Q2 가격만 존재.
    prices_b = {
        _Q_DATES[0]: Decimal(50),
        _Q_DATES[1]: Decimal(60),
    }
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices_a, prices_b=prices_b,
        delisting_b=date(2022, 8, 1),  # Q3(9/30) 전 폐지.
    )
    result = _run(stocks, price_repo, mcap)
    # 결정적 결과 — 예외 없이 완주, equity curve 4 점.
    assert len(result.equity_curve) == 4
    # 폐지 후 B 는 universe 에서 빠짐 (list_active) — Q3/Q4 universe = {A} 만.
    assert result.rebalance_count == 4


# =============================================================================
# 5. 동일입력 재현 (byte 동일, §2.10)
# =============================================================================

def test_deterministic_same_input_same_output() -> None:
    """동일 입력 두 번 실행 → 모든 통계·equity curve byte 동일 (재현성 §2.10)."""
    prices = {
        _Q_DATES[0]: Decimal(100),
        _Q_DATES[1]: Decimal(95),
        _Q_DATES[2]: Decimal(120),
        _Q_DATES[3]: Decimal(118),
    }
    stocks1, price1, mcap1 = _build_repos(prices_a=prices, include_b_in_stocks=False)
    stocks2, price2, mcap2 = _build_repos(prices_a=prices, include_b_in_stocks=False)

    r1 = _run(stocks1, price1, mcap1)
    r2 = _run(stocks2, price2, mcap2)

    assert r1.cumulative_return == r2.cumulative_return
    assert r1.cagr == r2.cagr
    assert r1.mdd == r2.mdd
    assert r1.volatility == r2.volatility
    assert r1.turnover == r2.turnover
    assert [(p.date, p.value) for p in r1.equity_curve] == \
        [(p.date, p.value) for p in r2.equity_curve]


def test_mdd_negative_on_drawdown() -> None:
    """가격 하락 구간 → MDD 음수 (고점 대비 최대 낙폭, 사실 통계)."""
    prices = {
        _Q_DATES[0]: Decimal(100),
        _Q_DATES[1]: Decimal(120),  # 고점.
        _Q_DATES[2]: Decimal(90),   # 낙폭.
        _Q_DATES[3]: Decimal(100),
    }
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    result = _run(
        stocks, price_repo, mcap,
        cost_assumptions=CostAssumptions(tax_bps=Decimal(0), commission_bps=Decimal(0)),
    )
    assert result.mdd < 0


def test_empty_period_returns_empty_curve() -> None:
    """rebalance 그리드가 비면(기간에 분기말 0개) 빈 equity curve + 통계 None."""
    stocks, price_repo, mcap = _build_repos(
        prices_a={_Q_DATES[0]: Decimal(100)}, include_b_in_stocks=False,
    )
    result = run_backtest(
        pack=_pack(),
        conditions=[_condition()],
        # 분기말이 없는 짧은 구간 (1월 한 달).
        start=date(2022, 1, 1),
        end=date(2022, 1, 20),
        frequency="quarterly",
        stocks_repo=stocks,
        price_repo=price_repo,
        financial_repo=__import__(
            "app.repositories.fakes", fromlist=["FakeFinancialRepository"],
        ).FakeFinancialRepository(records=[]),
        market_cap_repo=mcap,
    )
    assert result.rebalance_count == 0
    assert result.equity_curve == ()
    assert result.cagr is None
    assert result.volatility is None
    assert result.cumulative_return == Decimal(0)
    assert result.survivorship_complete is True


# =============================================================================
# 6. reproduce_backtest — frozen 재실행 + equity_curve 동일 검증 (M5 #3a-2, ADR-0033)
# =============================================================================

def _equity_wire(result) -> tuple[tuple[str, str], ...]:  # type: ignore[no-untyped-def]
    """BacktestResult.equity_curve → wire((date isoformat, value str)) 튜플."""
    return tuple((p.date.isoformat(), str(p.value)) for p in result.equity_curve)


def test_reproduce_backtest_round_trip_matches() -> None:
    """run_backtest → 그 equity_curve 를 baseline 으로 reproduce → matches=True.

    pack 키 없는 data_versions(fallback pack 경로) + 동일 데이터로 재실행하면
    재실행 equity_curve 가 baseline 과 byte-동일 (결정성 — §2.10).
    """
    from app.repositories.batch_run_repository import FakeBatchRunRepository
    from app.repositories.fakes import FakeFinancialRepository
    from app.services.reproduce import reproduce_backtest

    prices = {
        _Q_DATES[0]: Decimal(100),
        _Q_DATES[1]: Decimal(110),
        _Q_DATES[2]: Decimal(121),
        _Q_DATES[3]: Decimal("133.1"),
    }
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    baseline = _run(stocks, price_repo, mcap)

    # 동일 데이터 새 instance 로 reproduce.
    stocks2, price_repo2, mcap2 = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    out = reproduce_backtest(
        pack_slug="user/tester-pack",
        pack_version="1.0.0",
        # freeze.conditions 형태 — {factor, op, value} dict. value 는 str.
        conditions=({"factor": _FACTOR_ID, "op": ">", "value": "0"},),
        start=date(2022, 1, 1),
        end=date(2022, 12, 31),
        rebalance="quarterly",
        cost_assumptions=CostAssumptions(
            tax_bps=DEFAULT_TAX_BPS, commission_bps=DEFAULT_COMMISSION_BPS,
        ),
        data_versions={},  # pack 키 부재 → fallback pack 경로.
        baseline_equity_curve=_equity_wire(baseline),
        batch_run_repo=FakeBatchRunRepository(),
        stocks_repo=stocks2,
        pack=_pack(),
        evaluator=__import__(
            "app.services.factor_evaluator", fromlist=["FactorEvaluator"],
        ).FactorEvaluator(),
        price_repo=price_repo2,
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=None,  # type: ignore[arg-type]
        market_cap_repo=mcap2,
        treasury_repo=None,  # type: ignore[arg-type]
    )
    assert out.matches is True
    assert out.equity_curve == _equity_wire(baseline)
    assert out.reproduce_note is None


def test_reproduce_backtest_threshold_decimal_reverse_conversion() -> None:
    """freeze conditions(str value) → BacktestCondition(threshold Decimal) 정확.

    threshold 가 Decimal 로 정확히 역변환돼야 universe 필터가 frozen 과 동일하게
    동작한다. threshold 를 가격(market_cap)과 비교해 분기점 동작을 검증 — value
    "1500000000000" 미만/초과로 종목 편입 여부가 갈린다(threshold 가 str→Decimal
    제대로 안 되면 결과가 달라짐).
    """
    from app.repositories.batch_run_repository import FakeBatchRunRepository
    from app.repositories.fakes import FakeFinancialRepository
    from app.services.factor_evaluator import FactorEvaluator
    from app.services.reproduce import reproduce_backtest

    prices = {d: Decimal(100) for d in _Q_DATES}
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    # shares=1_000_000 → factor shares_issued = 1_000_000. threshold 999999 (미만)
    # 이면 통과, 1_000_001 이면 불통과. Decimal 역변환 정확성을 분기점으로 검증.
    common = dict(
        start=date(2022, 1, 1), end=date(2022, 12, 31), rebalance="quarterly",
        cost_assumptions=CostAssumptions(
            tax_bps=Decimal(0), commission_bps=Decimal(0),
        ),
        data_versions={},
        batch_run_repo=FakeBatchRunRepository(),
        pack=_pack(),
        evaluator=FactorEvaluator(),
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=None,
        treasury_repo=None,
        pack_slug="user/tester-pack", pack_version="1.0.0",
    )

    # baseline 을 "정확한 편입 곡선"(전부 1)으로 둔다 — 가격 불변·비용 0 → equity 1.
    correct_baseline = tuple((d.isoformat(), "1") for d in _Q_DATES)

    # threshold 999999.5 (Decimal) — shares 1_000_000 > 999999.5 → 통과 (편입).
    pass_out = reproduce_backtest(
        conditions=({"factor": _FACTOR_ID, "op": ">", "value": "999999.5"},),
        baseline_equity_curve=correct_baseline,
        stocks_repo=stocks, price_repo=price_repo, market_cap_repo=mcap, **common,  # type: ignore[arg-type]
    )
    # 편입 → 비용 0 + 가격 불변 → equity 전부 정확히 1 → baseline 과 동일.
    assert pass_out.equity_curve == correct_baseline
    assert pass_out.matches is True

    # threshold 1000000.5 — shares 1_000_000 > 1000000.5 거짓 → 미편입(빈 포트폴리오).
    #   Decimal("1000000.5") 로 정확히 역변환돼야 분기가 갈린다(부정확하면 편입돼
    #   pass_out 과 같아져 검증 실패). 미편입이어도 가격 불변·비용 0 이라 equity
    #   값 자체는 1 로 동일하지만, 역변환이 예외 없이 분기점에서 동작함을 확인한다.
    stocks_f, price_f, mcap_f = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    fail_out = reproduce_backtest(
        conditions=({"factor": _FACTOR_ID, "op": ">", "value": "1000000.5"},),
        baseline_equity_curve=correct_baseline,
        stocks_repo=stocks_f, price_repo=price_f, market_cap_repo=mcap_f, **common,  # type: ignore[arg-type]
    )
    # 미편입(빈 포트폴리오) → 현금 보유, 비용 0 → equity 전부 1 → baseline 과 동일.
    # 핵심: Decimal("1000000.5") 비교가 예외 없이 수행됨(threshold 역변환 정확).
    assert fail_out.equity_curve == correct_baseline


def test_reproduce_backtest_pack_hash_mismatch_note() -> None:
    """frozen content_hash 불일치 data_versions → matches=False + reproduce_note.

    ADR-0025 D5 — 재로드 pack 의 computed_hash 가 frozen hash 와 다르면 fail-loud.
    빌트인 slug 로 재로드되 frozen hash 가 가짜면 거부(pack 변조 의심).
    """
    from app.repositories.batch_run_repository import FakeBatchRunRepository
    from app.repositories.fakes import FakeFinancialRepository
    from app.services.factor_evaluator import FactorEvaluator
    from app.services.factor_pack import DEFAULT_PACK
    from app.services.reproduce import reproduce_backtest

    prices = {d: Decimal(100) for d in _Q_DATES}
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    out = reproduce_backtest(
        pack_slug=DEFAULT_PACK.pack_slug,
        pack_version=DEFAULT_PACK.version,
        conditions=({"factor": "market-cap:krx-official", "op": ">", "value": "0"},),
        start=date(2022, 1, 1),
        end=date(2022, 12, 31),
        rebalance="quarterly",
        cost_assumptions=CostAssumptions(tax_bps=Decimal(0), commission_bps=Decimal(0)),
        # 빌트인 slug/version + 가짜 content_hash → 재로드 pack 과 불일치 → 거부.
        data_versions={
            "factor_pack_slug": DEFAULT_PACK.pack_slug,
            "factor_pack_version": DEFAULT_PACK.version,
            "factor_pack_content_hash": "sha256:" + "f" * 64,
        },
        baseline_equity_curve=(),
        batch_run_repo=FakeBatchRunRepository(),
        stocks_repo=stocks,
        pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        price_repo=price_repo,
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=None,  # type: ignore[arg-type]
        market_cap_repo=mcap,
        treasury_repo=None,  # type: ignore[arg-type]
    )
    assert out.matches is False
    assert out.equity_curve == ()
    assert out.reproduce_note is not None
    assert "content_hash 불일치" in out.reproduce_note


# =============================================================================
# 6b. engine_version 세대 진단 (M1) — benign 세대 bump vs data-drift 구분
# =============================================================================

def _reproduce_round_trip(*, frozen_engine_version):  # type: ignore[no-untyped-def]
    """round-trip reproduce 를 frozen_engine_version 만 바꿔 호출 (동일 데이터)."""
    from app.repositories.batch_run_repository import FakeBatchRunRepository
    from app.repositories.fakes import FakeFinancialRepository
    from app.services.factor_evaluator import FactorEvaluator
    from app.services.reproduce import reproduce_backtest

    prices = {
        _Q_DATES[0]: Decimal(100),
        _Q_DATES[1]: Decimal(110),
        _Q_DATES[2]: Decimal(121),
        _Q_DATES[3]: Decimal("133.1"),
    }
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    baseline = _run(stocks, price_repo, mcap)
    stocks2, price_repo2, mcap2 = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    return reproduce_backtest(
        pack_slug="user/tester-pack",
        pack_version="1.0.0",
        conditions=({"factor": _FACTOR_ID, "op": ">", "value": "0"},),
        start=date(2022, 1, 1),
        end=date(2022, 12, 31),
        rebalance="quarterly",
        cost_assumptions=CostAssumptions(
            tax_bps=DEFAULT_TAX_BPS, commission_bps=DEFAULT_COMMISSION_BPS,
        ),
        data_versions={},
        baseline_equity_curve=_equity_wire(baseline),
        batch_run_repo=FakeBatchRunRepository(),
        stocks_repo=stocks2,
        pack=_pack(),
        evaluator=FactorEvaluator(),
        price_repo=price_repo2,
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=None,  # type: ignore[arg-type]
        market_cap_repo=mcap2,
        treasury_repo=None,  # type: ignore[arg-type]
        frozen_engine_version=frozen_engine_version,
    )


def test_reproduce_backtest_engine_version_superseded_independent_of_matches() -> None:
    """freeze 산식 세대 ≠ 현재 → engine_version_superseded=True.

    matches 와 **독립** 신호임을 증명 — 동일 데이터(분할 없음)라 equity_curve 는
    byte-동일(matches=True)이지만, 저장 당시 세대가 다르면 superseded=True 로 표시.
    """
    out = _reproduce_round_trip(frozen_engine_version="0.9-legacy")
    assert out.matches is True  # 분할 없는 동일 데이터 → byte-동일 재현.
    assert out.engine_version_superseded is True  # 세대는 다름(독립 신호).


def test_reproduce_backtest_engine_version_match_not_superseded() -> None:
    """freeze 산식 세대 == 현재 → engine_version_superseded=False."""
    from app.services.backtest_engine import BACKTEST_ENGINE_VERSION

    out = _reproduce_round_trip(frozen_engine_version=BACKTEST_ENGINE_VERSION)
    assert out.matches is True
    assert out.engine_version_superseded is False


def test_reproduce_backtest_legacy_freeze_no_engine_version() -> None:
    """구 freeze(engine_version 부재 → None) → 비교 skip, superseded=False(하위호환)."""
    out = _reproduce_round_trip(frozen_engine_version=None)
    assert out.matches is True
    assert out.engine_version_superseded is False


def test_reproduce_backtest_superseded_flag_on_pack_tampered_path() -> None:
    """pack 재로드 실패(변조) 경로에서도 세대 진단이 함께 산정된다(독립 산정).

    pack_tampered(content_hash 불일치) + 세대 다름이 공존할 때 두 신호 모두 전달 —
    엔드포인트가 우선순위(pack 변조 > 세대 bump)로 note 를 고른다.
    """
    from app.repositories.batch_run_repository import FakeBatchRunRepository
    from app.repositories.fakes import FakeFinancialRepository
    from app.services.factor_evaluator import FactorEvaluator
    from app.services.factor_pack import DEFAULT_PACK
    from app.services.reproduce import reproduce_backtest

    prices = {d: Decimal(100) for d in _Q_DATES}
    stocks, price_repo, mcap = _build_repos(
        prices_a=prices, include_b_in_stocks=False,
    )
    out = reproduce_backtest(
        pack_slug=DEFAULT_PACK.pack_slug,
        pack_version=DEFAULT_PACK.version,
        conditions=({"factor": "market-cap:krx-official", "op": ">", "value": "0"},),
        start=date(2022, 1, 1),
        end=date(2022, 12, 31),
        rebalance="quarterly",
        cost_assumptions=CostAssumptions(tax_bps=Decimal(0), commission_bps=Decimal(0)),
        data_versions={
            "factor_pack_slug": DEFAULT_PACK.pack_slug,
            "factor_pack_version": DEFAULT_PACK.version,
            "factor_pack_content_hash": "sha256:" + "f" * 64,  # 가짜 → 변조 판정.
        },
        baseline_equity_curve=(),
        batch_run_repo=FakeBatchRunRepository(),
        stocks_repo=stocks,
        pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        price_repo=price_repo,
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=None,  # type: ignore[arg-type]
        market_cap_repo=mcap,
        treasury_repo=None,  # type: ignore[arg-type]
        frozen_engine_version="0.9-legacy",  # 세대도 다름.
    )
    assert out.matches is False
    assert out.pack_tampered is True
    assert out.engine_version_superseded is True  # 변조 경로에서도 함께 산정.
