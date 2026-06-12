"""SQL Portfolio Repository — M3 #4 거래내역 기반 사실 회계 (ADR-0029 D1).

`portfolio_repository.py` 의 `FakePortfolioRepository` 와 동일 contract 를
SQLAlchemy 2 sync session 위에서 구현. `sql_notes_repository.py` 의 user-scoped
패턴을 미러하되, body mutable 대신 거래 append-only(UPDATE 없음 — 정정은 역분개,
입력 실수 정정용 본인 거래 삭제만 허용)이다.

설계 결정:
1. **Fake 와 동일 invariant 강제** — side 값('buy'|'sell'), quantity>0,
   unit_price/fee>=0. helper(`_validate_transaction`)는 portfolio_repository
   에서 import 공유.
2. **session 의존** — `__init__(session: Session)`. 한 request = 한 session.
3. **명시적 flush** — Fake 와 contract 동등성을 위해 add 직후 flush. commit 은
   `get_db_session_or_none`(DI wiring)가 endpoint 종료 시 수행.
4. **list_transactions 는 user_id WHERE 필수** — user_id predicate 누락 시 전
   사용자 거래 누출(가장 위험). code_lineage_id 는 선택 필터. DB 복합 인덱스로도
   보강. 정렬은 (trade_date, created_at, id) asc — 결정성 + 같은 거래일의
   입력순 보존(평단 이동평균 정합).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.converters import (
    portfolio_transaction_orm_to_record,
    portfolio_transaction_record_to_orm,
)
from app.db.orm.portfolio_transactions import PortfolioTransactionORM
from app.repositories.portfolio_repository import (
    PortfolioRepository,
    PortfolioTransaction,
    _validate_transaction,
)

__all__ = ["SqlPortfolioRepository"]


def _now() -> datetime:
    return datetime.now(UTC)


class SqlPortfolioRepository(PortfolioRepository):
    """SQLAlchemy 기반 Portfolio repository — M3 #4 거래내역 append-only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_transaction(
        self,
        *,
        user_id: UUID,
        code_lineage_id: UUID,
        side: str,
        quantity: int,
        unit_price: Decimal,
        trade_date: date,
        fee: Decimal = Decimal("0"),
    ) -> PortfolioTransaction:
        _validate_transaction(
            side=side, quantity=quantity, unit_price=unit_price, fee=fee,
        )
        record = PortfolioTransaction(
            id=uuid4(),
            user_id=user_id,
            code_lineage_id=code_lineage_id,
            side=side,
            quantity=quantity,
            unit_price=unit_price,
            trade_date=trade_date,
            fee=fee,
            created_at=_now(),
        )
        self._session.add(portfolio_transaction_record_to_orm(record))
        self._session.flush()
        return record

    def list_transactions(
        self,
        *,
        user_id: UUID,
        code_lineage_id: UUID | None = None,
    ) -> Sequence[PortfolioTransaction]:
        # user_id WHERE 필수 — 누락 시 전 사용자 누출(가장 위험). code_lineage_id
        # 주어지면 추가 필터. (trade_date, created_at, id) asc 정렬로 결정성 +
        # 같은 거래일 입력순 보존(평단 이동평균 정합).
        stmt = select(PortfolioTransactionORM).where(
            PortfolioTransactionORM.user_id == user_id,
        )
        if code_lineage_id is not None:
            stmt = stmt.where(
                PortfolioTransactionORM.code_lineage_id == code_lineage_id,
            )
        stmt = stmt.order_by(
            PortfolioTransactionORM.trade_date.asc(),
            PortfolioTransactionORM.created_at.asc(),
            PortfolioTransactionORM.id.asc(),
        )
        result = self._session.execute(stmt).scalars().all()
        return tuple(portfolio_transaction_orm_to_record(o) for o in result)

    def delete_transaction(self, tx_id: UUID, *, user_id: UUID) -> bool:
        orm = self._session.get(PortfolioTransactionORM, tx_id)
        if orm is None or orm.user_id != user_id:
            return False  # 미존재 또는 owner mismatch — IDOR 차단.
        self._session.delete(orm)
        self._session.flush()
        return True
