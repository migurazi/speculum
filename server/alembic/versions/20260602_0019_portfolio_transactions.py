"""M3 #4 — portfolio_transactions 테이블 (거래내역 기반 사실 회계, ADR-0029 D1).

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-02

ADR-0029 Portfolio 회계의 backbone. 사용자가 **수동 입력**하는 매수/매도 거래내역을
종목 lineage 단위로 저장하는 user-scoped **append-only** 테이블. 거래 정정은 append
(역분개)로(ADR-0020 정신) — 단, 입력 실수 정정용 본인 거래 삭제는 허용(repository
delete_transaction). 증권사 계좌 연동 0(ADR-0029 D4 — 수동 입력 장부).

설계 결정 (ADR-0029 D1):
    - user_id FK → users.id (ADR-0021 D2 격리 anchor). naming
      `fk_portfolio_transactions_user_id_users` — base.py naming_convention 일치.
    - code_lineage_id: stocks_master.id lineage UUID (FK 없음 — watchlist_items /
      stock_notes 선례와 동일. stocks_master 미bootstrap 환경 호환).
    - side: String(4) — 'buy' | 'sell'. enum 이 아니라 String + service/route
      layer(두 값만 허용)가 값 도메인 강제(SQLite/PG 동형).
    - quantity: Integer — 거래 수량(주). 분할매매도 정수 주 단위(fractional share
      미지원, KRX 일반주).
    - unit_price: Numeric(18, 4) — 거래 단가(원). prices_daily 의 _PRICE_NUMERIC
      과 동일 precision/scale(한국 주식 최대 ~9백만원 + split 후 소수부 마진).
    - trade_date: Date — 거래 체결일. corporate action 보정(D5)의 기준일.
    - fee: Numeric(18, 4), server_default '0' — 수수료(+세금 합산 입력값, D1).
      세금 계산은 #5(ADR-0030)에서 별도 — 본 컬럼은 사용자 입력 비용일 뿐.
    - created_at: 입력 시각(UTC). append-only 영구 보존.

**insert 절대 없음 (시스템 생성 거래 0)**:
    seeder/migration/builtin 어떤 경로도 거래 row 를 만들지 않는다. 거래 생성은
    인증 route + repository 뿐(수동 입력). 본 migration 은 schema(table + index)만.

PG/SQLite 양쪽 (FK):
    FK 추가는 `op.create_table` 의 ForeignKeyConstraint 로 dialect 무관 처리.
    SQLite 의 FK 강제는 connection 단위 PRAGMA foreign_keys=ON 필요(테스트
    conftest 가 활성, 운영 PG 는 default ON). portfolio_transactions 는
    append-only trigger 대상이 아니다(0007 은 financials/corporate_actions 만) —
    본인 거래 삭제(입력 실수 정정)가 DB trigger 에 막히지 않는다.

인덱스:
    - ix_portfolio_transactions_user_id: list/owner-check hot path.
    - ix_portfolio_transactions_user_code(user_id, code_lineage_id):
      list_transactions 의 (user_id, code_lineage_id) 복합 필터 — user_id
      predicate 누락 회귀 방지의 DB 차원 보강(stock_notes 선례 일관).

관련 ADR / 문서:
- ADR-0029 D1 (append-only 수동 입력 거래내역), D4 (계좌 연동 0), D5 (CA 보정),
  D6 (세전만)
- ADR-0021 D2 (users 테이블 + user_id FK 격리), migration 0012 (FK 선례)
- ADR-0020 (append-only 불변), migration 0014 (stock_notes — user-scoped 선례)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # portfolio_transactions 테이블 — 거래내역 기반 사실 회계(ADR-0029 D1).
    # user_id FK → users.id (ADR-0021 D2). code_lineage_id 는 FK 없음
    # (watchlist_items / stock_notes 선례 — stocks_master 미bootstrap 호환).
    op.create_table(
        "portfolio_transactions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code_lineage_id", sa.Uuid(as_uuid=True), nullable=False),
        # side — 'buy' | 'sell'. 값 도메인은 service/route layer 강제.
        sa.Column("side", sa.String(4), nullable=False),
        # quantity — 거래 수량(주, 정수). KRX 일반주(fractional 미지원).
        sa.Column("quantity", sa.Integer(), nullable=False),
        # unit_price — 거래 단가(원). prices_daily 와 동일 Numeric(18, 4).
        sa.Column("unit_price", sa.Numeric(precision=18, scale=4), nullable=False),
        # trade_date — 거래 체결일. CA 보정(D5)의 기준일.
        sa.Column("trade_date", sa.Date(), nullable=False),
        # fee — 수수료(+세금 합산 입력값, D1). server_default '0'.
        sa.Column(
            "fee",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_portfolio_transactions"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_portfolio_transactions_user_id_users",
        ),
    )
    # owner-check / list hot path 인덱스.
    op.create_index(
        "ix_portfolio_transactions_user_id",
        "portfolio_transactions",
        ["user_id"],
    )
    # 복합 인덱스 — list_transactions 의 (user_id, code_lineage_id) 필터.
    # user_id predicate 누락 회귀 방지의 DB 차원 보강(stock_notes 선례).
    op.create_index(
        "ix_portfolio_transactions_user_code",
        "portfolio_transactions",
        ["user_id", "code_lineage_id"],
    )


def downgrade() -> None:
    # 인덱스 → 테이블 역순 drop.
    op.drop_index(
        "ix_portfolio_transactions_user_code",
        table_name="portfolio_transactions",
    )
    op.drop_index(
        "ix_portfolio_transactions_user_id",
        table_name="portfolio_transactions",
    )
    op.drop_table("portfolio_transactions")
