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
from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.db.converters import (
    corporate_action_orm_to_record,
    financial_orm_to_record,
    financial_record_to_orm,
    macro_indicator_orm_to_record,
    market_cap_orm_to_record,
    market_cap_record_to_orm,
    price_orm_to_record,
    price_record_to_orm,
    stocks_master_orm_to_record,
    treasury_shares_orm_to_record,
    treasury_shares_record_to_orm,
)
from app.db.orm.batch_runs import BatchRunORM
from app.db.orm.corporate_actions import CorporateActionORM
from app.db.orm.financials import FinancialORM
from app.db.orm.macro_indicators import MacroIndicatorORM
from app.db.orm.market_caps import MarketCapDailyORM
from app.db.orm.prices_daily import PriceDailyORM
from app.db.orm.source_citations import SourceCitationORM
from app.db.orm.stocks_master import StocksMasterORM
from app.db.orm.treasury_shares import TreasurySharesORM
from app.repositories.batch_run_repository import BatchCutoff
from app.repositories.pit_protocols import (
    CorporateActionRecord,
    CorporateActionRepository,
    FinancialRecord,
    FinancialRepository,
    MacroIndicatorRecord,
    MacroIndicatorRepository,
    MarketCapRecord,
    MarketCapRepository,
    PriceRecord,
    PriceRepository,
    StockMasterRecord,
    TreasurySharesRecord,
    TreasurySharesRepository,
)
from app.repositories.stocks_master_repository import StocksMasterRepository
from app.services.pit_enforcer import PITEnforcer

__all__ = [
    "AppendOnlyViolationError",
    "SqlCorporateActionRepository",
    "SqlFinancialRepository",
    "SqlMacroIndicatorRepository",
    "SqlMarketCapRepository",
    "SqlPriceRepository",
    "SqlStocksMasterRepository",
    "SqlTreasurySharesRepository",
]


class AppendOnlyViolationError(Exception):
    """ADR-0020 의 append-only 불변식 위반 — repository layer 방어선.

    SQLite (테스트) 는 PG 의 조건부 BEFORE UPDATE 트리거 (ADR-0020 D3, Alembic
    0007) 를 지원하지 않으므로, repository 의 `update_superseded_by` 가 동일
    불변식 (superseded_by NULL→non-NULL 단일 전이만 허용, chain 1 회성) 을
    검증하여 본 예외로 거부. 운영 PG 는 트리거가 최종 방어선.
    """


# =============================================================================
# 재현 batch cutoff — citation→batch_runs 2-hop join 필터 (M1 T48c)
# =============================================================================

