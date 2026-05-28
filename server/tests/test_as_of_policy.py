"""as_of_policy 단위 테스트.

테스트 매트릭스:
1. None 입력 → kst_today default fill + was_defaulted
2. 영업일 정상 입력 → 그대로 통과
3. 휴장일 입력 → snap to previous + was_snapped
4. 미래 입력 → AsOfInFutureError
5. 캘린더 범위 밖 → AsOfOutOfRangeError (CalendarRangeError rewrap)
6. PIT_POLICY_VERSION = "1.0"
7. NormalizedAsOf frozen 검증
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from app.services.as_of_policy import (
    PIT_POLICY_VERSION,
    AsOfInFutureError,
    AsOfOutOfRangeError,
    AsOfPolicy,
    AsOfPolicyError,
)
from app.services.krx_calendar import DEFAULT_CALENDAR

# =============================================================================
# 1. None default fill
# =============================================================================

def test_normalize_none_uses_kst_today_snapped_to_business_day() -> None:
    """input=None + today=일요일 → 직전 금요일."""
    today = date(2024, 5, 5)  # 일
    result = AsOfPolicy._normalize_with_today(None, today=today)
    assert result.value == date(2024, 5, 3)  # 금
    assert result.was_defaulted is True
    assert result.was_snapped is True  # today != value
    assert result.original_input is None
    assert result.pit_policy_version == "1.0"


def test_normalize_none_on_business_day_today() -> None:
    """input=None + today 가 영업일 → today 그대로 + was_snapped=False."""
    today = date(2024, 5, 7)  # 화
    result = AsOfPolicy._normalize_with_today(None, today=today)
    assert result.value == today
    assert result.was_defaulted is True
    assert result.was_snapped is False


# =============================================================================
# 2. 영업일 정상 입력
# =============================================================================

def test_normalize_business_day_passthrough() -> None:
    """input=영업일 → 그대로 통과, 메타 false."""
    result = AsOfPolicy._normalize_with_today(date(2024, 5, 7), today=date(2024, 5, 10))
    assert result.value == date(2024, 5, 7)
    assert result.was_defaulted is False
    assert result.was_snapped is False
    assert result.original_input == date(2024, 5, 7)


# =============================================================================
# 3. 휴장일 snap
# =============================================================================

def test_normalize_holiday_snaps_to_previous_business_day() -> None:
    """input=어린이날 대체공휴일 (월) → 직전 금."""
    result = AsOfPolicy._normalize_with_today(date(2024, 5, 6), today=date(2024, 5, 10))
    assert result.value == date(2024, 5, 3)
    assert result.was_snapped is True
    assert result.original_input == date(2024, 5, 6)


def test_normalize_weekend_snaps_to_previous_business_day() -> None:
    """input=일요일 → 직전 금요일."""
    result = AsOfPolicy._normalize_with_today(date(2024, 5, 5), today=date(2024, 5, 10))
    assert result.value == date(2024, 5, 3)
    assert result.was_snapped is True


def test_normalize_long_holiday_chain_snaps_correctly() -> None:
    """추석 연휴 (9/16~9/18) 마지막날 입력 → 9/13 (금)."""
    result = AsOfPolicy._normalize_with_today(date(2024, 9, 18), today=date(2024, 12, 1))
    assert result.value == date(2024, 9, 13)


# =============================================================================
# 4. 미래 거부
# =============================================================================

def test_normalize_rejects_future_date() -> None:
    """input > today → AsOfInFutureError."""
    with pytest.raises(AsOfInFutureError) as exc:
        AsOfPolicy._normalize_with_today(date(2024, 5, 10), today=date(2024, 5, 7))
    assert exc.value.requested == date(2024, 5, 10)
    assert exc.value.today_kst == date(2024, 5, 7)


def test_normalize_today_itself_is_not_future() -> None:
    """input == today → 통과 (영업일이면)."""
    today = date(2024, 5, 7)  # 화
    result = AsOfPolicy._normalize_with_today(today, today=today)
    assert result.value == today


# =============================================================================
# 5. 범위 밖 — CalendarRangeError rewrap
# =============================================================================

def test_normalize_below_min_raises_out_of_range() -> None:
    """input < calendar.min_date → AsOfOutOfRangeError."""
    with pytest.raises(AsOfOutOfRangeError) as exc:
        AsOfPolicy._normalize_with_today(date(2023, 6, 1), today=date(2024, 5, 10))
    assert exc.value.requested == date(2023, 6, 1)
    assert exc.value.min_date == DEFAULT_CALENDAR.min_date


def test_normalize_above_max_raises_out_of_range_when_today_in_future() -> None:
    """today 가 캘린더 범위 밖이고 input 도 그 범위면 → AsOfOutOfRangeError."""
    # input < today, 둘 다 범위 밖 — calendar 가 가장 먼저 차단
    with pytest.raises(AsOfOutOfRangeError):
        AsOfPolicy._normalize_with_today(date(2025, 6, 1), today=date(2025, 12, 1))


def test_normalize_none_with_today_out_of_range_raises() -> None:
    """None default + today 가 범위 밖 → AsOfOutOfRangeError (latest_business_day 실패)."""
    with pytest.raises(AsOfOutOfRangeError):
        AsOfPolicy._normalize_with_today(None, today=date(2025, 6, 1))


def test_calendar_range_error_chains_via_from_exc() -> None:
    """`from exc` chain 으로 root cause traceback 유지 (oracle R5)."""
    try:
        AsOfPolicy._normalize_with_today(date(2023, 1, 1), today=date(2024, 5, 10))
    except AsOfOutOfRangeError as exc:
        # CalendarRangeError 가 __cause__ 로 보존되어야 함.
        assert exc.__cause__ is not None
        assert type(exc.__cause__).__name__ == "CalendarRangeError"
    else:
        pytest.fail("AsOfOutOfRangeError not raised")


# =============================================================================
# 6. Policy version
# =============================================================================

def test_pit_policy_version_constant() -> None:
    assert PIT_POLICY_VERSION == "1.0"


def test_normalized_as_of_carries_policy_version() -> None:
    """NormalizedAsOf instance 가 자체 policy version 보관 — snapshot freeze 용."""
    result = AsOfPolicy._normalize_with_today(date(2024, 5, 7), today=date(2024, 5, 10))
    assert result.pit_policy_version == PIT_POLICY_VERSION


# =============================================================================
# 7. NormalizedAsOf frozen + 예외 계층
# =============================================================================

def test_normalized_as_of_is_frozen() -> None:
    result = AsOfPolicy._normalize_with_today(date(2024, 5, 7), today=date(2024, 5, 10))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.value = date(2024, 1, 1)  # type: ignore[misc]


def test_error_hierarchy() -> None:
    assert issubclass(AsOfInFutureError, AsOfPolicyError)
    assert issubclass(AsOfOutOfRangeError, AsOfPolicyError)


# =============================================================================
# 8. 운영 진입점 normalize() — today 주입 X (oracle 2 차 리뷰 M1)
# =============================================================================

def test_normalize_signature_does_not_accept_today() -> None:
    """운영 normalize() 는 `today` keyword 를 받지 않음 — PIT 우회 차단."""
    import inspect
    sig = inspect.signature(AsOfPolicy.normalize)
    assert "today" not in sig.parameters


def test_private_helper_is_underscore_prefixed() -> None:
    """`_normalize_with_today` 는 underscore prefix — 운영 wildcard import X."""
    from app.services import as_of_policy as mod
    assert "_normalize_with_today" not in (mod.__all__ or [])


def test_normalize_uses_real_kst_today_by_default() -> None:
    """운영 normalize 가 kst_today 기반으로 동작 (range 가 맞으면 통과)."""
    import pytest
    # DEFAULT_CALENDAR 가 2024 단년 verified — 현재 (2026) 는 범위 밖 → 명시적 fail.
    # 본 테스트는 운영 entry 가 kst_today 를 사용한다는 contract 만 확인.
    with pytest.raises(AsOfOutOfRangeError):
        AsOfPolicy.normalize(None)  # today 미주입, kst_today 자동 사용
