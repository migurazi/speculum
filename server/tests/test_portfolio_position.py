"""Portfolio Position 계산 service 단위 테스트 — M3 #4 (ADR-0029 D2/D5).

테스트 매트릭스:
1. 이동평균법 평단 — 다중 매수 후 평단 = 가중평균(+ fee 산입).
2. 실현손익 — 매도 시 (매도가 − 평단)×수량 − fee, 평단 불변(이동평균법).
3. 액면분할 보정(D5) — 거래일 이후 split → 수량 2배·평단 1/2(원가 보존).
4. 무상증자 보정 — 수량 ×(1+ratio)·평단 ÷(1+ratio).
5. merger/spin_off 미보정 — 보유수량 불변 + unadjusted_actions 보고.
6. 현재가 평가 — market_value/unrealized_pnl 산술(세전).
7. 결정성 — 같은 입력 = 같은 출력(Decimal 정밀).
8. 평가 라벨 0(D3) — Position 에 손익률/등급/순위 필드 없음(숫자 사실만).
9. 전량 매도 — 보유 0, 평단 0, 실현손익 누적.
10. 과다 매도 → PortfolioPositionError.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest

from app.repositories.pit_protocols import CorporateActionRecord
from app.repositories.portfolio_repository import PortfolioTransaction
from app.services.portfolio_position import (
    PortfolioPositionError,
    Position,
    compute_position,
)

_LINEAGE = UUID("11111111-1111-1111-1111-111111111111")
_USER = UUID("00000000-0000-0000-0000-00000000000a")

_SEQ = [0]


def _tx(
    side: str,
    quantity: int,
    unit_price: str,
    trade_date: date,
    fee: str = "0",
) -> PortfolioTransaction:
    """결정적 created_at(삽입순) 의 거래 헬퍼."""
    _SEQ[0] += 1
    return PortfolioTransaction(
        id=uuid4(),
        user_id=_USER,
        code_lineage_id=_LINEAGE,
        side=side,
        quantity=quantity,
        unit_price=Decimal(unit_price),
        trade_date=trade_date,
        fee=Decimal(fee),
        created_at=datetime(2026, 1, 1, 0, 0, _SEQ[0] % 60, tzinfo=UTC),
    )


def _ca(
    action_type: str,
    effective_date: date,
    *,
    ratio: str | None = None,
    details: dict | None = None,
) -> CorporateActionRecord:
    return CorporateActionRecord(
        id=uuid4(),
        code="005930",
        code_lineage_id=_LINEAGE,
        action_type=action_type,
        announced_date=effective_date,
        effective_date=effective_date,
        payment_date=None,
        ratio=Decimal(ratio) if ratio is not None else None,
        cash_amount=None,
        details=MappingProxyType(details or {}),
        citation_id=uuid4(),
        superseded_by=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


# =============================================================================
# 이동평균법 평단
# =============================================================================

def test_moving_average_cost_basic() -> None:
    """100주@1000 + 100주@2000 → 평단 1500, 보유 200, 원가 300000."""
    txns = [
        _tx("buy", 100, "1000", date(2024, 1, 1)),
        _tx("buy", 100, "2000", date(2024, 2, 1)),
    ]
    pos = compute_position(txns, code_lineage_id=_LINEAGE)
    assert pos.quantity == 200
    assert pos.avg_cost == Decimal("1500")
    assert pos.total_cost == Decimal("300000")
    assert pos.realized_pnl == Decimal("0")


def test_moving_average_cost_with_fee() -> None:
    """fee 는 취득가액(원가)에 산입 → 평단에 반영."""
    txns = [_tx("buy", 100, "1000", date(2024, 1, 1), fee="500")]
    pos = compute_position(txns, code_lineage_id=_LINEAGE)
    # 원가 = 100*1000 + 500 = 100500, 평단 = 1005.
    assert pos.total_cost == Decimal("100500")
    assert pos.avg_cost == Decimal("1005")


# =============================================================================
# 실현손익
# =============================================================================

def test_realized_pnl_on_sell() -> None:
    """100주@1000 매수, 50주@1500 매도 → 실현손익 = (1500-1000)*50 = 25000."""
    txns = [
        _tx("buy", 100, "1000", date(2024, 1, 1)),
        _tx("sell", 50, "1500", date(2024, 3, 1)),
    ]
    pos = compute_position(txns, code_lineage_id=_LINEAGE)
    assert pos.realized_pnl == Decimal("25000")
    # 매도 후 보유 50, 평단 불변(이동평균법).
    assert pos.quantity == 50
    assert pos.avg_cost == Decimal("1000")
    assert pos.total_cost == Decimal("50000")


def test_realized_pnl_with_sell_fee() -> None:
    """매도 fee 는 실현손익에서 차감(세전 — 세금 아님)."""
    txns = [
        _tx("buy", 100, "1000", date(2024, 1, 1)),
        _tx("sell", 100, "1500", date(2024, 3, 1), fee="1000"),
    ]
    pos = compute_position(txns, code_lineage_id=_LINEAGE)
    # (1500-1000)*100 - 1000 = 49000.
    assert pos.realized_pnl == Decimal("49000")
    assert pos.quantity == 0
    assert pos.avg_cost == Decimal("0")
    assert pos.total_cost == Decimal("0")


def test_full_liquidation() -> None:
    """전량 매도 → 보유 0, 평단 0, 실현손익만 남음."""
    txns = [
        _tx("buy", 100, "1000", date(2024, 1, 1)),
        _tx("sell", 100, "1200", date(2024, 2, 1)),
    ]
    pos = compute_position(txns, code_lineage_id=_LINEAGE)
    assert pos.quantity == 0
    assert pos.realized_pnl == Decimal("20000")


# =============================================================================
# corporate action 보정 (D5)
# =============================================================================

def test_split_adjusts_quantity_and_avg_cost() -> None:
    """거래일 이후 액면분할 1:2 → 수량 2배, 평단 1/2, 원가 보존."""
    txns = [_tx("buy", 100, "2000", date(2024, 1, 1))]
    # 2024-06-01 액면분할 2배(split_ratio=2).
    actions = [_ca("split", date(2024, 6, 1), details={"split_ratio": 2})]
    pos = compute_position(
        txns, code_lineage_id=_LINEAGE, corporate_actions=actions,
    )
    # 수량 100 → 200, 평단 2000 → 1000, 원가 200000 불변.
    assert pos.quantity == 200
    assert pos.avg_cost == Decimal("1000")
    assert pos.total_cost == Decimal("200000")


def test_split_before_trade_not_applied() -> None:
    """거래일 이전 발효 split 은 미보정(사용자가 분할 후 가격으로 입력 가정)."""
    txns = [_tx("buy", 100, "1000", date(2024, 7, 1))]
    actions = [_ca("split", date(2024, 6, 1), details={"split_ratio": 2})]
    pos = compute_position(
        txns, code_lineage_id=_LINEAGE, corporate_actions=actions,
    )
    assert pos.quantity == 100
    assert pos.avg_cost == Decimal("1000")


def test_bonus_issue_adjusts() -> None:
    """무상증자 20% → 수량 ×1.2, 평단 ÷1.2(원가 보존)."""
    txns = [_tx("buy", 100, "1200", date(2024, 1, 1))]
    actions = [_ca("bonus_issue", date(2024, 6, 1), details={"bonus_ratio": "0.2"})]
    pos = compute_position(
        txns, code_lineage_id=_LINEAGE, corporate_actions=actions,
    )
    # 수량 100 → 120, 원가 120000 보존, 평단 = 1000.
    assert pos.quantity == 120
    assert pos.total_cost == Decimal("120000")
    assert pos.avg_cost == Decimal("1000")


def test_merger_not_adjusted_but_reported() -> None:
    """merger 는 미보정(보유 불변) + unadjusted_actions 보고."""
    txns = [_tx("buy", 100, "1000", date(2024, 1, 1))]
    actions = [_ca("merger", date(2024, 6, 1))]
    pos = compute_position(
        txns, code_lineage_id=_LINEAGE, corporate_actions=actions,
    )
    assert pos.quantity == 100  # 미보정.
    assert pos.unadjusted_actions == (date(2024, 6, 1),)


# =============================================================================
# 현재가 평가 (산술 사실, 세전)
# =============================================================================

def test_market_valuation() -> None:
    """현재가 입력 시 평가금액 = 현재가×보유, 평가손익 = 평가금액 − 원가."""
    txns = [_tx("buy", 100, "1000", date(2024, 1, 1))]
    pos = compute_position(
        txns, code_lineage_id=_LINEAGE, current_price=Decimal("1500"),
    )
    assert pos.market_value == Decimal("150000")
    assert pos.unrealized_pnl == Decimal("50000")  # 세전.


def test_no_current_price_means_no_valuation() -> None:
    """현재가 미입력 → market_value/unrealized_pnl = None."""
    txns = [_tx("buy", 100, "1000", date(2024, 1, 1))]
    pos = compute_position(txns, code_lineage_id=_LINEAGE)
    assert pos.market_value is None
    assert pos.unrealized_pnl is None


# =============================================================================
# 결정성 + 평가 라벨 0 (D3)
# =============================================================================

def test_deterministic() -> None:
    """같은 입력 = 같은 출력(Decimal 정밀, 결정적)."""
    txns = [
        _tx("buy", 33, "1001", date(2024, 1, 1), fee="7"),
        _tx("buy", 67, "999", date(2024, 2, 1), fee="3"),
        _tx("sell", 40, "1500", date(2024, 3, 1), fee="11"),
    ]
    p1 = compute_position(txns, code_lineage_id=_LINEAGE)
    p2 = compute_position(txns, code_lineage_id=_LINEAGE)
    assert p1 == p2


def test_position_has_only_numeric_facts() -> None:
    """ADR-0029 D3 가드 — Position 에 평가 라벨/등급/순위 필드 0.

    숫자 사실 필드만(손익률·등급·순위·색분기 필드 부재). 회귀 게이트.
    """
    fields = set(Position.__dataclass_fields__.keys())
    assert fields == {
        "code_lineage_id",
        "quantity",
        "avg_cost",
        "total_cost",
        "realized_pnl",
        "market_value",
        "unrealized_pnl",
        "unadjusted_actions",
    }
    # 평가 라벨류 필드명 부재.
    forbidden = {"pnl_rate", "return_rate", "grade", "rank", "label", "color"}
    assert not (fields & forbidden)


# =============================================================================
# invariant 위반
# =============================================================================

def test_oversell_raises() -> None:
    """보유보다 많은 매도 → PortfolioPositionError(입력 실수)."""
    txns = [
        _tx("buy", 50, "1000", date(2024, 1, 1)),
        _tx("sell", 100, "1500", date(2024, 2, 1)),
    ]
    with pytest.raises(PortfolioPositionError, match="exceeds holding"):
        compute_position(txns, code_lineage_id=_LINEAGE)


def test_other_lineage_ignored() -> None:
    """다른 종목 거래는 무시(방어 — 호출자 필터 이중 안전)."""
    other = uuid4()
    txns = [
        _tx("buy", 100, "1000", date(2024, 1, 1)),
        PortfolioTransaction(
            id=uuid4(), user_id=_USER, code_lineage_id=other, side="buy",
            quantity=999, unit_price=Decimal("5"), trade_date=date(2024, 1, 2),
            fee=Decimal("0"), created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    ]
    pos = compute_position(txns, code_lineage_id=_LINEAGE)
    assert pos.quantity == 100  # 다른 종목 999 무시.
