"""BATCH_STATUS_PARTIAL — 부분 실패 배치의 status 라벨링 + §2.10 byte-동일 가드.

작업 1 (배치 운영 견고성) 검증:
    1. 각 배치 `_finalize_batch_run` 이 failure_count>0 시 'partial', 0 시
       'success' 로 저장 (dart / ecos / kosis / snapshot / survivorship).
    2. krx 의 우선순위 skipped > partial > success
       (skipped_reason 있으면 failure>0 이어도 'skipped').
    3. §2.10 회귀 가드 — partial 배치도 collect_batch_versions freeze 후보 +
       latest_successful 에 포함됨 (partial 도입 전 'success' 저장과 byte-동일).

배치 인스턴스는 `object.__new__` 로 생성하고 `_batch_run_repo` 만 주입 —
`_finalize_batch_run` 은 self._batch_run_repo 외 의존이 없어 무거운 wiring 불요.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from app.db.orm.batch_runs import (
    BATCH_STATUS_PARTIAL,
    BATCH_STATUS_SKIPPED,
    BATCH_STATUS_SUCCESS,
)
from app.repositories.batch_run_repository import FakeBatchRunRepository
from batch.dart_daily import DartBatchSummary, DartDailyBatch
from batch.ecos_daily import EcosBatchSummary, EcosDailyBatch
from batch.kosis_daily import KosisBatchSummary, KosisDailyBatch
from batch.krx_daily import BatchSummary as KrxBatchSummary
from batch.krx_daily import KrxDailyBatch
from batch.snapshot_daily import SnapshotBatchSummary, SnapshotDailyBatch
from batch.survivorship_backfill import (
    BackfillSummary,
    SurvivorshipBackfillBatch,
)

_T0 = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)
_T1 = datetime(2024, 6, 28, 9, 5, tzinfo=UTC)


def _started_repo(
    *, source: str, market: str | None = None, batch_id: UUID
) -> FakeBatchRunRepository:
    """start() 된 running row 가 있는 Fake repo (finalize 전제)."""
    repo = FakeBatchRunRepository()
    repo.start(
        run_id=batch_id, market=market, source=source, started_at=_T0
    )
    return repo


def _finalize_via(batch_cls: type, repo: FakeBatchRunRepository, summary) -> str:  # noqa: ANN001
    """배치 클래스의 _finalize_batch_run 을 가벼운 인스턴스로 호출 → status 반환."""
    obj = object.__new__(batch_cls)
    obj._batch_run_repo = repo
    obj._finalize_batch_run(summary)
    return repo.fetch_by_id(summary.batch_id).status


# =============================================================================
# 1. 각 배치 partial vs success
# =============================================================================

def _dart_summary(*, batch_id: UUID, failure_count: int) -> DartBatchSummary:
    return DartBatchSummary(
        batch_id=batch_id,
        fiscal_year=2024,
        fiscal_quarter=1,
        target_count=10,
        success_count=10 - failure_count,
        failure_count=failure_count,
        skipped_count=0,
        failures=tuple(
            (f"00{i}", "boom") for i in range(failure_count)
        ),
        skipped_codes=(),
        total_rows_saved=20,
        dry_run=False,
        started_at=_T0,
        ended_at=_T1,
    )


def test_dart_partial_when_failures() -> None:
    bid = uuid4()
    repo = _started_repo(source="DART", batch_id=bid)
    status = _finalize_via(
        DartDailyBatch, repo, _dart_summary(batch_id=bid, failure_count=2)
    )
    assert status == BATCH_STATUS_PARTIAL


def test_dart_success_when_no_failures() -> None:
    bid = uuid4()
    repo = _started_repo(source="DART", batch_id=bid)
    status = _finalize_via(
        DartDailyBatch, repo, _dart_summary(batch_id=bid, failure_count=0)
    )
    assert status == BATCH_STATUS_SUCCESS


def _ecos_summary(*, batch_id: UUID, failure_count: int) -> EcosBatchSummary:
    return EcosBatchSummary(
        batch_id=batch_id,
        observed_date=date(2024, 6, 28),
        target_count=5,
        success_count=5 - failure_count,
        failure_count=failure_count,
        skipped_count=0,
        total_rows_saved=100,
        failures=tuple(
            (f"ind{i}", "boom") for i in range(failure_count)
        ),
        started_at=_T0,
        ended_at=_T1,
    )


def test_ecos_partial_when_failures() -> None:
    bid = uuid4()
    repo = _started_repo(source="ECOS", batch_id=bid)
    status = _finalize_via(
        EcosDailyBatch, repo, _ecos_summary(batch_id=bid, failure_count=1)
    )
    assert status == BATCH_STATUS_PARTIAL


def test_ecos_success_when_no_failures() -> None:
    bid = uuid4()
    repo = _started_repo(source="ECOS", batch_id=bid)
    status = _finalize_via(
        EcosDailyBatch, repo, _ecos_summary(batch_id=bid, failure_count=0)
    )
    assert status == BATCH_STATUS_SUCCESS


def _kosis_summary(*, batch_id: UUID, failure_count: int) -> KosisBatchSummary:
    return KosisBatchSummary(
        batch_id=batch_id,
        observed_date=date(2024, 6, 28),
        target_count=3,
        success_count=3 - failure_count,
        failure_count=failure_count,
        skipped_count=0,
        total_rows_saved=30,
        failures=tuple(
            (f"ind{i}", "boom") for i in range(failure_count)
        ),
        started_at=_T0,
        ended_at=_T1,
    )


def test_kosis_partial_when_failures() -> None:
    bid = uuid4()
    repo = _started_repo(source="KOSIS", batch_id=bid)
    status = _finalize_via(
        KosisDailyBatch, repo, _kosis_summary(batch_id=bid, failure_count=1)
    )
    assert status == BATCH_STATUS_PARTIAL


def _snapshot_summary(
    *, batch_id: UUID, failure_count: int
) -> SnapshotBatchSummary:
    return SnapshotBatchSummary(
        batch_id=batch_id,
        observed_date=date(2024, 6, 28),
        universe_count=100,
        factor_count=5,
        success_count=100 - failure_count,
        failure_count=failure_count,
        skipped_count=0,
        snapshots_saved=(100 - failure_count) * 5,
        failures=tuple(
            (f"00{i}", "boom") for i in range(failure_count)
        ),
        started_at=_T0,
        ended_at=_T1,
    )


def test_snapshot_partial_when_failures() -> None:
    bid = uuid4()
    repo = _started_repo(source="PRECOMPUTE", batch_id=bid)
    status = _finalize_via(
        SnapshotDailyBatch, repo,
        _snapshot_summary(batch_id=bid, failure_count=3),
    )
    assert status == BATCH_STATUS_PARTIAL


def test_snapshot_success_when_no_failures() -> None:
    bid = uuid4()
    repo = _started_repo(source="PRECOMPUTE", batch_id=bid)
    status = _finalize_via(
        SnapshotDailyBatch, repo,
        _snapshot_summary(batch_id=bid, failure_count=0),
    )
    assert status == BATCH_STATUS_SUCCESS


def _backfill_summary(
    *, batch_id: UUID, failure_count: int
) -> BackfillSummary:
    return BackfillSummary(
        batch_id=batch_id,
        cutoff=date(2024, 6, 28),
        market=None,
        delisted_universe_size=10,
        success_count=10 - failure_count,
        failure_count=failure_count,
        failures=tuple(
            (f"00{i}", "boom") for i in range(failure_count)
        ),
        rows_saved=50,
        rows_skipped_existing=0,
        empty_windows=0,
        dry_run=False,
        started_at=_T0,
        ended_at=_T1,
    )


def test_survivorship_partial_when_failures() -> None:
    bid = uuid4()
    repo = _started_repo(source="KRX", batch_id=bid)
    status = _finalize_via(
        SurvivorshipBackfillBatch, repo,
        _backfill_summary(batch_id=bid, failure_count=2),
    )
    assert status == BATCH_STATUS_PARTIAL


def test_survivorship_success_when_no_failures() -> None:
    bid = uuid4()
    repo = _started_repo(source="KRX", batch_id=bid)
    status = _finalize_via(
        SurvivorshipBackfillBatch, repo,
        _backfill_summary(batch_id=bid, failure_count=0),
    )
    assert status == BATCH_STATUS_SUCCESS


# =============================================================================
# 2. krx 우선순위 skipped > partial > success
# =============================================================================

def _krx_summary(
    *, batch_id: UUID, failure_count: int, skipped_reason: str | None
) -> KrxBatchSummary:
    return KrxBatchSummary(
        batch_id=batch_id,
        as_of=date(2024, 6, 28),
        market="KOSPI",
        skipped_reason=skipped_reason,
        universe_size=0 if skipped_reason else 100,
        success_count=0 if skipped_reason else 100 - failure_count,
        failure_count=failure_count,
        failures=tuple(
            (f"00{i}", "boom") for i in range(failure_count)
        ),
        conflicts=(),
        market_cap_rows=(),
        dry_run=False,
        started_at=_T0,
        ended_at=_T1,
    )


def test_krx_skipped_takes_priority_over_partial() -> None:
    """skipped_reason 있으면 failure>0 이어도 'skipped' (skipped > partial)."""
    bid = uuid4()
    repo = _started_repo(source="KRX", market="KOSPI", batch_id=bid)
    status = _finalize_via(
        KrxDailyBatch, repo,
        _krx_summary(
            batch_id=bid, failure_count=5, skipped_reason="non_business_day"
        ),
    )
    assert status == BATCH_STATUS_SKIPPED


def test_krx_partial_when_failures_and_not_skipped() -> None:
    bid = uuid4()
    repo = _started_repo(source="KRX", market="KOSPI", batch_id=bid)
    status = _finalize_via(
        KrxDailyBatch, repo,
        _krx_summary(batch_id=bid, failure_count=3, skipped_reason=None),
    )
    assert status == BATCH_STATUS_PARTIAL


def test_krx_success_when_clean() -> None:
    bid = uuid4()
    repo = _started_repo(source="KRX", market="KOSPI", batch_id=bid)
    status = _finalize_via(
        KrxDailyBatch, repo,
        _krx_summary(batch_id=bid, failure_count=0, skipped_reason=None),
    )
    assert status == BATCH_STATUS_SUCCESS


# =============================================================================
# 3. §2.10 회귀 가드 — partial 도 freeze 후보 + latest_successful (byte-동일)
# =============================================================================

def test_partial_batch_is_latest_successful_candidate() -> None:
    """latest_successful 가 partial 배치를 집어야 함 (success 와 자격 동일)."""
    repo = FakeBatchRunRepository()
    bid = uuid4()
    repo.start(run_id=bid, market=None, source="DART", started_at=_T0)
    repo.finalize(
        run_id=bid, ended_at=_T1, success_count=8,
        status=BATCH_STATUS_PARTIAL,
    )
    found = repo.latest_successful("DART")
    assert found is not None
    assert found.id == bid
    assert found.status == BATCH_STATUS_PARTIAL


def test_skipped_batch_is_not_latest_successful() -> None:
    """skipped 는 데이터 미생산이라 latest_successful 에서 제외."""
    repo = FakeBatchRunRepository()
    bid = uuid4()
    repo.start(run_id=bid, market="KOSPI", source="KRX", started_at=_T0)
    repo.finalize(
        run_id=bid, ended_at=_T1, success_count=0,
        status=BATCH_STATUS_SKIPPED,
    )
    assert repo.latest_successful("KRX") is None


def test_partial_chosen_over_older_success() -> None:
    """더 최신 partial 배치가 옛 success 배치보다 우선 (자격 동등 + 최신성)."""
    repo = FakeBatchRunRepository()
    old = uuid4()
    new = uuid4()
    repo.start(run_id=old, market=None, source="DART", started_at=_T0)
    repo.finalize(
        run_id=old, ended_at=_T0, success_count=10,
        status=BATCH_STATUS_SUCCESS,
    )
    repo.start(
        run_id=new, market=None, source="DART",
        started_at=_T0 + timedelta(days=1),
    )
    repo.finalize(
        run_id=new, ended_at=_T1 + timedelta(days=1), success_count=9,
        status=BATCH_STATUS_PARTIAL,
    )
    found = repo.latest_successful("DART")
    assert found is not None
    assert found.id == new

# Note: collect_batch_versions(SQL) 의 partial 후보 포함 / skipped 제외 §2.10
# 가드는 SQL session fixture 가 있는 tests/test_db/test_collect_batch_versions.py
# (test_partial_batch_is_freeze_candidate 등 3건) 에서 실증한다.
