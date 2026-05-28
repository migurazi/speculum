"""SQL Repository — ADR-0011 + ADR-0008 D7 사용자 데이터.

`watchlist_repository.py` + `screen_run_repository.py` 의 Fake 구현체와 동일
contract 를 SQLAlchemy 2 sync session 위에서 구현. T13 Phase B 산출물 +
T13 Phase C 의 ScreenRunSnapshot 추가.

설계 결정:

1. **Fake 와 동일 invariant 강제** — 폴더 이름 길이, 노트 길이, depth ≤ 2,
   ownership, is_default 삭제 거부. helper 들은 Fake 와 동일 logic copy
   (운영 cycle 분리 위해 sharing 보류 — backlog).

2. **session 의존** — `__init__(session: Session)`. 한 request = 한 session
   pattern.

3. **명시적 flush** — Fake 와 contract 동등성을 위해 save / update 직후 flush.
   commit 은 `get_db_session_or_none` (T13 wiring) 가 endpoint 종료 시 수행.

4. **ScreenerSetORM.conditions / selected_factors** — JSON list 직렬화. tuple
   ↔ list 변환은 converters.

5. **delete_folder cascade** — DB level `ondelete="CASCADE"` (watchlist_items)
   가 item 삭제. 자식 폴더는 ORM 단계에서 명시 삭제 (Fake 와 동일 logic).

6. **SqlScreenRunRepository (T13 Phase C)** — save 시 같은 id overwrite (Fake
   와 동일 M0 정책). nested query JSON 직렬화는 converters.

관련 ADR / 문서:
- ADR-0011 D1 (depth ≤ 2), D2 (folder + item schema), D7 (is_default)
- ADR-0008 D7 (screen_runs schema)
- 8 기둥 §2.10 Reproducibility — Run freeze
- M0_PLAN T28 / T30 / AC-F-08
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.converters import (
    screen_run_orm_to_record,
    screen_run_record_to_orm,
    screener_set_orm_to_record,
    screener_set_record_to_orm,
    watchlist_folder_orm_to_record,
    watchlist_folder_record_to_orm,
    watchlist_item_orm_to_record,
    watchlist_item_record_to_orm,
)
from app.db.orm.screen_runs import ScreenRunSnapshotORM
from app.db.orm.screener_sets import ScreenerSetORM
from app.db.orm.watchlists import WatchlistFolderORM, WatchlistItemORM
from app.repositories.screen_run_repository import ScreenRunRepository
from app.repositories.watchlist_repository import (
    ScreenerSet,
    ScreenerSetRepository,
    WatchlistDataError,
    WatchlistFolder,
    WatchlistItem,
    WatchlistRepository,
)
from app.services.screen_run import ScreenRunSnapshot

__all__ = [
    "SqlScreenRunRepository",
    "SqlScreenerSetRepository",
    "SqlWatchlistRepository",
]


# Fake 와 동일 invariant 상수 (ADR-0011 D1 + M0 정책).
_MAX_FOLDER_NAME_LENGTH = 100
_MAX_NOTE_LENGTH = 280
_MAX_DEPTH = 2  # root + 1 (parent must be root)

# oracle 리뷰 M2 — list_folders 정렬에서 root (parent_id=None) 를 child 앞에
# 두기 위한 sentinel. UUID(int=0) 의 직접 사용 시 의도가 불명확 — 명시 상수
# 로 의미 보강. Fake (`watchlist_repository.py`) 와 동일 정렬 의도이나 sharing
# 은 별도 cycle backlog.
_ROOT_PARENT_SORT_KEY = UUID(int=0)


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_folder_name(name: str) -> None:
    if not name or not name.strip():
        raise WatchlistDataError("folder name must be non-empty")
    if len(name) > _MAX_FOLDER_NAME_LENGTH:
        raise WatchlistDataError(
            f"folder name exceeds {_MAX_FOLDER_NAME_LENGTH} chars"
        )


def _validate_note(note: str | None) -> None:
    if note is not None and len(note) > _MAX_NOTE_LENGTH:
        raise WatchlistDataError(
            f"note exceeds {_MAX_NOTE_LENGTH} chars (ADR-0011 D1 M0 limit)"
        )


# =============================================================================
# Watchlist (folder + item) — SqlWatchlistRepository
# =============================================================================

class SqlWatchlistRepository(WatchlistRepository):
    """SQLAlchemy 기반 Watchlist repository — ADR-0011 D1/D2."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- Folder operations ----

    def create_folder(
        self,
        *,
        user_id: UUID,
        name: str,
        parent_id: UUID | None = None,
    ) -> WatchlistFolder:
        _validate_folder_name(name)
        self._validate_parent(parent_id, user_id)

        # display_order = 같은 parent 의 max + 1 (Fake 와 동일).
        siblings_stmt = select(WatchlistFolderORM).where(
            WatchlistFolderORM.user_id == user_id,
            WatchlistFolderORM.parent_id == parent_id,
        )
        siblings = self._session.execute(siblings_stmt).scalars().all()
        max_order = max(
            (s.display_order for s in siblings), default=-1,
        )

        now = _now()
        record = WatchlistFolder(
            id=uuid4(),
            user_id=user_id,
            parent_id=parent_id,
            name=name.strip(),
            display_order=max_order + 1,
            is_default=False,
            created_at=now,
            updated_at=now,
        )
        self._session.add(watchlist_folder_record_to_orm(record))
        self._session.flush()
        return record

    def list_folders(self, *, user_id: UUID) -> Sequence[WatchlistFolder]:
        stmt = select(WatchlistFolderORM).where(
            WatchlistFolderORM.user_id == user_id,
        )
        result = self._session.execute(stmt).scalars().all()
        folders = [watchlist_folder_orm_to_record(o) for o in result]
        # root (parent_id=None) 를 child 앞에 두는 정렬. oracle 리뷰 M2 의
        # 명시 sentinel.
        return tuple(sorted(
            folders,
            key=lambda f: (
                f.parent_id or _ROOT_PARENT_SORT_KEY, f.display_order,
            ),
        ))

    def get_folder(
        self, folder_id: UUID, *, user_id: UUID,
    ) -> WatchlistFolder | None:
        orm = self._session.get(WatchlistFolderORM, folder_id)
        if orm is None or orm.user_id != user_id:
            return None
        return watchlist_folder_orm_to_record(orm)

    def update_folder(
        self,
        folder_id: UUID,
        *,
        user_id: UUID,
        name: str | None = None,
        display_order: int | None = None,
    ) -> WatchlistFolder | None:
        orm = self._session.get(WatchlistFolderORM, folder_id)
        if orm is None or orm.user_id != user_id:
            return None
        if name is not None:
            _validate_folder_name(name)
            orm.name = name.strip()
        if display_order is not None:
            orm.display_order = display_order
        orm.updated_at = _now()
        self._session.flush()
        return watchlist_folder_orm_to_record(orm)

    def delete_folder(self, folder_id: UUID, *, user_id: UUID) -> bool:
        orm = self._session.get(WatchlistFolderORM, folder_id)
        if orm is None or orm.user_id != user_id:
            return False
        if orm.is_default:
            return False  # ADR-0011 D7 — is_default 삭제 거부.

        # 자식 폴더 재귀 삭제 — Fake 와 동일 logic. depth ≤ 2 라 1 단계만.
        # parent_id self-FK 는 CASCADE 미지정 — depth 정책상 자식 폴더 의도가
        # 사용자 명시이므로 자동 삭제 위험. Repository layer 가 재귀 강제.
        children_stmt = select(WatchlistFolderORM).where(
            WatchlistFolderORM.parent_id == folder_id,
            WatchlistFolderORM.user_id == user_id,
        )
        children = self._session.execute(children_stmt).scalars().all()
        for child in children:
            self.delete_folder(child.id, user_id=user_id)

        # oracle 리뷰 M1 — watchlist_items 는 DB `ondelete="CASCADE"` 에 위임.
        # 명시 delete 제거 (0-row SAWarning + 의도 모호 차단). SQLite 는
        # `conftest._enable_sqlite_fk` 가 PRAGMA foreign_keys=ON 활성. 운영 PG
        # default ON.
        self._session.delete(orm)
        self._session.flush()
        return True

    # ---- Item operations ----

    def add_item(
        self,
        *,
        user_id: UUID,
        folder_id: UUID,
        code_lineage_id: UUID,
        note: str | None = None,
    ) -> WatchlistItem | None:
        folder_orm = self._session.get(WatchlistFolderORM, folder_id)
        if folder_orm is None or folder_orm.user_id != user_id:
            # oracle T28 #C1 — 폴더 미존재는 raise (endpoint 404 매핑).
            raise WatchlistDataError(
                f"folder {folder_id} not found or not owned"
            )
        _validate_note(note)

        # 중복 검사 — 같은 folder + lineage 의 기존 item.
        dup_stmt = select(WatchlistItemORM).where(
            WatchlistItemORM.watchlist_id == folder_id,
            WatchlistItemORM.code_lineage_id == code_lineage_id,
        )
        existing = self._session.execute(dup_stmt).scalars().first()
        if existing is not None:
            return None  # 409 — Fake 와 동일 contract.

        # display_order — 같은 folder 의 max + 1.
        order_stmt = select(WatchlistItemORM).where(
            WatchlistItemORM.watchlist_id == folder_id,
        )
        siblings = self._session.execute(order_stmt).scalars().all()
        max_order = max(
            (s.display_order for s in siblings), default=-1,
        )

        record = WatchlistItem(
            id=uuid4(),
            watchlist_id=folder_id,
            code_lineage_id=code_lineage_id,
            note=note,
            display_order=max_order + 1,
            added_at=_now(),
        )
        self._session.add(watchlist_item_record_to_orm(record))
        self._session.flush()
        return record

    def list_items(
        self, folder_id: UUID, *, user_id: UUID,
    ) -> Sequence[WatchlistItem]:
        folder_orm = self._session.get(WatchlistFolderORM, folder_id)
        if folder_orm is None or folder_orm.user_id != user_id:
            return ()  # owner mismatch → 빈 list (Fake 와 동일).
        stmt = (
            select(WatchlistItemORM)
            .where(WatchlistItemORM.watchlist_id == folder_id)
            .order_by(WatchlistItemORM.display_order.asc())
        )
        result = self._session.execute(stmt).scalars().all()
        return tuple(watchlist_item_orm_to_record(o) for o in result)

    def remove_item(self, item_id: UUID, *, user_id: UUID) -> bool:
        item_orm = self._session.get(WatchlistItemORM, item_id)
        if item_orm is None:
            return False
        folder_orm = self._session.get(
            WatchlistFolderORM, item_orm.watchlist_id,
        )
        if folder_orm is None or folder_orm.user_id != user_id:
            return False
        self._session.delete(item_orm)
        self._session.flush()
        return True

    def update_item_note(
        self,
        item_id: UUID,
        *,
        user_id: UUID,
        note: str | None,
    ) -> WatchlistItem | None:
        item_orm = self._session.get(WatchlistItemORM, item_id)
        if item_orm is None:
            return None
        folder_orm = self._session.get(
            WatchlistFolderORM, item_orm.watchlist_id,
        )
        if folder_orm is None or folder_orm.user_id != user_id:
            return None
        _validate_note(note)
        item_orm.note = note
        self._session.flush()
        return watchlist_item_orm_to_record(item_orm)

    # ---- helpers ----

    def _validate_parent(
        self, parent_id: UUID | None, user_id: UUID,
    ) -> None:
        """parent_id 가 valid root 폴더인지 (depth ≤ 2)."""
        if parent_id is None:
            return  # root
        parent = self._session.get(WatchlistFolderORM, parent_id)
        if parent is None or parent.user_id != user_id:
            raise WatchlistDataError(
                f"parent folder {parent_id} not found or not owned"
            )
        if parent.parent_id is not None:
            raise WatchlistDataError(
                f"folder depth would exceed limit {_MAX_DEPTH} "
                f"(ADR-0011 D1 — parent must be root)"
            )


