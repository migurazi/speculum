"""M2 ADR-0023 D2 — stocks_master.security_type 자산군 분류 컬럼 추가.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-01

자산군 분류 (보통주/우선주/ETF/리츠) 를 lineage entity stocks_master 에 신설.

설계 결정 (ADR-0023 D2):
    - security_type: String(16) NOT NULL server_default 'common'.
      lineage 의 정체(identity) — PIT 시계열 불변(immutable).
      우선주→보통주 전환은 발행주식수 감소 + delisting_date 로 처리하며
      security_type 변경이 아님 (ADR-0009 D10).
    - 기존 전 종목 backfill: server_default='common' 으로 기존 row 및 신규 row
      모두 자동 채워짐 (KOSPI/KOSDAQ 현행 universe = 보통주).
    - valid_from / valid_to 없음: 분류는 lineage 생애 동안 불변이므로 단일 컬럼.
    - 분포 partition (T78 D5) / screener 필터 (T79 D7) 는 별도 사이클 — 여기서
      구현하지 않음.

PG/SQLite 양쪽 호환:
    - `add_column` 은 두 dialect 모두 지원. 트리거(ADR-0020 append-only) 와
      충돌 없음 — 컬럼 추가만이라 기존 BEFORE UPDATE trigger 미영향.
    - SQLite 에서 NOT NULL + server_default 는 기존 row 를 server_default 로
      backfill 한 후 추가 (SQLite ADD COLUMN 의 표준 동작).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # stocks_master 에 security_type 컬럼 추가.
    # server_default='common' — 기존 row 및 migration 이전 INSERT 모두 'common' 으로
    # 채워져 backfill 별도 UPDATE 불요. 신규 INSERT 시 값 미지정이면 자동 'common'.
    # NOT NULL 강제 — 허용값: common | preferred | etf | reit (ADR-0023 D2).
    with op.batch_alter_table("stocks_master", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "security_type",
                sa.String(16),
                nullable=False,
                server_default="common",
            )
        )


def downgrade() -> None:
    # security_type 컬럼 제거.
    with op.batch_alter_table("stocks_master", schema=None) as batch_op:
        batch_op.drop_column("security_type")
