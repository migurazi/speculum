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
from collections import defaultdict
from collections.abc import Iterator, Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import Insert, and_, func, or_, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import ColumnElement

from app.db.converters import (
    corporate_action_orm_to_record,
    corporate_action_record_to_orm,
    financial_orm_to_record,
    financial_record_to_orm,
    macro_indicator_orm_to_record,
    market_cap_orm_to_record,
    market_cap_record_to_orm,
    price_orm_to_record,
    price_record_to_orm,
    stock_snapshot_orm_to_record,
    stock_snapshot_record_to_orm,
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
from app.db.orm.stock_snapshots import StockSnapshotORM
from app.db.orm.stocks_master import StocksMasterORM
from app.db.orm.treasury_shares import TreasurySharesORM
from app.repositories.batch_run_repository import BatchCutoff
from app.repositories.pit_protocols import (
    CorporateActionRecord,
    CorporateActionRepository,
    DividendRepository,
    FinancialRecord,
    FinancialRepository,
    MacroIndicatorRecord,
    MacroIndicatorRepository,
    MarketCapRecord,
    MarketCapRepository,
    PriceRecord,
    PriceRepository,
    StockMasterRecord,
    StockSnapshotRecord,
    StockSnapshotRepository,
    TreasurySharesRecord,
    TreasurySharesRepository,
)
from app.repositories.pit_resolution import (
    resolve_active_actions,
    resolve_dividends,
    resolve_financial_periods,
    resolve_latest_treasury,
)
from app.repositories.stocks_master_repository import StocksMasterRepository
from app.services.pit_enforcer import PITDataCorruptionError, PITEnforcer

__all__ = [
    "AppendOnlyViolationError",
    "SqlCorporateActionRepository",
    "SqlDividendRepository",
    "SqlFinancialRepository",
    "SqlMacroIndicatorRepository",
    "SqlMarketCapRepository",
    "SqlPriceRepository",
    "SqlStockSnapshotRepository",
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


# SQLite 는 SQLITE_MAX_VARIABLE_NUMBER(구버전 999) 제한이 있어 `code IN (...)` 의
# 바인드 수를 넘으면 OperationalError. universe(~2500) bulk fetch 시 code 리스트를
# 청크로 쪼개 쿼리한다 — round-trip 은 N(종목) → ceil(N/CHUNK) 로 여전히 대폭 축소.
# 500 은 999 한계 + 여유(다른 바인드 파라미터 공간) 확보.
_SQL_IN_CHUNK: int = 500


def _chunked(items: Sequence[str], size: int) -> Iterator[list[str]]:
    """`items` 를 `size` 개씩 끊어 yield (마지막 청크는 잔여). 빈 입력이면 0회."""
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def _orm_insert_values(orm: object) -> dict[str, object]:
    """ORM instance → Core insert 의 values dict (전 컬럼 빠짐없이).

    M2 — Core insert 는 ORM 변환 단계(`*_record_to_orm`)가 채우던 컬럼 default/
    매핑을 자동 적용하지 않으므로, converter 가 산출한 ORM instance 의 **모든
    매핑 컬럼**을 읽어 그대로 values 에 옮긴다. mapper 의 column attr key 를
    순회 → converter 가 채운 값과 1:1. 컬럼 추가/변경 시 자동 추종(drift 차단).
    """
    mapper = sa_inspect(type(orm))
    return {
        col.key: getattr(orm, col.key) for col in mapper.column_attrs  # type: ignore[union-attr]
    }


def _insert_on_conflict_do_nothing(
    session: Session,
    orm_cls: type,
    *,
    index_elements: tuple[str, ...],
) -> Insert:
    """dialect-aware INSERT ... ON CONFLICT DO NOTHING 빌더.

    M1 — PG/SQLite 는 동일 의미(`on_conflict_do_nothing(index_elements=...)`)를
    제공하나 dialect 별 `insert` import 경로가 달라 bind dialect 로 분기한다.
    conflict target 은 호출부가 PK 컬럼(`index_elements`)을 명시 — surrogate id
    UNIQUE 가 아니라 PK 가 idempotency 의 충돌 축이다(first-wins). 그 외 dialect
    (운영 PG / 테스트 SQLite 외)는 명시적 raise (silent 잘못된 동작 차단).
    """
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return pg_insert(orm_cls).on_conflict_do_nothing(
            index_elements=list(index_elements),
        )
    if dialect == "sqlite":
        return sqlite_insert(orm_cls).on_conflict_do_nothing(
            index_elements=list(index_elements),
        )
    raise NotImplementedError(
        f"ON CONFLICT DO NOTHING 미지원 dialect: {dialect!r} "
        "(PostgreSQL / SQLite 만 지원)"
    )


# =============================================================================
# Price — KRX 가격, supersede 없음
# =============================================================================

class SqlPriceRepository(PriceRepository):
    """SQLAlchemy 기반 가격 시계열 repository.

    KRX 가격은 정정 안 됨 → supersede 무관. SQL 단순 범위 scan + 결정적 정렬.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        # M5_PLAN #1 — fetch 반환 record 의 사후 PIT regression assert 용. SQL WHERE
        # (effective_date <= as_of) 를 대체하지 않고 defense-in-depth 한 겹.
        self._enforcer = PITEnforcer()

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
        records = tuple(price_orm_to_record(o) for o in result.scalars().all())
        # M5_PLAN #1 — 사후 regression assert (defense-in-depth). 위 SQL WHERE 의
        # effective_date <= as_of 1차 필터를 *대체하지 않고* 반환 직전 한 번 더
        # 검사. 코드 변경으로 WHERE 가 회귀하면 LookAheadError 로 fail-loud.
        # 정상 데이터에선 SQL 이 이미 걸렀으므로 항상 통과 (거짓양성 0 전제).
        self._enforcer.assert_no_lookahead(records, as_of)
        return records

    def fetch_prices_bulk(
        self,
        codes: Sequence[str],
        *,
        as_of: date,
        start: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> dict[str, tuple[PriceRecord, ...]]:
        """여러 code 의 `[start, as_of]` 가격 시계열을 1회(청크) bulk fetch.

        screen/backtest universe 루프의 per-code N+1 제거용 (CachingPriceRepository
        prime). 각 code 의 반환은 `fetch_prices(code, ...)` 를 N 회 호출한 것과
        **byte-동일** — 동일 WHERE(effective_date 범위 + 동일 batch_cutoff 술어),
        동일 정렬(code 내 effective_date asc). 누락 code 는 빈 tuple.

        결정성: ORDER BY (code, effective_date) asc — 단건 fetch 의 effective_date
        asc 와 code 내 순서 동일. 사후 PIT regression assert 는 전체 반환 record 를
        1회 검사(단건 경로와 동일 defense-in-depth, effective_date<=as_of).
        """
        if start > as_of:
            return {c: () for c in codes}
        grouped: dict[str, list[PriceRecord]] = defaultdict(list)
        for chunk in _chunked(list(dict.fromkeys(codes)), _SQL_IN_CHUNK):
            stmt = (
                select(PriceDailyORM)
                .where(
                    PriceDailyORM.code.in_(chunk),
                    PriceDailyORM.effective_date >= start,
                    PriceDailyORM.effective_date <= as_of,
                )
                .order_by(
                    PriceDailyORM.code.asc(),
                    PriceDailyORM.effective_date.asc(),
                )
            )
            if batch_cutoff is not None:
                stmt = stmt.where(
                    _batch_cutoff_predicate(
                        PriceDailyORM.citation_id, source="KRX", cutoff=batch_cutoff,
                    )
                )
            for o in self._session.execute(stmt).scalars().all():
                grouped[o.code].append(price_orm_to_record(o))
        # 사후 regression assert — 전체 record 1회(단건 fetch 가 각 호출마다 한 것과
        # 합집합 동일). 정상 데이터에선 SQL WHERE 가 이미 걸러 항상 통과.
        self._enforcer.assert_no_lookahead(
            [r for recs in grouped.values() for r in recs], as_of,
        )
        return {c: tuple(grouped.get(c, ())) for c in dict.fromkeys(codes)}

    def fetch_codes_with_prices(
        self,
        codes: Sequence[str],
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> set[str]:
        """`as_of` 이하 가격이 1건이라도 있는 code 집합 (full-range 존재 판정).

        `_select_portfolio` 의 survivorship 측정(가격 누락 종목 수, ADR-0027 D3)용 —
        종목별 `fetch_prices(start=date.min)` 의 "빈 시계열 여부" 판정을 1회 bulk
        DISTINCT 쿼리로 대체. 반환 정보는 동일(존재 bool) 이라 missing_price_count·
        equity_curve 에 영향 없음(oracle 설계검토 A2). bounded prime 캐시로는 답할
        수 없어(window 밖 과거 데이터 누락) full-range 별도 쿼리.
        """
        found: set[str] = set()
        for chunk in _chunked(list(dict.fromkeys(codes)), _SQL_IN_CHUNK):
            stmt = (
                select(PriceDailyORM.code)
                .where(
                    PriceDailyORM.code.in_(chunk),
                    PriceDailyORM.effective_date <= as_of,
                )
                .distinct()
            )
            if batch_cutoff is not None:
                stmt = stmt.where(
                    _batch_cutoff_predicate(
                        PriceDailyORM.citation_id, source="KRX", cutoff=batch_cutoff,
                    )
                )
            found.update(self._session.execute(stmt).scalars().all())
        return found

    def save_prices(self, records: Sequence[PriceRecord]) -> None:
        """T18 합류 — bulk insert (ON CONFLICT DO NOTHING idempotent, first-wins).

        같은 (code, effective_date) PK 가 이미 있으면 **조용히 skip** (기존 row
        보존). KRX 일배치 재실행/retry 가 같은 영업일을 다시 적재해도 PK 충돌
        IntegrityError 가 나지 않아 재실행 안전. §2.10 byte-동일: KRX 가격은 정정이
        없어 같은 (code, date) = 같은 값이므로 first-wins(첫 row 보존)와
        last-wins(덮어쓰기)의 관측 결과가 동일 — DO NOTHING 으로 첫 row 를 보존해도
        값이 변하지 않는다 (FakePriceRepository.save_prices 와 대칭).

        conflict target 은 PK (code, effective_date) 로 명시 — surrogate `id`
        UNIQUE 가 아니라 PK 가 충돌 축이다 (id 는 매번 새 uuid4 라 같은 (code,date)
        재insert 도 id 는 다름; id UNIQUE 위반은 별개로 여전히 raise).
        """
        if not records:
            return  # 빈 values insert 회피 (no-op).
        # M1 — dialect-aware ON CONFLICT DO NOTHING. PG/SQLite 모두
        # on_conflict_do_nothing(index_elements=PK) 지원하나 import 경로가 다르다.
        # converter 가 채우던 전 컬럼을 빠짐없이 매핑하기 위해 record_to_orm 의
        # __dict__ 산출값을 재사용 (M2 — 컬럼 drift 차단).
        values = [_orm_insert_values(price_record_to_orm(r)) for r in records]
        stmt = _insert_on_conflict_do_nothing(
            self._session, PriceDailyORM,
            index_elements=("code", "effective_date"),
        ).values(values)
        self._session.execute(stmt)
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
        # M5_PLAN #1 — fetch 반환 record 의 사후 PIT regression assert 용
        # (SqlPriceRepository 와 동일 defense-in-depth).
        self._enforcer = PITEnforcer()

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
        record = market_cap_orm_to_record(row)
        # M5_PLAN #1 — 단건 반환 직전 사후 regression assert (defense-in-depth).
        # 위 SQL WHERE 의 effective_date <= as_of 를 대체하지 않고 한 겹 더 검사.
        # None 은 검사 대상 없음이므로 skip. 정상 데이터에선 항상 통과.
        self._enforcer.assert_no_lookahead((record,), as_of)
        return record

    def fetch_latest_bulk(
        self,
        codes: Sequence[str],
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> dict[str, MarketCapRecord]:
        """여러 code 의 latest market_cap(<=as_of)을 1회(청크) bulk fetch.

        universe 루프의 per-code N+1 제거용 (CachingMarketCapRepository prime). 각
        code 결과는 `fetch_latest(code, ...)` 와 **동일** — 같은 후보(effective_date
        <= as_of + 동일 cutoff 술어) 에서 동일 tiebreak((effective_date, created_at,
        id) desc 최신 1건). 데이터 없는 code 는 dict 부재(caching .get → None).

        구현: window function ROW_NUMBER() PARTITION BY code 로 code 별 rn=1 만
        SELECT — 후보 전체를 Python 으로 끌어오지 않아(메모리) 단건 LIMIT 1 의 bulk
        등가물. ORDER BY 키는 fetch_latest 와 정확히 동일(결정성).
        """
        best: dict[str, MarketCapRecord] = {}
        rn = (
            func.row_number()
            .over(
                partition_by=MarketCapDailyORM.code,
                order_by=(
                    MarketCapDailyORM.effective_date.desc(),
                    MarketCapDailyORM.created_at.desc(),
                    MarketCapDailyORM.id.desc(),
                ),
            )
            .label("rn")
        )
        for chunk in _chunked(list(dict.fromkeys(codes)), _SQL_IN_CHUNK):
            inner = select(MarketCapDailyORM, rn).where(
                MarketCapDailyORM.code.in_(chunk),
                MarketCapDailyORM.effective_date <= as_of,
            )
            if batch_cutoff is not None:
                # 후보 한정은 window 랭킹 **이전** — frozen 이하 batch row 만 순위에
                # 들어가야 단건 fetch 와 동일 latest (backfill row 제외, C#1).
                inner = inner.where(
                    _batch_cutoff_predicate(
                        MarketCapDailyORM.citation_id,
                        source="KRX",
                        cutoff=batch_cutoff,
                    )
                )
            subq = inner.subquery()
            ranked = aliased(MarketCapDailyORM, subq)
            stmt = select(ranked).where(subq.c.rn == 1)
            for o in self._session.execute(stmt).scalars().all():
                rec = market_cap_orm_to_record(o)
                best[rec.code] = rec
        # 사후 regression assert — latest 들 1회(단건 경로와 동일 defense-in-depth).
        self._enforcer.assert_no_lookahead(tuple(best.values()), as_of)
        return best

    def save_market_caps(self, records: Sequence[MarketCapRecord]) -> None:
        """Phase B 합류 — bulk insert (ON CONFLICT DO NOTHING idempotent, first-wins).

        같은 (code, effective_date) PK 가 이미 있으면 조용히 skip — KRX 일배치
        재실행 안전 (SqlPriceRepository.save_prices 와 동일 정책). §2.10 byte-동일:
        KRX 시가총액도 정정이 없어 같은 (code,date)=같은 값 → first-wins/last-wins
        관측 동일 (FakeMarketCapRepository.save_market_caps 와 대칭).

        conflict target = PK (code, effective_date). surrogate `id` UNIQUE 위반은
        별개로 여전히 raise (다른 (code,date)에 같은 id 를 넣는 경우).

        각 record 의 citation_id 가 source_citations 에 미리 save 돼 있어야 FK
        만족 (배치 orchestrator 가 citation → market_cap 순서 강제).
        """
        if not records:
            return  # 빈 values insert 회피 (no-op).
        # M1/M2 — save_prices 와 동일: dialect-aware DO NOTHING + converter 산출값
        # 전 컬럼 매핑.
        values = [
            _orm_insert_values(market_cap_record_to_orm(r)) for r in records
        ]
        stmt = _insert_on_conflict_do_nothing(
            self._session, MarketCapDailyORM,
            index_elements=("code", "effective_date"),
        ).values(values)
        self._session.execute(stmt)
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
        # account/ifrs 필터 + fiscal_period chain 해소 + sort + truncate 는
        # pit_resolution 의 공유 helper (Fake·CachingFinancialRepository 와 단일
        # 출처). all_records 는 이미 cutoff-필터된 candidate(account 필터 이전,
        # 전체 vintage) — helper 계약. assert(아래)는 helper 미포함이라 호출부 책임.
        result_records = tuple(
            resolve_financial_periods(
                all_records,
                as_of,
                account=account,
                ifrs_type=ifrs_type,
                max_periods=max_periods,
                enforcer=self._enforcer,
            )
        )
        # M5_PLAN #1 — 최종 반환 record 의 사후 regression assert (defense-in-depth).
        # latest_active_by_key 가 effective_date <= as_of candidates 만 active 로
        # 해소하므로 정상 데이터에선 항상 통과. 코드 변경으로 그 불변식이 깨지면
        # LookAheadError 로 fail-loud (effective_date 축).
        self._enforcer.assert_no_lookahead(result_records, as_of)
        return result_records

    def fetch_all_financials_bulk(
        self,
        codes: Sequence[str],
        *,
        batch_cutoff: BatchCutoff | None = None,
    ) -> dict[str, tuple[FinancialRecord, ...]]:
        """여러 code 의 **전체 vintage** financial raw row 를 1회(청크) bulk fetch.

        screen/backtest universe 루프의 per-code N+1 제거용
        (CachingFinancialRepository.prime). 각 code 의 반환은 단건 fetch_financials
        가 SQL 단계에서 적재하는 candidate set(`WHERE code=:code`(+cutoff), account
        필터·해소 **이전** 전체 row)과 **동일** — `WHERE code IN (chunk)` 로만 확장.
        serve-time 에 `resolve_financial_periods` 가 단건과 동일 알고리즘으로 해소
        하므로 결과 byte-동일.

        date 필터 부재 (oracle 설계검토 C1 — load-bearing):
            단건 fetch 처럼 `effective_date <= as_of` 사전 필터를 **넣지 않는다**.
            chain integrity(`_validate_chain_integrity`)가 candidate 전체에 대해
            검증되므로 date>as_of 인 successor 를 drop 하면 depth/cycle 검사 결과가
            단건과 달라져(한쪽 PITDataCorruptionError, 한쪽 정상) §2.10 재현성이
            깨진다. financial 은 시계열이 아니라 row 수(종목당 수백)가 작아 전체
            vintage 적재의 메모리 부담이 price 시계열보다 오히려 가볍다.

        결정성: ORDER BY 불요 — serve-time 해소가 (effective_date, fiscal_period,
        id) 로 정렬·max 하므로 입력 순서에 무관(id final tiebreaker). 누락 code 는
        빈 tuple. cutoff 술어는 단건과 동일(source="DART").
        """
        grouped: dict[str, list[FinancialRecord]] = defaultdict(list)
        for chunk in _chunked(list(dict.fromkeys(codes)), _SQL_IN_CHUNK):
            stmt = select(FinancialORM).where(FinancialORM.code.in_(chunk))
            if batch_cutoff is not None:
                stmt = stmt.where(
                    _batch_cutoff_predicate(
                        FinancialORM.citation_id, source="DART", cutoff=batch_cutoff,
                    )
                )
            for o in self._session.execute(stmt).scalars().all():
                rec = financial_orm_to_record(o)
                grouped[rec.code].append(rec)
        return {c: tuple(grouped.get(c, ())) for c in dict.fromkeys(codes)}

    def fetch_active_disclosure(
        self,
        code: str,
        fiscal_period: str,
        ifrs_type: str,
    ) -> tuple[str | None, tuple[FinancialRecord, ...]]:
        """현재 active(superseded_by IS NULL) 인 (code, fiscal_period, ifrs_type)
        그룹의 (active_rcept_no, active_rows) — DART 정정공시 배치 전용.

        D1 = citation-join: financials 엔 rcept_no 컬럼이 없으므로 source_citations
        (identifier = rcept_no) 와 JOIN 하여 active row 의 rcept_no 를 함께 읽는다.
        migration 불필요(read-only SELECT). active = superseded_by IS NULL.

        active 그룹 key 에 ifrs_type 을 포함하는 것이 **load-bearing**: CFS/OFS 는
        별개 fact·별개 supersede chain 이므로, 같은 (code, fiscal_period) 에 CFS
        active 와 OFS active 가 공존한다. ifrs_type 으로 좁혀야 cross-ifrs supersede
        (절대 금지)를 구조적으로 차단한다.
        """
        # financials.citation_id → source_citations.id JOIN 으로 identifier(rcept_no)
        # 동반 SELECT. superseded_by IS NULL 인 active head 만.
        stmt = (
            select(FinancialORM, SourceCitationORM.identifier)
            .join(
                SourceCitationORM,
                FinancialORM.citation_id == SourceCitationORM.id,
            )
            .where(
                FinancialORM.code == code,
                FinancialORM.fiscal_period == fiscal_period,
                FinancialORM.ifrs_type == ifrs_type,
                FinancialORM.superseded_by.is_(None),
            )
        )
        rows: list[FinancialRecord] = []
        rcept_nos: set[str] = set()
        for orm, identifier in self._session.execute(stmt).all():
            rows.append(financial_orm_to_record(orm))
            rcept_nos.add(identifier)
        if not rows:
            # 첫 공시 직전 — active 전무.
            return (None, ())
        # 단일 rcept_no assertion — DART 1회 fetch 는 N account row 가 모두 같은
        # rcept_no 1개를 공유하므로 정상 active head 의 rcept_no 는 단일해야 한다.
        # 여러 rcept_no = 중복 active head(수동 corruption / chain 누락) → fail-loud
        # (no-UNIQUE 환경에서 silent 진행 차단, oracle Critical 방어선).
        if len(rcept_nos) != 1:
            raise PITDataCorruptionError(
                f"multiple active rcept_no for code={code} "
                f"fiscal_period={fiscal_period} ifrs_type={ifrs_type}: "
                f"{sorted(rcept_nos)} — 중복 active head (corruption)"
            )
        (active_rcept_no,) = tuple(rcept_nos)
        return (active_rcept_no, tuple(rows))

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
        # fiscal_period chain 해소 + 최신 1건 선택은 pit_resolution 공유 helper
        # (Fake·CachingTreasurySharesRepository 와 단일 출처). records 는 이미
        # cutoff-필터된 candidate(전체 vintage). assert 는 helper 미포함 — 호출부 책임.
        record = resolve_latest_treasury(records, as_of, enforcer=self._enforcer)
        if record is None:
            return None
        # M5_PLAN #1 — 단건 반환 직전 사후 regression assert (defense-in-depth).
        # latest_active_by_key 가 effective_date <= as_of candidates 만 해소하므로
        # 정상 데이터에선 항상 통과. 그 불변식 회귀 시 LookAheadError fail-loud.
        self._enforcer.assert_no_lookahead((record,), as_of)
        return record

    def fetch_all_treasury_bulk(
        self,
        codes: Sequence[str],
        *,
        batch_cutoff: BatchCutoff | None = None,
    ) -> dict[str, tuple[TreasurySharesRecord, ...]]:
        """여러 code 의 **전체 vintage** 자사주 raw row 를 1회(청크) bulk fetch.

        CachingTreasurySharesRepository.prime 용 — fetch_all_financials_bulk 와
        동형. 각 code 의 반환은 단건 fetch_latest_active 가 SQL 단계에서 적재하는
        candidate set(`WHERE code=:code`(+cutoff) 전체 row)과 동일(`IN (chunk)` 확장).
        serve-time 에 `resolve_latest_treasury` 가 단건과 동일 해소 → 결과 byte-동일.

        date 필터 부재 (oracle 설계검토 C1): financials 와 동일 이유로
        `effective_date <= as_of` 사전 필터를 넣지 않는다(chain integrity 등가성).
        """
        grouped: dict[str, list[TreasurySharesRecord]] = defaultdict(list)
        for chunk in _chunked(list(dict.fromkeys(codes)), _SQL_IN_CHUNK):
            stmt = select(TreasurySharesORM).where(
                TreasurySharesORM.code.in_(chunk)
            )
            if batch_cutoff is not None:
                stmt = stmt.where(
                    _batch_cutoff_predicate(
                        TreasurySharesORM.citation_id,
                        source="DART",
                        cutoff=batch_cutoff,
                    )
                )
            for o in self._session.execute(stmt).scalars().all():
                rec = treasury_shares_orm_to_record(o)
                grouped[rec.code].append(rec)
        return {c: tuple(grouped.get(c, ())) for c in dict.fromkeys(codes)}

    def fetch_active_treasury_disclosure(
        self,
        code: str,
        fiscal_period: str,
    ) -> tuple[str | None, TreasurySharesRecord | None]:
        """현재 active(superseded_by IS NULL) 인 (code, fiscal_period) 자사주의
        (active_rcept_no, active_row) — DART 정정공시 배치 전용.

        financials 의 fetch_active_disclosure 동형이나 **account 축이 없어** active
        row 는 최대 1건. D1 = citation-join(identifier = rcept_no, migration 없음).
        """
        stmt = (
            select(TreasurySharesORM, SourceCitationORM.identifier)
            .join(
                SourceCitationORM,
                TreasurySharesORM.citation_id == SourceCitationORM.id,
            )
            .where(
                TreasurySharesORM.code == code,
                TreasurySharesORM.fiscal_period == fiscal_period,
                TreasurySharesORM.superseded_by.is_(None),
            )
        )
        rows: list[TreasurySharesRecord] = []
        rcept_nos: set[str] = set()
        for orm, identifier in self._session.execute(stmt).all():
            rows.append(treasury_shares_orm_to_record(orm))
            rcept_nos.add(identifier)
        if not rows:
            return (None, None)
        # account 축이 없으므로 active row 는 1건이어야 한다 — 2건 이상이면 중복
        # active head(corruption) → fail-loud (financials 다중 rcept_no assertion
        # 과 동일 방어선). 같은 rcept_no 라도 row 가 2건이면 비정상이므로 row 수로
        # 판정한다(자사주는 1 group=1 row).
        if len(rows) != 1:
            raise PITDataCorruptionError(
                f"multiple active treasury rows for code={code} "
                f"fiscal_period={fiscal_period}: rcept_no={sorted(rcept_nos)} "
                f"({len(rows)} rows) — 중복 active head (corruption)"
            )
        (active_rcept_no,) = tuple(rcept_nos)
        return (active_rcept_no, rows[0])

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
        # action_types 필터 + announced_date chain 해소 + sort 는 pit_resolution
        # 공유 helper(Fake·CachingCorporateActionRepository 와 단일 출처). assert(아래)
        # 는 helper 미포함이라 호출부 책임.
        actions = tuple(
            resolve_active_actions(
                records, as_of, action_types=action_types, enforcer=self._enforcer,
            )
        )
        # M5_PLAN #1 — corporate action 의 PIT 가용성 축은 announced_date 다
        # (pit_protocols.py:583 — fetch_actions 는 "announced_date <= as_of 인 사건,
        # effective_date 무관 — 공시 가용성"). 사후 regression assert 는 그 가용성
        # 축(announced_date)만 검사 (defense-in-depth — SQL 의 filter_active_records
        # announced_date 해소를 대체하지 않고 한 겹 더).
        #
        # 주의 (거짓양성 0 — M5_PLAN Critical 리스크): effective_date 는 권리락일
        # 등 효력 발생일로 정상적으로 as_of 이후(미래)일 수 있다(공시는 됐으나 아직
        # 발효 전). 따라서 effective_date 에 사후 assert 를 걸면 정상 데이터에 거짓
        # 양성 → 운영 평가 차단(가용성 사고). announced_date 만 검사하여 회귀(공시
        # 가용성 위반)는 fail-loud 하되 미래 effective_date 정상 사건은 통과시킨다.
        self._enforcer.assert_no_lookahead(
            actions, as_of, date_of=lambda r: r.announced_date,
        )
        return actions

    def fetch_all_actions_bulk(
        self,
        codes: Sequence[str],
    ) -> dict[str, tuple[CorporateActionRecord, ...]]:
        """여러 code 의 **전체 vintage** corporate action raw 를 1회(청크) bulk fetch.

        backtest 보유수익률(`_portfolio_return`)의 per-code N+1 제거용
        (CachingCorporateActionRepository.prime). 각 code 의 반환은 단건
        fetch_actions 가 SQL 단계에서 적재하는 candidate set(`WHERE code=:code`,
        action_types·해소 **이전** 전체 row)과 동일 — `WHERE code IN (chunk)` 확장.
        serve-time 에 `resolve_active_actions` 가 단건과 동일 알고리즘으로 해소(결과
        byte-동일). fetch_actions 는 batch_cutoff 가 없어(CA cutoff freeze 불가,
        ADR-0033 D4 상속 한계) bulk 도 cutoff 무관.

        date 필터 부재: 단건 fetch 가 `WHERE code` 만 쓰고 announced_date 해소를
        Python(`filter_active_records`)에서 하므로 bulk 도 전체 vintage 적재(chain
        integrity 등가성 — financial/treasury C1 과 동일 근거). 누락 code 는 빈 tuple.
        """
        grouped: dict[str, list[CorporateActionRecord]] = defaultdict(list)
        for chunk in _chunked(list(dict.fromkeys(codes)), _SQL_IN_CHUNK):
            stmt = select(CorporateActionORM).where(
                CorporateActionORM.code.in_(chunk)
            )
            for o in self._session.execute(stmt).scalars().all():
                rec = corporate_action_orm_to_record(o)
                grouped[rec.code].append(rec)
        return {c: tuple(grouped.get(c, ())) for c in dict.fromkeys(codes)}

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
# Dividend — cash_dividend 이중 PIT (announced 가용성 축 + effective 발생 축)
# =============================================================================

class SqlDividendRepository(DividendRepository):
    """SQLAlchemy 기반 cash_dividend repository — 이중 PIT.

    이중 PIT 설계 근거는 DividendRepository Protocol docstring(pit_protocols.py) 참조.

    announced_date 축: SQL 로 code 만 fetch(action_type 필터 X) 후
    PITEnforcer.filter_active_records(date_of=announced_date)가 **전체 chain** 해소.
    chain 해소 후 action_type=="cash_dividend" 필터 — fetch_actions(action_types=
    {"cash_dividend"}) 와 byte-동일 (cross-action_type chain truncation 방지).
    effective_date 축: cash_dividend 필터 결과에 effective_date <= as_of 추가 필터.
    M5 defense-in-depth: 반환 직전 assert_no_lookahead(announced_date 축만).
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._enforcer = PITEnforcer()

    def fetch_dividends(
        self,
        code: str,
        *,
        as_of: date,
    ) -> Sequence[CorporateActionRecord]:
        # SQL 로 code 만 1차 필터 (action_type 필터 제거 — chain truncation 방지).
        # cash_dividend(orig) 가 다른 action_type(split 등) successor 로 superseded
        # 되는 cross-action_type chain 에서, 만약 여기서 action_type 을 좁히면
        # successor 가 record_by_id 에 부재 → _is_active_one_hop 가 orig 을 보수적
        # active 복원 → superseded 된 orig 부활 오판(§2.4) + fetch_actions 의 corruption
        # 검출과 비대칭(§2.10 hole). 따라서 전체 chain 을 받은 뒤 chain 해소 후 필터.
        stmt = select(CorporateActionORM).where(
            CorporateActionORM.code == code,
        )
        result = self._session.execute(stmt)
        records = [
            corporate_action_orm_to_record(o) for o in result.scalars().all()
        ]

        # announced 축 chain 해소 → cash_dividend 필터 → effective<=as_of 필터 →
        # 결정성 sort 는 pit_resolution 공유 helper(Fake·CachingDividendRepository 와
        # 단일 출처). assert(아래)는 helper 미포함이라 호출부 책임.
        dividends = tuple(
            resolve_dividends(records, as_of, enforcer=self._enforcer)
        )

        # M5_PLAN #1 — defense-in-depth: announced_date 축만 사후 assert.
        # effective_date 는 as_of 이후가 정상(공시됐으나 미발생)이므로 검사 대상 외.
        # fetch_dividends 에서는 이미 effective ≤ as_of 필터 적용 후지만, announced
        # 축 회귀 차단을 위해 SqlCorporateActionRepository 와 동일 패턴 유지.
        self._enforcer.assert_no_lookahead(
            dividends, as_of, date_of=lambda r: r.announced_date,
        )
        return dividends

    def fetch_all_dividends_bulk(
        self,
        codes: Sequence[str],
    ) -> dict[str, tuple[CorporateActionRecord, ...]]:
        """여러 code 의 **전체 vintage** corporate action raw 를 1회(청크) bulk fetch.

        dividend(total return §2.4)의 per-code N+1(`fetch_dividends`) 제거용
        (CachingDividendRepository.prime). 각 code 의 반환은 단건 fetch_dividends 가
        SQL 단계에서 적재하는 candidate set(`WHERE code=:code`, action_type·해소
        **이전** 전체 corporate_action row — cash_dividend 로 좁히지 **않음**)과 동일
        — `WHERE code IN (chunk)` 확장. serve-time 에 `resolve_dividends` 가 단건과
        동일 알고리즘으로 해소(announced chain → cash_dividend 필터 → effective<=as_of
        필터)하여 결과 byte-동일. fetch_dividends 는 batch_cutoff 가 없어(CA/dividend
        cutoff freeze 불가, ADR-0033 D4 상속 한계) bulk 도 cutoff 무관.

        date·action_type 필터 부재 (chain truncation 방지 — C1 동일 근거): 단건 fetch
        가 `WHERE code` 만 쓰고 announced chain 해소·cash_dividend 필터를 Python 에서
        하므로(cross-action_type chain 부활 오판 방지), bulk 도 전체 vintage(action_
        type 무관)를 적재한다. fetch_all_actions_bulk 와 동일 구조. 누락 code 는 빈 tuple.
        """
        grouped: dict[str, list[CorporateActionRecord]] = defaultdict(list)
        for chunk in _chunked(list(dict.fromkeys(codes)), _SQL_IN_CHUNK):
            stmt = select(CorporateActionORM).where(
                CorporateActionORM.code.in_(chunk)
            )
            for o in self._session.execute(stmt).scalars().all():
                rec = corporate_action_orm_to_record(o)
                grouped[rec.code].append(rec)
        return {c: tuple(grouped.get(c, ())) for c in dict.fromkeys(codes)}

    def save_dividends(
        self, records: Sequence[CorporateActionRecord],
    ) -> None:
        """cash_dividend bulk insert (append-only).

        - action_type != "cash_dividend" 인 record 는 방어적 ValueError.
        - insert 시 superseded_by 가 non-NULL 이면 ValueError — 정정공시 supersede 는
          INSERT 가 아닌 `update_superseded_by` 로 수행한다. self-FK 순서 의존
          (successor-first insert) 제거를 위해 insert 는 항상 superseded_by=NULL.
          (financials / treasury_shares / corporate_action 패턴과 동일.)
        - batch 전체 검증 후 add (atomic — 부분 add 후 raise 금지).

        SqlTreasurySharesRepository.save_treasury_shares + update_superseded_by 패턴.
        """
        # 전체 batch 를 먼저 검증 — 위반 시 어떤 record 도 add 하지 않음 (atomic).
        for r in records:
            if r.action_type != "cash_dividend":
                raise ValueError(
                    f"DividendRepository 는 cash_dividend 만 허용 — "
                    f"record {r.id} 의 action_type={r.action_type!r}"
                )
            if r.superseded_by is not None:
                raise ValueError(
                    f"insert 시 superseded_by 금지 — record {r.id} 의 "
                    f"superseded_by={r.superseded_by!r}. 정정공시 supersede 는 "
                    f"update_superseded_by 를 사용"
                )
        # 검증 통과 후에만 add.
        for r in records:
            self._session.add(corporate_action_record_to_orm(r))
        self._session.flush()

    def update_superseded_by(
        self, record_id: UUID, successor_id: UUID,
    ) -> None:
        """정정공시 chain — cash_dividend 원 row 의 superseded_by 를 NULL→successor set.

        ADR-0020 D4 — cash_dividend 의 유일 허용 UPDATE 경로
        (SqlCorporateActionRepository.update_superseded_by 와 동일 불변식). cash_dividend
        대상만 허용 — 대상이 다른 action_type 이면 not-found 와 동일 취급
        (AppendOnlyViolationError). save_dividends 가 insert 시 superseded_by=NULL 만
        허용하므로 정정공시 처리는 (정정 row insert + 본 메서드) 2 단계, insert 순서
        무관.

        Raises:
            AppendOnlyViolationError: 대상/successor 부재 또는 이미 superseded.
        """
        target = self._session.get(CorporateActionORM, record_id)
        if target is None or target.action_type != "cash_dividend":
            raise AppendOnlyViolationError(
                f"cash_dividend row {record_id} 부재 — update_superseded_by 불가"
            )
        if target.superseded_by is not None:
            raise AppendOnlyViolationError(
                f"cash_dividend row {record_id} 는 이미 superseded "
                f"(superseded_by={target.superseded_by}) — chain 1 회성 위반 "
                f"(ADR-0020 D1)"
            )
        successor = self._session.get(CorporateActionORM, successor_id)
        if successor is None:
            raise AppendOnlyViolationError(
                f"successor cash_dividend row {successor_id} 부재 — "
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
        # M5_PLAN #1 — fetch 반환 record 의 사후 PIT regression assert 용. macro 는
        # 이중 시간축이라 assert_no_vintage_lookahead (reference/vintage 둘 다 검사).
        self._enforcer = PITEnforcer()

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
        record = macro_indicator_orm_to_record(row)
        # M5_PLAN #1 — 단건 반환 직전 사후 regression assert (defense-in-depth).
        # macro 는 이중 시간축 — 위 SQL WHERE 의 reference_date <= as_of AND
        # vintage_date <= as_of 를 대체하지 않고 두 축을 한 번 더 검사. 어느 축이든
        # 회귀하면 LookAheadError 로 fail-loud. 정상 데이터에선 항상 통과.
        self._enforcer.assert_no_vintage_lookahead((record,), as_of)
        return record


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

    def list_delisted_before(
        self, *, cutoff: date,
    ) -> Sequence[StockMasterRecord]:
        # survivorship backfill universe — delisting_date IS NOT NULL AND <= cutoff.
        # list_active 의 보집합(폐지분). 정렬은 Fake 와 동일(delisting_date asc + id).
        stmt = (
            select(StocksMasterORM)
            .where(StocksMasterORM.delisting_date.is_not(None))
            .where(StocksMasterORM.delisting_date <= cutoff)
        )
        result = self._session.execute(stmt)
        records = [stocks_master_orm_to_record(o) for o in result.scalars().all()]
        return tuple(
            sorted(records, key=lambda r: (r.delisting_date or date.min, str(r.id)))
        )


# =============================================================================
# StockSnapshot — factor 결과 precomputed (as_of exact lookup, ADR-0002 D5)
# =============================================================================

class SqlStockSnapshotRepository(StockSnapshotRepository):
    """SQLAlchemy 기반 factor 결과 precomputed snapshot repository.

    `as_of_date == as_of` exact lookup — PIT 해소가 아니라 일배치가 미리 계산한
    값의 단순 조회(StockSnapshotRecord docstring). FakeStockSnapshotRepository 와
    동일 contract. screen/market_overview 의 read 경로 배선은 §2.10 reproduce
    bypass 설계가 필요한 별도 cycle — 본 repository 는 영속 layer(read+write)만 제공.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def fetch_snapshot(
        self,
        code: str,
        *,
        as_of: date,
        factor_uuid: UUID,
    ) -> StockSnapshotRecord | None:
        # exact match — (stock_code, as_of_date, factor_uuid) PK 단건 조회.
        stmt = select(StockSnapshotORM).where(
            StockSnapshotORM.stock_code == code,
            StockSnapshotORM.as_of_date == as_of,
            StockSnapshotORM.factor_uuid == factor_uuid,
        )
        row = self._session.execute(stmt).scalars().first()
        if row is None:
            return None
        return stock_snapshot_orm_to_record(row)

    def fetch_snapshots_for_code(
        self,
        code: str,
        *,
        as_of: date,
        factor_uuids: frozenset[UUID] | None = None,
    ) -> Sequence[StockSnapshotRecord]:
        # (code, as_of) 의 여러 factor — Stock Detail 지표 카드. ix_stock_snapshots_
        # code_asof 인덱스 hot path. factor_uuids 필터는 Python(작은 set) — Fake 동형.
        stmt = select(StockSnapshotORM).where(
            StockSnapshotORM.stock_code == code,
            StockSnapshotORM.as_of_date == as_of,
        )
        records = [
            stock_snapshot_orm_to_record(o)
            for o in self._session.execute(stmt).scalars().all()
        ]
        if factor_uuids is not None:
            records = [r for r in records if r.factor_uuid in factor_uuids]
        return records

    def save_snapshots(self, records: Sequence[StockSnapshotRecord]) -> None:
        """precompute 일배치 합류 — **UPSERT** (같은 (code,as_of,factor) overwrite).

        snapshot 은 정정 후 재계산 overwrite 가 routine 인 derived cache 라 INSERT-only
        가 아니라 UPSERT (Protocol docstring, oracle M2). `session.merge` 가 composite
        PK 로 insert-or-update — dialect 무관(SQLite/PG). id 는 PK 의 deterministic
        함수라 갱신 시에도 불변(UNIQUE(id) 충돌 없음). citation_id FK 는 호출자가
        선보장(citation → snapshot 순서).
        """
        for r in records:
            self._session.merge(stock_snapshot_record_to_orm(r))
        self._session.flush()
