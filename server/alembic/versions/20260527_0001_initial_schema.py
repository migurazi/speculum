"""T13 initial schema — ADR-0002 D5 + ADR-0009 D6 의 5 core 테이블.

Revision ID: 0001
Revises:
Create Date: 2026-05-27

본 migration 의 산출 테이블:
    1. source_citations — ADR-0002 D3 7-tuple (append-only)
    2. stocks_master — ADR-0009 D6 lineage entity
    3. prices_daily — ADR-0001 raw + adjusted 가격
    4. financials — ADR-0002 D5 + ADR-0009 D5 정정공시 chain
    5. corporate_actions — ADR-0009 D1 이중 PIT entity

PostgreSQL-specific:
    - source_citations BEFORE UPDATE trigger — append-only 강제 (ADR-0002 D5 line 190).
      SQLite (test 환경) 는 trigger 생략 (dialect 분기).

User-side tables (users / watchlists / watchlist_items / screener_sets / screen_runs)
는 T13 Phase B 또는 별도 사이클. 본 migration 은 data layer foundation 만.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------------
    # 1. source_citations — ADR-0002 D3 의 7-tuple (append-only).
    # ------------------------------------------------------------------------
    op.create_table(
        "source_citations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("identifier", sa.String(length=500), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_source_citations"),
    )
    op.create_index(
        "ix_source_citations_batch_id",
        "source_citations",
        ["batch_id"],
    )
    op.create_index(
        "ix_source_citations_effective_date",
        "source_citations",
        ["effective_date"],
    )

    # PostgreSQL-only append-only trigger — ADR-0002 D5 line 190 의 "UPDATE 금지".
    # SQLite 는 dialect 분기로 생략 (Repository layer 의 id 중복 검사가 부분 방어).
    #
    # **운영 주의 (oracle 리뷰 C4)**: offline mode (`alembic upgrade head --sql`)
    # 에서는 `op.get_bind()` 가 MockConnection 을 반환하지만 dialect.name 은
    # SPECULUM_DATABASE_URL (또는 alembic.ini sqlalchemy.url) 에서 결정. 따라서
    # **PG 운영용 SQL export 시 반드시 SPECULUM_DATABASE_URL 을 PG dialect 로
    # 명시**:
    #     SPECULUM_DATABASE_URL="postgresql+psycopg://..." alembic upgrade head --sql
    # SQLite URL 로 export 한 SQL 을 PG 에 적용하면 trigger 누락 → ADR-0002 D3
    # invariant 결손. CI 가 dialect mismatch 검출 필요 (M1 backlog).
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION speculum_source_citations_no_update()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION
                    'source_citations is append-only (ADR-0002 D3/D5)';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER speculum_source_citations_no_update_trg
            BEFORE UPDATE ON source_citations
            FOR EACH ROW
            EXECUTE FUNCTION speculum_source_citations_no_update();
            """
        )

    # ------------------------------------------------------------------------
    # 2. stocks_master — ADR-0009 D6 lineage entity.
    # ------------------------------------------------------------------------
    op.create_table(
        "stocks_master",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("current_code", sa.String(length=12), nullable=True),
        sa.Column("current_name", sa.String(length=200), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("listing_date", sa.Date(), nullable=False),
        sa.Column("delisting_date", sa.Date(), nullable=True),
        sa.Column("fiscal_month", sa.Integer(), nullable=False),
        # PostgreSQL = JSONB, SQLite = JSON. with_variant 으로 자동 분기.
        sa.Column(
            "code_history",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "ifrs_preference_default",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'AUTO'"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stocks_master"),
    )
    op.create_index(
        "ix_stocks_master_current_code",
        "stocks_master",
        ["current_code"],
    )
    op.create_index(
        "ix_stocks_master_market",
        "stocks_master",
        ["market"],
    )

    # ------------------------------------------------------------------------
    # 3. prices_daily — ADR-0001.
    # ------------------------------------------------------------------------
    op.create_table(
        "prices_daily",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("code_lineage_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("open_raw", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("high_raw", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("low_raw", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("close_raw", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column(
            "close_adjusted",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.Column("citation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["citation_id"],
            ["source_citations.id"],
            name="fk_prices_daily_citation_id_source_citations",
        ),
        sa.PrimaryKeyConstraint(
            "code", "effective_date", name="pk_prices_daily",
        ),
        # oracle 리뷰 C2 — id surrogate UUID 의 PITRecord 호환 보강.
        sa.UniqueConstraint("id", name="uq_prices_daily_id"),
    )
    op.create_index(
        "ix_prices_daily_lineage_date",
        "prices_daily",
        ["code_lineage_id", "effective_date"],
    )

    # ------------------------------------------------------------------------
    # 4. financials — ADR-0002 D5 + ADR-0009 D5.
    # ------------------------------------------------------------------------
    op.create_table(
        "financials",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("code_lineage_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=16), nullable=False),
        sa.Column("account", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Numeric(precision=38, scale=4), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("ifrs_type", sa.String(length=16), nullable=False),
        sa.Column("citation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("superseded_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["citation_id"],
            ["source_citations.id"],
            name="fk_financials_citation_id_source_citations",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["financials.id"],
            name="fk_financials_superseded_by_financials",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_financials"),
    )
    op.create_index(
        "ix_financials_code_account_date",
        "financials",
        ["code", "account", "effective_date"],
    )
    op.create_index(
        "ix_financials_superseded_by",
        "financials",
        ["superseded_by"],
    )

    # ------------------------------------------------------------------------
    # 5. corporate_actions — ADR-0009 D1.
    # ------------------------------------------------------------------------
    op.create_table(
        "corporate_actions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("code_lineage_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("announced_date", sa.Date(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("ratio", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column(
            "cash_amount", sa.Numeric(precision=20, scale=8), nullable=True,
        ),
        sa.Column(
            "details",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("citation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("superseded_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["citation_id"],
            ["source_citations.id"],
            name="fk_corporate_actions_citation_id_source_citations",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["corporate_actions.id"],
            name="fk_corporate_actions_superseded_by_corporate_actions",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_corporate_actions"),
    )
    op.create_index(
        "ix_corporate_actions_code_announced",
        "corporate_actions",
        ["code", "announced_date"],
    )
    op.create_index(
        "ix_corporate_actions_code_effective",
        "corporate_actions",
        ["code", "effective_date"],
    )
    op.create_index(
        "ix_corporate_actions_superseded_by",
        "corporate_actions",
        ["superseded_by"],
    )


def downgrade() -> None:
    # FK dependency 역순.
    op.drop_index(
        "ix_corporate_actions_superseded_by", table_name="corporate_actions",
    )
    op.drop_index(
        "ix_corporate_actions_code_effective", table_name="corporate_actions",
    )
    op.drop_index(
        "ix_corporate_actions_code_announced", table_name="corporate_actions",
    )
    op.drop_table("corporate_actions")

    op.drop_index("ix_financials_superseded_by", table_name="financials")
    op.drop_index("ix_financials_code_account_date", table_name="financials")
    op.drop_table("financials")

    op.drop_index("ix_prices_daily_lineage_date", table_name="prices_daily")
    op.drop_table("prices_daily")

    op.drop_index("ix_stocks_master_market", table_name="stocks_master")
    op.drop_index("ix_stocks_master_current_code", table_name="stocks_master")
    op.drop_table("stocks_master")

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "speculum_source_citations_no_update_trg ON source_citations"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS speculum_source_citations_no_update()"
        )

    op.drop_index(
        "ix_source_citations_effective_date", table_name="source_citations",
    )
    op.drop_index(
        "ix_source_citations_batch_id", table_name="source_citations",
    )
    op.drop_table("source_citations")
