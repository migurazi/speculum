"""T48c — Screen Run 재현 필터 (Reproducibility) 회귀 테스트.

work-order m1-t48c-reproduction.md (Momus rev2 OKAY) 의 필수 6 종 회귀 + 하위호환
+ reproduce_run end-to-end. SQL (in-memory SQLite 실 citation→batch_runs join) +
Fake (citation_runs 주입) 양쪽 동일 시나리오로 contract 동등성 검증.

검증 매트릭스 (spec §회귀 테스트):
1. financial supersede (load-bearing) — frozen=batch1 → batch2 정정 제외 → 보수
   분기 active 복원 → 원 값 byte-동일. 대조 (cutoff 없음) = 정정 값.
2. started_at tie (C#2) — batch1·batch2 동일 started_at, id 다름. freeze 가 id
   DESC 로 고른 batch 와 **정확히 동일 집합**만 통과 (단순 started_at 비교면 실패).
3. KRX backfill (C#1) — market_cap 과거 영업일 row 가 frozen 이후 batch → 제외.
4. source mismatch (H#3) — financial 에 KRX cutoff 의미 batch citation → 제외.
5. 빈 batch_id (H#5) — EXCLUDE_ALL → financial/treasury 전부 제외 (빈).
6. NoActiveRecordError 흡수 (M#6) — cutoff 로 빈 candidates → N/A (예외 아님).
7. 하위호환 — cutoff=None 기존 동작 보존.
8. reproduce_run end-to-end — 저장 run 재현 byte-동일 (matches=True).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.converters import (
    financial_record_to_orm,
    market_cap_record_to_orm,
    treasury_shares_record_to_orm,
)
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.batch_run_repository import (
    EXCLUDE_ALL_CUTOFF,
    BatchCutoff,
    CitationBatch,
)
from app.repositories.citation_repository import SqlCitationRepository
from app.repositories.fakes import (
    FakeFinancialRepository,
    FakeMarketCapRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    FinancialRecord,
    MarketCapRecord,
    TreasurySharesRecord,
)
from app.repositories.sql_repositories import (
    SqlFinancialRepository,
    SqlMarketCapRepository,
    SqlTreasurySharesRepository,
)
from app.schemas.screen import ConditionIn, OpEnum

_CODE: Final[str] = "005930"
_LINEAGE: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000aa")
_ACCOUNT: Final[str] = "net_income_attributable_to_owners"
_AS_OF: Final[date] = date(2024, 6, 30)


# =============================================================================
# Builders — batch_runs / citation / fact (SQL 시드 + Fake citation_runs)
# =============================================================================

def _batch_uuid(n: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-0000000000{n:02d}")


def _citation_uuid(n: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-0000000001{n:02d}")


def _make_batch_orm(
    *,
    batch_id: UUID,
    source: str,
    started_at: datetime,
) -> BatchRunORM:
    return BatchRunORM(
        id=batch_id,
        market="KOSPI" if source == "KRX" else None,
        source=source,
        started_at=started_at,
        ended_at=started_at,
        success_count=1,
        status=BATCH_STATUS_SUCCESS,
    )


def _make_citation(
    *,
    cid: UUID,
    batch_id: UUID,
    source: SourceKind,
    effective_date: date = date(2024, 5, 1),
) -> SourceCitation:
    return SourceCitation(
        id=cid,
        source=source,
        identifier=f"id-{cid.int % 100000:05d}",
        retrieved_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
        effective_date=effective_date,
        adapter_version="1.0.0",
        batch_id=batch_id,
        url=None,
        created_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
    )


def _fin(
    *,
    fiscal_period: str,
    value: str,
    effective_date: date,
    citation_id: UUID,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> FinancialRecord:
    return FinancialRecord(
        id=record_id or uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        account=_ACCOUNT,
        value=Decimal(value),
        unit="krw",
        ifrs_type="consolidated",
        citation_id=citation_id,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 5, 1, tzinfo=UTC),
    )


def _mc(
    *,
    effective_date: date,
    market_cap: str,
    citation_id: UUID,
    record_id: UUID | None = None,
) -> MarketCapRecord:
    return MarketCapRecord(
        id=record_id or uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        market_cap=Decimal(market_cap),
        shares_outstanding=1_000_000,
        shares_treasury=None,
        citation_id=citation_id,
        created_at=datetime(
            effective_date.year, effective_date.month, effective_date.day,
            17, 0, tzinfo=UTC,
        ),
    )


def _treasury(
    *,
    fiscal_period: str,
    shares: int,
    effective_date: date,
    citation_id: UUID,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> TreasurySharesRecord:
    return TreasurySharesRecord(
        id=record_id or uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        shares_treasury=shares,
        citation_id=citation_id,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 5, 1, tzinfo=UTC),
    )


def _seed_batch(session: Session, batch: BatchRunORM) -> None:
    if session.get(BatchRunORM, batch.id) is None:
        session.add(batch)
        session.flush()


def _seed_citation(session: Session, citation: SourceCitation) -> None:
    SqlCitationRepository(session).save(citation)


# =============================================================================
# 1. financial supersede (load-bearing) — SQL + Fake
# =============================================================================

def test_financial_supersede_sql_reproduces_original_value(
    db_session: Session,
) -> None:
    """frozen=batch1 → batch2 정정 제외 → 보수 분기로 원 row 복원 → 원 값.

    대조: cutoff 없으면 정정 값. SQL 실 citation→batch_runs join.
    """
    b1, b2 = _batch_uuid(1), _batch_uuid(2)
    c1, c2 = _citation_uuid(1), _citation_uuid(2)
    # batch1 = 원 공시 (오전), batch2 = 정정 (다음날) — started_at 명확히 분리.
    _seed_batch(db_session, _make_batch_orm(
        batch_id=b1, source="DART", started_at=datetime(2024, 5, 1, 9, tzinfo=UTC),
    ))
    _seed_batch(db_session, _make_batch_orm(
        batch_id=b2, source="DART", started_at=datetime(2024, 5, 10, 9, tzinfo=UTC),
    ))
    _seed_citation(db_session, _make_citation(cid=c1, batch_id=b1, source=SourceKind.DART))
    _seed_citation(db_session, _make_citation(cid=c2, batch_id=b2, source=SourceKind.DART))

    orig = _fin(
        fiscal_period="2024Q1", value="100", effective_date=date(2024, 5, 15),
        citation_id=c1,
    )
    correction = _fin(
        fiscal_period="2024Q1", value="200", effective_date=date(2024, 5, 15),
        citation_id=c2, created_at=datetime(2024, 5, 10, tzinfo=UTC),
    )
    repo = SqlFinancialRepository(db_session)
    # append-only — 두 row INSERT 후 update_superseded_by (ADR-0020 D4 유일 경로).
    repo.save_financials([orig, correction])
    repo.update_superseded_by(orig.id, correction.id)
    db_session.flush()

    cutoff = BatchCutoff(
        started_at=datetime(2024, 5, 1, 9, tzinfo=UTC), id=b1,
    )
    # 재현 (frozen=batch1) — batch2 정정 제외 → 원 값 100.
    reproduced = repo.fetch_financials(
        _CODE, as_of=_AS_OF, account=_ACCOUNT, batch_cutoff=cutoff,
    )
    assert [r.value for r in reproduced] == [Decimal("100")]
    # 대조 (cutoff 없음) — 정정 값 200.
    live = repo.fetch_financials(_CODE, as_of=_AS_OF, account=_ACCOUNT)
    assert [r.value for r in live] == [Decimal("200")]


def test_financial_supersede_fake_reproduces_original_value() -> None:
    """Fake (citation_runs 주입) — SQL 과 동일 시나리오, 동일 결과."""
    b1, b2 = _batch_uuid(1), _batch_uuid(2)
    c1, c2 = _citation_uuid(1), _citation_uuid(2)
    citation_runs = {
        c1: CitationBatch(
            started_at=datetime(2024, 5, 1, 9, tzinfo=UTC), batch_id=b1, source="DART",
        ),
        c2: CitationBatch(
            started_at=datetime(2024, 5, 10, 9, tzinfo=UTC), batch_id=b2, source="DART",
        ),
    }
    correction = _fin(
        fiscal_period="2024Q1", value="200", effective_date=date(2024, 5, 15),
        citation_id=c2,
    )
    orig = _fin(
        fiscal_period="2024Q1", value="100", effective_date=date(2024, 5, 15),
        citation_id=c1, superseded_by=correction.id,
    )
    repo = FakeFinancialRepository([orig, correction], citation_runs=citation_runs)
    cutoff = BatchCutoff(started_at=datetime(2024, 5, 1, 9, tzinfo=UTC), id=b1)

    reproduced = repo.fetch_financials(
        _CODE, as_of=_AS_OF, account=_ACCOUNT, batch_cutoff=cutoff,
    )
    assert [r.value for r in reproduced] == [Decimal("100")]
    live = repo.fetch_financials(_CODE, as_of=_AS_OF, account=_ACCOUNT)
    assert [r.value for r in live] == [Decimal("200")]


# =============================================================================
# 2. started_at tie (C#2) — lexicographic id tiebreak
# =============================================================================

def test_started_at_tie_sql_matches_freeze_id_tiebreak(
    db_session: Session,
) -> None:
    """batch1·batch2 동일 started_at, id 다름. cutoff=(ts, b1) → b1 citation 만.

    단순 `started_at <=` 면 b2 citation 도 통과시켜 재현이 깨짐 (핵심).
    id 가 작은 b1 을 cutoff 로 잡으면 b2 (id 큼) 의 row 는 제외돼야 함.
    """
    same_ts = datetime(2024, 5, 5, 9, tzinfo=UTC)
    # id 순서 보장 — b_low < b_high (UUID 비교).
    b_low = UUID("00000000-0000-0000-0000-00000000aa01")
    b_high = UUID("00000000-0000-0000-0000-00000000aa02")
    c_low, c_high = _citation_uuid(11), _citation_uuid(12)
    _seed_batch(db_session, _make_batch_orm(batch_id=b_low, source="DART", started_at=same_ts))
    _seed_batch(db_session, _make_batch_orm(batch_id=b_high, source="DART", started_at=same_ts))
    _seed_citation(db_session, _make_citation(cid=c_low, batch_id=b_low, source=SourceKind.DART))
    _seed_citation(db_session, _make_citation(cid=c_high, batch_id=b_high, source=SourceKind.DART))

    # 두 batch 가 서로 다른 fiscal_period 의 값을 생산 (집합 구별용).
    fin_low = _fin(
        fiscal_period="2024Q1", value="100", effective_date=date(2024, 5, 15),
        citation_id=c_low,
    )
    fin_high = _fin(
        fiscal_period="2024Q2", value="300", effective_date=date(2024, 5, 16),
        citation_id=c_high,
    )
    for r in (fin_low, fin_high):
        db_session.add(financial_record_to_orm(r))
    db_session.flush()

    repo = SqlFinancialRepository(db_session)
    # freeze 가 id DESC 로 b_high 를 골랐다면 cutoff=(ts, b_high) → 둘 다 통과.
    cutoff_high = BatchCutoff(started_at=same_ts, id=b_high)
    res_high = repo.fetch_financials(
        _CODE, as_of=_AS_OF, account=_ACCOUNT, batch_cutoff=cutoff_high,
    )
    assert {r.fiscal_period for r in res_high} == {"2024Q1", "2024Q2"}

    # freeze 가 b_low 를 골랐다면 cutoff=(ts, b_low) → b_high (id 큼) 는 제외.
    cutoff_low = BatchCutoff(started_at=same_ts, id=b_low)
    res_low = repo.fetch_financials(
        _CODE, as_of=_AS_OF, account=_ACCOUNT, batch_cutoff=cutoff_low,
    )
    assert {r.fiscal_period for r in res_low} == {"2024Q1"}


def test_started_at_tie_fake_matches_freeze_id_tiebreak() -> None:
    """Fake — started_at tie 에서 id lexicographic tiebreak (SQL 과 동일)."""
    same_ts = datetime(2024, 5, 5, 9, tzinfo=UTC)
    b_low = UUID("00000000-0000-0000-0000-00000000aa01")
    b_high = UUID("00000000-0000-0000-0000-00000000aa02")
    c_low, c_high = _citation_uuid(11), _citation_uuid(12)
    citation_runs = {
        c_low: CitationBatch(started_at=same_ts, batch_id=b_low, source="DART"),
        c_high: CitationBatch(started_at=same_ts, batch_id=b_high, source="DART"),
    }
    fin_low = _fin(
        fiscal_period="2024Q1", value="100", effective_date=date(2024, 5, 15),
        citation_id=c_low,
    )
    fin_high = _fin(
        fiscal_period="2024Q2", value="300", effective_date=date(2024, 5, 16),
        citation_id=c_high,
    )
    repo = FakeFinancialRepository([fin_low, fin_high], citation_runs=citation_runs)

    res_high = repo.fetch_financials(
        _CODE, as_of=_AS_OF, account=_ACCOUNT,
        batch_cutoff=BatchCutoff(started_at=same_ts, id=b_high),
    )
    assert {r.fiscal_period for r in res_high} == {"2024Q1", "2024Q2"}

    res_low = repo.fetch_financials(
        _CODE, as_of=_AS_OF, account=_ACCOUNT,
        batch_cutoff=BatchCutoff(started_at=same_ts, id=b_low),
    )
    assert {r.fiscal_period for r in res_low} == {"2024Q1"}


# =============================================================================
# 3. KRX backfill (C#1) — market_cap 과거 row 가 frozen 이후 batch
# =============================================================================

def test_krx_backfill_sql_excludes_late_batch_row(db_session: Session) -> None:
    """market_cap 의 같은 effective_date 를 frozen 이후 batch 가 backfill → 제외.

    supersede 무관 path — cutoff 가 backfill row 만 제외 (effective_date<=as_of
    와 직교). 단, market_caps PK = (code, effective_date) 이므로 같은 일자 중복
    불가 → backfill 은 더 늦은 effective_date 를 frozen 이후 batch 가 채운 형태로
    검증 (frozen 이후 batch 의 row 가 max 를 차지하는 look-ahead 차단).
    """
    b1, b2 = _batch_uuid(21), _batch_uuid(22)
    c1, c2 = _citation_uuid(21), _citation_uuid(22)
    _seed_batch(db_session, _make_batch_orm(
        batch_id=b1, source="KRX", started_at=datetime(2024, 5, 1, 9, tzinfo=UTC),
    ))
    _seed_batch(db_session, _make_batch_orm(
        batch_id=b2, source="KRX", started_at=datetime(2024, 6, 1, 9, tzinfo=UTC),
    ))
    _seed_citation(db_session, _make_citation(cid=c1, batch_id=b1, source=SourceKind.KRX))
    _seed_citation(db_session, _make_citation(cid=c2, batch_id=b2, source=SourceKind.KRX))

    # frozen 시점(batch1) 까지의 시총 (5/20). frozen 이후 batch2 가 더 최신 일자
    # (5/25) row 를 채움 — 재현 시 그 row 가 max 를 차지하면 look-ahead.
    early = _mc(effective_date=date(2024, 5, 20), market_cap="100", citation_id=c1)
    late = _mc(effective_date=date(2024, 5, 25), market_cap="999", citation_id=c2)
    for r in (early, late):
        db_session.add(market_cap_record_to_orm(r))
    db_session.flush()

    repo = SqlMarketCapRepository(db_session)
    cutoff = BatchCutoff(started_at=datetime(2024, 5, 1, 9, tzinfo=UTC), id=b1)
    # 재현 (frozen=batch1) — batch2 의 late row 제외 → early(100) 가 최신.
    reproduced = repo.fetch_latest(_CODE, as_of=_AS_OF, batch_cutoff=cutoff)
    assert reproduced is not None
    assert reproduced.market_cap == Decimal("100")
    # 대조 (cutoff 없음) — late(999) 가 최신.
    live = repo.fetch_latest(_CODE, as_of=_AS_OF)
    assert live is not None and live.market_cap == Decimal("999")


def test_krx_backfill_fake_excludes_late_batch_row() -> None:
    """Fake — KRX backfill row 제외 (SQL 과 동일)."""
    b1, b2 = _batch_uuid(21), _batch_uuid(22)
    c1, c2 = _citation_uuid(21), _citation_uuid(22)
    citation_runs = {
        c1: CitationBatch(
            started_at=datetime(2024, 5, 1, 9, tzinfo=UTC), batch_id=b1, source="KRX",
        ),
        c2: CitationBatch(
            started_at=datetime(2024, 6, 1, 9, tzinfo=UTC), batch_id=b2, source="KRX",
        ),
    }
    early = _mc(effective_date=date(2024, 5, 20), market_cap="100", citation_id=c1)
    late = _mc(effective_date=date(2024, 5, 25), market_cap="999", citation_id=c2)
    repo = FakeMarketCapRepository([early, late], citation_runs=citation_runs)
    cutoff = BatchCutoff(started_at=datetime(2024, 5, 1, 9, tzinfo=UTC), id=b1)

    reproduced = repo.fetch_latest(_CODE, as_of=_AS_OF, batch_cutoff=cutoff)
    assert reproduced is not None and reproduced.market_cap == Decimal("100")
    live = repo.fetch_latest(_CODE, as_of=_AS_OF)
    assert live is not None and live.market_cap == Decimal("999")


# =============================================================================
# 4. source mismatch 방어 (H#3) — DART fetch 에 KRX batch citation 불통과
# =============================================================================

def test_source_mismatch_sql_excludes_other_source_batch(
    db_session: Session,
) -> None:
    """financial(DART) fetch cutoff 가 KRX batch citation 의 row 를 통과 안 시킴.

    citation 이 (오염 가정) KRX batch 를 가리키면 source 필터로 제외 — fact 종류
    ↔ source 정합 방어. cutoff started_at/id 가 충분히 커도 source 불일치로 제외.
    """
    krx_batch = _batch_uuid(31)
    c_krx = _citation_uuid(31)
    _seed_batch(db_session, _make_batch_orm(
        batch_id=krx_batch, source="KRX",
        started_at=datetime(2024, 5, 1, 9, tzinfo=UTC),
    ))
    _seed_citation(db_session, _make_citation(
        cid=c_krx, batch_id=krx_batch, source=SourceKind.KRX,
    ))
    fin = _fin(
        fiscal_period="2024Q1", value="100", effective_date=date(2024, 5, 15),
        citation_id=c_krx,
    )
    db_session.add(financial_record_to_orm(fin))
    db_session.flush()

    repo = SqlFinancialRepository(db_session)
    # cutoff 는 DART source 로 필터 — KRX batch citation 의 row 는 제외 (빈 결과).
    dart_cutoff = BatchCutoff(
        started_at=datetime(2024, 12, 31, 23, tzinfo=UTC), id=_batch_uuid(99),
    )
    reproduced = repo.fetch_financials(
        _CODE, as_of=_AS_OF, account=_ACCOUNT, batch_cutoff=dart_cutoff,
    )
    assert list(reproduced) == []


def test_source_mismatch_fake_excludes_other_source_batch() -> None:
    """Fake — source 불일치 citation 제외 (SQL 과 동일)."""
    krx_batch = _batch_uuid(31)
    c_krx = _citation_uuid(31)
    citation_runs = {
        c_krx: CitationBatch(
            started_at=datetime(2024, 5, 1, 9, tzinfo=UTC),
            batch_id=krx_batch, source="KRX",
        ),
    }
    fin = _fin(
        fiscal_period="2024Q1", value="100", effective_date=date(2024, 5, 15),
        citation_id=c_krx,
    )
    repo = FakeFinancialRepository([fin], citation_runs=citation_runs)
    dart_cutoff = BatchCutoff(
        started_at=datetime(2024, 12, 31, 23, tzinfo=UTC), id=_batch_uuid(99),
    )
    reproduced = repo.fetch_financials(
        _CODE, as_of=_AS_OF, account=_ACCOUNT, batch_cutoff=dart_cutoff,
    )
    assert list(reproduced) == []


# =============================================================================
# 5. 빈 batch_id (H#5) — EXCLUDE_ALL → financial/treasury 전부 제외
# =============================================================================

def test_exclude_all_sql_excludes_every_fact(db_session: Session) -> None:
    """EXCLUDE_ALL cutoff → financial / treasury 전부 제외 (빈 결과)."""
    b1 = _batch_uuid(41)
    c1 = _citation_uuid(41)
    _seed_batch(db_session, _make_batch_orm(
        batch_id=b1, source="DART", started_at=datetime(2024, 5, 1, 9, tzinfo=UTC),
    ))
    _seed_citation(db_session, _make_citation(cid=c1, batch_id=b1, source=SourceKind.DART))
    fin = _fin(
        fiscal_period="2024Q1", value="100", effective_date=date(2024, 5, 15),
        citation_id=c1,
    )
    treasury = _treasury(
        fiscal_period="2024Q1", shares=50, effective_date=date(2024, 5, 15),
        citation_id=c1,
    )
    db_session.add(financial_record_to_orm(fin))
    db_session.add(treasury_shares_record_to_orm(treasury))
    db_session.flush()

    fin_repo = SqlFinancialRepository(db_session)
    treasury_repo = SqlTreasurySharesRepository(db_session)
    assert list(fin_repo.fetch_financials(
        _CODE, as_of=_AS_OF, account=_ACCOUNT, batch_cutoff=EXCLUDE_ALL_CUTOFF,
    )) == []
    assert treasury_repo.fetch_latest_active(
        _CODE, as_of=_AS_OF, batch_cutoff=EXCLUDE_ALL_CUTOFF,
    ) is None


def test_exclude_all_fake_excludes_every_fact() -> None:
    """Fake — EXCLUDE_ALL 전부 제외 (SQL 과 동일)."""
    b1 = _batch_uuid(41)
    c1 = _citation_uuid(41)
    citation_runs = {
        c1: CitationBatch(
            started_at=datetime(2024, 5, 1, 9, tzinfo=UTC), batch_id=b1, source="DART",
        ),
    }
    fin = _fin(
        fiscal_period="2024Q1", value="100", effective_date=date(2024, 5, 15),
        citation_id=c1,
    )
    treasury = _treasury(
        fiscal_period="2024Q1", shares=50, effective_date=date(2024, 5, 15),
        citation_id=c1,
    )
    fin_repo = FakeFinancialRepository([fin], citation_runs=citation_runs)
    treasury_repo = FakeTreasurySharesRepository([treasury], citation_runs=citation_runs)
    assert list(fin_repo.fetch_financials(
        _CODE, as_of=_AS_OF, account=_ACCOUNT, batch_cutoff=EXCLUDE_ALL_CUTOFF,
    )) == []
    assert treasury_repo.fetch_latest_active(
        _CODE, as_of=_AS_OF, batch_cutoff=EXCLUDE_ALL_CUTOFF,
    ) is None


# =============================================================================
# 6. NoActiveRecordError 흡수 (M#6) — 빈 candidates → N/A (예외 아님)
# =============================================================================

def test_no_active_record_absorbed_sql(db_session: Session) -> None:
    """cutoff 로 candidates 가 빈 fiscal_period → 예외 없이 빈 결과 (M#6).

    cutoff 가 모든 row 의 batch 보다 이른 (started_at, id) 이면 그 group 의
    candidates 가 빔 → latest_active_by_key 가 NoActiveRecordError 를 group 내부
    흡수 → 500 아닌 빈 결과.
    """
    b1 = _batch_uuid(51)
    c1 = _citation_uuid(51)
    _seed_batch(db_session, _make_batch_orm(
        batch_id=b1, source="DART", started_at=datetime(2024, 5, 1, 9, tzinfo=UTC),
    ))
    _seed_citation(db_session, _make_citation(cid=c1, batch_id=b1, source=SourceKind.DART))
    fin = _fin(
        fiscal_period="2024Q1", value="100", effective_date=date(2024, 5, 15),
        citation_id=c1,
    )
    db_session.add(financial_record_to_orm(fin))
    db_session.flush()

    repo = SqlFinancialRepository(db_session)
    # cutoff 가 batch1 보다 이른 시점 + 작은 id → 어떤 row 도 통과 못 함.
    early_cutoff = BatchCutoff(
        started_at=datetime(2024, 4, 1, 0, tzinfo=UTC),
        id=UUID("00000000-0000-0000-0000-000000000000"),
    )
    # 예외 없이 빈 결과.
    result = repo.fetch_financials(
        _CODE, as_of=_AS_OF, account=_ACCOUNT, batch_cutoff=early_cutoff,
    )
    assert list(result) == []


def test_no_active_record_absorbed_fake() -> None:
    """Fake — 빈 candidates 흡수 (SQL 과 동일)."""
    b1 = _batch_uuid(51)
    c1 = _citation_uuid(51)
    citation_runs = {
        c1: CitationBatch(
            started_at=datetime(2024, 5, 1, 9, tzinfo=UTC), batch_id=b1, source="DART",
        ),
    }
    fin = _fin(
        fiscal_period="2024Q1", value="100", effective_date=date(2024, 5, 15),
        citation_id=c1,
    )
    repo = FakeFinancialRepository([fin], citation_runs=citation_runs)
    early_cutoff = BatchCutoff(
        started_at=datetime(2024, 4, 1, 0, tzinfo=UTC),
        id=UUID("00000000-0000-0000-0000-000000000000"),
    )
    assert list(repo.fetch_financials(
        _CODE, as_of=_AS_OF, account=_ACCOUNT, batch_cutoff=early_cutoff,
    )) == []


# =============================================================================
# 7. 하위호환 — cutoff=None 기존 동작 보존
# =============================================================================

def test_backward_compat_no_cutoff_sql(db_session: Session) -> None:
    """cutoff=None (default) → 무필터. citation_runs 무관, 기존 결과 그대로."""
    b1 = _batch_uuid(61)
    c1 = _citation_uuid(61)
    _seed_batch(db_session, _make_batch_orm(
        batch_id=b1, source="DART", started_at=datetime(2024, 5, 1, 9, tzinfo=UTC),
    ))
    _seed_citation(db_session, _make_citation(cid=c1, batch_id=b1, source=SourceKind.DART))
    fin = _fin(
        fiscal_period="2024Q1", value="100", effective_date=date(2024, 5, 15),
        citation_id=c1,
    )
    db_session.add(financial_record_to_orm(fin))
    db_session.flush()

    repo = SqlFinancialRepository(db_session)
    result = repo.fetch_financials(_CODE, as_of=_AS_OF, account=_ACCOUNT)
    assert [r.value for r in result] == [Decimal("100")]


def test_backward_compat_no_cutoff_fake_without_citation_runs() -> None:
    """cutoff=None + citation_runs 미주입 → 무필터 (기존 4 repo 테스트 회귀 0)."""
    fin = _fin(
        fiscal_period="2024Q1", value="100", effective_date=date(2024, 5, 15),
        citation_id=_citation_uuid(61),
    )
    # citation_runs 미주입 (기존 생성자 시그니처) — cutoff 없으면 무관.
    repo = FakeFinancialRepository([fin])
    result = repo.fetch_financials(_CODE, as_of=_AS_OF, account=_ACCOUNT)
    assert [r.value for r in result] == [Decimal("100")]


# =============================================================================
# 8. reproduce_run end-to-end — 저장 run 재현 byte-동일 (matches=True)
# =============================================================================

_EPS_ACCOUNT: Final[str] = "basic_eps"
_EPS_FACTOR_ID: Final[str] = "eps:basic-ttm-consolidated-ifrs"
_REPRO_AS_OF: Final[date] = date(2024, 5, 7)


def _eps_quarter(
    *,
    code: str,
    fiscal_period: str,
    value: str,
    effective_date: date,
    citation_id: UUID,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> FinancialRecord:
    return FinancialRecord(
        id=record_id or uuid4(),
        code=code,
        code_lineage_id=UUID(int=int(code)),
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        account=_EPS_ACCOUNT,
        value=Decimal(value),
        unit="krw",
        ifrs_type="consolidated",
        citation_id=citation_id,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


def test_reproduce_run_end_to_end_byte_identical() -> None:
    """저장 run 을 frozen DART batch 기준 재현 → byte-동일 (matches=True).

    시나리오: 005930 의 4 분기 EPS 합 = 4000 (batch1, 원 공시). frozen run 은 EPS
    > 3999 조건으로 005930 을 포함. 이후 batch2 가 마지막 분기를 정정 (값 하향)
    하여 라이브 screen 에서는 005930 이 탈락. reproduce_run(frozen=batch1) 은
    batch2 정정 제외 → 원 EPS 복원 → 005930 재포함 → 저장 result_codes 와 동일.
    """
    from app.repositories.batch_run_repository import FakeBatchRunRepository
    from app.repositories.fakes import (
        FakeCorporateActionRepository,
        FakePriceRepository,
    )
    from app.repositories.pit_protocols import CodeHistoryEntry, StockMasterRecord
    from app.repositories.stocks_master_repository import (
        FakeStocksMasterRepository,
    )
    from app.services.factor_evaluator import FactorEvaluator
    from app.services.factor_pack import DEFAULT_PACK
    from app.services.reproduce import reproduce_run
    from app.services.screen_run import ScreenRunBuilder

    code = "005930"
    dart_batch1 = _batch_uuid(71)  # 원 공시 batch (frozen).
    dart_batch2 = _batch_uuid(72)  # 정정 batch (frozen 이후).
    c1, c2 = _citation_uuid(71), _citation_uuid(72)
    citation_runs = {
        c1: CitationBatch(
            started_at=datetime(2024, 4, 1, 9, tzinfo=UTC),
            batch_id=dart_batch1, source="DART",
        ),
        c2: CitationBatch(
            started_at=datetime(2024, 5, 5, 9, tzinfo=UTC),
            batch_id=dart_batch2, source="DART",
        ),
    }

    periods = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    eff = [date(2023, 5, 15), date(2023, 8, 14), date(2023, 11, 14),
           date(2024, 3, 30)]
    # batch1 (원 공시) — 각 분기 1000 → TTM 합 4000.
    records: list[FinancialRecord] = []
    last_orig_id = None
    for fp, e in zip(periods, eff, strict=True):
        rid = uuid4()
        if fp == "2023Q4":
            last_orig_id = rid
        records.append(_eps_quarter(
            code=code, fiscal_period=fp, value="1000", effective_date=e,
            citation_id=c1, record_id=rid,
        ))
    # batch2 (정정) — 마지막 분기 2023Q4 를 100 으로 하향 (원 row supersede).
    correction = _eps_quarter(
        code=code, fiscal_period="2023Q4", value="100", effective_date=date(2024, 3, 30),
        citation_id=c2, created_at=datetime(2024, 5, 5, tzinfo=UTC),
    )
    # 원 2023Q4 row 가 정정으로 supersede.
    assert last_orig_id is not None
    records = [
        _eps_quarter(
            code=code, fiscal_period=r.fiscal_period, value=str(r.value),
            effective_date=r.effective_date, citation_id=r.citation_id,
            record_id=r.id,
            superseded_by=correction.id if r.id == last_orig_id else None,
            created_at=r.created_at,
        )
        for r in records
    ]
    records.append(correction)

    fin_repo = FakeFinancialRepository(records, citation_runs=citation_runs)
    stocks_repo = FakeStocksMasterRepository(records=[
        StockMasterRecord(
            id=UUID(int=int(code)), current_code=code, current_name="삼성전자",
            market="KOSPI", listing_date=date(2000, 1, 1), delisting_date=None,
            fiscal_month=12,
            code_history=(CodeHistoryEntry(code, date(2000, 1, 1), None,
                                           "initial_listing"),),
        ),
    ])
    evaluator = FactorEvaluator()
    common = dict(
        stocks_repo=stocks_repo,
        pack=DEFAULT_PACK,
        evaluator=evaluator,
        price_repo=FakePriceRepository(records=(), citation_runs=citation_runs),
        financial_repo=fin_repo,
        corporate_action_repo=FakeCorporateActionRepository(records=()),
        market_cap_repo=FakeMarketCapRepository(records=(), citation_runs=citation_runs),
        treasury_repo=FakeTreasurySharesRepository(records=(), citation_runs=citation_runs),
    )

    # frozen run 의 result_codes — frozen batch1 기준 screen (cutoff=batch1).
    from app.api.routes.screen import screen_active_codes
    cutoff1 = BatchCutoff(started_at=datetime(2024, 4, 1, 9, tzinfo=UTC), id=dart_batch1)
    cond = [ConditionIn(factor=_EPS_FACTOR_ID, op=OpEnum.GT, value="3999")]
    frozen_codes = screen_active_codes(
        as_of=_REPRO_AS_OF, conditions=cond, dart_batch_cutoff=cutoff1, **common,
    )
    # 원 EPS=4000 > 3999 → 005930 포함.
    assert frozen_codes.result_codes == ("005930",)

    # 라이브 (cutoff 없음) — 정정 EPS = 1000*3 + 100 = 3100 < 3999 → 탈락.
    live_codes = screen_active_codes(as_of=_REPRO_AS_OF, conditions=cond, **common)
    assert live_codes.result_codes == ()

    # snapshot 저장 — data_versions 에 dart_batch1 (frozen).
    data_versions = {"dart_batch_id": str(dart_batch1), "krx_batch_id": ""}
    snapshot = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": _EPS_FACTOR_ID, "op": ">", "value": "3999"}],
        selected_factors=[_EPS_FACTOR_ID],
        as_of=_REPRO_AS_OF,
        result_codes=frozen_codes.result_codes,
        data_versions=data_versions,
    )

    # batch_run_repo — get_run 으로 BatchCutoff 해소. dart_batch1 등록
    # (start INSERT → finalize success, 운영 일배치 경로 동일).
    batch_repo = FakeBatchRunRepository()
    batch_repo.start(
        run_id=dart_batch1, market=None, source="DART",
        started_at=datetime(2024, 4, 1, 9, tzinfo=UTC),
    )
    batch_repo.finalize(
        run_id=dart_batch1, ended_at=datetime(2024, 4, 1, 10, tzinfo=UTC),
        success_count=1, status=BATCH_STATUS_SUCCESS,
    )

    # reproduce_run — frozen batch1 기준 재현 → 원 EPS 복원 → 005930 재포함.
    result = reproduce_run(snapshot, batch_run_repo=batch_repo, **common)
    assert result.result_codes == ("005930",)
    assert result.matches is True

    # -------------------------------------------------------------------------
    # AC-M2-C-05 (ADR-0021 D5) — 재현은 user 무관. SYSTEM_USER_ID 로 저장된 run
    # 을 다른 실유저 컨텍스트에서 재현해도 동일 결과 + result_hash byte 동일.
    #
    # ① result_hash 가 user_id 입력 아님 — 같은 query/as_of/result_codes/
    #    data_versions 면 user_id 가 달라도 hash 동일(screen_run.py:318-327).
    # ② reproduce_run 은 user_id 를 입력받지 않음 — fact 조회는 batch_cutoff
    #    (공용)만으로 결정(D5). 따라서 다른 user 컨텍스트의 동일 snapshot 재현은
    #    matches=True + result_codes 동일.
    # -------------------------------------------------------------------------
    from app.services.screen_run import SYSTEM_USER_ID

    # SYSTEM-owned snapshot (M1 저장 run 시뮬레이션).
    system_snapshot = ScreenRunBuilder.build(
        run_id=uuid4(),
        user_id=SYSTEM_USER_ID,
        conditions=[{"factor": _EPS_FACTOR_ID, "op": ">", "value": "3999"}],
        selected_factors=[_EPS_FACTOR_ID],
        as_of=_REPRO_AS_OF,
        result_codes=frozen_codes.result_codes,
        data_versions=data_versions,
    )
    # 다른 실유저가 동일 query/as_of/result_codes/data_versions 로 저장한 run.
    other_user = UUID("00000000-0000-0000-0000-0000000000ff")
    other_snapshot = ScreenRunBuilder.build(
        run_id=uuid4(),
        user_id=other_user,
        conditions=[{"factor": _EPS_FACTOR_ID, "op": ">", "value": "3999"}],
        selected_factors=[_EPS_FACTOR_ID],
        as_of=_REPRO_AS_OF,
        result_codes=frozen_codes.result_codes,
        data_versions=data_versions,
    )
    # ① result_hash byte 동일 — user_id 무관.
    assert system_snapshot.user_id == SYSTEM_USER_ID
    assert other_snapshot.user_id == other_user
    assert system_snapshot.result_hash == other_snapshot.result_hash

    # ② SYSTEM run 을 다른 user 컨텍스트(other_snapshot 의 frozen 자산)에서 재현
    #    — reproduce_run 은 user_id 를 안 읽으므로 byte-동일.
    system_result = reproduce_run(
        system_snapshot, batch_run_repo=batch_repo, **common,
    )
    other_result = reproduce_run(
        other_snapshot, batch_run_repo=batch_repo, **common,
    )
    assert system_result.matches is True
    assert other_result.matches is True
    assert system_result.result_codes == other_result.result_codes == ("005930",)
