"""financials + treasury_shares 에 effective_date_precise BOOLEAN 컬럼 추가.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-29

ADR-0012 D6 (PIT 정밀화) — DART 응답 row 의 `rcept_no` (14자리 접수번호) 앞
8자리 (YYYYMMDD = 접수일자 = 공시일) 에서 직접 도출한 정밀 effective_date 를
구분하는 영구 컬럼. 구 `FetchResult.estimated_fields={"effective_date"}` marker
(in-memory only) 를 영속화한 것.

값 의미:
    - True  → effective_date 가 rcept_no 도출 실 공시일 (정밀).
    - False → 자본시장법 제160조 신고기한 보수값 fallback (도출 실패 시).
      기존 (본 migration 이전) row 도 보수값이므로 server_default false 로 호환.

NOT NULL DEFAULT false — 기존 row backfill 불필요 (전부 보수값이었음). 신규
fetch 는 adapter 가 rcept_no 유효 시 True 로 채움.

ADR-0020 append-only 조건부 트리거 갱신 (PG 전용):
    - financials / treasury_shares 의 BEFORE UPDATE 트리거 함수가 "superseded_by
      외 컬럼 불변" 을 검사 (Alembic 0007 / 0009). 신규 컬럼 effective_date_precise
      도 불변 대상에 포함되어야 stray UPDATE 가 차단됨 → 트리거 함수를
      CREATE OR REPLACE 로 재정의 (컬럼 목록에 effective_date_precise 추가).
      Alembic 0007 의 T54 Neutral 주석이 명시한 후속 작업.
    - SQLite (테스트) 는 plpgsql 미지원 → dialect 분기로 함수 재정의 생략.
      repository layer (`update_superseded_by`) 의 불변식 검증 (superseded_by
      ORM attribute 1 개만 set) 이 SQLite 방어선 (ADR-0020 D5).

관련:
- ADR-0012 D1 (보수 신고기한), D6 (rcept_no 직접 도출 — list.json 폐기)
- ADR-0020 D1 (조건부 트리거), Alembic 0007/0009 (트리거 패턴)
- docs/work-orders/m1-milestone.md T53/T54
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 신규 컬럼 추가 후 financials / treasury_shares 의 "superseded_by 외 컬럼 불변"
# 검사 대상 — effective_date_precise 포함 (Alembic 0007/0009 목록 + 신규 컬럼).
_FINANCIAL_IMMUTABLE_COLS: tuple[str, ...] = (
    "id", "code", "code_lineage_id", "effective_date", "fiscal_period",
    "account", "value", "unit", "ifrs_type", "effective_date_precise",
    "citation_id", "created_at",
)
_TREASURY_IMMUTABLE_COLS: tuple[str, ...] = (
    "id", "code", "code_lineage_id", "effective_date", "fiscal_period",
    "shares_treasury", "effective_date_precise", "citation_id", "created_at",
)
# 0007 / 0009 의 기존 (effective_date_precise 미포함) 목록 — downgrade 시 복원.
_FINANCIAL_IMMUTABLE_COLS_PRE: tuple[str, ...] = (
    "id", "code", "code_lineage_id", "effective_date", "fiscal_period",
    "account", "value", "unit", "ifrs_type", "citation_id", "created_at",
)
_TREASURY_IMMUTABLE_COLS_PRE: tuple[str, ...] = (
    "id", "code", "code_lineage_id", "effective_date", "fiscal_period",
    "shares_treasury", "citation_id", "created_at",
)


def _immutable_check_sql(cols: tuple[str, ...]) -> str:
    """비-superseded_by 컬럼이 하나라도 변경되면 True 인 boolean 식 (NULL-safe).

    Alembic 0007 의 동일 helper. `OLD.col IS DISTINCT FROM NEW.col` OR 연결.
    """
    return "\n                OR ".join(
        f"OLD.{c} IS DISTINCT FROM NEW.{c}" for c in cols
    )


def _make_update_fn_sql(fn_name: str, table: str, cols: tuple[str, ...]) -> str:
    """BEFORE UPDATE 조건부 트리거 함수 plpgsql 본문 (ADR-0020 D1 — 0007 동일)."""
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


# (table, 신규 immutable cols, 기존 immutable cols) — upgrade/downgrade 공용.
_TRIGGER_TABLES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("financials", _FINANCIAL_IMMUTABLE_COLS, _FINANCIAL_IMMUTABLE_COLS_PRE),
    ("treasury_shares", _TREASURY_IMMUTABLE_COLS, _TREASURY_IMMUTABLE_COLS_PRE),
)


def upgrade() -> None:
    # 1. 컬럼 추가 — NOT NULL DEFAULT false (기존 row 전부 보수값이므로 backfill 불요).
    op.add_column(
        "financials",
        sa.Column(
            "effective_date_precise",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "treasury_shares",
        sa.Column(
            "effective_date_precise",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # 2. PG append-only 트리거 함수 재정의 — 신규 컬럼을 불변 대상에 포함.
    #    SQLite 는 plpgsql 미지원 → 생략 (ADR-0020 D5 repository 방어선).
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, cols, _pre in _TRIGGER_TABLES:
        upd_fn = f"speculum_{table}_append_only_update"
        op.execute(_make_update_fn_sql(upd_fn, table, cols))


def downgrade() -> None:
    # 1. PG 트리거 함수 — effective_date_precise 미포함 목록으로 복원.
    if op.get_bind().dialect.name == "postgresql":
        for table, _cols, pre in _TRIGGER_TABLES:
            upd_fn = f"speculum_{table}_append_only_update"
            op.execute(_make_update_fn_sql(upd_fn, table, pre))

    # 2. 컬럼 제거.
    op.drop_column("treasury_shares", "effective_date_precise")
    op.drop_column("financials", "effective_date_precise")
