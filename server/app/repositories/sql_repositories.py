"""SQL Repository 구현체 — PostgreSQL/SQLite sync ORM 기반.

`app/repositories/fakes.py` + `app/repositories/citation_repository.py` +
`app/repositories/stocks_master_repository.py` 의 Fake 구현체와 **동일 contract**
를 SQLAlchemy 2 sync session 위에서 충족.

핵심 설계 (oracle 자문 R2 / 2 차 리뷰 C5):

1. **PITEnforcer 위임 — Fake 와 동일 알고리즘**:
   SQL 은 candidate set 만 fetch (SQL filter — `code` + `account` + 가벼운
   date 범위), PIT 의 active 판정·tie-break·chain 해소는 PITEnforcer 의 같은
   메서드를 사용. Fake/SQL contract 동등성 보장 + 알고리즘 단일 source.

2. **모든 method 가 `as_of` keyword-only required** — pit_protocols.py 의 Protocol
   계약. type-level 강제로 endpoint 가 PIT 의도를 명시.

3. **ORM ↔ Record 변환은 fetch 직후 일관 수행** — 운영 코드는 dataclass Record
   와만 상호작용. ORM detached row 가 layer 경계 밖으로 누출 금지.

4. **결정성 보장 sort** — Fake 와 같은 (effective_date, ..., id) tuple 키.
   SQL ORDER BY 가 알고리즘 단계 의 일관성을 보강.

5. **sync session 의존** — `__init__(session: Session)`. 한 request = 한 session
   pattern (FastAPI Depends + get_session generator).

Note (성능):
    SQL 의 candidate fetch 는 단순 indexed scan (code/account/date). chain 해소를
    SQL 로 옮기면 query 복잡도 증가 + Fake 와 알고리즘 분기 → M1 SQL 최적화 backlog.
"""

from __future__ import annotations

