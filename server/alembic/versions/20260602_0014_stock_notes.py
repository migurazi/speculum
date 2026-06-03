"""M2 T76 — stock_notes 테이블 (종목별 사용자 Markdown 메모, USER_PRIVATE).

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-02

종목 lineage 단위의 사용자 개인 메모(Markdown body)를 저장하는 user-scoped
mutable 테이블. watchlist_items 와 동형이나 폴더 개념 없이 종목별 직접 메모.

설계 결정 (T76):
    - user_id FK → users.id (ADR-0021 D2 격리 anchor). naming
      `fk_stock_notes_user_id_users` — base.py naming_convention 일치.
    - code_lineage_id: stocks_master.id lineage UUID (FK 없음 — watchlist_items
      선례와 동일. stocks_master 미bootstrap 환경 호환).
    - body: TEXT — Markdown 원문. 길이 cap(10000자)은 repository layer 가 강제
      (DB CHECK 미사용 — dialect 호환 + 정책 변경 유연성).
    - scope: String default 'user-private'. USER_SHARED 는 미구현(예약).
      repository 가 user-private 외 거부.
    - created_at / updated_at: mutable CRUD (append-only 아님 — screen_runs 와
      다름). UPDATE 시 updated_at 갱신.

**insert 절대 없음 (T76 시스템 생성 Notes 0 원칙)**:
    seeder/migration/builtin 어떤 경로도 Note row 를 만들지 않는다. Note 생성은
    인증 route + repository 뿐. 본 migration 은 schema(table + index)만 생성.

PG/SQLite 양쪽 (FK):
    FK 추가는 `op.create_table` 의 ForeignKeyConstraint 로 dialect 무관 처리.
    SQLite 의 FK 강제는 connection 단위 PRAGMA foreign_keys=ON 필요(테스트
    conftest 가 활성, 운영 PG 는 default ON).

인덱스:
    - ix_stock_notes_user_id: list/owner-check hot path.
    - ix_stock_notes_user_code(user_id, code_lineage_id): list_for_code 의
      복합 필터(user_id AND code_lineage_id) — user_id predicate 누락 회귀
      방지의 DB 차원 보강.

관련 ADR / 문서:
- ADR-0021 D2 (users 테이블 + user_id FK 격리), migration 0012 (FK 선례)
- forbidden_words CheckScope.USER_PRIVATE (검사 X — 본인만 보는 텍스트)
- docs/work-orders/m2-milestone.md T76
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # stock_notes 테이블 — 종목별 사용자 개인 메모(USER_PRIVATE).
    # user_id FK → users.id (ADR-0021 D2). code_lineage_id 는 FK 없음
    # (watchlist_items 선례 — stocks_master 미bootstrap 호환).
    op.create_table(
        "stock_notes",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code_lineage_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        # scope server_default 'user-private' — USER_SHARED 미구현(예약).
        # repository 가 user-private 외 거부.
        sa.Column(
            "scope",
            sa.String(16),
            nullable=False,
            server_default="user-private",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stock_notes"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_stock_notes_user_id_users",
        ),
    )
    # owner-check / list hot path 인덱스.
    op.create_index(
        "ix_stock_notes_user_id", "stock_notes", ["user_id"],
    )
    # 복합 인덱스 — list_for_code 의 (user_id, code_lineage_id) 필터.
    # user_id predicate 누락 회귀 방지의 DB 차원 보강.
    op.create_index(
        "ix_stock_notes_user_code",
        "stock_notes",
        ["user_id", "code_lineage_id"],
    )


def downgrade() -> None:
    # 인덱스 → 테이블 역순 drop.
    op.drop_index("ix_stock_notes_user_code", table_name="stock_notes")
    op.drop_index("ix_stock_notes_user_id", table_name="stock_notes")
    op.drop_table("stock_notes")
