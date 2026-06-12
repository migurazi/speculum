"""M2 V-M2-4 — stocks_master.security_type CHECK constraint (ADR-0023 D2).

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-02

m2-conformance-review V-M2-4: security_type 허용값(common/preferred/etf/reit,
ADR-0023 D2)이 application layer(stocks_master.validate_security_type)에서만
강제됐다. 배치 직접 INSERT / 수동 SQL 이 미지원 값('bond' 등)을 넣으면 DB 가
수용하고 분포 partition(D5)이 그 종목을 새 모집단으로 분리하나 어디서도 거부
안 됨. DB CHECK constraint 로 defense-in-depth(application + DB 이중 강제).

PG/SQLite 양쪽 호환:
    - batch_alter_table + create_check_constraint. SQLite 는 batch(테이블 재생성)로
      CHECK 포함, PG 는 ALTER TABLE ADD CONSTRAINT.
    - **stocks_master 는 append-only trigger 대상 아님** — 0007 은 financials /
      corporate_actions 의 PostgreSQL BEFORE UPDATE 트리거만 건다. 따라서 SQLite
      테이블 재생성 부작용 0(trigger 손실 없음). index(ix_stocks_master_current_code
      / _market)는 alembic batch 가 reflect 로 보존.
    - 기존 row 는 0013 server_default 'common' 또는 유효 값 → CHECK 위반 0.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# CHECK constraint 명 — ORM __table_args__(stocks_master.py)와 동일.
_CHECK_NAME = "ck_stocks_master_security_type"
# ADR-0023 D2 허용 자산군 — stocks_master.py `_VALID_SECURITY_TYPES` 와 동일 집합.
# (application SoT 는 _VALID_SECURITY_TYPES, DB 는 동일 집합을 이중 강제 —
#  drift 시 신규 자산군 INSERT 가 CHECK 로 거부되므로 둘을 함께 갱신해야 한다.)
_ALLOWED: tuple[str, ...] = ("common", "preferred", "etf", "reit")


def upgrade() -> None:
    allowed_sql = ", ".join(f"'{v}'" for v in _ALLOWED)
    with op.batch_alter_table("stocks_master", schema=None) as batch_op:
        batch_op.create_check_constraint(
            _CHECK_NAME, f"security_type IN ({allowed_sql})",
        )


def downgrade() -> None:
    with op.batch_alter_table("stocks_master", schema=None) as batch_op:
        batch_op.drop_constraint(_CHECK_NAME, type_="check")
