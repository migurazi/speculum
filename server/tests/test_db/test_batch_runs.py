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
    BATCH_STATUS_SKIPPED,
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


# =============================================================================
# 8. latest_successful — M5 #4a (데이터 신선도 진단의 "데이터 기준일" source)
# =============================================================================

def _finalize_success(repo, *, source, started_at, market=None):  # noqa: ANN001, ANN202
    """헬퍼 — start + finalize(success) 한 batch row 생성, run_id 반환."""
    rid = uuid4()
    repo.start(run_id=rid, market=market, source=source, started_at=started_at)
    repo.finalize(
        run_id=rid, ended_at=started_at, success_count=1,
        status=BATCH_STATUS_SUCCESS,
    )
    return rid


def test_fake_latest_successful_returns_newest(  # noqa: D103
) -> None:
    repo = FakeBatchRunRepository()
    _finalize_success(repo, source="KRX", started_at=datetime(2024, 5, 7, 1, tzinfo=UTC))
    newest = _finalize_success(
        repo, source="KRX", started_at=datetime(2024, 5, 9, 1, tzinfo=UTC),
    )
    _finalize_success(repo, source="KRX", started_at=datetime(2024, 5, 8, 1, tzinfo=UTC))
    rec = repo.latest_successful("KRX")
    assert rec is not None
    assert rec.id == newest
    assert rec.started_at == datetime(2024, 5, 9, 1, tzinfo=UTC)


def test_fake_latest_successful_excludes_running_and_skipped() -> None:  # noqa: D103
    repo = FakeBatchRunRepository()
    # running batch (가장 최신 started_at) — 제외돼야.
    repo.start(
        run_id=uuid4(), market=None, source="DART",
        started_at=datetime(2024, 5, 10, 1, tzinfo=UTC),
    )
    # skipped batch (휴장일 등) — 제외돼야.
    rid_skip = uuid4()
    repo.start(
        run_id=rid_skip, market=None, source="DART",
        started_at=datetime(2024, 5, 9, 1, tzinfo=UTC),
    )
    repo.finalize(
        run_id=rid_skip, ended_at=datetime(2024, 5, 9, 2, tzinfo=UTC),
        success_count=0, status=BATCH_STATUS_SKIPPED,
    )
    # success batch — 이것만 반환돼야 (running/skipped 보다 older 여도).
    success = _finalize_success(
        repo, source="DART", started_at=datetime(2024, 5, 8, 1, tzinfo=UTC),
    )
    rec = repo.latest_successful("DART")
    assert rec is not None
    assert rec.id == success
    assert rec.status == BATCH_STATUS_SUCCESS


def test_fake_latest_successful_source_isolation() -> None:  # noqa: D103
    repo = FakeBatchRunRepository()
    _finalize_success(repo, source="KRX", started_at=datetime(2024, 5, 9, 1, tzinfo=UTC))
    # DART 성공 batch 없음 → None.
    assert repo.latest_successful("DART") is None


def test_fake_latest_successful_absent_returns_none() -> None:  # noqa: D103
    repo = FakeBatchRunRepository()
    assert repo.latest_successful("KRX") is None


def test_sql_latest_successful_returns_newest(db_session: Session) -> None:  # noqa: D103
    repo = SqlBatchRunRepository(db_session)
    _finalize_success(repo, source="KRX", started_at=datetime(2024, 5, 7, 1, tzinfo=UTC))
    newest = _finalize_success(
        repo, source="KRX", started_at=datetime(2024, 5, 9, 1, tzinfo=UTC),
    )
    _finalize_success(repo, source="KRX", started_at=datetime(2024, 5, 8, 1, tzinfo=UTC))
    rec = repo.latest_successful("KRX")
    assert rec is not None
    assert rec.id == newest


def test_sql_latest_successful_excludes_running_and_skipped(
    db_session: Session,
) -> None:  # noqa: D103
    repo = SqlBatchRunRepository(db_session)
    repo.start(
        run_id=uuid4(), market=None, source="DART",
        started_at=datetime(2024, 5, 10, 1, tzinfo=UTC),
    )
    rid_skip = uuid4()
    repo.start(
        run_id=rid_skip, market=None, source="DART",
        started_at=datetime(2024, 5, 9, 1, tzinfo=UTC),
    )
    repo.finalize(
        run_id=rid_skip, ended_at=datetime(2024, 5, 9, 2, tzinfo=UTC),
        success_count=0, status=BATCH_STATUS_SKIPPED,
    )
    success = _finalize_success(
        repo, source="DART", started_at=datetime(2024, 5, 8, 1, tzinfo=UTC),
    )
    rec = repo.latest_successful("DART")
    assert rec is not None
    assert rec.id == success
    assert rec.status == BATCH_STATUS_SUCCESS


def test_sql_latest_successful_absent_returns_none(db_session: Session) -> None:  # noqa: D103
    repo = SqlBatchRunRepository(db_session)
    assert repo.latest_successful("KRX") is None
