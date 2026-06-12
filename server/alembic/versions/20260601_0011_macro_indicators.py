"""macro_indicators 테이블 + append-only 무조건 차단 트리거 (M1 T62).

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-01

ECOS 거시지표의 vintage 이중 시간축(reference_date × vintage_date) 스키마 선설계.
한국은행 ECOS 는 사후 개정 구조 — 잠정치 먼저 공표 후 확정치로 같은 기간 값을
갱신. PIT-정합(재현성 P0)을 위해 두 번째 시간축 vintage_date 를 도입.

같은 (indicator_id, reference_date) 에 대해 vintage_date 가 다른 row 가
append-only 로 누적. PIT 조회 규약: vintage_date <= as_of 중 각 reference_date 별
max(vintage_date) 의 value 를 선택 (look-ahead 0). 실제 Repository 구현은 T64.

**financials / treasury_shares 의 조건부 트리거와 다른 이유**:
    macro_indicators 는 superseded_by 컬럼이 없으므로 "superseded_by NULL→set
    허용" 조건부 UPDATE 가 아닌 **모든 UPDATE 를 무조건 차단**한다. DELETE 도
    무조건 차단(PIT 영구 보존). 이는 source_citations 의 무조건 차단 트리거
    (Alembic 0001) 와 같은 수준의 엄격함이다.

인덱스:
    - UNIQUE(indicator_id, reference_date, vintage_date) — 중복 insert 차단(idempotent).
    - (indicator_id, vintage_date, reference_date) — PIT 조회 hot path.
    - (indicator_id, reference_date) — 시계열 범위 scan hot path.

관련:
    - ADR-0003 D2 (MacroIndicator canonical schema + vintage 이중 시간축)
    - `app/repositories/pit_protocols.py` MacroIndicatorRecord
    - `app/db/orm/macro_indicators.py` MacroIndicatorORM
    - Alembic 0001 (source_citations 무조건 차단 트리거 — 같은 수준 적용)
    - Alembic 0007/0009 (financials/treasury 조건부 트리거 — 성격 비교)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE: str = "macro_indicators"


def _make_update_block_fn_sql(fn_name: str, table: str) -> str:
    """BEFORE UPDATE 무조건 차단 트리거 함수 plpgsql 본문.

    macro_indicators 는 superseded_by 가 없으므로 어떤 UPDATE 도 허용하지 않음.
    개정 = 새 vintage_date 를 가진 새 row 의 INSERT (순수 append-only).
    """
    return f"""
        CREATE OR REPLACE FUNCTION {fn_name}()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                '{table} is append-only: 모든 UPDATE 금지.'
                ' 개정은 새 vintage_date 를 가진 새 row INSERT 로 표현 (T62 ADR-0003)';
        END;
        $$ LANGUAGE plpgsql;
    """


def _make_delete_block_fn_sql(fn_name: str, table: str) -> str:
    """BEFORE DELETE 무조건 차단 트리거 함수 plpgsql 본문."""
    return f"""
        CREATE OR REPLACE FUNCTION {fn_name}()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                '{table} is append-only: DELETE 금지 (PIT 영구 보존 — T62 ADR-0003)';
        END;
        $$ LANGUAGE plpgsql;
    """


def upgrade() -> None:
    op.create_table(
        "macro_indicators",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("indicator_id", sa.String(length=64), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=28, scale=8), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("vintage_date", sa.Date(), nullable=False),
        sa.Column("citation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["citation_id"],
            ["source_citations.id"],
            name="fk_macro_indicators_citation_id_source_citations",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_macro_indicators"),
        # 자연키 중복 차단 — idempotent batch insert 보장.
        sa.UniqueConstraint(
            "indicator_id",
            "reference_date",
            "vintage_date",
            name="uq_macro_indicators_indicator_ref_vintage",
        ),
    )
    # PIT 조회 hot path: indicator_id 로 필터 후 vintage_date<=as_of max 탐색.
    op.create_index(
        "ix_macro_indicators_indicator_vintage_ref",
        "macro_indicators",
        ["indicator_id", "vintage_date", "reference_date"],
    )
    # 시계열 범위 scan hot path (T64 표시용).
    op.create_index(
        "ix_macro_indicators_indicator_ref",
        "macro_indicators",
        ["indicator_id", "reference_date"],
    )

    # append-only 무조건 차단 트리거 — PG 전용. SQLite 는 dialect 분기로 생략.
    # macro_indicators 는 superseded_by 없음 → 모든 UPDATE 차단 (source_citations
    # 무조건 차단, Alembic 0001 과 동일 수준).
    if op.get_bind().dialect.name != "postgresql":
        return

    upd_fn = f"speculum_{_TABLE}_append_only_update"
    del_fn = f"speculum_{_TABLE}_append_only_delete"
    op.execute(_make_update_block_fn_sql(upd_fn, _TABLE))
    op.execute(_make_delete_block_fn_sql(del_fn, _TABLE))
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
        "ix_macro_indicators_indicator_ref",
        table_name="macro_indicators",
    )
    op.drop_index(
        "ix_macro_indicators_indicator_vintage_ref",
        table_name="macro_indicators",
    )
    op.drop_table("macro_indicators")
