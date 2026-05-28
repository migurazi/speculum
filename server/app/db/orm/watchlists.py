"""WatchlistFolderORM + WatchlistItemORM — ADR-0011 D2.

`app/repositories/watchlist_repository.py` 의 `WatchlistFolder` / `WatchlistItem`
frozen dataclass 의 DB 표현.

핵심 결정 (T13 Phase B):
    - **parent_id self-FK** — folder 가 다른 folder 의 parent. depth ≤ 2 는
      DB 차원 CHECK constraint X (구현 복잡) — Repository / endpoint layer 가
      강제.
    - **user_id FK 없음** — M0 single-user, users 테이블 미존재. user_id 는
      단순 UUID 컬럼 + 인덱스. T31 NextAuth 합류 시 users.id FK 추가 backlog.
    - **is_default** — ADR-0011 D7 의 "내 관심 종목" 빌트인 폴더 marker.
    - **display_order** — 같은 parent 내 정렬 키. Fake 의 max+1 정책 유지.
    - **watchlist_items.code_lineage_id** — stocks_master.id 의 lineage UUID.
      현재 stocks_master 가 미bootstrap 이라 FK 없음 (T13 Phase C backlog).
    - **UNIQUE (watchlist_id, code_lineage_id)** — Fake 의 "같은 folder +
      lineage 중복 reject" invariant 강제.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class WatchlistFolderORM(Base):
    """watchlists row — ADR-0011 D2 의 lineage entity (folder)."""

    __tablename__ = "watchlists"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    # parent_id self-FK — None = root, 있으면 다른 폴더의 자식.
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("watchlists.id"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        # user_id 인덱스 — list_folders 의 hot path.
        Index("ix_watchlists_user_id", "user_id"),
        # parent_id 인덱스 — 자식 폴더 lookup.
        Index("ix_watchlists_parent_id", "parent_id"),
    )


class WatchlistItemORM(Base):
    """watchlist_items row — ADR-0011 D2 의 종목 entry."""

    __tablename__ = "watchlist_items"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    watchlist_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("watchlists.id", ondelete="CASCADE"),
        nullable=False,
    )
    # code_lineage_id — stocks_master.id 의 lineage. FK 없음 (T13 Phase C).
    code_lineage_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False,
    )
    note: Mapped[str | None] = mapped_column(String(280), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        # 같은 folder + lineage 중복 차단 — Fake 의 add_item dedup 의 DB
        # 차원 강제.
        UniqueConstraint(
            "watchlist_id", "code_lineage_id",
            name="uq_watchlist_items_folder_lineage",
        ),
        Index("ix_watchlist_items_watchlist_id", "watchlist_id"),
    )
