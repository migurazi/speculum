"""Phase B — market_caps 테이블 (ADR-0003 D2 + ADR-0004).

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-29

일별 시가총액 + 발행주식수 + (옵션) 자사주의 영구화 table. pykrx adapter 가
fetch 하던 `MarketCapRow` 를 영속화하여 DbFieldProvider 의
`market_cap_krx_official` / `shares_issued` 해소를 가능케 함.

prices_daily (Alembic 0001) 패턴 미러:
    - PK (code, effective_date) — KRX 시가총액은 정정 없음 (supersede 없음).
    - citation_id FK → source_citations.id (Fidelity, 모든 row citation 참조).
    - id surrogate UUID + UNIQUE (PITRecord protocol 1차 키, silent overwrite 차단).
    - (code_lineage_id, effective_date) 복합 인덱스 (lineage 시계열 hot path).

자사주 (shares_treasury) 는 nullable — pykrx 미제공 시 None. 절대 0 으로 가정
하지 않음 (silent 오류 — DART 보강 별도 cycle, T48c).

append-only 트리거 무관:
    - market_caps 는 prices_daily 와 동일하게 KRX 가격류 (정정 없음). source_
      citations 의 무조건 차단 트리거 (Alembic 0001) / financials·corporate_
      actions 의 조건부 트리거 (ADR-0020, Alembic 0007) 와 무관 — supersede 컬럼
      자체가 없음. 본 migration 은 어떤 트리거도 추가하지 않음.

관련:
- ADR-0003 D2 (시가총액 출처), ADR-0004 (시가총액 정의)
- ADR-0002 D3 (Source Citation persistence)
- Alembic 0001 (prices_daily — 본 migration 의 미러 대상)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_caps",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("code_lineage_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column(
            "market_cap", sa.Numeric(precision=28, scale=4), nullable=False,
        ),
        sa.Column("shares_outstanding", sa.BigInteger(), nullable=False),
        # pykrx 미제공 시 None — 절대 0 으로 가정 금지 (DART 보강 별도 cycle).
        sa.Column("shares_treasury", sa.BigInteger(), nullable=True),
        sa.Column("citation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["citation_id"],
            ["source_citations.id"],
            name="fk_market_caps_citation_id_source_citations",
        ),
        sa.PrimaryKeyConstraint(
            "code", "effective_date", name="pk_market_caps",
        ),
        # prices_daily 와 동일 — id surrogate UUID 의 PITRecord 호환 보강.
        sa.UniqueConstraint("id", name="uq_market_caps_id"),
    )
    op.create_index(
        "ix_market_caps_lineage_date",
        "market_caps",
        ["code_lineage_id", "effective_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_market_caps_lineage_date", table_name="market_caps")
    op.drop_table("market_caps")
