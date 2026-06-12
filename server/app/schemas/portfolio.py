"""Portfolio wire schemas — M3 #4 거래내역 기반 사실 회계 (ADR-0029).

설계 (notes / watchlist 선례 일관):
- Pydantic v2 strict=False(JSON string → UUID/Decimal/date coercion) +
  extra=forbid + frozen.
- `from_domain` 단방향 factory.
- **user_id 미노출**(IDOR — route 의 CurrentUserDep 가 결정).
- side 는 Literal 'buy'|'sell' — repository 검증과 동일 도메인.
- unit_price / fee / avg_cost 등 금액은 **문자열**로 wire 노출(Decimal 직렬화 —
  stocks.py 의 가격 str 패턴 일관, float 잔차/정밀도 손실 차단).

회계 ≠ 평가 분리(ADR-0029 D3) — wire 계약 가드레일:
    positions 응답은 **숫자 사실 필드만**(quantity/avg_cost/total_cost/
    realized_pnl/market_value?/unrealized_pnl?). 손익률·등급·순위·"수익/손실/양호"
    라벨·등락색 필드를 일절 두지 않는다. 클라이언트는 grayscale 중립 톤으로만
    표시(판단·강조·색분기 0).

세금 미결합 — 세전(税前)만(ADR-0029 D6):
    실현손익/평가손익은 세전. 세금 필드(양도세·세후손익 등)는 두지 않는다.
    fee 는 사용자 입력 거래 비용일 뿐(세금 계산 #5 ADR-0030 별도).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.repositories.portfolio_repository import PortfolioTransaction
from app.services.portfolio_position import Position

__all__ = [
    "PortfolioPositionOut",
    "PortfolioPositionsOut",
    "PortfolioTransactionCreateIn",
    "PortfolioTransactionListOut",
    "PortfolioTransactionOut",
]

# strict=False — JSON string → UUID/Decimal/date coercion 허용(notes/watchlist
# 패턴 일관). extra=forbid + frozen 유지.
_STRICT_MODEL_CONFIG = ConfigDict(strict=False, extra="forbid", frozen=True)


# =============================================================================
# 거래 입력 / 출력
# =============================================================================

class PortfolioTransactionCreateIn(BaseModel):
    """POST /api/portfolio/transactions body — 거래 입력.

    user_id 미노출(CurrentUserDep). side 는 'buy'|'sell' 만. quantity>0,
    unit_price/fee>=0(Pydantic + repository 이중 검증).
    """

    model_config = _STRICT_MODEL_CONFIG

    # code — code_lineage_id(종목 lineage UUID). watchlist/notes 와 동일 식별자.
    code_lineage_id: UUID
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)
    # 금액은 str/number 입력 모두 coercion(Decimal). 음수 차단(ge=0).
    unit_price: Decimal = Field(ge=0)
    trade_date: date
    fee: Decimal = Field(default=Decimal("0"), ge=0)


class PortfolioTransactionOut(BaseModel):
    """거래 wire output — 금액은 문자열(Decimal 정밀 노출)."""

    model_config = _STRICT_MODEL_CONFIG

    id: UUID
    code_lineage_id: UUID
    side: str
    quantity: int
    unit_price: str
    trade_date: date
    fee: str
    created_at: datetime

    @classmethod
    def from_domain(cls, tx: PortfolioTransaction) -> PortfolioTransactionOut:
        return cls(
            id=tx.id,
            code_lineage_id=tx.code_lineage_id,
            side=tx.side,
            quantity=tx.quantity,
            unit_price=str(tx.unit_price),
            trade_date=tx.trade_date,
            fee=str(tx.fee),
            created_at=tx.created_at,
        )


class PortfolioTransactionListOut(BaseModel):
    """GET /api/portfolio/transactions 결과."""

    model_config = _STRICT_MODEL_CONFIG

    items: tuple[PortfolioTransactionOut, ...]
    total: int


# =============================================================================
# Position 계산 결과 — **숫자 사실만**(ADR-0029 D3, 세전 D6)
# =============================================================================

class PortfolioPositionOut(BaseModel):
    """단일 종목 position — 보유수량/평단/원가/실현손익 + (현재가 시)평가.

    가드레일(ADR-0029 D3/D6): 손익률·등급·순위·평가 라벨·등락색 필드 없음.
    market_value/unrealized_pnl 은 현재가 조회 가능 시에만(None=미조회). 세전만.
    """

    model_config = _STRICT_MODEL_CONFIG

    code_lineage_id: UUID
    quantity: int
    avg_cost: str
    total_cost: str
    realized_pnl: str
    # 현재가 조회 가능 시에만 — None 이면 평가 미수행(시장 데이터 없음).
    market_value: str | None = None
    unrealized_pnl: str | None = None

    @classmethod
    def from_domain(cls, position: Position) -> PortfolioPositionOut:
        return cls(
            code_lineage_id=position.code_lineage_id,
            quantity=position.quantity,
            avg_cost=str(position.avg_cost),
            total_cost=str(position.total_cost),
            realized_pnl=str(position.realized_pnl),
            market_value=(
                str(position.market_value)
                if position.market_value is not None
                else None
            ),
            unrealized_pnl=(
                str(position.unrealized_pnl)
                if position.unrealized_pnl is not None
                else None
            ),
        )


class PortfolioPositionsOut(BaseModel):
    """GET /api/portfolio/positions 결과 — 종목별 position list.

    code_lineage_id asc 결정적 정렬(랭킹·손익순 정렬 0 — ADR-0029 D3 행동 유도
    차단). 평가 라벨/세금 필드 없음.
    """

    model_config = _STRICT_MODEL_CONFIG

    positions: tuple[PortfolioPositionOut, ...]
