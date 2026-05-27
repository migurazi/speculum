"""Watchlist + ScreenerSet wire schemas — POST/GET/PUT/DELETE endpoint.

설계 (T26 패턴 일관):
- Pydantic v2 strict + extra=forbid + frozen=True
- `from_domain` 단방향 factory
- name / note 길이 제한 (ADR-0011 D1)
- IDOR — user_id 는 endpoint dependency 가 결정, body 에 미노출
"""

from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.repositories.watchlist_repository import (
    ScreenerSet,
    WatchlistFolder,
    WatchlistItem,
)

__all__ = [
    "ScreenerSetCreateIn",
    "ScreenerSetListOut",
    "ScreenerSetOut",
    "WatchlistFolderCreateIn",
    "WatchlistFolderListOut",
    "WatchlistFolderOut",
    "WatchlistFolderUpdateIn",
    "WatchlistItemAddIn",
    "WatchlistItemListOut",
    "WatchlistItemOut",
    "WatchlistItemUpdateIn",
]

# strict=False — JSON string → UUID coercion 허용. extra=forbid + frozen 유지.
# (POST body 의 UUID 는 자연스러운 string. ConditionIn 의 OpEnum 패턴 일관.)
_STRICT_MODEL_CONFIG = ConfigDict(strict=False, extra="forbid", frozen=True)

_MAX_FOLDER_NAME: Final[int] = 100
_MAX_NOTE: Final[int] = 280  # ADR-0011 D1


# =============================================================================
# Watchlist Folder
# =============================================================================

class WatchlistFolderCreateIn(BaseModel):
    """POST /api/watchlists body."""

    model_config = _STRICT_MODEL_CONFIG

    name: str = Field(min_length=1, max_length=_MAX_FOLDER_NAME)
    parent_id: UUID | None = None


class WatchlistFolderUpdateIn(BaseModel):
    """PUT /api/watchlists/{id} body."""

    model_config = _STRICT_MODEL_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=_MAX_FOLDER_NAME)
    display_order: int | None = Field(default=None, ge=0, le=10000)


class WatchlistFolderOut(BaseModel):
    """폴더 wire output."""

    model_config = _STRICT_MODEL_CONFIG

    id: UUID
    parent_id: UUID | None
    name: str
    display_order: int
    is_default: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, folder: WatchlistFolder) -> "WatchlistFolderOut":
        return cls(
            id=folder.id,
            parent_id=folder.parent_id,
            name=folder.name,
            display_order=folder.display_order,
            is_default=folder.is_default,
            created_at=folder.created_at,
            updated_at=folder.updated_at,
        )


class WatchlistFolderListOut(BaseModel):
    """GET /api/watchlists 결과."""

    model_config = _STRICT_MODEL_CONFIG

    items: tuple[WatchlistFolderOut, ...]
    total: int


# =============================================================================
# Watchlist Item
# =============================================================================

class WatchlistItemAddIn(BaseModel):
    """POST /api/watchlists/{folder_id}/items body."""

    model_config = _STRICT_MODEL_CONFIG

    code_lineage_id: UUID
    note: str | None = Field(default=None, max_length=_MAX_NOTE)


class WatchlistItemUpdateIn(BaseModel):
    """PATCH /api/watchlists/items/{item_id} body — 메모 갱신.

    note=None 이면 메모 제거. note 가 명시되지 않은 PATCH 는 422 (extra=forbid).
    """

    model_config = _STRICT_MODEL_CONFIG

    note: str | None = Field(default=None, max_length=_MAX_NOTE)


class WatchlistItemOut(BaseModel):
    """item wire output."""

    model_config = _STRICT_MODEL_CONFIG

    id: UUID
    watchlist_id: UUID
    code_lineage_id: UUID
    note: str | None
    display_order: int
    added_at: datetime

    @classmethod
    def from_domain(cls, item: WatchlistItem) -> "WatchlistItemOut":
        return cls(
            id=item.id,
            watchlist_id=item.watchlist_id,
            code_lineage_id=item.code_lineage_id,
            note=item.note,
            display_order=item.display_order,
            added_at=item.added_at,
        )


class WatchlistItemListOut(BaseModel):
    """폴더의 items 결과."""

    model_config = _STRICT_MODEL_CONFIG

    items: tuple[WatchlistItemOut, ...]
    total: int


# =============================================================================
# ScreenerSet
# =============================================================================

class ScreenerSetCreateIn(BaseModel):
    """POST /api/screener-sets body."""

    model_config = _STRICT_MODEL_CONFIG

    name: str = Field(min_length=1, max_length=_MAX_FOLDER_NAME)
    conditions: list[dict[str, str]] = Field(min_length=1, max_length=32)
    selected_factors: list[str] = Field(min_length=1, max_length=32)


class ScreenerSetOut(BaseModel):
    """조건셋 wire output."""

    model_config = _STRICT_MODEL_CONFIG

    id: UUID
    name: str
    conditions: tuple[dict[str, str], ...]
    selected_factors: tuple[str, ...]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, s: ScreenerSet) -> "ScreenerSetOut":
        return cls(
            id=s.id,
            name=s.name,
            conditions=s.conditions,
            selected_factors=s.selected_factors,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )


class ScreenerSetListOut(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    items: tuple[ScreenerSetOut, ...]
    total: int
