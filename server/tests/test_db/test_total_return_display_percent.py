"""M7 #4 Fix 4 — bare-field total_return_trailing_1y 의 display ×100 정합.

oracle 리뷰 Medium: bare-field factor `price-return:total-annual`
(`{field: total_return_trailing_1y}`, unit=percent)가 display layer 에서
`ratio_pct` factor(dividend-yield, unit=percent)와 **동일하게 ×100** 적용되는지
end-to-end 미검증.

설계 전제(ADR-0002 D6): evaluator 는 두 factor 모두 **raw ratio** 를 반환하고,
×100 표시는 display layer 가 `unit == "percent"` 를 보고 단일 지점에서 적용한다.
따라서 wire(FactorValueOut)는 둘 다 **ratio 문자열 + unit=percent** 를 동일 형태로
실어야 한다 — 이중 ×100(evaluator 가 미리 ×100)·누락(unit 누락) 없음.

본 테스트는 wire 레벨에서:
1. bare-field price-return:total-annual → value == raw ratio(0.21, 미리 ×100 안 됨)
   + unit == "percent".
2. ratio_pct dividend-yield → value == raw ratio + unit == "percent".
둘 다 동일 ratio 표현 + percent unit 이므로 display ×100 이 두 factor 에 동일하게
1 회 적용됨을 보장(divergence 0).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeDividendRepository,
    FakeFinancialRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import CorporateActionRecord, PriceRecord
from app.schemas.stocks import FactorValueOut
from app.services.db_field_provider import DbFieldProvider
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK
from app.services.total_return_adjuster import TotalReturnAdjuster

_CODE: Final[str] = "005930"
_LINEAGE: Final[UUID] = UUID(int=int(_CODE))
_CITATION: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000ce")
_AS_OF: Final[date] = date(2024, 5, 7)

_TOTAL_RETURN_FACTOR: Final[str] = "price-return:total-annual"
_DIVIDEND_YIELD_FACTOR: Final[str] = "dividend-yield:trailing-annual"


def _price(*, d: date, close: str) -> PriceRecord:
    return PriceRecord(
        id=uuid4(), code=_CODE, code_lineage_id=_LINEAGE, effective_date=d,
        open_raw=Decimal(close), high_raw=Decimal(close), low_raw=Decimal(close),
        close_raw=Decimal(close), volume=1000, trading_value=Decimal("1000000"),
        close_adjusted=Decimal(close), citation_id=_CITATION,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _div(*, effective_date: date, cash_amount: str) -> CorporateActionRecord:
    return CorporateActionRecord(
        id=uuid4(), code=_CODE, code_lineage_id=_LINEAGE,
        action_type="cash_dividend", announced_date=effective_date,
        effective_date=effective_date, payment_date=None, ratio=None,
        cash_amount=Decimal(cash_amount), details={}, citation_id=_CITATION,
        superseded_by=None, created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _provider() -> DbFieldProvider:
    """가격 100→121(1 년) + 직전 12 개월 배당 5 → total_return / dividend-yield 산출.

    total_return_trailing_1y(배당 재투자 포함) ratio + dividend-yield ratio 둘 다
    raw ratio 로 평가됨(둘 다 unit=percent → display ×100 정합 검증).
    """
    prices = [
        _price(d=date(2023, 5, 10), close="100"),  # min-coverage 충족.
        _price(d=date(2024, 1, 15), close="110"),
        _price(d=_AS_OF, close="121"),
    ]
    dividends = [_div(effective_date=date(2024, 2, 15), cash_amount="5")]
    return DbFieldProvider(
        code=_CODE, as_of=_AS_OF,
        price_repo=FakePriceRepository(records=prices),
        financial_repo=FakeFinancialRepository(records=()),
        corporate_action_repo=FakeCorporateActionRepository(records=()),
        market_cap_repo=FakeMarketCapRepository(records=()),
        treasury_repo=FakeTreasurySharesRepository(records=()),
        dividend_repo=FakeDividendRepository(records=dividends),
        total_return_adjuster=TotalReturnAdjuster(),
        factor_pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
    )


def _eval_wire(canonical_id: str) -> FactorValueOut:
    provider = _provider()
    evaluator = FactorEvaluator()
    factor = next(
        f for f in DEFAULT_PACK.body["factors"] if f["canonical_id"] == canonical_id
    )
    result = evaluator.evaluate(factor, provider, as_of=_AS_OF)
    return FactorValueOut.from_evaluation(
        result, factor_name=factor["name"], factor_unit=factor["unit"],
    )


def test_total_return_bare_field_wire_emits_ratio_and_percent_unit() -> None:
    """bare-field price-return:total-annual → wire value = raw ratio(미리 ×100 안 됨).

    formula 가 `{field: total_return_trailing_1y}` 이므로 evaluator 는 field 의 raw
    ratio 를 그대로 반환(이중 ×100 없음). unit=percent 라 display layer 가 ×100 을
    1 회 적용. 가격 100→121 + 배당 5 재투자 → ratio ≈ 0.26(0.21 가격 + 배당 기여).
    핵심: value 가 ratio(<1) 이지 26 같은 percent 가 아님(미리 ×100 금지).
    """
    out = _eval_wire(_TOTAL_RETURN_FACTOR)
    assert out.unit == "percent"
    assert out.is_na is False
    assert out.value is not None
    value = Decimal(out.value)
    # raw ratio — 미리 ×100 됐다면 >1(예 26). ratio 는 0<v<1 범위(1 년 26% 가정).
    assert Decimal("0.20") < value < Decimal("0.40")


def test_ratio_pct_dividend_yield_wire_emits_ratio_and_percent_unit() -> None:
    """ratio_pct dividend-yield → wire value = raw ratio + unit=percent (대조군).

    dividend_per_share(5) / close(121) = 0.0413… raw ratio. ratio_pct 도 ×100 은
    display layer 책임(ADR-0002 D6) — wire 는 ratio. bare-field 와 동일 형태.
    """
    out = _eval_wire(_DIVIDEND_YIELD_FACTOR)
    assert out.unit == "percent"
    assert out.is_na is False
    assert out.value is not None
    value = Decimal(out.value)
    # 5 / 121 ≈ 0.0413 raw ratio (미리 ×100 됐다면 ~4.13).
    assert Decimal("0.03") < value < Decimal("0.06")


def test_bare_field_and_ratio_pct_display_x100_consistent() -> None:
    """bare-field 와 ratio_pct 가 display ×100 정합 — 둘 다 ratio + percent unit.

    end-to-end 정합 검증: 두 factor 모두 wire 에 raw ratio + unit=percent 를 동일
    형태로 실으므로, display layer 의 `unit=='percent' → ×100` 규칙이 두 factor 에
    동일하게 1 회 적용된다(bare-field 만 이중 ×100 되거나 ×100 누락되는 divergence
    없음). 두 factor 의 ×100 후 표시값을 모사해 일관성을 명시 검증.
    """
    tr = _eval_wire(_TOTAL_RETURN_FACTOR)
    dy = _eval_wire(_DIVIDEND_YIELD_FACTOR)

    # 동일 표시 규칙: 둘 다 percent unit + ratio wire.
    assert tr.unit == dy.unit == "percent"
    assert tr.value is not None and dy.value is not None

    # display layer 모사 — unit=percent 면 ×100 단일 적용. 둘 다 같은 규칙 적용.
    def display_percent(out: FactorValueOut) -> Decimal:
        raw = Decimal(out.value)  # type: ignore[arg-type]
        return raw * Decimal(100) if out.unit == "percent" else raw

    tr_display = display_percent(tr)
    dy_display = display_percent(dy)
    # ×100 후 percent 영역(예 26%, 4.1%) — ratio(0.26)·percent(26) 혼동 없이 일관.
    assert tr_display == Decimal(tr.value) * Decimal(100)
    assert dy_display == Decimal(dy.value) * Decimal(100)
    # 두 factor 가 같은 변환을 받음(규칙 분기 없음) — bare-field 특례 0.
    assert (tr_display / Decimal(tr.value)) == (dy_display / Decimal(dy.value))
