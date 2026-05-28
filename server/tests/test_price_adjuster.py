"""price_adjuster 단위 테스트.

테스트 매트릭스:
1. 단순 split 50:1 — 과거 가격 / 50
2. Bonus issue 20% — factor 1/1.2
3. Reverse split 10:1 — 과거 가격 × 10
4. Rights issue — details theoretical_ex_price 사용
5. Rights issue cross-check — recompute mismatch 시 InconsistentTheoreticalPriceError
6. Cash dividend — 보정 X (ignore)
7. Treasury — 보정 X (ignore)
8. Merger detect-only — unadjusted_jumps 노출 (ADR-0009 D7)
9. 누적 (split + bonus) — factor 곱셈
10. as_of cutoff — effective_date > as_of 미적용
11. Identity adjustment — 사건 0 개 → adjusted == raw + is_identity_adjustment True
12. 정정공시 chain — PIT Enforcer 위임
13. 알 수 없는 action_type → AdjusterDataError
14. 중복 일자 / 다중 code → AdjusterDataError
15. 정책 hash 결정성 + Screen Run freeze 입력
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

import pytest

from app.repositories.pit_protocols import CorporateActionRecord, PriceRecord
from app.services.price_adjuster import (
    ADJUSTMENT_POLICY_VERSION,
    CORPORATE_ACTION_POLICY_VERSION,
    POLICY_CONTENT_HASH,
    POLICY_LABEL,
    AdjusterDataError,
    AdjusterError,
    InconsistentTheoreticalPriceError,
    PriceAdjuster,
)

_DUMMY_CITATION: Final[UUID] = UUID("00000000-0000-0000-0000-00000000ffff")
_DUMMY_LINEAGE: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000aa")
_CODE: Final[str] = "005930"


def _price(
    *, d: date, close: int | float | str = 50000, volume: int = 1_000_000
) -> PriceRecord:
    """OHLC 모두 close 와 같은 단순 fixture."""
    p = Decimal(str(close))
    return PriceRecord(
        id=uuid4(), code=_CODE, code_lineage_id=_DUMMY_LINEAGE,
        effective_date=d,
        open_raw=p, high_raw=p, low_raw=p, close_raw=p,
        volume=volume,
        close_adjusted=p,  # placeholder — Adjuster 가 재계산.
        citation_id=_DUMMY_CITATION,
        created_at=datetime(d.year, d.month, d.day, 17, 0, tzinfo=UTC),
    )


def _action(
    *,
    action_type: str,
    announced: date,
    effective: date,
    details: dict | None = None,
    ratio: Decimal | None = None,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
) -> CorporateActionRecord:
    return CorporateActionRecord(
        id=record_id or uuid4(),
        code=_CODE, code_lineage_id=_DUMMY_LINEAGE,
        action_type=action_type,
        announced_date=announced,
        effective_date=effective,
        payment_date=None,
        ratio=ratio,
        cash_amount=None,
        details=details or {},
        citation_id=_DUMMY_CITATION,
        superseded_by=superseded_by,
        created_at=datetime(announced.year, announced.month, announced.day,
                            9, 0, tzinfo=UTC),
    )


# =============================================================================
# 1. 단순 split — 과거 가격 / 50
# =============================================================================

def test_split_adjusts_past_prices() -> None:
    """50:1 액면분할 — 과거 가격 1/50, 발효일 이후는 raw 그대로."""
    adjuster = PriceAdjuster()
    prices = [
        _price(d=date(2024, 4, 1), close=50000),  # before split — adjusted to 1000
        _price(d=date(2024, 4, 14), close=49000),  # before split — adjusted to 980
        _price(d=date(2024, 4, 15), close=1000),  # split effective — raw == adjusted
        _price(d=date(2024, 4, 16), close=1050),
    ]
    actions = [_action(
        action_type="split", announced=date(2024, 3, 1),
        effective=date(2024, 4, 15),
        details={"split_ratio": 50},
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 4, 20))

    by_date = {r.date: r for r in result.adjusted}
    assert by_date[date(2024, 4, 1)].close_adjusted == Decimal("1000")
    assert by_date[date(2024, 4, 14)].close_adjusted == Decimal("980")
    assert by_date[date(2024, 4, 15)].close_adjusted == Decimal("1000")
    assert by_date[date(2024, 4, 16)].close_adjusted == Decimal("1050")
    # raw 보존 검증
    assert by_date[date(2024, 4, 1)].close_raw == Decimal("50000")


def test_split_uses_action_ratio_when_details_missing() -> None:
    """details 없으면 action.ratio fallback."""
    adjuster = PriceAdjuster()
    prices = [
        _price(d=date(2024, 4, 1), close=10000),
        _price(d=date(2024, 4, 15), close=1000),
    ]
    actions = [_action(
        action_type="split", announced=date(2024, 3, 1),
        effective=date(2024, 4, 15), ratio=Decimal("10"),
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 4, 20))
    by_date = {r.date: r for r in result.adjusted}
    assert by_date[date(2024, 4, 1)].close_adjusted == Decimal("1000")


def test_split_rejects_non_positive_ratio() -> None:
    adjuster = PriceAdjuster()
    actions = [_action(
        action_type="split", announced=date(2024, 3, 1),
        effective=date(2024, 4, 15),
        details={"split_ratio": 0},
    )]
    with pytest.raises(AdjusterDataError, match="positive"):
        adjuster.adjust(
            [_price(d=date(2024, 4, 1))], actions, as_of=date(2024, 4, 20)
        )


# =============================================================================
# 2. Bonus issue 20% — factor 1/1.2
# =============================================================================

def test_bonus_issue_adjusts_with_one_over_one_plus_ratio() -> None:
    adjuster = PriceAdjuster()
    prices = [
        _price(d=date(2024, 5, 1), close=12000),  # before — adjusted = 10000
        _price(d=date(2024, 5, 15), close=10000),  # ex-bonus
    ]
    actions = [_action(
        action_type="bonus_issue", announced=date(2024, 4, 1),
        effective=date(2024, 5, 15),
        details={"bonus_ratio": "0.2"},
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 5, 20))
    by_date = {r.date: r for r in result.adjusted}
    # 12000 / 1.2 = 10000
    assert by_date[date(2024, 5, 1)].close_adjusted == Decimal("10000")
    assert by_date[date(2024, 5, 15)].close_adjusted == Decimal("10000")


def test_stock_dividend_uses_same_logic_as_bonus_issue() -> None:
    """주식배당과 무상증자는 보정 효과 동일 (ADR-0001 D1 note)."""
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 5, 1), close=12000),
              _price(d=date(2024, 5, 15), close=10000)]
    actions = [_action(
        action_type="stock_dividend", announced=date(2024, 4, 1),
        effective=date(2024, 5, 15),
        details={"dividend_ratio": "0.2"},
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 5, 20))
    by_date = {r.date: r for r in result.adjusted}
    assert by_date[date(2024, 5, 1)].close_adjusted == Decimal("10000")


# =============================================================================
# 3. Reverse split 10:1 — 과거 가격 × 10
# =============================================================================

def test_reverse_split_multiplies_past_prices() -> None:
    """10:1 액면병합 — 과거 1000원이 10000원으로 표시."""
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 6, 1), close=1000),
              _price(d=date(2024, 6, 15), close=10000)]
    actions = [_action(
        action_type="reverse_split", announced=date(2024, 5, 1),
        effective=date(2024, 6, 15),
        details={"merge_ratio": 10},
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 6, 20))
    by_date = {r.date: r for r in result.adjusted}
    assert by_date[date(2024, 6, 1)].close_adjusted == Decimal("10000")


# =============================================================================
# 4. Rights issue — details theoretical_ex_price 사용
# =============================================================================

def test_rights_issue_uses_declared_theoretical_price() -> None:
    """KRX 공식 이론가 (details["theoretical_ex_price"]) 를 1차 source."""
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 7, 1), close=30000),
              _price(d=date(2024, 7, 15), close=28500)]
    actions = [_action(
        action_type="rights_issue", announced=date(2024, 6, 1),
        effective=date(2024, 7, 15),
        details={
            "subscription_ratio": "0.2",  # 신주발행수 / 기존주식수
            "subscription_price": "15000",
            "theoretical_ex_price": "27500",  # KRX 공식
            "previous_close": "30000",
        },
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 7, 20))
    by_date = {r.date: r for r in result.adjusted}
    # factor = 27500 / 30000 ≈ 0.9167. 30000 × 0.9167 = 27500.
    expected = Decimal("30000") * (Decimal("27500") / Decimal("30000"))
    assert by_date[date(2024, 7, 1)].close_adjusted == expected


# =============================================================================
# 5. Rights issue cross-check — mismatch
# =============================================================================

def test_rights_issue_raises_on_theoretical_price_mismatch() -> None:
    """declared (10000) vs recomputed ((30000 + 15000 × 0.2)/1.2 = 27500) → 차이 ~63%."""
    adjuster = PriceAdjuster()
    actions = [_action(
        action_type="rights_issue", announced=date(2024, 6, 1),
        effective=date(2024, 7, 15),
        details={
            "subscription_ratio": "0.2",
            "subscription_price": "15000",
            "theoretical_ex_price": "10000",  # 의도적 mismatch
            "previous_close": "30000",
        },
    )]
    with pytest.raises(InconsistentTheoreticalPriceError) as exc:
        adjuster.adjust(
            [_price(d=date(2024, 7, 1))], actions, as_of=date(2024, 7, 20)
        )
    assert exc.value.declared == Decimal("10000")


def test_rights_issue_within_tolerance_passes() -> None:
    """declared 가 recomputed 와 0.5% 이내면 통과."""
    adjuster = PriceAdjuster()
    # recomputed = (30000 + 15000 × 0.2) / 1.2 = 33000/1.2 = 27500
    # declared = 27600 — 0.36% 차이. tolerance 0.5% 이내.
    actions = [_action(
        action_type="rights_issue", announced=date(2024, 6, 1),
        effective=date(2024, 7, 15),
        details={
            "subscription_ratio": "0.2",
            "subscription_price": "15000",
            "theoretical_ex_price": "27600",
            "previous_close": "30000",
        },
    )]
    # raise X
    result = adjuster.adjust(
        [_price(d=date(2024, 7, 1))], actions, as_of=date(2024, 7, 20)
    )
    assert result.events[0].classification == "price-adjust"


# =============================================================================
# 6. Cash dividend — 보정 X
# =============================================================================

def test_cash_dividend_does_not_adjust_prices() -> None:
    """현금배당은 ADR-0001 D1 의 rights-only 정책에서 보정 미대상."""
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 5, 1), close=50000),
              _price(d=date(2024, 5, 15), close=48500)]  # 배당락 1500
    actions = [_action(
        action_type="cash_dividend", announced=date(2024, 4, 1),
        effective=date(2024, 5, 15),
        details={"per_share": 1500, "type": "annual"},
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 5, 20))
    by_date = {r.date: r for r in result.adjusted}
    # raw == adjusted (no factor).
    assert by_date[date(2024, 5, 1)].close_adjusted == Decimal("50000")
    assert by_date[date(2024, 5, 15)].close_adjusted == Decimal("48500")
    # Event 는 ignore classification 으로 기록.
    assert len(result.events) == 1
    assert result.events[0].classification == "ignore"


# =============================================================================
# 7. Treasury — 보정 X
# =============================================================================

def test_treasury_purchase_does_not_adjust_prices() -> None:
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 8, 1), close=70000)]
    actions = [_action(
        action_type="treasury_purchase", announced=date(2024, 7, 1),
        effective=date(2024, 7, 15),
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 8, 5))
    assert result.adjusted[0].close_adjusted == Decimal("70000")
    assert result.events[0].classification == "ignore"


# =============================================================================
# 8. Merger detect-only — unadjusted_jumps 노출 (ADR-0009 D7)
# =============================================================================

def test_merger_is_detect_only_and_appears_in_unadjusted_jumps() -> None:
    """M0 에서 merger 는 보정 미적용. raw 시계열의 점프가 unadjusted_jumps 로 노출."""
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 8, 1), close=10000),
              _price(d=date(2024, 8, 30), close=15000)]
    actions = [_action(
        action_type="merger", announced=date(2024, 7, 1),
        effective=date(2024, 8, 30),
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 9, 5))
    # raw == adjusted (보정 미적용)
    assert result.adjusted[0].close_adjusted == result.adjusted[0].close_raw
    # unadjusted_jumps 에 effective_date 포함
    assert date(2024, 8, 30) in result.unadjusted_jumps
    # Event classification
    assert result.events[0].classification == "detect-only-merger"


# =============================================================================
# 9. 누적 — split 50:1 + bonus 20%
# =============================================================================

def test_chained_actions_accumulate_factors() -> None:
    """split 50:1 (4/15) 후 bonus 20% (5/15) — 4/1 가격의 factor = (1/50) × (1/1.2).

    50000 × (1/50) × (1/1.2) = 1000 / 1.2 = 833.333...
    """
    adjuster = PriceAdjuster()
    prices = [
        _price(d=date(2024, 4, 1), close=50000),
        _price(d=date(2024, 4, 15), close=1000),  # post-split
        _price(d=date(2024, 5, 15), close=833),  # post-bonus (raw)
    ]
    actions = [
        _action(action_type="split", announced=date(2024, 3, 1),
                effective=date(2024, 4, 15), details={"split_ratio": 50}),
        _action(action_type="bonus_issue", announced=date(2024, 4, 20),
                effective=date(2024, 5, 15), details={"bonus_ratio": "0.2"}),
    ]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 5, 20))
    by_date = {r.date: r for r in result.adjusted}
    # 4/1 가격: 50000 × (1/50) × (1/1.2) = 50000/60 = 833.333...
    # Decimal 28 자리 정밀도에서 두 번 나눗셈의 마지막 자리는 ROUND_HALF_EVEN 의
    # 누적 영향으로 한 번에 계산한 값과 1 ulp 차이 가능 → 10 자리로 quantize 비교.
    q = Decimal("0.0000000001")
    expected_apr_1 = (Decimal("50000") / Decimal("60")).quantize(q)
    assert by_date[date(2024, 4, 1)].close_adjusted.quantize(q) == expected_apr_1
    # 4/15 가격: split 이후이지만 bonus 이전 → bonus factor 만 적용
    expected_apr_15 = (Decimal("1000") / Decimal("1.2")).quantize(q)
    assert by_date[date(2024, 4, 15)].close_adjusted.quantize(q) == expected_apr_15
    # 5/15 가격: 모든 보정 이후 — raw 그대로
    assert by_date[date(2024, 5, 15)].close_adjusted == Decimal("833")


# =============================================================================
# 10. as_of cutoff — effective_date > as_of 미적용
# =============================================================================

def test_action_not_yet_effective_is_recorded_but_not_applied() -> None:
    """as_of = 2024-04-10 에서 split effective_date=2024-04-15 는 아직 미발효."""
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 4, 1), close=50000)]
    actions = [_action(
        action_type="split", announced=date(2024, 3, 1),
        effective=date(2024, 4, 15), details={"split_ratio": 50},
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 4, 10))
    # 보정 미적용 — raw 그대로
    assert result.adjusted[0].close_adjusted == Decimal("50000")
    # 그러나 event 는 announced-not-effective 로 기록
    assert len(result.events) == 1
    assert result.events[0].classification == "announced-not-effective"


def test_action_announced_after_as_of_is_excluded_by_pit() -> None:
    """announced > as_of 이면 PIT Enforcer 가 제외 — 그 시점에 알 수 없는 사건."""
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 4, 1), close=50000)]
    # announced=2024-04-05, as_of=2024-04-03 → 미공시.
    actions = [_action(
        action_type="split", announced=date(2024, 4, 5),
        effective=date(2024, 4, 15), details={"split_ratio": 50},
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 4, 3))
    assert result.is_identity_adjustment
    assert result.events == ()


# =============================================================================
# 11. Identity adjustment
# =============================================================================

def test_identity_adjustment_when_no_actions() -> None:
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 4, 1), close=50000)]
    result = adjuster.adjust(prices, [], as_of=date(2024, 4, 20))
    assert result.is_identity_adjustment
    assert result.adjusted[0].close_adjusted == Decimal("50000")
    assert result.events == ()
    assert result.unadjusted_jumps == ()


def test_identity_adjustment_with_only_ignored_actions() -> None:
    """cash_dividend 만 있어도 is_identity_adjustment True (price-adjust 0 개)."""
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 5, 15), close=48500)]
    actions = [_action(
        action_type="cash_dividend", announced=date(2024, 4, 1),
        effective=date(2024, 5, 15),
        details={"per_share": 1500},
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 5, 20))
    assert result.is_identity_adjustment


# =============================================================================
# 12. 정정공시 chain — PIT Enforcer 위임
# =============================================================================

def test_supersede_chain_uses_corrected_action() -> None:
    """v1 (split 10:1) → v2 정정 (split 8:1). announced 시점 이후의 as_of 는 v2 사용."""
    adjuster = PriceAdjuster()
    v1_id = UUID("00000000-0000-0000-0000-0000000000a1")
    v2_id = UUID("00000000-0000-0000-0000-0000000000a2")
    actions = [
        _action(
            action_type="split", announced=date(2024, 3, 1),
            effective=date(2024, 4, 15),
            details={"split_ratio": 10},  # 옛 비율
            superseded_by=v2_id, record_id=v1_id,
        ),
        _action(
            action_type="split", announced=date(2024, 4, 5),  # 정정 발표
            effective=date(2024, 4, 15),
            details={"split_ratio": 8},  # 정정된 비율
            record_id=v2_id,
        ),
    ]
    prices = [_price(d=date(2024, 4, 1), close=80000),
              _price(d=date(2024, 4, 15), close=10000)]

    # as_of=2024-04-20 → 정정 announce 됨 → v2 사용 (factor 1/8).
    result = adjuster.adjust(prices, actions, as_of=date(2024, 4, 20))
    assert result.adjusted[0].close_adjusted == Decimal("10000")  # 80000 / 8
    # Event 는 1 개 (v2 만 active).
    price_adjust_events = [e for e in result.events if e.classification == "price-adjust"]
    assert len(price_adjust_events) == 1


def test_supersede_chain_uses_old_action_when_correction_not_yet_announced() -> None:
    """as_of=2024-04-02 → 정정공시 아직 announced X → v1 (옛 비율) 사용."""
    adjuster = PriceAdjuster()
    v1_id = UUID("00000000-0000-0000-0000-0000000000a1")
    v2_id = UUID("00000000-0000-0000-0000-0000000000a2")
    actions = [
        _action(
            action_type="split", announced=date(2024, 3, 1),
            effective=date(2024, 4, 15),
            details={"split_ratio": 10},
            superseded_by=v2_id, record_id=v1_id,
        ),
        _action(
            action_type="split", announced=date(2024, 4, 5),
            effective=date(2024, 4, 15),
            details={"split_ratio": 8},
            record_id=v2_id,
        ),
    ]
    # as_of=2024-04-02 < v2.announced (2024-04-05) → v1 active.
    # 단 effective_date=2024-04-15 > as_of → 발효 전 → 보정 미적용 (factor=1).
    prices = [_price(d=date(2024, 4, 1), close=80000)]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 4, 2))
    # 발효 전이므로 보정 미적용 — but event 는 announced-not-effective 로 기록.
    assert result.adjusted[0].close_adjusted == Decimal("80000")
    assert result.events[0].action_id == v1_id  # v1 active


# =============================================================================
# 13. 알 수 없는 action_type
# =============================================================================

def test_unknown_action_type_raises() -> None:
    adjuster = PriceAdjuster()
    actions = [_action(
        action_type="invented_action", announced=date(2024, 1, 1),
        effective=date(2024, 1, 15),
    )]
    with pytest.raises(AdjusterDataError, match="unknown action_type"):
        adjuster.adjust(
            [_price(d=date(2024, 1, 1))], actions, as_of=date(2024, 2, 1)
        )


# =============================================================================
# 14. 입력 데이터 invariant — 중복 / 다중 code
# =============================================================================

def test_duplicate_price_dates_raises() -> None:
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 4, 1)), _price(d=date(2024, 4, 1))]
    with pytest.raises(AdjusterDataError, match="duplicate"):
        adjuster.adjust(prices, [], as_of=date(2024, 4, 20))


def test_multiple_codes_in_price_list_raises() -> None:
    adjuster = PriceAdjuster()
    p1 = _price(d=date(2024, 4, 1))
    p2_other = PriceRecord(
        id=uuid4(), code="000660", code_lineage_id=_DUMMY_LINEAGE,
        effective_date=date(2024, 4, 2),
        open_raw=Decimal("100"), high_raw=Decimal("100"),
        low_raw=Decimal("100"), close_raw=Decimal("100"),
        volume=1, close_adjusted=Decimal("100"),
        citation_id=_DUMMY_CITATION,
        created_at=datetime(2024, 4, 2, tzinfo=UTC),
    )
    with pytest.raises(AdjusterDataError, match="multiple codes"):
        adjuster.adjust([p1, p2_other], [], as_of=date(2024, 4, 20))


def test_empty_prices_returns_empty_series() -> None:
    adjuster = PriceAdjuster()
    result = adjuster.adjust([], [], as_of=date(2024, 4, 20))
    assert tuple(result.adjusted) == ()
    assert result.is_identity_adjustment


# =============================================================================
# 15. 정책 hash + freeze
# =============================================================================

def test_policy_content_hash_is_stable() -> None:
    """모듈 reload 또는 instance 변경과 무관하게 같은 hash."""
    a1 = PriceAdjuster()
    a2 = PriceAdjuster()
    assert a1.policy_content_hash == a2.policy_content_hash == POLICY_CONTENT_HASH


def test_policy_label_matches_adr_format() -> None:
    """ADR-0001 D6 의 tooltip 형식."""
    assert POLICY_LABEL == "v1.0:rights-only,theoretical"


def test_policy_versions_constants() -> None:
    assert ADJUSTMENT_POLICY_VERSION == "1.0"
    assert CORPORATE_ACTION_POLICY_VERSION == "1.0"


def test_policy_content_hash_format() -> None:
    """sha256:<64hex> 형식 — factor_pack / krx_calendar 와 동일."""
    assert POLICY_CONTENT_HASH.startswith("sha256:")
    assert len(POLICY_CONTENT_HASH) == len("sha256:") + 64


def test_adjusted_series_carries_policy_hash() -> None:
    """결과 dataclass 가 Screen Run snapshot 의 freeze 입력 보존."""
    adjuster = PriceAdjuster()
    result = adjuster.adjust(
        [_price(d=date(2024, 4, 1))], [], as_of=date(2024, 4, 20)
    )
    assert result.policy_content_hash == POLICY_CONTENT_HASH
    assert result.adj_policy_label == POLICY_LABEL


# =============================================================================
# 16. 결과 dataclass + 예외 계층
# =============================================================================

def test_adjusted_price_series_is_frozen() -> None:
    """immutable result — Screen Run freeze 보장."""
    import dataclasses
    adjuster = PriceAdjuster()
    result = adjuster.adjust(
        [_price(d=date(2024, 4, 1))], [], as_of=date(2024, 4, 20)
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.code = "tampered"  # type: ignore[misc]


def test_error_hierarchy() -> None:
    assert issubclass(AdjusterDataError, AdjusterError)
    assert issubclass(InconsistentTheoreticalPriceError, AdjusterError)


def test_pit_enforcer_dependency_injection() -> None:
    """DI 친화 — 외부 PITEnforcer 인스턴스 주입 가능."""
    from app.services.pit_enforcer import PITEnforcer
    enforcer = PITEnforcer(max_chain_depth=50)
    adjuster = PriceAdjuster(pit_enforcer=enforcer)
    assert adjuster.pit_enforcer is enforcer


# =============================================================================
# 17. OHLC 전체 컬럼 보정 (close 외에도 open/high/low)
# =============================================================================

def test_all_ohlc_columns_get_adjusted() -> None:
    """단일 factor 가 OHLC 4 컬럼 모두에 동일하게 적용."""
    adjuster = PriceAdjuster()
    p = PriceRecord(
        id=uuid4(), code=_CODE, code_lineage_id=_DUMMY_LINEAGE,
        effective_date=date(2024, 4, 1),
        open_raw=Decimal("50000"), high_raw=Decimal("51000"),
        low_raw=Decimal("49500"), close_raw=Decimal("50500"),
        volume=1_000_000, close_adjusted=Decimal("50500"),
        citation_id=_DUMMY_CITATION,
        created_at=datetime(2024, 4, 1, 17, 0, tzinfo=UTC),
    )
    actions = [_action(
        action_type="split", announced=date(2024, 3, 1),
        effective=date(2024, 4, 15), details={"split_ratio": 50},
    )]
    result = adjuster.adjust([p], actions, as_of=date(2024, 4, 20))
    rec = result.adjusted[0]
    assert rec.open_adjusted == Decimal("1000")
    assert rec.high_adjusted == Decimal("1020")
    assert rec.low_adjusted == Decimal("990")
    assert rec.close_adjusted == Decimal("1010")


# =============================================================================
# 18. oracle 2 차 리뷰 회귀 — Critical 항목별
# =============================================================================

def test_invalid_decimal_in_details_raises_adjuster_data_error() -> None:
    """oracle 2 차 C4 — InvalidOperation 이 AdjusterDataError 로 wrap."""
    adjuster = PriceAdjuster()
    actions = [_action(
        action_type="split", announced=date(2024, 3, 1),
        effective=date(2024, 4, 15),
        details={"split_ratio": "not-a-number"},
    )]
    with pytest.raises(AdjusterDataError, match="invalid numeric"):
        adjuster.adjust(
            [_price(d=date(2024, 4, 1))], actions, as_of=date(2024, 4, 20)
        )


def test_same_effective_date_split_and_bonus_apply_both() -> None:
    """oracle 2 차 C2 — 같은 effective_date 의 split + bonus 동시 발효.

    raw 4/1 가격: split 50:1 + bonus 0.2 둘 다 4/15 발효.
    4/1 의 factor = (1/50) × (1/1.2) = 1/60.
    """
    adjuster = PriceAdjuster()
    prices = [
        _price(d=date(2024, 4, 1), close=60000),
        _price(d=date(2024, 4, 15), close=1000),
    ]
    actions = [
        _action(action_type="split", announced=date(2024, 3, 1),
                effective=date(2024, 4, 15), details={"split_ratio": 50}),
        _action(action_type="bonus_issue", announced=date(2024, 3, 5),
                effective=date(2024, 4, 15), details={"bonus_ratio": "0.2"}),
    ]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 4, 20))
    by_date = {r.date: r for r in result.adjusted}
    q = Decimal("0.0000000001")
    expected = (Decimal("60000") / Decimal("60")).quantize(q)
    assert by_date[date(2024, 4, 1)].close_adjusted.quantize(q) == expected
    # 4/15 가격은 이미 ex-price → raw 그대로
    assert by_date[date(2024, 4, 15)].close_adjusted == Decimal("1000")


def test_detect_only_merger_announced_but_not_effective() -> None:
    """oracle 2 차 C5 — detect-only-merger 가 announced-not-effective 시 별도 classification.

    `detect-only-merger-announced` 로 표시. unadjusted_jumps 미포함 (발효 안 됐으므로
    raw 시계열에 점프 없음). factor=1 invariant 도 검증.
    """
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 7, 1), close=10000)]
    actions = [_action(
        action_type="merger", announced=date(2024, 7, 1),
        effective=date(2024, 9, 1),
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 7, 20))
    assert result.events[0].classification == "detect-only-merger-announced"
    assert result.events[0].factor == Decimal(1)
    assert result.unadjusted_jumps == ()


def test_detect_only_spin_off_announced_uses_correct_classification() -> None:
    """oracle 2 차 NEW-C1 — spin_off 도 _ANNOUNCED_BY_BASE 매핑으로 type-safe 분류."""
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 7, 1), close=10000)]
    actions = [_action(
        action_type="spin_off_personal", announced=date(2024, 7, 1),
        effective=date(2024, 9, 1),
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 7, 20))
    assert result.events[0].classification == "detect-only-spin-off-announced"
    assert result.unadjusted_jumps == ()


def test_detect_only_spin_off_effective_appears_in_unadjusted_jumps() -> None:
    """spin_off 가 effective 된 경우 — detect-only-spin-off classification + 점프 표시."""
    adjuster = PriceAdjuster()
    prices = [_price(d=date(2024, 7, 1), close=10000),
              _price(d=date(2024, 8, 30), close=8000)]
    actions = [_action(
        action_type="spin_off_business", announced=date(2024, 7, 1),
        effective=date(2024, 8, 30),
    )]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 9, 5))
    assert result.events[0].classification == "detect-only-spin-off"
    assert date(2024, 8, 30) in result.unadjusted_jumps


def test_adjusted_price_series_carries_pit_policy_version() -> None:
    """oracle 2 차 C6 — Screen Run snapshot 의 결합 hash 입력 보존."""
    from app.services.as_of_policy import PIT_POLICY_VERSION
    adjuster = PriceAdjuster()
    result = adjuster.adjust(
        [_price(d=date(2024, 4, 1))], [], as_of=date(2024, 4, 20)
    )
    assert result.pit_policy_version == PIT_POLICY_VERSION == "1.0"


def test_adjusted_records_tuple_is_immutable() -> None:
    """oracle 2 차 L9 — adjusted 가 tuple 로 immutable."""
    adjuster = PriceAdjuster()
    result = adjuster.adjust(
        [_price(d=date(2024, 4, 1))], [], as_of=date(2024, 4, 20)
    )
    assert isinstance(result.adjusted, tuple)
    with pytest.raises(AttributeError):
        result.adjusted.append(None)  # type: ignore[attr-defined]


def test_supersede_chain_combined_with_chained_factor_accumulation() -> None:
    """oracle 2 차 M8 — 정정공시 chain + 별개 chain 의 누적 보정 동시 시나리오.

    Chain A (split): v1 (10:1 announced 3/1) → v2 (8:1 정정 announced 4/5).
    Chain B (bonus): 0.25 비율 (announced 4/10).
    as_of=2024-04-20 → A 는 v2 active (8:1), B 는 그대로. effective 모두 4/15.
    """
    adjuster = PriceAdjuster()
    v1_id = UUID("00000000-0000-0000-0000-0000000000aa")
    v2_id = UUID("00000000-0000-0000-0000-0000000000ab")
    actions = [
        _action(
            action_type="split", announced=date(2024, 3, 1),
            effective=date(2024, 4, 15), details={"split_ratio": 10},
            superseded_by=v2_id, record_id=v1_id,
        ),
        _action(
            action_type="split", announced=date(2024, 4, 5),
            effective=date(2024, 4, 15), details={"split_ratio": 8},
            record_id=v2_id,
        ),
        _action(
            action_type="bonus_issue", announced=date(2024, 4, 10),
            effective=date(2024, 4, 15), details={"bonus_ratio": "0.25"},
        ),
    ]
    prices = [_price(d=date(2024, 4, 1), close=80000),
              _price(d=date(2024, 4, 15), close=8000)]
    result = adjuster.adjust(prices, actions, as_of=date(2024, 4, 20))
    by_date = {r.date: r for r in result.adjusted}
    # 4/1 의 factor = (1/8) × (1/1.25) = 1/10. 80000/10 = 8000.
    q = Decimal("0.0000000001")
    expected = (Decimal("80000") / Decimal("10")).quantize(q)
    assert by_date[date(2024, 4, 1)].close_adjusted.quantize(q) == expected
    # price-adjust event 가 2 개 (v2 split + bonus) — v1 은 supersede.
    price_adjust_count = sum(1 for e in result.events if e.classification == "price-adjust")
    assert price_adjust_count == 2


def test_volume_is_preserved_as_raw() -> None:
    """ADR-0001 schema 의 단일 volume — 본 사이클은 raw 그대로 (M0 한계)."""
    adjuster = PriceAdjuster()
    p = _price(d=date(2024, 4, 1), close=50000, volume=12345678)
    actions = [_action(
        action_type="split", announced=date(2024, 3, 1),
        effective=date(2024, 4, 15), details={"split_ratio": 50},
    )]
    result = adjuster.adjust([p], actions, as_of=date(2024, 4, 20))
    assert result.adjusted[0].volume == 12345678
