"""Citation Repository Protocol + Fake + SQL — `pit_protocols.py` 와 결 일관.

`SourceCitation` 모델은 `app/models/` 의 도메인 entity. Repository (DB 추상화)
와 Fake (test/in-memory infrastructure) 는 본 `app/repositories/` layer.

oracle 2 차 리뷰 M4 — `app/models/` 가 운영 도메인 entity 만 보유, Repository
는 본 layer 로 분리하여 `pit_protocols.py` 와 동일 패턴 유지.

T13 합류 — `SqlCitationRepository` 추가 (SQLAlchemy 2 sync session).
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.converters import (
    citation_orm_to_record,
    citation_record_to_orm,
)
from app.db.orm.source_citations import SourceCitationORM
from app.models.source_citation import SourceCitation, SourceCitationError

__all__ = [
    "CitationRepository",
    "FakeCitationRepository",
    "SqlCitationRepository",
]


@runtime_checkable
class CitationRepository(Protocol):
    """Citation 저장/조회 contract.

    DB layer 의 append-only invariant — 본 Protocol 은 update method 의도적
    부재 (ADR-0002 D5 의 "UPDATE 금지" 와 일관).
    """

    def save(self, citation: SourceCitation) -> None:
        """Citation row 생성. id 중복 시 구현체 정책 (raise 권장)."""
        ...

    def fetch_by_id(self, citation_id: UUID) -> SourceCitation | None:
        """`citation_id` 의 citation. 없으면 None."""
        ...

    def fetch_by_batch(self, batch_id: UUID) -> Sequence[SourceCitation]:
        """같은 batch 의 모든 citation. 재현성 검증·rollback 시 사용."""
        ...


class FakeCitationRepository(CitationRepository):
    """In-memory citation store — SQLAlchemy 구현체의 contract reference."""

    def __init__(self) -> None:
        self._by_id: dict[UUID, SourceCitation] = {}
        self._by_batch: dict[UUID, list[SourceCitation]] = {}

    def save(self, citation: SourceCitation) -> None:
        # append-only — id 중복 시 raise (DB UNIQUE constraint 동등).
        if citation.id in self._by_id:
            raise SourceCitationError(
                f"citation with id {citation.id} already exists "
                f"(append-only invariant)"
            )
        self._by_id[citation.id] = citation
        self._by_batch.setdefault(citation.batch_id, []).append(citation)

    def fetch_by_id(self, citation_id: UUID) -> SourceCitation | None:
        return self._by_id.get(citation_id)

    def fetch_by_batch(self, batch_id: UUID) -> Sequence[SourceCitation]:
        # created_at 오름차순 — 재현성을 위한 결정적 ordering.
        return tuple(sorted(
            self._by_batch.get(batch_id, []),
            key=lambda c: (c.created_at, str(c.id)),
        ))


class SqlCitationRepository(CitationRepository):
    """SQLAlchemy 기반 citation store — T13 합류.

    append-only invariant 강제:
        - SQLAlchemy session 의 add/flush 로 INSERT 만 사용.
        - 같은 id 의 row 가 이미 있으면 `SourceCitationError` raise (DB UNIQUE
          constraint 위반 → IntegrityError 를 변환).
        - DB layer 의 BEFORE UPDATE trigger 가 UPDATE 차단 (PostgreSQL 한정,
          alembic 0001 migration). SQLite (test) 는 본 layer 의 id 중복 검사가
          유일 방어.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, citation: SourceCitation) -> None:
        # id 중복 사전 검사 — DB IntegrityError 보다 명확한 SourceCitationError.
        existing = self._session.get(SourceCitationORM, citation.id)
        if existing is not None:
            raise SourceCitationError(
                f"citation with id {citation.id} already exists "
                f"(append-only invariant)"
            )
        self._session.add(citation_record_to_orm(citation))
        self._session.flush()

    def fetch_by_id(self, citation_id: UUID) -> SourceCitation | None:
        orm = self._session.get(SourceCitationORM, citation_id)
        return citation_orm_to_record(orm) if orm is not None else None

    def fetch_by_batch(self, batch_id: UUID) -> Sequence[SourceCitation]:
        stmt = (
            select(SourceCitationORM)
            .where(SourceCitationORM.batch_id == batch_id)
            .order_by(
                SourceCitationORM.created_at.asc(),
                SourceCitationORM.id.asc(),
            )
        )
        result = self._session.execute(stmt)
        return tuple(citation_orm_to_record(o) for o in result.scalars().all())
