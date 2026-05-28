"""ScreenRunSnapshotORM — ADR-0008 D7 의 screen_runs 테이블.

`app/services/screen_run.py` 의 `ScreenRunSnapshot` frozen dataclass 의 DB
표현. T13 Phase C 산출물.

8 기둥 §2.10 Reproducibility 의 정점 — query + as_of + result_codes +
data_versions + result_hash 의 완전 freeze.

핵심 결정:
    - **query JSON** — ScreenRunQuery 의 conditions + selected_factors +
      presentation_order. PG=JSONB, SQLite=JSON.
    - **result_codes JSON** — `tuple[str, ...]` → JSON array.
    - **data_versions JSON** — `Mapping[str, str]` → JSON object.
    - **user_id 인덱스 + computed_at 인덱스** — fetch_recent hot path.
    - **result_hash 컬럼 String** — `"sha256:<hex>"` prefix 포함, 67 chars.
    - **append-only 정책 X** — M0 정책 = overwrite 허용 (Fake 와 동일).
      운영 시 정정공시 chain 처럼 별도 한 cycle 의 ADR 결정 필요.

관련 ADR:
- ADR-0008 D7 — screen_runs schema (본 ORM 의 source-of-truth)
- ADR-0002 D5 — Run snapshot 자리 (T30 = stub, 본 cycle 이 본격 구현)
- 8 기둥 §2.10 Reproducibility — freeze 단위
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Date, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class ScreenRunSnapshotORM(Base):
    """screen_runs row — Screen Run 의 완전 freeze entity."""

    __tablename__ = "screen_runs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    # query: ScreenRunQuery 의 conditions + selected_factors + presentation_order
    # nested JSON. PG=JSONB, SQLite=JSON.
    query: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    # result_codes: tuple[str] → JSON array. 운영 size 1만 종목 = ~80KB JSON.
    result_codes: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    # result_hash: "sha256:<64 hex>" — 7 + 64 = 71 chars + 여유. 운영 size 80.
    result_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    # data_versions: dict[str, str] — 정책 freeze key→version map.
    data_versions: Mapped[dict[str, str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    computed_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False,
    )

    __table_args__ = (
        # fetch_recent: WHERE user_id ORDER BY computed_at DESC LIMIT N.
        # 복합 인덱스가 정렬도 충당 (descending 은 PG/SQLite 모두 reverse scan).
        Index(
            "ix_screen_runs_user_computed",
            "user_id", "computed_at",
        ),
        # result_hash 단독 검색 — 재현성 검증 / 디버깅.
        Index("ix_screen_runs_result_hash", "result_hash"),
    )
