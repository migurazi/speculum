"""Notes Repository — T76 종목별 사용자 Markdown 메모 (USER_PRIVATE).

종목 lineage 단위의 사용자 개인 메모. watchlist_repository 의 user-scoped CRUD
패턴을 복제하되 폴더 개념 없이 종목별 직접 메모이며, append-only 가 아닌 **평범한
mutable CRUD**(screen_runs 와 다름).

설계 (watchlist 선례 일관 — oracle T26/T28 자문):
- 모든 method 가 `user_id: UUID` keyword-only — IDOR 차단. user_id 는 route 의
  CurrentUserDep 만 주입, request body 에서 절대 받지 않음.
- owner-check — 미존재 또는 owner mismatch 시 None(404). mismatch 와 미존재를
  구별하지 않아 user 정보 누출 차단.
- **list_for_code 는 반드시 user_id AND code_lineage_id 둘 다 필터** — user_id
  누락 시 전 사용자 메모 누출(가장 위험). DB 복합 인덱스로도 보강.
- **scope = USER_PRIVATE 전용** — create/update 에서 user-private 외 거부
  (NotesDataError). USER_SHARED 는 미구현(예약). forbidden_words 의
  CheckScope.USER_PRIVATE 는 검사 X(본인만 보는 텍스트) 라 assert_clean 미호출.
- body 길이 cap(10000자) — 초과 NotesDataError.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

__all__ = [
    "FakeNotesRepository",
    "Note",
    "NotesDataError",
    "NotesRepository",
]


class NotesDataError(Exception):
    """Note invariant 위반 — body 길이 초과, scope 위반(user-private 외) 등."""


# =============================================================================
# Domain entity — frozen dataclass (codebase 일관)
# =============================================================================

@dataclass(frozen=True, slots=True)
class Note:
    """stock_notes row 의 in-memory representation.

    Attributes:
        id: Note UUID.
        user_id: 소유자 — IDOR owner-check 기준.
        code_lineage_id: stocks_master.id lineage. 메모가 부착된 종목.
        body: Markdown 원문 (≤ 10000 자).
        scope: 가시성 scope — 'user-private' 고정(USER_SHARED 미구현).
        created_at: 생성 시각 (UTC).
        updated_at: 마지막 갱신 시각 (UTC). mutable — UPDATE 시 갱신.
    """

    id: UUID
    user_id: UUID
    code_lineage_id: UUID
    body: str
    scope: str
    created_at: datetime
    updated_at: datetime


# =============================================================================
# Validation constants
# =============================================================================

_MAX_BODY_LENGTH = 10000  # Markdown 메모 길이 cap (T76)
# USER_PRIVATE 전용 — forbidden_words.CheckScope.USER_PRIVATE 와 동일 문자열.
# USER_SHARED("user-shared") 는 미구현(예약) — create/update 에서 거부.
_PRIVATE_SCOPE = "user-private"


def _validate_body(body: str) -> None:
    """body 비어있지 않음 + 길이 cap 검증."""
    if not body or not body.strip():
        raise NotesDataError("note body must be non-empty")
    if len(body) > _MAX_BODY_LENGTH:
        raise NotesDataError(
            f"note body exceeds {_MAX_BODY_LENGTH} chars (T76 limit)"
        )


def _reject_non_private_scope(scope: str) -> None:
    """scope 가 user-private 외이면 거부 — USER_SHARED 봉쇄(미구현, 예약)."""
    if scope != _PRIVATE_SCOPE:
        raise NotesDataError(
            f"scope '{scope}' 는 미지원 — T76 은 '{_PRIVATE_SCOPE}' 전용 "
            f"(USER_SHARED 는 예약 미구현)"
        )


# =============================================================================
# Notes Repository Protocol
# =============================================================================

@runtime_checkable
class NotesRepository(Protocol):
    """Note CRUD contract — 모든 method 가 `user_id` keyword-only (IDOR).

    mutable CRUD — append-only 아님(screen_runs 와 다름). 평범한 UPDATE/DELETE.
    """

    def create(
        self,
        *,
        user_id: UUID,
        code_lineage_id: UUID,
        body: str,
        scope: str = _PRIVATE_SCOPE,
    ) -> Note:
        """메모 생성. body ≤ 10000. scope != user-private 이면 NotesDataError.

        Raises:
            NotesDataError: body 길이/공백 위반 또는 scope 위반(user-private 외).
        """
        ...

    def list_for_code(
        self, code_lineage_id: UUID, *, user_id: UUID,
    ) -> Sequence[Note]:
        """본인의 해당 종목 메모 (created_at asc).

        **반드시 user_id AND code_lineage_id 둘 다 필터** — user_id 누락 시 전
        사용자 메모 누출. owner 의 메모가 없으면 빈 list.
        """
        ...

    def get(self, note_id: UUID, *, user_id: UUID) -> Note | None:
        """단일 메모. 미존재 또는 owner mismatch 시 None."""
        ...

    def update(
        self, note_id: UUID, body: str, *, user_id: UUID,
    ) -> Note | None:
        """메모 body 갱신 + updated_at 갱신. 미존재/owner mismatch 시 None.

        Raises:
            NotesDataError: body 길이/공백 위반.
        """
        ...

    def delete(self, note_id: UUID, *, user_id: UUID) -> bool:
        """메모 삭제. 성공 True. 다른 user 의 메모이면 False."""
        ...


# =============================================================================
# Fake — contract reference (in-memory)
# =============================================================================

class FakeNotesRepository(NotesRepository):
    """In-memory Note store — contract reference."""

    def __init__(self) -> None:
        self._notes: dict[UUID, Note] = {}

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def create(
        self,
        *,
        user_id: UUID,
        code_lineage_id: UUID,
        body: str,
        scope: str = _PRIVATE_SCOPE,
    ) -> Note:
        # scope 검증 우선 — USER_SHARED 봉쇄(미구현, 예약).
        _reject_non_private_scope(scope)
        _validate_body(body)
        now = self._now()
        note = Note(
            id=uuid4(),
            user_id=user_id,
            code_lineage_id=code_lineage_id,
            body=body,
            scope=scope,
            created_at=now,
            updated_at=now,
        )
        self._notes[note.id] = note
        return note

    def list_for_code(
        self, code_lineage_id: UUID, *, user_id: UUID,
    ) -> Sequence[Note]:
        # user_id AND code_lineage_id 둘 다 필터 — user_id 누락 시 전 사용자
        # 누출(가장 위험). created_at asc 정렬.
        owned = [
            n
            for n in self._notes.values()
            if n.user_id == user_id and n.code_lineage_id == code_lineage_id
        ]
        return tuple(sorted(owned, key=lambda n: (n.created_at, n.id)))

    def get(self, note_id: UUID, *, user_id: UUID) -> Note | None:
        n = self._notes.get(note_id)
        if n is None or n.user_id != user_id:
            return None
        return n

    def update(
        self, note_id: UUID, body: str, *, user_id: UUID,
    ) -> Note | None:
        n = self.get(note_id, user_id=user_id)
        if n is None:
            return None
        _validate_body(body)
        updated = Note(
            id=n.id,
            user_id=n.user_id,
            code_lineage_id=n.code_lineage_id,
            body=body,
            scope=n.scope,
            created_at=n.created_at,
            updated_at=self._now(),
        )
        self._notes[note_id] = updated
        return updated

    def delete(self, note_id: UUID, *, user_id: UUID) -> bool:
        n = self.get(note_id, user_id=user_id)
        if n is None:
            return False
        self._notes.pop(note_id, None)
        return True