def _batch_cutoff_predicate(
    citation_id_col: ColumnElement[UUID],
    *,
    source: str,
    cutoff: BatchCutoff,
) -> ColumnElement[bool]:
    """fact 의 `citation_id` 가 cutoff 이하 batch 가 생산한 것인지 SQL 술어.

    M1 T48c — 재현 fetch 가 frozen batch 이하 batch 가 생산한 row 만 통과시키는
    필터. fact → source_citations (citation_id) → batch_runs (batch_id) 2-hop
    join 을 평탄화하여 `citation_id IN (allowed)` 형태로 구성.

    필터 (Momus C#2 / H#3):
        - **source 필터 (H#3)**: `br.source = :source` — fact 종류 ↔ source 정합
          방어 (KRX fetch → 'KRX', DART fetch → 'DART'). 오염된 citation 의
          타-source batch 가 통과하지 않음.
        - **lexicographic cutoff (C#2)**: `(br.started_at < :cs) OR
          (br.started_at = :cs AND br.id <= :cid)`. freeze 의 `ORDER BY
          started_at DESC, id DESC LIMIT 1` 와 정확한 대칭 — frozen batch 자신 +
          그 이전 batch 만 통과. 단순 `started_at <=` 금지.

    EXCLUDE_ALL (H#5):
        cutoff 가 EXCLUDE_ALL sentinel (빈 frozen batch_id) 이면 어떤 fact 도
        통과하지 못하도록 항상 거짓 술어를 반환 — 그 source 의 fact 전부 제외
        (as_of 시점 데이터 없음 상태 재현, look-ahead 누출 차단).
    """
    if cutoff.is_exclude_all:
        # 항상 거짓 — 그 source fact 전부 제외 (무필터 아님).
        return citation_id_col.in_([])

    assert cutoff.started_at is not None and cutoff.id is not None
    allowed_citations = (
        select(SourceCitationORM.id)
        .join(BatchRunORM, SourceCitationORM.batch_id == BatchRunORM.id)
        .where(
            BatchRunORM.source == source,
            or_(
                BatchRunORM.started_at < cutoff.started_at,
                and_(
                    BatchRunORM.started_at == cutoff.started_at,
                    BatchRunORM.id <= cutoff.id,
                ),
            ),
        )
    )
    return citation_id_col.in_(allowed_citations)


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
        batch_cutoff: BatchCutoff | None = None,
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
        # M1 T48c 재현 — cutoff 주입 시 frozen 이하 KRX batch 가 생산한 row 만
        # (price 는 supersede 없음 — backfill row 제외 목적, C#1). default None 은
        # 무필터 = 기존 동작 완전 보존 (회귀 0).
        if batch_cutoff is not None:
            stmt = stmt.where(
                _batch_cutoff_predicate(
                    PriceDailyORM.citation_id, source="KRX", cutoff=batch_cutoff,
                )
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
# MarketCap — KRX 시가총액, supersede 없음 (prices 패턴)
# =============================================================================

class SqlMarketCapRepository(MarketCapRepository):
    """SQLAlchemy 기반 일별 시가총액 repository.

    KRX 시가총액은 정정 안 됨 → supersede 무관 (SqlPriceRepository 패턴).
    `effective_date <= as_of` 중 최신을 SQL 단순 범위 scan + 결정적 정렬로 산출.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def fetch_latest(
        self,
        code: str,
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> MarketCapRecord | None:
        # effective_date <= as_of 중 최신 1 건. 동일 일자 tie 는 created_at, id
        # final tiebreaker (Fake 와 동일 결정성). KRX 시가총액은 정정 없으나
        # 방어적 결정성 sort.
        stmt = (
            select(MarketCapDailyORM)
            .where(
                MarketCapDailyORM.code == code,
                MarketCapDailyORM.effective_date <= as_of,
            )
            .order_by(
                MarketCapDailyORM.effective_date.desc(),
                MarketCapDailyORM.created_at.desc(),
                MarketCapDailyORM.id.desc(),
            )
            .limit(1)
        )
        # M1 T48c 재현 — cutoff 주입 시 frozen 이하 KRX batch 가 생산한 row 로
        # 한정한 뒤 max. backfill (과거 영업일을 늦은 batch 가 보충) row 제외가
        # 목적 (C#1 — supersede 무관, effective_date<=as_of 와 직교). cutoff
        # 적용 후 max 를 다시 잡아야 frozen 이후 batch 의 row 가 max 를 차지하는
        # look-ahead 를 차단. default None = 무필터 (회귀 0).
        if batch_cutoff is not None:
            stmt = stmt.where(
                _batch_cutoff_predicate(
                    MarketCapDailyORM.citation_id,
                    source="KRX",
                    cutoff=batch_cutoff,
                )
            )
        row = self._session.execute(stmt).scalars().first()
        if row is None:
            return None
        return market_cap_orm_to_record(row)

    def save_market_caps(self, records: Sequence[MarketCapRecord]) -> None:
        """Phase B 합류 — bulk insert. 같은 (code, date) PK 중복 시 IntegrityError.

        호출자 (KRX 일배치) 가 dedup 책임 — save_prices 와 동일 정책. 각 record
        의 citation_id 가 source_citations 에 미리 save 돼 있어야 FK 만족
        (배치 orchestrator 가 citation → market_cap 순서 강제).
        """
        for r in records:
            self._session.add(market_cap_record_to_orm(r))
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
        ifrs_type: str | None = None,
        max_periods: int = 8,
        batch_cutoff: BatchCutoff | None = None,
    ) -> Sequence[FinancialRecord]:
        # oracle 리뷰 C1 — supersede chain head 가 account 가 미세하게 다른 case
        # (회계기간 소급정정으로 dart_account_mapper 정규화 변경 등) 에 대비하여
        # SQL 측에서는 code 만 fetch. Python 측에서 account 필터 + PITEnforcer
        # 가 chain 해소 시 record_by_id 가 same-code 의 모든 row 를 cover.
        # 한 종목의 financials 가 운영 size (~수십 account × 수년 = 수백 row)
        # 이므로 부담 미미.
        stmt = select(FinancialORM).where(FinancialORM.code == code)
        # M1 T48c 재현 — cutoff 주입 시 frozen 이하 DART batch 가 생산한 row 만
        # candidate 에 남긴다. 이후 batch 의 successor (정정) row 가 빠지면
        # latest_active_by_key 가 pit_enforcer.py:306-311 보수 분기
        # (record_by_id.get(superseded_by) is None → active=True) 로 원 row 를
        # 복원 → 정정 전 값으로 byte-동일 재현 (C#1, load-bearing). cutoff+as_of
        # 곱집합이 빈 candidates 인 group 은 latest_active_by_key 가
        # NoActiveRecordError 를 group 내부에서 흡수 (silent skip) → 빈 결과로
        # 처리, 500 미발생 (M#6). default None = 무필터 (회귀 0).
        if batch_cutoff is not None:
            stmt = stmt.where(
                _batch_cutoff_predicate(
                    FinancialORM.citation_id, source="DART", cutoff=batch_cutoff,
                )
            )
        result = self._session.execute(stmt)
        all_records = [
            financial_orm_to_record(o) for o in result.scalars().all()
        ]
        # account + ifrs_type 필터는 Python 측 — Fake 와 동일 위치. ifrs_type 은
        # 그룹화 이전 필터 — 연결/별도 별개 chain (ADR-0005) 그룹 key 충돌 방지.
        records = [
            r for r in all_records
            if r.account == account
            and (ifrs_type is None or r.ifrs_type == ifrs_type)
        ]

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

    def fetch_restatement_history(
        self,
        code: str,
        *,
        fiscal_period: str | None = None,
        as_of: date | None = None,
    ) -> list[FinancialRecord]:
        """정정공시 이력 — 해당 code 의 모든 vintage(active + superseded) 반환.

        read-only SELECT 만 — ADR-0020 append-only 불변식 미변경. 어떠한
        UPDATE / INSERT 도 수행하지 않음.

        PIT look-ahead 차단 (§2.4): as_of 주어지면 effective_date <= as_of 만.
        fiscal_period 주어지면 해당 분기만. None 이면 필터 없음(전체 이력 뷰).

        정렬: (fiscal_period asc, effective_date asc, id asc) — Fake 와 동일
        결정성. id 가 final tiebreaker.
        """
        # SQL 필터: code (+ 선택적 fiscal_period + as_of PIT look-ahead 차단).
        # superseded_by 유무 무관 — active + superseded 전체 vintage 반환.
        stmt = select(FinancialORM).where(FinancialORM.code == code)
        if fiscal_period is not None:
            stmt = stmt.where(FinancialORM.fiscal_period == fiscal_period)
        if as_of is not None:
            # PIT look-ahead 차단 — as_of 이후 공시된 vintage(effective_date > as_of) 제외.
            stmt = stmt.where(FinancialORM.effective_date <= as_of)
        # Fake 와 동일 결정적 정렬 — (fiscal_period, effective_date, id) asc.
        stmt = stmt.order_by(
            FinancialORM.fiscal_period.asc(),
            FinancialORM.effective_date.asc(),
            FinancialORM.id.asc(),
        )
        result = self._session.execute(stmt)
        return [financial_orm_to_record(o) for o in result.scalars().all()]

    def save_financials(self, records: Sequence[FinancialRecord]) -> None:
        """T19 합류 — bulk insert. 같은 id 중복 시 IntegrityError.

        정정공시 처리는 호출자 책임:
            - 정정 row 는 본 메서드로 INSERT 후, 옛 row 의 superseded_by 는
              `update_superseded_by` (ADR-0020 D4 — 유일 허용 UPDATE 경로) 로
              NULL→successor set. 본 메서드는 INSERT-only 이며 기존 row 를
              변경하지 않음 (append-only).
        """
        for r in records:
            self._session.add(financial_record_to_orm(r))
        self._session.flush()

    def update_superseded_by(
        self, record_id: UUID, successor_id: UUID,
    ) -> None:
        """정정공시 chain — 원 row 의 superseded_by 를 NULL→successor 로 set.

        ADR-0020 D4 — financials 의 **유일하게 허용된 UPDATE 경로**. 정정공시
        처리 (정정 row INSERT + 원 row supersede) 의 진입점. PG 는 ADR-0020 의
        조건부 BEFORE UPDATE 트리거가 DB 레벨에서 동일 불변식을 재차 강제하며,
        SQLite (테스트) 는 본 메서드의 검증이 유일 방어선.

        불변식 (ADR-0020 D1):
            - 대상 row 가 존재해야 함.
            - 대상 row 의 현재 superseded_by 가 NULL 이어야 함 (이미 superseded
              면 chain 1 회성 위반 → 거부).
            - successor row 가 같은 테이블에 존재해야 함 (self-FK 무결성).
            - superseded_by 외 컬럼은 변경하지 않음 (ORM attribute 1 개만 set).

        Raises:
            AppendOnlyViolationError: 대상/ successor 부재 또는 이미 superseded.
        """
        target = self._session.get(FinancialORM, record_id)
        if target is None:
            raise AppendOnlyViolationError(
                f"financials row {record_id} 부재 — update_superseded_by 불가"
            )
        if target.superseded_by is not None:
            raise AppendOnlyViolationError(
                f"financials row {record_id} 는 이미 superseded "
                f"(superseded_by={target.superseded_by}) — chain 1 회성 위반 "
                f"(ADR-0020 D1)"
            )
        successor = self._session.get(FinancialORM, successor_id)
        if successor is None:
            raise AppendOnlyViolationError(
                f"successor financials row {successor_id} 부재 — self-FK 무결성 위반"
            )
        # superseded_by 외 컬럼 미변경 — ORM attribute 1 개만 set.
        target.superseded_by = successor_id
        self._session.flush()


# =============================================================================
# TreasuryShares — 자사주 정정공시 chain 해소 (financials 패턴)
# =============================================================================

class SqlTreasurySharesRepository(TreasurySharesRepository):
    """SQLAlchemy 기반 자사주 repository — supersede chain 해소.

    동작 (SqlFinancialRepository 패턴):
        1. SQL 로 `code` candidate fetch (PIT 인덱스 활용).
        2. PITEnforcer.latest_active_by_key 로 fiscal_period 별 latest active.
        3. 그 중 가장 최신 fiscal_period (effective_date, fiscal_period, id) 1 건.

    Fake 와 동일 알고리즘. SQL 은 candidate set 좁히는 역할만.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._enforcer = PITEnforcer()

    def fetch_latest_active(
        self,
        code: str,
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> TreasurySharesRecord | None:
        # SqlFinancialRepository.fetch_financials 와 동일 — code 만 SQL fetch 후
        # Python 측에서 PITEnforcer 가 chain 해소 (record_by_id 가 same-code 모든
        # row cover). 한 종목의 treasury 는 운영 size (~수년 × 4 분기) 미미.
        stmt = select(TreasurySharesORM).where(TreasurySharesORM.code == code)
        # M1 T48c 재현 — financials 와 동일 정정 chain × cutoff 보수 분기 복원
        # (C#1, source='DART'). default None = 무필터 (회귀 0).
        if batch_cutoff is not None:
            stmt = stmt.where(
                _batch_cutoff_predicate(
                    TreasurySharesORM.citation_id,
                    source="DART",
                    cutoff=batch_cutoff,
                )
            )
        result = self._session.execute(stmt)
        records = [
            treasury_shares_orm_to_record(o) for o in result.scalars().all()
        ]
        latest_by_period = self._enforcer.latest_active_by_key(
            records, as_of, key=lambda r: r.fiscal_period
        )
        if not latest_by_period:
            return None
        # Fake 와 동일 결정성 — id final tiebreaker.
        return max(
            latest_by_period.values(),
            key=lambda r: (r.effective_date, r.fiscal_period, str(r.id)),
        )

    def save_treasury_shares(
        self, records: Sequence[TreasurySharesRecord],
    ) -> None:
        """DART 일배치 합류 — bulk insert. 같은 id 중복 시 IntegrityError.

        정정공시 처리는 호출자 책임 (SqlFinancialRepository.save_financials
        동일): 정정 row INSERT 후 옛 row 의 superseded_by 는 `update_superseded_by`
        (ADR-0020 D4 유일 허용 UPDATE 경로) 로 set. 본 메서드는 INSERT-only.
        """
        for r in records:
            self._session.add(treasury_shares_record_to_orm(r))
        self._session.flush()

    def update_superseded_by(
        self, record_id: UUID, successor_id: UUID,
    ) -> None:
        """정정공시 chain — 원 row 의 superseded_by 를 NULL→successor 로 set.

        ADR-0020 D4 — treasury_shares 의 유일 허용 UPDATE 경로
        (SqlFinancialRepository.update_superseded_by 와 동일 불변식). PG 는
        ADR-0020 조건부 트리거가 DB 레벨에서 재차 강제, SQLite (테스트) 는 본
        검증이 유일 방어선 (ADR-0020 D5).

        Raises:
            AppendOnlyViolationError: 대상/successor 부재 또는 이미 superseded.
        """
        target = self._session.get(TreasurySharesORM, record_id)
        if target is None:
            raise AppendOnlyViolationError(
                f"treasury_shares row {record_id} 부재 — "
                f"update_superseded_by 불가"
            )
        if target.superseded_by is not None:
            raise AppendOnlyViolationError(
                f"treasury_shares row {record_id} 는 이미 superseded "
                f"(superseded_by={target.superseded_by}) — chain 1 회성 위반 "
                f"(ADR-0020 D1)"
            )
        successor = self._session.get(TreasurySharesORM, successor_id)
        if successor is None:
            raise AppendOnlyViolationError(
                f"successor treasury_shares row {successor_id} 부재 — "
                f"self-FK 무결성 위반"
            )
        target.superseded_by = successor_id
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

    def update_superseded_by(
        self, record_id: UUID, successor_id: UUID,
    ) -> None:
        """정정공시 chain — corporate_actions 의 유일 허용 UPDATE 경로.

        ADR-0020 D4. SqlFinancialRepository.update_superseded_by 와 동일 불변식
        (NULL→successor 단일 전이, chain 1 회성, self-FK 무결성). PG 는 ADR-0020
        조건부 트리거가 재차 강제, SQLite 는 본 검증이 유일 방어선.

        Raises:
            AppendOnlyViolationError: 대상/ successor 부재 또는 이미 superseded.
        """
        target = self._session.get(CorporateActionORM, record_id)
        if target is None:
            raise AppendOnlyViolationError(
                f"corporate_actions row {record_id} 부재 — "
                f"update_superseded_by 불가"
            )
        if target.superseded_by is not None:
            raise AppendOnlyViolationError(
                f"corporate_actions row {record_id} 는 이미 superseded "
                f"(superseded_by={target.superseded_by}) — chain 1 회성 위반 "
                f"(ADR-0020 D1)"
            )
        successor = self._session.get(CorporateActionORM, successor_id)
        if successor is None:
            raise AppendOnlyViolationError(
                f"successor corporate_actions row {successor_id} 부재 — "
                f"self-FK 무결성 위반"
            )
        target.superseded_by = successor_id
        self._session.flush()


# =============================================================================
# MacroIndicator — ECOS 거시지표, vintage 이중 시간축 PIT 조회
# =============================================================================

class SqlMacroIndicatorRepository(MacroIndicatorRepository):
    """SQLAlchemy 기반 ECOS 거시지표 repository — vintage 이중 시간축 PIT 조회.

    동작:
        reference_date <= as_of AND vintage_date <= as_of 를 동시에 만족하는 row 중
        reference_date DESC, vintage_date DESC 로 정렬한 첫 번째 row 를 반환.

    잠정→확정 재현:
        같은 (indicator_id, reference_date) 에 잠정(이른 vintage)·확정(늦은 vintage)
        두 row 가 있을 때, as_of 가 잠정·확정 사이면 vintage_date <= as_of 가 잠정만
        통과 → 잠정값 반환. as_of 가 확정 vintage 이후면 확정값이 ORDER BY 우선 선택
        → 확정값 반환. as_of 고정 시 결과 불변 (look-ahead 0).

    batch_cutoff 없음:
        매크로 vintage_date 는 한국은행의 공표 시점 자체이므로 vintage_date <= as_of
        가 곧 재현 축 — KRX/DART 의 별도 batch_cutoff 불필요.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def fetch_latest(
        self,
        indicator_id: str,
        *,
        as_of: date,
    ) -> MacroIndicatorRecord | None:
        # vintage 이중 시간축 PIT 조회:
        #   reference_date <= as_of — 미래 기간 제외 (아직 도달하지 않은 기준 기간).
        #   vintage_date   <= as_of — as_of 시점에 알 수 있던 관측만 허용 (잠정→확정
        #       재현 축). 이후 공표된 확정치가 as_of 이전 잠정치를 "오염"하지 않음.
        # ORDER BY reference_date DESC, vintage_date DESC:
        #   reference_date DESC → 가장 최근 기준 기간 우선.
        #   vintage_date   DESC → 같은 reference_date 에 복수 vintage 가 있으면
        #       as_of 이하의 가장 늦은 vintage(=가장 최신 관측) 우선.
        # LIMIT 1 → 단일 PIT 정합 row 선택.
        stmt = (
            select(MacroIndicatorORM)
            .where(
                MacroIndicatorORM.indicator_id == indicator_id,
                MacroIndicatorORM.reference_date <= as_of,
                MacroIndicatorORM.vintage_date <= as_of,
            )
            .order_by(
                MacroIndicatorORM.reference_date.desc(),
                MacroIndicatorORM.vintage_date.desc(),
            )
            .limit(1)
        )
        row = self._session.execute(stmt).scalars().first()
        if row is None:
            return None
        return macro_indicator_orm_to_record(row)


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
