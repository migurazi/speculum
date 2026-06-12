"""CustomPackORM — ADR-0022 D9 Factor Lab 사용자 정의 pack 영속화 (user-scoped).

`app/repositories/custom_pack_repository.py` 의 `CustomPack` frozen dataclass 의
DB 표현. `stock_notes.py`(user_id FK anchor) + `screener_sets.py`(JSON body 컬럼)
구조를 복제하되 **append-only immutable** — UPDATE 컬럼(updated_at) 없음.

핵심 결정 (ADR-0022 D9):
    - **user_id FK → users.id** (ADR-0021 D2 격리 anchor). naming
      `fk_custom_packs_user_id_users` (base.py 규약). user 격리의 FK anchor.
    - **append-only immutable** — UPDATE 메서드 / updated_at 컬럼 없음. 같은
      (user_id, pack_slug, version) 의 다른 정의는 신규 row 가 아니라 충돌(409).
      screen_runs(append-only) 정신을 따른다(stock_notes 의 mutable 과 다름).
    - **content_hash** — JCS+SHA-256 봉인값("sha256:<hex>", 71자). load 시
      재검증(converter)으로 변조 탐지. 클라이언트 제출 hash 무시·서버 재봉인.
    - **body JSON** — pack 전체 정의(JCS 직렬화). PG=JSONB, SQLite=JSON.
    - **factor_count** — 표시용 비정규화. body['factors'] 길이.
    - **pack_slug 는 derive_tier 가 canonical 이 아닌 것만**(repository/route 강제).
      M2 는 user/ tier — DB 는 slug 문자열 그대로 저장(tier 판정은 service).

UniqueConstraint / 인덱스:
    - uq_custom_packs_user_slug_version(user_id, pack_slug, version): immutable
      append-only 의 DB 차원 강제 — 같은 (user, slug, version) 재저장은 충돌
      신호(IntegrityError → repository 가 409 변환).
    - ix_custom_packs_user_id: owner-check / list_for_user hot path.
    - ix_custom_packs_user_pack(user_id, pack_slug): slug 별 version 조회 보강 +
      user_id predicate 누락 회귀 방지의 DB 차원 보강.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class CustomPackORM(Base):
    """custom_packs row — ADR-0022 D9 사용자 정의 pack(append-only immutable)."""

    __tablename__ = "custom_packs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    # user_id FK → users.id (ADR-0021 D2). user 격리 FK anchor. NOT NULL.
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_custom_packs_user_id_users"),
        nullable=False,
    )
    # pack_slug — 'user/{...}' / 'community/{...}'. canonical(빌트인)은 service
    # 가 저장 거부(사칭 차단). DB 는 문자열 그대로.
    pack_slug: Mapped[str] = mapped_column(String(128), nullable=False)
    # version — semver "x.y.z".
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    # content_hash — "sha256:<64 hex>" (71자). load 시 converter 가 재검증.
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    # body — pack 전체 정의 JSON. PG=JSONB, SQLite=JSON.
    body: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    # factor_count — 표시용 비정규화 (body['factors'] 길이).
    factor_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    # created_at — 생성 시각(UTC). **updated_at 없음** — append-only immutable.
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    # visibility — ADR-0028 D1 공유 메타. 'private'(default) | 'public'. body/
    # content_hash 와 달리 **mutable**(공유 토글) — append-only 불변은 body 만
    # 적용되며 visibility 는 사용자 의도 메타다(재현성 입력 아님). NOT NULL +
    # server_default 'private'(migration 0018 backfill). 값 도메인 강제는 route/
    # repository(set_visibility 가 두 값만 허용)가 담당.
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="private",
    )
    # source_url — ADR-0032 D3 provenance. import 출처 URL(예: GitHub raw). **body 가
    # 아닌 row 메타**라 content_hash(body 기준 봉인)에 영향 0 — ADR-0020 immutability
    # 불변식 보존(provenance 를 body 에 넣으면 봉인 파손). editor 생성 pack 은 NULL.
    # nullable → 별도 server_default/backfill 불필요(기존 row 는 NULL = 출처 미상).
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    __table_args__ = (
        # immutable append-only DB 강제 — 같은 (user, slug, version) 재저장은
        # IntegrityError → repository 가 409(충돌) 변환.
        UniqueConstraint(
            "user_id", "pack_slug", "version",
            name="uq_custom_packs_user_slug_version",
        ),
        # owner-check / list_for_user hot path.
        Index("ix_custom_packs_user_id", "user_id"),
        # (user_id, pack_slug) 복합 — slug 별 version 조회 + user_id predicate
        # 누락 회귀 방지의 DB 차원 보강.
        Index("ix_custom_packs_user_pack", "user_id", "pack_slug"),
    )
