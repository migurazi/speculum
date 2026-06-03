"""ADR-0022 D9 — custom_packs 테이블 (Factor Lab 사용자 정의 pack 영속화).

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-02

Factor Lab 사용자 정의 pack JSON 의 user-scoped 저장. watchlists/stock_notes 와
동형의 user_id FK 격리이나 **append-only immutable** — updated_at 컬럼 없음
(screen_runs 정신). 같은 (user_id, pack_slug, version) 의 다른 정의는 UNIQUE
제약으로 충돌(repository 가 409 변환)시킨다.

설계 결정 (ADR-0022 D9):
    - user_id FK → users.id (ADR-0021 D2 격리 anchor). naming
      `fk_custom_packs_user_id_users` — base.py naming_convention 일치.
    - pack_slug: String(128). 'user/{...}' / 'community/{...}'. canonical(빌트인)
      slug 는 service layer 가 저장 거부(사칭 차단) — DB 는 문자열 그대로.
    - version: String(32) semver. content_hash: String(71) "sha256:<64 hex>".
    - body: JSON(PG=JSONB) — pack 전체 정의. factor_count: Integer 표시용.
    - created_at: append-only(updated_at 없음). UPDATE 경로 없음.
    - UniqueConstraint(user_id, pack_slug, version): immutable append-only 의 DB
      차원 강제. 사전 조회를 우회한 race(동시 저장)도 IntegrityError → 충돌 변환.

**insert 절대 없음 (시스템 생성 0)**:
    seeder/migration/builtin 어떤 경로도 custom_pack row 를 만들지 않는다. pack
    생성은 인증 route + repository 뿐. 본 migration 은 schema(table + UNIQUE +
    index)만 생성 — 빈 테이블.

PG/SQLite 양쪽 (FK + UNIQUE + JSON):
    FK / UNIQUE 는 `op.create_table` 의 제약으로 dialect 무관 처리. JSON 컬럼은
    SQLite=JSON, PG=JSONB(ORM 의 with_variant). SQLite FK 강제는 connection 단위
    PRAGMA foreign_keys=ON 필요(테스트 conftest 활성, 운영 PG default ON).

인덱스:
    - ix_custom_packs_user_id: owner-check / list_for_user hot path.
    - ix_custom_packs_user_pack(user_id, pack_slug): slug 별 version 조회 +
      user_id predicate 누락 회귀 방지의 DB 차원 보강.

관련 ADR / 문서:
- ADR-0022 D9 (2026-06-02 — custom pack 영속화 설계 확정), D8.2 (canonical 불가침)
- ADR-0021 D2 (users 테이블 + user_id FK 격리), migration 0012/0014 (FK 선례)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # custom_packs 테이블 — 사용자 정의 pack(append-only immutable). user_id FK
    # → users.id (ADR-0021 D2). updated_at 없음(append-only). insert 0(빈 테이블).
    op.create_table(
        "custom_packs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("pack_slug", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        # body — pack 전체 정의 JSON. PG=JSONB, SQLite=JSON.
        sa.Column(
            "body",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("factor_count", sa.Integer(), nullable=False),
        # created_at 만 — append-only immutable(updated_at 없음).
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_custom_packs"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_custom_packs_user_id_users",
        ),
        # immutable append-only DB 강제 — 같은 (user, slug, version) 재저장은
        # IntegrityError → repository 가 409(충돌) 변환.
        sa.UniqueConstraint(
            "user_id", "pack_slug", "version",
            name="uq_custom_packs_user_slug_version",
        ),
    )
    # owner-check / list_for_user hot path 인덱스.
    op.create_index(
        "ix_custom_packs_user_id", "custom_packs", ["user_id"],
    )
    # 복합 인덱스 — (user_id, pack_slug). slug 별 version 조회 + user_id
    # predicate 누락 회귀 방지의 DB 차원 보강.
    op.create_index(
        "ix_custom_packs_user_pack",
        "custom_packs",
        ["user_id", "pack_slug"],
    )


def downgrade() -> None:
    # 인덱스 → 테이블 역순 drop.
    op.drop_index("ix_custom_packs_user_pack", table_name="custom_packs")
    op.drop_index("ix_custom_packs_user_id", table_name="custom_packs")
    op.drop_table("custom_packs")
