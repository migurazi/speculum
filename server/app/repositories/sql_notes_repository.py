"""SQL Notes Repository — T76 종목별 사용자 Markdown 메모 (USER_PRIVATE).

`notes_repository.py` 의 `FakeNotesRepository` 와 동일 contract 를 SQLAlchemy 2
sync session 위에서 구현. `sql_user_repositories.py` 의 SqlWatchlistRepository
owner-check CRUD 패턴을 복제하되 폴더 개념 없이 종목별 직접 메모이며, **평범한
mutable UPDATE/DELETE**(SqlScreenRunRepository 의 append-only 가 아님).

설계 결정:
1. **Fake 와 동일 invariant 강제** — body 길이/공백 cap, scope(user-private 외
   거부), owner-check. helper(`_validate_body`/`_reject_non_private_scope`)는
   notes_repository 에서 import 공유.
2. **session 의존** — `__init__(session: Session)`. 한 request = 한 session.
3. **명시적 flush** — Fake 와 contract 동등성을 위해 create/update 직후 flush.
   commit 은 `get_db_session_or_none`(DI wiring)가 endpoint 종료 시 수행.
4. **list_for_code 는 user_id AND code_lineage_id 둘 다 WHERE** — user_id
   predicate 누락 시 전 사용자 누출(가장 위험). DB 복합 인덱스로도 보강.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.converters import note_orm_to_record, note_record_to_orm
from app.db.orm.notes import StockNoteORM
from app.repositories.notes_repository import (
    _PRIVATE_SCOPE,
    Note,
    NotesRepository,
    _reject_non_private_scope,
    _validate_body,
)

__all__ = ["SqlNotesRepository"]


def _now() -> datetime:
    return datetime.now(UTC)


class SqlNotesRepository(NotesRepository):
    """SQLAlchemy 기반 Notes repository — T76 USER_PRIVATE mutable CRUD."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        user_id: UUID,
        code_lineage_id: UUID,
        body: str,
        scope: str = _PRIVATE_SCOPE,
    ) -> Note:
        # scope 검증 우선 — USER_SHARED 봉쇄(미구현, 예약).
        _reject_non_private_scope(scope)
        _validate_body(body)
        now = _now()
        record = Note(
            id=uuid4(),
            user_id=user_id,
            code_lineage_id=code_lineage_id,
            body=body,
            scope=scope,
            created_at=now,
            updated_at=now,
        )
        self._session.add(note_record_to_orm(record))
        self._session.flush()
        return record

    def list_for_code(
        self, code_lineage_id: UUID, *, user_id: UUID,
    ) -> Sequence[Note]:
        # user_id AND code_lineage_id 둘 다 WHERE — user_id 누락 시 전 사용자
        # 누출(가장 위험). created_at / id 보조 정렬로 결정성 보장.
        stmt = (
            select(StockNoteORM)
            .where(
                StockNoteORM.user_id == user_id,
                StockNoteORM.code_lineage_id == code_lineage_id,
            )
            .order_by(
                StockNoteORM.created_at.asc(),
                StockNoteORM.id.asc(),
            )
        )
        result = self._session.execute(stmt).scalars().all()
        return tuple(note_orm_to_record(o) for o in result)

    def get(self, note_id: UUID, *, user_id: UUID) -> Note | None:
        orm = self._session.get(StockNoteORM, note_id)
        if orm is None or orm.user_id != user_id:
            return None
        return note_orm_to_record(orm)

    def update(
        self, note_id: UUID, body: str, *, user_id: UUID,
    ) -> Note | None:
        orm = self._session.get(StockNoteORM, note_id)
        if orm is None or orm.user_id != user_id:
            return None
        _validate_body(body)
        # mutable UPDATE — append-only 아님. updated_at 갱신.
        orm.body = body
        orm.updated_at = _now()
        self._session.flush()
        return note_orm_to_record(orm)

    def delete(self, note_id: UUID, *, user_id: UUID) -> bool:
        orm = self._session.get(StockNoteORM, note_id)
        if orm is None or orm.user_id != user_id:
            return False
        self._session.delete(orm)
        self._session.flush()
        return True
