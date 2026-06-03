"""treasury_shares 테이블 + ADR-0020 append-only 조건부 트리거.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-29

DART `stockTotqySttus.json` (주식의 총수 현황) 의 보통주 자기주식수를 fiscal_period
단위로 영구화하는 table. DbFieldProvider 의 `shares_treasury` 해소 → factor
`market-cap:ex-treasury` 실평가를 가능케 함.

financials (Alembic 0001) 패턴 미러:
    - id surrogate UUID PK + 정정 chain (superseded_by self-FK nullable).
    - citation_id FK → source_citations.id (Fidelity).
    - (code, effective_date) PIT scan + superseded_by chain + (code_lineage_id,
      effective_date) lineage hot path 인덱스.

ADR-0020 append-only 조건부 트리거 (Alembic 0007 _TABLES 패턴):
    - financials / corporate_actions 와 동일하게 superseded_by NULL→set 단일
      전이만 허용하는 BEFORE UPDATE 조건부 트리거 + BEFORE DELETE 무조건 차단.
    - PostgreSQL 전용 (plpgsql). SQLite (테스트) 는 dialect 분기로 생략 —
      `SqlTreasurySharesRepository.update_superseded_by` 의 불변식 검증이 유일
      방어선 (ADR-0020 D5).

**0007 의 무조건 차단 (source_citations) 을 복사하지 말 것** — treasury_shares 는
정정 chain (ADR-0009 D5) 의 사후 superseded_by UPDATE 를 허용해야 하므로 조건부
(ADR-0020 D1/D2).

관련:
- ADR-0020 D1/D3 (조건부 트리거), D2 (citation 무조건 차단과 구분)
- ADR-0009 D5 (정정공시 supersede chain)
- ADR-0002 D3 (Source Citation persistence)
- Alembic 0001 (financials — 본 migration 의 미러 대상), 0007 (조건부 트리거 패턴)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# treasury_shares 의 superseded_by 외 컬럼 — "그 외 컬럼 불변" 검사 대상
# (Alembic 0007 의 _FINANCIAL_IMMUTABLE_COLS 패턴).
_TREASURY_IMMUTABLE_COLS: tuple[str, ...] = (
    "id", "code", "code_lineage_id", "effective_date", "fiscal_period",
    "shares_treasury", "citation_id", "created_at",
)


def _immutable_check_sql(cols: tuple[str, ...]) -> str:
    """비-superseded_by 컬럼이 하나라도 변경되면 True 인 boolean 식 (NULL-safe)."""
    return "\n                OR ".join(
        f"OLD.{c} IS DISTINCT FROM NEW.{c}" for c in cols
    )


def _make_update_fn_sql(fn_name: str, table: str, cols: tuple[str, ...]) -> str:
    """BEFORE UPDATE 조건부 트리거 함수 plpgsql 본문 (ADR-0020 D1)."""
    immutable_changed = _immutable_check_sql(cols)
    return f"""
        CREATE OR REPLACE FUNCTION {fn_name}()
        RETURNS TRIGGER AS $$
        BEGIN
            -- 허용: superseded_by NULL→non-NULL 단일 전이 + 그 외 컬럼 불변.
            IF OLD.superseded_by IS NULL
               AND NEW.superseded_by IS NOT NULL
               AND NOT (
                {immutable_changed}
               )
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                '{table} is append-only: superseded_by NULL->set 단일 전이 외 '
                'UPDATE 금지 (ADR-0020 D1)';
        END;
        $$ LANGUAGE plpgsql;
    """


def _make_delete_fn_sql(fn_name: str, table: str) -> str:
    """BEFORE DELETE 무조건 차단 함수 plpgsql 본문 (ADR-0020 D1)."""
    return f"""
        CREATE OR REPLACE FUNCTION {fn_name}()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                '{table} is append-only: DELETE 금지 (ADR-0020 D1)';
        END;
        $$ LANGUAGE plpgsql;
    """


_TABLE: str = "treasury_shares"


def upgrade() -> None:
    op.create_table(
        "treasury_shares",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("code_lineage_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=16), nullable=False),
        sa.Column("shares_treasury", sa.BigInteger(), nullable=False),
        sa.Column("citation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("superseded_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["citation_id"],
            ["source_citations.id"],
            name="fk_treasury_shares_citation_id_source_citations",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["treasury_shares.id"],
            name="fk_treasury_shares_superseded_by_treasury_shares",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_treasury_shares"),
    )
    op.create_index(
        "ix_treasury_shares_code_date",
        "treasury_shares",
        ["code", "effective_date"],
    )
    op.create_index(
        "ix_treasury_shares_superseded_by",
        "treasury_shares",
        ["superseded_by"],
    )
    op.create_index(
        "ix_treasury_shares_lineage_date",
        "treasury_shares",
        ["code_lineage_id", "effective_date"],
    )

    # ADR-0020 조건부 append-only 트리거 — PG 전용. SQLite 는 repository layer
    # 검증이 방어선 (ADR-0020 D5).
    if op.get_bind().dialect.name != "postgresql":
        return

    upd_fn = f"speculum_{_TABLE}_append_only_update"
    del_fn = f"speculum_{_TABLE}_append_only_delete"
    op.execute(_make_update_fn_sql(upd_fn, _TABLE, _TREASURY_IMMUTABLE_COLS))
    op.execute(_make_delete_fn_sql(del_fn, _TABLE))
    op.execute(
        f"""
        CREATE TRIGGER speculum_{_TABLE}_append_only_update_trg
        BEFORE UPDATE ON {_TABLE}
        FOR EACH ROW
        EXECUTE FUNCTION {upd_fn}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER speculum_{_TABLE}_append_only_delete_trg
        BEFORE DELETE ON {_TABLE}
        FOR EACH ROW
        EXECUTE FUNCTION {del_fn}();
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"DROP TRIGGER IF EXISTS "
            f"speculum_{_TABLE}_append_only_update_trg ON {_TABLE}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS "
            f"speculum_{_TABLE}_append_only_delete_trg ON {_TABLE}"
        )
        op.execute(
            f"DROP FUNCTION IF EXISTS speculum_{_TABLE}_append_only_update()"
        )
        op.execute(
            f"DROP FUNCTION IF EXISTS speculum_{_TABLE}_append_only_delete()"
        )

    op.drop_index(
        "ix_treasury_shares_lineage_date", table_name="treasury_shares",
    )
    op.drop_index(
        "ix_treasury_shares_superseded_by", table_name="treasury_shares",
    )
    op.drop_index(
        "ix_treasury_shares_code_date", table_name="treasury_shares",
    )
    op.drop_table("treasury_shares")
