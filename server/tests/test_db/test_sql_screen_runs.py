"""SqlScreenRunRepository 단위 테스트 — T13 Phase C contract.

테스트 매트릭스:
    1. save + fetch_by_id round-trip (nested query JSON 직렬화)
    2. fetch_by_id owner mismatch → None
    3. fetch_by_id 미존재 → None
    4. fetch_recent — computed_at desc + limit
    5. fetch_recent limit=0 / 음수
    6. save id 중복 → overwrite (Fake 와 동일 M0 정책)
    7. ScreenRunQuery 의 conditions / selected_factors / presentation_order 보존
    8. data_versions MappingProxyType round-trip
    9. Fake/SQL contract 동등성
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.repositories.screen_run_repository import FakeScreenRunRepository
from app.repositories.sql_user_repositories import SqlScreenRunRepository
from app.services.screen_run import (
    ScreenRunBuilder,
    ScreenRunQuery,
    ScreenRunSnapshot,
)

_USER_A = UUID("00000000-0000-0000-0000-00000000000a")
_USER_B = UUID("00000000-0000-0000-0000-00000000000b")


def _make_snapshot(
    *,
    run_id: UUID | None = None,
    user_id: UUID = _USER_A,
    conditions: list[dict[str, str]] | None = None,
    selected_factors: list[str] | None = None,
    as_of: date = date(2024, 5, 7),
    result_codes: list[str] | None = None,
    data_versions: dict[str, str] | None = None,
    computed_at: datetime | None = None,
) -> ScreenRunSnapshot:
    return ScreenRunBuilder.build(
        run_id=run_id or uuid4(),
        user_id=user_id,
        conditions=conditions or [
            {"factor": "per:ttm", "op": "<", "value": "10"},
        ],
        selected_factors=selected_factors or ["per:ttm", "pbr:ttm"],
        as_of=as_of,
        result_codes=result_codes or ["005930", "000660"],
        data_versions=data_versions or {
            "factor_pack": "1.0.0",
            "pit_policy": "1.0.0",
        },
        computed_at=computed_at or datetime(2024, 5, 7, 16, 30, tzinfo=UTC),
    )


# =============================================================================
# 1. save + fetch_by_id round-trip
# =============================================================================

def test_save_and_fetch_by_id(db_session: Session) -> None:
    repo = SqlScreenRunRepository(db_session)
    snap = _make_snapshot()
    repo.save(snap)
    fetched = repo.fetch_by_id(snap.id, user_id=_USER_A)
    assert fetched is not None
    assert fetched.id == snap.id
    assert fetched.user_id == _USER_A
    assert fetched.as_of == date(2024, 5, 7)
    assert fetched.result_codes == ("000660", "005930")  # sorted by normalize.
    # nested query JSON 직렬화 → MappingProxyType view 로 round-trip.
    assert fetched.query.selected_factors == ("pbr:ttm", "per:ttm")  # sorted.
    assert len(fetched.query.conditions) == 1
    assert dict(fetched.query.conditions[0]) == {
        "factor": "per:ttm", "op": "<", "value": "10",
    }
    # data_versions immutable view.
    assert fetched.data_versions["factor_pack"] == "1.0.0"
    # result_hash 보존.
    assert fetched.result_hash == snap.result_hash


# =============================================================================
# 2. owner mismatch → None
# =============================================================================

def test_fetch_by_id_owner_mismatch_returns_none(db_session: Session) -> None:
    repo = SqlScreenRunRepository(db_session)
    snap = _make_snapshot(user_id=_USER_A)
    repo.save(snap)
    assert repo.fetch_by_id(snap.id, user_id=_USER_B) is None


def test_fetch_by_id_missing_returns_none(db_session: Session) -> None:
    repo = SqlScreenRunRepository(db_session)
    assert repo.fetch_by_id(uuid4(), user_id=_USER_A) is None


# =============================================================================
# 3. fetch_recent — desc + limit
# =============================================================================

def test_fetch_recent_orders_by_computed_at_desc(db_session: Session) -> None:
    repo = SqlScreenRunRepository(db_session)
    older = _make_snapshot(
        computed_at=datetime(2024, 5, 1, 12, 0, tzinfo=UTC),
    )
    newer = _make_snapshot(
        computed_at=datetime(2024, 5, 7, 12, 0, tzinfo=UTC),
    )
    repo.save(older)
    repo.save(newer)
    recent = repo.fetch_recent(user_id=_USER_A, limit=10)
    assert [r.id for r in recent] == [newer.id, older.id]


def test_fetch_recent_respects_limit(db_session: Session) -> None:
    repo = SqlScreenRunRepository(db_session)
    snaps = [
        _make_snapshot(
            computed_at=datetime(2024, 5, i + 1, tzinfo=UTC),
        )
        for i in range(5)
    ]
    for s in snaps:
        repo.save(s)
    recent = repo.fetch_recent(user_id=_USER_A, limit=3)
    assert len(recent) == 3
    # 가장 새 3개 — 5, 4, 3일.
    assert recent[0].computed_at.day == 5
    assert recent[2].computed_at.day == 3


def test_fetch_recent_filters_by_user(db_session: Session) -> None:
    repo = SqlScreenRunRepository(db_session)
    repo.save(_make_snapshot(user_id=_USER_A))
    repo.save(_make_snapshot(user_id=_USER_B))
    user_a_runs = repo.fetch_recent(user_id=_USER_A, limit=10)
    assert len(user_a_runs) == 1
    assert user_a_runs[0].user_id == _USER_A


def test_fetch_recent_limit_zero_returns_empty(db_session: Session) -> None:
    repo = SqlScreenRunRepository(db_session)
    repo.save(_make_snapshot())
    assert repo.fetch_recent(user_id=_USER_A, limit=0) == ()


def test_fetch_recent_negative_limit_raises(db_session: Session) -> None:
    repo = SqlScreenRunRepository(db_session)
    with pytest.raises(ValueError, match="limit must be >= 0"):
        repo.fetch_recent(user_id=_USER_A, limit=-1)


# =============================================================================
# 4. save id 중복 → overwrite (Fake 와 동일 정책)
# =============================================================================

def test_save_overwrites_same_id(db_session: Session) -> None:
    repo = SqlScreenRunRepository(db_session)
    run_id = uuid4()
    first = _make_snapshot(
        run_id=run_id,
        result_codes=["005930"],
        computed_at=datetime(2024, 5, 1, tzinfo=UTC),
    )
    repo.save(first)
    # 같은 id 로 다른 데이터 save.
    second = _make_snapshot(
        run_id=run_id,
        result_codes=["005930", "000660"],
        computed_at=datetime(2024, 5, 7, tzinfo=UTC),
    )
    repo.save(second)
    # fetch 시 second 가 보존.
    fetched = repo.fetch_by_id(run_id, user_id=_USER_A)
    assert fetched is not None
    assert fetched.result_codes == ("000660", "005930")
    assert fetched.computed_at == datetime(2024, 5, 7, tzinfo=UTC)


# =============================================================================
# 5. nested query JSON round-trip — presentation_order 보존
# =============================================================================

def test_nested_query_round_trip_with_presentation_order(
    db_session: Session,
) -> None:
    """ScreenRunQuery 의 presentation_order tuple JSON round-trip."""
    repo = SqlScreenRunRepository(db_session)
    snap = ScreenRunSnapshot(
        id=uuid4(),
        user_id=_USER_A,
        query=ScreenRunQuery(
            conditions=(
                MappingProxyType({"factor": "per:ttm", "op": "<", "value": "10"}),
                MappingProxyType({"factor": "pbr:ttm", "op": "<", "value": "1"}),
            ),
            selected_factors=("per:ttm", "pbr:ttm"),
            presentation_order=(0, 1),
        ),
        as_of=date(2024, 5, 7),
        result_codes=("005930",),
        result_hash="sha256:" + "a" * 64,
        data_versions=MappingProxyType({"factor_pack": "1.0.0"}),
        computed_at=datetime(2024, 5, 7, tzinfo=UTC),
    )
    repo.save(snap)
    fetched = repo.fetch_by_id(snap.id, user_id=_USER_A)
    assert fetched is not None
    assert fetched.query.presentation_order == (0, 1)
    assert len(fetched.query.conditions) == 2


def test_nested_query_round_trip_without_presentation_order(
    db_session: Session,
) -> None:
    """presentation_order=None 도 JSON null 로 round-trip."""
    repo = SqlScreenRunRepository(db_session)
    snap = ScreenRunSnapshot(
        id=uuid4(),
        user_id=_USER_A,
        query=ScreenRunQuery(
            conditions=(MappingProxyType({"factor": "per:ttm", "op": "<", "value": "10"}),),
            selected_factors=("per:ttm",),
            presentation_order=None,
        ),
        as_of=date(2024, 5, 7),
        result_codes=("005930",),
        result_hash="sha256:" + "b" * 64,
        data_versions=MappingProxyType({}),
        computed_at=datetime(2024, 5, 7, tzinfo=UTC),
    )
    repo.save(snap)
    fetched = repo.fetch_by_id(snap.id, user_id=_USER_A)
    assert fetched is not None
    assert fetched.query.presentation_order is None


# =============================================================================
# 6. Fake/SQL contract 동등성
# =============================================================================

def test_fake_sql_contract_equivalence_fetch_recent(
    db_session: Session,
) -> None:
    """같은 fixture 에서 Fake/SQL 동일 fetch_recent 결과."""
    sql_repo = SqlScreenRunRepository(db_session)
    fake_repo = FakeScreenRunRepository()

    older = _make_snapshot(
        computed_at=datetime(2024, 5, 1, tzinfo=UTC),
    )
    newer = _make_snapshot(
        computed_at=datetime(2024, 5, 7, tzinfo=UTC),
    )
    for repo in (sql_repo, fake_repo):
        repo.save(older)
        repo.save(newer)

    sql_recent = sql_repo.fetch_recent(user_id=_USER_A, limit=10)
    fake_recent = fake_repo.fetch_recent(user_id=_USER_A, limit=10)
    # id sequence 동일 (computed_at desc).
    assert [r.id for r in sql_recent] == [r.id for r in fake_recent]
