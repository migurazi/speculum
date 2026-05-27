"""SourceCitationORM — ADR-0002 D5 의 `source_citations` 테이블.

`app/models/source_citation.py` 의 frozen dataclass `SourceCitation` 의 DB 표현.
변환은 `app/db/converters.py` 가 양방향 책임.

핵심 invariant:
    - **append-only** — ADR-0002 D3 / D5. UPDATE 차단은 PostgreSQL trigger 로
      구현 (Alembic 0001 migration). SQLite (test 환경) 는 trigger 없음 — Repository
      layer 가 save 시 id 중복 검사로 부분 방어.
    - **id 는 PK + UUID** — 모든 fact (price/financial/corporate_action) row 가
      `citation_id` FK 로 참조. 누락 시 빌드 게이트 (T23) 차단.
    - **batch_id index** — 같은 일배치의 모든 citation 함께 freeze (재현성).
    - **effective_date index** — PIT 쿼리 (`effective_date <= as_of`) 의 backbone.

컬럼 ↔ dataclass 매핑:
    - `id` ↔ `id` (UUID PK)
    - `source` ↔ `source` (SourceKind enum → str)
    - `identifier` ↔ `identifier` (String(500))
    - `retrieved_at` ↔ `retrieved_at` (UTC tz-aware datetime)
    - `effective_date` ↔ `effective_date` (KST date)
    - `adapter_version` ↔ `adapter_version` (String(64))
    - `batch_id` ↔ `batch_id` (UUID)
    - `url` ↔ `url` (String(2048), nullable)
    - `created_at` ↔ `created_at` (UTC tz-aware datetime, default now)
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class SourceCitationORM(Base):
    """source_citations row — ADR-0002 D3 의 7-tuple + created_at."""

    __tablename__ = "source_citations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    # source — SourceKind enum 의 string value. enum 컬럼 대신 String + 호출자
    # 검증 (frozen dataclass `__post_init__` + repository save 시 변환).
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    identifier: Mapped[str] = mapped_column(String(500), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False,
    )
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    # ADR-0002 D3 — url None 허용 (FDR 등 일부 source).
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False,
    )

    __table_args__ = (
        # ADR-0002 D5 line 193 — batch_id 인덱스 (재현성·rollback).
        Index("ix_source_citations_batch_id", "batch_id"),
        # ADR-0002 D5 line 194 — effective_date 인덱스 (PIT 쿼리).
        Index("ix_source_citations_effective_date", "effective_date"),
    )
