"""T46 V3 — prices_daily.trading_value 컬럼 추가.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-28

Momus M0 review V3 (High) fix. factor pack `volume-turnover:avg-20d` 의 입력
`trading_value_20d_avg` (20 영업일 거래대금 평균) 산출 path 의 schema 준비.

- 컬럼 추가: `trading_value` Numeric(28, 4) NOT NULL — 한국 주식 일거래대금
  max ~수조원. `server_default=0` 으로 기존 row 0 backfill (M0 dev 환경은
  빈 DB 라 backfill 무관). ORM 의 nullable=False 와 일관.
- 운영 PG 의 production backfill 정책 (실 trading_value 산출) 은 M1 cycle.

FieldProvider wiring (factor_evaluator 합류 + DB read path) 은 M1 backlog.
현 cycle 은:
- PriceRecord schema + ORM + converters + KRX batch 영구화 완성.
- 단 sql_repositories / repository test 갱신 + 신규 마이그레이션.

관련:
- Momus M0 review V3
- factor pack `volume-turnover:avg-20d` (server/builtin-packs/factors/
  speculum-builtin-v1.0.0.json line 304)
- ADR-0001 D4 (raw + adjusted) — 거래대금 raw 만 (volume 보정 미구현과 일관)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default=0 — 기존 row 가 있을 때 NOT NULL 위반 회피. M0 dev 환경은
    # 빈 DB 라 영향 없음. 운영 PG 의 실 backfill (KRX 거래대금 산출) 은 별도
    # 운영 script (M1 cycle). add_column 은 server_default 동반이라 SQLite 에서도
    # 직접 실행 가능 (배치 불요).
    op.add_column(
        "prices_daily",
        sa.Column(
            "trading_value",
            sa.Numeric(precision=28, scale=4),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # server_default 는 schema 준비만 — 새 row insert 시 명시적 값 (ORM 의
    # nullable=False 강제) 의무. 운영 invariant 위반 silent 회피 위해 default
    # 제거.
    #
    # SQLite 는 `ALTER TABLE ... ALTER COLUMN ... DROP DEFAULT` 를 지원하지 않아
    # (SQLite 는 ALTER COLUMN 자체가 없음) 이 한 줄이 `alembic upgrade head` 를
    # SQLite 에서 깨뜨렸다 (test_market_caps_migration 의 "0004 가 chain SQLite
    # 실행 불가 원인" 주석 참조). batch_alter_table 로 감싸면 SQLite 는 테이블을
    # 재생성 (copy → drop → rename) 하여 default 없는 스키마를 만들고, PG/기타
    # dialect 는 batch 가 직접 ALTER 를 발행 (recreate="auto" → 비-SQLite 는
    # 테이블 복제 없이 그대로) 하므로 동작이 동일하다. README 의 sqlite:///
    # 운영 경로 (정식 데이터 적재) 가 alembic 으로 프로비저닝 가능해진다.
    with op.batch_alter_table("prices_daily") as batch_op:
        batch_op.alter_column("trading_value", server_default=None)


def downgrade() -> None:
    # drop_column 도 구버전 SQLite (<3.35) 는 미지원 → batch 로 안전 처리
    # (신버전 SQLite·PG 는 직접 DROP). upgrade 와 대칭.
    with op.batch_alter_table("prices_daily") as batch_op:
        batch_op.drop_column("trading_value")
