"""M1 T49 — financials / corporate_actions append-only 조건부 트리거 (ADR-0020).

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-29

ADR-0020 D3 — financials / corporate_actions 에 PostgreSQL BEFORE UPDATE 조건부
트리거 + BEFORE DELETE 무조건 차단 트리거 추가.

허용되는 UPDATE (ADR-0020 D1):
    `superseded_by` 의 OLD=NULL AND NEW!=NULL 단일 전이 + 그 외 모든 컬럼 OLD
    동일. 그 외 (다른 컬럼 변경 / superseded_by 재변경 / NULL 되돌리기) 차단.
DELETE 는 무조건 차단 (historical 영구 보존 — adr-0009 D5).

source_citations 의 무조건 차단 트리거 (Alembic 0001) 와는 성격이 다름 —
citation 은 superseded_by 컬럼 자체가 없어 무조건 차단이 옳고, 본 두 테이블은
정정 chain (adr-0009 D5) 의 사후 superseded_by UPDATE 를 허용해야 하므로 조건부.
**0001 의 무조건 차단을 복사하지 말 것** (ADR-0020 D2).

"그 외 컬럼 불변" 비교는 테이블별로 컬럼 set 이 다르므로 **컬럼 명시 비교**로
구현 (m1-milestone.md T49 의 ROW(...) EXCEPT 는 의사코드). 각 비-superseded_by
컬럼을 `IS DISTINCT FROM` 으로 OLD/NEW 비교 — NULL-safe.

PostgreSQL-specific (운영 주의 — Alembic 0001 line 65-75 와 동일):
    - 본 트리거는 PG 전용. SQLite (테스트) 는 plpgsql 미지원 → dialect 분기로
      생략. SQLite 는 repository layer (`SqlFinancialRepository.
      update_superseded_by` 등) 의 불변식 검증이 유일 방어선 (ADR-0020 D5).
    - PG 운영용 SQL export 시 SPECULUM_DATABASE_URL 을 PG dialect 로 명시
      (트리거 누락 방지).

T54 (effective_date_precise BOOLEAN 컬럼 추가) 시 financials 트리거 함수의
"그 외 컬럼 불변" 비교에 신규 컬럼을 포함하도록 별도 migration 에서 함수 재정의
필요 (ADR-0020 Neutral).

관련:
- ADR-0020 D1/D3 (조건부 트리거), D2 (citation 과 구분)
- adr-0009 D5 (정정공시 supersede chain)
- adr-0002 D3/D5 (append-only), Alembic 0001 (citation 무조건 차단 — 성격 비교)
- docs/work-orders/m1-milestone.md Phase M1-0 T49
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# financials 의 superseded_by 외 컬럼 — "그 외 컬럼 불변" 검사 대상.
_FINANCIAL_IMMUTABLE_COLS: tuple[str, ...] = (
    "id", "code", "code_lineage_id", "effective_date", "fiscal_period",
    "account", "value", "unit", "ifrs_type", "citation_id", "created_at",
)
# corporate_actions 의 superseded_by 외 컬럼.
_CA_IMMUTABLE_COLS: tuple[str, ...] = (
    "id", "code", "code_lineage_id", "action_type", "announced_date",
    "effective_date", "payment_date", "ratio", "cash_amount", "details",
    "citation_id", "created_at",
)


def _immutable_check_sql(cols: tuple[str, ...]) -> str:
    """비-superseded_by 컬럼이 하나라도 변경되면 True 인 boolean 식 생성.

    각 컬럼을 `OLD.col IS DISTINCT FROM NEW.col` (NULL-safe) 로 비교 후 OR.
    """
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


_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("financials", _FINANCIAL_IMMUTABLE_COLS),
    ("corporate_actions", _CA_IMMUTABLE_COLS),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        # SQLite (테스트) — repository layer 검증이 방어선 (ADR-0020 D5).
        return

    for table, cols in _TABLES:
        upd_fn = f"speculum_{table}_append_only_update"
        del_fn = f"speculum_{table}_append_only_delete"
        op.execute(_make_update_fn_sql(upd_fn, table, cols))
        op.execute(_make_delete_fn_sql(del_fn, table))
        op.execute(
            f"""
            CREATE TRIGGER speculum_{table}_append_only_update_trg
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION {upd_fn}();
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER speculum_{table}_append_only_delete_trg
            BEFORE DELETE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION {del_fn}();
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table, _cols in _TABLES:
        op.execute(
            f"DROP TRIGGER IF EXISTS "
            f"speculum_{table}_append_only_update_trg ON {table}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS "
            f"speculum_{table}_append_only_delete_trg ON {table}"
        )
        op.execute(
            f"DROP FUNCTION IF EXISTS speculum_{table}_append_only_update()"
        )
        op.execute(
            f"DROP FUNCTION IF EXISTS speculum_{table}_append_only_delete()"
        )
