"""Watchlist Repository — ADR-0011 D2 의 watchlists + watchlist_items entity.

폴더링 (parent_id, depth ≤ 2) + 종목별 메모 (≤ 280 자) + display_order. M0
Scope:

- WatchlistFolder: id, user_id, parent_id (Optional), name, display_order, is_default.
- WatchlistItem: id, watchlist_id, code_lineage_id, note (≤ 280 자), display_order, added_at.
- depth ≤ 2 (root + 1 단계).

CRUD pattern — T26 의 ScreenRunRepository 패턴 일관 — user_id keyword-only.
T13 SQLAlchemy 합류 후 동일 Protocol 의 SQL 구현체.

설계 (oracle T26 자문 일관):
- 모든 method 가 `user_id: UUID` keyword-only — IDOR 차단.
- create/update/delete 의 ownership 검증 (다른 user 의 데이터 접근 X).
- 폴더 삭제 시 cascade item 삭제 (foreign key 의미).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

__all__ = [
    "FakeScreenerSetRepository",
    "FakeWatchlistRepository",
    "ScreenerSet",
    "ScreenerSetRepository",
    "WatchlistDataError",
    "WatchlistFolder",
    "WatchlistItem",
    "WatchlistRepository",
]


class WatchlistDataError(Exception):
    """Watchlist invariant 위반 — 폴더 depth 초과, 메모 길이, ownership 등."""


# =============================================================================
# Domain entities — frozen dataclass (codebase 일관)
# =============================================================================

@dataclass(frozen=True, slots=True)
class WatchlistFolder:
    """ADR-0011 D2 의 watchlists row 의 in-memory representation.

    Attributes:
        id: 폴더 UUID.
        user_id: 소유자.
        parent_id: 부모 폴더 (None = root). depth ≤ 2 → parent 가 root only.
        name: 폴더 이름 (≤ 100 자).
        display_order: UI 표시 순서.
        is_default: "내 관심 종목" 빌트인 폴더 (ADR-0011 D7).
        created_at: 생성 시각 (UTC).
        updated_at: 마지막 갱신 시각 (UTC).
    """

    id: UUID
    user_id: UUID
    parent_id: UUID | None
    name: str
    display_order: int
    is_default: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class WatchlistItem:
    """ADR-0011 D2 의 watchlist_items row.

    Attributes:
        id: item UUID.
        watchlist_id: 폴더 ID.
        code_lineage_id: stocks_master.id (lineage). ADR-0009 D6.
        note: 메모 (≤ 280 자, M0).
        display_order: 폴더 내 표시 순서.
        added_at: 추가 시각.
    """

    id: UUID
    watchlist_id: UUID
    code_lineage_id: UUID
    note: str | None
    display_order: int
    added_at: datetime


@dataclass(frozen=True, slots=True)
class ScreenerSet:
    """조건셋 저장 — M0_PLAN T28 + AC-F-07.

    Watchlist 와 분리된 entity (저장된 query — Screener 의 reload). ADR-0011 의
    Screen Run 과 다름 (Run = 실행 결과 freeze, ScreenerSet = 재실행 가능한 query).

    Attributes:
        id: ScreenerSet UUID.
        user_id: 소유자.
        name: 사용자 라벨 (≤ 100 자).
        conditions: 조건 list — `[{"factor": "per:ttm", "op": "<", "value": "10"}, ...]`.
        selected_factors: 결과 표시 factor list.
        created_at / updated_at: UTC.
    """

    id: UUID
    user_id: UUID
    name: str
    conditions: tuple[dict[str, str], ...]
    selected_factors: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


# =============================================================================
# Validation constants
# =============================================================================

_MAX_FOLDER_NAME_LENGTH = 100
_MAX_NOTE_LENGTH = 280  # ADR-0011 D1 — M0
_MAX_DEPTH = 2  # root + 1 (parent 가 root only)


# =============================================================================
# Watchlist Repository Protocol
# =============================================================================

@runtime_checkable
class WatchlistRepository(Protocol):
    """Watchlist CRUD contract — 모든 method 가 `user_id` keyword-only (IDOR)."""

    # ---- Folder operations ----

    def create_folder(
        self,
        *,
        user_id: UUID,
        name: str,
        parent_id: UUID | None = None,
    ) -> WatchlistFolder:
        """폴더 생성. depth ≤ 2 강제 — parent_id 가 또 다른 폴더의 parent 면 거부."""
        ...

    def list_folders(self, *, user_id: UUID) -> Sequence[WatchlistFolder]:
        """user 의 모든 폴더 (display_order asc)."""
        ...

    def get_folder(
        self, folder_id: UUID, *, user_id: UUID,
    ) -> WatchlistFolder | None:
        """단일 folder. 미존재 또는 owner mismatch 시 None."""
        ...

    def update_folder(
        self,
        folder_id: UUID,
        *,
        user_id: UUID,
        name: str | None = None,
        display_order: int | None = None,
    ) -> WatchlistFolder | None:
        """폴더 이름 / 순서 갱신. 미존재 또는 owner mismatch 시 None."""
        ...

    def delete_folder(self, folder_id: UUID, *, user_id: UUID) -> bool:
        """폴더 삭제 + cascade item 삭제. 성공 시 True. is_default 폴더는 거부 (False)."""
        ...

    # ---- Item operations ----

    def add_item(
        self,
        *,
        user_id: UUID,
        folder_id: UUID,
        code_lineage_id: UUID,
        note: str | None = None,
    ) -> WatchlistItem | None:
        """폴더에 종목 추가. note ≤ 280.

        반환 contract (oracle T28 #C1):
            - 정상 추가 → WatchlistItem
            - 중복 (같은 folder + lineage) → None (endpoint 가 409 매핑)

        Raises:
            WatchlistDataError: folder 미존재 또는 owner mismatch — endpoint 가
                404 매핑. None 반환 (중복) 과 분리하여 ambiguity 차단.
        """
        ...

    def list_items(
        self, folder_id: UUID, *, user_id: UUID,
    ) -> Sequence[WatchlistItem]:
        """폴더의 모든 item (display_order asc). owner mismatch 시 빈 list."""
        ...

    def remove_item(self, item_id: UUID, *, user_id: UUID) -> bool:
        """item 삭제. 성공 True. 다른 user 의 item 이면 False."""
        ...

    def update_item_note(
        self,
        item_id: UUID,
        *,
        user_id: UUID,
        note: str | None,
    ) -> WatchlistItem | None:
        """item 메모 갱신. owner mismatch 또는 미존재 시 None."""
        ...


# =============================================================================
# ScreenerSet Repository Protocol
# =============================================================================

@runtime_checkable
class ScreenerSetRepository(Protocol):
    """조건셋 저장 / 불러오기 contract — AC-F-07."""

    def save(
        self,
        *,
        user_id: UUID,
        name: str,
        conditions: Sequence[dict[str, str]],
        selected_factors: Sequence[str],
    ) -> ScreenerSet:
        """새 조건셋 저장. 동일 이름 허용 (ScreenerSet 은 UUID 식별)."""
        ...

    def list_all(self, *, user_id: UUID) -> Sequence[ScreenerSet]:
        """user 의 모든 조건셋 (updated_at desc)."""
        ...

    def get(self, set_id: UUID, *, user_id: UUID) -> ScreenerSet | None:
        """단일 조건셋. owner mismatch 시 None."""
        ...

    def delete(self, set_id: UUID, *, user_id: UUID) -> bool:
        """조건셋 삭제. 성공 True."""
        ...


# =============================================================================
# Fakes — T13 SQLAlchemy 합류 후 reference behavior
# =============================================================================

class FakeWatchlistRepository(WatchlistRepository):
    """In-memory watchlist store — contract reference."""

    def __init__(self) -> None:
        self._folders: dict[UUID, WatchlistFolder] = {}
        self._items: dict[UUID, WatchlistItem] = {}
        self._items_by_folder: dict[UUID, list[UUID]] = defaultdict(list)

    # ---- helpers ----

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _validate_folder_name(self, name: str) -> None:
        if not name or not name.strip():
            raise WatchlistDataError("folder name must be non-empty")
        if len(name) > _MAX_FOLDER_NAME_LENGTH:
            raise WatchlistDataError(
                f"folder name exceeds {_MAX_FOLDER_NAME_LENGTH} chars"
            )

    def _validate_note(self, note: str | None) -> None:
        if note is not None and len(note) > _MAX_NOTE_LENGTH:
            raise WatchlistDataError(
                f"note exceeds {_MAX_NOTE_LENGTH} chars (ADR-0011 D1 M0 limit)"
            )

    def _validate_parent(self, parent_id: UUID | None, user_id: UUID) -> None:
        """parent_id 가 valid 한 root 폴더인지 — depth ≤ 2 (ADR-0011 D1)."""
        if parent_id is None:
            return  # root
        parent = self._folders.get(parent_id)
        if parent is None or parent.user_id != user_id:
            raise WatchlistDataError(
                f"parent folder {parent_id} not found or not owned"
            )
        if parent.parent_id is not None:
            raise WatchlistDataError(
                f"folder depth would exceed limit {_MAX_DEPTH} "
                f"(ADR-0011 D1 — parent must be root)"
            )

    # ---- Folder methods ----

    def create_folder(
        self, *, user_id: UUID, name: str, parent_id: UUID | None = None,
    ) -> WatchlistFolder:
        from uuid import uuid4
        self._validate_folder_name(name)
        self._validate_parent(parent_id, user_id)
        now = self._now()
        # display_order = 같은 parent 내의 max + 1
        siblings = [f for f in self._folders.values()
                    if f.user_id == user_id and f.parent_id == parent_id]
        max_order = max((f.display_order for f in siblings), default=-1)
        folder = WatchlistFolder(
            id=uuid4(),
            user_id=user_id,
            parent_id=parent_id,
            name=name.strip(),
            display_order=max_order + 1,
            is_default=False,
            created_at=now,
            updated_at=now,
        )
        self._folders[folder.id] = folder
        return folder

    def list_folders(self, *, user_id: UUID) -> Sequence[WatchlistFolder]:
        owned = [f for f in self._folders.values() if f.user_id == user_id]
        return tuple(sorted(
            owned,
            key=lambda f: (f.parent_id or UUID(int=0), f.display_order),
        ))

    def get_folder(
        self, folder_id: UUID, *, user_id: UUID,
    ) -> WatchlistFolder | None:
        f = self._folders.get(folder_id)
        if f is None or f.user_id != user_id:
            return None
        return f

    def update_folder(
        self,
        folder_id: UUID,
        *,
        user_id: UUID,
        name: str | None = None,
        display_order: int | None = None,
    ) -> WatchlistFolder | None:
        f = self.get_folder(folder_id, user_id=user_id)
        if f is None:
            return None
        new_name = f.name if name is None else name.strip()
        if name is not None:
            self._validate_folder_name(name)
        new_order = f.display_order if display_order is None else display_order
        updated = WatchlistFolder(
            id=f.id, user_id=f.user_id, parent_id=f.parent_id,
            name=new_name, display_order=new_order, is_default=f.is_default,
            created_at=f.created_at, updated_at=self._now(),
        )
        self._folders[folder_id] = updated
        return updated

    def delete_folder(self, folder_id: UUID, *, user_id: UUID) -> bool:
        f = self.get_folder(folder_id, user_id=user_id)
        if f is None:
            return False
        if f.is_default:
            return False  # is_default 폴더 삭제 거부.
        # cascade — items + 자식 폴더 삭제.
        for item_id in list(self._items_by_folder.get(folder_id, [])):
            self._items.pop(item_id, None)
        self._items_by_folder.pop(folder_id, None)
        # 자식 폴더 삭제 (depth 2 라 1 단계만).
        children = [c.id for c in self._folders.values()
                    if c.parent_id == folder_id and c.user_id == user_id]
        for child_id in children:
            self.delete_folder(child_id, user_id=user_id)
        self._folders.pop(folder_id, None)
        return True

    # ---- Item methods ----

    def add_item(
        self,
        *,
        user_id: UUID,
        folder_id: UUID,
        code_lineage_id: UUID,
        note: str | None = None,
    ) -> WatchlistItem | None:
        f = self.get_folder(folder_id, user_id=user_id)
        if f is None:
            # oracle T28 #C1 — folder 미존재는 404 의미 (None 으로 endpoint 에 전달
            # 시 endpoint 가 분기 필요). raise 로 endpoint 가 명확히 404 매핑.
            raise WatchlistDataError(
                f"folder {folder_id} not found or not owned"
            )
        self._validate_note(note)
        # 중복 검사 — 같은 folder + lineage.
        for existing_id in self._items_by_folder.get(folder_id, []):
            existing = self._items.get(existing_id)
            if existing and existing.code_lineage_id == code_lineage_id:
                return None  # 409 — 중복
        from uuid import uuid4
        existing_ids = self._items_by_folder.get(folder_id, [])
        max_order = max(
            (self._items[i].display_order for i in existing_ids if i in self._items),
            default=-1,
        )
        item = WatchlistItem(
            id=uuid4(),
            watchlist_id=folder_id,
            code_lineage_id=code_lineage_id,
            note=note,
            display_order=max_order + 1,
            added_at=self._now(),
        )
        self._items[item.id] = item
        self._items_by_folder[folder_id].append(item.id)
        return item

    def list_items(
        self, folder_id: UUID, *, user_id: UUID,
    ) -> Sequence[WatchlistItem]:
        f = self.get_folder(folder_id, user_id=user_id)
        if f is None:
            return ()
        ids = self._items_by_folder.get(folder_id, [])
        items = [self._items[i] for i in ids if i in self._items]
        return tuple(sorted(items, key=lambda x: x.display_order))

    def remove_item(self, item_id: UUID, *, user_id: UUID) -> bool:
        item = self._items.get(item_id)
        if item is None:
            return False
        # ownership 검사 — item 의 folder owner.
        folder = self._folders.get(item.watchlist_id)
        if folder is None or folder.user_id != user_id:
            return False
        self._items.pop(item_id, None)
        bucket = self._items_by_folder.get(item.watchlist_id, [])
        if item_id in bucket:
            bucket.remove(item_id)
        return True

    def update_item_note(
        self, item_id: UUID, *, user_id: UUID, note: str | None,
    ) -> WatchlistItem | None:
        item = self._items.get(item_id)
        if item is None:
            return None
        folder = self._folders.get(item.watchlist_id)
        if folder is None or folder.user_id != user_id:
            return None
        self._validate_note(note)
        updated = WatchlistItem(
            id=item.id, watchlist_id=item.watchlist_id,
            code_lineage_id=item.code_lineage_id,
            note=note, display_order=item.display_order,
            added_at=item.added_at,
        )
        self._items[item_id] = updated
        return updated


class FakeScreenerSetRepository(ScreenerSetRepository):
    """In-memory ScreenerSet store."""

    def __init__(self) -> None:
        self._sets: dict[UUID, ScreenerSet] = {}

    def save(
        self,
        *,
        user_id: UUID,
        name: str,
        conditions: Sequence[dict[str, str]],
        selected_factors: Sequence[str],
    ) -> ScreenerSet:
        from uuid import uuid4
        if not name or not name.strip():
            raise WatchlistDataError("ScreenerSet name must be non-empty")
        if len(name) > _MAX_FOLDER_NAME_LENGTH:
            raise WatchlistDataError(
                f"name exceeds {_MAX_FOLDER_NAME_LENGTH} chars"
            )
        now = datetime.now(UTC)
        s = ScreenerSet(
            id=uuid4(),
            user_id=user_id,
            name=name.strip(),
            conditions=tuple(dict(c) for c in conditions),
            selected_factors=tuple(selected_factors),
            created_at=now,
            updated_at=now,
        )
        self._sets[s.id] = s
        return s

    def list_all(self, *, user_id: UUID) -> Sequence[ScreenerSet]:
        owned = [s for s in self._sets.values() if s.user_id == user_id]
        return tuple(sorted(owned, key=lambda x: x.updated_at, reverse=True))

    def get(self, set_id: UUID, *, user_id: UUID) -> ScreenerSet | None:
        s = self._sets.get(set_id)
        if s is None or s.user_id != user_id:
            return None
        return s

    def delete(self, set_id: UUID, *, user_id: UUID) -> bool:
        s = self.get(set_id, user_id=user_id)
        if s is None:
            return False
        self._sets.pop(set_id, None)
        return True
