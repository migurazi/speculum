"""Adapter base — ABC + FetchResult + canonical schemas + 에러 계층.

ADR-0003 D1~D8 의 implementation. 모든 외부 출처 adapter 는 본 base 의 ABC 를
상속. service / repository 는 canonical schema 만 인식.

설계 (oracle T13/T15 자문 일관):

1. **frozen dataclass + slots** — codebase 의 SourceCitation / PriceRecord /
   FactorPack 등 모든 운영 entity 패턴 일관. Pydantic 거부 (운영 모델 layer).
2. **adapter = sync** — codebase 의 sync repository 정책 일관. ADR-0003 D1 의
   async 권고는 M1+ cycle backlog.
3. **`FetchResult` generic** — adapter 가 데이터 + citation list + warning list
   를 묶어 반환. warning 은 비치명적 데이터 이슈 (예: 일부 row 결측, schema
   drift).
4. **canonical schema 의 단위 통일**:
   - 가격 = Decimal (Float 거부 — 누적 오차).
   - 거래량 = int (KRX 단위 = 주).
   - date = naive `date` (KST 가정, ADR-0008 D8 의 일 단위 PIT).
   - tz-aware datetime 은 UTC offset=0 강제 (SourceCitation 패턴 일관).

관련 ADR:
- ADR-0003 D1 (ABC), D2 (canonical), D3 (citation), D4 (충돌), D5 (semver), D8 (fixture test)
- ADR-0002 D3 (Source Citation 7-tuple)
- ADR-0001 (가격 raw/adjusted)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Generic, TypeVar

from app.models.source_citation import SourceCitation

__all__ = [
    "AdapterError",
    "AdapterRetryError",
    "DataSourceAdapter",
    "FetchResult",
    "FinancialStatementRow",
    "IfrsType",
    "KrxCalendarDay",
    "MacroIndicatorRow",
    "MarketCapRow",
    "NavRow",
    "OHLCVRow",
    "StockMaster",
]


# =============================================================================
# Errors
# =============================================================================

class AdapterError(Exception):
    """Adapter base exception.

    하위 fetch 메서드가 raise 하는 모든 예외의 base. 호출자 (T18 일배치) 가
    fail-fast / circuit breaker / retry 정책에 따라 분기.
    """


class AdapterRetryError(AdapterError):
    """일시적 실패 — 재시도 권장.

    예: rate limit 초과, 네트워크 일시 단절. ADR-0003 D6 의 exponential
    backoff path.
    """


# =============================================================================
# Canonical schemas — frozen dataclass (codebase 일관)
# =============================================================================

@dataclass(frozen=True, slots=True)
class OHLCVRow:
    """일일 OHLCV — ADR-0003 D2 의 canonical 가격 schema.

    Attributes:
        code: KRX 종목코드 (6자리 zero-padded).
        trade_date: 거래일 (KST naive date). PIT 의 `effective_date` 와 동치.
        open: 시가.
        high: 고가.
        low: 저가.
        close: 종가 (raw — corporate action 보정 전).
        volume: 거래량 (주).
        value: 거래대금 (원). pykrx 의 `거래대금` 컬럼.

    Notes:
        adjusted close 는 본 schema 에 포함 X — corporate action 보정 엔진
        (T20) 의 별도 산출물. raw close 만 1차 자료로 저장 (ADR-0001 D1).
    """

    code: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    value: Decimal


@dataclass(frozen=True, slots=True)
class MarketCapRow:
    """일일 시가총액 + 발행주식수 — ADR-0003 D2 + ADR-0004 (시가총액 정의).

    Attributes:
        code: KRX 종목코드.
        trade_date: 기준일 (KST naive date).
        market_cap: 시가총액 (원).
        shares_outstanding: 발행주식수 (자사주 포함).
        shares_treasury: 자사주 수 (pykrx 미지원 시 None — DART 보강 필요).
    """

    code: str
    trade_date: date
    market_cap: Decimal
    shares_outstanding: int
    shares_treasury: int | None


@dataclass(frozen=True, slots=True)
class NavRow:
    """ETF 일별 NAV · 시장가 · 괴리율 · AUM — ADR-0023 D3-a.

    KRX 의 ETF 가격 데이터 (pykrx `get_etf_ohlcv_by_date`) + 괴리율
    (`get_etf_price_deviation`) 의 adapter canonical 표현. canonical_id /
    factor pack 미등록 — R4 deferred (데이터 영구화 전 등록 금지, ADR-0023 D8).

    Attributes:
        code: KRX ETF 종목코드 (6자리 zero-padded).
        trade_date: 기준일 (KST naive date).
        nav: ETF 순자산가치 (NAV, 원). KRX 공시 기준.
        market_price: ETF 시장 종가 (원).
        premium_discount_rate: 괴리율 = (market_price - nav) / nav.
            양수 = 프리미엄, 음수 = 디스카운트.
        aum: 순자산총액 (원). pykrx 미제공 시 None.

    Notes:
        canonical_id 미등록 상태 — R4 (데이터 영구화 cycle) 진입 전까지
        SqlNavRepository / ORM / migration / 일배치 wiring 금지.
    """

    code: str
    trade_date: date
    nav: Decimal
    market_price: Decimal
    premium_discount_rate: Decimal
    aum: Decimal | None


@dataclass(frozen=True, slots=True)
class StockMaster:
    """KRX 종목 마스터 — ADR-0003 D2 + ADR-0009 D6.

    Attributes:
        code: 현재 KRX 종목코드.
        name: 한글 종목명.
        market: "KOSPI" | "KOSDAQ" | "KONEX".
        listing_date: 최초 상장일 (KST naive date). None = pykrx 미제공.
        delisting_date: 폐지일. None = 활성.
        sector_krx: KRX 표준 업종분류 (e.g., "전기전자"). None = 미분류.

    Notes:
        fiscal_month / ifrs_preference 는 DART 의존 정보 — pykrx adapter 는 채울
        수 없음. T16 (DART adapter) 가 보강. stocks_master DB row 의 lineage
        구성은 T18 일배치가 multi-source merge.
    """

    code: str
    name: str
    market: str
    listing_date: date | None
    delisting_date: date | None
    sector_krx: str | None


class IfrsType(str, Enum):
    """K-IFRS 재무제표 분류 — ADR-0005 D1.

    str-mixin — JSON 직렬화 시 enum value 자연 변환 ("CFS" / "OFS").
    DB schema 의 `financials.ifrs_type` 컬럼은 string ("consolidated" /
    "separate") 와 매핑 — 본 enum 의 의미적 alias 이나 wire format 은 enum
    value 보존.
    """

    CFS = "consolidated"  # 연결재무제표 (ADR-0005 1순위).
    OFS = "separate"      # 별도재무제표.


@dataclass(frozen=True, slots=True)
class FinancialStatementRow:
    """DART 재무제표 단일 account row — ADR-0003 D2 + ADR-0005 D2.

    Attributes:
        code: KRX 종목코드 (6자리). DART 의 corp_code 가 아닌 KRX stock code
            — adapter 가 conversion 후 채움.
        fiscal_year: 사업연도 (예: 2023).
        fiscal_quarter: 분기 (1~4). DART reprt_code 매핑:
            Q=1 → "11013" (1분기보고서), Q=2 → "11012" (반기보고서),
            Q=3 → "11014" (3분기보고서), Q=4 → "11011" (사업보고서 = 연간).
        effective_date: 공시 효력일. PIT 의 1차 키. ADR-0012 D6 — rcept_no 의
            앞 8자리 (YYYYMMDD = DART 접수일자) 에서 직접 도출한 정밀 공시일.
            도출 실패 (schema drift 등) 시 자본시장법 제160조 신고기한 보수값
            fallback (`effective_date_precise = False`).
        effective_date_precise: True 면 effective_date 가 rcept_no 도출 실
            공시일 (정밀). False 면 신고기한 보수 추정값 (look-ahead 0 보장).
        account: dart_account_mapper 정규화된 canonical key
            (e.g., "total_assets", "net_income_consolidated").
        value: 금액 (Decimal). DART 의 thstrm_amount string 변환.
        unit: "krw" (default — 한국 회계기준 단위 원).
        ifrs_type: CFS / OFS.
        rcept_no: DART 접수번호 (14자리). identifier.
        currency: 통화 — "KRW" 기본. 일부 외국통화 표시 종목 가능.
    """

    code: str
    fiscal_year: int
    fiscal_quarter: int  # 0 = 사업보고서 / 1~4 = 분기.
    effective_date: date
    account: str
    value: Decimal
    unit: str
    ifrs_type: IfrsType
    rcept_no: str
    currency: str
    effective_date_precise: bool


@dataclass(frozen=True, slots=True)
class MacroIndicatorRow:
    """ECOS 거시지표 단일 관측값 — ADR-0003 D2 canonical schema.

    EcosAdapter.fetch_statistic 의 반환 단위. MacroIndicatorRecord (DB) 와 필드
    정합을 맞춰 배치(T64)가 Row → Record 변환 시 추가 파싱 없이 1:1 복사.

    Attributes:
        indicator_id: ECOS 통계표코드 + 항목코드 조합 (예: "722Y001/0101000").
            T62 MacroIndicatorRecord.indicator_id 와 동일 규약. EcosAdapter 가
            STAT_CODE 와 ITEM_CODE1 을 슬래시로 결합하여 생성.
        reference_date: 지표의 기준 시점 (KST naive date). ECOS TIME 필드를
            주기(CYCLE)별로 파싱:
            - D (일별): YYYYMMDD → 해당일.
            - M (월별): YYYYMM → 해당 월 1일.
            - Q (분기별): YYYYQ(5자리) → 분기 시작월 1일 (Q1=01, Q2=04, Q3=07, Q4=10).
            - A (연간): YYYY → 해당 연도 1월 1일.
            - S (반기): YYYYS(6자리) → S1=1월 1일, S2=7월 1일.
        value: 지표 값 (Decimal). ECOS DATA_VALUE 문자열을 Decimal 로 변환.
            빈 string / null 은 결측으로 간주하여 row 자체를 skip (절대 0 가정 금지).
        unit: 지표 단위 (ECOS UNIT_NAME 원문, 예: "연%", "지수").
        vintage_date: **관측 시점 근사** — ECOS API 는 공표일/개정일 필드를 제공하지
            않는다. 항상 최신값만 반환하기 때문에, batch fetch 일자(observed_date)를
            "우리가 이 값을 관측한 시점" 으로 vintage_date 에 주입한다. 정확한 한국은행
            공표일이 아닌 관측 근사임에 주의 (T64 표시 시 disclaimer 대상). 배치 간
            같은 reference_date 의 값이 바뀌면 새 vintage row 가 누적된다.

    Notes:
        indicator_id 조합 helper: `make_indicator_id(stat_code, item_code)`.
        vintage = 관측 근사이므로 MacroIndicatorRecord 영속화 후 vintage_date
        기반 PIT 쿼리 결과는 "해당 시점 배치가 알고 있던 값" 이라는 해석이 정확.
    """

    indicator_id: str    # STAT_CODE + "/" + ITEM_CODE1
    reference_date: date  # TIME 파싱 결과 (주기별 date 정규화)
    value: Decimal       # DATA_VALUE 문자열 → Decimal
    unit: str            # UNIT_NAME 원문
    vintage_date: date   # 관측 시점 근사 (batch fetch 일자 주입)


@dataclass(frozen=True, slots=True)
class KrxCalendarDay:
    """KRX 영업일 — ADR-0003 D2 + T17 KRX 캘린더와 cross-check 용.

    Attributes:
        day: 일자 (KST naive date).
        is_trading_day: True 면 정규 거래일, False 면 휴장일.

    Notes:
        pykrx 의 `get_previous_business_day` 등은 영업일만 반환. 휴장일 list
        는 별도 derivation 필요. 본 schema 는 cross-check (T17 의 verified
        캘린더와 비교) 용.
    """

    day: date
    is_trading_day: bool


# =============================================================================
# FetchResult generic — adapter 의 반환 wrapper
# =============================================================================

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class FetchResult(Generic[T]):
    """Adapter fetch 의 반환 wrapper — data + citations + warnings + estimated_fields.

    Attributes:
        data: canonical schema 인스턴스 또는 Sequence (도메인별).
        citations: ADR-0002 D3 의 7-tuple list. fact 마다 1 개 또는 batch 전체에
            1 개 (adapter 정책). 빈 list 면 빌드 게이트 (T23) 가 차단.
        warnings: 비치명적 이슈 메시지 (예: "row 3 in DataFrame had NaN volume").
            호출자가 Sentry / log 로 기록.
        estimated_fields: canonical schema 의 필드 중 본 fetch 가 정밀값 대신
            추정값 (e.g., `close × volume`) 을 채운 필드 집합 (frozenset).
            T18 ConflictDetector 가 본 set 의 필드는 비교 대상에서 제외 — 출처
            간 의미론적 비대칭 (FDR 의 거래대금 추정 vs KRX 의 평균체결가) 으로
            인한 false-positive alert 차단.

    Notes:
        `citations` 의 batch_id 는 adapter 가 채우는 게 아니라 호출자 (일배치)
        가 주입. adapter 는 batch context 외부에서도 호출 가능하므로 batch_id
        는 메서드 parameter.
    """

    data: T
    citations: Sequence[SourceCitation]
    warnings: Sequence[str] = field(default_factory=tuple)
    estimated_fields: frozenset[str] = field(default_factory=frozenset)


# =============================================================================
# DataSourceAdapter ABC
# =============================================================================

class DataSourceAdapter(ABC):
    """모든 외부 출처 adapter 의 base — ADR-0003 D1.

    ClassVar:
        SOURCE_KIND: SourceKind enum value 와 일치 — "DART" / "KRX" / "FDR" /
            "PYKRX" / "ECOS" / "KOSIS". citation 의 source 채움.
        ADAPTER_VERSION: 본 adapter 코드의 semver. 코드 변경 시 bump 의무
            (ADR-0003 D5).

    각 구현체:
        - 자기 도메인의 fetch_* 메서드 정의 (signature 자유).
        - 각 fetch 가 `FetchResult` 반환.
        - 외부 호출 실패 시 `AdapterError` 또는 `AdapterRetryError` raise.
        - `health_check()` 가 외부 도달 가능성 boolean 반환.
    """

    SOURCE_KIND: str = ""
    ADAPTER_VERSION: str = "0.0.0"

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # subclass 가 SOURCE_KIND / ADAPTER_VERSION 누락 시 즉시 raise — silent
        # citation 의 잘못된 source value 차단.
        if not cls.SOURCE_KIND:
            raise TypeError(
                f"{cls.__name__} must define SOURCE_KIND (e.g., 'PYKRX')"
            )
        if cls.ADAPTER_VERSION == "0.0.0":
            raise TypeError(
                f"{cls.__name__} must define ADAPTER_VERSION (semver, "
                f"e.g., '1.0.0')"
            )

    @abstractmethod
    def health_check(self) -> bool:
        """외부 출처 도달 가능성 검사.

        Returns:
            True = 응답 정상, False = 외부 출처 도달 불가.

        Raises:
            AdapterError: 검사 자체 실패 (라이브러리 import 실패 등 영구적 사유).
        """
