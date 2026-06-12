"""PortfolioTransactionORM — M3 #4 거래내역 기반 사실 회계 (ADR-0029 D1).

`app/repositories/portfolio_repository.py` 의 `PortfolioTransaction` frozen
dataclass 의 DB 표현. `stock_notes`(notes.py)의 user-scoped 패턴을 미러하되
폴더/body 대신 거래 5-tuple(side/quantity/unit_price/trade_date/fee)을 보유한다.

핵심 결정 (ADR-0029 D1):
    - **user_id FK → users.id (ADR-0021 D2, migration 0012)** — user 격리 FK
      anchor. naming `fk_portfolio_transactions_user_id_users`(base.py 규약).
    - **code_lineage_id** — stocks_master.id lineage. FK 없음(watchlist_items /
      stock_notes 선례 — stocks_master 미bootstrap 호환).
    - **side** — String(4) 'buy' | 'sell'. 값 도메인은 repository/route 강제.
    - **unit_price / fee** — Numeric(18, 4)(prices_daily 의 _PRICE_NUMERIC 동일).
      Decimal 로 매핑 — 평단·실현손익 산술의 결정성(부동소수 잔차 회피).
    - **append-only** — created_at 만(updated_at 없음 — screen_runs / custom_packs
      정신). 거래 정정은 append(역분개), 입력 실수 정정용 본인 거래 삭제만 허용
      (DB trigger 대상 아님 — 0007 은 financials/corporate_actions 만).

인덱스:
    - ix_portfolio_transactions_user_id: owner-check / list hot path.
    - ix_portfolio_transactions_user_code(user_id, code_lineage_id):
      list_transactions 의 복합 필터 — user_id predicate 누락 회귀 방지의 DB
      차원 보강(stock_notes 선례 일관).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

# 단가 / 수수료 Numeric — prices_daily 의 _PRICE_NUMERIC 과 동일(18, 4).
# 한국 주식 가격 최대 ~9백만원(8자리 정수부) + split 후 소수부(4자리) 마진.
_MONEY_NUMERIC = Numeric(precision=18, scale=4)


class PortfolioTransactionORM(Base):
    """portfolio_transactions row — ADR-0029 D1 의 거래내역 entity(append-only)."""

    __tablename__ = "portfolio_transactions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    # user_id FK → users.id (ADR-0021 D2). user 격리 FK anchor.
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id", name="fk_portfolio_transactions_user_id_users",
        ),
        nullable=False,
    )
    # code_lineage_id — stocks_master.id lineage. FK 없음(stock_notes 선례).
    code_lineage_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False,
    )
    # side — 'buy' | 'sell'. 값 도메인은 repository/route 강제.
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    # quantity — 거래 수량(주, 정수). KRX 일반주(fractional 미지원).
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # unit_price — 거래 단가(원). Decimal 산술 결정성.
    unit_price: Mapped[Decimal] = mapped_column(_MONEY_NUMERIC, nullable=False)
    # trade_date — 거래 체결일. CA 보정(D5)의 기준일.
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    # fee — 수수료(+세금 합산 입력값, D1). 미입력 시 0(server_default).
    fee: Mapped[Decimal] = mapped_column(
        _MONEY_NUMERIC, nullable=False, default=Decimal("0"),
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        # owner-check / list hot path.
        Index("ix_portfolio_transactions_user_id", "user_id"),
        # list_transactions 의 (user_id, code_lineage_id) 복합 필터 — user_id
        # predicate 누락 회귀 방지의 DB 차원 보강(stock_notes 선례).
        Index(
            "ix_portfolio_transactions_user_code",
            "user_id",
            "code_lineage_id",
        ),
    )
