"""TreasurySharesRepository PIT + 정정 chain + append-only (financials 패턴).

Fake / Sql 동등성 + ADR-0020 append-only 불변식 검증.

테스트 매트릭스:
    1. fetch_latest_active — effective_date <= as_of 중 최신 fiscal_period.
    2. as_of 이전 effective_date 만 active (PIT 경계).
    3. 정정 chain 해소 — superseded 옛 row 는 제외, 후속 row 반환.
    4. Fake / Sql 동등 결과.
    5. update_superseded_by NULL→set 1 회 허용 + 재변경/부재 거부 (ADR-0020 D5).
    6. update_superseded_by 가 superseded_by 외 컬럼 미변경.
    7. 빈 store → None.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.db.converters import citation_record_to_orm
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.db.orm.source_citations import SourceCitationORM
from app.db.orm.treasury_shares import TreasurySharesORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.fakes import FakeTreasurySharesRepository
from app.repositories.pit_protocols import TreasurySharesRecord
from app.repositories.sql_repositories import (
    AppendOnlyViolationError,
    SqlTreasurySharesRepository,
)

_CID = UUID("00000000-0000-0000-0000-00000000ffff")
_LINEAGE = UUID("00000000-0000-0000-0000-0000000000aa")
_BATCH = UUID("00000000-0000-0000-0000-0000000000bb")


def _seed_fk_rows(session: Session) -> None:
    """citation_id FK + batch_id FK 만족용 dummy row."""
    if session.get(BatchRunORM, _BATCH) is None:
        session.add(
            BatchRunORM(
                id=_BATCH, market=None, source="DART",
                started_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
                ended_at=datetime(2024, 5, 20, 9, 5, tzinfo=UTC),
                success_count=1, status=BATCH_STATUS_SUCCESS,
            )
        )
    if session.get(SourceCitationORM, _CID) is None:
        session.add(citation_record_to_orm(SourceCitation(
            id=_CID,
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


def _rec(
    *,
    record_id: UUID,
    fiscal_period: str,
    effective_date: date,
    shares_treasury: int,
    superseded_by: UUID | None = None,
    created_at: datetime | None = None,
    effective_date_precise: bool = False,
) -> TreasurySharesRecord:
    return TreasurySharesRecord(
        id=record_id,
        code="005930",
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        shares_treasury=shares_treasury,
        effective_date_precise=effective_date_precise,
        citation_id=_CID,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


# =============================================================================
# 1~2. fetch_latest_active PIT
# =============================================================================

def test_fake_fetch_latest_active_returns_newest_period() -> None:
    repo = FakeTreasurySharesRepository([
        _rec(
            record_id=uuid4(), fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=100,
        ),
        _rec(
            record_id=uuid4(), fiscal_period="2024Q1",
            effective_date=date(2024, 5, 15), shares_treasury=200,
        ),
    ])
    result = repo.fetch_latest_active("005930", as_of=date(2024, 6, 1))
    assert result is not None
    assert result.fiscal_period == "2024Q1"
    assert result.shares_treasury == 200


def test_fake_fetch_latest_active_respects_as_of_boundary() -> None:
    """effective_date > as_of 인 최신 분기는 제외 (PIT)."""
    repo = FakeTreasurySharesRepository([
        _rec(
            record_id=uuid4(), fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=100,
        ),
        _rec(
            record_id=uuid4(), fiscal_period="2024Q1",
            effective_date=date(2024, 5, 15), shares_treasury=200,
        ),
    ])
    # as_of 가 2024Q1 신고기한 직전 → 2023Q4 만 active.
    result = repo.fetch_latest_active("005930", as_of=date(2024, 4, 1))
    assert result is not None
    assert result.fiscal_period == "2023Q4"
    assert result.shares_treasury == 100


def test_fake_fetch_latest_active_empty_returns_none() -> None:
    repo = FakeTreasurySharesRepository([])
    assert repo.fetch_latest_active("005930", as_of=date(2024, 6, 1)) is None


# =============================================================================
# 3. 정정 chain 해소
# =============================================================================

def test_fake_supersede_chain_returns_successor() -> None:
    """같은 fiscal_period 의 옛 row 가 superseded → 후속 row 반환."""
    orig_id, succ_id = uuid4(), uuid4()
    repo = FakeTreasurySharesRepository([
        _rec(
            record_id=orig_id, fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=100,
            superseded_by=succ_id,
            created_at=datetime(2024, 3, 30, tzinfo=UTC),
        ),
        _rec(
            record_id=succ_id, fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=150,
            created_at=datetime(2024, 4, 10, tzinfo=UTC),
        ),
    ])
    result = repo.fetch_latest_active("005930", as_of=date(2024, 6, 1))
    assert result is not None
    assert result.id == succ_id
    assert result.shares_treasury == 150


# =============================================================================
# 4. Fake / Sql 동등성
# =============================================================================

def test_sql_fetch_latest_active_matches_fake(db_session: Session) -> None:
    _seed_fk_rows(db_session)
    records = [
        _rec(
            record_id=uuid4(), fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=100,
        ),
        _rec(
            record_id=uuid4(), fiscal_period="2024Q1",
            effective_date=date(2024, 5, 15), shares_treasury=200,
        ),
    ]
    sql_repo = SqlTreasurySharesRepository(db_session)
    sql_repo.save_treasury_shares(records)

    fake_repo = FakeTreasurySharesRepository(records)
    as_of = date(2024, 6, 1)
    sql_result = sql_repo.fetch_latest_active("005930", as_of=as_of)
    fake_result = fake_repo.fetch_latest_active("005930", as_of=as_of)
    assert sql_result is not None and fake_result is not None
    assert sql_result.fiscal_period == fake_result.fiscal_period == "2024Q1"
    assert sql_result.shares_treasury == fake_result.shares_treasury == 200


def test_sql_effective_date_precise_round_trip(db_session: Session) -> None:
    """ADR-0012 D6 — effective_date_precise 가 ORM ↔ record round-trip 보존.

    precise=True row 와 precise=False (default fallback) row 를 모두 저장 후
    fetch 하여 컬럼이 정확히 복원되는지 검증 (Sql 경로). default False 인 기존
    row 호환도 같이 확인.
    """
    _seed_fk_rows(db_session)
    repo = SqlTreasurySharesRepository(db_session)
    precise_id, fallback_id = uuid4(), uuid4()
    repo.save_treasury_shares([
        _rec(
            record_id=precise_id, fiscal_period="2024Q1",
            effective_date=date(2024, 5, 1), shares_treasury=200,
            effective_date_precise=True,
        ),
        _rec(
            record_id=fallback_id, fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=100,
            effective_date_precise=False,
        ),
    ])
    # 2024Q1 (precise) 가 latest active.
    latest = repo.fetch_latest_active("005930", as_of=date(2024, 6, 1))
    assert latest is not None
    assert latest.id == precise_id
    assert latest.effective_date == date(2024, 5, 1)
    assert latest.effective_date_precise is True
    # 보수 fallback row (2024-04-01 as_of) — precise=False 복원.
    fallback = repo.fetch_latest_active("005930", as_of=date(2024, 4, 1))
    assert fallback is not None
    assert fallback.id == fallback_id
    assert fallback.effective_date_precise is False


def test_sql_supersede_chain_resolution(db_session: Session) -> None:
    _seed_fk_rows(db_session)
    orig_id, succ_id = uuid4(), uuid4()
    repo = SqlTreasurySharesRepository(db_session)
    repo.save_treasury_shares([
        _rec(
            record_id=orig_id, fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=100,
            created_at=datetime(2024, 3, 30, tzinfo=UTC),
        ),
        _rec(
            record_id=succ_id, fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=150,
            created_at=datetime(2024, 4, 10, tzinfo=UTC),
        ),
    ])
    repo.update_superseded_by(orig_id, succ_id)

    result = repo.fetch_latest_active("005930", as_of=date(2024, 6, 1))
    assert result is not None
    assert result.id == succ_id
    assert result.shares_treasury == 150


# =============================================================================
# 5. update_superseded_by 불변식 (ADR-0020 D5)
# =============================================================================

def test_sql_supersede_null_to_set_allowed_once(db_session: Session) -> None:
    _seed_fk_rows(db_session)
    repo = SqlTreasurySharesRepository(db_session)
    orig_id, succ_id = uuid4(), uuid4()
    repo.save_treasury_shares([
        _rec(
            record_id=orig_id, fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=100,
        ),
        _rec(
            record_id=succ_id, fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=150,
        ),
    ])
    repo.update_superseded_by(orig_id, succ_id)
    orm = db_session.get(TreasurySharesORM, orig_id)
    assert orm is not None
    assert orm.superseded_by == succ_id


def test_sql_supersede_already_set_rejected(db_session: Session) -> None:
    _seed_fk_rows(db_session)
    repo = SqlTreasurySharesRepository(db_session)
    orig_id, succ1, succ2 = uuid4(), uuid4(), uuid4()
    repo.save_treasury_shares([
        _rec(record_id=orig_id, fiscal_period="2023Q4",
             effective_date=date(2024, 3, 30), shares_treasury=100),
        _rec(record_id=succ1, fiscal_period="2023Q4",
             effective_date=date(2024, 3, 30), shares_treasury=150),
        _rec(record_id=succ2, fiscal_period="2023Q4",
             effective_date=date(2024, 3, 30), shares_treasury=160),
    ])
    repo.update_superseded_by(orig_id, succ1)
    with pytest.raises(AppendOnlyViolationError, match="이미 superseded"):
        repo.update_superseded_by(orig_id, succ2)


def test_sql_supersede_missing_successor_rejected(db_session: Session) -> None:
    _seed_fk_rows(db_session)
    repo = SqlTreasurySharesRepository(db_session)
    orig_id = uuid4()
    repo.save_treasury_shares([
        _rec(record_id=orig_id, fiscal_period="2023Q4",
             effective_date=date(2024, 3, 30), shares_treasury=100),
    ])
    with pytest.raises(AppendOnlyViolationError, match="successor"):
        repo.update_superseded_by(orig_id, uuid4())


def test_sql_supersede_missing_target_rejected(db_session: Session) -> None:
    _seed_fk_rows(db_session)
    repo = SqlTreasurySharesRepository(db_session)
    with pytest.raises(AppendOnlyViolationError, match="부재"):
        repo.update_superseded_by(uuid4(), uuid4())


def test_sql_supersede_leaves_other_columns_unchanged(
    db_session: Session,
) -> None:
    _seed_fk_rows(db_session)
    repo = SqlTreasurySharesRepository(db_session)
    orig_id, succ_id = uuid4(), uuid4()
    repo.save_treasury_shares([
        _rec(record_id=orig_id, fiscal_period="2023Q4",
             effective_date=date(2024, 3, 30), shares_treasury=100),
        _rec(record_id=succ_id, fiscal_period="2023Q4",
             effective_date=date(2024, 3, 30), shares_treasury=150),
    ])
    before = db_session.get(TreasurySharesORM, orig_id)
    assert before is not None
    snap = (before.code, before.shares_treasury, before.effective_date,
            before.fiscal_period, before.citation_id, before.created_at)

    repo.update_superseded_by(orig_id, succ_id)

    after = db_session.get(TreasurySharesORM, orig_id)
    assert after is not None
    assert (after.code, after.shares_treasury, after.effective_date,
            after.fiscal_period, after.citation_id, after.created_at) == snap
