"""DbFieldProvider — factor_evaluator 의 FieldProvider Protocol 의 생산용 구현체.

M1-A T50 의 핵심 산출물. M0 에서 `stocks.py:_build_factor_stubs` 가 모든 factor
를 N/A stub 으로 채우던 placeholder 를 제거하고, factor pack 의 formula 가 참조
하는 field 명을 **DB fact 로 실제 해소**하는 field-resolution 레지스트리.

설계 원칙 (factor_evaluator / pit_enforcer / sql_repositories 스타일 모방):

1. **PIT 필수** — 모든 해소가 PITEnforcer 의 active chain (정정공시 해소) 또는
   KRX 캘린더 영업일 경계를 통과. `effective_date <= as_of`. stale / look-ahead 0.
   - 재무 scalar/series 는 FinancialRepository 가 `latest_active_by_key`
     (`pit_enforcer.py:360`) 로 이미 fiscal_period 별 latest active 를 반환.
   - 가격은 PriceRepository 가 `effective_date <= as_of` 범위 fetch + PriceAdjuster
     의 as_of-aware read-time 보정.

2. **field-resolution 레지스트리** — `_RESOLVERS` mapping 이 각 field 명을 해소
   전략 (resolver callable) 에 매핑. 알 수 없는 field 는 `UnknownFieldError`
   (pack typo / schema 불일치 — Sentry alert 대상). 해소는 가능하나 데이터가
   결손이면 `None` / 빈 series (운영 일상의 N/A — silent).

3. **strict series 의미 준수** (`factor_evaluator.py:133-143`) — `get_quarterly_series`
   는 정확히 n 개 또는 빈 sequence. 부분 series 반환 금지.

4. **request-scoped 캐싱** — 한 종목 detail 평가에서 여러 factor 가 같은 field
   (예: `market_cap_ex_treasury` 가 여러 PER 식에 공유) 를 참조하므로, 한
   provider 인스턴스 lifecycle 동안 fetch 결과를 메모이즈. as_of 는 생성자 고정.

5. **시가총액 / 발행주식수 해소 (Phase B)** — `market_cap_krx_official` /
   `shares_issued` 는 KRX 일배치가 영구화한 `market_caps` table 을
   MarketCapRepository.fetch_latest (`effective_date <= as_of` 중 최신) 로 해소.
   `market_cap_repo` 미주입 (None) 시 정식 N/A.

6. **자사주 해소 (DART stockTotqySttus 영구화)** — `shares_treasury` 는 DART
   일배치가 treasury_shares table 에 영구화한 보통주 자기주식수를
   TreasurySharesRepository.fetch_latest_active (정정 chain 해소 후 최신
   fiscal_period) 로 해소. `treasury_repo` 미주입 (None) 또는 결측 시 정식 N/A.
   이 field 가 해소되면 factor `market-cap:ex-treasury` (primitive 입력
   shares_issued - shares_treasury) 가 evaluator 에 의해 자동 실평가됨.
   여전히 정식 N/A 인 field: `dividend_per_share_trailing_annual` (주당배당 집계
   미합류). placeholder 코드가 아닌, resolver 가 정식 `None` 반환 → evaluator 가
   `missing_input:<field>` N/A 결과 생성. `trading_value_20d_avg` 는 T51
   (이번 cycle OUT).

7. **파생 필드 해소 (derived_factor — Option B)** — `market_cap_ex_treasury` 는
   primitive DB field 가 아니라 다른 factor `market-cap:ex-treasury` 의 출력
   (formula = (shares_issued - shares_treasury) × close_price_adjusted) 을
   가리키는 **파생 composite field**. PER/PBR factor 가 이 field 를 입력으로
   참조한다. provider 가 이 field 를 만나면 **대응 factor 를 FactorEvaluator 로
   평가**하여 그 결과값을 반환한다 (`_resolve_derived_factor`). composite 공식을
   provider 에 하드코딩하지 **않는다** — Fidelity (표시값 = 명시 공식, 단일 출처;
   8 기둥 §2.1) 위배 + pack 공식 변경 시 divergence 방지. 따라서
   `get_scalar("market_cap_ex_treasury")` 의 값은
   `evaluate(market-cap:ex-treasury factor)` 의 값과 **반드시 동일**.
   - pack / evaluator 미주입 시 정식 N/A (None) — 비-stocks 컨텍스트 / 테스트
     호환 (Phase B 의 repo 미주입과 동일 패턴).
   - 순환 탐지 (`self._resolving`) — 파생 factor 가 자기 자신을 field 로 참조
     (직·간접) 하면 pack 무결성 위반 → `UnknownFieldError` (cycle 은 데이터 N/A
     가 아닌 pack 결함이므로 fail-loud; `factor_evaluator.py:16-19` 의 pack
     무결성 vs 데이터 결손 이분법과 일관).
   - 파생 결과도 get_scalar 의 `_scalar_cache` 로 1 회만 계산 후 재사용 —
     PER + PBR 이 같은 `market_cap_ex_treasury` 를 공유해도 sub-factor 평가는
     1 회 (request-scoped 캐싱).

관련 ADR / 문서:
- m1-milestone.md Phase M1-A T50 / `.sisyphus/plans/speculum-m1.md`
- ADR-0001 (가격 보정), ADR-0002 D3/D5 (PIT effective_date), ADR-0004 (factor
  정의), ADR-0005 (K-IFRS 연결/별도), ADR-0009 D5 (supersede chain)
- factor_evaluator.py:108 (FieldProvider Protocol), pit_enforcer.py:360
  (latest_active_by_key), price_adjuster.py:545 (as_of-aware adjust)

이번 cycle OUT (다음 cycle 명시):
- T51: `trading_value_20d_avg` 20 영업일 집계 (daily price series + KRX 캘린더).
  현재는 정식 N/A.
- dividend 의 주당배당 집계 — 별도 cycle. 현재는 정식 N/A.
- `market_cap_krx_official` 은 primitive (market_caps DB) 유지 — 파생으로
  바꾸지 않음 (별도 이슈, 본 cycle 범위 밖).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from app.repositories.batch_run_repository import BatchCutoff
from app.repositories.pit_protocols import (
    CorporateActionRepository,
    DividendRepository,
    FinancialRecord,
    FinancialRepository,
    MacroIndicatorRepository,
    MarketCapRecord,
    MarketCapRepository,
    PriceRepository,
    TreasurySharesRepository,
)
from app.services.factor_evaluator import UnknownFieldError

if TYPE_CHECKING:
    from app.services.factor_evaluator import FactorEvaluator
from app.services.krx_calendar import (
    DEFAULT_CALENDAR,
    CalendarRangeError,
    TradingCalendar,
)
from app.services.price_adjuster import AdjusterError, PriceAdjuster, _as_decimal
from app.services.total_return_adjuster import TotalReturnAdjuster

__all__ = [
    "FIELD_RESOLUTIONS",
    "DbFieldProvider",
    "FieldResolution",
]


# 가격 fetch 시 lookback window — 보정 사건 누적 + 직전 영업일 보장. 직전 거래일
# 종가만 필요하나, PriceAdjuster 가 as_of 범위 corporate action 보정을 누적하므로
# 충분한 과거 window 를 준다 (휴장 연휴 + 데이터 결손 대비 ~30 일).
_PRICE_LOOKBACK_DAYS: Final[int] = 30

# 재무 fetch 의 최대 분기 수 — trailing 4 분기 + period_begin 직전 분기 + 여유.
# fetch_financials 의 max_periods default 는 8 이나, 명시적으로 strict series
# (4 분기) + period_begin (5 번째 직전) 을 cover 하도록 8 사용.
_FINANCIAL_MAX_PERIODS: Final[int] = 8

# trading_value 평균의 영업일 window (factor pack `trading_value_20d_avg`). KRX
# 캘린더 기준 정확히 20 영업일 — 단순 row count 가 아닌 휴장일 제외 영업일 수.
_TRADING_VALUE_WINDOW: Final[int] = 20

# trailing-annual 집계 window (일수) — `dividend_per_share_trailing_annual` 의 직전
# 12 개월 cash_dividend 합산 + `total_return_trailing_1y` 의 직전 1 년 시계열 lookback.
# relativedelta(years=1) 대신 365 일 고정(외부 의존 회피, 윤년 ±1 일 무시 — 배당
# 집계·총수익률 둘 다 직전 1 년 근사로 충분).
_TRAILING_ANNUAL_DAYS: Final[int] = 365

# total_return_trailing_1y 의 min-coverage guard (M7 #4 fix3, §2.1 Fidelity).
# total return 시계열의 첫 거래일이 as_of 로부터 이 일수 이내이면 "직전 1 년"을
# cover 하지 못한 것(상장 1 년 미만 종목 / 가격 history 결손)이므로 N/A 반환.
# 340 일 = 365 일에서 휴장·상장 tolerance 를 뺀 합리값(영업일 ~250 일 기준, 약
# 11 개월 history 면 1 년 총수익률로 인정). _resolve_financial_annual(strict 4
# 분기)·_resolve_trading_value_avg(strict 20 영업일) 의 strict-or-N/A 패턴 일관 —
# 데이터 의존 lookback("3 개월짜리를 1 년이라 라벨")의 §2.1 위반 차단.
_TRAILING_ANNUAL_MIN_COVERAGE_DAYS: Final[int] = 340


# =============================================================================
# Field-resolution 전략 메타 — 디버깅 / 문서화용
# =============================================================================

@dataclass(frozen=True, slots=True)
class FieldResolution:
    """단일 field 의 해소 전략 메타 — 레지스트리 entry.

    Attributes:
        field: factor pack formula 가 참조하는 field 명.
        kind: 해소 종류 — "financial_scalar" / "financial_annual" /
            "financial_period_begin" / "price_adjusted" / "derived_factor" /
            "macro_indicator" / "unsupported_m0".
        account: financial 계열일 때 DB financials.account canonical key.
        ifrs_type: financial 계열일 때 ifrs_type ("consolidated" 등).
        target_factor: kind == "derived_factor" 일 때 평가 대상 factor 의
            canonical_id (예: "market-cap:ex-treasury"). 그 외 None.
        indicator_id: kind == "macro_indicator" 일 때 ECOS 통계 식별자
            (예: "722Y001/0101000"). vintage PIT(MacroIndicatorRepository.
            fetch_latest)로 해소. 그 외 None.
        note: unsupported / N/A 사유 (운영 문서).
        magnitude: True 면 financial scalar value 를 절댓값(크기)으로 정규화.
            부호가 의미와 무관한 field(예: dividends_paid_annual — K-IFRS
            현금흐름표의 "재무활동 배당금 지급"은 현금유출이라 음수로 보고될 수
            있으나 dividend-yield 분자는 *배당 규모*다) 의 §2.1 거울 왜곡(음수 yield)
            차단. financial_scalar kind 에만 적용(conformance M2.1 이슈 1).
    """

    field: str
    kind: str
    account: str | None = None
    ifrs_type: str | None = None
    target_factor: str | None = None
    indicator_id: str | None = None
    note: str = ""
    magnitude: bool = False


# DART 재무 account 매핑 — factor pack field 명 → DB financials.account.
#
# factor pack 의 field 명은 의미를 embed (예: `net_income_attributable_consolidated_ifrs`
# 는 "지배기업소유주 귀속 연결 당기순이익"). DB 의 financials.account 는
# dart_account_mapper.py 의 canonical key (`net_income_attributable_to_owners` 등)
# 이고 ifrs_type 은 별도 컬럼. 따라서 field 명 → (account, ifrs_type) 매핑이 필요.
_NET_INCOME_ACCOUNT: Final[str] = "net_income_attributable_to_owners"
_EQUITY_ACCOUNT: Final[str] = "equity_attributable_to_owners"
_BASIC_EPS_ACCOUNT: Final[str] = "basic_eps"
_CONSOLIDATED: Final[str] = "consolidated"
# 리츠(REIT) FFO/배당 구성 계정 (dart_account_mapper.py:91-93, ADR-0023 D3-c/D8).
# 빌트인 pack 무변경 — 이 field 들은 reference custom pack
# (speculum-reit-reference) 의 리츠 factor 만 참조. 보통주·감가상각 미보고 리츠는
# 계정 결손 → resolver None → evaluator 가 missing_input N/A (§2.1 Fidelity).
_DEPRECIATION_ACCOUNT: Final[str] = "depreciation_expense"
_DIVIDENDS_PAID_ACCOUNT: Final[str] = "dividends_paid_annual"


# B1 (ROADMAP_v2 V1b) — DART 분기보고서의 **누적(YTD) flow 계정** 집합.
#
# DART 손익계산서·현금흐름표 항목(`thstrm_amount`)은 회계연도 기준 **누적값**이다:
# 1분기=3M, 반기=6M, 3분기=9M, 사업보고서=12M(연간). 따라서 TTM(4분기 합)을
# 단순히 raw 분기값으로 더하면 중복 계산되어 ~2.5배 과대해진다(예: 삼성 2024-06-28
# 기준 EPS — naive 합 4144 vs 정확 standalone 합 2900). `_resolve_financial_series`
# 가 본 집합의 계정에 대해 **누적→분기단독(standalone) 변환** 후 합산한다.
#
# **재무상태표(stock) 계정은 미포함** — 자본/자산/부채는 시점 잔액이라 누적이 아니다
# (그대로 시점값 사용). 이미 연 누적 의미인 `_annual` resolution 계정(dividends_paid_
# annual, depreciation_expense)도 미포함(별도 처리).
#
# **재현성(metis H1 — 미해결 follow-up)**: 본 변환은 factor 값을 바꿔 result_hash 에
# 영향한다. 정식으로는 `field_resolution_policy_version` 신규 키로 freeze 해야 하나,
# 현재 실 frozen run 이 0건(시스템이 실값을 낸 적 없음)이라 기존 재현 파괴 없음 →
# 정책버전 키는 후속(ROADMAP_v2 Phase 1). 본 변경 시점 이후 저장 run 부터 적용.
_FLOW_CUMULATIVE_ACCOUNTS: Final[frozenset[str]] = frozenset({
    "basic_eps",
    "diluted_eps",
    "net_income",
    "net_income_attributable_to_owners",
    "revenue",
    "operating_income",
    "cash_flow_operating",
    "cash_flow_investing",
    "cash_flow_financing",
})


def _parse_fiscal_period(fiscal_period: str) -> tuple[int, int]:
    """`"2024Q1"` → `(2024, 1)`. dart_daily 의 `f"{year}Q{quarter}"` 포맷 전제.

    파싱 실패 시 `ValueError` — 호출자가 strict N/A 로 처리(비표준 포맷은 변환 불가).
    """
    year_str, _, quarter_str = fiscal_period.partition("Q")
    return int(year_str), int(quarter_str)


# 레지스트리 — field 명 → FieldResolution 메타. resolver dispatch 는
# DbFieldProvider 의 method 들이 kind 기준으로 수행.
_RESOLUTIONS: Final[dict[str, FieldResolution]] = {
    # ----- 가격 (PriceAdjuster as_of-aware read-time 보정) -----
    "close_price_adjusted": FieldResolution(
        field="close_price_adjusted", kind="price_adjusted",
    ),
    # ----- 재무 scalar (latest active fiscal_period) -----
    "equity_attributable_to_common_consolidated_ifrs": FieldResolution(
        field="equity_attributable_to_common_consolidated_ifrs",
        kind="financial_scalar",
        account=_EQUITY_ACCOUNT, ifrs_type=_CONSOLIDATED,
    ),
    # ----- 재무 period-begin (해당 분기 시작 직전 active row) -----
    "equity_attributable_to_common_consolidated_ifrs_period_begin": FieldResolution(
        field="equity_attributable_to_common_consolidated_ifrs_period_begin",
        kind="financial_period_begin",
        account=_EQUITY_ACCOUNT, ifrs_type=_CONSOLIDATED,
    ),
    # ----- 재무 trailing-annual scalar (4 분기 strict 합) -----
    "net_income_attributable_consolidated_ifrs_annual": FieldResolution(
        field="net_income_attributable_consolidated_ifrs_annual",
        kind="financial_annual",
        account=_NET_INCOME_ACCOUNT, ifrs_type=_CONSOLIDATED,
    ),
    # ----- 재무 quarterly series (sum_last_n_quarters 의 strict series) -----
    "net_income_attributable_consolidated_ifrs": FieldResolution(
        field="net_income_attributable_consolidated_ifrs",
        kind="financial_series",
        account=_NET_INCOME_ACCOUNT, ifrs_type=_CONSOLIDATED,
    ),
    "basic_eps_consolidated_ifrs": FieldResolution(
        field="basic_eps_consolidated_ifrs",
        kind="financial_series",
        account=_BASIC_EPS_ACCOUNT, ifrs_type=_CONSOLIDATED,
    ),
    # ----- 리츠(REIT) FFO/배당 구성 재무 scalar (reference pack 전용) -----
    # 빌트인 pack 무변경 (ADR-0023 D8 재현성 경로) — 이 field 들은 reference
    # custom pack speculum-reit-reference 의 ffo-multiple:reit / dividend-yield:reit
    # factor 만 참조. security_type 분기 코드 없음 — 보통주·감가상각 미보고 리츠는
    # 계정 자연 결손 → resolver None → evaluator 가 missing_input:<field> N/A
    # (§2.1 Fidelity — 데이터 결손의 정식 N/A, placeholder 아님).
    #
    # depreciation: net_income annual 과 동일한 trailing-annual(4 분기 strict 합)
    # 의미 — FFO = net_income TTM + depreciation TTM 의 연간 일관성. 4 분기 미만이면
    # _resolve_financial_annual 이 N/A → FFO 구성 결손 → ffo-multiple:reit N/A.
    "depreciation_expense_annual": FieldResolution(
        field="depreciation_expense_annual",
        kind="financial_annual",
        account=_DEPRECIATION_ACCOUNT, ifrs_type=_CONSOLIDATED,
    ),
    # dividends_paid: DART DividendsPaidClassifiedAsFinancingActivities 는
    # 현금흐름표의 재무활동 배당금 지급 — 이미 연 누적값이라 trailing-quarter 합이
    # 아닌 latest active fiscal_period scalar (financial_scalar). 결손 시 N/A.
    # magnitude=True — 현금유출이라 음수로 보고될 수 있으나 배당 *규모*가 의미
    # (dividend-yield 분자). 절댓값 정규화로 음수 yield(거울 왜곡) 차단(M2.1 이슈 1).
    "dividends_paid_annual": FieldResolution(
        field="dividends_paid_annual",
        kind="financial_scalar",
        account=_DIVIDENDS_PAID_ACCOUNT, ifrs_type=_CONSOLIDATED,
        magnitude=True,
    ),
    # ----- 시가총액 / 발행주식수 (market_caps 영구화, Phase B) -----
    # pykrx adapter 가 fetch → KRX 일배치가 market_caps table 영구화.
    # MarketCapRepository.fetch_latest (effective_date <= as_of 중 최신) 로 해소.
    "market_cap_krx_official": FieldResolution(
        field="market_cap_krx_official", kind="market_cap",
        note="market_caps.market_cap (KRX 공식 시가총액, PIT effective_date<=as_of)",
    ),
    "shares_issued": FieldResolution(
        field="shares_issued", kind="shares",
        note="market_caps.shares_outstanding (발행주식수, 자사주 포함)",
    ),
    # ----- 자사주 (treasury_shares 영구화, DART stockTotqySttus) -----
    # DART 일배치가 보통주 자기주식수를 treasury_shares table 에 영구화.
    # TreasurySharesRepository.fetch_latest_active (정정 chain 해소 후 최신
    # fiscal_period) 로 해소. treasury_repo 미주입 (None) 또는 결측 시 정식 N/A.
    # 이 field 가 해소되면 factor `market-cap:ex-treasury` (primitive 입력
    # shares_issued - shares_treasury) 가 evaluator 에 의해 자동 실평가됨.
    "shares_treasury": FieldResolution(
        field="shares_treasury", kind="treasury",
        note="treasury_shares.shares_treasury (DART 보통주 자기주식수, "
             "PIT 정정 chain 해소). repo None 또는 결측 시 정식 N/A",
    ),
    # ----- 파생 필드 (derived_factor — 대응 factor 를 evaluator 로 평가) -----
    # market_cap_ex_treasury 는 primitive DB field 가 아니라 다른 factor
    # `market-cap:ex-treasury` 의 출력 (formula = (shares_issued -
    # shares_treasury) × close_price_adjusted). PER/PBR 가 입력으로 참조.
    # provider 는 composite 공식을 하드코딩하지 않고 (Fidelity — 단일 출처;
    # pack 공식 변경 시 divergence 방지) 대응 factor 를 FactorEvaluator 로
    # 평가해 그 결과값을 반환 (`_resolve_derived_factor`). 따라서 get_scalar 값은
    # evaluate(market-cap:ex-treasury) 값과 동일. pack/evaluator 미주입 시 N/A.
    "market_cap_ex_treasury": FieldResolution(
        field="market_cap_ex_treasury", kind="derived_factor",
        target_factor="market-cap:ex-treasury",
        note="파생 composite field — 대응 factor market-cap:ex-treasury 를 "
             "evaluator 로 평가한 결과값 (provider 하드코딩 composite 금지, "
             "Fidelity 단일 출처). pack/evaluator 미주입 시 정식 N/A",
    ),
    # ----- ECOS 매크로 지표 — vintage PIT (M2 T81, M1 표시용→factor 연산용) -----
    # MacroIndicatorRepository.fetch_latest(indicator_id, as_of=as_of) 가
    # vintage 이중 시간축 PIT (vintage_date<=as_of AND reference_date<=as_of)
    # 를 강제하므로 look-ahead 0 보장 (AC-M1-P-05 연속). macro_repo 미주입
    # (None) 이면 정식 N/A (None) — silent regression 아님이나 기능 미동작.
    "ecos_base_rate": FieldResolution(
        field="ecos_base_rate", kind="macro_indicator",
        indicator_id="722Y001/0101000",
        note="ECOS 매크로 factor 입력, vintage PIT(M2 T81 — M1 표시용→연산용). "
             "한국은행 기준금리. macro_repo 미주입 시 정식 N/A",
    ),
    "ecos_cpi": FieldResolution(
        field="ecos_cpi", kind="macro_indicator",
        indicator_id="901Y009/0",
        note="ECOS 매크로 factor 입력, vintage PIT(M2 T81 — M1 표시용→연산용). "
             "소비자물가지수. macro_repo 미주입 시 정식 N/A",
    ),
    # ----- KOSIS 매크로 지표 — 통계청 고유 지표만 (ECOS 미보유, M9 #2, ADR-0036 D5/D6) -----
    # ECOS 기존 지표(CPI/PPI 등)는 제외 — ECOS 1차 SoT(ADR-0036 D5).
    # _resolve_macro_indicator 무수정 — indicator_id만 다르면 generic 동작.
    # vintage PIT: reference_date<=as_of AND vintage_date<=as_of (ECOS 동일 규약).
    # macro_repo 미주입(None) 시 정식 N/A — ECOS 선례 동일.
    # 표시(_MACRO_INDICATORS in market.py) + factor field 둘 다 등록 (ADR-0007 D5 현 효력, ADR-0036 D6).
    #
    # indicator_id = "kosis/{orgId}/{tblId}/{itmId}" (objL1 은 id 에 미포함 — 배치
    # _KOSIS_INDICATORS 의 obj_l 필드가 보유). 아래 tblId/itmId 는 **라이브 실측
    # 검증됨(2026-06-18, Param/statisticsParameterData.do 로 실데이터 반환 확인)**:
    #   실업률·고용률 = 경제활동인구조사 월별 표 DT_1DA7001S(itm T80/T90),
    #   전산업생산 = 산업활동동향 DT_1JH20201(원지수 T1),
    #   경기선행 = 경기종합지수 DT_1C8015(T1). (배치의 objL1 도 실측 검증.)
    "kosis_unemployment_rate": FieldResolution(
        field="kosis_unemployment_rate", kind="macro_indicator",
        indicator_id="kosis/101/DT_1DA7001S/T80",
        note="KOSIS 통계청 실업률 (경제활동인구조사 DT_1DA7001S, itm=T80, objL1=0 성별 계). "
             "ECOS 미보유 통계청 고유 지표 — ECOS 1차 SoT(ADR-0036 D5). "
             "macro_repo 미주입 시 정식 N/A. 라이브 검증됨(2026-06-18).",
    ),
    "kosis_employment_rate": FieldResolution(
        field="kosis_employment_rate", kind="macro_indicator",
        indicator_id="kosis/101/DT_1DA7001S/T90",
        note="KOSIS 통계청 고용률 (경제활동인구조사 DT_1DA7001S, itm=T90, objL1=0 성별 계). "
             "ECOS 미보유 통계청 고유 지표 — ECOS 1차 SoT(ADR-0036 D5). "
             "macro_repo 미주입 시 정식 N/A. 라이브 검증됨(2026-06-18).",
    ),
    "kosis_industrial_production": FieldResolution(
        field="kosis_industrial_production", kind="macro_indicator",
        indicator_id="kosis/101/DT_1JH20201/T1",
        note="KOSIS 통계청 전산업생산지수 (산업활동동향 원지수 DT_1JH20201, itm=T1, "
             "objL1=0 농림어업 제외). ECOS 미보유 통계청 고유 지표 — ECOS 1차 SoT(ADR-0036 D5). "
             "macro_repo 미주입 시 정식 N/A. 라이브 검증됨(2026-06-18).",
    ),
    "kosis_leading_index": FieldResolution(
        field="kosis_leading_index", kind="macro_indicator",
        indicator_id="kosis/101/DT_1C8015/T1",
        note="KOSIS 통계청 경기선행지수 (경기종합지수 DT_1C8015, itm=T1, objL1=A00 선행종합지수). "
             "ECOS 미보유 통계청 고유 지표 — ECOS 1차 SoT(ADR-0036 D5). "
             "M9_PLAN §3 #2 의 '경기선행/동행지수' 중 선행. macro_repo 미주입 시 정식 N/A. "
             "라이브 검증됨(2026-06-18).",
    ),
    # ----- 주당배당 trailing-annual (corporate_actions cash_dividend 집계, M7 #4) -----
    # DividendRepository.fetch_dividends (이중 PIT — announced<=as_of AND
    # effective<=as_of, supersede chain 해소) 가 반환한 cash_dividend 중 직전 12 개월
    # (effective_date > as_of-365 AND <= as_of) 의 per_share 를 Decimal 합산.
    # per_share = cash_amount(Decimal) coalesce details["per_share"](#3 패턴 일관).
    # dividend_repo 미주입(None) 시 정식 N/A. v1.0.0 의 dividend-yield:trailing-annual
    # factor AST(ratio_pct(이 field, close_price_adjusted))가 이 field 를 소비 —
    # formula 무변경.
    "dividend_per_share_trailing_annual": FieldResolution(
        field="dividend_per_share_trailing_annual", kind="dividend_per_share_annual",
        note="직전 12 개월 cash_dividend per_share 합 (이중 PIT). repo None 시 N/A",
    ),
    # ----- 1 년 총수익률 (배당 재투자 포함, M7 #4 — price-return:total-annual) -----
    # 직전 1 년 보정 종가 시계열 + cash_dividend 를 TotalReturnAdjuster 로 chaining 한
    # total return index 의 마지막값 - 1 = 1 년 누적 총수익률 ratio (소수). field 는
    # 다른 raw field 와 일관되게 **ratio** 반환 — v1.1.0 factor 가 unit=percent 로
    # 표현(ratio_pct/dividend-yield 와 동일하게 ×100 은 표시 layer 책임, ADR-0002 D6).
    # dividend_repo 또는 total_return_adjuster 미주입 시 정식 N/A.
    "total_return_trailing_1y": FieldResolution(
        field="total_return_trailing_1y", kind="total_return_annual",
        note="직전 1 년 total return index[-1] - 1 (배당 재투자 ratio). "
             "dividend_repo / total_return_adjuster 미주입 시 정식 N/A",
    ),
    # ----- 거래대금 20 영업일 평균 (volume-turnover liquidity, T51) -----
    "trading_value_20d_avg": FieldResolution(
        field="trading_value_20d_avg", kind="trading_value_avg",
        note="KRX 캘린더 20 영업일 trading_value 평균 (raw 금액, 보정 무관)",
    ),
}


# =============================================================================
# DbFieldProvider
# =============================================================================

class DbFieldProvider:
    """FieldProvider Protocol 의 생산용 구현체 — repository 주입 + as_of 보유.

    한 종목 (code) 의 한 시점 (as_of) factor 평가에 대응하는 1 회용 인스턴스.
    여러 factor 가 같은 field 를 참조할 때 fetch 중복을 피하기 위해 instance 단위
    캐싱. as_of 는 생성자에서 고정 (FieldProvider Protocol 의 invariant —
    `factor_evaluator.py:112` PIT-aware).

    Attributes:
        as_of: PIT 기준 일자 (FieldProvider Protocol 요구 attribute).
    """

    as_of: date

    def __init__(
        self,
        *,
        code: str,
        as_of: date,
        price_repo: PriceRepository,
        financial_repo: FinancialRepository,
        corporate_action_repo: CorporateActionRepository | None = None,
        market_cap_repo: MarketCapRepository | None = None,
        treasury_repo: TreasurySharesRepository | None = None,
        macro_repo: MacroIndicatorRepository | None = None,
        dividend_repo: DividendRepository | None = None,
        total_return_adjuster: TotalReturnAdjuster | None = None,
        price_adjuster: PriceAdjuster | None = None,
        calendar: TradingCalendar = DEFAULT_CALENDAR,
        factor_pack: Any | None = None,
        evaluator: FactorEvaluator | None = None,
        krx_batch_cutoff: BatchCutoff | None = None,
        dart_batch_cutoff: BatchCutoff | None = None,
    ) -> None:
        """Args:
            code: 평가 대상 종목코드 (6 자리 정규화 후 전달 권장).
            as_of: PIT 기준 — 모든 해소가 이 시점 기준 active 만.
            price_repo: 가격 시계열 repository.
            financial_repo: 재무제표 repository (supersede chain 해소 포함).
            corporate_action_repo: 가격 보정용 corporate action repository. None
                이면 보정 미적용 (raw 종가 = adjusted, identity).
            market_cap_repo: 시가총액 / 발행주식수 repository (Phase B). None 이면
                `market_cap_krx_official` / `shares_issued` 정식 N/A.
            treasury_repo: 자사주 (보통주 자기주식수) repository (DART
                stockTotqySttus 영구화). None 이면 `shares_treasury` 정식 N/A
                (→ factor `market-cap:ex-treasury` 도 missing_input N/A).
            macro_repo: ECOS 거시지표 repository (M2 T81). None 이면
                `ecos_base_rate` / `ecos_cpi` 등 kind "macro_indicator" field
                정식 N/A — 기능 미동작이나 silent regression 아님 (미주입 컨텍스트
                에서 ECOS field 를 쓰는 factor 가 있으면 missing_input N/A 로
                표시되어 인지 가능). vintage PIT 는 fetch_latest 가 강제
                (vintage_date <= as_of, AC-M1-P-05).
            dividend_repo: cash_dividend 이중 PIT repository (M7 #1, ADR-0035 D6).
                None 이면 `dividend_per_share_trailing_annual` /
                `total_return_trailing_1y` 정식 N/A (하위호환 — market_cap_repo=None
                선례). 주입 시 fetch_dividends 가 announced<=as_of AND effective<=as_of
                + supersede chain 해소.
            total_return_adjuster: 세전 total return index 계산 엔진 (M7 #3,
                ADR-0035 D1/D6/D7). None 이면 `total_return_trailing_1y` 정식 N/A.
                `dividend_repo` 와 함께 주입돼야 의미 (둘 중 하나라도 None 이면 N/A).
            price_adjuster: as_of-aware read-time 보정 엔진. None 이면 default.
            calendar: trading_value 20 영업일 window 산출용 KRX 캘린더. default
                = `DEFAULT_CALENDAR` (as_of_policy 와 동일 패턴). 테스트가 별도
                calendar 주입 가능.
            factor_pack: 파생 필드 (kind "derived_factor") 해소용 LoadedPack —
                `body["factors"]` 에서 target_factor (canonical_id) 로 대응
                factor 를 조회. None 이면 파생 필드 정식 N/A (비-stocks 컨텍스트
                / 테스트 호환). `evaluator` 와 함께 주입돼야 의미.
            evaluator: 파생 필드 해소용 FactorEvaluator — 대응 factor 의 formula
                AST 를 self (FieldProvider) 로 재귀 평가. None 이면 파생 필드
                정식 N/A. 같은 evaluator 인스턴스를 stocks.py 의 display factor
                평가와 공유 (동일 policy version).
            krx_batch_cutoff: M1 T48c 재현 모드 — price / market_cap fetch 의
                batch cutoff (frozen KRX batch 이하 row 만). None (default) 이면
                무필터 = 기존 동작 (라이브 screen). EXCLUDE_ALL sentinel 이면
                KRX fact 전부 제외 (빈 batch_id 재현, H#5).
            dart_batch_cutoff: M1 T48c 재현 모드 — financial / treasury fetch 의
                batch cutoff (frozen DART batch 이하 row 만 → 정정 chain 보수 분기
                복원). None (default) 이면 무필터. EXCLUDE_ALL 이면 DART fact 전부
                제외.
        """
        self._code = code
        self.as_of = as_of
        self._price_repo = price_repo
        self._financial_repo = financial_repo
        self._ca_repo = corporate_action_repo
        self._market_cap_repo = market_cap_repo
        self._treasury_repo = treasury_repo
        self._macro_repo = macro_repo
        # M7 #4 fix5 — both-or-neither: dividend_repo 와 total_return_adjuster 는
        # 함께 주입되거나 함께 None 이어야 한다. 하나만 None 이면 항상 배선 버그
        # (total_return_trailing_1y 가 silent N/A 로 빠지는 §2.10 reproducibility
        # 위험). 둘 다 None(N/A)·둘 다 주입은 정상 — fail-loud.
        if (dividend_repo is None) != (total_return_adjuster is None):
            raise ValueError(
                "dividend_repo 와 total_return_adjuster 는 함께 주입되거나 함께"
                " None 이어야 합니다 (both-or-neither — total_return_trailing_1y"
                " silent N/A 방지)."
            )
        self._dividend_repo = dividend_repo
        self._total_return_adjuster = total_return_adjuster
        self._adjuster = price_adjuster or PriceAdjuster()
        self._calendar = calendar
        self._factor_pack = factor_pack
        self._evaluator = evaluator
        # M1 T48c 재현 cutoff — price/market_cap→krx, financial/treasury→dart.
        self._krx_batch_cutoff = krx_batch_cutoff
        self._dart_batch_cutoff = dart_batch_cutoff
        # 파생 필드 (derived_factor) 의 순환 탐지 — 현재 해소 진행 중인 field 명
        # 집합. 평가 중 같은 field 가 다시 참조되면 pack 무결성 위반 (cycle).
        self._resolving: set[str] = set()
        # target_factor canonical_id → factor dict 캐시 (pack body 1 회 인덱싱).
        self._factors_by_id: dict[str, dict] | None = None
        # market_cap fetch 결과 캐시 — sentinel 로 "미계산" 과 "None" 구별.
        # market_cap / shares 두 field 가 같은 fetch_latest 결과를 공유.
        self._market_cap_fetched: bool = False
        self._market_cap_record: MarketCapRecord | None = None
        # treasury fetch 결과 캐시 — sentinel 로 "미계산" 과 "None" 구별.
        self._treasury_fetched: bool = False
        self._treasury_value: Decimal | None = None

        # request-scoped 캐시 — 같은 field 의 재fetch 회피.
        self._scalar_cache: dict[str, Decimal | None] = {}
        self._series_cache: dict[tuple[str, int], tuple[Decimal, ...]] = {}
        # 재무 fetch 결과 캐시 — (account, ifrs_type) → fiscal_period asc records.
        self._financial_cache: dict[
            tuple[str, str], tuple[FinancialRecord, ...]
        ] = {}
        # close_price_adjusted 캐시 — sentinel 로 "미계산" 과 "None" 구별.
        self._adjusted_close_computed: bool = False
        self._adjusted_close: Decimal | None = None
        # total_return_trailing_1y 캐시 — sentinel 로 "미계산" 과 "None" 구별
        # (_adjusted_close 패턴). 1 년 시계열 + 배당 fetch + compute 는 무거우므로
        # request-scoped 1 회.
        self._total_return_computed: bool = False
        self._total_return_value: Decimal | None = None

    # ---------------------------------------------------------------------
    # FieldProvider Protocol — get_scalar
    # ---------------------------------------------------------------------

    def get_scalar(self, field: str) -> Decimal | None:
        """단일 scalar 값. 결손 시 None.

        Raises:
            UnknownFieldError: field 가 레지스트리에 없음 (pack typo / schema 불일치).
        """
        if field in self._scalar_cache:
            return self._scalar_cache[field]

        resolution = _RESOLUTIONS.get(field)
        if resolution is None:
            raise UnknownFieldError(
                f"unknown field '{field}' — DbFieldProvider 레지스트리에 없음 "
                f"(factor pack inputs typo 또는 미등록 field)"
            )

        value = self._resolve_scalar(resolution)
        self._scalar_cache[field] = value
        return value

    def _resolve_scalar(self, resolution: FieldResolution) -> Decimal | None:
        """resolution.kind 기준 scalar 해소 dispatch."""
        kind = resolution.kind
        if kind == "price_adjusted":
            return self._resolve_close_price_adjusted()
        if kind == "financial_scalar":
            return self._resolve_financial_scalar(resolution)
        if kind == "financial_period_begin":
            return self._resolve_financial_period_begin(resolution)
        if kind == "financial_annual":
            return self._resolve_financial_annual(resolution)
        if kind == "trading_value_avg":
            return self._resolve_trading_value_avg()
        if kind == "market_cap":
            return self._resolve_market_cap()
        if kind == "shares":
            return self._resolve_shares_outstanding()
        if kind == "treasury":
            return self._resolve_shares_treasury()
        if kind == "derived_factor":
            return self._resolve_derived_factor(resolution)
        if kind == "macro_indicator":
            return self._resolve_macro_indicator(resolution)
        if kind == "dividend_per_share_annual":
            return self._resolve_dividend_per_share_annual()
        if kind == "total_return_annual":
            return self._resolve_total_return_trailing_1y()
        if kind == "financial_series":
            # series field 가 scalar context 에서 참조되는 일은 정상 pack 에서
            # 없음 (sum_last_n_quarters 의 인자). 방어적으로 N/A.
            return None
        if kind == "unsupported_m0":
            # M0 데이터 미보유 — 정식 N/A (evaluator 가 missing_input:<field>).
            return None
        # 레지스트리에 등록됐으나 kind dispatch 누락 — 무결성 위반.
        raise UnknownFieldError(
            f"field '{resolution.field}' resolution kind '{kind}' "
            f"미지원 (레지스트리 무결성 위반)"
        )

    # ---------------------------------------------------------------------
    # FieldProvider Protocol — get_quarterly_series (strict)
    # ---------------------------------------------------------------------

    def get_quarterly_series(self, field: str, *, n: int) -> Sequence[Decimal]:
        """최근 n 분기 series (fiscal_period 오름차순). 정확히 n 개 또는 빈 sequence.

        strict 의미 (`factor_evaluator.py:133-143`) — 결손이 한 분기라도 있으면
        빈 sequence. 부분 series 반환 금지 (silent regression 위험).

        Raises:
            UnknownFieldError: field 가 레지스트리에 없음.
        """
        cache_key = (field, n)
        if cache_key in self._series_cache:
            return self._series_cache[cache_key]

        resolution = _RESOLUTIONS.get(field)
        if resolution is None:
            raise UnknownFieldError(
                f"unknown field '{field}' — DbFieldProvider 레지스트리에 없음 "
                f"(factor pack inputs typo 또는 미등록 field)"
            )

        series = self._resolve_series(resolution, n=n)
        self._series_cache[cache_key] = series
        return series

    def _resolve_series(
        self, resolution: FieldResolution, *, n: int,
    ) -> tuple[Decimal, ...]:
        """resolution.kind 기준 series 해소 dispatch (strict)."""
        kind = resolution.kind
        if kind == "financial_series":
            return self._resolve_financial_series(resolution, n=n)
        if kind == "unsupported_m0":
            # M0 미보유 — strict 빈 series.
            return ()
        # 그 외 kind 는 series context 에서 의미 없음 — 빈 series (strict N/A).
        return ()

    # ---------------------------------------------------------------------
    # 재무 해소 helpers — fetch + PIT active chain 통과
    # ---------------------------------------------------------------------

    def _fetch_financial_periods(
        self, account: str, ifrs_type: str,
    ) -> tuple[FinancialRecord, ...]:
        """(account, ifrs_type) 의 fiscal_period 별 latest active record (asc).

        FinancialRepository.fetch_financials 가 `latest_active_by_key`
        (`pit_enforcer.py:360`) 로 fiscal_period 별 정정공시 chain 을 as_of-time
        해소. ifrs_type 은 repository 의 그룹화 **이전** 필터로 위임 — 연결/별도가
        같은 (account, fiscal_period) 에 공존할 때 그룹 key 충돌 + max_periods
        희석 방지 (ADR-0005). 본 helper 는 fiscal_period 오름차순 재정렬만 추가
        (fetch_financials 의 sort 는 effective_date 우선이라 period 순서와 다를 수
        있음 — series / period_begin 의 시간순 의미 보장).
        """
        cache_key = (account, ifrs_type)
        if cache_key in self._financial_cache:
            return self._financial_cache[cache_key]

        records = self._financial_repo.fetch_financials(
            self._code,
            as_of=self.as_of,
            account=account,
            ifrs_type=ifrs_type,
            max_periods=_FINANCIAL_MAX_PERIODS,
            batch_cutoff=self._dart_batch_cutoff,
        )
        filtered = tuple(sorted(records, key=lambda r: r.fiscal_period))
        self._financial_cache[cache_key] = filtered
        return filtered

    def _resolve_financial_scalar(
        self, resolution: FieldResolution,
    ) -> Decimal | None:
        """가장 최근 fiscal_period 의 active value (latest_active_by_key 통과 후)."""
        assert resolution.account is not None and resolution.ifrs_type is not None
        records = self._fetch_financial_periods(
            resolution.account, resolution.ifrs_type,
        )
        if not records:
            return None
        # fiscal_period asc 정렬됐으므로 마지막이 가장 최근 분기.
        value = records[-1].value
        # magnitude field(dividends_paid_annual 등) 는 부호가 의미와 무관 — 절댓값
        # 정규화. 현금흐름표 배당금 지급의 음수(현금유출) 보고가 dividend-yield 를
        # 음수로 만드는 §2.1 거울 왜곡 차단(conformance M2.1 이슈 1).
        return abs(value) if resolution.magnitude else value

    def _resolve_financial_period_begin(
        self, resolution: FieldResolution,
    ) -> Decimal | None:
        """해당 분기 시작 직전 (= 직전 분기말) 의 active value.

        ROE 평균자본의 분기초 자본 — 가장 최근 분기 (period_end) 의 직전 분기
        값 (period_begin). 직전 분기가 없으면 N/A (ROE TTM 산출에 4 분기 +
        직전 1 분기 필요).
        """
        assert resolution.account is not None and resolution.ifrs_type is not None
        records = self._fetch_financial_periods(
            resolution.account, resolution.ifrs_type,
        )
        if len(records) < 2:
            # 가장 최근 분기 (period_end) 의 직전 분기 (period_begin) 부재.
            return None
        # records[-1] = 최근 분기말. records[-2] = 그 직전 분기말 = 분기초 자본.
        return records[-2].value

    def _resolve_financial_annual(
        self, resolution: FieldResolution,
    ) -> Decimal | None:
        """trailing-annual scalar — 최근 4 분기 strict 합. 4 개 미만이면 N/A.

        `_annual` suffix field 의 의미 (work order) — trailing 4 분기 합. strict
        series 와 동일 의미를 scalar 로 노출 (formula 가 sum_last_n_quarters 가
        아닌 단일 field 참조). 정확히 4 개 분기가 있어야 산출.
        """
        assert resolution.account is not None and resolution.ifrs_type is not None
        series = self._resolve_financial_series(resolution, n=4)
        if not series:
            return None
        return sum(series, Decimal(0))

    def _resolve_financial_series(
        self, resolution: FieldResolution, *, n: int,
    ) -> tuple[Decimal, ...]:
        """최근 n 분기 strict series (fiscal_period asc). 정확히 n 개 또는 빈 tuple.

        strict 의미 — fiscal_period asc 정렬 후 마지막 n 개. n 개 미만이면 빈
        tuple (`factor_evaluator.py:137` 의 부분 series 금지 정책).

        **B1 (ROADMAP_v2 V1b)**: account 가 `_FLOW_CUMULATIVE_ACCOUNTS`(손익/현금흐름
        누적 flow)면 각 분기의 누적(YTD)값을 **분기단독(standalone)** 으로 변환 후
        반환한다. 변환 = 해당 분기 누적 − 직전 분기(같은 회계연도) 누적. 1분기는
        누적=3M=standalone(직전 없음). 변환에 필요한 직전 분기가 결손이면 standalone
        산출 불가 → strict N/A(빈 tuple). 재무상태표(stock) 계정은 시점 잔액이라
        미변환(기존 동작). 누적을 안 고치면 TTM 합이 ~2.5배 과대(EPS 4144 vs 2900).
        """
        assert resolution.account is not None and resolution.ifrs_type is not None
        records = self._fetch_financial_periods(
            resolution.account, resolution.ifrs_type,
        )
        if len(records) < n:
            return ()

        if resolution.account not in _FLOW_CUMULATIVE_ACCOUNTS:
            # stock(잔액) 또는 비-누적 계정 — 시점값 그대로(기존 동작).
            return tuple(r.value for r in records[-n:])

        # flow(누적) — fetch 된 전 분기로 (year, quarter)→누적 map 을 만들고, 최근 n
        # 분기 각각을 standalone 으로 변환. 직전 분기(q>1) 결손이면 strict N/A.
        try:
            cum_by_period: dict[tuple[int, int], Decimal] = {
                _parse_fiscal_period(r.fiscal_period): r.value for r in records
            }
        except ValueError:
            # 비표준 fiscal_period 포맷 — 변환 불가, 보수적 N/A.
            return ()

        standalones: list[Decimal] = []
        for r in records[-n:]:
            year, quarter = _parse_fiscal_period(r.fiscal_period)
            if quarter == 1:
                # 1분기 누적(3M) = standalone — 직전 분기 차감 불필요.
                standalones.append(r.value)
                continue
            prior = cum_by_period.get((year, quarter - 1))
            if prior is None:
                # 직전 분기 누적 결손 → 이 분기 standalone 산출 불가 → 부분 series 금지.
                return ()
            standalones.append(r.value - prior)
        return tuple(standalones)

    # ---------------------------------------------------------------------
    # 가격 해소 — close_price_adjusted (as_of-aware read-time 보정)
    # ---------------------------------------------------------------------

    def _resolve_close_price_adjusted(self) -> Decimal | None:
        """as_of 시점 (직전 영업일) 의 보정 종가.

        ADR-0001 의 read-time 보정 — DB 의 `close_adjusted` 컬럼은 batch 시점
        보정이라 as_of 와 무관하게 미래 사건까지 반영됐을 수 있음. PIT 정합을
        위해 PriceAdjuster 로 as_of 범위 corporate action 만 누적 보정.

        직전 영업일의 종가 (as_of 범위 최신 PriceRecord) 의 보정값 반환. 보정
        factor 는 "그 일자 이후 사건" 의 곱이므로, 최신 일자의 factor 는 항상 1
        (as_of 범위에 그 이후 사건 없음) → 사실상 raw 최신 종가. 그러나 PriceAdjuster
        경유로 PIT / 정책 hash 의미론 일관 유지.
        """
        if self._adjusted_close_computed:
            return self._adjusted_close

        self._adjusted_close_computed = True
        start = self.as_of - timedelta(days=_PRICE_LOOKBACK_DAYS)
        prices = self._price_repo.fetch_prices(
            self._code, as_of=self.as_of, start=start,
            batch_cutoff=self._krx_batch_cutoff,
        )
        if not prices:
            self._adjusted_close = None
            return None

        actions = ()
        if self._ca_repo is not None:
            actions = tuple(
                self._ca_repo.fetch_actions(self._code, as_of=self.as_of)
            )

        try:
            series = self._adjuster.adjust(prices, actions, as_of=self.as_of)
        except AdjusterError:
            # 보정 데이터 invariant 위반 (알 수 없는 action_type / 이론가 불일치
            # 등) — PIT 정합 종가를 산출할 수 없으므로 정식 N/A. fail-soft.
            self._adjusted_close = None
            return None

        if not series.adjusted:
            self._adjusted_close = None
            return None
        # adjusted 는 effective_date asc — 마지막 = as_of 직전 영업일의 보정 종가.
        self._adjusted_close = series.adjusted[-1].close_adjusted
        return self._adjusted_close

    # ---------------------------------------------------------------------
    # 거래대금 해소 — trading_value_20d_avg (KRX 영업일 window)
    # ---------------------------------------------------------------------

    def _resolve_trading_value_avg(self) -> Decimal | None:
        """최근 20 KRX 영업일의 `trading_value` 평균.

        factor pack 의 `trading_value_20d_avg` (volume-turnover liquidity) 입력.

        설계 (m1-milestone.md T51):
        - **KRX 캘린더 기준 정확히 20 영업일** — 단순 직전 20 row 가 아닌 휴장일
          제외 영업일. as_of 이하 가장 최근 영업일부터 역방향 20 영업일 수집.
        - **strict** — 20 영업일 중 하나라도 가격 결손 (거래정지 / 상장 20 영업일
          미만 / 데이터 누락) 이면 N/A. 부분 평균 금지 (`factor_evaluator.py:137`
          의 strict series 정책과 동일 — silent regression 방지).
        - **raw trading_value** — 거래대금은 금액 (원) 이라 가격 보정 (ADR-0001)
          대상 아님. PriceRecord.trading_value 를 그대로 사용.
        - 캘린더 verified 범위 (v1.0 = 2024 단년, ADR-0008 D8 한계) 밖이면 N/A.
        """
        cal = self._calendar
        # 1. as_of 이하 최근 영업일부터 역방향으로 정확히 20 영업일 수집.
        try:
            cursor = cal.latest_business_day(self.as_of)
            window_days = [cursor]
            for _ in range(_TRADING_VALUE_WINDOW - 1):
                cursor = cal.previous_business_day(cursor)
                window_days.append(cursor)
        except CalendarRangeError:
            # verified 범위 밖 — 20 영업일 window 산출 불가. fail-soft N/A.
            return None
        window_days.sort()  # 오름차순 — window_days[0] = 가장 이른 영업일.

        # 2. window 범위 가격 fetch 후 effective_date → trading_value 매핑.
        prices = self._price_repo.fetch_prices(
            self._code, as_of=self.as_of, start=window_days[0],
            batch_cutoff=self._krx_batch_cutoff,
        )
        by_date = {p.effective_date: p.trading_value for p in prices}

        # 3. 20 영업일 모두 가격 존재해야 — 하나라도 결손이면 strict N/A.
        total = Decimal(0)
        for business_day in window_days:
            tv = by_date.get(business_day)
            if tv is None:
                return None
            total += tv
        return total / Decimal(_TRADING_VALUE_WINDOW)

    # ---------------------------------------------------------------------
    # 시가총액 / 발행주식수 해소 — market_caps 영구화 (Phase B)
    # ---------------------------------------------------------------------

    def _fetch_market_cap(self) -> MarketCapRecord | None:
        """`effective_date <= as_of` 중 최신 market_cap record (캐싱).

        market_cap / shares 두 field 가 같은 fetch_latest 결과를 공유 — 1 회만
        fetch. repo 미주입 (None) 이면 정식 N/A (None).
        """
        if self._market_cap_fetched:
            return self._market_cap_record

        self._market_cap_fetched = True
        if self._market_cap_repo is None:
            self._market_cap_record = None
            return None
        self._market_cap_record = self._market_cap_repo.fetch_latest(
            self._code, as_of=self.as_of,
            batch_cutoff=self._krx_batch_cutoff,
        )
        return self._market_cap_record

    def _resolve_market_cap(self) -> Decimal | None:
        """KRX 공식 시가총액 (market_caps.market_cap, PIT effective_date<=as_of)."""
        record = self._fetch_market_cap()
        if record is None:
            return None
        return record.market_cap

    def _resolve_shares_outstanding(self) -> Decimal | None:
        """발행주식수 (market_caps.shares_outstanding, 자사주 포함).

        DB 는 int (BigInteger) 이나 FieldProvider 의 scalar contract 는 Decimal —
        Decimal 로 변환하여 반환 (factor formula 의 산술 일관성).
        """
        record = self._fetch_market_cap()
        if record is None:
            return None
        return Decimal(record.shares_outstanding)

    # ---------------------------------------------------------------------
    # 자사주 해소 — treasury_shares 영구화 (DART stockTotqySttus)
    # ---------------------------------------------------------------------

    def _resolve_shares_treasury(self) -> Decimal | None:
        """보통주 자기주식수 (treasury_shares.shares_treasury, PIT 정정 chain).

        TreasurySharesRepository.fetch_latest_active 가 정정공시 chain 을
        as_of-time 해소한 뒤 최신 fiscal_period 의 자사주를 반환. repo 미주입
        (None) 또는 결측 시 정식 N/A (None). DB 는 int (BigInteger) 이나 scalar
        contract 는 Decimal — Decimal 로 변환 (factor 산술 일관성). 결과 캐싱
        (sentinel 로 미계산/None 구별).

        본 field 가 실값을 반환하면 factor `market-cap:ex-treasury` 의 primitive
        입력 (shares_issued - shares_treasury) 이 모두 충족돼 evaluator 가 자동
        실평가 (provider 변경 불필요 — 파생 composite 메커니즘 아님).
        """
        if self._treasury_fetched:
            return self._treasury_value

        self._treasury_fetched = True
        if self._treasury_repo is None:
            self._treasury_value = None
            return None
        record = self._treasury_repo.fetch_latest_active(
            self._code, as_of=self.as_of,
            batch_cutoff=self._dart_batch_cutoff,
        )
        if record is None:
            self._treasury_value = None
            return None
        self._treasury_value = Decimal(record.shares_treasury)
        return self._treasury_value

    # ---------------------------------------------------------------------
    # ECOS 매크로 지표 해소 — macro_indicator (vintage PIT, M2 T81)
    # ---------------------------------------------------------------------

    def _resolve_macro_indicator(
        self, resolution: FieldResolution,
    ) -> Decimal | None:
        """ECOS 매크로 지표 — vintage 이중 시간축 PIT 조회.

        MacroIndicatorRepository.fetch_latest(indicator_id, as_of=self.as_of) 가
        vintage 이중 시간축 PIT 규약(reference_date<=as_of AND vintage_date<=as_of
        중 최신) 을 강제 — look-ahead 0, 잠정→확정 재현 보장 (AC-M1-P-05).

        macro_repo 미주입(None) 이면 정식 N/A(None). 기능 미동작이나 silent
        regression 아님 — 이 field 를 쓰는 factor 가 있으면 evaluator 가
        missing_input:<field> N/A 로 표시되어 인지 가능. 결과는 get_scalar 의
        _scalar_cache 로 1 회만 fetch (request-scoped 캐싱).
        """
        # macro_repo 미주입 — 정식 N/A (비-stocks 컨텍스트 / 테스트 호환).
        if self._macro_repo is None:
            return None
        assert resolution.indicator_id is not None
        # vintage PIT: fetch_latest 가 reference_date + vintage_date <= as_of 강제.
        record = self._macro_repo.fetch_latest(
            resolution.indicator_id, as_of=self.as_of,
        )
        if record is None:
            return None
        return record.value

    # ---------------------------------------------------------------------
    # 배당 / 총수익률 해소 — dividend / total return (M7 #4, ADR-0035)
    # ---------------------------------------------------------------------

    def _resolve_dividend_per_share_annual(self) -> Decimal | None:
        """직전 12 개월 cash_dividend per_share 합 (이중 PIT) — 주당배당 trailing-annual.

        DividendRepository.fetch_dividends 가 이중 PIT(announced<=as_of AND
        effective<=as_of, supersede chain 해소) cash_dividend 를 반환. 그 중 배당락일
        (effective_date) 이 **직전 12 개월**(as_of - 365 일 < effective_date <= as_of)
        인 배당의 per_share 를 Decimal 합산. per_share 는 TotalReturnAdjuster 와 동일
        coalesce — cash_amount(Decimal) 우선, 없으면 details["per_share"] 파싱(#3 패턴
        일관). 음수·파싱 실패 per_share 는 skip(데이터 오류 / silent 오보정 회피).

        dividend_repo 미주입(None) 이면 정식 N/A(None). v1.0.0 의
        dividend-yield:trailing-annual factor AST(ratio_pct(이 field,
        close_price_adjusted))가 이 field 를 그대로 소비 — formula 무변경.
        """
        if self._dividend_repo is None:
            return None

        dividends = self._dividend_repo.fetch_dividends(
            self._code, as_of=self.as_of,
        )
        window_start = self.as_of - timedelta(days=_TRAILING_ANNUAL_DAYS)
        total = Decimal(0)
        found = False
        for record in dividends:
            ex_date = record.effective_date
            # 직전 12 개월: window_start < effective_date <= as_of (effective<=as_of 는
            # fetch_dividends 이미 보장이나 방어적 재확인).
            if ex_date <= window_start or ex_date > self.as_of:
                continue
            per_share = self._dividend_amount(record)
            if per_share is None:
                continue
            total += per_share
            found = True
        # 직전 12 개월 배당이 하나도 없으면 정식 N/A (배당 미지급 종목 — 0 이 아닌
        # N/A: 데이터 결손과 "배당 0" 의 §2.1 구별. dividend-yield 분자 N/A → factor N/A).
        if not found:
            return None
        return total

    @staticmethod
    def _dividend_amount(record: Any) -> Decimal | None:
        """단일 배당 record 의 per_share — cash_amount(Decimal) coalesce details (#3 패턴).

        TotalReturnAdjuster._dividend_per_share 와 동일 coalesce 의미 (cash_amount
        우선, 없으면 details["per_share"] 파싱, 음수·파싱실패 skip) — total return 과
        주당배당 집계가 같은 배당 해석을 공유(divergence 0). 단 본 헬퍼는 warning
        누적이 아닌 silent skip (배당수익률 분자 집계는 경고 채널 없음).
        """
        amount = record.cash_amount
        if amount is None:
            raw = record.details.get("per_share")
            if raw is None:
                return None
            try:
                amount = _as_decimal(raw)
            except AdjusterError:
                return None
        # 음수 per_share 는 데이터 오류 / 자본감소 오분류 — skip (silent 오보정 회피).
        if amount < 0:
            return None
        return amount

    def _resolve_total_return_trailing_1y(self) -> Decimal | None:
        """직전 1 년 총수익률 ratio (배당 재투자 포함) — total_return_trailing_1y.

        직전 1 년(as_of - 365 일 ~ as_of) 보정 종가 시계열을 PriceAdjuster 로
        as_of-aware 보정한 뒤 TotalReturnAdjuster.compute 로 배당 재투자 chaining
        total return index 를 산출. `series[0].total_return_index == 1.0`(첫 거래일
        기준) 이므로 `series[-1].total_return_index - 1` = 1 년 누적 총수익률 ratio.

        **가격 history ≥ ~1 년 필요(M7 #4 fix3, §2.1)** — total return 시계열의 첫
        거래일이 as_of 로부터 `_TRAILING_ANNUAL_MIN_COVERAGE_DAYS`(340 일) 이내이면
        직전 1 년을 cover 하지 못한 것(상장 1 년 미만 / 가격 결손)이므로 N/A.
        ~3 개월짜리 window 를 "1 년 총수익률"로 라벨하는 데이터 의존 lookback(§2.1
        Fidelity 위반)을 차단 — _resolve_financial_annual(strict 4 분기)·
        _resolve_trading_value_avg(strict 20 영업일)의 strict-or-N/A 패턴 일관.

        field 는 다른 raw field 와 일관되게 **ratio**(소수) 반환 — v1.1.0
        price-return:total-annual factor 가 unit=percent 로 표현(ratio_pct/
        dividend-yield 와 동일하게 ×100 은 표시 layer 책임, ADR-0002 D6).

        dividend_repo 또는 total_return_adjuster 미주입 시 정식 N/A(None). 빈 시계열
        (가격 결손)·1 년 미만 history·보정 실패도 N/A. 결과 sentinel 캐싱
        (_adjusted_close 패턴).
        """
        if self._total_return_computed:
            return self._total_return_value

        self._total_return_computed = True
        if self._dividend_repo is None or self._total_return_adjuster is None:
            self._total_return_value = None
            return None

        start = self.as_of - timedelta(days=_TRAILING_ANNUAL_DAYS)
        prices = self._price_repo.fetch_prices(
            self._code, as_of=self.as_of, start=start,
            batch_cutoff=self._krx_batch_cutoff,
        )
        if not prices:
            self._total_return_value = None
            return None

        actions = ()
        if self._ca_repo is not None:
            actions = tuple(
                self._ca_repo.fetch_actions(self._code, as_of=self.as_of)
            )
        dividends = tuple(
            self._dividend_repo.fetch_dividends(self._code, as_of=self.as_of)
        )

        try:
            adjusted = self._adjuster.adjust(prices, actions, as_of=self.as_of)
            series = self._total_return_adjuster.compute(adjusted, dividends)
        except AdjusterError:
            # 보정 / total return invariant 위반(알 수 없는 action_type / 0 division
            # 등) — PIT 정합 총수익률 산출 불가, 정식 N/A. fail-soft (다른 resolver
            # 의 AdjusterError 처리와 동일 의미론).
            self._total_return_value = None
            return None

        if not series.series:
            self._total_return_value = None
            return None
        # M7 #4 fix3 (§2.1) — min-coverage guard. series[0] 은 fetch window 의 첫
        # 거래일. 상장 1 년 미만이면 window 가 ~수개월짜리인데 이를 "1 년 총수익률"
        # 로 라벨하는 것은 데이터 의존 lookback(§2.1 위반). 첫 거래일이 1 년 전
        # 근방(340 일)보다 늦으면 1 년 history 부재 → N/A(strict-or-N/A 패턴 일관).
        min_coverage_date = self.as_of - timedelta(
            days=_TRAILING_ANNUAL_MIN_COVERAGE_DAYS,
        )
        if series.series[0].date > min_coverage_date:
            self._total_return_value = None
            return None
        # series[0].total_return_index == 1.0 (첫 거래일 기준) → 마지막 - 1 = 1 년
        # 누적 총수익률 ratio.
        self._total_return_value = series.series[-1].total_return_index - Decimal(1)
        return self._total_return_value

    # ---------------------------------------------------------------------
    # 파생 필드 해소 — derived_factor (대응 factor 를 evaluator 로 평가, Option B)
    # ---------------------------------------------------------------------

    def _resolve_derived_factor(
        self, resolution: FieldResolution,
    ) -> Decimal | None:
        """파생 필드 — 대응 factor 를 FactorEvaluator 로 평가한 결과값.

        `market_cap_ex_treasury` 처럼 primitive DB field 가 아니라 다른 factor
        (`target_factor` canonical_id) 의 출력을 가리키는 field 의 해소.
        provider 가 composite 공식을 하드코딩하지 않고 (Fidelity — 표시값 =
        명시 공식, 단일 출처; 8 기둥 §2.1) 대응 factor 의 formula AST 를 self 를
        FieldProvider 로 하여 재귀 평가. 따라서 본 field 값은
        `evaluate(target_factor)` 값과 항상 동일 (pack 공식 변경 divergence 0).

        N/A 의미론 (`factor_evaluator.py:16-19` 의 pack 무결성 vs 데이터 결손
        이분법):
        - pack/evaluator 미주입 → 정식 N/A (None). 비-stocks 컨텍스트 / 테스트
          호환 (Phase B repo 미주입과 동일 패턴 — 데이터 일상의 N/A).
        - **순환 (cycle)** → `UnknownFieldError` raise. target_factor 의 AST 가
          (직·간접으로) 자기 자신을 field 로 다시 참조하면 pack 무결성 위반이며,
          데이터 N/A 가 아닌 pack 결함이므로 fail-loud (Sentry alert 대상).
        - pack 에 target_factor canonical_id 없음 → `UnknownFieldError`
          (registry / pack drift — `get_scalar` 의 unknown field 와 동일 정책).
        - target_factor 평가 결과가 N/A (is_na) → None 반환 (N/A 자연 전파 —
          예: shares_treasury 결측 → market_cap:ex-treasury N/A → 본 field N/A).

        결과는 호출자 `get_scalar` 의 `_scalar_cache` 로 1 회만 계산 후 재사용 —
        PER + PBR 이 같은 `market_cap_ex_treasury` 를 공유해도 sub-factor 평가
        1 회 (request-scoped 캐싱).
        """
        # pack/evaluator 미주입 — 정식 N/A (비-stocks 컨텍스트 / 테스트 호환).
        if self._factor_pack is None or self._evaluator is None:
            return None

        field = resolution.field
        assert resolution.target_factor is not None
        target = resolution.target_factor

        # 순환 탐지 — 본 field 가 이미 해소 진행 중이면 target_factor 의 AST 가
        # 자기 자신을 다시 참조하는 cycle. pack 무결성 위반 (fail-loud).
        if field in self._resolving:
            raise UnknownFieldError(
                f"derived field '{field}' cycle 탐지 — target factor "
                f"'{target}' 의 AST 가 자기 자신을 (직·간접) 참조 "
                f"(factor pack 무결성 위반)"
            )

        factor = self._factor_by_id(target)
        if factor is None:
            raise UnknownFieldError(
                f"derived field '{field}' 의 target factor '{target}' 가 "
                f"factor pack 에 없음 (레지스트리 / pack drift)"
            )

        self._resolving.add(field)
        try:
            result = self._evaluator.evaluate(factor, self, as_of=self.as_of)
        finally:
            self._resolving.discard(field)
        # is_na 면 result.value 는 None — N/A 자연 전파.
        return result.value

    def _factor_by_id(self, canonical_id: str) -> dict | None:
        """factor pack body 에서 canonical_id 로 factor dict 조회 (1 회 인덱싱).

        `factor_pack.LoadedPack.body["factors"]` 는 factor dict 리스트
        (stocks.py `_evaluate_display_factors` 의 `factors_by_id` 패턴 재사용).
        """
        if self._factors_by_id is None:
            factors = self._factor_pack.body["factors"]
            self._factors_by_id = {f["canonical_id"]: f for f in factors}
        return self._factors_by_id.get(canonical_id)


# field-resolution 레지스트리 외부 노출 (테스트 / 문서) — read-only 의미.
FIELD_RESOLUTIONS: Final[dict[str, FieldResolution]] = dict(_RESOLUTIONS)
