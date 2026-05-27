"""ScreenerSet (조건셋 저장/불러오기) endpoint — AC-F-07.

ADR-0011 의 Watchlist 와 분리 entity. Screen Run (실행 결과 freeze) 과도 분리.

Endpoints:
    POST   /api/screener-sets        — create
    GET    /api/screener-sets        — list (current user, updated_at desc)
    GET    /api/screener-sets/{id}   — fetch by id
    DELETE /api/screener-sets/{id}   — delete
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies.auth import CurrentUserDep
from app.api.dependencies.repositories import ScreenerSetRepoDep
from app.repositories.watchlist_repository import WatchlistDataError
from app.schemas.watchlist import (
    ScreenerSetCreateIn,
    ScreenerSetListOut,
    ScreenerSetOut,
)

router = APIRouter(prefix="/api/screener-sets", tags=["screener-sets"])


@router.post(
    "",
    response_model=ScreenerSetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_set(
    user: CurrentUserDep,
    repo: ScreenerSetRepoDep,
    body: ScreenerSetCreateIn,
) -> ScreenerSetOut:
    try:
        s = repo.save(
            user_id=user.user_id,
            name=body.name,
            conditions=body.conditions,
            selected_factors=body.selected_factors,
        )
    except WatchlistDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScreenerSetOut.from_domain(s)


@router.get("", response_model=ScreenerSetListOut)
async def list_sets(
    user: CurrentUserDep,
    repo: ScreenerSetRepoDep,
) -> ScreenerSetListOut:
    sets = repo.list_all(user_id=user.user_id)
    items = tuple(ScreenerSetOut.from_domain(s) for s in sets)
    return ScreenerSetListOut(items=items, total=len(items))


@router.get("/{set_id}", response_model=ScreenerSetOut)
async def get_set(
    set_id: UUID,
    user: CurrentUserDep,
    repo: ScreenerSetRepoDep,
) -> ScreenerSetOut:
    s = repo.get(set_id, user_id=user.user_id)
    if s is None:
        raise HTTPException(status_code=404, detail="ScreenerSet 을 찾을 수 없습니다.")
    return ScreenerSetOut.from_domain(s)


@router.delete("/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_set(
    set_id: UUID,
    user: CurrentUserDep,
    repo: ScreenerSetRepoDep,
) -> None:
    ok = repo.delete(set_id, user_id=user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="ScreenerSet 을 찾을 수 없습니다.")
