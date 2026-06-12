"""SQL bulk financial/treasury contract test — bulk+serve == N×single (N+1 완화).

`SqlFinancialRepository.fetch_all_financials_bulk` /
`SqlTreasurySharesRepository.fetch_all_treasury_bulk` 가 적재한 raw vintage 를
serve-time(CachingFinancialRepository/CachingTreasurySharesRepository)에 해소한
결과가 단건 fetch 를 N 회 호출한 것과 **byte-동일** 한지 검증. 깨지면 screen/backtest
prime 경로가 단건과 다른 결과 → §2.10 재현성 위반(가장 심각).

핵심 검증(oracle 설계검토 C1): bulk 는 date 사전필터 없이 전체 vintage 를 적재하고
chain integrity·정정 chain 해소가 단건과 동일하게 동작.

테스트 매트릭스:
    1. financial: Caching(Sql) == Sql 단건 (다종목·다account·다period).
    2. financial: 정정 chain — bulk+serve == 단건 (후속 row 반환).
    3. financial: fetch_all_financials_bulk 누락 code → 빈 tuple.
    4. financial: fetch_all_financials_bulk 전체 vintage 적재(정정 옛 row 포함).
    5. financial: cutoff 주입 — bulk+serve == 단건 (frozen 이하 candidate 한정).
    6. financial: EXCLUDE_ALL_CUTOFF → 전부 빈 (재현성 sentinel 정합).
    7. treasury: Caching(Sql) == Sql 단건 (다종목).
    8. treasury: 정정 chain — bulk+serve == 단건.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.converters import (
    citation_record_to_orm,
    corporate_action_record_to_orm,
    financial_record_to_orm,
    treasury_shares_record_to_orm,
)
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.db.orm.source_citations import SourceCitationORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.batch_run_repository import (
    EXCLUDE_ALL_CUTOFF,
    BatchCutoff,
)
from app.repositories.caching_repositories import (
    CachingCorporateActionRepository,
    CachingDividendRepository,
    CachingFinancialRepository,
    CachingTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    CorporateActionRecord,
    FinancialRecord,
    TreasurySharesRecord,
)
from app.repositories.sql_repositories import (
    SqlCorporateActionRepository,
    SqlDividendRepository,
    SqlFinancialRepository,
    SqlTreasurySharesRepository,
)

_LINEAGE: UUID = UUID("00000000-0000-0000-0000-0000000000aa")
_CITATION: UUID = UUID("00000000-0000-0000-0000-00000000ffff")
_BATCH: UUID = UUID("00000000-0000-0000-0000-0000000000bb")
_AS_OF = date(2024, 6, 1)


def _seed_dart_fk(session: Session) -> None:
    """DART citation_id FK + batch_id FK 만족용 dummy row (cutoff source='DART')."""
    if session.get(BatchRunORM, _BATCH) is None:
        session.add(
            BatchRunORM(
                id=_BATCH, market=None, source="DART",
                started_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
                ended_at=datetime(2024, 5, 20, 9, 5, tzinfo=UTC),
                success_count=1, status=BATCH_STATUS_SUCCESS,
            )
        )
    if session.get(SourceCitationORM, _CITATION) is None:
        session.add(citation_record_to_orm(SourceCitation(
            id=_CITATION,
            source=SourceKind.DART,
            identifier="20240520000001",
            retrieved_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
            effective_date=date(2024, 5, 1),
            adapter_version="1.0.0",
            batch_id=_BATCH,
            url=None,
            created_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
        )))
    session.flush()


def _fin(
    *,
    code: str,
    fiscal_period: str,
    account: str,
    value: str,
    effective_date: date,
    ifrs_type: str = "consolidated",
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> FinancialRecord:
    return FinancialRecord(
        id=record_id or uuid4(),
        code=code,
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        account=account,
        value=Decimal(value),
        unit="krw",
        ifrs_type=ifrs_type,
        citation_id=_CITATION,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


def _treasury(
    *,
    code: str,
    fiscal_period: str,
    effective_date: date,
    shares_treasury: int,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> TreasurySharesRecord:
    return TreasurySharesRecord(
        id=record_id or uuid4(),
        code=code,
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        shares_treasury=shares_treasury,
        effective_date_precise=False,
        citation_id=_CITATION,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


def _persist_fin(session: Session, records: list[FinancialRecord]) -> None:
    """실 ingestion 미러 — self-FK(superseded_by) 충족을 위해 먼저 비우고 INSERT 후
    set(ADR-0020 save→update_superseded_by 패턴). 순서 무관하게 chain 안전."""
    _seed_dart_fk(session)
    pending: list[tuple[UUID, UUID]] = []
    for r in records:
        orm = financial_record_to_orm(r)
        if r.superseded_by is not None:
            pending.append((r.id, r.superseded_by))
            orm.superseded_by = None
        session.add(orm)
    session.flush()
    for record_id, successor_id in pending:
        from app.db.orm.financials import FinancialORM
        session.get(FinancialORM, record_id).superseded_by = successor_id
    session.flush()


def _persist_treasury(session: Session, records: list[TreasurySharesRecord]) -> None:
    _seed_dart_fk(session)
    pending: list[tuple[UUID, UUID]] = []
    for r in records:
        orm = treasury_shares_record_to_orm(r)
        if r.superseded_by is not None:
            pending.append((r.id, r.superseded_by))
            orm.superseded_by = None
        session.add(orm)
    session.flush()
    for record_id, successor_id in pending:
        from app.db.orm.treasury_shares import TreasurySharesORM
        session.get(TreasurySharesORM, record_id).superseded_by = successor_id
    session.flush()


def _assert_fin_equal(a, b) -> None:
    """두 FinancialRecord 시퀀스가 byte-동일(id·value·정렬)인지."""
    a, b = list(a), list(b)
    assert [r.id for r in a] == [r.id for r in b]
    assert [r.value for r in a] == [r.value for r in b]
    assert [r.fiscal_period for r in a] == [r.fiscal_period for r in b]


# =============================================================================
# 1. financial: Caching(Sql) == Sql 단건
# =============================================================================

def test_financial_bulk_serve_matches_single(db_session: Session) -> None:
    records = []
    for code in ("000001", "000002", "000003"):
        records.append(_fin(
            code=code, fiscal_period="2024Q1", account="net_income",
            value="100", effective_date=date(2024, 5, 15),
        ))
        records.append(_fin(
            code=code, fiscal_period="2023Q4", account="net_income",
            value="80", effective_date=date(2024, 3, 30),
        ))
        records.append(_fin(
            code=code, fiscal_period="2024Q1", account="total_equity",
            value="5000", effective_date=date(2024, 5, 15),
        ))
    _persist_fin(db_session, records)
    sql_repo = SqlFinancialRepository(db_session)
    codes = ["000001", "000002", "000003"]

    cached = CachingFinancialRepository(sql_repo)
    cached.prime(codes, as_of=_AS_OF)

    for code in codes:
        for account in ("net_income", "total_equity"):
            _assert_fin_equal(
                cached.fetch_financials(code, as_of=_AS_OF, account=account),
                sql_repo.fetch_financials(code, as_of=_AS_OF, account=account),
            )


# =============================================================================
# 2. financial: 정정 chain — bulk+serve == 단건
# =============================================================================

def test_financial_bulk_serve_supersede_chain_matches_single(
    db_session: Session,
) -> None:
    orig_id, succ_id = uuid4(), uuid4()
    records = [
        _fin(
            code="000001", fiscal_period="2024Q1", account="net_income",
            value="100", effective_date=date(2024, 5, 15),
            record_id=orig_id, superseded_by=succ_id,
            created_at=datetime(2024, 5, 15, tzinfo=UTC),
        ),
        _fin(
            code="000001", fiscal_period="2024Q1", account="net_income",
            value="130", effective_date=date(2024, 5, 15),
            record_id=succ_id,
            created_at=datetime(2024, 5, 28, tzinfo=UTC),
        ),
    ]
    _persist_fin(db_session, records)
    sql_repo = SqlFinancialRepository(db_session)
    cached = CachingFinancialRepository(sql_repo)
    cached.prime(["000001"], as_of=_AS_OF)

    via_cache = cached.fetch_financials("000001", as_of=_AS_OF, account="net_income")
    via_single = sql_repo.fetch_financials(
        "000001", as_of=_AS_OF, account="net_income",
    )
    _assert_fin_equal(via_cache, via_single)
    # 후속 row 가 반환되는지 명시 확인.
    assert via_cache[0].id == succ_id
    assert via_cache[0].value == Decimal("130")


# =============================================================================
# 3~4. fetch_all_financials_bulk raw 적재 검증
# =============================================================================

def test_financial_bulk_raw_missing_code_empty(db_session: Session) -> None:
    _persist_fin(db_session, [
        _fin(
            code="000001", fiscal_period="2024Q1", account="net_income",
            value="100", effective_date=date(2024, 5, 15),
        ),
    ])
    sql_repo = SqlFinancialRepository(db_session)
    bulk = sql_repo.fetch_all_financials_bulk(["000001", "999999"])
    assert len(bulk["000001"]) == 1
    assert bulk["999999"] == ()


def test_financial_bulk_raw_loads_full_vintage(db_session: Session) -> None:
    """정정 옛 row(superseded)도 raw 에 포함 — date 사전필터 없음(C1)."""
    orig_id, succ_id = uuid4(), uuid4()
    _persist_fin(db_session, [
        _fin(
            code="000001", fiscal_period="2024Q1", account="net_income",
            value="100", effective_date=date(2024, 5, 15),
            record_id=orig_id, superseded_by=succ_id,
        ),
        _fin(
            code="000001", fiscal_period="2024Q1", account="net_income",
            value="130", effective_date=date(2024, 5, 15), record_id=succ_id,
        ),
    ])
    sql_repo = SqlFinancialRepository(db_session)
    raw = sql_repo.fetch_all_financials_bulk(["000001"])["000001"]
    # 옛 row + 후속 row 둘 다 적재(전체 vintage).
    assert {r.id for r in raw} == {orig_id, succ_id}


# =============================================================================
# 5~6. cutoff
# =============================================================================

def test_financial_bulk_serve_cutoff_matches_single(db_session: Session) -> None:
    _persist_fin(db_session, [
        _fin(
            code="000001", fiscal_period="2024Q1", account="net_income",
            value="100", effective_date=date(2024, 5, 15),
        ),
    ])
    sql_repo = SqlFinancialRepository(db_session)
    # _BATCH(started_at 2024-05-20 09:00)를 cutoff 상한으로 — 해당 batch 가 생산한
    # row 가 통과(lexicographic (started_at, id) <=). started_at/id 둘 다 _BATCH 값.
    cutoff = BatchCutoff(
        started_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC), id=_BATCH,
    )
    cached = CachingFinancialRepository(sql_repo)
    cached.prime(["000001"], as_of=_AS_OF, batch_cutoff=cutoff)
    _assert_fin_equal(
        cached.fetch_financials(
            "000001", as_of=_AS_OF, account="net_income", batch_cutoff=cutoff,
        ),
        sql_repo.fetch_financials(
            "000001", as_of=_AS_OF, account="net_income", batch_cutoff=cutoff,
        ),
    )


def test_financial_bulk_serve_exclude_all_empty(db_session: Session) -> None:
    _persist_fin(db_session, [
        _fin(
            code="000001", fiscal_period="2024Q1", account="net_income",
            value="100", effective_date=date(2024, 5, 15),
        ),
    ])
    sql_repo = SqlFinancialRepository(db_session)
    cached = CachingFinancialRepository(sql_repo)
    cached.prime(["000001"], as_of=_AS_OF, batch_cutoff=EXCLUDE_ALL_CUTOFF)
    result = cached.fetch_financials(
        "000001", as_of=_AS_OF, account="net_income",
        batch_cutoff=EXCLUDE_ALL_CUTOFF,
    )
    assert result == ()
    # 단건도 동일하게 빈 결과.
    assert sql_repo.fetch_financials(
        "000001", as_of=_AS_OF, account="net_income",
        batch_cutoff=EXCLUDE_ALL_CUTOFF,
    ) == ()


# =============================================================================
# 7~8. treasury
# =============================================================================

def test_treasury_bulk_serve_matches_single(db_session: Session) -> None:
    records = []
    for code in ("000001", "000002"):
        records.append(_treasury(
            code=code, fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=100,
        ))
        records.append(_treasury(
            code=code, fiscal_period="2024Q1",
            effective_date=date(2024, 5, 15), shares_treasury=200,
        ))
    _persist_treasury(db_session, records)
    sql_repo = SqlTreasurySharesRepository(db_session)
    codes = ["000001", "000002"]
    cached = CachingTreasurySharesRepository(sql_repo)
    cached.prime(codes, as_of=_AS_OF)

    for code in codes:
        via_cache = cached.fetch_latest_active(code, as_of=_AS_OF)
        via_single = sql_repo.fetch_latest_active(code, as_of=_AS_OF)
        assert via_cache is not None and via_single is not None
        assert via_cache.id == via_single.id
        assert via_cache.shares_treasury == via_single.shares_treasury


def test_treasury_bulk_serve_supersede_chain_matches_single(
    db_session: Session,
) -> None:
    orig_id, succ_id = uuid4(), uuid4()
    _persist_treasury(db_session, [
        _treasury(
            code="000001", fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=100,
            record_id=orig_id, superseded_by=succ_id,
            created_at=datetime(2024, 3, 30, tzinfo=UTC),
        ),
        _treasury(
            code="000001", fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=150,
            record_id=succ_id, created_at=datetime(2024, 4, 10, tzinfo=UTC),
        ),
    ])
    sql_repo = SqlTreasurySharesRepository(db_session)
    cached = CachingTreasurySharesRepository(sql_repo)
    cached.prime(["000001"], as_of=_AS_OF)
    via_cache = cached.fetch_latest_active("000001", as_of=_AS_OF)
    via_single = sql_repo.fetch_latest_active("000001", as_of=_AS_OF)
    assert via_cache is not None and via_single is not None
    assert via_cache.id == via_single.id == succ_id
    assert via_cache.shares_treasury == 150


# =============================================================================
# 9~10. corporate_action (ⓒ holding-return bulk) — Caching(Sql) == Sql 단건
# =============================================================================

def _ca(
    *,
    code: str,
    action_type: str,
    announced: date,
    effective: date,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
) -> CorporateActionRecord:
    return CorporateActionRecord(
        id=record_id or uuid4(),
        code=code,
        code_lineage_id=_LINEAGE,
        action_type=action_type,
        announced_date=announced,
        effective_date=effective,
        payment_date=None,
        ratio=Decimal("2"),
        cash_amount=None,
        details={},
        citation_id=_CITATION,
        superseded_by=superseded_by,
        created_at=datetime(announced.year, announced.month, announced.day,
                            9, 0, tzinfo=UTC),
    )


def _persist_ca(session: Session, records: list[CorporateActionRecord]) -> None:
    _seed_dart_fk(session)
    pending: list[tuple[UUID, UUID]] = []
    for r in records:
        orm = corporate_action_record_to_orm(r)
        if r.superseded_by is not None:
            pending.append((r.id, r.superseded_by))
            orm.superseded_by = None
        session.add(orm)
    session.flush()
    for record_id, successor_id in pending:
        from app.db.orm.corporate_actions import CorporateActionORM
        session.get(CorporateActionORM, record_id).superseded_by = successor_id
    session.flush()


def test_corporate_action_bulk_serve_matches_single(db_session: Session) -> None:
    records = []
    for code in ("000001", "000002"):
        records.append(_ca(
            code=code, action_type="split",
            announced=date(2024, 3, 1), effective=date(2024, 3, 15),
        ))
        records.append(_ca(
            code=code, action_type="cash_dividend",
            announced=date(2024, 4, 1), effective=date(2024, 4, 10),
        ))
    _persist_ca(db_session, records)
    sql_repo = SqlCorporateActionRepository(db_session)
    codes = ["000001", "000002"]
    cached = CachingCorporateActionRepository(sql_repo)
    cached.prime(codes, as_of=_AS_OF)
    for code in codes:
        via_cache = list(cached.fetch_actions(code, as_of=_AS_OF))
        via_single = list(sql_repo.fetch_actions(code, as_of=_AS_OF))
        assert [r.id for r in via_cache] == [r.id for r in via_single]
        assert [r.action_type for r in via_cache] == [
            r.action_type for r in via_single
        ]


def test_corporate_action_bulk_raw_full_vintage_and_supersede(
    db_session: Session,
) -> None:
    """raw bulk 는 정정 옛 row 포함(전체 vintage), serve 는 후속만(byte-동일)."""
    orig_id, succ_id = uuid4(), uuid4()
    _persist_ca(db_session, [
        _ca(
            code="000001", action_type="split",
            announced=date(2024, 3, 1), effective=date(2024, 3, 15),
            record_id=orig_id, superseded_by=succ_id,
        ),
        _ca(
            code="000001", action_type="split",
            announced=date(2024, 3, 5), effective=date(2024, 3, 20),
            record_id=succ_id,
        ),
    ])
    sql_repo = SqlCorporateActionRepository(db_session)
    raw = sql_repo.fetch_all_actions_bulk(["000001"])["000001"]
    assert {r.id for r in raw} == {orig_id, succ_id}  # 전체 vintage 적재.
    assert sql_repo.fetch_all_actions_bulk(["999999"])["999999"] == ()
    # serve 는 단건과 동일(후속만 active).
    cached = CachingCorporateActionRepository(sql_repo)
    cached.prime(["000001"], as_of=_AS_OF)
    via_cache = list(cached.fetch_actions("000001", as_of=_AS_OF))
    via_single = list(sql_repo.fetch_actions("000001", as_of=_AS_OF))
    assert [r.id for r in via_cache] == [r.id for r in via_single]
    assert len(via_cache) == 1 and via_cache[0].id == succ_id


# =============================================================================
# 11~12. dividend (dual-PIT bulk) — Caching(Sql) == Sql 단건
# =============================================================================

def test_dividend_bulk_serve_matches_single(db_session: Session) -> None:
    """다종목 cash_dividend(+무관 split): Caching(Sql) == Sql 단건 (byte-동일)."""
    records = []
    for code in ("000001", "000002"):
        records.append(_ca(
            code=code, action_type="cash_dividend",
            announced=date(2024, 4, 1), effective=date(2024, 4, 10),
        ))
        # split 은 dividend serve 결과에서 제외되어야(cash_dividend 필터).
        records.append(_ca(
            code=code, action_type="split",
            announced=date(2024, 3, 1), effective=date(2024, 3, 15),
        ))
    _persist_ca(db_session, records)
    sql_repo = SqlDividendRepository(db_session)
    codes = ["000001", "000002"]
    cached = CachingDividendRepository(sql_repo)
    cached.prime(codes, as_of=_AS_OF)
    for code in codes:
        via_cache = list(cached.fetch_dividends(code, as_of=_AS_OF))
        via_single = list(sql_repo.fetch_dividends(code, as_of=_AS_OF))
        assert [r.id for r in via_cache] == [r.id for r in via_single]
        assert [r.action_type for r in via_cache] == [
            r.action_type for r in via_single
        ]
        # cash_dividend 만(split 제외) — dual-PIT 두 축 통과 1건.
        assert len(via_cache) == 1
        assert via_cache[0].action_type == "cash_dividend"


def test_dividend_bulk_raw_full_vintage_dual_pit(db_session: Session) -> None:
    """raw bulk 는 전체 corporate_action(split·superseded 포함), serve 는 dual-PIT 해소.

    effective_date 미래(배당락 미발생) cash_dividend 는 serve 에서 제외 — 단건과 동일.
    """
    orig_id, succ_id = uuid4(), uuid4()
    _persist_ca(db_session, [
        # cash_dividend(orig) → cash_dividend(succ) supersede chain.
        _ca(
            code="000001", action_type="cash_dividend",
            announced=date(2024, 4, 1), effective=date(2024, 4, 10),
            record_id=orig_id, superseded_by=succ_id,
        ),
        _ca(
            code="000001", action_type="cash_dividend",
            announced=date(2024, 4, 15), effective=date(2024, 4, 20),
            record_id=succ_id,
        ),
        # split — raw 에는 있으나 serve 에서 cash_dividend 필터로 제외.
        _ca(
            code="000001", action_type="split",
            announced=date(2024, 3, 1), effective=date(2024, 3, 15),
        ),
        # effective 미래 cash_dividend — serve 에서 effective 축으로 제외.
        _ca(
            code="000001", action_type="cash_dividend",
            announced=date(2024, 5, 1), effective=date(2024, 7, 1),
        ),
    ])
    sql_repo = SqlDividendRepository(db_session)
    raw = sql_repo.fetch_all_dividends_bulk(["000001"])["000001"]
    # raw 는 전체 vintage(split·superseded·미래 effective 모두 적재) — 4건.
    assert len(raw) == 4
    assert sql_repo.fetch_all_dividends_bulk(["999999"])["999999"] == ()
    # serve 는 단건과 동일 — superseded orig·split·미래 effective 모두 제외, succ 만.
    cached = CachingDividendRepository(sql_repo)
    cached.prime(["000001"], as_of=_AS_OF)
    via_cache = list(cached.fetch_dividends("000001", as_of=_AS_OF))
    via_single = list(sql_repo.fetch_dividends("000001", as_of=_AS_OF))
    assert [r.id for r in via_cache] == [r.id for r in via_single]
    assert len(via_cache) == 1 and via_cache[0].id == succ_id
