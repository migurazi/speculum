"""ScreenRunRepository Protocol — DB 추상화 (T13 후 SQLAlchemy 구현체 합류).

T26 (`/api/screen`) / T40 (Save Run 버튼) 의 contract. 본 사이클은 Protocol 만
정의 + in-memory Fake 제공 (oracle 자문 결정 6).

PIT-aware Repository (pit_protocols.py) 와 달리 Screen Run 은 자기 자체가 freeze
단위 — 임의 시점 조회 가능. method 시그니처에 `as_of` 없음.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.services.screen_run import ScreenRunSnapshot

__all__ = [
    "FakeScreenRunRepository",
    "ScreenRunAlreadyExistsError",
    "ScreenRunNotFoundError",
    "ScreenRunRepository",
]


class ScreenRunNotFoundError(Exception):
    """`fetch_by_id` 의 not-found case — `None` 반환 vs raise 정책 선택은 호출자."""


class ScreenRunAlreadyExistsError(Exception):
    """`save` 의 append-only 위반 — 같은 id 의 run 이 이미 존재 (ADR-0021 D2).

    M2 부터 screen_runs 는 append-only — 한 번 freeze 된 run 은 재저장 불가.
    owner 일치 여부와 무관하게 거부(IDOR 차단 + 재현 자산 보존). 정상 경로는
    매 Save Run 마다 신규 `uuid4()` 라 발생하지 않음 — 악의적/버그성 재저장만.
    """


@runtime_checkable
class ScreenRunRepository(Protocol):
    """Screen Run 의 저장/조회 contract.

    모든 method 가 `user_id` keyword required — M0 single-user 라도 type-level
    enforcement (T31 합류 후 multi-user 안전).
    """

    def save(self, snapshot: ScreenRunSnapshot) -> None:
        """snapshot 저장 — append-only (ADR-0021 D2). 같은 id 재저장 거부.

        M0 의 overwrite 정책 폐기. 같은 id 의 row 가 이미 있으면 owner 무관
        `ScreenRunAlreadyExistsError` (IDOR 차단 + 재현 자산 보존).
        """
        ...

    def fetch_by_id(
        self, run_id: UUID, *, user_id: UUID,
    ) -> ScreenRunSnapshot | None:
        """`run_id` 의 snapshot. `user_id` 가 owner 가 아니면 None.

        Note: M0 single-user 라도 type-level user_id 검사 강제 — T31 합류 후
        다른 사용자의 Run 누출 차단.
        """
        ...

    def fetch_recent(
        self, *, user_id: UUID, limit: int = 20,
    ) -> Sequence[ScreenRunSnapshot]:
        """`user_id` 의 최근 Run — `computed_at` 내림차순. 최대 `limit` 개."""
        ...


class FakeScreenRunRepository(ScreenRunRepository):
    """In-memory Fake — T26/T40 의 contract test + 단위 테스트.

    SQLAlchemy 구현체 (T13 후) 와 동일 시나리오 통과 강제. M0 의 reference
    implementation.
    """

    def __init__(self) -> None:
        self._by_id: dict[UUID, ScreenRunSnapshot] = {}
        self._by_user: dict[UUID, list[ScreenRunSnapshot]] = defaultdict(list)

    def save(self, snapshot: ScreenRunSnapshot) -> None:
        # M2 정책: append-only (ADR-0021 D2). 같은 id 가 이미 있으면 owner 무관
        # 거부 — SqlScreenRunRepository 와 동일 contract(IDOR 차단 + 재현 자산
        # 보존). 정상 경로(매 Save Run 신규 uuid4)는 충돌 없음.
        if snapshot.id in self._by_id:
            raise ScreenRunAlreadyExistsError(
                f"screen_run {snapshot.id} 는 이미 존재 — append-only "
                f"(ADR-0021 D2)"
            )
        self._by_id[snapshot.id] = snapshot
        self._by_user[snapshot.user_id].append(snapshot)

    def fetch_by_id(
        self, run_id: UUID, *, user_id: UUID,
    ) -> ScreenRunSnapshot | None:
        snap = self._by_id.get(run_id)
        if snap is None or snap.user_id != user_id:
            return None
        return snap

    def fetch_recent(
        self, *, user_id: UUID, limit: int = 20,
    ) -> Sequence[ScreenRunSnapshot]:
        if limit < 0:
            raise ValueError(f"limit must be >= 0, got {limit}")
        bucket = self._by_user.get(user_id, [])
        # computed_at 내림차순 정렬, 최대 limit.
        sorted_runs = sorted(bucket, key=lambda s: s.computed_at, reverse=True)
        return tuple(sorted_runs[:limit])
