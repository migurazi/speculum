"""Portfolio Repository — M3 #4 거래내역 기반 사실 회계 (ADR-0029 D1).

종목 lineage 단위의 사용자 **수동 입력** 거래내역(매수/매도). stock_notes
(notes_repository)의 user-scoped CRUD 패턴을 미러하되 body 대신 거래
5-tuple(side/quantity/unit_price/trade_date/fee)을 보유하며, **append-only**
정신(거래 정정은 역분개로) — 단 입력 실수 정정용 본인 거래 삭제는 허용한다.

설계 (notes / watchlist 선례 일관 — oracle T26/T28 자문):
- 모든 method 가 `user_id: UUID` keyword-only — IDOR 차단. user_id 는 route 의
  CurrentUserDep 로만 주입, request body 에서 절대 받지 않음.
- **list_transactions 는 반드시 user_id 필터** — user_id 누락 시 전 사용자 거래
  누출(가장 위험). code_lineage_id 는 선택 필터(특정 종목만). DB 복합 인덱스로도
  보강.
- owner-check — delete 는 미존재 또는 owner mismatch 시 False(IDOR). 미존재와
  mismatch 를 구별하지 않아 user 정보 누출 차단.
- **side 값 도메인 강제** — 'buy' | 'sell' 외 거부(PortfolioDataError).
- **quantity > 0, unit_price >= 0, fee >= 0** 강제 — 음수/0 수량은 입력 실수.
  (회계 사실 입력 — 평가/제안 0, ADR-0029 D3. 본 layer 는 숫자 사실만 보관.)

회계 ≠ 평가 분리(ADR-0029 D3):
    본 repository 는 거래 사실(side/quantity/unit_price/trade_date/fee)만 저장.
    보유수량·평단·실현손익 등 position 산출은 `portfolio_position` service(stateless)
    가 거래내역에서 계산한다. repository 는 손익/등급/순위 등 평가 라벨을 일절
    저장·산출하지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

__all__ = [
    "FakePortfolioRepository",
    "PortfolioDataError",
    "PortfolioRepository",
    "PortfolioTransaction",
    "VALID_SIDES",
]


class PortfolioDataError(Exception):
    """거래 invariant 위반 — side 값, 음수 수량/단가/수수료 등."""


# side 값 도메인 — 'buy' | 'sell' 외 거부. enum 대신 frozenset(SQLite/PG 동형).
VALID_SIDES: frozenset[str] = frozenset({"buy", "sell"})


# =============================================================================
# Domain entity — frozen dataclass (codebase 일관)
# =============================================================================

@dataclass(frozen=True, slots=True)
class PortfolioTransaction:
    """portfolio_transactions row 의 in-memory representation(ADR-0029 D1).

    Attributes:
        id: 거래 UUID.
        user_id: 소유자 — IDOR owner-check 기준.
        code_lineage_id: stocks_master.id lineage. 거래가 부착된 종목.
        side: 'buy' | 'sell'.
        quantity: 거래 수량(주, 양의 정수).
        unit_price: 거래 단가(원, Decimal). 부동소수 잔차 회피.
        trade_date: 거래 체결일. corporate action 보정(D5)의 기준일.
        fee: 수수료(+세금 합산 입력값, D1). Decimal, 미입력 시 0.
        created_at: 입력 시각(UTC). append-only 영구 보존.
    """

    id: UUID
    user_id: UUID
    code_lineage_id: UUID
    side: str
    quantity: int
    unit_price: Decimal
    trade_date: date
    fee: Decimal
    created_at: datetime


# =============================================================================
# Validation helpers
# =============================================================================

def _validate_transaction(
    *, side: str, quantity: int, unit_price: Decimal, fee: Decimal,
) -> None:
    """거래 입력 invariant — side 값 + 양수 수량 + 비음수 단가/수수료.

    회계 사실의 정합성만 검증(평가/제안 0 — ADR-0029 D3). 단가·수량은 숫자
    사실일 뿐, 본 함수는 어떤 손익 라벨도 산출하지 않는다.
    """
    if side not in VALID_SIDES:
        raise PortfolioDataError(
            f"side '{side}' 는 미지원 — {sorted(VALID_SIDES)} 만 허용",
        )
    if quantity <= 0:
        raise PortfolioDataError(
            f"quantity must be positive (got {quantity})",
        )
    if unit_price < 0:
        raise PortfolioDataError(
            f"unit_price must be >= 0 (got {unit_price})",
        )
    if fee < 0:
        raise PortfolioDataError(
            f"fee must be >= 0 (got {fee})",
        )


# =============================================================================
# Portfolio Repository Protocol
# =============================================================================

@runtime_checkable
class PortfolioRepository(Protocol):
    """거래내역 CRUD contract — 모든 method 가 `user_id` keyword-only (IDOR).

    append-only 정신 — 거래 정정은 역분개(반대 side append)로. 입력 실수 정정용
    본인 거래 삭제(delete_transaction)만 허용(UPDATE 없음).
    """

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
        """거래 추가. side='buy'|'sell', quantity>0, unit_price/fee>=0.

        Raises:
            PortfolioDataError: side 값 위반 또는 음수 수량/단가/수수료.
        """
        ...

    def list_transactions(
        self,
        *,
        user_id: UUID,
        code_lineage_id: UUID | None = None,
    ) -> Sequence[PortfolioTransaction]:
        """본인 거래내역 (trade_date asc, created_at asc, id asc — 결정적).

        **반드시 user_id 필터** — user_id 누락 시 전 사용자 거래 누출. code_lineage_id
        주어지면 해당 종목만, None 이면 전 종목. owner 의 거래가 없으면 빈 list.
        """
        ...

    def delete_transaction(self, tx_id: UUID, *, user_id: UUID) -> bool:
        """거래 삭제(입력 실수 정정용). 성공 True. 미존재/owner mismatch 시 False.

        append-only 정신상 거래 정정은 역분개 권장이나, 입력 실수의 본인 거래
        삭제는 허용. owner mismatch(타 user 거래)는 False — IDOR 차단.
        """
        ...


# =============================================================================
# Fake — contract reference (in-memory)
# =============================================================================

class FakePortfolioRepository(PortfolioRepository):
    """In-memory 거래내역 store — contract reference."""

    def __init__(self) -> None:
        self._txns: dict[UUID, PortfolioTransaction] = {}
        # 삽입 순서 보조 tiebreaker — 같은 trade_date/created_at 의 결정적 정렬.
        self._seq: dict[UUID, int] = {}
        self._counter: int = 0

    def _now(self) -> datetime:
        return datetime.now(UTC)

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
        tx = PortfolioTransaction(
            id=uuid4(),
            user_id=user_id,
            code_lineage_id=code_lineage_id,
            side=side,
            quantity=quantity,
            unit_price=unit_price,
            trade_date=trade_date,
            fee=fee,
            created_at=self._now(),
        )
        self._txns[tx.id] = tx
        self._seq[tx.id] = self._counter
        self._counter += 1
        return tx

    def list_transactions(
        self,
        *,
        user_id: UUID,
        code_lineage_id: UUID | None = None,
    ) -> Sequence[PortfolioTransaction]:
        # user_id 필터 우선 — 누락 시 전 사용자 누출(가장 위험). code_lineage_id
        # 는 선택 필터. trade_date / created_at / 삽입순으로 결정적 정렬(같은
        # trade_date 의 매수/매도 순서를 입력순으로 보존 — 평단 이동평균의 정합).
        owned = [
            t
            for t in self._txns.values()
            if t.user_id == user_id
            and (code_lineage_id is None or t.code_lineage_id == code_lineage_id)
        ]
        return tuple(
            sorted(
                owned,
                key=lambda t: (t.trade_date, t.created_at, self._seq[t.id]),
            )
        )

    def delete_transaction(self, tx_id: UUID, *, user_id: UUID) -> bool:
        tx = self._txns.get(tx_id)
        if tx is None or tx.user_id != user_id:
            return False  # 미존재 또는 owner mismatch — IDOR 차단.
        self._txns.pop(tx_id, None)
        self._seq.pop(tx_id, None)
        return True