import unicodedata
from datetime import date
from typing import Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.converters import (
    corporate_action_orm_to_record,
    financial_orm_to_record,
    financial_record_to_orm,
    price_orm_to_record,
    price_record_to_orm,
    stocks_master_orm_to_record,
)
from app.db.orm.corporate_actions import CorporateActionORM
from app.db.orm.financials import FinancialORM
from app.db.orm.prices_daily import PriceDailyORM
from app.db.orm.stocks_master import StocksMasterORM
from app.repositories.pit_protocols import (
    CorporateActionRecord,
    CorporateActionRepository,
    FinancialRecord,
    FinancialRepository,
    PriceRecord,
    PriceRepository,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import StocksMasterRepository
from app.services.pit_enforcer import PITEnforcer

__all__ = [
    "SqlCorporateActionRepository",
    "SqlFinancialRepository",
    "SqlPriceRepository",
    "SqlStocksMasterRepository",
]


# =============================================================================
# Price — KRX 가격, supersede 없음
# =============================================================================

class SqlPriceRepository(PriceRepository):
    """SQLAlchemy 기반 가격 시계열 repository.

    KRX 가격은 정정 안 됨 → supersede 무관. SQL 단순 범위 scan + 결정적 정렬.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def fetch_prices(
        self,
        code: str,
        *,
        as_of: date,
        start: date,
    ) -> Sequence[PriceRecord]:
        if start > as_of:
            return ()
        stmt = (
            select(PriceDailyORM)
            .where(
                PriceDailyORM.code == code,
                PriceDailyORM.effective_date >= start,
                PriceDailyORM.effective_date <= as_of,
            )
            .order_by(PriceDailyORM.effective_date.asc())
        )
        result = self._session.execute(stmt)
        return tuple(price_orm_to_record(o) for o in result.scalars().all())

    def save_prices(self, records: Sequence[PriceRecord]) -> None:
        """T18 합류 — bulk insert. 같은 (code, date) PK 중복 시 IntegrityError.

        호출자 (T18 KRX 일배치) 가 dedup 책임:
            - 일배치 1 회 = 1 영업일 fetch → 중복 가능성 낮음.
            - retry 시 같은 batch 의 부분 성공 후 다시 insert 면 IntegrityError.
              상위 layer 가 처리 (현재 사이클 미구현 — backlog).
        """
        for r in records:
            self._session.add(price_record_to_orm(r))
        self._session.flush()


# =============================================================================
# Financial — 정정공시 chain 해소 (PITEnforcer 위임)
# =============================================================================

class SqlFinancialRepository(FinancialRepository):
    """SQLAlchemy 기반 재무제표 repository — supersede chain 해소.

    동작:
        1. SQL 로 `(code, account)` candidate fetch (PIT 인덱스 활용).
        2. PITEnforcer.latest_active_by_key 로 fiscal_period 별 latest active 산출.
        3. 결정적 sort (effective_date, fiscal_period, id) 후 max_periods 절사.

    Fake 와 동일 알고리즘. SQL 은 candidate set 좁히는 역할만.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._enforcer = PITEnforcer()

    def fetch_financials(
        self,
        code: str,
        *,
        as_of: date,
        account: str,
        max_periods: int = 8,
    ) -> Sequence[FinancialRecord]:
        # oracle 리뷰 C1 — supersede chain head 가 account 가 미세하게 다른 case
        # (회계기간 소급정정으로 dart_account_mapper 정규화 변경 등) 에 대비하여
        # SQL 측에서는 code 만 fetch. Python 측에서 account 필터 + PITEnforcer
        # 가 chain 해소 시 record_by_id 가 same-code 의 모든 row 를 cover.
        # 한 종목의 financials 가 운영 size (~수십 account × 수년 = 수백 row)
        # 이므로 부담 미미.
        stmt = select(FinancialORM).where(FinancialORM.code == code)
        result = self._session.execute(stmt)
        all_records = [
            financial_orm_to_record(o) for o in result.scalars().all()
        ]
        # account 필터는 Python 측 — Fake (`FakeFinancialRepository.fetch_
        # financials:87-90`) 와 동일 위치.
        records = [r for r in all_records if r.account == account]

        # fiscal_period 별 latest active (effective_date 기준 PIT).
        latest_by_period = self._enforcer.latest_active_by_key(
            records, as_of, key=lambda r: r.fiscal_period
        )
        # Fake 와 동일 sort key — id 가 final tiebreaker.
        sorted_periods = sorted(
            latest_by_period.values(),
            key=lambda r: (r.effective_date, r.fiscal_period, str(r.id)),
        )
        return tuple(sorted_periods[-max_periods:])

    def save_financials(self, records: Sequence[FinancialRecord]) -> None:
        """T19 합류 — bulk insert. 같은 id 중복 시 IntegrityError.

        정정공시 처리는 호출자 책임:
            - 옛 row 의 superseded_by 컬럼은 별도 UPDATE 가 아니라, 옛 row 를
              fetch 후 `superseded_by=new_id` 의 새 row 로 다시 save_financials
              (append-only 의미 위반). M0 단순화: 옛 row 의 superseded_by 는
              호출자가 별도 ORM 업데이트로 처리 (T19 향후 cycle 또는 별도
              update_superseded_by 메서드).
        """
        for r in records:
            self._session.add(financial_record_to_orm(r))
        self._session.flush()


# =============================================================================
# CorporateAction — 이중 PIT (announced 기준) + chain 해소
# =============================================================================

class SqlCorporateActionRepository(CorporateActionRepository):
    """SQLAlchemy 기반 corporate action repository.

    announced_date PIT — `announced_date <= as_of` candidates 를 SQL 로 fetch
    후 PITEnforcer.filter_active_records 가 chain 해소 (date_of=announced_date).
    Fake 와 동일 알고리즘 (oracle 2 차 리뷰 C5 동일성).
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._enforcer = PITEnforcer()

    def fetch_actions(
        self,
        code: str,
        *,
        as_of: date,
        action_types: frozenset[str] | None = None,
    ) -> Sequence[CorporateActionRecord]:
        # SQL 로 code 만 1차 필터 — action_types / announced_date / chain 해소는
        # Python layer 에서 PITEnforcer 알고리즘 단일 source.
        stmt = select(CorporateActionORM).where(CorporateActionORM.code == code)
        result = self._session.execute(stmt)
        records = [
            corporate_action_orm_to_record(o) for o in result.scalars().all()
        ]
        if action_types is not None:
            records = [r for r in records if r.action_type in action_types]

        # announced_date 기준 PIT — Fake 와 동일.
        active = self._enforcer.filter_active_records(
            records, as_of, date_of=lambda r: r.announced_date,
        )
        # Fake 와 동일 sort.
        return tuple(sorted(
            active,
            key=lambda r: (r.effective_date, r.created_at, str(r.id)),
        ))


# =============================================================================
# StocksMaster — lineage 단위 search/lookup
# =============================================================================

class SqlStocksMasterRepository(StocksMasterRepository):
    """SQLAlchemy 기반 종목 마스터 repository.

    Fake 와 동일 contract:
        - fetch_by_code: lineage 의 `code_history` 에 `code` entry 가 어디든
          있으면 lineage 반환. as_of 는 hint (oracle T25 결정 6).
        - search: NFKC 정규화 + 숫자 자동감지. as_of 시점 active filter.
        - list_active: as_of 시점 listing/delisting 검사.

    Note:
        `code_history` 가 JSON 컬럼이라 PostgreSQL/SQLite 모두 SQL 측 검색이
        dialect 의존적. 본 구현은 모든 lineage 를 fetch 후 Python 측에서 검색
        (KOSPI/KOSDAQ ~2,500 종목 × 1~수개 history entry → 운영 size 충분).
        대규모 universe (M2 ETF 합류) 시 JSONB GIN 인덱스 + dialect-specific
        query 도입 backlog.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def fetch_by_code(
        self, code: str, *, as_of: date,
    ) -> StockMasterRecord | None:
        # oracle 리뷰 M2 — 2-단계 fetch:
        # 1) fast path: `current_code == code` index lookup (운영 hot path 80%+).
        # 2) slow path: code_history JSON 의 옛 코드 매칭 (lineage 전체 scan).
        #
        # oracle 리뷰 C2 — `current_code` 에 UNIQUE 미강제 (ADR-0009 D7 재상장
        # 등에서 같은 code 가 다른 lineage 의 current_code 역사 가질 수 있음).
        # `scalar_one_or_none` 이 `MultipleResultsFound` raise 가능 → `.first()`
        # + ORDER BY id 결정성 보강. Fake (`stocks_master_repository.py:130`)
        # 의 `candidates[0]` 첫 매치 의미와 동등.
        fast_stmt = (
            select(StocksMasterORM)
            .where(StocksMasterORM.current_code == code)
            .order_by(StocksMasterORM.id.asc())
        )
        fast_row = self._session.execute(fast_stmt).scalars().first()
        if fast_row is not None:
            return stocks_master_orm_to_record(fast_row)

        # slow path — `current_code != code` 이지만 code_history 에 있는 lineage.
        slow_stmt = select(StocksMasterORM)
        result = self._session.execute(slow_stmt)
        for orm in result.scalars().all():
            record = stocks_master_orm_to_record(orm)
            for entry in record.code_history:
                if entry.code == code:
                    return record
        return None

    def search(
        self,
        query: str,
        *,
        as_of: date,
        limit: int = 50,
        include_delisted: bool = False,
    ) -> Sequence[StockMasterRecord]:
        # NFKC 정규화 + 빈 입력 검증을 Fake 와 동일 의미로 유지.
        normalized = unicodedata.normalize("NFKC", query).strip()
        if not normalized:
            raise ValueError("search query must be non-empty")
        if limit < 0:
            raise ValueError(f"limit must be >= 0, got {limit}")

        is_numeric_query = normalized.isdigit()

        stmt = select(StocksMasterORM)
        result = self._session.execute(stmt)
        records = [stocks_master_orm_to_record(o) for o in result.scalars().all()]

        results: list[tuple[bool, str, StockMasterRecord]] = []
        seen: set[str] = set()
        for r in records:
            if str(r.id) in seen:
                continue
            if r.listing_date > as_of:
                continue
            if (not include_delisted
                    and r.delisting_date is not None
                    and r.delisting_date <= as_of):
                continue

            matched = False
            is_prefix = False
            if is_numeric_query:
                padded_query = (
                    normalized.zfill(6) if len(normalized) <= 6 else normalized
                )
                for entry in r.code_history:
                    if normalized in entry.code:
                        matched = True
                        if entry.code == padded_query:
                            is_prefix = True
                        elif entry.code.startswith(padded_query):
                            is_prefix = True
                        break
            else:
                name_norm = unicodedata.normalize("NFKC", r.current_name)
                if normalized in name_norm:
                    matched = True
                    if name_norm.startswith(normalized):
                        is_prefix = True

            if matched:
                results.append((not is_prefix, r.current_name, r))
                seen.add(str(r.id))

        results.sort()
        return tuple(r for _, _, r in results[:limit])

    def list_active(
        self, *, as_of: date,
    ) -> Sequence[StockMasterRecord]:
        # SQL 로 1차 필터 — listing_date <= as_of AND (delisting NULL OR > as_of).
        stmt = (
            select(StocksMasterORM)
            .where(StocksMasterORM.listing_date <= as_of)
            .where(
                or_(
                    StocksMasterORM.delisting_date.is_(None),
                    StocksMasterORM.delisting_date > as_of,
                )
            )
        )
        result = self._session.execute(stmt)
        records = [stocks_master_orm_to_record(o) for o in result.scalars().all()]
        return tuple(sorted(records, key=lambda r: r.current_code or ""))
