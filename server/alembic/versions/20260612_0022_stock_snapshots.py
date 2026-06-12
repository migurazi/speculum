"""ADR-0002 D5 — stock_snapshots 테이블 (factor 결과 precomputed).

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-12

ⓓ slice 1 — screen/market_overview 의 종목별 factor 실시간 재계산(N+1)을 일배치
precompute lookup 으로 대체하기 위한 영속 layer. 본 migration 은 **schema(빈 테이블)
만** 생성 — write 배치·read 경로 배선(§2.10 reproduce bypass)은 후속 cycle.

설계 결정 (app/db/orm/stock_snapshots.py StockSnapshotORM):
    - PK = (stock_code, as_of_date, factor_uuid): `as_of_date == as_of` exact
      lookup 키 + 같은 (code, 일자, factor) 중복 insert 차단. 정정 후 재계산
      overwrite 는 호출자(일배치) 책임(save_prices/save_financials INSERT-only 동일).
    - id: 합성 uuid5(code|as_of|factor) — PITRecord protocol 단일 키. composite PK
      와 별개로 UNIQUE("id") 강제(market_caps 패턴, silent overwrite 차단). id 가
      PK 컬럼들의 deterministic 함수라 PK 와 1:1.
    - value: String(nullable) — factor 값 scale 이 제각각이라 고정 Numeric 대신
      Decimal-as-str 직렬화(코드베이스 wire 관례, §2.10 byte-동일 round-trip).
      NULL = 미산정(분모 0 등).
    - inputs: JSON(SQLite)/JSONB(PG) — 산정 입력값(Fidelity §2.1). custom_packs
      (0017) 와 동일 variant 패턴.
    - citation_id FK → source_citations.id: 결과 대표 citation(Fidelity).

append-only 무관:
    snapshot 은 derived cache — supersede 컬럼 없음. ADR-0020 조건부 트리거
    (financials/corporate_actions) 대상 아님. 기존 테이블 무영향(신규 테이블만 추가).

insert 0 (빈 테이블):
    본 migration 은 schema 만. snapshot row 생성은 후속 cycle 의 precompute 배치뿐.

관련: ADR-0002 D5, app/db/orm/stock_snapshots.py, StockSnapshotRecord(pit_protocols.py).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # stock_snapshots — factor 결과 precomputed snapshot(빈 테이블).
    op.create_table(
        "stock_snapshots",
        # 합성 id — uuid5(code|as_of|factor). PITRecord protocol 단일 키.
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("stock_code", sa.String(12), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("factor_uuid", sa.Uuid(as_uuid=True), nullable=False),
        # factor 값 — Decimal 을 str 로(정밀도 보존). NULL = 미산정.
        sa.Column("value", sa.String(), nullable=True),
        sa.Column("value_unit", sa.String(32), nullable=False),
        # 산정 입력값 JSON. PG=JSONB, SQLite=JSON(ORM 의 with_variant 일치).
        sa.Column(
            "inputs",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("citation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        # 산정 당시 freeze fingerprint — screen_runs.data_versions 와 대칭. 후속
        # read-bypass 의 serve-vs-recompute 결정 축(§2.10, oracle C1). PG=JSONB.
        sa.Column(
            "data_versions",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        # exact lookup PK + 중복 insert 차단.
        sa.PrimaryKeyConstraint(
            "stock_code", "as_of_date", "factor_uuid", name="pk_stock_snapshots",
        ),
        # 합성 id surrogate UNIQUE — PITRecord 단일 키 + silent overwrite 차단.
        sa.UniqueConstraint("id", name="uq_stock_snapshots_id"),
        # 결과 대표 citation FK(Fidelity).
        sa.ForeignKeyConstraint(
            ["citation_id"],
            ["source_citations.id"],
            name="fk_stock_snapshots_citation_id_source_citations",
        ),
    )
    # fetch_snapshots_for_code((code, as_of) 여러 factor) hot path.
    op.create_index(
        "ix_stock_snapshots_code_asof",
        "stock_snapshots",
        ["stock_code", "as_of_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_stock_snapshots_code_asof", table_name="stock_snapshots")
    op.drop_table("stock_snapshots")
