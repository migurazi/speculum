"""PIT-aware Repository Protocol + 도메인 Record dataclass.

본 모듈은 T21 standalone 단계의 **Repository 외곽 경계**. Protocol 만 정의하고
실제 SQLAlchemy 구현체는 T13 합류 후. 모든 Protocol method 가 `as_of: date` 를
keyword-only required 로 받아 호출자가 PIT 의도를 type-level 로 명시하도록 강제
(oracle 자문 §1 — ORM-leaking 차단).

도메인별 PIT 의미론은 ADR 별로 미묘하게 다름:

| 도메인 | PIT semantics | ADR |
|---|---|---|
| Price | `trade_date <= as_of`. KRX 가격은 정정 안 됨 → supersede 개념 없음 | ADR-0001 |
| Financial | `effective_date <= as_of` + active-at-as_of (supersede chain) | ADR-0002 D3, D5 |
| CorporateAction | `announced_date <= as_of` + `effective_date <= as_of` 이중 PIT | ADR-0009 D5 |
| StockSnapshot | `as_of_date == as_of` exact match (일배치 precomputed) | ADR-0002 D5 |

이 차이는 generic `PITQueryable` 추상화로 묶지 않고 도메인별 Protocol 로 명시
(oracle §1 — false promise 회피).

관련 ADR:
- ADR-0008 D5 (PIT Enforcer 인터페이스), D6 (API contract), D8 (일 단위 한계)
- ADR-0002 D3 (Source Citation effective_date), D5 (stock_snapshots schema)
- ADR-0009 D1 (corporate_actions schema), D5 (supersede chain), D11 (stock_status)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable
from uuid import NAMESPACE_OID, UUID, uuid5

from app.repositories.batch_run_repository import BatchCutoff

# =============================================================================
# Common PIT Record Protocols
# =============================================================================

@runtime_checkable
class PITRecord(Protocol):
    """PIT 알고리즘이 의존하는 최소 메타 속성 — 4 도메인 공통.

    `created_at` 은 같은 `effective_date` 끼리의 tie-break key. 정정공시가 옛
    record 의 effective_date 를 가져갈 수 있는 회계기간 소급 정정 시 필수.
    """

    id: UUID
    effective_date: date
    created_at: datetime


@runtime_checkable
class SupersedableRecord(PITRecord, Protocol):
    """정정공시 chain 지원이 필요한 record — Financial / CorporateAction 만.

    Price (KRX 가격) 와 StockSnapshot (precomputed lookup) 은 supersede 무관.
    oracle 자문 §2 Variant A — 도메인 의미 불일치 회피.
    """

    superseded_by: UUID | None


# =============================================================================
# 도메인 Record dataclass — frozen + Protocol 자동 implements
# =============================================================================

@dataclass(frozen=True, slots=True)
class PriceRecord:
    """가격 시계열 단일 일자.

    ADR-0001 D1 — 수정주가는 별도 `close_adjusted` 컬럼. raw vs adjusted 토글 시
    호출자가 어느 컬럼을 쓸지 결정. 본 record 는 두 값 모두 보유.

    `effective_date` = `trade_date` (KRX 가격의 PIT 의미는 거래일).
    `created_at` = 일배치 retrieval 시점 — tie-break 사용 (정정 없으므로 의미 약함).
    """

    id: UUID
    code: str  # 종목코드 (사건 발생 시점 기준)
    code_lineage_id: UUID  # ADR-0009 D6 의 lineage
    effective_date: date  # = trade_date
    open_raw: Decimal
    high_raw: Decimal
    low_raw: Decimal
    close_raw: Decimal
    volume: int
    # 거래대금 (원) — Momus M0 review V3 fix. factor pack 의
    # `volume-turnover:avg-20d` 가 20 영업일 trading_value 평균 입력으로 사용.
    # FieldProvider wiring (factor_evaluator 합류) 은 M1 backlog —
    # 현 cycle 은 schema + batch 영구화 만.
    trading_value: Decimal
    close_adjusted: Decimal  # ADR-0001
    citation_id: UUID  # ADR-0002 D3
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MarketCapRecord:
    """일별 시가총액 + 발행주식수 — ADR-0003 D2 + ADR-0004.

    `MarketCapRow` (adapter canonical) 의 DB record 표현. KRX 시가총액은 정정
    안 됨 → supersede 없음 (PriceRecord 와 동일). `effective_date` = 기준일
    (KRX 거래일) — PriceRecord 와 동일 PIT 의미 (`effective_date <= as_of`).

    `shares_treasury` 는 pykrx 미제공 시 None — 절대 0 으로 가정하지 않음
    (silent 오류 회피, DART 보강 별도 cycle, T48c). `market_cap_ex_treasury`
    산출은 자사주가 채워질 때까지 불가.
    """

    id: UUID
    code: str  # 종목코드 (사건 발생 시점 기준)
    code_lineage_id: UUID  # ADR-0009 D6 의 lineage
    effective_date: date  # = 기준일 (KRX 거래일)
    market_cap: Decimal  # 시가총액 (원)
    shares_outstanding: int  # 발행주식수 (자사주 포함)
    shares_treasury: int | None  # 자사주 수 (pykrx 미제공 시 None)
    citation_id: UUID  # ADR-0002 D3
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FinancialRecord:
    """재무제표 단일 항목 (account 별 row).

    `effective_date` 의 의미 (ADR-0012 D7 명시):
        - KRX 가격: trade_date (정확).
        - DART 재무제표: 본 record 가 시장에 100% 가용해진 시점. M1 정밀화 —
          DART 응답 각 row 의 `rcept_no` (14자리 접수번호) 앞 8자리 (YYYYMMDD =
          접수일자 = 공시일) 에서 직접 도출 (ADR-0012 D6). 도출 실패 시 자본시장법
          제160조 신고기한 (Q1~Q3 = +45일, Q4 = +90일) 보수값 fallback (ADR-0012
          D1). 어느 경우든 look-ahead 0.

    `effective_date_precise`:
        - True → effective_date 가 rcept_no 도출 실 공시일 (정밀, ADR-0012 D6).
        - False → 신고기한 보수 추정값 (구 `estimated_fields={"effective_date"}`
          marker 를 대체하는 영구 컬럼).
    정정공시 시 새 row + 옛 row 의 `superseded_by` = 새 row.id.
    """

    id: UUID
    code: str
    code_lineage_id: UUID
    effective_date: date
    # fiscal_period: T19 DART 일배치 표준 = `f"{year}Q{quarter}"` (예: "2024Q1",
    # "2024Q2", "2024Q3", "2024Q4"). Q4 = DART 사업보고서 (연간). oracle T19 M2
    # — ADR-0002 D5 예시의 "2024-FY" 는 의미상 alias 이나 운영 코드는 QN 포맷
    # 일관. service / API 가 다른 포맷 도입 시 silent mismatch — 별도 cycle 결정.
    fiscal_period: str
    account: str  # "net_income_consolidated_ifrs"
    value: Decimal
    unit: str  # "krw", "ratio" 등
    ifrs_type: str  # "consolidated" | "separate"
    citation_id: UUID
    superseded_by: UUID | None
    created_at: datetime
    # ADR-0012 D6 — effective_date 가 rcept_no 도출 실 공시일이면 True, 신고기한
    # 보수값 fallback 이면 False. schema 의 effective_date_precise 컬럼과 1:1.
    effective_date_precise: bool = False


@dataclass(frozen=True, slots=True)
class TreasurySharesRecord:
    """보통주 자기주식수 단일 fiscal_period — DART stockTotqySttus.json.

    financials (`FinancialRecord`) 패턴 미러. DART 의 주식의 총수 현황에서
    추출한 보통주 자사주를 fiscal_period 단위로 영구화. 정정공시 시 새 row +
    옛 row 의 `superseded_by` = 새 row.id.

    `effective_date` / `effective_date_precise` 의 의미 (financials 와 동일,
    ADR-0012 D6):
        - DART: 본 record 가 시장에 100% 가용해진 시점. stockTotqySttus.json 응답
          의 `rcept_no` 앞 8자리 (YYYYMMDD = 접수일자) 에서 직접 도출 (정밀,
          `effective_date_precise = True`). 도출 실패 시 자본시장법 제160조
          신고기한 보수값 fallback (`effective_date_precise = False`).
    """

    id: UUID
    code: str
    code_lineage_id: UUID
    effective_date: date
    # DART 일배치 표준 = `f"{year}Q{quarter}"` (예: "2024Q1") — financials 동일.
    fiscal_period: str
    shares_treasury: int  # 보통주 자기주식수 (stockTotqySttus tesstk_co)
    citation_id: UUID
    superseded_by: UUID | None
    created_at: datetime
    # ADR-0012 D6 — financials 와 동일 의미. rcept_no 도출 실 공시일이면 True.
    effective_date_precise: bool = False


@dataclass(frozen=True, slots=True)
class CorporateActionRecord:
    """Corporate action — ADR-0009 D1 의 corporate_actions row.

    이중 PIT (announced_date / effective_date) — `announced_date` 는 정보 가용성
    시점, `effective_date` 는 효력 발생 (권리락일 등). PIT 쿼리는 보통 effective
    기준이지만, "공시된 시점에 알 수 있었던 사건" 이 필요할 때 announced 기준도 사용.

    `details` 는 `Mapping` 으로 type-hint — 호출자가 `MappingProxyType(dict)` 등
    immutable view 를 주입 권장 (oracle 2 차 리뷰 L6).
    """

    id: UUID
    code: str
    code_lineage_id: UUID
    action_type: str  # ADR-0009 D2 enum
    announced_date: date
    effective_date: date
    payment_date: date | None
    ratio: Decimal | None
    cash_amount: Decimal | None
    details: Mapping[str, object]  # ADR-0009 D2 의 action_type 별 JSONB
    citation_id: UUID
    superseded_by: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MacroIndicatorRecord:
    """ECOS 거시지표 단일 관측값 — vintage 이중 시간축 PIT record.

    한국은행 ECOS 는 거시지표를 사후 개정한다(잠정치 공표 → 나중에 확정치로 같은
    기간의 값이 바뀜). PIT-정합(재현성 P0)을 위해 이중 시간축을 도입:

        `reference_date` (date):
            지표가 가리키는 기준 시점/기간. 월별 지표는 해당 월의 1일
            (2024-01 기준금리 → 2024-01-01), 분기별 지표는 분기 시작일
            (2024Q1 GDP → 2024-01-01).

        `vintage_date` (date):
            한국은행이 그 값을 공표/개정한 시점 — PIT 의 두 번째 축.
            같은 `(indicator_id, reference_date)` 에 대해 vintage_date 가 다른
            여러 row 가 append-only 로 누적된다(잠정→확정 개정마다 새 row).

    PIT 조회 규약 (look-ahead 0):
        `WHERE indicator_id=? AND vintage_date <= as_of` 중 각 reference_date 별
        `max(vintage_date)` 의 value 를 사용 → as_of 시점에 알 수 있었던 vintage 만
        선택. 잠정치로 한 과거 분석이 확정치 공표 후에도 재현 가능(불변). 이 규약의
        실제 Repository 구현은 T64 MacroIndicatorRepository 에서 vintage_date<=as_of
        max 규약으로 구현 예정.

    왜 superseded_by 가 없는가:
        financials / treasury_shares 의 정정공시는 사람이 명시적으로 정정 이벤트를
        올리는 구조라 superseded_by chain 으로 old row 를 inactive 처리한다.
        반면 ECOS 매크로 개정은 "새 vintage_date 를 가진 새 row" 의 자연 누적으로
        표현 — 어떤 UPDATE 도 없고, 개정 = 새 vintage row 의 INSERT 다(순수
        append-only). superseded_by chain 을 두면 개정마다 이전 row 를 UPDATE 해야
        하는데, 이는 append-only 불변식 위반이다. vintage_date max 규약으로 동일한
        PIT 의미론을 UPDATE 없이 달성한다.

    Attributes:
        id: row 고유 UUID (surrogate PK).
        indicator_id: ECOS 통계 식별자 (통계표·항목 코드 조합). T63 EcosAdapter 에서
            실제 코드 체계 확정.
        reference_date: 지표의 기준 시점 — 월/분기를 date 로 정규화.
        value: 지표 값 (금리·지수·금액 등).
        unit: 값의 단위 ("percent" / "index" / "krw_100m" 등). T63 에서 확정.
        vintage_date: 한국은행의 공표/개정 시점 — PIT 두 번째 축.
        citation_id: ECOS fetch 의 source citation (ADR-0002 D3 Fidelity).
        created_at: row insert 시각 (UTC). append-only 영구 보존.
    """

    id: UUID
    indicator_id: str  # ECOS 통계 식별자 (예: "722Y001/0101000")
    reference_date: date  # 지표 기준 시점 (월/분기 → date 정규화)
    value: Decimal
    unit: str  # "percent" / "index" / "krw_100m" 등
    vintage_date: date  # 한국은행 공표/개정 시점 (PIT 두 번째 축)
    citation_id: UUID  # ADR-0002 D3 — ECOS fetch citation
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CodeHistoryEntry:
    """`stocks_master.code_history` JSONB 의 단일 entry — ADR-0009 D6.

    종목코드 변경·재상장·합병 시 새 row 가 아닌 history append. 본 entry 의
    `valid_from <= as_of < (valid_to or +inf)` 매칭이 lineage 의 시점 검색 key.
    """

    code: str
    valid_from: date
    valid_to: date | None  # None = 현재 유효
    reason: str  # "initial_listing" / "merger_temporary" / "code_change" 등


@dataclass(frozen=True, slots=True)
class StockMasterRecord:
    """KRX 종목 마스터 — ADR-0002 D5 / ADR-0009 D6 의 stocks_master row.

    lineage 단위 entity — 종목코드 변경·재상장·합병 시 새 row 가 아닌 history
    append. `current_code` 는 현재 활성 코드 (NULL = 폐지).

    Attributes:
        id: lineage UUID (영구 보존, 종목코드 변경 무관).
        current_code: 현재 활성 코드. delisting_date 가 있으면 None 가능.
        current_name: 종목명.
        market: 상장 시장 — "KOSPI" / "KOSDAQ" / "KONEX".
        listing_date: 최초 상장일.
        delisting_date: 폐지일. None = 현재 활성.
        fiscal_month: 결산 월 (12 = 12 월결산). ADR-0005 의 K-IFRS 결산기.
        code_history: 종목코드 변경 history. `(code, valid_from, valid_to, reason)`.
        ifrs_preference_default: K-IFRS 연결/별도 선호 — ADR-0005. "AUTO" / "CONSOLIDATED" / "SEPARATE".
        security_type: 자산군 분류 — ADR-0023 D2. "common" / "preferred" / "etf" / "reit".
            lineage 시계열 불변(immutable). 분포 partition(T78 D5)/screener 필터(T79 D7) 는 별도 사이클.
    """

    id: UUID
    current_code: str | None
    current_name: str
    market: str  # "KOSPI" | "KOSDAQ" | "KONEX"
    listing_date: date
    delisting_date: date | None
    fiscal_month: int
    code_history: tuple[CodeHistoryEntry, ...]
    ifrs_preference_default: str = "AUTO"
    # ADR-0023 D2 — 자산군 분류. 기존 전 종목 backfill = "common".
    # 분포 partition(T78) / screener 필터(T79) 는 별도 사이클 — 여기서 구현 X.
    security_type: str = "common"


@dataclass(frozen=True, slots=True)
class StockSnapshotRecord:
    """Factor 결과 precomputed snapshot — ADR-0002 D5 의 stock_snapshots row.

    `as_of_date == as_of` exact match — 일배치가 미리 계산. PIT semantics 라기보다
    lookup. 정정공시 발생 시 일배치가 재계산하여 새 row 로 overwrite (M0 한계 —
    snapshot 자체의 supersede chain 은 M2+).

    `id` 는 `__post_init__` 에서 한 번 계산하여 캐싱 (oracle 2 차 리뷰 C4).
    매 attribute access 마다 uuid5 SHA-1 hash 재계산 회피.
    """

    stock_code: str
    as_of_date: date
    factor_uuid: UUID
    value: Decimal | None  # None = 미산정 (분모 0 등)
    value_unit: str
    inputs: Mapping[str, object]  # 산정 시 사용한 입력값 (Fidelity)
    citation_id: UUID  # 결과의 대표 citation
    computed_at: datetime
    # 캐싱된 합성 id. __post_init__ 에서 frozen 우회로 설정. PITRecord 호환.
    id: UUID = field(init=False)

    def __post_init__(self) -> None:
        # frozen dataclass 의 immutability 우회 — id 캐싱.
        synthetic_id = uuid5(
            NAMESPACE_OID,
            f"{self.stock_code}|{self.as_of_date.isoformat()}|{self.factor_uuid}",
        )
        object.__setattr__(self, "id", synthetic_id)

    # PITRecord 의 effective_date / created_at 호환 — as_of_date / computed_at 의 alias.
    @property
    def effective_date(self) -> date:
        return self.as_of_date

    @property
    def created_at(self) -> datetime:
        return self.computed_at


# =============================================================================
# Repository Protocols — domain-specific PIT semantics
# =============================================================================

@runtime_checkable
class PriceRepository(Protocol):
    """가격 시계열. KRX 가격은 정정 안 됨 → supersede 없음.

    `as_of` 는 keyword-only required — 호출자가 PIT 의도 명시.
    """

    def fetch_prices(
        self,
        code: str,
        *,
        as_of: date,
        start: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> Sequence[PriceRecord]:
        """`[start, as_of]` 범위의 가격 시계열 (오름차순).

        `as_of` 가 영업일이 아닌 경우 호출자 책임 (AsOfPolicy 가 normalize 후 전달).

        `batch_cutoff` (M1 T48c 재현): None (default) 이면 무필터 = 기존 동작.
        주입 시 frozen 이하 KRX batch 가 생산한 row 만 (backfill 제외 — price 는
        supersede 없음). EXCLUDE_ALL sentinel 이면 그 source fact 전부 제외.
        """
        ...

    def save_prices(self, records: Sequence[PriceRecord]) -> None:
        """가격 row bulk insert — T18 KRX 일배치 합류.

        Invariant:
            - 같은 `(code, effective_date)` 의 중복 insert 는 구현체 정책 (SQL
              은 UNIQUE constraint raise, Fake 는 overwrite). 호출자 책임으로
              dedup 권장.
            - 각 record 의 `citation_id` 가 source_citations 에 미리 save 돼 있어야
              FK 만족. T18 orchestrator 가 citation → price 순서 강제.
        """
        ...


@runtime_checkable
class MarketCapRepository(Protocol):
    """일별 시가총액 + 발행주식수. KRX 시가총액은 정정 안 됨 → supersede 없음.

    PriceRepository 와 동일 PIT 의미 — `effective_date <= as_of` 중 최신. `as_of`
    는 keyword-only required (호출자가 PIT 의도 명시).
    """

    def fetch_latest(
        self,
        code: str,
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> MarketCapRecord | None:
        """`effective_date <= as_of` 중 가장 최신 record. 없으면 None.

        PriceRepository 와 동일 PIT 의미론 (KRX 정정 없음 → supersede 무관).
        `as_of` 가 영업일이 아닌 경우 호출자 책임 (AsOfPolicy 가 normalize 후 전달).

        `batch_cutoff` (M1 T48c 재현): None (default) 이면 무필터. 주입 시 frozen
        이하 KRX batch 가 생산한 row 로 한정한 뒤 max (backfill row 제외). 단순
        `started_at <=` 가 아닌 lexicographic `(started_at, id) <= cutoff`.
        """
        ...

    def save_market_caps(self, records: Sequence[MarketCapRecord]) -> None:
        """시가총액 row bulk insert — KRX 일배치 Phase B 합류.

        Invariant (save_prices 와 동일):
            - 같은 `(code, effective_date)` 의 중복 insert 는 구현체 정책 (SQL
              은 UNIQUE constraint raise, Fake 는 overwrite). 호출자 책임으로
              dedup 권장.
            - 각 record 의 `citation_id` 가 source_citations 에 미리 save 돼 있어야
              FK 만족. 배치 orchestrator 가 citation → market_cap 순서 강제.
        """
        ...


@runtime_checkable
class FinancialRepository(Protocol):
    """재무제표 — supersede chain 해소가 핵심."""

    def fetch_financials(
        self,
        code: str,
        *,
        as_of: date,
        account: str,
        ifrs_type: str | None = None,
        max_periods: int = 8,
        batch_cutoff: BatchCutoff | None = None,
    ) -> Sequence[FinancialRecord]:
        """`as_of` 시점에 active 였던 (code, account) 의 fiscal_period 별 record.

        `batch_cutoff` (M1 T48c 재현): None (default) 이면 무필터 = 기존 동작.
        주입 시 frozen 이하 DART batch 가 생산한 row 로 candidate 를 한정 → 이후
        정정 (successor) row 가 빠지면 `latest_active_by_key` 의 보수 분기
        (pit_enforcer.py:306-311) 가 원 row 를 active 복원 → 정정 전 값 재현
        (C#1, load-bearing). cutoff+as_of 곱집합이 빈 group 은 NoActiveRecordError
        를 group 내부 흡수 → 빈 결과 (M#6, 500 미발생). EXCLUDE_ALL 이면 전부 제외.

        구현체는 supersede chain 을 as_of-time 기준으로 해소 — `PITEnforcer.
        latest_active_record` 의 의미론을 구현체가 가져가거나, 호출 후 PITEnforcer
        에 위임 가능. Protocol 은 시그니처만 강제.

        `ifrs_type` (ADR-0005 K-IFRS 연결/별도): 지정 시 fiscal_period 별 latest
        active 그룹화 **이전에** 해당 ifrs_type 만 필터. 연결/별도는 별개 fact·별개
        supersede chain 이므로, 한 (code, account, fiscal_period) 에 연결+별도가
        공존할 때 그룹 key 충돌을 방지 (None 이면 ifrs_type 무관 — legacy 동작).
        그룹화 이전 필터라 `max_periods` 절사가 단일 ifrs_type 기준으로 적용됨
        (trailing-4 분기 / period_begin 이 다른 ifrs_type 으로 희석되지 않음).
        """
        ...

    def fetch_restatement_history(
        self,
        code: str,
        *,
        fiscal_period: str | None = None,
        as_of: date | None = None,
    ) -> list[FinancialRecord]:
        """정정공시 이력 조회 — 해당 code 의 모든 vintage(active + superseded) 반환.

        ADR-0009(chain) / ADR-0020(append-only) — read-only SELECT 만. 어떠한
        UPDATE / INSERT 도 수행하지 않음. 불변 이력 메타 뷰.

        정렬: (fiscal_period asc, effective_date asc, id asc) — 정정 chain 을
        시간순으로. id 가 final tiebreaker (결정성 보장).

        PIT look-ahead 차단 (§2.4):
            `as_of` 가 주어지면 `effective_date <= as_of` vintage 만 포함 — 그
            시점까지 공시된 정정만 반영. None 이면 전체(현재까지 알려진 모든 정정 =
            메타 이력 뷰). look-ahead 0 보장.

        No Advice (§2.2):
            사실(effective_date / value / is_active / superseded_by)만 반환.
            "정정으로 개선/악화" 같은 판단·라벨 없음. 순수 관측 반환.

        Args:
            code: KRX 종목코드.
            fiscal_period: 특정 분기만 필터. None 이면 전 분기 모두.
            as_of: PIT look-ahead 차단 기준일. None 이면 필터 없음.

        Returns:
            FinancialRecord list — (fiscal_period, effective_date, id) asc 정렬.
            code 가 존재하지 않거나 해당 vintage 없으면 빈 list.
        """
        ...

    def save_financials(self, records: Sequence[FinancialRecord]) -> None:
        """재무제표 row bulk insert — T19 DART 일배치 합류.

        Invariant:
            - 정정공시 시 호출자가 `superseded_by` 를 옛 row.id 로 설정. 본 메서드는
              그 의미를 강제하지 않음 (data integrity 는 호출자 + DB CHECK).
            - 각 record 의 `citation_id` 가 source_citations 에 미리 save 돼 있어야
              FK 만족. T19 orchestrator 가 citation → financial 순서.
            - SQL 구현체는 같은 id 중복 시 IntegrityError (id 는 PK). Fake 는
              overwrite (단순화).
        """
        ...


@runtime_checkable
class TreasurySharesRepository(Protocol):
    """자사주 (보통주 자기주식수) — supersede chain 해소 (financials 패턴)."""

    def fetch_latest_active(
        self,
        code: str,
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> TreasurySharesRecord | None:
        """`as_of` 시점에 active 였던 최신 fiscal_period 의 자사주 record.

        `batch_cutoff` (M1 T48c 재현): None (default) 이면 무필터. 주입 시 frozen
        이하 DART batch 가 생산한 row 로 한정 (financials 와 동일 정정 chain ×
        cutoff 보수 분기 복원, C#1). EXCLUDE_ALL 이면 전부 제외 (→ None).

        구현체는 financials 처럼 `latest_active_by_key` (fiscal_period) 로 정정
        chain 을 as_of-time 해소한 뒤, 그 중 가장 최신 fiscal_period (effective_
        date 기준) 1 건을 반환. 없으면 None (정식 N/A — DbFieldProvider 가
        `shares_treasury` N/A).

        `as_of` 는 keyword-only required — 호출자가 PIT 의도를 type-level 명시.
        """
        ...

    def save_treasury_shares(
        self, records: Sequence[TreasurySharesRecord],
    ) -> None:
        """자사주 row bulk insert — DART 일배치 합류 (financials 패턴).

        Invariant:
            - 정정공시 시 호출자가 `superseded_by` 를 옛 row.id 로 설정 (본
              메서드는 INSERT-only, 기존 row 변경 X — append-only).
            - 각 record 의 `citation_id` 가 source_citations 에 미리 save 돼
              있어야 FK 만족 (배치 orchestrator 가 citation → treasury 순서).
            - SQL 구현체는 같은 id 중복 시 IntegrityError. Fake 는 overwrite.
        """
        ...


@runtime_checkable
class CorporateActionRepository(Protocol):
    """Corporate action — 이중 PIT."""

    def fetch_actions(
        self,
        code: str,
        *,
        as_of: date,
        action_types: frozenset[str] | None = None,
    ) -> Sequence[CorporateActionRecord]:
        """`announced_date <= as_of` 인 사건 (effective_date 무관 — 공시 가용성).

        호출자가 "effective 기준" 으로 필터하려면 결과를 PITEnforcer 의 helper 로 후처리.
        action_types None 이면 모든 종류.
        """
        ...


@runtime_checkable
class MacroIndicatorRepository(Protocol):
    """ECOS 거시지표 — vintage 이중 시간축 PIT 조회.

    매크로 지표는 동일 기준 기간(reference_date)에 대해 공표 시점(vintage_date)이
    다른 여러 값이 append-only 로 누적된다(잠정치 → 확정치). PIT 정합 조회는 이
    이중 시간축을 모두 as_of 로 제한하여 look-ahead 를 0 으로 차단한다.

    PIT 조회 규약 — fetch_latest 의 선택 기준:

        WHERE indicator_id = ?
          AND reference_date <= as_of    -- as_of 이후의 미래 기간 제외
          AND vintage_date   <= as_of    -- as_of 시점에 알 수 있던 관측만 허용
        ORDER BY reference_date DESC, vintage_date DESC
        LIMIT 1

    이 쿼리의 의미:
        "as_of 시점에 알 수 있었던 가장 최근 기준 기간의, 그 기간에 대한 가장
        최신 관측값."

    잠정→확정 재현 보장:
        같은 reference_date 에 잠정 vintage(v_prov)와 확정 vintage(v_final) 가
        있을 때 —
            as_of ∈ [v_prov, v_final) → vintage_date <= as_of 조건이 v_prov 만
                통과 → 잠정값 반환.
            as_of >= v_final → v_final 도 통과 → ORDER BY vintage_date DESC 로
                v_final(확정값) 선택.
        이로써 as_of 를 고정하면 항상 동일 값이 재현된다(look-ahead 0, 불변).

    batch_cutoff 파라미터 없음:
        KRX/DART 의 batch_cutoff 는 "어느 배치 run 이 생산한 row인가"를 제한하는
        재현 축. 반면 매크로 vintage_date 는 한국은행의 공표 시점 자체이므로
        vintage_date <= as_of 가 곧 재현 축이다. 별도 batch_cutoff 없이도
        look-ahead 0 이 보장된다.
    """

    def fetch_latest(
        self,
        indicator_id: str,
        *,
        as_of: date,
    ) -> MacroIndicatorRecord | None:
        """as_of 시점 PIT 정합 최신 관측값. 없으면 None.

        vintage 이중 시간축 PIT 규약 (look-ahead 0):
            reference_date <= as_of AND vintage_date <= as_of 를 동시에 만족하는
            row 중 reference_date DESC, vintage_date DESC 로 정렬한 첫 번째 row.

        잠정→확정 재현:
            같은 reference_date 에 대해 잠정 vintage 와 확정 vintage 가 모두 존재할
            때, as_of 가 잠정·확정 사이면 잠정값, 확정 이후면 확정값을 반환한다.
            as_of 고정 시 결과가 불변이므로 과거 분석을 byte-동일하게 재현 가능.

        Args:
            indicator_id: ECOS 통계 식별자 (예: "722Y001/0101000").
            as_of: PIT 기준 시점 (keyword-only required). 호출자가 PIT 의도를
                type-level 로 명시.

        Returns:
            MacroIndicatorRecord — 조건을 만족하는 단일 관측값. 데이터 없으면 None.
        """
        ...


@runtime_checkable
class StockSnapshotRepository(Protocol):
    """Factor 결과 precomputed lookup."""

    def fetch_snapshot(
        self,
        code: str,
        *,
        as_of: date,
        factor_uuid: UUID,
    ) -> StockSnapshotRecord | None:
        """`(code, as_of, factor_uuid)` exact lookup. 없으면 None."""
        ...

    def fetch_snapshots_for_code(
        self,
        code: str,
        *,
        as_of: date,
        factor_uuids: frozenset[UUID] | None = None,
    ) -> Sequence[StockSnapshotRecord]:
        """`(code, as_of)` 의 여러 factor — Stock Detail 뷰의 지표 카드들."""
        ...


# =============================================================================
# ETF NAV Record + Repository (ADR-0023 D3-a) — R4 deferred
#
# canonical_id / factor pack / ORM / migration / 일배치 wiring 미등록.
# 데이터 영구화 (R4) 진입 전까지 SqlNavRepository / ETF NAV ORM 작성 금지.
# =============================================================================

@dataclass(frozen=True, slots=True)
class NavRecord:
    """ETF 일별 NAV · 시장가 · 괴리율 · AUM — PIT record.

    `NavRow` (adapter canonical) 의 DB record 표현. KRX NAV 는 정정 안 됨 →
    supersede 없음 (MarketCapRecord 와 동일 구조).

    `effective_date` = 기준일 (KRX 거래일) — `effective_date <= as_of` PIT 의미.
    `premium_discount_rate` = (market_price - nav) / nav.

    canonical_id / factor pack 미등록 — R4 deferred (ADR-0023 D8).
    SqlNavRepository / ETF NAV ORM / migration 은 R4 합류 시 구현.
    """

    id: UUID
    code: str
    effective_date: date
    nav: Decimal
    market_price: Decimal
    premium_discount_rate: Decimal
    aum: Decimal | None
    citation_id: UUID
    created_at: datetime


@runtime_checkable
class NavRepository(Protocol):
    """ETF NAV — KRX NAV 는 정정 안 됨 → supersede 없음 (MarketCapRepository 동형).

    PIT 의미: `effective_date <= as_of` 중 가장 최신 record.
    `as_of` 는 keyword-only required — 호출자가 PIT 의도를 type-level 로 명시.

    canonical_id / factor pack 미등록 상태 — R4 deferred.
    SqlNavRepository 구현체는 R4 합류 시 작성.
    """

    def get_nav(
        self,
        code: str,
        *,
        as_of: date,
    ) -> NavRecord | None:
        """`effective_date <= as_of` 중 가장 최신 NavRecord. 없으면 None.

        Args:
            code: KRX ETF 종목코드.
            as_of: PIT 기준일 (keyword-only required).

        Returns:
            가장 최신 NavRecord 또는 None (해당 ETF 의 데이터 없음).
        """
        ...

    def save_navs(self, records: Sequence[NavRecord]) -> None:
        """NavRecord bulk insert.

        Invariant (MarketCapRepository.save_market_caps 와 동일):
            - 같은 `(code, effective_date)` 의 중복 insert 는 구현체 정책 (SQL 은
              UNIQUE constraint raise, Fake 는 overwrite).
            - 각 record 의 `citation_id` 가 source_citations 에 미리 save 돼 있어야
              FK 만족.
        """
        ...
