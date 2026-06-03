"""백테스트 엔진 — PIT rebalance 그리드 순회 + 사실 통계 산출 (ADR-0027 D1~D3/D6).

본 모듈은 PIT 데이터 layer 의 첫 대량 소비자다. 기존 인프라를 *시점 순회*로
조립한다 — 신규 발명 최소 (ADR-0027 Rationale):

- `StocksMasterRepository.list_active(as_of=t)` + `security_type=="common"` →
  t 시점 universe (survivorship-correct PIT 복원, ADR-0009 D7).
- 종목별 `DbFieldProvider(code, as_of=t)` + `FactorEvaluator` → pack conditions
  AND 통과 종목 = 포트폴리오 (screen.py 의 조건 매칭과 동일 의미론).
- **동일가중** (equal-weight 고정) — 가중 최적화는 "최적 전략 제안"(§2.2 위반)
  이라 영구 범위 외. 사실 계산만 한다.
- t→t+1 보유수익률 = `PriceAdjuster.adjust(as_of=t+1)` read-time 수정주가 변화.
  폐지 종목은 폐지일까지 보유 후 현금 (그 시점 마지막 수정종가 고정).
- 거래비용(D2): 매도 증권거래세 + 위탁수수료 default 를 회전 시 차감, 0 으로
  숨길 수 없음. 적용 비율을 결과에 명시.
- survivorship 측정(D3): 각 t universe 종목 중 가격 시계열이 부재한 비율
  (`missing_price_ratio`) 누적. 누락이 있으면 `survivorship_complete = False`.

**stateless·결정적** — 동일 입력 byte 동일 출력. 외부 부수효과 0 (재현성 §2.10).
Decimal 산술 + `ROUND_HALF_EVEN` (price_adjuster / factor_evaluator 동일 의미론).

가드레일 (ADR-0027):
- D6: 평가 라벨/등급 0 — CAGR/누적수익률/MDD/변동성/turnover 등 *사실 통계만*.
  "우수"/"양호" 등 판단 어휘를 본 모듈은 절대 생성하지 않는다.
- D2: 거래비용 강제 노출 — cost_assumptions 를 결과 dataclass 에 항상 포함.
- D3: survivorship 한계를 숨기지 않고 측정·노출 (fail-loud 디스클로저는 route/UI).

거래비용 default 근거 (2026 기준, KOSPI/KOSDAQ 보통주):
- 증권거래세(매도): 2025 기준 코스피 0.15%(거래세 0.00% + 농어촌특별세 0.15%),
  코스닥 0.15%. 자본시장 정책상 2025 년 이후 0.15% 로 인하 (정부 단계적 인하
  로드맵). 보수적으로 **15 bps(0.15%)** 를 매도 거래세 default 로 사용.
  *시장/연도별 정확한 세율은 외부 정책 데이터 조달 범위 — 본 엔진은 합리적
  단일 default 를 강제 노출하고 사용자 override 를 허용한다 (ADR-0027 D2).*
- 위탁수수료(매수·매도 양변): 온라인 증권사 통상 0.015% 내외. **1.5 bps(0.015%)**
  를 편도 수수료 default 로 사용.
회전(turnover) 발생 시: 매도분에 (거래세 + 수수료), 매수분에 (수수료) 를 차감.

관련 ADR / 문서:
- ADR-0027 D1~D3/D6 (본 엔진), ADR-0025 D5 (재현 freeze), ADR-0009 D7
  (survivorship 보존), CONCEPT §2.2/§2.4/§2.10.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Final, Literal

from app.repositories.pit_protocols import (
    CorporateActionRepository,
    FinancialRepository,
    MacroIndicatorRepository,
    MarketCapRepository,
    PriceRepository,
    TreasurySharesRepository,
)
from app.repositories.stocks_master_repository import StocksMasterRepository
from app.services.db_field_provider import DbFieldProvider
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import LoadedPack
from app.services.price_adjuster import PriceAdjuster

__all__ = [
    "BACKTEST_ENGINE_VERSION",
    "DEFAULT_COMMISSION_BPS",
    "DEFAULT_TAX_BPS",
    "BacktestCondition",
    "BacktestResult",
    "CostAssumptions",
    "EquityPoint",
    "RebalanceFrequency",
    "run_backtest",
]

# 본 엔진의 알고리즘 버전 — freeze 입력 (rebalance/수익률/비용/survivorship 산식
# 의미가 바뀌면 bump). result_hash 가 산식 변경을 감지하도록 freeze 에 포함.
BACKTEST_ENGINE_VERSION: Final[str] = "1.0"

# 거래비용 default (bps = 1/10000). 근거는 모듈 docstring.
DEFAULT_TAX_BPS: Final[Decimal] = Decimal("15")  # 매도 증권거래세 0.15%
DEFAULT_COMMISSION_BPS: Final[Decimal] = Decimal("1.5")  # 편도 위탁수수료 0.015%

# bps → 비율 변환 분모.
_BPS_DENOMINATOR: Final[Decimal] = Decimal("10000")

# Decimal 산술 정밀도 / 라운딩 — price_adjuster / factor_evaluator 와 동일.
_DECIMAL_PRECISION: Final[int] = 28

# 연율화 기준 — 1 년 거래일 수 (변동성 연율화 표준 가정). 252 는 관행값.
_TRADING_DAYS_PER_YEAR: Final[Decimal] = Decimal("252")
# 1 년 일수 (CAGR 연환산 — 달력일 기준).
_DAYS_PER_YEAR: Final[Decimal] = Decimal("365.25")

# rebalance 주기 — quarterly(분기말) | monthly(월말).
RebalanceFrequency = Literal["quarterly", "monthly"]


# =============================================================================
# 입력 / 결과 dataclass
# =============================================================================

@dataclass(frozen=True, slots=True)
class BacktestCondition:
    """백테스트 universe 필터 조건 — screen.py 의 ConditionIn 도메인 등가.

    factor canonical_id 를 op/threshold 로 비교. AND 결합 (모든 조건 통과 =
    포트폴리오 편입). N/A 종목은 해당 조건 불충족 (값 없는 종목이 조건을
    만족한다고 주장하지 않음 — §2.1 Fidelity).
    """

    factor: str
    op: str  # "<" | "<=" | "=" | ">=" | ">" | "!="
    threshold: Decimal


@dataclass(frozen=True, slots=True)
class CostAssumptions:
    """거래비용 가정 — ADR-0027 D2 강제 노출. 결과·freeze 에 항상 포함.

    Attributes:
        tax_bps: 매도 증권거래세 (bps). 회전 시 매도분에 차감.
        commission_bps: 편도 위탁수수료 (bps). 회전 시 매수·매도 양변에 차감.
    """

    tax_bps: Decimal
    commission_bps: Decimal


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """equity curve 단일 점 — 날짜별 누적가치 (초기 1.0 기준)."""

    date: date
    value: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """백테스트 산출 — 사실 통계만 (ADR-0027 D6, 평가 라벨 0).

    Attributes:
        equity_curve: rebalance 시점별 누적가치 (초기 1.0). 결정적 순서 (시간순).
        cagr: 연복리 수익률 (Compound Annual Growth Rate). 기간이 1 년 미만이거나
            누적가치 <= 0 이면 None (외삽 금지 — 사실만).
        cumulative_return: 누적수익률 = 최종가치 - 1.
        mdd: 최대낙폭 (Maximum Drawdown) — equity curve 의 고점 대비 최대 하락폭
            (음수 또는 0). 단일 점이면 0.
        volatility: 연율화 변동성 — rebalance 기간 수익률의 표준편차 ×
            sqrt(연 rebalance 횟수). 표본 2 개 미만이면 None.
        turnover: 평균 회전율 — 각 rebalance 의 (편입 변경 종목 / 직전 보유)
            비율 평균. 0~1.
        rebalance_count: 실제 수행된 rebalance 횟수 (스냅된 시점 수).
        cost_assumptions: 적용된 거래비용 가정 (D2 강제 노출).
        missing_price_ratio: survivorship — 각 t universe 종목 중 가격 시계열
            부재 비율의 누적 평균 (0~1). D3 측정.
        survivorship_complete: missing_price_ratio == 0 이면 True. 누락 있으면
            False → route/UI 가 survivorship bias 경고 강제 (D3).
    """

    equity_curve: tuple[EquityPoint, ...]
    cagr: Decimal | None
    cumulative_return: Decimal
    mdd: Decimal
    volatility: Decimal | None
    turnover: Decimal
    rebalance_count: int
    cost_assumptions: CostAssumptions
    missing_price_ratio: Decimal
    survivorship_complete: bool


# =============================================================================
# rebalance 그리드 — 분기말 / 월말 calendar date 생성
# =============================================================================

def _month_end(year: int, month: int) -> date:
    """해당 (year, month) 의 말일 date."""
    if month == 12:
        return date(year, 12, 31)
    # 다음 달 1 일의 하루 전 = 이번 달 말일.
    from datetime import timedelta
    first_of_next = date(year, month + 1, 1)
    return first_of_next - timedelta(days=1)


def _rebalance_calendar_dates(
    start: date, end: date, frequency: RebalanceFrequency,
) -> list[date]:
    """[start, end] 범위의 rebalance 기준 calendar date list (오름차순).

    - quarterly: 3/6/9/12 월의 말일.
    - monthly: 매월 말일.

    start 이상 end 이하인 말일만. 본 함수는 *calendar* 기준 — 실제 거래일 snap 은
    가격 시계열로 별도 수행 (`_snap_to_trading_day`). 비영업일 말일이어도 그
    이전 마지막 영업일 종가를 사용하므로 결과는 정의된다.
    """
    if start > end:
        return []
    target_months = (
        {3, 6, 9, 12} if frequency == "quarterly" else set(range(1, 13))
    )
    dates: list[date] = []
    year = start.year
    month = start.month
    while True:
        if month in target_months:
            me = _month_end(year, month)
            if start <= me <= end:
                dates.append(me)
            elif me > end:
                break
        # 다음 달.
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
        if date(year, month, 1) > end:
            break
    return dates


# =============================================================================
# 가격 helper — as_of 시점 수정종가 조회
# =============================================================================

def _adjusted_close_at(
    code: str,
    *,
    as_of: date,
    start: date,
    price_repo: PriceRepository,
    corporate_action_repo: CorporateActionRepository | None,
    adjuster: PriceAdjuster,
) -> Decimal | None:
    """`as_of` 시점 (이하 마지막 영업일) 의 수정종가. 데이터 없으면 None.

    `price_adjuster.adjust(as_of=as_of)` 로 그 시점까지 발효된 corporate action
    을 read-time 보정 (ADR-0001). 보정된 시계열의 마지막 (as_of 이하 최신) 일자
    종가를 반환. 시계열이 비면 None — survivorship 측정(D3)의 누락 신호.

    `start` 는 fetch 하한 — 보정 누적의 정확성을 위해 충분히 이른 시점부터 fetch
    해야 하나, 본 helper 는 호출자가 준 start 를 그대로 사용한다 (호출자가
    백테스트 전체 기간 시작을 넘긴다).
    """
    raw = price_repo.fetch_prices(code, as_of=as_of, start=start)
    if not raw:
        return None
    cas: Sequence = ()
    if corporate_action_repo is not None:
        cas = corporate_action_repo.fetch_actions(code, as_of=as_of)
    series = adjuster.adjust(raw, cas, as_of=as_of)
    if not series.adjusted:
        return None
    # adjusted 는 effective_date 오름차순 — 마지막이 as_of 이하 최신.
    return series.adjusted[-1].close_adjusted


# =============================================================================
# universe 선정 — pack conditions AND 필터 (screen.py 의미론)
# =============================================================================

_OP_COMPARATORS: Final[Mapping[str, object]] = {
    "<": lambda lhs, rhs: lhs < rhs,
    "<=": lambda lhs, rhs: lhs <= rhs,
    "=": lambda lhs, rhs: lhs == rhs,
    ">=": lambda lhs, rhs: lhs >= rhs,
    ">": lambda lhs, rhs: lhs > rhs,
    "!=": lambda lhs, rhs: lhs != rhs,
}


def _select_portfolio(
    *,
    as_of: date,
    pack: LoadedPack,
    conditions: Sequence[BacktestCondition],
    stocks_repo: StocksMasterRepository,
    evaluator: FactorEvaluator,
    price_repo: PriceRepository,
    financial_repo: FinancialRepository,
    corporate_action_repo: CorporateActionRepository | None,
    market_cap_repo: MarketCapRepository | None,
    treasury_repo: TreasurySharesRepository | None,
    macro_repo: MacroIndicatorRepository | None,
) -> tuple[tuple[str, ...], int, int]:
    """`as_of` 시점 universe 에서 conditions AND 통과 종목 = 포트폴리오.

    반환: (선정 종목코드 tuple [정렬], universe 크기, 가격 누락 종목 수).
    universe = list_active(as_of) 중 security_type=="common" (ADR-0027 D1 —
    보통주만, ETF/리츠/우선주 제외). 각 종목 DbFieldProvider + evaluator 로 조건
    평가. N/A 는 불충족 → 제외 (§2.1).

    가격 누락 종목 수 (D3): universe 종목 중 fetch_prices 가 빈 시계열인 종목 수.
    survivorship 측정 — 소급 폐지 종목 OHLCV 미백필 추정 (ADR-0027 D3).
    """
    factors_by_id = {f["canonical_id"]: f for f in pack.body["factors"]}

    selected: list[str] = []
    universe_size = 0
    missing_price_count = 0

    for record in stocks_repo.list_active(as_of=as_of):
        code = record.current_code
        if not code:
            continue
        # ADR-0027 D1 — universe 정의는 security_type=="common" (보통주만).
        if record.security_type != "common":
            continue
        universe_size += 1

        # survivorship 측정(D3) — universe 종목의 가격 시계열 존재 여부. 빈
        # 시계열이면 소급 폐지 종목 backfill 누락 추정 → missing 카운트.
        raw = price_repo.fetch_prices(code, as_of=as_of, start=date.min)
        if not raw:
            missing_price_count += 1
            # 가격 없으면 보유수익률 계산 불가 → 포트폴리오 편입 불가. 조건 평가도
            # 가격 기반 factor 면 어차피 N/A. 편입 후보에서 제외 (사실만).
            continue

        provider = DbFieldProvider(
            code=code,
            as_of=as_of,
            price_repo=price_repo,
            financial_repo=financial_repo,
            corporate_action_repo=corporate_action_repo,
            market_cap_repo=market_cap_repo,
            treasury_repo=treasury_repo,
            macro_repo=macro_repo,
            factor_pack=pack,
            evaluator=evaluator,
        )
        if _passes_all(provider, conditions, factors_by_id, evaluator, as_of=as_of):
            selected.append(code)

    return tuple(sorted(set(selected))), universe_size, missing_price_count


def _passes_all(
    provider: DbFieldProvider,
    conditions: Sequence[BacktestCondition],
    factors_by_id: Mapping[str, dict],
    evaluator: FactorEvaluator,
    *,
    as_of: date,
) -> bool:
    """한 종목이 모든 condition 을 통과하는지 (AND, screen.py `_passes_all_conditions` 동형).

    조건 factor 가 pack 에 없으면 그 조건은 불충족 (잘못된 factor 참조 = 통과
    불가, 사실만). N/A 종목도 불충족 (§2.1).
    """
    for cond in conditions:
        factor = factors_by_id.get(cond.factor)
        if factor is None:
            return False
        result = evaluator.evaluate(factor, provider, as_of=as_of)
        if result.is_na or result.value is None:
            return False
        comparator = _OP_COMPARATORS[cond.op]
        if not comparator(result.value, cond.threshold):  # type: ignore[operator]
            return False
    return True


# =============================================================================
# 통계 산출 — 사실만 (ADR-0027 D6)
# =============================================================================

def _compute_mdd(values: Sequence[Decimal]) -> Decimal:
    """equity curve 의 최대낙폭 (MDD) — 고점 대비 최대 하락 비율 (<= 0).

    각 시점 누적가치를 직전까지의 고점과 비교 — (현재 - 고점)/고점 의 최솟값
    (가장 깊은 낙폭). 단일 점이면 0. 사실 통계 (D6).
    """
    if len(values) < 2:
        return Decimal(0)
    peak = values[0]
    mdd = Decimal(0)
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            drawdown = (v - peak) / peak
            if drawdown < mdd:
                mdd = drawdown
    return mdd


def _compute_volatility(period_returns: Sequence[Decimal], periods_per_year: Decimal) -> Decimal | None:
    """rebalance 기간 수익률의 연율화 변동성 (표본 표준편차 × sqrt(연 횟수)).

    표본 2 개 미만이면 None (표준편차 미정의 — 외삽 금지). 사실 통계 (D6).
    """
    n = len(period_returns)
    if n < 2:
        return None
    mean = sum(period_returns, Decimal(0)) / Decimal(n)
    # 표본 분산 (n-1 분모 — unbiased).
    variance = sum(((r - mean) ** 2 for r in period_returns), Decimal(0)) / Decimal(n - 1)
    std = variance.sqrt()
    return std * periods_per_year.sqrt()


def _periods_per_year(frequency: RebalanceFrequency) -> Decimal:
    """연 rebalance 횟수 — 변동성 연율화 계수."""
    return Decimal(4) if frequency == "quarterly" else Decimal(12)


def _compute_cagr(final_value: Decimal, span_days: int) -> Decimal | None:
    """연복리 수익률 — final_value^(365.25/span_days) - 1.

    span_days <= 0 또는 final_value <= 0 이면 None (계산 불가 — 사실만, 외삽 금지).
    """
    if span_days <= 0 or final_value <= 0:
        return None
    years = Decimal(span_days) / _DAYS_PER_YEAR
    if years <= 0:
        return None
    # final_value^(1/years) - 1 = exp(ln(final_value)/years) - 1.
    exponent = (Decimal(1) / years) * final_value.ln()
    return exponent.exp() - Decimal(1)


# =============================================================================
# 엔진 진입점
# =============================================================================

def run_backtest(
    *,
    pack: LoadedPack,
    conditions: Sequence[BacktestCondition],
    start: date,
    end: date,
    frequency: RebalanceFrequency,
    stocks_repo: StocksMasterRepository,
    price_repo: PriceRepository,
    financial_repo: FinancialRepository,
    evaluator: FactorEvaluator | None = None,
    corporate_action_repo: CorporateActionRepository | None = None,
    market_cap_repo: MarketCapRepository | None = None,
    treasury_repo: TreasurySharesRepository | None = None,
    macro_repo: MacroIndicatorRepository | None = None,
    cost_assumptions: CostAssumptions | None = None,
    price_adjuster: PriceAdjuster | None = None,
) -> BacktestResult:
    """백테스트 실행 — PIT rebalance 순회 + 사실 통계 (ADR-0027 D1~D3/D6).

    각 rebalance 시점 t 마다:
    1. universe = list_active(as_of=t) ∩ security_type=="common".
    2. 포트폴리오 = universe 중 conditions AND 통과 종목 (동일가중).
    3. t→t+1 보유수익률 = 종목별 수정종가 변화의 동일가중 평균.
    4. 회전 시 거래비용(D2) 차감.
    survivorship(D3): 각 t universe 의 가격 누락 비율 누적.

    **stateless·결정적** — 동일 입력 byte 동일 출력. 외부 부수효과 0.

    Args:
        pack: 조건 평가용 LoadedPack (registry.resolve 결과).
        conditions: universe 필터 조건 (AND).
        start / end: 백테스트 기간 [start, end].
        frequency: "quarterly"(분기말) | "monthly"(월말).
        stocks_repo / price_repo / financial_repo: PIT repository.
        evaluator: None 이면 default FactorEvaluator.
        corporate_action_repo: read-time 보정용. None 이면 보정 미적용(identity).
        market_cap_repo / treasury_repo / macro_repo: factor 입력 repository.
        cost_assumptions: None 이면 default (DEFAULT_TAX_BPS / DEFAULT_COMMISSION_BPS).
            ADR-0027 D2 — 0 으로 숨길 수 없음 (0 입력은 허용하되 결과에 명시 노출).
        price_adjuster: None 이면 default PriceAdjuster.

    Returns:
        BacktestResult — equity curve + 사실 통계 + 거래비용 가정 + survivorship.
    """
    evaluator = evaluator or FactorEvaluator()
    adjuster = price_adjuster or PriceAdjuster()
    costs = cost_assumptions or CostAssumptions(
        tax_bps=DEFAULT_TAX_BPS, commission_bps=DEFAULT_COMMISSION_BPS,
    )

    with localcontext() as ctx:
        ctx.prec = _DECIMAL_PRECISION
        ctx.rounding = ROUND_HALF_EVEN

        return _run_backtest_inner(
            pack=pack,
            conditions=conditions,
            start=start,
            end=end,
            frequency=frequency,
            stocks_repo=stocks_repo,
            price_repo=price_repo,
            financial_repo=financial_repo,
            evaluator=evaluator,
            corporate_action_repo=corporate_action_repo,
            market_cap_repo=market_cap_repo,
            treasury_repo=treasury_repo,
            macro_repo=macro_repo,
            costs=costs,
            adjuster=adjuster,
        )


def _run_backtest_inner(
    *,
    pack: LoadedPack,
    conditions: Sequence[BacktestCondition],
    start: date,
    end: date,
    frequency: RebalanceFrequency,
    stocks_repo: StocksMasterRepository,
    price_repo: PriceRepository,
    financial_repo: FinancialRepository,
    evaluator: FactorEvaluator,
    corporate_action_repo: CorporateActionRepository | None,
    market_cap_repo: MarketCapRepository | None,
    treasury_repo: TreasurySharesRepository | None,
    macro_repo: MacroIndicatorRepository | None,
    costs: CostAssumptions,
    adjuster: PriceAdjuster,
) -> BacktestResult:
    """run_backtest 의 localcontext 내부 본체 — Decimal 결정성 보장 하에 순회.

    rebalance 그리드 t_0..t_n 를 순회하며, 각 [t_i, t_{i+1}] 구간의 동일가중
    보유수익률을 누적가치에 곱한다. 폐지 종목은 t_{i+1} 시점에 가격 시계열이
    t_{i+1} 이전에서 끝나므로 그 마지막 종가가 사용됨(폐지일까지 보유 후 현금
    동결 — `_adjusted_close_at` 이 as_of 이하 마지막 종가 반환).
    """
    tax_rate = costs.tax_bps / _BPS_DENOMINATOR
    commission_rate = costs.commission_bps / _BPS_DENOMINATOR

    rebalance_dates = _rebalance_calendar_dates(start, end, frequency)

    # 누적가치 1.0 에서 시작. equity_curve 의 첫 점은 첫 rebalance 시점.
    equity_value = Decimal(1)
    equity_curve: list[EquityPoint] = []
    period_returns: list[Decimal] = []

    # survivorship 누적 — 각 rebalance 의 (가격 누락 / universe) 비율의 평균.
    missing_ratios: list[Decimal] = []
    # turnover 누적 — 각 rebalance 의 직전 보유 대비 변경 비율 평균.
    turnover_ratios: list[Decimal] = []

    prev_holdings: tuple[str, ...] | None = None

    for i, t in enumerate(rebalance_dates):
        # 1. t 시점 포트폴리오 선정 + survivorship 측정.
        holdings, universe_size, missing_count = _select_portfolio(
            as_of=t,
            pack=pack,
            conditions=conditions,
            stocks_repo=stocks_repo,
            evaluator=evaluator,
            price_repo=price_repo,
            financial_repo=financial_repo,
            corporate_action_repo=corporate_action_repo,
            market_cap_repo=market_cap_repo,
            treasury_repo=treasury_repo,
            macro_repo=macro_repo,
        )
        if universe_size > 0:
            missing_ratios.append(Decimal(missing_count) / Decimal(universe_size))

        # 2. 회전 측정 — 직전 보유 대비 매도분(편출)·매수분(편입) 비율 분리.
        #    동일가중 포트폴리오에서 매도분은 직전 포트폴리오 비중 기준, 매수분은
        #    신규 포트폴리오 비중 기준. 첫 rebalance 는 전량 매수(매도 0, 매수 1).
        sell_fraction, buy_fraction = _turnover_fractions(prev_holdings, holdings)
        # turnover 통계는 매도·매수 평균 비율로 보고 (회전 강도 사실값).
        turnover_ratios.append((sell_fraction + buy_fraction) / Decimal(2))

        # 3. 거래비용 차감 — 매도분에 (세금 + 수수료), 매수분에 (수수료). 동일가중
        #    포트폴리오의 매도/매수 비율에 비례. equity 에 (1 - cost) 곱. ADR-0027
        #    D2 — 회전 시 강제 차감, 0 으로 숨길 수 없음. 첫 전량 매수는 매도세 0.
        cost_drag = (
            sell_fraction * (tax_rate + commission_rate)
            + buy_fraction * commission_rate
        )
        equity_value *= (Decimal(1) - cost_drag)

        # equity_curve 점 기록 (비용 차감 후 시점 가치).
        equity_curve.append(EquityPoint(date=t, value=equity_value))

        # 4. 다음 rebalance 까지 보유수익률 — 마지막 시점은 구간 없음.
        if i + 1 < len(rebalance_dates):
            t_next = rebalance_dates[i + 1]
            period_return = _portfolio_return(
                holdings=holdings,
                t=t,
                t_next=t_next,
                fetch_start=start,
                price_repo=price_repo,
                corporate_action_repo=corporate_action_repo,
                adjuster=adjuster,
            )
            period_returns.append(period_return)
            equity_value *= (Decimal(1) + period_return)

        prev_holdings = holdings

    return _assemble_result(
        equity_curve=equity_curve,
        period_returns=period_returns,
        turnover_ratios=turnover_ratios,
        missing_ratios=missing_ratios,
        rebalance_dates=rebalance_dates,
        frequency=frequency,
        costs=costs,
    )


def _turnover_fractions(
    prev: tuple[str, ...] | None, current: tuple[str, ...],
) -> tuple[Decimal, Decimal]:
    """직전→신규 포트폴리오의 (매도분 비율, 매수분 비율).

    동일가중 가정 — 각 종목 비중 = 1/N. rebalance 시:
    - 매도분 = 직전 보유 중 신규에서 빠진 종목의 직전 비중 합 = |prev - current| / |prev|.
    - 매수분 = 신규 보유 중 직전에 없던 종목의 신규 비중 합 = |current - prev| / |current|.

    첫 rebalance(prev None/빈)는 전량 신규 매수 → (매도 0, 매수 1). 양쪽 모두 빈
    포트폴리오면 (0, 0). 거래비용(D2)은 매도분에 (세금+수수료), 매수분에
    (수수료)를 적용 — 첫 전량 매수에 매도세가 붙지 않도록 buy/sell 을 분리한다.

    Note (단순화 — ADR-0027 D2 디스클레이머):
        교집합 종목의 비중 변동(N 변화로 인한 미세 rebalance)은 본 모듈이 비용
        부과하지 않는다(체결·슬리피지 단순화 가정, D4 디스클레이머가 명시). 편입/
        편출 종목만 회전 비용 대상 — 보수적이되 환상(0 비용)은 아님.
    """
    prev_set = set(prev) if prev else set()
    cur_set = set(current)

    sell_fraction = (
        Decimal(len(prev_set - cur_set)) / Decimal(len(prev_set))
        if prev_set else Decimal(0)
    )
    buy_fraction = (
        Decimal(len(cur_set - prev_set)) / Decimal(len(cur_set))
        if cur_set else Decimal(0)
    )
    return sell_fraction, buy_fraction


def _portfolio_return(
    *,
    holdings: tuple[str, ...],
    t: date,
    t_next: date,
    fetch_start: date,
    price_repo: PriceRepository,
    corporate_action_repo: CorporateActionRepository | None,
    adjuster: PriceAdjuster,
) -> Decimal:
    """[t, t_next] 구간의 동일가중 보유수익률.

    각 종목 i 의 수익률 = (수정종가@t_next / 수정종가@t) - 1. 두 시점 모두
    `price_adjuster.adjust(as_of=t_next)` 의 보정 시계열에서 조회 — 같은 보정
    기준(t_next)으로 두 가격을 잡아야 corporate action 보정이 일관 (분할 등이
    구간 내 발생해도 비율 왜곡 0).

    폐지 종목: t_next 이전에 시계열이 끝나면 그 마지막 종가가 close@t_next 로
    사용됨 (폐지일까지 보유 후 현금 동결 — `_adjusted_close_at` 이 as_of 이하
    최신 반환). t 시점 가격이 없으면 그 종목은 수익률 계산 제외 (편입 불가
    종목이 holdings 에 없으므로 정상 경로엔 미발생).

    빈 포트폴리오(holdings 0)는 현금 보유 → 수익률 0 (사실만, 외삽 금지).
    """
    if not holdings:
        return Decimal(0)

    returns: list[Decimal] = []
    for code in holdings:
        # 두 시점 모두 t_next 보정 기준으로 조회 (보정 일관성).
        close_t_next = _adjusted_close_at(
            code, as_of=t_next, start=fetch_start,
            price_repo=price_repo,
            corporate_action_repo=corporate_action_repo, adjuster=adjuster,
        )
        close_t = _adjusted_close_at(
            code, as_of=t, start=fetch_start,
            price_repo=price_repo,
            corporate_action_repo=corporate_action_repo, adjuster=adjuster,
        )
        if close_t is None or close_t <= 0 or close_t_next is None:
            # 시작 가격 없음 — 편입 불가 종목 (정상 경로 미발생) 또는 분모 0.
            # 사실만: 수익률 계산 불가 종목은 평균에서 제외 (0 으로 가정 안 함).
            continue
        returns.append((close_t_next / close_t) - Decimal(1))

    if not returns:
        # 모든 보유 종목의 가격 계산 불가 — 현금 동결 (수익률 0).
        return Decimal(0)
    # 동일가중 평균.
    return sum(returns, Decimal(0)) / Decimal(len(returns))


def _assemble_result(
    *,
    equity_curve: list[EquityPoint],
    period_returns: list[Decimal],
    turnover_ratios: list[Decimal],
    missing_ratios: list[Decimal],
    rebalance_dates: list[date],
    frequency: RebalanceFrequency,
    costs: CostAssumptions,
) -> BacktestResult:
    """순회 결과를 BacktestResult 로 조립 — 사실 통계 산출 (D6).

    rebalance 0 회(빈 그리드)면 빈 결과 (누적가치 1.0 단일 가정, 통계 None).
    survivorship_complete = 누적 missing_ratio 가 0 (누락 0).
    """
    values = tuple(p.value for p in equity_curve)
    final_value = values[-1] if values else Decimal(1)

    cumulative_return = final_value - Decimal(1)
    mdd = _compute_mdd(values)
    volatility = _compute_volatility(period_returns, _periods_per_year(frequency))

    # CAGR — 첫 rebalance ~ 마지막 rebalance 의 달력일 span 기준.
    cagr: Decimal | None = None
    if len(rebalance_dates) >= 2:
        span_days = (rebalance_dates[-1] - rebalance_dates[0]).days
        cagr = _compute_cagr(final_value, span_days)

    turnover = (
        sum(turnover_ratios, Decimal(0)) / Decimal(len(turnover_ratios))
        if turnover_ratios else Decimal(0)
    )

    # survivorship — 각 rebalance 의 가격 누락 비율 평균. 누락 없으면 0 → complete.
    missing_price_ratio = (
        sum(missing_ratios, Decimal(0)) / Decimal(len(missing_ratios))
        if missing_ratios else Decimal(0)
    )
    survivorship_complete = missing_price_ratio == Decimal(0)

    return BacktestResult(
        equity_curve=tuple(equity_curve),
        cagr=cagr,
        cumulative_return=cumulative_return,
        mdd=mdd,
        volatility=volatility,
        turnover=turnover,
        rebalance_count=len(rebalance_dates),
        cost_assumptions=costs,
        missing_price_ratio=missing_price_ratio,
        survivorship_complete=survivorship_complete,
    )
