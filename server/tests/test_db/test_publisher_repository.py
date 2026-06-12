"""PublisherRepository (Fake + SQL) contract 단위 테스트 — ADR-0034 D4.

테스트 매트릭스 — Fake 와 SQL 동일 contract:
    1. claim happy path + get_by_user_id / get_by_handle
    2. handle 인스턴스 유일 — 다른 user 가 같은 handle claim → PublisherClaimError
    3. user 1:1 — 같은 user 가 다른 handle claim → PublisherClaimError
    4. idempotent — 같은 user + 같은 handle 재claim → 기존 반환
    5. 미claim 조회 — get_by_user_id / get_by_handle → None

Fake 와 SQL 을 같은 테스트로 parametrize(contract 동등성). SQL 은 conftest 의
db_session(users seed _USER_A/_USER_B)을 사용해 FK 충족.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.repositories.publisher_repository import (
    FakePublisherRepository,
    PublisherClaimError,
)
from app.repositories.sql_publisher_repository import SqlPublisherRepository

# conftest 가 users 에 seed 한 표준 user_id (FK 충족).
_USER_A = UUID("00000000-0000-0000-0000-00000000000a")
_USER_B = UUID("00000000-0000-0000-0000-00000000000b")


def _sql_repo(db_session: Session) -> SqlPublisherRepository:
    return SqlPublisherRepository(db_session)


def _fake_repo(_db_session: Session) -> FakePublisherRepository:
    return FakePublisherRepository()


_REPO_FACTORIES = (_sql_repo, _fake_repo)


# =============================================================================
# claim happy path + 조회
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_claim_and_get(factory, db_session: Session) -> None:  # noqa: ANN001
    repo = factory(db_session)
    publisher = repo.claim(user_id=_USER_A, handle="alice")
    assert publisher.user_id == _USER_A
    assert publisher.handle == "alice"
    assert publisher.created_at is not None

    by_user = repo.get_by_user_id(_USER_A)
    assert by_user is not None
    assert by_user.handle == "alice"

    by_handle = repo.get_by_handle("alice")
    assert by_handle is not None
    assert by_handle.user_id == _USER_A


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_get_unclaimed_returns_none(factory, db_session: Session) -> None:  # noqa: ANN001
    repo = factory(db_session)
    assert repo.get_by_user_id(_USER_A) is None
    assert repo.get_by_handle("nobody") is None


# =============================================================================
# handle 인스턴스 유일 (사칭 차단)
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_handle_uniqueness_blocks_other_user(  # noqa: ANN001
    factory, db_session: Session,
) -> None:
    repo = factory(db_session)
    repo.claim(user_id=_USER_A, handle="alice")
    # 다른 user 가 같은 handle 점유 시도 — 사칭 차단(인스턴스 유일).
    with pytest.raises(PublisherClaimError):
        repo.claim(user_id=_USER_B, handle="alice")


# =============================================================================
# user 1:1
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_user_one_to_one_blocks_second_handle(  # noqa: ANN001
    factory, db_session: Session,
) -> None:
    repo = factory(db_session)
    repo.claim(user_id=_USER_A, handle="alice")
    # 같은 user 가 다른 handle claim 시도 — user 1:1 위반.
    with pytest.raises(PublisherClaimError):
        repo.claim(user_id=_USER_A, handle="alice2")


# =============================================================================
# idempotent (같은 user + 같은 handle 재claim)
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_reclaim_same_is_idempotent(factory, db_session: Session) -> None:  # noqa: ANN001
    repo = factory(db_session)
    first = repo.claim(user_id=_USER_A, handle="alice")
    again = repo.claim(user_id=_USER_A, handle="alice")
    # 같은 user + 같은 handle — idempotent(기존 반환, 신규 row 없음).
    assert again.id == first.id
    assert again.handle == "alice"
