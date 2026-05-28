"""T13 Phase C — screen_runs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-28

ADR-0008 D7 의 `screen_runs` schema — 8 기둥 §2.10 Reproducibility 의 정점.
Run = query + as_of + result_codes + data_versions + result_hash 의 완전 freeze.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "screen_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "query",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column(
            "result_codes",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("result_hash", sa.String(length=80), nullable=False),
        sa.Column(
            "data_versions",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_screen_runs"),
    )
    op.create_index(
        "ix_screen_runs_user_computed",
        "screen_runs", ["user_id", "computed_at"],
    )
    op.create_index(
        "ix_screen_runs_result_hash", "screen_runs", ["result_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_screen_runs_result_hash", table_name="screen_runs")
    op.drop_index("ix_screen_runs_user_computed", table_name="screen_runs")
    op.drop_table("screen_runs")
