"""total_return_adjuster 단위 테스트 — M7 #3 / ADR-0035 D1/D6/D7.

테스트 매트릭스:
1. 배당 0 → TRI == close_adjusted[t]/close_adjusted[0] (순수 가격 수익률).
2. 배당락 1건 → 배당락일 TRI 가 (close+div)/prev 비율만큼 상승.
3. 복수 배당락일 누적곱.
4. 같은 배당락일 복수 배당 합산.
5. cash_amount None → details["per_share"] fallback.
6. 배당락일이 시계열에 없음 → warning + 미반영.
7. close_adjusted[t-1]==0 → 에러.
8. 정책 hash anchor (stable / prefix / len / series 반송).
9. frozen / tuple 불변.
10. 결정성 (같은 입력 두 번 → byte 동일).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

import pytest

from app.repositories.pit_protocols import CorporateActionRecord
from app.services.price_adjuster import (
    POLICY_CONTENT_HASH as PRICE_POLICY_HASH,
)
from app.services.price_adjuster import (
    POLICY_LABEL,
    AdjustedPriceRecord,
    AdjustedPriceSeries,
    PriceAdjuster,
)
from app.services.total_return_adjuster import (
    POLICY_CONTENT_HASH,
    TOTAL_RETURN_POLICY_VERSION,
    TotalReturnAdjuster,
    TotalReturnAdjusterError,
    TotalReturnRecord,
    TotalReturnSeries,
)

_DUMMY_CITATION: Final[UUID] = UUID("00000000-0000-0000-0000-00000000ffff")
_DUMMY_LINEAGE: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000aa")
_CODE: Final[str] = "005930"
_Q: Final[Decimal] = Decimal("0.0000000001")  # 10 자리 quantize


def _rec(*, d: date, close: int | float | str) -> AdjustedPriceRecord:
    """단일 보정 close 만 의미 있는 AdjustedPriceRecord fixture (raw == adjusted)."""
    p = Decimal(str(close))
    return AdjustedPriceRecord(
        date=d, code=_CODE,
        open_raw=p, high_raw=p, low_raw=p, close_raw=p, volume=0,
        open_adjusted=p, high_adjusted=p, low_adjusted=p, close_adjusted=p,
    )


def _rec_split(
    *, d: date, close_raw: int | float | str, close_adjusted: int | float | str,
) -> AdjustedPriceRecord:
    """close_raw != close_adjusted 인 fixture — split 보정된 (축소 스케일) 날짜 모사.

    factor = close_adjusted / close_raw (예 2:1 split 이전 날짜는 0.5).
    """
    raw = Decimal(str(close_raw))
    adj = Decimal(str(close_adjusted))
    return AdjustedPriceRecord(
        date=d, code=_CODE,
        open_raw=raw, high_raw=raw, low_raw=raw, close_raw=raw, volume=0,
        open_adjusted=adj, high_adjusted=adj, low_adjusted=adj, close_adjusted=adj,
    )


def _series(records: list[AdjustedPriceRecord]) -> AdjustedPriceSeries:
    return AdjustedPriceSeries(
        code=_CODE if records else "",
        adjusted=tuple(records),
        events=(),
        unadjusted_jumps=(),
        adj_policy_label=POLICY_LABEL,
        policy_content_hash=PRICE_POLICY_HASH,
        pit_policy_version="1.0",
    )


def _dividend(
    *,
    effective: date,
    cash_amount: Decimal | None = None,
    per_share: str | None = None,
    record_id: UUID | None = None,
) -> CorporateActionRecord:
    details: dict = {}
    if per_share is not None:
        details["per_share"] = per_share
    return CorporateActionRecord(
        id=record_id or uuid4(),
        code=_CODE, code_lineage_id=_DUMMY_LINEAGE,
        action_type="cash_dividend",
        announced_date=date(effective.year, 1, 1),
        effective_date=effective,
        payment_date=None,
        ratio=None,
        cash_amount=cash_amount,
        details=details,
        citation_id=_DUMMY_CITATION,
        superseded_by=None,
        created_at=datetime(effective.year, 1, 1, 9, 0, tzinfo=UTC),
    )


# =============================================================================
# 1. 배당 0 → 순수 가격 수익률
# =============================================================================

def test_no_dividends_tri_equals_price_return() -> None:
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 1, 2), close=10000),
        _rec(d=date(2024, 1, 3), close=11000),
        _rec(d=date(2024, 1, 4), close=9900),
    ]
    result = adjuster.compute(_series(recs), [])
    by_date = {r.date: r for r in result.series}
    assert by_date[date(2024, 1, 2)].total_return_index == Decimal(1)
    # 11000/10000 = 1.1
    assert by_date[date(2024, 1, 3)].total_return_index.quantize(_Q) == \
        (Decimal("11000") / Decimal("10000")).quantize(_Q)
    # 9900/10000 = 0.99
    assert by_date[date(2024, 1, 4)].total_return_index.quantize(_Q) == \
        (Decimal("9900") / Decimal("10000")).quantize(_Q)
    assert result.warnings == ()


# =============================================================================
# 2. 배당락 1건 — (close+div)/prev
# =============================================================================

def test_single_dividend_on_ex_date_raises_tri() -> None:
    """배당락일 close=9700 (배당락 300 하락), div=300 → 그날 total 수익 = (9700+300)/10000 = 1.0."""
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),  # ex-dividend day
        _rec(d=date(2024, 3, 5), close=9800),
    ]
    divs = [_dividend(effective=date(2024, 3, 4), cash_amount=Decimal("300"))]
    result = adjuster.compute(_series(recs), divs)
    by_date = {r.date: r for r in result.series}
    # (9700 + 300) / 10000 = 1.0 — 배당락 하락을 배당이 상쇄.
    assert by_date[date(2024, 3, 4)].total_return_index.quantize(_Q) == \
        Decimal("1").quantize(_Q)
    # 다음날: 1.0 * 9800/9700
    expected_d5 = (Decimal(1) * Decimal("9800") / Decimal("9700"))
    assert by_date[date(2024, 3, 5)].total_return_index.quantize(_Q) == \
        expected_d5.quantize(_Q)
    assert result.warnings == ()


# =============================================================================
# 3. 복수 배당락일 누적곱
# =============================================================================

def test_multiple_dividend_ex_dates_compound() -> None:
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 6, 3), close=10200),  # ex-div 200
        _rec(d=date(2024, 9, 2), close=10100),  # ex-div 100
    ]
    divs = [
        _dividend(effective=date(2024, 6, 3), cash_amount=Decimal("200")),
        _dividend(effective=date(2024, 9, 2), cash_amount=Decimal("100")),
    ]
    result = adjuster.compute(_series(recs), divs)
    by_date = {r.date: r for r in result.series}
    # TRI[1] = (10200+200)/10000
    tri1 = (Decimal("10400") / Decimal("10000"))
    # TRI[2] = TRI[1] * (10100+100)/10200
    tri2 = tri1 * (Decimal("10200") / Decimal("10200"))
    assert by_date[date(2024, 6, 3)].total_return_index.quantize(_Q) == tri1.quantize(_Q)
    assert by_date[date(2024, 9, 2)].total_return_index.quantize(_Q) == tri2.quantize(_Q)


# =============================================================================
# 4. 같은 배당락일 복수 배당 합산
# =============================================================================

def test_same_ex_date_dividends_are_summed() -> None:
    """같은 배당락일 두 배당 (200 + 100) → div_on = 300."""
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),
    ]
    divs = [
        _dividend(effective=date(2024, 3, 4), cash_amount=Decimal("200")),
        _dividend(effective=date(2024, 3, 4), cash_amount=Decimal("100")),
    ]
    result = adjuster.compute(_series(recs), divs)
    by_date = {r.date: r for r in result.series}
    # (9700 + 300) / 10000 = 1.0
    assert by_date[date(2024, 3, 4)].total_return_index.quantize(_Q) == \
        Decimal("1").quantize(_Q)
    assert result.warnings == ()


# =============================================================================
# 5. cash_amount None → details per_share fallback
# =============================================================================

def test_cash_amount_none_falls_back_to_details_per_share() -> None:
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),
    ]
    divs = [_dividend(
        effective=date(2024, 3, 4), cash_amount=None, per_share="300",
    )]
    result = adjuster.compute(_series(recs), divs)
    by_date = {r.date: r for r in result.series}
    assert by_date[date(2024, 3, 4)].total_return_index.quantize(_Q) == \
        Decimal("1").quantize(_Q)
    assert result.warnings == ()


def test_cash_amount_preferred_over_details_when_both_present() -> None:
    """cash_amount(Decimal) 가 1차 — details per_share 무시."""
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),
    ]
    divs = [_dividend(
        effective=date(2024, 3, 4),
        cash_amount=Decimal("300"), per_share="999",  # per_share 는 무시돼야
    )]
    result = adjuster.compute(_series(recs), divs)
    by_date = {r.date: r for r in result.series}
    assert by_date[date(2024, 3, 4)].total_return_index.quantize(_Q) == \
        Decimal("1").quantize(_Q)


def test_neither_cash_amount_nor_per_share_warns_and_skips() -> None:
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),
    ]
    divs = [_dividend(effective=date(2024, 3, 4))]  # 둘 다 없음
    result = adjuster.compute(_series(recs), divs)
    by_date = {r.date: r for r in result.series}
    # 재투자 미반영 — 순수 가격 수익률 9700/10000.
    assert by_date[date(2024, 3, 4)].total_return_index.quantize(_Q) == \
        (Decimal("9700") / Decimal("10000")).quantize(_Q)
    assert len(result.warnings) == 1
    assert "neither cash_amount" in result.warnings[0]


def test_unparseable_per_share_warns_and_skips() -> None:
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),
    ]
    divs = [_dividend(
        effective=date(2024, 3, 4), cash_amount=None, per_share="not-a-number",
    )]
    result = adjuster.compute(_series(recs), divs)
    assert len(result.warnings) == 1
    assert "not parseable" in result.warnings[0]


# =============================================================================
# 6. 배당락일이 시계열에 없음 → warning + 미반영
# =============================================================================

def test_ex_date_not_in_series_warns_and_skips() -> None:
    """배당락일 (2024-03-06) 이 거래일 시계열에 부재 → 추측 매칭 금지, 누락 + warning."""
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),
        _rec(d=date(2024, 3, 7), close=9800),
    ]
    divs = [_dividend(effective=date(2024, 3, 6), cash_amount=Decimal("300"))]
    result = adjuster.compute(_series(recs), divs)
    by_date = {r.date: r for r in result.series}
    # 어느 날도 배당 반영 안 됨 — 순수 가격 수익률.
    assert by_date[date(2024, 3, 4)].total_return_index.quantize(_Q) == \
        (Decimal("9700") / Decimal("10000")).quantize(_Q)
    assert len(result.warnings) == 1
    assert "not in price series" in result.warnings[0]


# =============================================================================
# 7. close_adjusted[t-1] == 0 → 에러
# =============================================================================

def test_zero_previous_close_raises() -> None:
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=0),
        _rec(d=date(2024, 3, 4), close=9700),
    ]
    with pytest.raises(TotalReturnAdjusterError, match="zero"):
        adjuster.compute(_series(recs), [])


# =============================================================================
# 8. 빈 입력 방어
# =============================================================================

def test_empty_series_returns_empty_with_hashes() -> None:
    adjuster = TotalReturnAdjuster()
    result = adjuster.compute(_series([]), [])
    assert result.series == ()
    assert result.policy_content_hash == POLICY_CONTENT_HASH
    assert result.price_policy_content_hash == PRICE_POLICY_HASH
    assert result.pit_policy_version == "1.0"
    assert result.warnings == ()


# =============================================================================
# 9. 정책 hash anchor
# =============================================================================

def test_policy_content_hash_is_stable() -> None:
    a1 = TotalReturnAdjuster()
    a2 = TotalReturnAdjuster()
    assert a1.policy_content_hash == a2.policy_content_hash == POLICY_CONTENT_HASH


def test_policy_content_hash_format() -> None:
    assert POLICY_CONTENT_HASH.startswith("sha256:")
    assert len(POLICY_CONTENT_HASH) == len("sha256:") + 64  # 71


def test_policy_version_constant() -> None:
    assert TOTAL_RETURN_POLICY_VERSION == "1.0"


def test_series_carries_both_policy_hashes() -> None:
    adjuster = TotalReturnAdjuster()
    recs = [_rec(d=date(2024, 3, 1), close=10000)]
    result = adjuster.compute(_series(recs), [])
    assert result.policy_content_hash == POLICY_CONTENT_HASH
    assert result.price_policy_content_hash == PRICE_POLICY_HASH


# =============================================================================
# 10. frozen / tuple 불변
# =============================================================================

def test_series_is_frozen() -> None:
    import dataclasses
    adjuster = TotalReturnAdjuster()
    result = adjuster.compute(_series([_rec(d=date(2024, 3, 1), close=10000)]), [])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.code = "tampered"  # type: ignore[misc]


def test_record_is_frozen() -> None:
    import dataclasses
    adjuster = TotalReturnAdjuster()
    result = adjuster.compute(_series([_rec(d=date(2024, 3, 1), close=10000)]), [])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.series[0].total_return_index = Decimal(99)  # type: ignore[misc]


def test_series_tuple_is_immutable() -> None:
    adjuster = TotalReturnAdjuster()
    result = adjuster.compute(_series([_rec(d=date(2024, 3, 1), close=10000)]), [])
    assert isinstance(result.series, tuple)
    with pytest.raises(AttributeError):
        result.series.append(None)  # type: ignore[attr-defined]


# =============================================================================
# 11. 결정성 — 같은 입력 두 번 → byte 동일
# =============================================================================

def test_deterministic_repeated_compute() -> None:
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),
        _rec(d=date(2024, 6, 3), close=10333),
        _rec(d=date(2024, 9, 2), close=9871),
    ]
    divs = [
        _dividend(effective=date(2024, 3, 4), cash_amount=Decimal("300")),
        _dividend(effective=date(2024, 6, 3), cash_amount=Decimal("123")),
    ]
    r1 = adjuster.compute(_series(recs), divs)
    r2 = adjuster.compute(_series(recs), divs)
    tri1 = [str(x.total_return_index) for x in r1.series]
    tri2 = [str(x.total_return_index) for x in r2.series]
    assert tri1 == tri2  # byte-identical Decimal repr


# =============================================================================
# 12. adjust() wrapper — PriceAdjuster 연결
# =============================================================================

def test_adjust_wrapper_chains_price_adjuster_then_compute() -> None:
    from app.repositories.pit_protocols import PriceRecord

    def _price(*, d: date, close: int) -> PriceRecord:
        p = Decimal(str(close))
        return PriceRecord(
            id=uuid4(), code=_CODE, code_lineage_id=_DUMMY_LINEAGE,
            effective_date=d, open_raw=p, high_raw=p, low_raw=p, close_raw=p,
            volume=0, trading_value=p, close_adjusted=p,
            citation_id=_DUMMY_CITATION,
            created_at=datetime(d.year, d.month, d.day, 17, 0, tzinfo=UTC),
        )

    adjuster = TotalReturnAdjuster(price_adjuster=PriceAdjuster())
    prices = [
        _price(d=date(2024, 3, 1), close=10000),
        _price(d=date(2024, 3, 4), close=9700),
    ]
    divs = [_dividend(effective=date(2024, 3, 4), cash_amount=Decimal("300"))]
    result = adjuster.adjust(prices, [], divs, as_of=date(2024, 3, 10))
    assert isinstance(result, TotalReturnSeries)
    by_date = {r.date: r for r in result.series}
    # no corporate actions → close_adjusted == close_raw → (9700+300)/10000 = 1.0
    assert by_date[date(2024, 3, 4)].total_return_index.quantize(_Q) == \
        Decimal("1").quantize(_Q)
    assert result.price_policy_content_hash == PRICE_POLICY_HASH


def test_record_type_and_fields() -> None:
    adjuster = TotalReturnAdjuster()
    result = adjuster.compute(_series([_rec(d=date(2024, 3, 1), close=10000)]), [])
    rec = result.series[0]
    assert isinstance(rec, TotalReturnRecord)
    assert rec.date == date(2024, 3, 1)
    assert rec.code == _CODE
    assert rec.close_adjusted == Decimal("10000")
    assert rec.total_return_index == Decimal(1)


# =============================================================================
# 13. Critical — split-adjusted close × 명목배당 단위 정합
# =============================================================================

def test_dividend_scaled_to_adjusted_basis_when_split_after_exdate() -> None:
    """배당락 **이후** split 발생 → 배당락일은 backward 축소 스케일.

    시나리오: 2:1 split 이 배당락일 다음에 발생. backward 보정으로 split 이전
    날짜 (d0, d1=배당락일) 의 close_adjusted = close_raw / 2. d2 (split 발효일)
    부터는 close_adjusted == close_raw.

    명목 배당 div_nom=300 (원본 원화). 배당락일 d1 의 factor = close_adj/close_raw
    = 0.5 → div_adj = 150. close_adj basis 와 일치.

    손계산:
        d0: close_raw=10000 → close_adj=5000, TRI=1
        d1: close_raw=9700  → close_adj=4850, div_nom=300, factor=0.5, div_adj=150
            TRI = 1 * (4850 + 150) / 5000 = 5000/5000 = 1.0
        d2: close_raw=9800  → close_adj=9800 (split 발효), TRI = 1.0 * 9800/4850
    """
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec_split(d=date(2024, 3, 1), close_raw=10000, close_adjusted=5000),
        _rec_split(d=date(2024, 3, 4), close_raw=9700, close_adjusted=4850),
        _rec_split(d=date(2024, 3, 5), close_raw=9800, close_adjusted=9800),
    ]
    divs = [_dividend(effective=date(2024, 3, 4), cash_amount=Decimal("300"))]
    result = adjuster.compute(_series(recs), divs)
    by_date = {r.date: r for r in result.series}

    # 배당락일: (4850 + 150) / 5000 = 1.0 — adjusted basis 정합.
    assert by_date[date(2024, 3, 4)].total_return_index.quantize(_Q) == \
        Decimal("1").quantize(_Q)
    # 다음날: 1.0 * 9800/4850
    expected_d5 = Decimal(1) * Decimal("9800") / Decimal("4850")
    assert by_date[date(2024, 3, 5)].total_return_index.quantize(_Q) == \
        expected_d5.quantize(_Q)
    assert result.warnings == ()


def test_split_scaled_dividend_differs_from_nominal_naive() -> None:
    """회귀 가드 — split 구간에서 naive 명목 더하기와 결과가 다름을 명시.

    naive (버그) 였다면 TRI[d1] = (4850 + 300)/5000 = 1.03 (과대). scaled 는 1.0.
    """
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec_split(d=date(2024, 3, 1), close_raw=10000, close_adjusted=5000),
        _rec_split(d=date(2024, 3, 4), close_raw=9700, close_adjusted=4850),
    ]
    divs = [_dividend(effective=date(2024, 3, 4), cash_amount=Decimal("300"))]
    tri = adjuster.compute(_series(recs), divs).series[-1].total_return_index
    naive_buggy = (Decimal("4850") + Decimal("300")) / Decimal("5000")
    assert tri.quantize(_Q) != naive_buggy.quantize(_Q)
    assert tri.quantize(_Q) == Decimal("1").quantize(_Q)


def test_no_split_factor_one_collapses_to_nominal() -> None:
    """split 없는 sub-window (close_adj == close_raw) → factor=1 → 명목과 동일.

    수정 1 의 정합성 — split 없으면 div_adj == div_nom, 기존 명목 계산과 byte 동일.
    """
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),
    ]
    divs = [_dividend(effective=date(2024, 3, 4), cash_amount=Decimal("300"))]
    tri = adjuster.compute(_series(recs), divs).series[-1].total_return_index
    nominal = (Decimal("9700") + Decimal("300")) / Decimal("10000")
    assert tri == nominal  # quantize 없이 byte 동일


def test_close_raw_zero_with_dividend_raises() -> None:
    """배당락일 close_raw==0 → adjusted basis 스케일 0 division → 에러 (가드)."""
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec_split(d=date(2024, 3, 1), close_raw=10000, close_adjusted=5000),
        _rec_split(d=date(2024, 3, 4), close_raw=0, close_adjusted=0),
    ]
    divs = [_dividend(effective=date(2024, 3, 4), cash_amount=Decimal("300"))]
    with pytest.raises(TotalReturnAdjusterError, match="close_raw is zero"):
        adjuster.compute(_series(recs), divs)


def test_adjust_wrapper_with_real_split_and_dividend() -> None:
    """adjust() wrapper 에 실제 split corporate_action + dividend 동시 → 정합.

    실제 PriceAdjuster backward 보정이 close_adjusted 를 만들고, 그 위에서 배당이
    adjusted basis 로 스케일되는지 end-to-end 검증.

    2:1 split (split_ratio=2) effective 2024-03-05. backward 보정으로 split 이전
    (d0=03-01, d1=03-04) close_adjusted = close_raw / 2. d2 (03-05) 는 close_adj
    == close_raw.

    배당 div_nom=300 effective 2024-03-04 (split 이전 → factor=0.5 → div_adj=150).
    손계산:
        d0: raw=10000 → adj=5000, TRI=1
        d1: raw=9700  → adj=4850, div_adj=150 → TRI=(4850+150)/5000 = 1.0
        d2: raw=9800  → adj=9800 → TRI = 1.0 * 9800/4850
    """
    from app.repositories.pit_protocols import PriceRecord

    def _price(*, d: date, close: int) -> PriceRecord:
        p = Decimal(str(close))
        return PriceRecord(
            id=uuid4(), code=_CODE, code_lineage_id=_DUMMY_LINEAGE,
            effective_date=d, open_raw=p, high_raw=p, low_raw=p, close_raw=p,
            volume=0, trading_value=p, close_adjusted=p,
            citation_id=_DUMMY_CITATION,
            created_at=datetime(d.year, d.month, d.day, 17, 0, tzinfo=UTC),
        )

    split_action = CorporateActionRecord(
        id=uuid4(), code=_CODE, code_lineage_id=_DUMMY_LINEAGE,
        action_type="split",
        announced_date=date(2024, 2, 1),
        effective_date=date(2024, 3, 5),
        payment_date=None,
        ratio=None,
        cash_amount=None,
        details={"split_ratio": 2},
        citation_id=_DUMMY_CITATION,
        superseded_by=None,
        created_at=datetime(2024, 2, 1, 9, 0, tzinfo=UTC),
    )

    adjuster = TotalReturnAdjuster(price_adjuster=PriceAdjuster())
    prices = [
        _price(d=date(2024, 3, 1), close=10000),
        _price(d=date(2024, 3, 4), close=9700),
        _price(d=date(2024, 3, 5), close=9800),
    ]
    divs = [_dividend(effective=date(2024, 3, 4), cash_amount=Decimal("300"))]
    result = adjuster.adjust(
        prices, [split_action], divs, as_of=date(2024, 3, 10),
    )
    by_date = {r.date: r for r in result.series}

    # split backward 보정 확인 — split 이전 close_adjusted 는 raw/2.
    assert by_date[date(2024, 3, 1)].close_adjusted == Decimal("5000")
    assert by_date[date(2024, 3, 4)].close_adjusted == Decimal("4850")
    assert by_date[date(2024, 3, 5)].close_adjusted == Decimal("9800")
    # 배당락일 TRI = (4850 + 150)/5000 = 1.0 (adjusted basis scaling).
    assert by_date[date(2024, 3, 4)].total_return_index.quantize(_Q) == \
        Decimal("1").quantize(_Q)
    expected_d5 = Decimal(1) * Decimal("9800") / Decimal("4850")
    assert by_date[date(2024, 3, 5)].total_return_index.quantize(_Q) == \
        expected_d5.quantize(_Q)
    assert result.warnings == ()


# =============================================================================
# 14. High — 음수 배당 거부
# =============================================================================

def test_negative_cash_amount_dividend_rejected() -> None:
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),
    ]
    divs = [_dividend(effective=date(2024, 3, 4), cash_amount=Decimal("-300"))]
    result = adjuster.compute(_series(recs), divs)
    by_date = {r.date: r for r in result.series}
    # 음수 배당 미반영 → 순수 가격 수익률.
    assert by_date[date(2024, 3, 4)].total_return_index.quantize(_Q) == \
        (Decimal("9700") / Decimal("10000")).quantize(_Q)
    assert len(result.warnings) == 1
    assert "negative" in result.warnings[0]


def test_negative_per_share_dividend_rejected() -> None:
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),
    ]
    divs = [_dividend(
        effective=date(2024, 3, 4), cash_amount=None, per_share="-50",
    )]
    result = adjuster.compute(_series(recs), divs)
    assert len(result.warnings) == 1
    assert "negative" in result.warnings[0]


def test_zero_dividend_is_noop_not_rejected() -> None:
    """per_share == 0 은 정상 (no-op) — 거부 아님, warning 없음."""
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),
    ]
    divs = [_dividend(effective=date(2024, 3, 4), cash_amount=Decimal("0"))]
    result = adjuster.compute(_series(recs), divs)
    by_date = {r.date: r for r in result.series}
    assert by_date[date(2024, 3, 4)].total_return_index.quantize(_Q) == \
        (Decimal("9700") / Decimal("10000")).quantize(_Q)
    assert result.warnings == ()


# =============================================================================
# 15. High — 첫 거래일 배당락 silent drop 고지
# =============================================================================

def test_dividend_on_first_trading_day_warns() -> None:
    """첫 거래일에 배당락일이 걸린 배당 → 재투자 불가 (드롭) 이나 warning 고지."""
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 4), close=9700),
    ]
    divs = [_dividend(effective=date(2024, 3, 1), cash_amount=Decimal("300"))]
    result = adjuster.compute(_series(recs), divs)
    by_date = {r.date: r for r in result.series}
    # TRI[0]=1 그대로, d1 은 배당 미반영 순수 가격 수익률.
    assert by_date[date(2024, 3, 1)].total_return_index == Decimal(1)
    assert by_date[date(2024, 3, 4)].total_return_index.quantize(_Q) == \
        (Decimal("9700") / Decimal("10000")).quantize(_Q)
    assert len(result.warnings) == 1
    assert "first trading day" in result.warnings[0]


# =============================================================================
# 16. Medium — compute() 입력 불변식 검증
# =============================================================================

def test_compute_rejects_unsorted_dates() -> None:
    from app.services.price_adjuster import AdjusterDataError
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 4), close=9700),
        _rec(d=date(2024, 3, 1), close=10000),  # 역순
    ]
    with pytest.raises(AdjusterDataError, match="ascending"):
        adjuster.compute(_series(recs), [])


def test_compute_rejects_duplicate_dates() -> None:
    from app.services.price_adjuster import AdjusterDataError
    adjuster = TotalReturnAdjuster()
    recs = [
        _rec(d=date(2024, 3, 1), close=10000),
        _rec(d=date(2024, 3, 1), close=10100),  # 중복 date
    ]
    with pytest.raises(AdjusterDataError, match="duplicate"):
        adjuster.compute(_series(recs), [])


def test_compute_rejects_multiple_codes() -> None:
    from app.services.price_adjuster import AdjusterDataError
    adjuster = TotalReturnAdjuster()
    p = Decimal("10000")
    rec_other = AdjustedPriceRecord(
        date=date(2024, 3, 4), code="000660",
        open_raw=p, high_raw=p, low_raw=p, close_raw=p, volume=0,
        open_adjusted=p, high_adjusted=p, low_adjusted=p, close_adjusted=p,
    )
    recs = [_rec(d=date(2024, 3, 1), close=10000), rec_other]
    with pytest.raises(AdjusterDataError, match="multiple codes"):
        adjuster.compute(_series(recs), [])
