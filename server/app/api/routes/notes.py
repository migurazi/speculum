"""Note CRUD endpoint — T76 종목별 사용자 Markdown 메모 (USER_PRIVATE).

종목 lineage 단위의 사용자 개인 메모. user-scoped mutable CRUD.

Endpoints:
    POST   /api/notes                       — create note (201)
    GET    /api/notes?code_lineage_id=      — list current user's notes for code
    PUT    /api/notes/{note_id}             — update note body
    DELETE /api/notes/{note_id}             — delete note (204)

설계 (watchlist 선례 일관):
- 모든 endpoint 가 CurrentUserDep — body 에 user_id X (IDOR 차단). user_id 는
  CurrentUserDep 로만 주입.
- Pydantic v2 strict input + from_domain output.
- 404 — ownership mismatch / 미존재 (구별 X — IDOR 정보 누출 차단).
- NotesDataError → 400 (body 길이/공백, scope 위반). Pydantic Field 위반은 422.
- **scope 는 입력 안 받음** — 서버가 USER_PRIVATE 고정(repository default).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies.auth import CurrentUserDep
from app.api.dependencies.repositories import NotesRepoDep
from app.repositories.notes_repository import NotesDataError
from app.schemas.notes import (
    NoteCreateIn,
    NoteListOut,
    NoteOut,
    NoteUpdateIn,
)

router = APIRouter(prefix="/api/notes", tags=["notes"])


@router.post(
    "",
    response_model=NoteOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_note(
    user: CurrentUserDep,
    repo: NotesRepoDep,
    body: NoteCreateIn,
) -> NoteOut:
    try:
        # scope 미주입 — repository default(user-private) 사용. user_id 는
        # CurrentUserDep 가 결정(body 에서 안 받음 — IDOR 차단).
        note = repo.create(
            user_id=user.user_id,
            code_lineage_id=body.code_lineage_id,
            body=body.body,
        )
    except NotesDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return NoteOut.from_domain(note)


@router.get("", response_model=NoteListOut)
async def list_notes(
    code_lineage_id: UUID,
    user: CurrentUserDep,
    repo: NotesRepoDep,
) -> NoteListOut:
    # user_id + code_lineage_id 둘 다 필터 — repository 가 강제(전 사용자 누출
    # 차단). owner 의 메모만 반환.
    notes = repo.list_for_code(code_lineage_id, user_id=user.user_id)
    items = tuple(NoteOut.from_domain(n) for n in notes)
    return NoteListOut(items=items, total=len(items))


@router.put("/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: UUID,
    user: CurrentUserDep,
    repo: NotesRepoDep,
    body: NoteUpdateIn,
) -> NoteOut:
    try:
        updated = repo.update(note_id, body.body, user_id=user.user_id)
    except NotesDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="메모를 찾을 수 없습니다.")
    return NoteOut.from_domain(updated)


# response_model=None — 204 No Content body 불가 (watchlists.py delete 선례).
@router.delete(
    "/{note_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None,
)
async def delete_note(
    note_id: UUID,
    user: CurrentUserDep,
    repo: NotesRepoDep,
) -> None:
    ok = repo.delete(note_id, user_id=user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="메모를 찾을 수 없습니다.")
