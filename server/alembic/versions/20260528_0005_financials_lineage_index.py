"""W4 — financials.(code_lineage_id, effective_date) 복합 인덱스.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-28

Momus M0 review rev2 W4 (Low) fix. financials 테이블의 lineage 단위 historical
scan hot path 의 인덱스 부재 해소. prices_daily 의 `ix_prices_daily_lineage_
date` 와 일관성.

상황:
- prices_daily 는 (code, effective_date) PK + (code_lineage_id, effective_date)
  복합 인덱스 모두 보유 (Alembic 0001).
- financials 는 (code, account, effective_date) 인덱스만 보유 — lineage 단위
  scan 의 hot path 부재.
- 종목코드 변경 (재상장 / 합병 후 신규 코드) lineage 의 historical financials
  fetch 시 PostgreSQL sequential scan.

M0 운영 규모 (~5000 row) 영향 미미하나 prices_daily 와의 schema 일관성 + M1+
universe 확장 (~250 → ~2500 종목, ~50,000 row) 시 hot path 보강.

관련:
- Momus M0 review rev2 W4
- ADR-0009 D6 (code_lineage_id 의미)
- prices_daily `ix_prices_daily_lineage_date` (Alembic 0001 line 166-170)
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_financials_lineage_date",
        "financials",
        ["code_lineage_id", "effective_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_financials_lineage_date", table_name="financials")
