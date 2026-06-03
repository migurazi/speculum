"""Watchlist CRUD endpoint — POST/GET/PUT/DELETE.

ADR-0011 (Watchlist scope) implementation. AC-F-06 backbone.

Endpoints:
    POST   /api/watchlists                       — create folder
    GET    /api/watchlists                       — list current user folders
    PUT    /api/watchlists/{folder_id}           — update folder (name/order)
    DELETE /api/watchlists/{folder_id}           — delete folder + cascade items
    POST   /api/watchlists/{folder_id}/items     — add stock to folder
    GET    /api/watchlists/{folder_id}/items     — list items in folder
    PATCH  /api/watchlists/items/{item_id}       — update item note
    DELETE /api/watchlists/items/{item_id}       — remove item

설계 (T26 패턴 일관):
- 모든 endpoint 가 CurrentUserDep — body 에 user_id X (IDOR 차단)
- Pydantic v2 strict input + from_domain output
- 404 — ownership mismatch / 미존재 (구별 X — IDOR 정보 누출 차단)
- WatchlistDataError → 400
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies.auth import CurrentUserDep
from app.api.dependencies.repositories import WatchlistRepoDep
from app.repositories.watchlist_repository import WatchlistDataError
from app.schemas.watchlist import (
    WatchlistFolderCreateIn,
    WatchlistFolderListOut,
    WatchlistFolderOut,
    WatchlistFolderUpdateIn,
    WatchlistItemAddIn,
    WatchlistItemListOut,
    WatchlistItemOut,
    WatchlistItemUpdateIn,
)

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])


# =============================================================================
# Folder CRUD
# =============================================================================

@router.post(
    "",
    response_model=WatchlistFolderOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    user: CurrentUserDep,
    repo: WatchlistRepoDep,
    body: WatchlistFolderCreateIn,
) -> WatchlistFolderOut:
    try:
        folder = repo.create_folder(
            user_id=user.user_id,
            name=body.name,
            parent_id=body.parent_id,
        )
    except WatchlistDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WatchlistFolderOut.from_domain(folder)


@router.get("", response_model=WatchlistFolderListOut)
async def list_folders(
    user: CurrentUserDep,
    repo: WatchlistRepoDep,
) -> WatchlistFolderListOut:
    folders = repo.list_folders(user_id=user.user_id)
    items = tuple(WatchlistFolderOut.from_domain(f) for f in folders)
    return WatchlistFolderListOut(items=items, total=len(items))


# Item routes 를 `/items/` 보다 먼저 정의 — `/items/{item_id}` 가 `/{folder_id}`
# 와 path conflict 회피.

@router.patch("/items/{item_id}", response_model=WatchlistItemOut)
async def update_item(
    item_id: UUID,
    user: CurrentUserDep,
    repo: WatchlistRepoDep,
    body: WatchlistItemUpdateIn,
) -> WatchlistItemOut:
    try:
        updated = repo.update_item_note(
            item_id, user_id=user.user_id, note=body.note,
        )
    except WatchlistDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="item 을 찾을 수 없습니다.")
    return WatchlistItemOut.from_domain(updated)


# response_model=None — 204 No Content body 불가. 최신 FastAPI 의 `-> None`
# NoneType 추론 → 204 assert 발화 방지 (screener_sets.py delete 참조).
@router.delete(
    "/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None,
)
async def delete_item(
    item_id: UUID,
    user: CurrentUserDep,
    repo: WatchlistRepoDep,
) -> None:
    ok = repo.remove_item(item_id, user_id=user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="item 을 찾을 수 없습니다.")


@router.put("/{folder_id}", response_model=WatchlistFolderOut)
async def update_folder(
    folder_id: UUID,
    user: CurrentUserDep,
    repo: WatchlistRepoDep,
    body: WatchlistFolderUpdateIn,
) -> WatchlistFolderOut:
    try:
        updated = repo.update_folder(
            folder_id,
            user_id=user.user_id,
            name=body.name,
            display_order=body.display_order,
        )
    except WatchlistDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="폴더를 찾을 수 없습니다.")
    return WatchlistFolderOut.from_domain(updated)


# response_model=None — 204 No Content body 불가 (위 items delete 와 동일 사유).
@router.delete(
    "/{folder_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None,
)
async def delete_folder(
    folder_id: UUID,
    user: CurrentUserDep,
    repo: WatchlistRepoDep,
) -> None:
    ok = repo.delete_folder(folder_id, user_id=user.user_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="폴더를 찾을 수 없거나 default 폴더입니다.",
        )


@router.post(
    "/{folder_id}/items",
    response_model=WatchlistItemOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_item(
    folder_id: UUID,
    user: CurrentUserDep,
    repo: WatchlistRepoDep,
    body: WatchlistItemAddIn,
) -> WatchlistItemOut:
    try:
        item = repo.add_item(
            user_id=user.user_id,
            folder_id=folder_id,
            code_lineage_id=body.code_lineage_id,
            note=body.note,
        )
    except WatchlistDataError as exc:
        # folder 미존재 / ownership mismatch → 404. note 길이 등 invariant 위반은
        # 별도 메시지로 처리하나, 본 endpoint 의 user-facing context 상 둘 다 4xx.
        # 메시지로 구별 가능 (oracle T28 C1 — None ambiguity 차단).
        msg = str(exc)
        if "not found" in msg or "not owned" in msg:
            raise HTTPException(status_code=404, detail="폴더를 찾을 수 없습니다.") from exc
        raise HTTPException(status_code=400, detail=msg) from exc
    if item is None:
        # 중복 (같은 folder + lineage) — 409.
        raise HTTPException(
            status_code=409,
            detail="같은 종목이 이미 폴더에 추가되어 있습니다.",
        )
    return WatchlistItemOut.from_domain(item)


@router.get("/{folder_id}/items", response_model=WatchlistItemListOut)
async def list_items(
    folder_id: UUID,
    user: CurrentUserDep,
    repo: WatchlistRepoDep,
) -> WatchlistItemListOut:
    items = repo.list_items(folder_id, user_id=user.user_id)
    out = tuple(WatchlistItemOut.from_domain(i) for i in items)
    return WatchlistItemListOut(items=out, total=len(out))
