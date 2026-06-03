"""M1 T48a — batch_runs ORM + SqlBatchRunRepository + source_citations FK 테스트.

테스트 매트릭스:
    1. SqlBatchRunRepository.start → status='running' row INSERT.
    2. finalize → ended_at/success_count/status UPDATE (running → success).
    3. start 중복 id → ValueError (run 메타 1 회).
    4. finalize before start → ValueError.
    5. source_citations.batch_id FK — batch_runs row 존재 시 citation INSERT +
       COMMIT 성공.
    6. source_citations.batch_id FK — batch_runs row 부재 시 orphan citation 이
       COMMIT 에서 거부 (DEFERRABLE INITIALLY DEFERRED — 검사 시점 = COMMIT/
       SAVEPOINT RELEASE).
    7. FakeBatchRunRepository 가 동일 contract (start/finalize/중복 거부).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.orm.batch_runs import (
    BATCH_STATUS_RUNNING,
    BATCH_STATUS_SUCCESS,
    BatchRunORM,
)
from app.db.orm.source_citations import SourceCitationORM
from app.repositories.batch_run_repository import (
    FakeBatchRunRepository,
    SqlBatchRunRepository,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _citation_orm(*, batch_id) -> SourceCitationORM:  # noqa: ANN001
    return SourceCitationORM(
        id=uuid4(),
        source="KRX",
        identifier="005930|2024-05-07",
        retrieved_at=_now(),
        effective_date=date(2024, 5, 7),
        adapter_version="1.0.0",
        batch_id=batch_id,
        url=None,
        created_at=_now(),
    )


# =============================================================================
# 1~4. SqlBatchRunRepository start / finalize
# =============================================================================

def test_start_inserts_running_row(db_session: Session) -> None:
    repo = SqlBatchRunRepository(db_session)
    rid = uuid4()
    started = _now()
    repo.start(run_id=rid, market="KOSPI", source="KRX", started_at=started)

    rec = repo.fetch_by_id(rid)
    assert rec is not None
    assert rec.status == BATCH_STATUS_RUNNING
    assert rec.market == "KOSPI"
    assert rec.source == "KRX"
    assert rec.success_count == 0
    # 시작 시점엔 ended_at == started_at (placeholder).
    assert rec.ended_at == rec.started_at


def test_finalize_updates_to_success(db_session: Session) -> None:
    repo = SqlBatchRunRepository(db_session)
    rid = uuid4()
    started = _now()
    repo.start(run_id=rid, market=None, source="DART", started_at=started)
    ended = _now()
    repo.finalize(
        run_id=rid, ended_at=ended, success_count=42,
        status=BATCH_STATUS_SUCCESS,
    )

    rec = repo.fetch_by_id(rid)
    assert rec is not None
    assert rec.status == BATCH_STATUS_SUCCESS
    assert rec.success_count == 42
    assert rec.market is None
    assert rec.source == "DART"


def test_start_duplicate_id_raises(db_session: Session) -> None:
    repo = SqlBatchRunRepository(db_session)
    rid = uuid4()
    repo.start(run_id=rid, market="KOSPI", source="KRX", started_at=_now())
    with pytest.raises(ValueError, match="already exists"):
        repo.start(run_id=rid, market="KOSPI", source="KRX", started_at=_now())


def test_finalize_before_start_raises(db_session: Session) -> None:
    repo = SqlBatchRunRepository(db_session)
    with pytest.raises(ValueError, match="not found"):
        repo.finalize(
            run_id=uuid4(), ended_at=_now(), success_count=1,
            status=BATCH_STATUS_SUCCESS,
        )


# =============================================================================
# 5~6. source_citations.batch_id FK (DEFERRABLE INITIALLY DEFERRED)
# =============================================================================

def test_citation_fk_satisfied_when_batch_run_exists(
    db_session: Session,
) -> None:
    """batch_runs row 가 존재하면 citation INSERT + COMMIT 성공 (FK 만족)."""
    repo = SqlBatchRunRepository(db_session)
    rid = uuid4()
    repo.start(run_id=rid, market="KOSPI", source="KRX", started_at=_now())
    db_session.add(_citation_orm(batch_id=rid))
    # COMMIT 시점 deferred FK 검사 통과.
    db_session.commit()

    assert db_session.query(SourceCitationORM).count() == 1
    assert db_session.query(BatchRunORM).count() == 1


def test_citation_fk_orphan_rejected_at_commit(db_session: Session) -> None:
    """batch_runs row 부재 시 orphan citation 이 COMMIT 에서 FK 위반으로 거부.

    DEFERRABLE INITIALLY DEFERRED — flush 시점이 아닌 COMMIT 시점 검사.
    """
    db_session.add(_citation_orm(batch_id=uuid4()))  # 대응 batch_runs 없음.
    with pytest.raises(IntegrityError):
        db_session.commit()


# =============================================================================
# 7. FakeBatchRunRepository contract 동등성
# =============================================================================

def test_fake_repo_start_finalize_contract() -> None:
    repo = FakeBatchRunRepository()
    rid = uuid4()
    repo.start(run_id=rid, market="KOSDAQ", source="KRX", started_at=_now())
    rec = repo.fetch_by_id(rid)
    assert rec is not None
    assert rec.status == BATCH_STATUS_RUNNING

    repo.finalize(
        run_id=rid, ended_at=_now(), success_count=7,
        status=BATCH_STATUS_SUCCESS,
    )
    rec2 = repo.fetch_by_id(rid)
    assert rec2 is not None
    assert rec2.status == BATCH_STATUS_SUCCESS
    assert rec2.success_count == 7

    with pytest.raises(ValueError, match="already exists"):
        repo.start(run_id=rid, market="KOSDAQ", source="KRX", started_at=_now())
    with pytest.raises(ValueError, match="not found"):
        repo.finalize(
            run_id=uuid4(), ended_at=_now(), success_count=1,
            status=BATCH_STATUS_SUCCESS,
        )
