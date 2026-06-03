"""SQL Custom Pack Repository — ADR-0022 D9 사용자 정의 pack 영속화.

`custom_pack_repository.py` 의 `FakeCustomPackRepository` 와 동일 contract 를
SQLAlchemy 2 sync session 위에서 구현. `sql_notes_repository.py` 의 owner-check
패턴을 복제하되 **append-only immutable** — UPDATE 없이 save/list/get/delete.

설계 결정:
1. **Fake 와 동일 invariant 강제** — body field 추출(extract_pack_fields), owner
   -check, idempotent / 충돌 판정. UNIQUE(user_id,pack_slug,version)을 DB 가 강제
   하나, 동일 트랜잭션 내 가시성과 명확한 에러를 위해 사전 조회로도 판정한다.
2. **session 의존** — `__init__(session: Session)`. 한 request = 한 session.
3. **명시적 flush** — Fake 와 contract 동등성을 위해 save 직후 flush. commit 은
   `get_db_session_or_none`(DI wiring)가 endpoint 종료 시 수행.
4. **IntegrityError(UNIQUE) → 충돌 변환** — 사전 조회를 우회한 race(동시 저장)
   에서도 UNIQUE 위반을 CustomPackDataError 로 변환(fail-loud, append-only).
5. **list_for_user 는 반드시 user_id WHERE** — 누락 시 전 사용자 누출(가장 위험).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.converters import pack_orm_to_record, pack_record_to_orm
from app.db.orm.custom_packs import CustomPackORM
from app.repositories.custom_pack_repository import (
    VALID_VISIBILITIES,
    VISIBILITY_PUBLIC,
    CustomPack,
    CustomPackDataError,
    CustomPackRepository,
    extract_pack_fields,
)

__all__ = ["SqlCustomPackRepository"]


def _now() -> datetime:
    return datetime.now(UTC)


class SqlCustomPackRepository(CustomPackRepository):
    """SQLAlchemy 기반 custom pack repository — append-only immutable."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, *, user_id: UUID, body: dict) -> CustomPack:
        pack_slug, version, content_hash, _ = extract_pack_fields(body)
        # 사전 조회 — 같은 (user_id, slug, version) 기존 row.
        stmt = select(CustomPackORM).where(
            CustomPackORM.user_id == user_id,
            CustomPackORM.pack_slug == pack_slug,
            CustomPackORM.version == version,
        )
        existing = self._session.execute(stmt).scalars().first()
        if existing is not None:
            if existing.content_hash == content_hash:
                # 같은 정의 재저장 — idempotent.
                return pack_orm_to_record(existing)
            raise CustomPackDataError(
                f"pack '{pack_slug}' v{version} 는 이미 다른 정의로 저장됨 — "
                f"immutable(append-only) 위반. 버전을 올리세요."
            )
        record = CustomPack(
            id=uuid4(),
            user_id=user_id,
            pack_slug=pack_slug,
            version=version,
            content_hash=content_hash,
            body=body,
            factor_count=len(body["factors"]),
            created_at=_now(),
        )
        self._session.add(pack_record_to_orm(record))
        try:
            self._session.flush()
        except IntegrityError as exc:
            # 사전 조회를 우회한 race(동시 저장) — UNIQUE 위반을 충돌로 변환.
            self._session.rollback()
            raise CustomPackDataError(
                f"pack '{pack_slug}' v{version} 동시 저장 충돌 — "
                f"immutable(append-only) 위반."
            ) from exc
        return record

    def list_for_user(self, *, user_id: UUID) -> tuple[CustomPack, ...]:
        # user_id WHERE 필수 — 누락 시 전 사용자 누출(가장 위험). created_at desc,
        # id 보조 정렬로 결정성 보장.
        stmt = (
            select(CustomPackORM)
            .where(CustomPackORM.user_id == user_id)
            .order_by(
                CustomPackORM.created_at.desc(),
                CustomPackORM.id.desc(),
            )
        )
        result = self._session.execute(stmt).scalars().all()
        return tuple(pack_orm_to_record(o) for o in result)

    def get(self, pack_id: UUID, *, user_id: UUID) -> CustomPack | None:
        orm = self._session.get(CustomPackORM, pack_id)
        if orm is None or orm.user_id != user_id:
            return None
        return pack_orm_to_record(orm)

    def delete(self, pack_id: UUID, *, user_id: UUID) -> bool:
        orm = self._session.get(CustomPackORM, pack_id)
        if orm is None or orm.user_id != user_id:
            return False
        self._session.delete(orm)
        self._session.flush()
        return True

    def get_by_slug_version(
        self, pack_slug: str, version: str, *, user_id: UUID,
    ) -> CustomPack | None:
        # user_id AND pack_slug AND version WHERE — UNIQUE 라 최대 1 건. user_id
        # 누락 시 타 user pack 노출(IDOR) 이므로 세 조건 모두 필수.
        stmt = select(CustomPackORM).where(
            CustomPackORM.user_id == user_id,
            CustomPackORM.pack_slug == pack_slug,
            CustomPackORM.version == version,
        )
        orm = self._session.execute(stmt).scalars().first()
        return pack_orm_to_record(orm) if orm is not None else None

    def list_public(self) -> tuple[CustomPack, ...]:
        # ADR-0028 D3 — visibility=='public' 만, user 무관(공개 목록). 비공개
        # 누출 차단의 단일 WHERE. created_at desc, id 보조 정렬(결정성). 큐레이션
        # 신호 0 — 정렬은 created_at(사실)만, 다운로드/인기/순위 컬럼 자체가 없다.
        stmt = (
            select(CustomPackORM)
            .where(CustomPackORM.visibility == VISIBILITY_PUBLIC)
            .order_by(
                CustomPackORM.created_at.desc(),
                CustomPackORM.id.desc(),
            )
        )
        result = self._session.execute(stmt).scalars().all()
        return tuple(pack_orm_to_record(o) for o in result)

    def get_public(self, pack_slug: str, version: str) -> CustomPack | None:
        # ADR-0028 D4 — visibility=='public' AND (slug, version). 비공개 누출
        # 차단. 동일 (slug, version) 다중 공개 시 created_at desc 첫 건(결정적,
        # 사실 정렬 — 큐레이션 아님). user 무관(공개 pack body).
        stmt = (
            select(CustomPackORM)
            .where(
                CustomPackORM.visibility == VISIBILITY_PUBLIC,
                CustomPackORM.pack_slug == pack_slug,
                CustomPackORM.version == version,
            )
            .order_by(
                CustomPackORM.created_at.desc(),
                CustomPackORM.id.desc(),
            )
        )
        orm = self._session.execute(stmt).scalars().first()
        return pack_orm_to_record(orm) if orm is not None else None

    def set_visibility(
        self, pack_id: UUID, *, user_id: UUID, visibility: str,
    ) -> bool:
        # 값 도메인 방어 — route 가 선행 검증하나 repository 도 거부(두 값만 허용).
        if visibility not in VALID_VISIBILITIES:
            raise CustomPackDataError(
                f"visibility 는 'private'/'public' 만 허용 — {visibility!r}"
            )
        orm = self._session.get(CustomPackORM, pack_id)
        # owner-check — 미존재/owner mismatch 시 False(IDOR — 타 user pack 의
        # 공유 상태 변경 차단). mismatch 와 미존재 구별 안 함(user 정보 누출 차단).
        if orm is None or orm.user_id != user_id:
            return False
        # ADR-0020 — body/content_hash 불변. visibility 컬럼만 UPDATE. custom_packs
        # 는 append-only trigger 대상이 아니므로 본 UPDATE 가 DB trigger 에 막히지
        # 않는다(0007 은 financials/corporate_actions 만).
        orm.visibility = visibility
        self._session.flush()
        return True
