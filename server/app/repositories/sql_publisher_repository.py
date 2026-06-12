"""SQL Publisher Repository — ADR-0034 D4 인증 publisher handle claim.

`publisher_repository.py` 의 `FakePublisherRepository` 와 동일 contract 를
SQLAlchemy 2 sync session 위에서 구현. `sql_custom_pack_repository.py` 의
사전 조회 + IntegrityError 변환 패턴을 복제하되, **append-only**(claim/get 만)
이며 handle 인스턴스 유일 + user 1:1 을 강제한다.

설계 결정:
1. **Fake 와 동일 invariant 강제** — handle 인스턴스 유일, user 1:1, idempotent
   (같은 user+handle 재요청). UNIQUE(user_id) / handle unique Index 를 DB 가
   강제하나, 동일 트랜잭션 내 가시성과 명확한 에러를 위해 사전 조회로도 판정한다.
2. **session 의존** — `__init__(session: Session)`. 한 request = 한 session.
3. **명시적 flush** — Fake 와 contract 동등성을 위해 claim 직후 flush. commit 은
   `get_db_session_or_none`(DI wiring)가 endpoint 종료 시 수행.
4. **IntegrityError(UNIQUE/unique Index) → PublisherClaimError** — 사전 조회를
   우회한 race(동시 claim)에서도 위반을 충돌로 변환(fail-loud, 사칭 차단 유지).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.orm.publishers import PublisherORM
from app.repositories.publisher_repository import (
    Publisher,
    PublisherClaimError,
    PublisherRepository,
)

__all__ = ["SqlPublisherRepository"]


def _now() -> datetime:
    return datetime.now(UTC)


def _orm_to_record(orm: PublisherORM) -> Publisher:
    """ORM → frozen dataclass — 단순 field 복사(invariant 는 dataclass 자체)."""
    return Publisher(
        id=orm.id,
        user_id=orm.user_id,
        handle=orm.handle,
        created_at=orm.created_at,
    )


class SqlPublisherRepository(PublisherRepository):
    """SQLAlchemy 기반 publisher repository — append-only(claim/get 만)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def claim(self, *, user_id: UUID, handle: str) -> Publisher:
        # 사전 조회 — handle 점유 우선 판정(사칭 차단의 명확한 에러).
        by_handle = self.get_by_handle(handle)
        if by_handle is not None:
            if by_handle.user_id == user_id:
                # 같은 user + 같은 handle 재요청 — idempotent(기존 반환).
                return by_handle
            raise PublisherClaimError(
                f"handle '{handle}' 는 이미 다른 사용자가 점유함 — "
                f"publisher handle 은 인스턴스 유일(ADR-0034 D4 사칭 차단)."
            )
        by_user = self.get_by_user_id(user_id)
        if by_user is not None:
            raise PublisherClaimError(
                f"사용자는 이미 handle '{by_user.handle}' 를 claim 함 — "
                f"user 당 publisher handle 은 1 개(ADR-0034 D4, §2.1 user 1:1)."
            )
        record = Publisher(
            id=uuid4(),
            user_id=user_id,
            handle=handle,
            created_at=_now(),
        )
        self._session.add(
            PublisherORM(
                id=record.id,
                user_id=record.user_id,
                handle=record.handle,
                created_at=record.created_at,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            # 사전 조회를 우회한 race(동시 claim) — UNIQUE(user_id) 또는 handle
            # unique Index 위반을 충돌로 변환(사칭 차단/1:1 유지, fail-loud).
            self._session.rollback()
            raise PublisherClaimError(
                f"handle '{handle}' 동시 claim 충돌 — handle 인스턴스 유일 또는 "
                f"user 1:1 위반(ADR-0034 D4)."
            ) from exc
        return record

    def get_by_user_id(self, user_id: UUID) -> Publisher | None:
        # UNIQUE(user_id) 라 최대 1 건.
        stmt = select(PublisherORM).where(PublisherORM.user_id == user_id)
        orm = self._session.execute(stmt).scalars().first()
        return _orm_to_record(orm) if orm is not None else None

    def get_by_handle(self, handle: str) -> Publisher | None:
        # handle unique Index 라 최대 1 건.
        stmt = select(PublisherORM).where(PublisherORM.handle == handle)
        orm = self._session.execute(stmt).scalars().first()
        return _orm_to_record(orm) if orm is not None else None
