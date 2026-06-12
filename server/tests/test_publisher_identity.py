"""publisher_identity 헬퍼 단위 테스트 — ADR-0034 D4 사칭 차단.

테스트 매트릭스:
    1. parse_v2_publisher — @x/y → x, v1 slug(user/x, community/x) → None, 형식
       위반(@/x, @x) → None.
    2. assert_handle_not_reserved — 예약 handle(community/user/speculum-builtin/
       speculum) 거부, 일반 handle 통과.
    3. verify_publisher_owns_slug — 일치 통과 / 불일치 raise / v1 무관 통과 /
       미claim raise.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.repositories.publisher_repository import FakePublisherRepository
from app.services.publisher_identity import (
    PublisherImpersonationError,
    ReservedHandleError,
    assert_handle_not_reserved,
    parse_v2_publisher,
    verify_publisher_owns_slug,
)

_USER_A = UUID("00000000-0000-0000-0000-00000000000a")
_USER_B = UUID("00000000-0000-0000-0000-00000000000b")


# =============================================================================
# parse_v2_publisher
# =============================================================================

@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("@alice/value-pack", "alice"),
        ("@bob/x", "bob"),
        ("user/p", None),
        ("community/p", None),
        ("speculum-builtin", None),
        ("@/p", None),       # publisher 비어있음 — 형식 위반.
        ("@alice", None),    # `/` 없음 — 형식 위반.
    ],
)
def test_parse_v2_publisher(slug: str, expected: str | None) -> None:
    assert parse_v2_publisher(slug) == expected


# =============================================================================
# assert_handle_not_reserved
# =============================================================================

@pytest.mark.parametrize(
    "handle", ["community", "user", "speculum-builtin", "speculum"],
)
def test_reserved_handles_rejected(handle: str) -> None:
    with pytest.raises(ReservedHandleError):
        assert_handle_not_reserved(handle)


@pytest.mark.parametrize("handle", ["alice", "bob-pack", "user2", "communityx"])
def test_non_reserved_handles_pass(handle: str) -> None:
    # raise 안 하면 통과.
    assert_handle_not_reserved(handle)


# =============================================================================
# verify_publisher_owns_slug
# =============================================================================

def test_verify_matching_publisher_passes() -> None:
    repo = FakePublisherRepository()
    repo.claim(user_id=_USER_A, handle="alice")
    # 발급 user 의 claim handle 과 slug publisher 일치 — 통과.
    verify_publisher_owns_slug(
        user_id=_USER_A, pack_slug="@alice/value", publisher_repo=repo,
    )


def test_verify_mismatched_publisher_raises() -> None:
    repo = FakePublisherRepository()
    repo.claim(user_id=_USER_A, handle="alice")
    # _USER_A 가 @bob/... 발급 시도 — 타 publisher 사칭(불일치).
    with pytest.raises(PublisherImpersonationError):
        verify_publisher_owns_slug(
            user_id=_USER_A, pack_slug="@bob/value", publisher_repo=repo,
        )


def test_verify_unclaimed_user_raises() -> None:
    repo = FakePublisherRepository()
    # 미claim user 가 v2 slug 발급 시도 — claim 선행 필요(사칭).
    with pytest.raises(PublisherImpersonationError):
        verify_publisher_owns_slug(
            user_id=_USER_B, pack_slug="@bob/value", publisher_repo=repo,
        )


def test_verify_v1_slug_passes_regardless() -> None:
    repo = FakePublisherRepository()
    # v1 slug — publisher 사칭 무관(미claim 이어도 통과).
    verify_publisher_owns_slug(
        user_id=_USER_A, pack_slug="user/my-pack", publisher_repo=repo,
    )
    verify_publisher_owns_slug(
        user_id=_USER_A, pack_slug="community/shared", publisher_repo=repo,
    )
