"""financials.account 컬럼 폭 확대 (String(128) → String(255)).

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-18

DART fnlttSinglAcntAll 의 **미매핑 보존 계정**("unmapped:ifrs-full_<원문>") 이 IFRS
전체 taxonomy 명을 담아 128 자를 초과(삼성 2024Q1 CFS 최장 154 자:
`unmapped:ifrs-full_ShareOfOtherComprehensiveIncomeOfAssociatesAndJointVentures
AccountedForUsingEquityMethodThatWillNotBeReclassifiedToProfitOrLossNetOfTax`).
PostgreSQL 은 VARCHAR(128) 을 강제 → 실 적재 시 StringDataRightTruncation 으로
전체 회사 실패. 매핑 canonical(≤~33)은 무영향. → 컬럼을 String(255) 로 확대.

dialect 분기(load-bearing):
    - **PostgreSQL**: `op.alter_column` **in-place** TYPE 변경 — 테이블 재생성 없음
      → financials append-only 조건부 트리거(migration 0007) 보존.
    - **SQLite**: VARCHAR 길이를 **강제하지 않음**(동적 타이핑) → 폭 변경 불요 =
      **no-op**. batch_alter_table 로 테이블을 재생성하면 0007 트리거가 유실되므로
      (financials 는 append-only 트리거 대상 — custom_packs 류와 다름) SQLite 에서는
      의도적으로 건드리지 않는다. 단위 테스트(test_alembic_chain_sqlite)는 길이
      미강제라 영향 0.

기존 데이터 무변경(폭 확대만, 값 보존). downgrade 는 PG 한정 축소(데이터 >128 시
실패 가능 — dev 한정, 운영 데이터엔 적용 금지 권장).

관련: ADR-0002(account 데이터 모델), ADR-0020/migration 0007(append-only 트리거),
dart_adapter._parse_response(미매핑 보존), M9 후속 DART 실적재 hardening.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PG 만 in-place TYPE 변경(트리거 보존). SQLite 는 길이 미강제 → no-op.
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "financials",
            "account",
            existing_type=sa.String(128),
            type_=sa.String(255),
            existing_nullable=False,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "financials",
            "account",
            existing_type=sa.String(255),
            type_=sa.String(128),
            existing_nullable=False,
        )
