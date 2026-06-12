"""ADR-0034 D4 — publishers 테이블 (인증 publisher handle claim).

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-05

M6 #3 — 인증 user 의 publisher handle claim 영속화. custom_packs(0017) 와 동형의
user_id FK 격리이나, **handle 인스턴스 유일** + **user 1:1** 제약을 추가로 둔다.
**append-only**(updated_at 컬럼 없음 — claim 은 1 회성 사실).

설계 결정 (ADR-0034 D4):
    - user_id FK → users.id (ADR-0021 D2 격리 anchor). naming
      `fk_publishers_user_id_users` — base.py naming_convention 일치.
    - handle: String(64). `@{handle}/...` v2 namespace 의 publisher 식별자.
      pattern(^[a-z0-9][a-z0-9-]*$) / 예약 handle 거부는 route+service — DB 는
      문자열 그대로.
    - created_at: append-only(updated_at 없음). UPDATE 경로 없음.
    - UniqueConstraint(user_id): user 1:1 — 같은 user 의 두 번째 claim 차단(§2.1).
    - handle unique Index: 인스턴스 유일 — 사칭 차단(`@{handle}/...` 발급권 단일
      user 귀속). 사전 조회를 우회한 race(동시 claim)도 위반을 IntegrityError →
      repository 가 PublisherClaimError 변환.

**insert 절대 없음 (시스템 생성 0)**:
    seeder/migration 어떤 경로도 publisher row 를 만들지 않는다. claim 은 인증
    route(POST /api/publishers) + repository 뿐. 본 migration 은 schema(table +
    UNIQUE + unique index)만 생성 — 빈 테이블.

PG/SQLite 양쪽 (FK + UNIQUE + unique Index):
    FK / UNIQUE 는 `op.create_table` 의 제약으로 dialect 무관 처리. handle unique
    Index 는 create_index(unique=True). SQLite FK 강제는 connection 단위 PRAGMA
    foreign_keys=ON 필요(테스트 conftest 활성, 운영 PG default ON).

기존 user/custom_packs 무영향 — 신규 테이블만 추가(0017 custom_packs 선례).

관련 ADR / 문서:
- ADR-0034 D4 (publisher 인증 발급 — handle claim + 사칭 차단)
- ADR-0021 D2 (users 테이블 + user_id FK 격리), migration 0017 (custom_packs FK 선례)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # publishers 테이블 — 인증 publisher handle claim(append-only). user_id FK →
    # users.id (ADR-0021 D2). updated_at 없음(append-only). insert 0(빈 테이블).
    op.create_table(
        "publishers",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("handle", sa.String(64), nullable=False),
        # created_at 만 — append-only(updated_at 없음, claim 은 1 회성 사실).
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_publishers"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_publishers_user_id_users",
        ),
        # user 1:1 — 같은 user 의 두 번째 claim 을 DB 차원에서 차단(§2.1).
        # IntegrityError → repository 가 PublisherClaimError(이미 claim) 변환.
        sa.UniqueConstraint("user_id", name="uq_publishers_user_id"),
    )
    # handle 인스턴스 유일 — 사칭 차단(`@{handle}/...` 발급권 단일 user 귀속).
    # unique Index → 같은 handle 두 user 점유 시 IntegrityError → 충돌 변환.
    op.create_index(
        "uq_publishers_handle", "publishers", ["handle"], unique=True,
    )


def downgrade() -> None:
    # 인덱스 → 테이블 역순 drop.
    op.drop_index("uq_publishers_handle", table_name="publishers")
    op.drop_table("publishers")
