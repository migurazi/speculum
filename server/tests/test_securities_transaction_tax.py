"""증권거래세 계산 service 단위 테스트 — M3 #5 (ADR-0030 D1/D2/D4).

검증 핵심:
- 시장구분별(코스피/코스닥/코넥스/비상장) 세율 적용 정확성.
- 효력일 구간 매칭 — 과거 거래일은 그 시점 효력 세율(ADR-0030 D4 freeze).
- Decimal 정밀(부동소수 잔차 0) + 결정성(같은 입력 = 같은 결과).
- 잘못된 시장구분·음수 금액·테이블 범위 밖 거래일 → 도메인 예외.
- 양도세·개별 상황 판단 부재(D1/D2) — service 표면에 그런 입력/출력 없음.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.securities_transaction_tax import (
    MARKET_KONEX,
    MARKET_KOSDAQ,
    MARKET_KOSPI,
    MARKET_UNLISTED,
    VALID_MARKETS,
    SecuritiesTransactionTaxError,
    calculate_securities_transaction_tax,
)

# =============================================================================
# 시장구분별 세율 적용 — 2025 효력 기준(현재 구간)
# =============================================================================

def test_kospi_2025_rate_0_15_percent() -> None:
    """코스피 2025 — 증권거래세 0.00% + 농특세 0.15% = 0.15%."""
    result = calculate_securities_transaction_tax(
        market=MARKET_KOSPI,
        trade_amount=Decimal("1000000"),
        trade_date=date(2025, 6, 2),
    )
    assert result.applied_rate == Decimal("0.0015")
    assert result.tax_amount == Decimal("1500")  # 1,000,000 × 0.0015
    assert result.effective_date == date(2025, 1, 1)
    assert "농어촌특별세" in result.legal_source
    assert "증권거래세법" in result.legal_source


def test_kosdaq_2025_rate_0_15_percent() -> None:
    """코스닥 2025 — 증권거래세 0.15%(농특세 비부과) = 0.15%."""
    result = calculate_securities_transaction_tax(
        market=MARKET_KOSDAQ,
        trade_amount=Decimal("1000000"),
        trade_date=date(2025, 6, 2),
    )
    assert result.applied_rate == Decimal("0.0015")
    assert result.tax_amount == Decimal("1500")
    assert result.effective_date == date(2025, 1, 1)


def test_konex_rate_0_10_percent() -> None:
    """코넥스 — 증권거래세 0.10%(저율, 농특세 비부과)."""
    result = calculate_securities_transaction_tax(
        market=MARKET_KONEX,
        trade_amount=Decimal("1000000"),
        trade_date=date(2025, 6, 2),
    )
    assert result.applied_rate == Decimal("0.0010")
    assert result.tax_amount == Decimal("1000")  # 1,000,000 × 0.0010


def test_unlisted_rate_0_35_percent() -> None:
    """비상장(장외) — 증권거래세법 §8 기본세율 0.35%."""
    result = calculate_securities_transaction_tax(
        market=MARKET_UNLISTED,
        trade_amount=Decimal("1000000"),
        trade_date=date(2025, 6, 2),
    )
    assert result.applied_rate == Decimal("0.0035")
    assert result.tax_amount == Decimal("3500")  # 1,000,000 × 0.0035
    assert "§8" in result.legal_source


# =============================================================================
# 효력일 구간 매칭 — 과거 거래일은 그 시점 세율(ADR-0030 D4)
# =============================================================================

def test_kospi_2024_rate_0_18_percent() -> None:
    """코스피 2024 거래 — 증권거래세 0.03% + 농특세 0.15% = 0.18%."""
    result = calculate_securities_transaction_tax(
        market=MARKET_KOSPI,
        trade_amount=Decimal("1000000"),
        trade_date=date(2024, 7, 1),
    )
    assert result.applied_rate == Decimal("0.0018")
    assert result.tax_amount == Decimal("1800")
    assert result.effective_date == date(2024, 1, 1)


def test_kospi_2023_rate_0_20_percent() -> None:
    """코스피 2023 거래 — 증권거래세 0.05% + 농특세 0.15% = 0.20%."""
    result = calculate_securities_transaction_tax(
        market=MARKET_KOSPI,
        trade_amount=Decimal("1000000"),
        trade_date=date(2023, 3, 15),
    )
    assert result.applied_rate == Decimal("0.0020")
    assert result.tax_amount == Decimal("2000")
    assert result.effective_date == date(2023, 1, 1)


def test_kosdaq_past_effective_dates() -> None:
    """코스닥 과거 구간 — 2023: 0.20%, 2024: 0.18%, 2025: 0.15%."""
    r2023 = calculate_securities_transaction_tax(
        market=MARKET_KOSDAQ, trade_amount=Decimal("1000000"),
        trade_date=date(2023, 6, 1),
    )
    r2024 = calculate_securities_transaction_tax(
        market=MARKET_KOSDAQ, trade_amount=Decimal("1000000"),
        trade_date=date(2024, 6, 1),
    )
    r2025 = calculate_securities_transaction_tax(
        market=MARKET_KOSDAQ, trade_amount=Decimal("1000000"),
        trade_date=date(2025, 6, 1),
    )
    assert r2023.applied_rate == Decimal("0.0020")
    assert r2024.applied_rate == Decimal("0.0018")
    assert r2025.applied_rate == Decimal("0.0015")


def test_effective_date_boundary_inclusive() -> None:
    """효력 개시일 당일(포함) — effective_from 당일은 새 구간 세율."""
    on_boundary = calculate_securities_transaction_tax(
        market=MARKET_KOSPI, trade_amount=Decimal("1000000"),
        trade_date=date(2025, 1, 1),  # 2025 구간 시작 당일
    )
    day_before = calculate_securities_transaction_tax(
        market=MARKET_KOSPI, trade_amount=Decimal("1000000"),
        trade_date=date(2024, 12, 31),  # 직전 = 2024 구간
    )
    assert on_boundary.applied_rate == Decimal("0.0015")
    assert day_before.applied_rate == Decimal("0.0018")


# =============================================================================
# Decimal 정밀 + 결정성
# =============================================================================

def test_decimal_precision_no_float_drift() -> None:
    """소수 거래금액 — Decimal 산술로 부동소수 잔차 0, 원 단위 quantize."""
    result = calculate_securities_transaction_tax(
        market=MARKET_KOSPI,
        trade_amount=Decimal("123456.78"),
        trade_date=date(2025, 6, 2),
    )
    # 123456.78 × 0.0015 = 185.18517 → 원 단위 ROUND_HALF_UP = 185
    assert result.tax_amount == Decimal("185")


def test_rounding_half_up_to_won() -> None:
    """원 단위 ROUND_HALF_UP — 0.5 원은 올림."""
    # 거래금액 × 0.0015 가 정확히 .5 원이 되도록: 1000 × 0.0015 = 1.5 → 2
    result = calculate_securities_transaction_tax(
        market=MARKET_KOSPI,
        trade_amount=Decimal("1000"),
        trade_date=date(2025, 6, 2),
    )
    assert result.tax_amount == Decimal("2")  # 1.5 → 2 (HALF_UP)


def test_zero_trade_amount_zero_tax() -> None:
    """거래금액 0 — 세액 0(사실)."""
    result = calculate_securities_transaction_tax(
        market=MARKET_KOSPI,
        trade_amount=Decimal("0"),
        trade_date=date(2025, 6, 2),
    )
    assert result.tax_amount == Decimal("0")


def test_deterministic_same_input_same_output() -> None:
    """결정성 — 같은 입력은 항상 같은 결과(stateless)."""
    args = dict(
        market=MARKET_KOSDAQ,
        trade_amount=Decimal("987654"),
        trade_date=date(2024, 11, 20),
    )
    r1 = calculate_securities_transaction_tax(**args)
    r2 = calculate_securities_transaction_tax(**args)
    assert r1 == r2


# =============================================================================
# 입력 검증 — 잘못된 시장구분 / 음수 / 범위 밖 거래일
# =============================================================================

def test_unknown_market_raises() -> None:
    """알 수 없는 시장구분 — 도메인 예외."""
    with pytest.raises(SecuritiesTransactionTaxError):
        calculate_securities_transaction_tax(
            market="nyse",
            trade_amount=Decimal("1000000"),
            trade_date=date(2025, 6, 2),
        )


def test_negative_trade_amount_raises() -> None:
    """음수 거래금액 — 도메인 예외."""
    with pytest.raises(SecuritiesTransactionTaxError):
        calculate_securities_transaction_tax(
            market=MARKET_KOSPI,
            trade_amount=Decimal("-1"),
            trade_date=date(2025, 6, 2),
        )


def test_trade_date_before_earliest_raises() -> None:
    """테이블 가장 이른 효력일보다 과거 거래일 — 효력 세율 근거 없음(보수적 거부)."""
    with pytest.raises(SecuritiesTransactionTaxError):
        calculate_securities_transaction_tax(
            market=MARKET_KOSPI,
            trade_amount=Decimal("1000000"),
            trade_date=date(2020, 1, 1),  # 2023 구간보다 과거
        )


def test_valid_markets_set() -> None:
    """VALID_MARKETS — 4 시장구분만(양도세·기타 없음)."""
    assert VALID_MARKETS == frozenset(
        {MARKET_KOSPI, MARKET_KOSDAQ, MARKET_KONEX, MARKET_UNLISTED}
    )
