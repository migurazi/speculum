"""PublisherORM — ADR-0034 D4 publisher handle claim (인증 publisher 발급).

M6 #3 (M6_PLAN §3) — 인증 user 가 자기 publisher handle 을 claim 하고, 그 user 만
`@{handle}/...` v2 pack 을 발급하도록 사칭을 차단한다(§2.1). custom_packs ORM
(user_id FK 격리 anchor) 패턴을 복제하되, **handle 인스턴스 유일** + **user 1:1**
제약을 추가로 둔다.

핵심 결정 (ADR-0034 D4):
    - **user_id FK → users.id** (ADR-0021 D2 격리 anchor). naming
      `fk_publishers_user_id_users` (base.py naming_convention). publisher 의
      소유 user 를 못박는 FK anchor.
    - **append-only** — UPDATE 컬럼(updated_at) 없음. handle claim 은 1 회성
      사실(custom_packs 의 append-only immutable 정신). handle 변경/이전은 본
      사이클 scope 밖(claim 만).
    - **user 1:1** — UniqueConstraint(user_id). 한 user 는 정확히 하나의
      publisher handle 만 claim(§2.1 — user↔handle 1:1). 두 번째 claim 은
      IntegrityError → repository 가 PublisherClaimError 변환.
    - **handle 인스턴스 유일** — Index(handle, unique=True). 한 인스턴스에서 같은
      handle 을 두 user 가 점유 불가(사칭 차단 — `@{handle}/...` 발급권은 단일
      user 에 귀속). users ORM(uq_users_google_sub) 의 unique Index 패턴.

제약 / 인덱스:
    - uq_publishers_user_id(user_id): user 1:1 — 같은 user 의 두 번째 claim 차단.
    - uq_publishers_handle(handle, unique): handle 인스턴스 유일 — 사칭 차단.

**insert 절대 없음 (시스템 생성 0)**:
    seeder/migration 어떤 경로도 publisher row 를 만들지 않는다. claim 은 인증
    route(POST /api/publishers) + repository 뿐. migration 은 빈 테이블만 생성.

관련 ADR / 문서:
- ADR-0034 D4 (publisher 인증 발급 — handle claim + 사칭 차단)
- ADR-0021 D2 (users 테이블 + user_id FK 격리), migration 0017 (custom_packs FK 선례)
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class PublisherORM(Base):
    """publishers row — ADR-0034 D4 인증 publisher handle claim(append-only)."""

    __tablename__ = "publishers"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    # user_id FK → users.id (ADR-0021 D2). publisher 소유 user 의 FK anchor.
    # NOT NULL — 모든 publisher 는 정확히 한 user 에 귀속.
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_publishers_user_id_users"),
        nullable=False,
    )
    # handle — `@{handle}/...` v2 namespace 의 publisher 식별자. 인스턴스 유일
    # (uq_publishers_handle). pattern(^[a-z0-9][a-z0-9-]*$ ≤64) 강제는 route 의
    # strict 스키마 — DB 는 문자열 그대로(예약 handle 거부도 route/service).
    handle: Mapped[str] = mapped_column(String(64), nullable=False)
    # created_at — claim 시각(UTC). **updated_at 없음** — append-only(claim 은
    # 1 회성 사실, handle 변경 경로 없음).
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        # user 1:1 — 같은 user 의 두 번째 claim 을 DB 차원에서 차단(§2.1).
        # 사전 조회를 우회한 race 도 IntegrityError → PublisherClaimError 변환.
        UniqueConstraint("user_id", name="uq_publishers_user_id"),
        # handle 인스턴스 유일 — 사칭 차단(`@{handle}/...` 발급권 단일 user 귀속).
        # users ORM(uq_users_google_sub)의 unique Index 패턴.
        Index("uq_publishers_handle", "handle", unique=True),
    )
