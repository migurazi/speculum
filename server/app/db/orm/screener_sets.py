"""ScreenerSetORM — ADR-0011 + M0_PLAN T28 의 조건셋 entity.

`app/repositories/watchlist_repository.py` 의 `ScreenerSet` frozen dataclass
의 DB 표현. Watchlist 와 분리된 entity — "저장된 query" (재실행 가능).

핵심 결정:
    - **conditions / selected_factors JSON** — tuple[dict] / tuple[str] 의
      직렬화. PG=JSONB, SQLite=JSON.
    - **user_id 인덱스** — list_all hot path. FK 없음 (M0 single-user).
    - **이름 중복 허용** — ScreenerSet 은 UUID 식별 (Fake docstring 일관).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class ScreenerSetORM(Base):
    """screener_sets row — 조건셋 저장 entity."""

    __tablename__ = "screener_sets"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # conditions: list of {"factor": "per:ttm", "op": "<", "value": "10"}.
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    # selected_factors: list of factor canonical_id.
    selected_factors: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("ix_screener_sets_user_id", "user_id"),
        # list_all 의 updated_at desc 정렬 보강 (user_id 와 함께).
        Index(
            "ix_screener_sets_user_updated",
            "user_id", "updated_at",
        ),
    )