# =============================================================================
# ScreenerSet — SqlScreenerSetRepository
# =============================================================================

class SqlScreenerSetRepository(ScreenerSetRepository):
    """SQLAlchemy 기반 ScreenerSet repository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        *,
        user_id: UUID,
        name: str,
        conditions: Sequence[dict[str, str]],
        selected_factors: Sequence[str],
    ) -> ScreenerSet:
        if not name or not name.strip():
            raise WatchlistDataError("ScreenerSet name must be non-empty")
        if len(name) > _MAX_FOLDER_NAME_LENGTH:
            raise WatchlistDataError(
                f"name exceeds {_MAX_FOLDER_NAME_LENGTH} chars"
            )
        now = _now()
        record = ScreenerSet(
            id=uuid4(),
            user_id=user_id,
            name=name.strip(),
            conditions=tuple(dict(c) for c in conditions),
            selected_factors=tuple(selected_factors),
            created_at=now,
            updated_at=now,
        )
        self._session.add(screener_set_record_to_orm(record))
        self._session.flush()
        return record

    def list_all(self, *, user_id: UUID) -> Sequence[ScreenerSet]:
        stmt = (
            select(ScreenerSetORM)
            .where(ScreenerSetORM.user_id == user_id)
            .order_by(ScreenerSetORM.updated_at.desc())
        )
        result = self._session.execute(stmt).scalars().all()
        return tuple(screener_set_orm_to_record(o) for o in result)

    def get(self, set_id: UUID, *, user_id: UUID) -> ScreenerSet | None:
        orm = self._session.get(ScreenerSetORM, set_id)
        if orm is None or orm.user_id != user_id:
            return None
        return screener_set_orm_to_record(orm)

    def delete(self, set_id: UUID, *, user_id: UUID) -> bool:
        orm = self._session.get(ScreenerSetORM, set_id)
        if orm is None or orm.user_id != user_id:
            return False
        self._session.delete(orm)
        self._session.flush()
        return True


# =============================================================================
# ScreenRunSnapshot — SqlScreenRunRepository (T13 Phase C)
# =============================================================================

class SqlScreenRunRepository(ScreenRunRepository):
    """SQLAlchemy 기반 ScreenRun snapshot repository — ADR-0008 D7.

    Fake (`FakeScreenRunRepository`) 와 동일 contract — save / fetch_by_id /
    fetch_recent. M0 정책 = 같은 id overwrite (Fake 와 일관).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, snapshot: ScreenRunSnapshot) -> None:
        """snapshot 저장. id 중복 시 overwrite (Fake 정책).

        SQLAlchemy 의 `Session.merge` 가 INSERT/UPDATE 통합 — 같은 PK row 가
        있으면 UPDATE, 없으면 INSERT. Fake 의 overwrite 의미와 동일.

        oracle 리뷰 M1 — `autoflush=True` 의 session 에서 merge 호출 직전에
        pending INSERT 가 flush 되는 race 차단. `no_autoflush` context 안에서
        merge 후 명시 flush — 단일 INSERT/UPDATE 결정 보장.
        """
        orm = screen_run_record_to_orm(snapshot)
        with self._session.no_autoflush:
            self._session.merge(orm)
        self._session.flush()

    def fetch_by_id(
        self, run_id: UUID, *, user_id: UUID,
    ) -> ScreenRunSnapshot | None:
        """run_id 의 snapshot. owner mismatch 시 None (M0 single-user 라도 강제)."""
        orm = self._session.get(ScreenRunSnapshotORM, run_id)
        if orm is None or orm.user_id != user_id:
            return None
        return screen_run_orm_to_record(orm)

    def fetch_recent(
        self, *, user_id: UUID, limit: int = 20,
    ) -> Sequence[ScreenRunSnapshot]:
        """user_id 의 최근 Run — computed_at 내림차순. 최대 limit."""
        if limit < 0:
            raise ValueError(f"limit must be >= 0, got {limit}")
        if limit == 0:
            return ()
        stmt = (
            select(ScreenRunSnapshotORM)
            .where(ScreenRunSnapshotORM.user_id == user_id)
            .order_by(ScreenRunSnapshotORM.computed_at.desc())
            .limit(limit)
        )
        result = self._session.execute(stmt).scalars().all()
        return tuple(screen_run_orm_to_record(o) for o in result)
