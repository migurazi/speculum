"""Portfolio Position 계산 service — M3 #4 거래내역 → 사실 회계 (ADR-0029 D2/D5).

거래내역(매수/매도)에서 종목별 **보유수량·평단(이동평균법)·원가(취득가액 합)·
실현손익**을 산출하는 **stateless·결정적** service. (선택)현재가가 주어지면
평가금액·평가손익도 산술 사실로 계산한다. corporate action(액면분할/무상증자)을
거래 레벨에 보정(D5)한다.

정체성 핵심 — 회계 ≠ 평가 분리(ADR-0029 D3):
    본 service 는 **순수 숫자 사실**만 반환한다. "수익/손실/양호" 같은 라벨,
    손익률 등급, 종목 순위·정렬, "더 담기/비중 조절" 등 행동 유도, 등락색 분기를
    일절 산출하지 않는다(§0 정체성 · §2.2 No Advice). 손익은 사실 숫자(realized_pnl
    / unrealized_pnl)로만 노출하고 판단·강조·색분기는 호출자(UI)도 차단(ADR-0007
    D2/D2.2 — grayscale 중립 톤). 본 service 의 책임은 산술의 정확성뿐이다.

세금 미결합 — 세전(税前)만(ADR-0029 D6):
    실현손익 = (매도가 − 평단)×수량 − fee. fee 는 사용자 입력 비용(수수료+세금
    합산 입력값, D1)일 뿐, 본 service 는 양도세 등 세금을 계산·결합하지 않는다.
    세금 계산(#5, ADR-0030)은 거래내역 위에서 별도.

알고리즘:
- **이동평균법 평단(average cost)**:
    매수 시 — 새 평단 = (기존 원가 + 매수금액 + 매수 fee) / (기존수량 + 매수수량).
      취득 비용(fee)을 원가에 산입(취득가액 합) → 평단에 반영.
    매도 시 — 평단 불변(이동평균법의 정의 — 매도는 평단을 바꾸지 않음). 보유수량·
      원가는 매도수량 비례 차감. 실현손익 = (매도단가 − 평단)×매도수량 − 매도 fee.
- **결정성** — 모든 산술은 Decimal(부동소수 잔차 0). 거래는 호출자가 결정적 순서
  (trade_date, created_at, id)로 정렬해 전달(repository 가 보장).
- **corporate action 보정(D5)** — `price_adjuster` 의 보정계수 산출 정신을 거래
  레벨에 적용. 거래일 **이후** 발효된 액면분할/무상증자의 누적 비율을 그 거래의
  수량·평단에 반영(수량 ×비율, 평단 ÷비율 — 원가·실현손익 불변). 분할 전 매수한
  주식이 분할 후 현재가와 정합되도록(§2.9 시계열 정합). merger/spin_off 는
  미보정(price_adjuster 일관 — detect-only)하고 보고(unadjusted_actions).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

from app.repositories.pit_protocols import CorporateActionRecord
from app.repositories.portfolio_repository import PortfolioTransaction

__all__ = [
    "PortfolioPositionError",
    "Position",
    "compute_position",
]

# 평단·평가 산술의 Decimal precision/rounding — price_adjuster 일관(결정성).
_DECIMAL_PRECISION: int = 28

# corporate action 보정 대상(거래 레벨) — 액면분할/무상증자/주식배당/액면병합.
# price_adjuster 의 "price-adjust" classification 과 동일 의미(수량·평단 비례 변동).
# merger/spin_off 는 미보정(detect-only) — price_adjuster._POLICY_MATRIX 일관.
_QUANTITY_ADJUST_TYPES: frozenset[str] = frozenset({
    "split",            # 액면분할 — 수량 ×split_ratio, 평단 ÷split_ratio.
    "reverse_split",    # 액면병합 — 수량 ÷merge_ratio, 평단 ×merge_ratio.
    "bonus_issue",      # 무상증자 — 수량 ×(1+bonus_ratio), 평단 ÷(1+bonus_ratio).
    "stock_dividend",   # 주식배당 — 무상증자와 동일 보정(ADR-0001 D1).
})

# 미보정(보고만) action — 합병/분할. price_adjuster 의 detect-only 와 일관.
_UNADJUSTED_TYPES: frozenset[str] = frozenset({
    "merger",
    "merger_absorption",
    "spin_off_personal",
    "spin_off_business",
})


class PortfolioPositionError(Exception):
    """position 계산 invariant 위반 — 잘못된 거래 순서, 음수 보유, CA ratio 등."""


@dataclass(frozen=True, slots=True)
class Position:
    """단일 종목의 position 계산 결과 — **순수 숫자 사실**(ADR-0029 D3).

    어떤 평가 라벨(수익/손실/등급/순위)도 포함하지 않는다. 손익은 사실 숫자
    (realized_pnl / unrealized_pnl)로만 노출하며, 판단·강조·색분기는 본 dataclass
    에 존재하지 않는다(회계 ≠ 평가 분리).

    Attributes:
        code_lineage_id: 종목 lineage.
        quantity: 현재 보유수량(주). CA 보정(D5) 반영 후. 0 = 전량 매도.
        avg_cost: 평단(이동평균법, 원/주). CA 보정 후. 보유수량 0 이면 0.
        total_cost: 현재 보유분의 원가 합(취득가액, 원). quantity × avg_cost.
        realized_pnl: 누적 실현손익(원, 세전). 매도 시 (매도가−평단)×수량 − fee.
            CA 보정과 무관(매도 시점 평단 기준의 사실).
        market_value: (현재가 입력 시)평가금액 = 현재가 × 보유수량. None = 현재가
            미조회.
        unrealized_pnl: (현재가 입력 시)평가손익(세전) = market_value − total_cost.
            None = 현재가 미조회. **평가 라벨 아님 — 산술 사실**(ADR-0029 D3).
        unadjusted_actions: 미보정(merger/spin_off) action 의 effective_date list
            — 보유수량/평단에 반영 안 됨(보고만, price_adjuster 일관 §2.9).
    """

    code_lineage_id: UUID
    quantity: int
    avg_cost: Decimal
    total_cost: Decimal
    realized_pnl: Decimal
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    unadjusted_actions: tuple[date, ...]


def _ca_factor(action: CorporateActionRecord) -> Decimal:
    """corporate action 의 **수량 배율**(거래 레벨 보정계수, D5).

    배율 r 의 의미: 거래일 이전 보유 1 주가 action 이후 r 주가 된다(평단은 ÷r).
    price_adjuster 의 가격 보정계수(과거 가격 ÷r)와 역수 관계 — 가격은 줄고
    수량은 늘어 시가총액(보유가치)은 보존된다.

    - split: r = split_ratio (예: 1→50 분할 → 50).
    - reverse_split: r = 1 / merge_ratio (예: 10→1 병합 → 0.1).
    - bonus_issue / stock_dividend: r = 1 + bonus_ratio (예: 20% 무상 → 1.2).

    details 우선순위는 price_adjuster 와 일관(details 값 > action.ratio).
    """
    details = action.details
    if action.action_type == "split":
        raw = details.get("split_ratio")
        ratio = _as_decimal(raw if raw is not None else action.ratio)
        if ratio <= 0:
            raise PortfolioPositionError(
                f"split_ratio must be positive (action {action.id}): {ratio}",
            )
        return ratio
    if action.action_type == "reverse_split":
        raw = details.get("merge_ratio")
        ratio = _as_decimal(raw if raw is not None else action.ratio)
        if ratio <= 0:
            raise PortfolioPositionError(
                f"merge_ratio must be positive (action {action.id}): {ratio}",
            )
        return Decimal(1) / ratio
    # bonus_issue / stock_dividend — 무상 비율(ADR-0001 D1 동일 보정).
    raw = details.get("bonus_ratio")
    if raw is None:
        raw = details.get("dividend_ratio")
    if raw is None:
        raw = action.ratio
    bonus = _as_decimal(raw)
    if bonus < 0:
        raise PortfolioPositionError(
            f"bonus/dividend ratio must be >= 0 (action {action.id}): {bonus}",
        )
    return Decimal(1) + bonus


def _as_decimal(value: object) -> Decimal:
    """numeric → Decimal(price_adjuster._as_decimal 정신 — float 잔차 회피)."""
    if value is None:
        raise PortfolioPositionError("required corporate action ratio is missing")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, str)):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    raise PortfolioPositionError(
        f"cannot convert {value!r} ({type(value).__name__}) to Decimal",
    )


def compute_position(
    transactions: Sequence[PortfolioTransaction],
    *,
    code_lineage_id: UUID,
    corporate_actions: Sequence[CorporateActionRecord] = (),
    current_price: Decimal | None = None,
) -> Position:
    """거래내역(단일 종목) → Position 사실 회계(stateless·결정적, ADR-0029 D2/D5).

    Args:
        transactions: 해당 종목의 거래내역. 호출자가 결정적 순서(trade_date,
            created_at, id asc)로 정렬해 전달(repository 가 보장). 다른 종목이
            섞여 있으면 code_lineage_id 로 필터한다(방어).
        code_lineage_id: 계산 대상 종목 lineage.
        corporate_actions: 해당 종목의 corporate action(D5 보정용). 액면분할/
            무상증자 등은 거래 레벨에 보정, merger/spin_off 는 미보정(보고).
        current_price: (선택)현재가(원/주). 주어지면 평가금액·평가손익을 산술
            사실로 계산. None 이면 market_value/unrealized_pnl = None.

    Returns:
        Position — 보유수량·평단·원가·실현손익 + (현재가 시)평가. **숫자 사실만**.

    Raises:
        PortfolioPositionError: 보유수량보다 많은 매도(음수 보유), 잘못된 CA ratio.
    """
    with localcontext() as ctx:
        ctx.prec = _DECIMAL_PRECISION
        ctx.rounding = ROUND_HALF_EVEN

        # 이동평균법 누적 상태.
        quantity = 0                 # 보유수량(주).
        total_cost = Decimal(0)      # 보유분 원가 합(취득가액, fee 산입).
        realized_pnl = Decimal(0)    # 누적 실현손익(세전).

        for tx in transactions:
            # 방어 — 다른 종목 거래 무시(호출자가 필터해 전달하나 이중 안전).
            if tx.code_lineage_id != code_lineage_id:
                continue
            qty = Decimal(tx.quantity)
            if tx.side == "buy":
                # 매수 — 원가에 매수금액 + fee 산입(취득가액 합), 보유수량 증가.
                # 평단은 (누적 원가)/(누적 수량)로 자연 도출(이동평균법).
                total_cost += tx.unit_price * qty + tx.fee
                quantity += tx.quantity
            elif tx.side == "sell":
                # 매도 — 평단 불변(이동평균법). 실현손익 = (매도가 − 평단)×수량 − fee.
                if tx.quantity > quantity:
                    raise PortfolioPositionError(
                        f"sell quantity {tx.quantity} exceeds holding {quantity} "
                        f"(lineage {code_lineage_id}, trade_date {tx.trade_date}) "
                        f"— 입력 실수 의심(역분개/삭제로 정정)",
                    )
                avg_cost = (
                    total_cost / Decimal(quantity) if quantity > 0 else Decimal(0)
                )
                realized_pnl += (tx.unit_price - avg_cost) * qty - tx.fee
                # 보유분 원가는 매도수량 비례 차감(남은 수량 × 평단).
                total_cost -= avg_cost * qty
                quantity -= tx.quantity
            # side 값은 repository 가 검증 — 그 외는 도달 불가(방어 생략).

        # corporate action 보정(D5) — 거래일 이후 발효된 액면분할/무상증자의 누적
        # 배율을 **현재 보유수량·평단**에 반영. 보유분 전체가 동일 종목이고 모든
        # 거래가 동일 CA 영향을 받으므로(거래일 이후 발효 CA 만 보정 대상),
        # 보유분 총 배율 = 모든 보정 대상 CA 배율의 곱. 원가·실현손익은 불변
        # (수량×배율, 평단÷배율 → 원가 = 수량×평단 보존).
        cumulative_factor = Decimal(1)
        unadjusted_actions: list[date] = []
        # 마지막 거래일 — 그 이후 발효된 CA 만 현재 보유분에 의미 있음. 거래가
        # 없으면(보유 0) 보정 대상 없음.
        last_trade_date = _last_relevant_trade_date(transactions, code_lineage_id)
        for action in sorted(corporate_actions, key=lambda a: a.effective_date):
            if action.action_type in _UNADJUSTED_TYPES:
                # merger/spin_off — 미보정(보고만, price_adjuster detect-only 일관).
                if last_trade_date is not None and action.effective_date > last_trade_date:
                    unadjusted_actions.append(action.effective_date)
                continue
            if action.action_type not in _QUANTITY_ADJUST_TYPES:
                continue  # cash_dividend/treasury 등 — 수량/평단 무관(보정 대상 X).
            # 거래일(마지막 보유 형성 거래) 이후 발효된 CA 만 현재 보유분에 반영.
            if last_trade_date is None or action.effective_date <= last_trade_date:
                continue
            cumulative_factor *= _ca_factor(action)

        if quantity > 0 and cumulative_factor != Decimal(1):
            # 수량 ×배율(정수화 — KRX 일반주 fractional 미지원. 무상/분할 비율은
            # 정수 주를 산출하나, 비정수 배율의 단주는 KRX 가 현금 정산 → 본 사실
            # 회계는 정수 보유만 반영, 단주 현금은 별도 거래로 사용자 입력).
            new_quantity_dec = Decimal(quantity) * cumulative_factor
            new_quantity = int(new_quantity_dec)
            quantity = new_quantity
            # total_cost 는 불변(보정은 단가만 분배) — 평단 = total_cost/수량 으로
            # 자연 ÷배율 효과. 단, 정수화로 수량이 약간 달라질 수 있으므로 평단은
            # total_cost/quantity 로 재산출(원가 보존 우선).

        # 평단 — 현재 보유분 원가 / 보유수량(보유 0 이면 0).
        avg_cost = (
            total_cost / Decimal(quantity) if quantity > 0 else Decimal(0)
        )

        # 평가(현재가 입력 시) — 산술 사실. 평가 라벨 아님(ADR-0029 D3).
        market_value: Decimal | None = None
        unrealized_pnl: Decimal | None = None
        if current_price is not None and quantity > 0:
            market_value = current_price * Decimal(quantity)
            unrealized_pnl = market_value - total_cost
        elif current_price is not None:
            # 보유 0 — 평가금액 0, 평가손익 0(사실).
            market_value = Decimal(0)
            unrealized_pnl = Decimal(0)

        return Position(
            code_lineage_id=code_lineage_id,
            quantity=quantity,
            avg_cost=avg_cost,
            total_cost=total_cost,
            realized_pnl=realized_pnl,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
            unadjusted_actions=tuple(unadjusted_actions),
        )


def _last_relevant_trade_date(
    transactions: Sequence[PortfolioTransaction],
    code_lineage_id: UUID,
) -> date | None:
    """현재 보유분에 영향 준 마지막 거래일 — CA 보정 기준점.

    그 이후 발효된 CA 만 현재 보유수량·평단에 반영한다(거래일 이전 보유분은 이미
    그 거래 단가가 분할 후 가격이라 가정 — 사용자가 분할 후 가격으로 입력). 거래가
    없으면(보유 0) None → 보정 대상 없음.
    """
    relevant = [
        t.trade_date for t in transactions if t.code_lineage_id == code_lineage_id
    ]
    return max(relevant) if relevant else None
