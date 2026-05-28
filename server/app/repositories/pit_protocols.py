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
    close_adjusted: Decimal  # ADR-0001
    citation_id: UUID  # ADR-0002 D3
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FinancialRecord:
    """재무제표 단일 항목 (account 별 row).

    ADR-0002 D3 — `effective_date` 는 공시 효력일 (DART rcept_dt 가 아닌 회계기간 등).
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
    ) -> Sequence[PriceRecord]:
        """`[start, as_of]` 범위의 가격 시계열 (오름차순).

        `as_of` 가 영업일이 아닌 경우 호출자 책임 (AsOfPolicy 가 normalize 후 전달).
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
class FinancialRepository(Protocol):
    """재무제표 — supersede chain 해소가 핵심."""

    def fetch_financials(
        self,
        code: str,
        *,
        as_of: date,
        account: str,
        max_periods: int = 8,
    ) -> Sequence[FinancialRecord]:
        """`as_of` 시점에 active 였던 (code, account) 의 fiscal_period 별 record.

        구현체는 supersede chain 을 as_of-time 기준으로 해소 — `PITEnforcer.
        latest_active_record` 의 의미론을 구현체가 가져가거나, 호출 후 PITEnforcer
        에 위임 가능. Protocol 은 시그니처만 강제.
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
