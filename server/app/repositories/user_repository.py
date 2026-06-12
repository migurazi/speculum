"""User Repository — T68 NextAuth JIT provision (ADR-0021 D1.1).

JWT 검증(`auth.get_current_user`, AUTH_SECRET 설정 시)이 google_sub 클레임을
실제 user_id 로 해소할 때 사용하는 repository. 두 method 만:

    - get_by_google_sub(sub) → UserRecord | None : 기존 user 조회.
    - provision(google_sub, email) → UserRecord : JIT 신규 생성 (uuid4).

해소 패턴 (auth.get_current_user):
    `user_repo.get_by_google_sub(sub) or user_repo.provision(sub, email)`.
    같은 google_sub 는 항상 같은 user_id 로 해소된다 (provision 은 미존재 시에만).

설계 (notes/watchlist repository 선례 일관):
    - 도메인 entity = frozen `UserRecord` (id / google_sub / email).
    - `UserRepository` Protocol + in-memory `FakeUserRepository` (contract reference).
    - **AUTH_SECRET 미설정(fallback) 경로는 본 repository 를 호출하지 않는다** —
      get_current_user 가 SYSTEM_USER_ID 만 반환 (user_repo 미사용). 따라서
      Fake 가 기본 wiring 이어도 기존 테스트(무토큰)는 본 repository 를 안 탄다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

__all__ = [
    "FakeUserRepository",
    "UserRecord",
    "UserRepository",
]


# =============================================================================
# Domain entity — frozen dataclass (codebase 일관)
# =============================================================================

@dataclass(frozen=True, slots=True)
class UserRecord:
    """users row 의 in-memory representation (OAuth 메타 포함).

    Attributes:
        id: 사용자 UUID — user 소유 데이터(watchlist/notes/runs) 격리의 키.
        google_sub: Google OAuth `sub` (안정 식별자). sentinel row 는 None.
        email: 표시용 이메일 (JWT email 클레임). None 가능.
    """

    id: UUID
    google_sub: str | None
    email: str | None


# =============================================================================
# Repository Protocol
# =============================================================================

@runtime_checkable
class UserRepository(Protocol):
    """User 조회 + JIT provision contract — T68 NextAuth.

    `auth.get_current_user`(AUTH_SECRET 설정 경로)만 사용. fallback 경로(미설정)
    는 본 repository 를 호출하지 않는다.
    """

    def get_by_google_sub(self, google_sub: str) -> UserRecord | None:
        """google_sub 로 기존 user 조회. 미존재 시 None."""
        ...

    def provision(
        self, google_sub: str, email: str | None,
    ) -> UserRecord:
        """JIT 신규 user 생성 — uuid4 id 부여. 첫 로그인 시 호출.

        Note:
            본 method 는 google_sub 가 미존재한다는 호출자 보장 하에 INSERT.
            get_by_google_sub 가 None 일 때만 호출하는 것이 계약(중복 google_sub
            는 DB unique 인덱스가 최종 방어).
        """
        ...


# =============================================================================
# Fake — contract reference (in-memory)
# =============================================================================

class FakeUserRepository(UserRepository):
    """In-memory User store — contract reference (테스트 + Fake-only mode)."""

    def __init__(self) -> None:
        # user_id → UserRecord. google_sub 역인덱스는 매 조회 선형 스캔(테스트
        # 규모상 무해 — 운영은 SqlUserRepository).
        self._users: dict[UUID, UserRecord] = {}

    def get_by_google_sub(self, google_sub: str) -> UserRecord | None:
        for record in self._users.values():
            if record.google_sub == google_sub:
                return record
        return None

    def provision(
        self, google_sub: str, email: str | None,
    ) -> UserRecord:
        record = UserRecord(
            id=uuid4(),
            google_sub=google_sub,
            email=email,
        )
        self._users[record.id] = record
        return record

    # 테스트 편의 — sentinel/seed 직접 주입 (운영 경로 미사용).
    def _seed(self, record: UserRecord) -> None:
        """기존 user row 를 직접 주입 (테스트 setup). created_at 무의미."""
        self._users[record.id] = record
