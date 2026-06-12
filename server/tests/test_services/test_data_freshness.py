"""데이터 신선도(stale) 진단 — M5 #4a (ADR-0033 D6) 서비스 테스트.

테스트 매트릭스:
    1. KRX 최근(영업일 ≤3) → not stale.
    2. KRX 오래(>3 영업일) → stale. 주말 경계(영업일 ≠ calendar) 정확.
    3. DART ≤100 calendar days → not stale, >100 → stale.
    4. batch 부재(KRX/DART) → stale + latest_batch_at/elapsed_days None.
    5. elapsed_days 는 calendar days 사실 (KRX 도), as_of = now 주입.
    6. now 주입 결정성 — 같은 입력 같은 결과.

now 는 전부 주입(결정성) — `datetime.now()` 직접 호출 0.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS
from app.repositories.batch_run_repository import FakeBatchRunRepository
from app.services.data_freshness import assess_data_freshness
from app.services.krx_calendar import DEFAULT_CALENDAR


def _seed_success(repo: FakeBatchRunRepository, *, source: str, ended_at: datetime) -> None:
    """source 의 성공 batch 1건 seed (started_at == ended_at 단순화)."""
    rid = uuid4()
    repo.start(run_id=rid, market=None, source=source, started_at=ended_at)
    repo.finalize(
        run_id=rid, ended_at=ended_at, success_count=1,
        status=BATCH_STATUS_SUCCESS,
    )


# =============================================================================
# 1~2. KRX — 영업일 기준 stale
# =============================================================================

def test_krx_recent_not_stale() -> None:
    repo = FakeBatchRunRepository()
    # 2024-05-07(화) batch, now 2024-05-10(금) = 3 영업일 경과 → not stale.
    _seed_success(repo, source="KRX", ended_at=datetime(2024, 5, 7, 2, tzinfo=UTC))
    f = assess_data_freshness(
        now=datetime(2024, 5, 10, 3, tzinfo=UTC),
        batch_run_repo=repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert f.krx.is_stale is False
    assert f.krx.latest_batch_at == datetime(2024, 5, 7, 2, tzinfo=UTC)
    # elapsed_days 는 calendar days (사실).
    assert f.krx.elapsed_days == 3


def test_krx_weekend_boundary_not_stale() -> None:
    """주말은 영업일 경과에 포함 안 됨 — calendar 5일 경과여도 영업일 3 → not stale."""
    repo = FakeBatchRunRepository()
    # 2024-05-07(화) batch, now 2024-05-12(일) = calendar 5일이나 영업일 3 (08·09·10).
    _seed_success(repo, source="KRX", ended_at=datetime(2024, 5, 7, 2, tzinfo=UTC))
    f = assess_data_freshness(
        now=datetime(2024, 5, 12, 3, tzinfo=UTC),
        batch_run_repo=repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert f.krx.is_stale is False
    # 표시(elapsed_days)는 calendar days = 5 (사실).
    assert f.krx.elapsed_days == 5


def test_krx_old_is_stale() -> None:
    """2024-05-07(화) batch, now 2024-05-13(월) = 4 영업일 경과 → stale."""
    repo = FakeBatchRunRepository()
    _seed_success(repo, source="KRX", ended_at=datetime(2024, 5, 7, 2, tzinfo=UTC))
    f = assess_data_freshness(
        now=datetime(2024, 5, 13, 3, tzinfo=UTC),
        batch_run_repo=repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert f.krx.is_stale is True
    assert f.krx.elapsed_days == 6


def test_krx_same_day_not_stale() -> None:
    repo = FakeBatchRunRepository()
    _seed_success(repo, source="KRX", ended_at=datetime(2024, 5, 7, 2, tzinfo=UTC))
    f = assess_data_freshness(
        now=datetime(2024, 5, 7, 23, tzinfo=UTC),
        batch_run_repo=repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert f.krx.is_stale is False
    assert f.krx.elapsed_days == 0


# =============================================================================
# 3. DART — calendar days 기준 stale (100일 임계)
# =============================================================================

def test_dart_recent_not_stale() -> None:
    repo = FakeBatchRunRepository()
    # now - 100일 = 경계 (100일 초과 아님) → not stale.
    _seed_success(repo, source="DART", ended_at=datetime(2024, 1, 1, 2, tzinfo=UTC))
    f = assess_data_freshness(
        now=datetime(2024, 4, 10, 3, tzinfo=UTC),  # 100일 경과 (2024-01-01 ~ 2024-04-10)
        batch_run_repo=repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert f.dart.elapsed_days == 100
    assert f.dart.is_stale is False


def test_dart_old_is_stale() -> None:
    repo = FakeBatchRunRepository()
    _seed_success(repo, source="DART", ended_at=datetime(2024, 1, 1, 2, tzinfo=UTC))
    f = assess_data_freshness(
        now=datetime(2024, 4, 11, 3, tzinfo=UTC),  # 101일 경과 → stale.
        batch_run_repo=repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert f.dart.elapsed_days == 101
    assert f.dart.is_stale is True


# =============================================================================
# 4. batch 부재 — 데이터 없음 = stale
# =============================================================================

def test_absent_batches_are_stale() -> None:
    repo = FakeBatchRunRepository()  # 비어 있음.
    f = assess_data_freshness(
        now=datetime(2024, 5, 10, 3, tzinfo=UTC),
        batch_run_repo=repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert f.krx.is_stale is True
    assert f.krx.latest_batch_at is None
    assert f.krx.elapsed_days is None
    assert f.dart.is_stale is True
    assert f.dart.latest_batch_at is None
    assert f.dart.elapsed_days is None
    # KOSIS 부재도 stale (krx/dart/kosis 모두 부재 → 모두 stale).
    assert f.kosis.is_stale is True
    assert f.kosis.latest_batch_at is None
    assert f.kosis.elapsed_days is None


# =============================================================================
# 5~6. as_of = now 주입, 결정성
# =============================================================================

def test_as_of_is_injected_now() -> None:
    repo = FakeBatchRunRepository()
    now = datetime(2024, 5, 10, 3, tzinfo=UTC)
    f = assess_data_freshness(now=now, batch_run_repo=repo, calendar=DEFAULT_CALENDAR)
    assert f.as_of == now


def test_deterministic_same_input() -> None:
    repo = FakeBatchRunRepository()
    _seed_success(repo, source="KRX", ended_at=datetime(2024, 5, 7, 2, tzinfo=UTC))
    _seed_success(repo, source="DART", ended_at=datetime(2024, 5, 7, 2, tzinfo=UTC))
    now = datetime(2024, 5, 10, 3, tzinfo=UTC)
    a = assess_data_freshness(now=now, batch_run_repo=repo, calendar=DEFAULT_CALENDAR)
    b = assess_data_freshness(now=now, batch_run_repo=repo, calendar=DEFAULT_CALENDAR)
    assert a == b


# =============================================================================
# 7. 캘린더 범위 밖 — 영업일 산정 불가 시 calendar days 로 보수 stale (silent 추정 0)
# =============================================================================

def test_krx_outside_calendar_range_is_stale() -> None:
    """now 가 verified 캘린더 범위(2024) 밖 → 영업일 산정 불가, calendar days 로 stale."""
    repo = FakeBatchRunRepository()
    _seed_success(repo, source="KRX", ended_at=datetime(2024, 5, 7, 2, tzinfo=UTC))
    # now 2026 — 캘린더 범위 밖. elapsed_days(calendar) 는 항상 산정 가능.
    f = assess_data_freshness(
        now=datetime(2026, 6, 4, 3, tzinfo=UTC),
        batch_run_repo=repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert f.krx.is_stale is True
    assert f.krx.elapsed_days is not None and f.krx.elapsed_days > 3


# =============================================================================
# KOSIS — 월간 freshness (45 calendar days 임계, M9 #3, ADR-0036 D7)
# =============================================================================

def test_kosis_fresh_within_45_days() -> None:
    """45일 이내 → not stale (경계 포함)."""
    repo = FakeBatchRunRepository()
    _seed_success(repo, source="KOSIS", ended_at=datetime(2024, 5, 1, 2, tzinfo=UTC))
    f = assess_data_freshness(
        now=datetime(2024, 6, 15, 3, tzinfo=UTC),  # 45일 경과 (경계)
        batch_run_repo=repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert f.kosis.elapsed_days == 45
    assert f.kosis.is_stale is False
    assert f.kosis.latest_batch_at == datetime(2024, 5, 1, 2, tzinfo=UTC)
    assert f.kosis.source == "KOSIS"


def test_kosis_stale_beyond_45_days() -> None:
    """46일 초과 → stale."""
    repo = FakeBatchRunRepository()
    _seed_success(repo, source="KOSIS", ended_at=datetime(2024, 5, 1, 2, tzinfo=UTC))
    f = assess_data_freshness(
        now=datetime(2024, 6, 16, 3, tzinfo=UTC),  # 46일 경과 → stale
        batch_run_repo=repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert f.kosis.elapsed_days == 46
    assert f.kosis.is_stale is True


def test_kosis_absent_batch_is_stale() -> None:
    """KOSIS batch 부재 → stale (데이터 없음), latest_batch_at/elapsed_days None."""
    repo = FakeBatchRunRepository()  # KOSIS batch 없음.
    f = assess_data_freshness(
        now=datetime(2024, 5, 10, 3, tzinfo=UTC),
        batch_run_repo=repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert f.kosis.is_stale is True
    assert f.kosis.latest_batch_at is None
    assert f.kosis.elapsed_days is None


def test_datafreshness_has_kosis_field() -> None:
    """DataFreshness.kosis 필드 존재 + SourceFreshness 타입 확인."""
    from app.services.data_freshness import DataFreshness, SourceFreshness
    repo = FakeBatchRunRepository()
    f = assess_data_freshness(
        now=datetime(2024, 5, 10, 3, tzinfo=UTC),
        batch_run_repo=repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert isinstance(f, DataFreshness)
    assert isinstance(f.kosis, SourceFreshness)
    assert f.kosis.source == "KOSIS"
