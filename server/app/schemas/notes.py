"""Note wire schemas — T76 종목별 사용자 Markdown 메모 (USER_PRIVATE).

설계 (watchlist 선례 일관):
- Pydantic v2 strict=False(JSON string → UUID coercion) + extra=forbid + frozen.
- `from_domain` 단방향 factory.
- body 길이 제한 (≤ 10000, T76) — repository invariant 와 동일 cap.
- **scope 는 입력 안 받음** — 서버가 USER_PRIVATE 고정(USER_SHARED 미구현 예약).
  IDOR — user_id 도 body 에 미노출(route 의 CurrentUserDep 가 결정). 응답엔
  scope 노출(클라이언트 가시성 표시용).
"""

from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.repositories.notes_repository import Note

__all__ = [
    "NoteCreateIn",
    "NoteListOut",
    "NoteOut",
    "NoteUpdateIn",
]

# strict=False — JSON string → UUID coercion 허용 (watchlist.py 패턴 일관).
# extra=forbid + frozen 유지.
_STRICT_MODEL_CONFIG = ConfigDict(strict=False, extra="forbid", frozen=True)

_MAX_BODY: Final[int] = 10000  # T76 메모 길이 cap


class NoteCreateIn(BaseModel):
    """POST /api/notes body.

    scope 는 미노출 — 서버가 USER_PRIVATE 고정. user_id 도 미노출(CurrentUserDep).
    """

    model_config = _STRICT_MODEL_CONFIG

    code_lineage_id: UUID
    body: str = Field(min_length=1, max_length=_MAX_BODY)


class NoteUpdateIn(BaseModel):
    """PUT /api/notes/{note_id} body — body 갱신만."""

    model_config = _STRICT_MODEL_CONFIG

    body: str = Field(min_length=1, max_length=_MAX_BODY)


class NoteOut(BaseModel):
    """Note wire output — scope 노출(가시성 표시용)."""

    model_config = _STRICT_MODEL_CONFIG

    id: UUID
    code_lineage_id: UUID
    body: str
    scope: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, note: Note) -> NoteOut:
        return cls(
            id=note.id,
            code_lineage_id=note.code_lineage_id,
            body=note.body,
            scope=note.scope,
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


class NoteListOut(BaseModel):
    """GET /api/notes?code_lineage_id= 결과."""

    model_config = _STRICT_MODEL_CONFIG

    items: tuple[NoteOut, ...]
    total: int
