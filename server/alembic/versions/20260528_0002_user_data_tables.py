"""T13 Phase B — watchlists / watchlist_items / screener_sets.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-28

ADR-0011 D2 의 사용자 데이터 entity:
    - watchlists (폴더, parent_id self-FK + depth ≤ 2 운영 정책)
    - watchlist_items (folder 의 종목 entry, UNIQUE(folder, lineage))
    - screener_sets (조건셋, JSON conditions + selected_factors)

users 테이블은 본 migration scope 밖 — M0 single-user + NextAuth 합류 시
별도 cycle (T13 Phase C).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------------
    # 1. watchlists — folder entity (parent_id self-FK).
    # ------------------------------------------------------------------------
    op.create_table(
        "watchlists",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("parent_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["watchlists.id"],
            name="fk_watchlists_parent_id_watchlists",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_watchlists"),
    )
    op.create_index(
        "ix_watchlists_user_id", "watchlists", ["user_id"],
    )
    op.create_index(
        "ix_watchlists_parent_id", "watchlists", ["parent_id"],
    )

    # ------------------------------------------------------------------------
    # 2. watchlist_items — folder 의 종목 entry.
    # ------------------------------------------------------------------------
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("watchlist_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code_lineage_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("note", sa.String(length=280), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["watchlist_id"], ["watchlists.id"],
            ondelete="CASCADE",
            name="fk_watchlist_items_watchlist_id_watchlists",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_watchlist_items"),
        sa.UniqueConstraint(
            "watchlist_id", "code_lineage_id",
            name="uq_watchlist_items_folder_lineage",
        ),
    )
    op.create_index(
        "ix_watchlist_items_watchlist_id",
        "watchlist_items", ["watchlist_id"],
    )

    # ------------------------------------------------------------------------
    # 3. screener_sets — 조건셋 entity.
    # ------------------------------------------------------------------------
    op.create_table(
        "screener_sets",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "conditions",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "selected_factors",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_screener_sets"),
    )
    op.create_index(
        "ix_screener_sets_user_id", "screener_sets", ["user_id"],
    )
    op.create_index(
        "ix_screener_sets_user_updated",
        "screener_sets", ["user_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_screener_sets_user_updated", table_name="screener_sets",
    )
    op.drop_index("ix_screener_sets_user_id", table_name="screener_sets")
    op.drop_table("screener_sets")

    op.drop_index(
        "ix_watchlist_items_watchlist_id", table_name="watchlist_items",
    )
    op.drop_table("watchlist_items")

    op.drop_index("ix_watchlists_parent_id", table_name="watchlists")
    op.drop_index("ix_watchlists_user_id", table_name="watchlists")
    op.drop_table("watchlists")
