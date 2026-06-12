"""Publisher Repository — ADR-0034 D4 인증 publisher handle claim (사칭 차단).

M6 #3 — 인증 user 가 자기 publisher handle 을 claim 하는 영속화 계약. custom_pack
_repository.py 의 frozen dataclass + Protocol + Fake 구조를 복제하되, **append-
only** (UPDATE 없이 claim/get 만) 이며 **handle 인스턴스 유일** + **user 1:1**
불변식을 강제한다.

설계 원칙 (ADR-0034 D4 / §2.1):
1. **user 1:1** — 한 user 는 정확히 하나의 handle 만 claim. 두 번째 claim 은
   PublisherClaimError(이미 claim). DB UNIQUE(user_id) 로도 강제.
2. **handle 인스턴스 유일** — 같은 handle 을 두 user 가 점유 불가(사칭 차단 —
   `@{handle}/...` v2 pack 발급권은 단일 user 에 귀속). DB unique Index 로 강제.
3. **append-only** — handle 변경/이전 경로 없음(claim 만). UPDATE 메서드 부재.
4. **예약 handle 거부는 service/route** — repository 는 중복(handle/user)만
   판정. 예약어(canonical/v1 tier 사칭) 차단은 publisher_identity.py 담당.

claim 은 인증 route(POST /api/publishers) + repository 뿐 — 시스템 생성 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

__all__ = [
    "FakePublisherRepository",
    "Publisher",
    "PublisherClaimError",
    "PublisherRepository",
]


class PublisherClaimError(Exception):
    """publisher claim invariant 위반 — handle 중복(인스턴스 유일) 또는 user 1:1 위반.

    같은 handle 을 다른 user 가 이미 claim(사칭 차단) 했거나, 같은 user 가 이미
    다른 handle 을 claim 한 경우 본 예외로 신호. route 가 409(Conflict)로 매핑한다.
    """


# =============================================================================
# Domain entity — frozen dataclass (codebase 일관)
# =============================================================================

@dataclass(frozen=True, slots=True)
class Publisher:
    """publishers row 의 in-memory representation.

    Attributes:
        id: Publisher UUID.
        user_id: 소유자 — handle 발급권의 귀속 user(사칭 차단 기준).
        handle: `@{handle}/...` v2 namespace 의 publisher 식별자. 인스턴스 유일.
        created_at: claim 시각 (UTC). **updated_at 없음** — append-only(1 회성 사실).
    """

    id: UUID
    user_id: UUID
    handle: str
    created_at: datetime


# =============================================================================
# Publisher Repository Protocol
# =============================================================================

@runtime_checkable
class PublisherRepository(Protocol):
    """Publisher claim 영속화 contract — **append-only**(claim/get 만, UPDATE 없음)."""

    def claim(self, *, user_id: UUID, handle: str) -> Publisher:
        """user 의 publisher handle 을 claim(append-only).

        handle 인스턴스 유일 + user 1:1 강제:
            - 같은 handle 을 다른 user 가 이미 claim → PublisherClaimError(사칭 차단).
            - 같은 user 가 이미 다른 handle 을 claim → PublisherClaimError(1:1 위반).
            - 같은 user + 같은 handle 재요청 → idempotent(기존 Publisher 반환).

        Raises:
            PublisherClaimError: handle 중복(인스턴스 유일) 또는 user 1:1 위반.
        """
        ...

    def get_by_user_id(self, user_id: UUID) -> Publisher | None:
        """user 가 claim 한 publisher. 미claim 시 None — UNIQUE(user_id) 라 최대 1 건."""
        ...

    def get_by_handle(self, handle: str) -> Publisher | None:
        """handle 을 점유한 publisher. 미점유 시 None — unique 라 최대 1 건."""
        ...


# =============================================================================
# Fake — contract reference (in-memory)
# =============================================================================

class FakePublisherRepository(PublisherRepository):
    """In-memory publisher store — contract reference (append-only, 사칭 차단)."""

    def __init__(self) -> None:
        self._by_id: dict[UUID, Publisher] = {}

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def claim(self, *, user_id: UUID, handle: str) -> Publisher:
        # idempotent / 충돌 판정 — handle / user 기존 점유 탐색.
        by_handle = self.get_by_handle(handle)
        if by_handle is not None:
            if by_handle.user_id == user_id:
                # 같은 user + 같은 handle 재요청 — idempotent(기존 반환).
                return by_handle
            # 다른 user 가 같은 handle 점유 — 사칭 차단(인스턴스 유일 위반).
            raise PublisherClaimError(
                f"handle '{handle}' 는 이미 다른 사용자가 점유함 — "
                f"publisher handle 은 인스턴스 유일(ADR-0034 D4 사칭 차단)."
            )
        by_user = self.get_by_user_id(user_id)
        if by_user is not None:
            # 같은 user 가 이미 다른 handle claim — user 1:1 위반.
            raise PublisherClaimError(
                f"사용자는 이미 handle '{by_user.handle}' 를 claim 함 — "
                f"user 당 publisher handle 은 1 개(ADR-0034 D4, §2.1 user 1:1)."
            )
        record = Publisher(
            id=uuid4(),
            user_id=user_id,
            handle=handle,
            created_at=self._now(),
        )
        self._by_id[record.id] = record
        return record

    def get_by_user_id(self, user_id: UUID) -> Publisher | None:
        # user_id 점유 — UNIQUE(user_id) 라 최대 1 건.
        for p in self._by_id.values():
            if p.user_id == user_id:
                return p
        return None

    def get_by_handle(self, handle: str) -> Publisher | None:
        # handle 점유 — unique Index 라 최대 1 건.
        for p in self._by_id.values():
            if p.handle == handle:
                return p
        return None
