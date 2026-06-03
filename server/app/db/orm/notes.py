"""StockNoteORM — T76 종목별 사용자 Markdown 메모 (USER_PRIVATE).

`app/repositories/notes_repository.py` 의 `Note` frozen dataclass 의 DB 표현.
`watchlists.py` 의 WatchlistItemORM 구조를 복제하되 폴더 FK(watchlist_id)를
제거하고 code_lineage_id + body + scope 를 직접 보유한다.

핵심 결정 (T76):
    - **user_id FK → users.id (ADR-0021 D2, migration 0012)** — user 격리의
      FK anchor. naming `fk_stock_notes_user_id_users` (base.py 규약).
    - **code_lineage_id** — stocks_master.id lineage. FK 없음 (watchlist_items
      선례 — stocks_master 미bootstrap 호환).
    - **body TEXT** — Markdown 원문. 길이 cap 은 repository layer 강제(DB CHECK
      미사용).
    - **scope** — String default 'user-private'. USER_SHARED 미구현(예약).
    - **mutable** — append-only 아님(screen_runs 와 다름). UPDATE 시 updated_at
      갱신.

인덱스:
    - ix_stock_notes_user_id: owner-check / list hot path.
    - ix_stock_notes_user_code(user_id, code_lineage_id): list_for_code 의
      복합 필터 — user_id predicate 누락 회귀 방지의 DB 차원 보강.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class StockNoteORM(Base):
    """stock_notes row — T76 종목별 사용자 개인 메모(USER_PRIVATE)."""

    __tablename__ = "stock_notes"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    # user_id FK → users.id (ADR-0021 D2, migration 0012). user 격리 FK anchor.
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_stock_notes_user_id_users"),
        nullable=False,
    )
    # code_lineage_id — stocks_master.id lineage. FK 없음 (watchlist_items 선례).
    code_lineage_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False,
    )
    # body — Markdown 원문. 길이 cap 은 repository layer 강제.
    body: Mapped[str] = mapped_column(Text(), nullable=False)
    # scope — USER_SHARED 미구현(예약). repository 가 user-private 외 거부.
    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="user-private",
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        # owner-check / list hot path.
        Index("ix_stock_notes_user_id", "user_id"),
        # list_for_code 의 (user_id, code_lineage_id) 복합 필터 — user_id
        # predicate 누락 회귀 방지의 DB 차원 보강.
        Index("ix_stock_notes_user_code", "user_id", "code_lineage_id"),
    )
