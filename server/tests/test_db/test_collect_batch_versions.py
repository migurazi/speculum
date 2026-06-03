"""M1 T48b — collect_batch_versions / collect_run_data_versions 테스트.

테스트 매트릭스:
    1. 빈 batch_runs → krx/dart batch_id 모두 "" (미존재 = empty string).
    2. source 별 max(started_at) 성공 batch 선택.
    3. as_of 필터 — started_at::date > as_of 인 batch 제외.
    4. status 필터 — 'running' / 'skipped' batch 제외 (success 만).
    5. collect_run_data_versions(session) → 13 키 (정책 11 + batch 2).
    6. collect_run_data_versions(None) → 정책-only 11 키 (Fake-mode 하위호환).
    7. collect_active_policy_versions() 무인자 순수 함수 불변 (11 키, batch 무관).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.orm.batch_runs import (
    BATCH_STATUS_RUNNING,
    BATCH_STATUS_SKIPPED,
    BATCH_STATUS_SUCCESS,
    BatchRunORM,
)
from app.services.snapshot_versions import (
    collect_active_policy_versions,
    collect_batch_versions,
    collect_run_data_versions,
)


def _insert_batch(
    session: Session,
    *,
    source: str,
    started: datetime,
    status: str = BATCH_STATUS_SUCCESS,
    market: str | None = None,
) -> UUID:
    rid = uuid4()
    session.add(
        BatchRunORM(
            id=rid,
            market=market,
            source=source,
            started_at=started,
            ended_at=started,
            success_count=1,
            status=status,
        )
    )
    session.flush()
    return rid


def _utc(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 9, 0, 0, tzinfo=UTC)


# =============================================================================
# 1. 빈 batch_runs
# =============================================================================

def test_empty_batch_runs_returns_empty_strings(db_session: Session) -> None:
    result = collect_batch_versions(date(2024, 5, 7), db_session)
    assert result["krx_batch_id"] == ""
    assert result["dart_batch_id"] == ""
    assert set(result.keys()) == {"krx_batch_id", "dart_batch_id"}


# =============================================================================
# 2. source 별 max(started_at)
# =============================================================================

def test_selects_max_started_per_source(db_session: Session) -> None:
    _insert_batch(db_session, source="KRX", started=_utc(2024, 5, 1))
    latest_krx = _insert_batch(db_session, source="KRX", started=_utc(2024, 5, 7))
    latest_dart = _insert_batch(
        db_session, source="DART", started=_utc(2024, 5, 6),
    )
    _insert_batch(db_session, source="DART", started=_utc(2024, 5, 3))

    result = collect_batch_versions(date(2024, 5, 7), db_session)
    assert result["krx_batch_id"] == str(latest_krx)
    assert result["dart_batch_id"] == str(latest_dart)


# =============================================================================
# 3. as_of 필터
# =============================================================================

def test_as_of_filter_excludes_future_batches(db_session: Session) -> None:
    on_time = _insert_batch(db_session, source="KRX", started=_utc(2024, 5, 5))
    # as_of (5/5) 이후 시작된 batch → 제외.
    _insert_batch(db_session, source="KRX", started=_utc(2024, 5, 8))

    result = collect_batch_versions(date(2024, 5, 5), db_session)
    assert result["krx_batch_id"] == str(on_time)


def test_as_of_filter_includes_same_date(db_session: Session) -> None:
    """started_at::date == as_of 는 포함 (<= 경계)."""
    same_day = _insert_batch(db_session, source="KRX", started=_utc(2024, 5, 7))
    result = collect_batch_versions(date(2024, 5, 7), db_session)
    assert result["krx_batch_id"] == str(same_day)


# =============================================================================
# 4. status 필터 (success 만)
# =============================================================================

def test_status_filter_excludes_non_success(db_session: Session) -> None:
    # 더 최신이지만 running / skipped → 제외.
    _insert_batch(
        db_session, source="KRX", started=_utc(2024, 5, 9),
        status=BATCH_STATUS_RUNNING,
    )
    _insert_batch(
        db_session, source="KRX", started=_utc(2024, 5, 8),
        status=BATCH_STATUS_SKIPPED,
    )
    success = _insert_batch(
        db_session, source="KRX", started=_utc(2024, 5, 5),
        status=BATCH_STATUS_SUCCESS,
    )

    result = collect_batch_versions(date(2024, 5, 30), db_session)
    assert result["krx_batch_id"] == str(success)


# =============================================================================
# 5~6. collect_run_data_versions merge
# =============================================================================

def test_run_data_versions_with_session_has_14_keys(
    db_session: Session,
) -> None:
    krx = _insert_batch(db_session, source="KRX", started=_utc(2024, 5, 7))
    merged = collect_run_data_versions(date(2024, 5, 7), db_session)
    assert merged["krx_batch_id"] == str(krx)
    assert merged["dart_batch_id"] == ""
    # 정책 12 키 (M2 T72 distribution_policy_version 합류) + batch 2 키 = 14.
    assert len(merged) == 14
    # 정책 키도 그대로 존재.
    assert "factor_pack_content_hash" in merged
    assert merged["snapshot_schema_version"] == "1.2"


def test_run_data_versions_without_session_is_policy_only() -> None:
    merged = collect_run_data_versions(date(2024, 5, 7), None)
    # batch_id 키 합류 skip — 정책-only 12 키.
    assert "krx_batch_id" not in merged
    assert "dart_batch_id" not in merged
    assert dict(merged) == dict(collect_active_policy_versions())


# =============================================================================
# 7. collect_active_policy_versions 무인자 순수 함수 불변
# =============================================================================

def test_active_policy_versions_unchanged_12_keys() -> None:
    """C2 해소 — batch_id 합류가 무인자 정책 함수를 오염시키지 않음.

    M2 T72 — distribution_policy_version 합류로 11→12 (여전히 batch_id 무합류).
    """
    result = collect_active_policy_versions()
    assert len(result) == 12
    assert "krx_batch_id" not in result
    assert "dart_batch_id" not in result
