"""SqlNotesRepository + FakeNotesRepository 단위 테스트 — T76.

테스트 매트릭스 — Fake 와 동일 contract:
    1. create + list_for_code + get (CRUD happy path)
    2. owner-check — get/update/delete owner mismatch → None/False
    3. list_for_code 격리(P0) — user_id AND code_lineage_id 둘 다 필터
       (user_id predicate 누락 회귀 방지의 가장 위험한 누출 게이트)
    4. scope invariant — USER_SHARED create → NotesDataError (SHARED 봉쇄 증명)
    5. body 길이 cap(10000) 초과 → NotesDataError
    6. mutable update — body + updated_at 갱신, created_at 보존
    7. Fake/SQL contract 동등성
    8. ORM create_all 의 stock_notes 컬럼 / 인덱스 검증
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.repositories.notes_repository import (
    FakeNotesRepository,
    NotesDataError,
    NotesRepository,
)
from app.repositories.sql_notes_repository import SqlNotesRepository

# conftest 가 users 에 seed 한 표준 user_id (FK 충족).
_USER_A = UUID("00000000-0000-0000-0000-00000000000a")
_USER_B = UUID("00000000-0000-0000-0000-00000000000b")

_CODE_A = UUID("11111111-1111-1111-1111-111111111111")
_CODE_B = UUID("22222222-2222-2222-2222-222222222222")


# repo factory 들 — Fake/SQL 동일 contract 를 parametrize 로 양쪽 구동.
def _sql_repo(db_session: Session) -> SqlNotesRepository:
    return SqlNotesRepository(db_session)


def _fake_repo(_db_session: Session) -> FakeNotesRepository:
    return FakeNotesRepository()


_REPO_FACTORIES = (_sql_repo, _fake_repo)


# =============================================================================
# CRUD happy path
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_create_and_list_and_get(db_session: Session, factory) -> None:
    repo: NotesRepository = factory(db_session)
    n1 = repo.create(user_id=_USER_A, code_lineage_id=_CODE_A, body="메모1")
    repo.create(user_id=_USER_A, code_lineage_id=_CODE_A, body="메모2")
    # 다른 종목 — 섞이지 않음.
    repo.create(user_id=_USER_A, code_lineage_id=_CODE_B, body="다른종목")

    notes = repo.list_for_code(_CODE_A, user_id=_USER_A)
    assert {n.body for n in notes} == {"메모1", "메모2"}
    # scope 는 user-private 고정.
    assert all(n.scope == "user-private" for n in notes)

    fetched = repo.get(n1.id, user_id=_USER_A)
    assert fetched is not None and fetched.body == "메모1"


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_update_mutable(db_session: Session, factory) -> None:
    repo: NotesRepository = factory(db_session)
    n = repo.create(user_id=_USER_A, code_lineage_id=_CODE_A, body="원본")
    updated = repo.update(n.id, "수정본", user_id=_USER_A)
    assert updated is not None
    assert updated.body == "수정본"
    assert updated.id == n.id
    # mutable — created_at 보존, updated_at 갱신(>= 원본).
    assert updated.created_at == n.created_at
    assert updated.updated_at >= n.created_at


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_delete(db_session: Session, factory) -> None:
    repo: NotesRepository = factory(db_session)
    n = repo.create(user_id=_USER_A, code_lineage_id=_CODE_A, body="지울 메모")
    assert repo.delete(n.id, user_id=_USER_A) is True
    assert repo.get(n.id, user_id=_USER_A) is None
    assert repo.list_for_code(_CODE_A, user_id=_USER_A) == ()


# =============================================================================
# owner-check (IDOR)
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_get_owner_mismatch_returns_none(db_session: Session, factory) -> None:
    repo: NotesRepository = factory(db_session)
    n = repo.create(user_id=_USER_A, code_lineage_id=_CODE_A, body="A's")
    assert repo.get(n.id, user_id=_USER_B) is None


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_update_owner_mismatch_returns_none(db_session: Session, factory) -> None:
    repo: NotesRepository = factory(db_session)
    n = repo.create(user_id=_USER_A, code_lineage_id=_CODE_A, body="A's")
    assert repo.update(n.id, "stolen", user_id=_USER_B) is None
    # A 의 메모 body 불변.
    assert repo.get(n.id, user_id=_USER_A).body == "A's"


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_delete_owner_mismatch_returns_false(db_session: Session, factory) -> None:
    repo: NotesRepository = factory(db_session)
    n = repo.create(user_id=_USER_A, code_lineage_id=_CODE_A, body="A's")
    assert repo.delete(n.id, user_id=_USER_B) is False
    assert repo.get(n.id, user_id=_USER_A) is not None


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_list_for_code_user_isolation_p0(db_session: Session, factory) -> None:
    """list_for_code 는 user_id AND code_lineage_id 둘 다 필터 (P0 누출 게이트).

    A·B 가 같은 종목에 메모 — 각자 list 는 자기 것만. user_id predicate 누락 시
    두 user 메모가 섞여 전 사용자 누출(가장 위험한 회귀).
    """
    repo: NotesRepository = factory(db_session)
    repo.create(user_id=_USER_A, code_lineage_id=_CODE_A, body="A note")
    repo.create(user_id=_USER_B, code_lineage_id=_CODE_A, body="B note")

    a_notes = repo.list_for_code(_CODE_A, user_id=_USER_A)
    assert [n.body for n in a_notes] == ["A note"]

    b_notes = repo.list_for_code(_CODE_A, user_id=_USER_B)
    assert [n.body for n in b_notes] == ["B note"]


# =============================================================================
# scope invariant — USER_SHARED 봉쇄
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_create_user_shared_scope_raises(db_session: Session, factory) -> None:
    """scope=user-shared create → NotesDataError (SHARED 미구현, 봉쇄 증명)."""
    repo: NotesRepository = factory(db_session)
    with pytest.raises(NotesDataError, match="user-private"):
        repo.create(
            user_id=_USER_A,
            code_lineage_id=_CODE_A,
            body="공유 시도",
            scope="user-shared",
        )


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_create_system_scope_raises(db_session: Session, factory) -> None:
    """scope=system create 도 거부 — user-private 전용."""
    repo: NotesRepository = factory(db_session)
    with pytest.raises(NotesDataError):
        repo.create(
            user_id=_USER_A,
            code_lineage_id=_CODE_A,
            body="시스템 시도",
            scope="system",
        )


# =============================================================================
# body 길이 / 공백 cap
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_create_body_too_long_raises(db_session: Session, factory) -> None:
    repo: NotesRepository = factory(db_session)
    with pytest.raises(NotesDataError, match="exceeds"):
        repo.create(user_id=_USER_A, code_lineage_id=_CODE_A, body="x" * 10001)


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_create_empty_body_raises(db_session: Session, factory) -> None:
    repo: NotesRepository = factory(db_session)
    with pytest.raises(NotesDataError, match="non-empty"):
        repo.create(user_id=_USER_A, code_lineage_id=_CODE_A, body="   ")


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_update_body_too_long_raises(db_session: Session, factory) -> None:
    repo: NotesRepository = factory(db_session)
    n = repo.create(user_id=_USER_A, code_lineage_id=_CODE_A, body="ok")
    with pytest.raises(NotesDataError, match="exceeds"):
        repo.update(n.id, "x" * 10001, user_id=_USER_A)


# =============================================================================
# ORM create_all — 컬럼 / 인덱스 검증
# =============================================================================

def test_orm_create_all_includes_stock_notes() -> None:
    """ORM create_all 의 stock_notes 컬럼/인덱스 set (migration drift 차단)."""
    from app.db import orm  # noqa: F401  ← Base.metadata 등록 side-effect
    from app.db.base import Base

    engine: Engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("stock_notes")}
        assert {
            "id", "user_id", "code_lineage_id", "body", "scope",
            "created_at", "updated_at",
        } <= cols

        index_names = {ix["name"] for ix in insp.get_indexes("stock_notes")}
        assert "ix_stock_notes_user_id" in index_names
        assert "ix_stock_notes_user_code" in index_names

        # 복합 인덱스 컬럼 순서 — (user_id, code_lineage_id).
        composite = next(
            ix for ix in insp.get_indexes("stock_notes")
            if ix["name"] == "ix_stock_notes_user_code"
        )
        assert composite["column_names"] == ["user_id", "code_lineage_id"]
    finally:
        engine.dispose()
