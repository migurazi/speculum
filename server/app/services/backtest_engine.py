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

from app.repositories.batch_run_repository import BatchCutoff
from app.repositories.caching_repositories import (
    CachingCorporateActionRepository,
    CachingDividendRepository,
    CachingFinancialRepository,
    CachingMacroIndicatorRepository,
    CachingMarketCapRepository,
    CachingPriceRepository,
    CachingTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    CorporateActionRepository,
    DividendRepository,
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
from app.services.total_return_adjuster import TotalReturnAdjuster

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
# 1.0→1.1: 보유수익률(_portfolio_return)의 close@t/close@t_next 를 단일 as_of=t_next
#   보정 기준으로 통일 — 보유구간 내 price-adjust corporate action(분할/병합/유상
#   증자) 발효 시 허위 ±점프 수익률 주입 버그 수정(_adjusted_closes_at). 분할 보유
#   구간이 있는 backtest 의 equity_curve 값이 1.0 과 달라지므로 산식 세대 분리
#   (옛 1.0 run 의 result_hash 와 미충돌 — equity_curve 는 hash 입력 아니고
#   engine_version 이 세대 freeze 축, oracle 분석). 분할 없는 backtest 는 결과 불변.
BACKTEST_ENGINE_VERSION: Final[str] = "1.1"

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

def _adjusted_closes_at(
    code: str,
    *,
    as_of: date,
    start: date,
    pick_dates: Sequence[date],
    price_repo: PriceRepository,
    corporate_action_repo: CorporateActionRepository | None,
    adjuster: PriceAdjuster,
    krx_batch_cutoff: BatchCutoff | None = None,
) -> dict[date, Decimal] | None:
    """`as_of` **단일 보정 기준**의 시계열에서 여러 시점의 수정종가를 한 번에 추출.

    핵심 — 보정 기준 통일 (corporate action 정합성, §2.1/§2.10):
        `_portfolio_return` 은 구간 [t, t_next] 수익률을 close@t_next / close@t 로
        잡는데, 두 종가를 **서로 다른 as_of 로 보정**하면(과거 코드의 결함) 보유
        구간 (t, t_next] 안에 분할/병합/유상증자 등 price-adjust action 이 발효될
        때 분모·분자의 보정 기준이 어긋나 **허위 ±점프 수익률**이 equity_curve 에
        주입된다(예: 2:1 분할 시 close@t 는 분할 전 명목가, close@t_next 는 분할 후
        반토막 → 허위 -50%). `PriceAdjuster.adjust` 는 `effective_date <= as_of`
        action 만 누적하므로(price_adjuster.py:627), **단일 as_of=t_next 기준으로
        한 번만 보정**하면 close@t 일자에는 분할 factor 가 back-adjust 로 적용되고
        close@t_next 에는 미적용 → 비율 왜곡 0 (read-time 보정 ADR-0001 의 본래 의미).

        따라서 본 helper 는 as_of 1개로 보정한 단일 시계열에서 각 pick_date 별
        "그 일자 이하 최신 보정종가"를 뽑는다. 종목당 fetch_prices·fetch_actions·
        adjust 가 **각 1회**로 합쳐져(과거 2회) corporate-action N+1·중복 보정도
        부수적으로 제거된다(oracle 분석 — 정합성 수정이 perf 1차분 포함).

    데이터 없음:
        raw 가 비거나 보정 시계열이 비면 None (survivorship D3 누락 신호). 특정
        pick_date 가 시계열 최소일자보다 이르면 그 키만 결과 dict 에서 누락(편입
        종목이라 정상 경로 미발생 — codes_with_price 통과 전제). 폐지 종목은
        시계열이 t_next 이전에 끝나 모든 pick_date 가 폐지일 종가로 수렴(폐지일까지
        보유 후 현금 동결 의미 보존).

    `start`/`krx_batch_cutoff` (ADR-0033 D4): start 는 보정 누적 정확성을 위한 fetch
        하한(호출자가 백테스트 전체 시작을 넘김). krx_batch_cutoff 는 reproduce 시
        가격 fetch 를 frozen 이하 KRX batch 로 한정(늦은 backfill 제외). default
        None 무필터(회귀 0). **corporate action 보정(`fetch_actions`)은 cutoff
        파라미터가 없어 freeze 불가**(ADR-0033 D4 known limit — screen 상속).
    """
    raw = price_repo.fetch_prices(
        code, as_of=as_of, start=start, batch_cutoff=krx_batch_cutoff,
    )
    if not raw:
        return None
    cas: Sequence = ()
    if corporate_action_repo is not None:
        # ADR-0033 D4 — corporate action 은 cutoff freeze 불가(fetch_actions 에
        # batch_cutoff 부재). reproduce 시 재실행 시점 정정 corporate action 이
        # 보정에 반영될 수 있음(screen 상속 한계).
        cas = corporate_action_repo.fetch_actions(code, as_of=as_of)
    series = adjuster.adjust(raw, cas, as_of=as_of)
    if not series.adjusted:
        return None
    # adjusted 는 effective_date 오름차순 — 각 pick_date 에 대해 그 일자 이하 최신
    # 보정종가를 선택(date 오름차순이라 date > pick_date 에서 early break).
    result: dict[date, Decimal] = {}
    for pick in pick_dates:
        latest: Decimal | None = None
        for rec in series.adjusted:
            if rec.date <= pick:
                latest = rec.close_adjusted
            else:
                break
        if latest is not None:
            result[pick] = latest
    return result


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
    dividend_repo: DividendRepository | None = None,
    krx_batch_cutoff: BatchCutoff | None = None,
    dart_batch_cutoff: BatchCutoff | None = None,
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

    # universe 를 1회 materialize — 보통주(ADR-0027 D1)만. universe_size 는 가격
    # 누락 여부와 무관하게 모집단 크기(기존 동작 동일).
    universe_records = [
        r
        for r in stocks_repo.list_active(as_of=as_of)
        if r.current_code and r.security_type == "common"
    ]
    universe_codes = [r.current_code for r in universe_records]
    universe_size = len(universe_records)

    # per-code N+1 완화 (oracle 설계검토 A2 — caching_repositories) — price/
    # market_cap 은 종목마다 값이 달라 memoize 가 아닌 **루프 전 1회 bulk prime**
    # 으로 종목별 개별 쿼리 N 회를 청크 쿼리 몇 회로 축약(round-trip 제거). 캐시
    # 수명=이 rebalance 시점(단일 as_of), 시점별 새 인스턴스라 메모리 bounded.
    # 범위 한계(설계상 의도) — 본 prefetch 는 **universe 조건평가 경로만** 커버한다.
    # 보유종목 수익률 계산(_portfolio_return → _adjusted_closes_at)은 backtest 시작일
    # (full-range)부터의 가격을 종목별로 직접 fetch 하므로 bounded-window 캐시로
    # 답할 수 없어 inner 위임(per-code 잔존). holdings(<<universe)뿐이라 허용
    # 가능한 N+1 이며, full-range 보유종목 bulk prime 은 별도 cycle(oracle M1).
    # 이중 래핑 방어 — 이미 캐싱 래퍼면 재prime 만(idempotent). prime window 밖
    # /다른 (as_of,cutoff) 요청은 inner 위임이라 결과 byte-불변(equity_curve 동일).
    cached_price = (
        price_repo
        if isinstance(price_repo, CachingPriceRepository)
        else CachingPriceRepository(price_repo)
    )
    cached_price.prime(universe_codes, as_of=as_of, batch_cutoff=krx_batch_cutoff)
    cached_market_cap = market_cap_repo
    if market_cap_repo is not None:
        cached_market_cap = (
            market_cap_repo
            if isinstance(market_cap_repo, CachingMarketCapRepository)
            else CachingMarketCapRepository(market_cap_repo)
        )
        cached_market_cap.prime(
            universe_codes, as_of=as_of, batch_cutoff=krx_batch_cutoff,
        )
    # financial/treasury 도 동일 루프 전 1회 bulk prime (price 와 동형, oracle
    # 설계검토). supersede chain 해소가 serve-time 이라 raw vintage 적재(account 가
    # serve-time 가변) + 전체 vintage(date 사전필터 없음, C1 — chain integrity
    # 등가성). cutoff 은 **dart**(DART 출처) — krx 와 혼동 시 캐시 miss 로 전부 inner
    # 위임. 이중 래핑 방어(idempotent). 다른 키/미prime 요청은 inner 위임 → 결과
    # byte-불변(equity_curve 동일). 보유종목 수익률 경로(_portfolio_return)는
    # financial 미사용이라 영향 없음(price 의 holding-return 잔존과 무관).
    cached_financial = (
        financial_repo
        if isinstance(financial_repo, CachingFinancialRepository)
        else CachingFinancialRepository(financial_repo)
    )
    cached_financial.prime(
        universe_codes, as_of=as_of, batch_cutoff=dart_batch_cutoff,
    )
    cached_treasury = treasury_repo
    if treasury_repo is not None:
        cached_treasury = (
            treasury_repo
            if isinstance(treasury_repo, CachingTreasurySharesRepository)
            else CachingTreasurySharesRepository(treasury_repo)
        )
        cached_treasury.prime(
            universe_codes, as_of=as_of, batch_cutoff=dart_batch_cutoff,
        )
    # corporate_action 도 universe 루프 전 bulk prime — adjusted-price factor 의
    # split/dividend/buyback chain 보정 fetch_actions per-code N+1 제거(screen 과
    # 동형). batch_cutoff 없음
    # (CA cutoff freeze 불가, ADR-0033 D4) — (as_of) 키. 이중 래핑 방어. _portfolio_
    # return 의 holding-return CA prime(별도 인스턴스)과 무관(여긴 universe 조건평가).
    cached_ca = corporate_action_repo
    if corporate_action_repo is not None:
        cached_ca = (
            corporate_action_repo
            if isinstance(corporate_action_repo, CachingCorporateActionRepository)
            else CachingCorporateActionRepository(corporate_action_repo)
        )
        cached_ca.prime(universe_codes, as_of=as_of)
    # dividend 도 universe 루프 전 bulk prime — total_return field(배당 재투자 §2.4)
    # 의 종목별 fetch_dividends per-code N+1 제거(screen·CA 와 동형). batch_cutoff
    # 없음(dividend cutoff freeze 불가, ADR-0033 D4) — (as_of) 키. **None 가드** —
    # dividend_repo None 이면 래핑 안 함(both-or-neither: dividend None → adjuster
    # None 보존). 이중 래핑 방어. raw 적재 후 serve-time 해소라 byte-동일(equity_curve
    # 불변). live backtest 와 reproduce_backtest 가 동일 배선이라 §2.10 재현 보존.
    cached_dividend = dividend_repo
    if dividend_repo is not None:
        cached_dividend = (
            dividend_repo
            if isinstance(dividend_repo, CachingDividendRepository)
            else CachingDividendRepository(dividend_repo)
        )
        cached_dividend.prime(universe_codes, as_of=as_of)

    # survivorship 측정(D3) — 가격 시계열이 존재하는 종목 집합을 1회 bulk full-range
    # 판정. 종목별 fetch_prices(start=date.min) 의 "빈 시계열 여부" 와 **동일 의미**
    # (존재 bool) 라 missing_price_count·equity_curve 불변(oracle A2). 빈 시계열
    # 종목 = 소급 폐지 backfill 누락 추정(ADR-0027 D3). ADR-0033 D4 — reproduce 시
    # cutoff 주입(frozen 이하 batch row 만). default None 무필터(기존 동작).
    codes_with_price = cached_price.fetch_codes_with_prices(
        universe_codes, as_of=as_of, batch_cutoff=krx_batch_cutoff,
    )

    selected: list[str] = []
    missing_price_count = 0

    for record in universe_records:
        code = record.current_code
        if code not in codes_with_price:
            missing_price_count += 1
            # 가격 없으면 보유수익률 계산 불가 → 포트폴리오 편입 불가. 조건 평가도
            # 가격 기반 factor 면 어차피 N/A. 편입 후보에서 제외 (사실만).
            continue

        provider = DbFieldProvider(
            code=code,
            as_of=as_of,
            price_repo=cached_price,
            financial_repo=cached_financial,
            corporate_action_repo=cached_ca,
            market_cap_repo=cached_market_cap,
            treasury_repo=cached_treasury,
            macro_repo=macro_repo,
            # M7 #4 (§2.10 break) — dividend / total return field 실연결. live
            # backtest 와 reproduce_backtest 가 동일 배선해야 dividend-yield/
            # total-return 조건이 silent N/A 아닌 같은 값 → equity_curve 재현.
            # dividend_repo None 시 두 field 정식 N/A(하위호환). adjuster stateless.
            dividend_repo=cached_dividend,
            total_return_adjuster=(
                TotalReturnAdjuster() if dividend_repo is not None else None
            ),
            factor_pack=pack,
            evaluator=evaluator,
            # ADR-0033 D4 — reproduce cutoff 주입(price/market_cap→krx, financial/
            # treasury→dart). default None 무필터(기존 평가 동작 — 회귀 0).
            krx_batch_cutoff=krx_batch_cutoff,
            dart_batch_cutoff=dart_batch_cutoff,
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
    dividend_repo: DividendRepository | None = None,
    cost_assumptions: CostAssumptions | None = None,
    price_adjuster: PriceAdjuster | None = None,
    # ADR-0033 D4 — reproduce 재현 모드. frozen batch_id → BatchCutoff 주입 시
    # 가격/시총(krx)·재무/자사주(dart) fetch 를 frozen 이하 batch row 로 한정
    # (늦은 backfill row 제외 → 재실행 결정성). default None 은 무필터(기존
    # execute_backtest 동작 — 회귀 0). corporate action 보정은 cutoff freeze 불가
    # (fetch_actions batch_cutoff 부재 — 상속 한계, known limit).
    krx_batch_cutoff: BatchCutoff | None = None,
    dart_batch_cutoff: BatchCutoff | None = None,
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
        dividend_repo: cash_dividend repository (M7 #4, §2.10 break 수정). None
            이면 `dividend_per_share_trailing_annual` / `total_return_trailing_1y`
            정식 N/A (하위호환). 주입 시 total_return_adjuster 가 함께 배선돼
            dividend-yield / total-return 조건을 평가 — live backtest 와
            reproduce_backtest 가 동일 배선해야 equity_curve byte-동일 재현.
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

    # N+1 완화 (screen 동일 — caching_repositories) — macro_indicator 는 종목-무관
    # ((indicator_id, as_of)) 이라 한 rebalance 시점 t 의 universe N 종목이 같은
    # macro 쿼리를 반복한다. backtest 전체(모든 rebalance) 1개 request-scoped 캐시로
    # 감싸면 시점당 N×M → M 으로 축약(전체 N×R×M → R×M). 캐시 키에 as_of 가 있어
    # rebalance 시점 간에는 공유되지 않지만(시점별 다른 PIT 값), 시점 내 N 종목
    # 중복은 제거된다. PIT 불변이라 equity_curve 결과 무변경. 이중 래핑 방어 —
    # reproduce_backtest 등 호출자가 이미 감쌌으면 재래핑 skip(idempotent).
    if macro_repo is not None and not isinstance(
        macro_repo, CachingMacroIndicatorRepository
    ):
        macro_repo = CachingMacroIndicatorRepository(macro_repo)

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
            dividend_repo=dividend_repo,
            costs=costs,
            adjuster=adjuster,
            krx_batch_cutoff=krx_batch_cutoff,
            dart_batch_cutoff=dart_batch_cutoff,
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
    dividend_repo: DividendRepository | None,
    costs: CostAssumptions,
    adjuster: PriceAdjuster,
    # ADR-0033 D4 — reproduce cutoff. public run_backtest 에서 전달(루프 본체가
    # _select_portfolio·_adjusted_closes_at 에 분배). default None 무필터.
    krx_batch_cutoff: BatchCutoff | None = None,
    dart_batch_cutoff: BatchCutoff | None = None,
) -> BacktestResult:
    """run_backtest 의 localcontext 내부 본체 — Decimal 결정성 보장 하에 순회.

    rebalance 그리드 t_0..t_n 를 순회하며, 각 [t_i, t_{i+1}] 구간의 동일가중
    보유수익률을 누적가치에 곱한다. 폐지 종목은 t_{i+1} 시점에 가격 시계열이
    t_{i+1} 이전에서 끝나므로 그 마지막 종가가 사용됨(폐지일까지 보유 후 현금
    동결 — `_adjusted_closes_at` 이 as_of 이하 최신 종가를 pick).
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
            dividend_repo=dividend_repo,
            krx_batch_cutoff=krx_batch_cutoff,
            dart_batch_cutoff=dart_batch_cutoff,
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
                krx_batch_cutoff=krx_batch_cutoff,
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
    # ADR-0033 D4 — reproduce cutoff(가격 fetch frozen 한정). default None 무필터.
    krx_batch_cutoff: BatchCutoff | None = None,
) -> Decimal:
    """[t, t_next] 구간의 동일가중 보유수익률.

    각 종목 i 의 수익률 = (수정종가@t_next / 수정종가@t) - 1. 두 시점 모두
    `price_adjuster.adjust(as_of=t_next)` 의 보정 시계열에서 조회 — 같은 보정
    기준(t_next)으로 두 가격을 잡아야 corporate action 보정이 일관 (분할 등이
    구간 내 발생해도 비율 왜곡 0).

    폐지 종목: t_next 이전에 시계열이 끝나면 그 마지막 종가가 close@t_next 로
    사용됨 (폐지일까지 보유 후 현금 동결 — `_adjusted_closes_at` 이 as_of 이하
    최신 종가를 pick). t 시점 가격이 없으면 그 종목은 수익률 계산 제외 (편입 불가
    종목이 holdings 에 없으므로 정상 경로엔 미발생).

    빈 포트폴리오(holdings 0)는 현금 보유 → 수익률 0 (사실만, 외삽 금지).
    """
    if not holdings:
        return Decimal(0)

    # per-code N+1 완화 (ⓒ — universe 경로의 price/financial bulk prime 과 동형).
    # holdings 의 각 종목마다 _adjusted_closes_at 가 price.fetch_prices·CA.fetch_
    # actions 를 개별 호출(R×H round-trip)하므로, 루프 **이전** 1회 bulk prime 으로
    # 청크 쿼리 몇 회(R×~1)로 축약. 단일 보정 기준(as_of=t_next, 1.1 수정)이라 두
    # 캐시 모두 as_of=t_next 단일 키 — bulk prime 이 깔끔히 들어맞는다.
    #   - price: 보유수익률은 [fetch_start, t_next] full-range 가 필요해(universe
    #     경로의 bounded window 밖) window_days 를 그 구간 전체로 prime. 캐시 start=
    #     fetch_start 라 fetch_prices(start=fetch_start) 가 전체 통과(byte-동일).
    #     이중 래핑 방어(idempotent) — 다른 as_of/window 밖 요청은 inner 위임.
    #   - corporate_action: cutoff 없는 raw bulk + serve-time 해소(financial 동형).
    # 캐시 수명 = 이 구간(단일 t_next). holdings≪universe 라 메모리 무해.
    full_window_days = max((t_next - fetch_start).days, 0)
    cached_price = (
        price_repo
        if isinstance(price_repo, CachingPriceRepository)
        else CachingPriceRepository(price_repo)
    )
    cached_price.prime(
        holdings, as_of=t_next, batch_cutoff=krx_batch_cutoff,
        window_days=full_window_days,
    )
    cached_ca = corporate_action_repo
    if corporate_action_repo is not None:
        cached_ca = (
            corporate_action_repo
            if isinstance(corporate_action_repo, CachingCorporateActionRepository)
            else CachingCorporateActionRepository(corporate_action_repo)
        )
        cached_ca.prime(holdings, as_of=t_next)

    returns: list[Decimal] = []
    for code in holdings:
        # 두 시점 종가를 **단일 as_of=t_next 보정 시계열**에서 함께 추출 — 보정 기준
        # 통일로 구간 내 분할/병합/유상증자 발효 시에도 비율 왜곡 0(_adjusted_
        # closes_at docstring). 과거엔 close_t 를 as_of=t 로 별도 보정해 보유구간
        # price-adjust action 이 허위 ±점프를 만들었다(BACKTEST_ENGINE_VERSION 1.1
        # 에서 수정). bulk prime 으로 fetch 가 캐시 서빙(round-trip 제거).
        closes = _adjusted_closes_at(
            code, as_of=t_next, start=fetch_start, pick_dates=(t, t_next),
            price_repo=cached_price,
            corporate_action_repo=cached_ca, adjuster=adjuster,
            krx_batch_cutoff=krx_batch_cutoff,
        )
        close_t = closes.get(t) if closes is not None else None
        close_t_next = closes.get(t_next) if closes is not None else None
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
