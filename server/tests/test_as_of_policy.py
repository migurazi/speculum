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
from app.services.krx_calendar import DEFAULT_CALENDAR, kst_today

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
    """today 가 캘린더 범위 밖이고 input 도 그 범위면 → AsOfOutOfRangeError.

    캘린더가 2026-06-25 까지 확장됐으므로(V1a B2) 범위 밖은 그 이후 일자로 검증.
    """
    # input < today, 둘 다 verified 범위(>2026-06-25) 밖 — calendar 가 가장 먼저 차단
    with pytest.raises(AsOfOutOfRangeError):
        AsOfPolicy._normalize_with_today(date(2027, 6, 1), today=date(2027, 12, 1))


def test_normalize_none_with_today_out_of_range_raises() -> None:
    """None default + today 가 범위 밖 → AsOfOutOfRangeError (latest_business_day 실패)."""
    with pytest.raises(AsOfOutOfRangeError):
        AsOfPolicy._normalize_with_today(None, today=date(2027, 6, 1))


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


def test_normalize_none_browse_succeeds_regardless_of_calendar_staleness() -> None:
    """browse normalize(None) 은 캘린더 만료와 무관하게 항상 200 — 시한폭탄 제거.

    **재구성 이력 (ADR-0008 D10, 2026-07-09)**: 이전 테스트
    `test_normalize_today_succeeds_after_calendar_extension` 는 strict `normalize(None)`
    이 실 `kst_today()` 로 성공하길 요구했는데, 이는 today 가 캘린더 `max_date` 를 초과
    하는 순간 `AsOfOutOfRangeError` 로 재실패하는 **구조적 시한폭탄**이었다(캘린더는 build
    시점까지만 coverage 하는 정적 파일). 근본 재검토 결과 실 사용자 대면 거울 경로
    (picker preview·portfolio·custom screen 실행)를 모두 browse 로 전환했으므로,
    **시간-비고정 계약은 strict 가 아니라 browse 가 진다**.

    browse 는 verified 범위 밖 today 도 `_previous_weekday` 근사로 degrade → 항상 200.
    따라서 이 단언은 벽시계 날짜·캘린더 coverage 와 무관하게 영구히 통과한다.
    (frozen strict 의 만료 시 거부 계약은 아래 별도 테스트에서 시간-비고정으로 검증.)
    """
    result = AsOfPolicy.normalize(None, mode="browse")
    assert result.was_defaulted is True
    assert result.value <= kst_today()


def test_normalize_none_strict_raises_when_stale_is_intended_frozen_contract() -> None:
    """strict normalize(None) 이 캘린더 만료 시 raise 하는 것은 frozen 의 정직한 계약.

    ADR-0008 D10.1: frozen/재현(runs·backtest)은 정확 휴장일 필수 → verified 범위 밖은
    `AsOfOutOfRangeError`(정직한 거부)가 옳다. 이는 'production 실패 은폐'가 아니다 —
    거울 경로가 browse 이므로 실 사용자는 이 strict 거부에 노출되지 않는다.

    시간-비고정: 실 `kst_today()` 대신 캘린더 어떤 verified 범위도 넘는 미래 today 를
    **주입**해 만료 상황을 재현한다(벽시계 의존 제거).
    """
    stale_today = date(2099, 1, 1)  # 어떤 verified 캘린더도 초과하는 주입 today
    with pytest.raises(AsOfOutOfRangeError):
        AsOfPolicy._normalize_with_today(None, today=stale_today, mode="strict")
    # 동일 만료 상황에서 browse 는 degrade 로 200 (무갭 보장).
    browse = AsOfPolicy._normalize_with_today(None, today=stale_today, mode="browse")
    assert browse.was_defaulted is True
    assert browse.was_degraded is True


# =============================================================================
# 9. browse degrade 모드 (ROADMAP_v2 §2.3 — 가용성 우선)
# =============================================================================

def test_previous_weekday_snaps_weekend_to_friday() -> None:
    """_previous_weekday: 토→금, 일→금, 평일→그대로."""
    from app.services.as_of_policy import _previous_weekday
    assert _previous_weekday(date(2026, 6, 20)) == date(2026, 6, 19)  # 토→금
    assert _previous_weekday(date(2026, 6, 21)) == date(2026, 6, 19)  # 일→금
    assert _previous_weekday(date(2026, 6, 19)) == date(2026, 6, 19)  # 금(평일)→그대로


def test_browse_none_out_of_range_degrades_not_raises() -> None:
    """browse: today 가 verified 범위 밖 → AsOfOutOfRangeError 대신 weekday degrade.

    캘린더가 2026-06-25 까지라(V1a B2) 범위 밖은 그 이후 일자로 검증.
    """
    from app.services.as_of_policy import _previous_weekday
    today = date(2030, 1, 1)  # 범위(≤2026-06-25) 밖
    result = AsOfPolicy._normalize_with_today(None, today=today, mode="browse")
    assert result.value == _previous_weekday(today)
    assert result.was_degraded is True
    assert result.was_defaulted is True


def test_browse_input_out_of_range_degrades() -> None:
    """browse: 명시 입력이 캘린더 밖 → weekday 근사 degrade(범위 밖 입력)."""
    from app.services.as_of_policy import _previous_weekday
    out_of_range = date(2030, 1, 6)  # 범위 밖(미래 today 주입으로 future 거부 회피)
    result = AsOfPolicy._normalize_with_today(
        out_of_range, today=date(2030, 6, 1), mode="browse",
    )
    assert result.value == _previous_weekday(out_of_range)
    assert result.was_degraded is True
    assert result.original_input == out_of_range


def test_browse_in_range_no_degrade() -> None:
    """browse: 범위 내 정상 입력은 strict 와 동일(degrade X, 정확 캘린더 사용)."""
    result = AsOfPolicy._normalize_with_today(
        date(2024, 5, 7), today=date(2026, 6, 25), mode="browse",
    )
    assert result.value == date(2024, 5, 7)
    assert result.was_degraded is False


def test_browse_future_still_rejected() -> None:
    """browse 도 미래는 거부 — degrade 는 가용성이지 미래 허용 아님."""
    with pytest.raises(AsOfInFutureError):
        AsOfPolicy._normalize_with_today(
            date(2026, 6, 26), today=date(2026, 6, 25), mode="browse",
        )


def test_strict_out_of_range_still_raises() -> None:
    """strict(기본) 는 범위 밖이면 여전히 AsOfOutOfRangeError — frozen 경로 보호.

    캘린더 2026-06-25 까지라(V1a B2) 범위 밖은 그 이후 일자로 검증.
    """
    with pytest.raises(AsOfOutOfRangeError):
        AsOfPolicy._normalize_with_today(
            None, today=date(2030, 1, 1), mode="strict",
        )
    with pytest.raises(AsOfOutOfRangeError):
        AsOfPolicy._normalize_with_today(
            date(2030, 1, 1), today=date(2030, 6, 1), mode="strict",
        )
