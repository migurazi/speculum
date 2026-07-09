"""krx_calendar 단위 테스트.

테스트 매트릭스 (factor_pack 의 패턴 확장):
1. JSON 로드 / 기본 캘린더 무결성 + content_hash
2. is_business_day / is_closed — 영업일·휴장일·토일·반장(half-day)
3. snap_to_previous / snap_to_next / latest_business_day
4. next_business_day / previous_business_day (strict) — boundary 메시지
5. business_days_between / business_days_in_range (inclusivity 4 변종 + weekend endpoint)
6. CalendarRangeError — original_input 보존
7. CalendarDataError — schema / hash / 중복 / weekend 포함 / coverage 위반
8. kst_today 의 timezone 일관성
9. assert_not_stale — staleness CI 게이트
10. _load_calendar path guard — 임의 path 차단
11. ADR-0008 conformance — As-of default 패턴
"""

from __future__ import annotations

import copy
import dataclasses
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app.services.krx_calendar import (
    DEFAULT_CALENDAR,
    CalendarDataError,
    CalendarError,
    CalendarRangeError,
    CalendarStaleError,
    TradingCalendar,
    _canonicalize_jcs,
    _fill_content_hash,
    _load_calendar,
    kst_today,
)

_DATA_PATH = (
    Path(__file__).resolve().parents[2] / "shared" / "data" / "calendar"
    / "krx-calendar-v1.json"
)

# 알려진 2024 KRX 휴장일 — count 와 list 가 self-consistent (oracle L5).
KNOWN_2024_HOLIDAYS: list[tuple[date, str]] = [
    (date(2024, 1, 1), "신정"),
    (date(2024, 2, 9), "설날 연휴 전일"),
    (date(2024, 2, 12), "설날 대체"),
    (date(2024, 3, 1), "삼일절"),
    (date(2024, 4, 10), "국회의원선거일"),
    (date(2024, 5, 1), "근로자의날"),
    (date(2024, 5, 6), "어린이날 대체"),
    (date(2024, 5, 15), "부처님오신날"),
    (date(2024, 6, 6), "현충일"),
    (date(2024, 8, 15), "광복절"),
    (date(2024, 9, 16), "추석 전"),
    (date(2024, 9, 17), "추석"),
    (date(2024, 9, 18), "추석 후"),
    (date(2024, 10, 1), "국군의날 임시"),
    (date(2024, 10, 3), "개천절"),
    (date(2024, 10, 9), "한글날"),
    (date(2024, 12, 25), "성탄절"),
    (date(2024, 12, 31), "연말 휴장"),
]


@pytest.fixture(scope="module")
def base_body() -> dict:
    """기본 캘린더 JSON 원본 — 각 테스트가 deep-copy 후 변경."""
    with _DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _write_with_fresh_hash(tmp_path: Path, body: dict, name: str = "cal.json") -> Path:
    """tmp_path 에 JSON 저장 + content_hash 재계산. 변형 테스트의 helper."""
    p = tmp_path / name
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    _fill_content_hash(p)
    return p


def _write_raw(tmp_path: Path, body: dict, name: str = "cal.json") -> Path:
    """hash 갱신 없이 그대로 저장 — hash mismatch 등 negative 테스트용."""
    p = tmp_path / name
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return p


# =============================================================================
# 1. 로드 / 무결성 / content_hash
# =============================================================================

def test_default_calendar_loaded() -> None:
    cal = DEFAULT_CALENDAR
    assert isinstance(cal, TradingCalendar)
    # 재확장 견고: build_krx_calendar 재실행마다 minor bump(1.x.0) — 하드코딩 대신
    # major 라인 고정만 단언(max_date 와 동형 정책).
    assert cal.version.startswith("1.")
    assert cal.min_date == date(2024, 1, 1)
    assert cal.max_date >= date(2024, 12, 31)  # 재확장 견고: max_date 는 2024-12-31 이상
    assert isinstance(cal.closed_days, frozenset)
    assert isinstance(cal.half_days, frozenset)


def test_default_calendar_closed_days_count_matches_known_list() -> None:
    """oracle L5 — KNOWN_2024_HOLIDAYS 전부 포함 확인. 캘린더 확장 후 총 count 는 더 클 수 있음."""
    # 2024 년 알려진 휴장일은 전부 포함되어야 함
    known_2024 = frozenset(d for d, _ in KNOWN_2024_HOLIDAYS)
    assert known_2024.issubset(DEFAULT_CALENDAR.closed_days)
    # 현재 캘린더 총 휴장일 수 (2024~2026.6 포함) — 향후 재확장 시 ≥ 현재 값
    assert len(DEFAULT_CALENDAR.closed_days) >= len(KNOWN_2024_HOLIDAYS)


def test_default_calendar_half_days_empty() -> None:
    # M0 에서는 빈 set (ADR-0008 D2 — 1998 폐지)
    assert DEFAULT_CALENDAR.half_days == frozenset()
    assert dict(DEFAULT_CALENDAR.half_day_close_times) == {}


def test_default_calendar_content_hash_present() -> None:
    """oracle C4 — content_hash 가 immutable hash 형식."""
    assert DEFAULT_CALENDAR.content_hash.startswith("sha256:")
    assert len(DEFAULT_CALENDAR.content_hash) == len("sha256:") + 64


def test_default_calendar_verified_fields_exposed() -> None:
    """oracle M8 — UI citation 용 verified_* 필드 노출."""
    cal = DEFAULT_CALENDAR
    assert isinstance(cal.verified_at, date)
    assert cal.verified_by == "speculum-pykrx-derived"  # pykrx 도출 캘린더
    assert cal.verified_source  # non-empty (URL 또는 출처 문자열)


def test_load_calendar_via_path() -> None:
    cal = _load_calendar(_DATA_PATH)
    assert cal.version == DEFAULT_CALENDAR.version
    assert cal.closed_days == DEFAULT_CALENDAR.closed_days
    assert cal.content_hash == DEFAULT_CALENDAR.content_hash


def test_tradingcalendar_is_frozen() -> None:
    """dataclass(frozen=True) — 인스턴스 mutation 금지 (oracle L2)."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_CALENDAR.version = "9.9.9"  # type: ignore[misc]


def test_half_day_close_times_is_immutable_view() -> None:
    """oracle M3 — MappingProxyType 으로 사실상 immutable."""
    with pytest.raises(TypeError):
        DEFAULT_CALENDAR.half_day_close_times[date(2024, 1, 1)] = "12:30"  # type: ignore[index]


# =============================================================================
# 2. is_business_day / is_closed / half-day
# =============================================================================

def test_is_business_day_for_normal_weekday() -> None:
    assert DEFAULT_CALENDAR.is_business_day(date(2024, 1, 2)) is True
    assert DEFAULT_CALENDAR.is_closed(date(2024, 1, 2)) is False


def test_is_business_day_returns_false_for_holiday() -> None:
    assert DEFAULT_CALENDAR.is_business_day(date(2024, 1, 1)) is False
    assert DEFAULT_CALENDAR.is_closed(date(2024, 1, 1)) is True


def test_is_business_day_returns_false_for_saturday() -> None:
    assert DEFAULT_CALENDAR.is_business_day(date(2024, 3, 2)) is False


def test_is_business_day_returns_false_for_sunday() -> None:
    assert DEFAULT_CALENDAR.is_business_day(date(2024, 3, 3)) is False


@pytest.mark.parametrize("d, reason", KNOWN_2024_HOLIDAYS)
def test_known_2024_holidays_are_closed(d: date, reason: str) -> None:
    """알려진 2024년 KRX 휴장일 — AC-D-04 의 verified 검증."""
    assert DEFAULT_CALENDAR.is_closed(d), f"{d} ({reason}) 가 휴장이어야 함"


def test_half_day_is_still_business_day(tmp_path: Path, base_body: dict) -> None:
    """ADR-0008 D2 — half-day 도 영업일로 처리 (oracle M7).

    DEFAULT_CALENDAR 는 half_days 가 비어 있으므로, tmp fixture 로 half-day 1 개
    inject 후 conformance 검증.
    """
    body = copy.deepcopy(base_body)
    body["half_days"].append({
        "date": "2024-12-30",  # 월
        "reason": "테스트 반장",
        "early_close_time": "12:30",
    })
    p = _write_with_fresh_hash(tmp_path, body)
    cal = _load_calendar(p, enforce_path_guard=False)
    assert date(2024, 12, 30) in cal.half_days
    assert cal.is_business_day(date(2024, 12, 30)) is True
    assert cal.is_closed(date(2024, 12, 30)) is False


def test_is_business_day_raises_for_date_below_range() -> None:
    with pytest.raises(CalendarRangeError) as exc:
        DEFAULT_CALENDAR.is_business_day(date(2023, 12, 31))
    assert exc.value.requested == date(2023, 12, 31)
    assert exc.value.min_date == date(2024, 1, 1)


def test_is_business_day_raises_for_date_above_range() -> None:
    above = DEFAULT_CALENDAR.max_date + timedelta(days=1)
    with pytest.raises(CalendarRangeError):
        DEFAULT_CALENDAR.is_business_day(above)


# =============================================================================
# 3. snap_to_previous / snap_to_next / latest_business_day
# =============================================================================

def test_snap_to_previous_returns_same_for_business_day() -> None:
    d = date(2024, 1, 2)
    assert DEFAULT_CALENDAR.snap_to_previous(d) == d


def test_snap_to_previous_skips_holiday() -> None:
    assert DEFAULT_CALENDAR.snap_to_previous(date(2024, 5, 6)) == date(2024, 5, 3)


def test_snap_to_previous_skips_weekend() -> None:
    assert DEFAULT_CALENDAR.snap_to_previous(date(2024, 3, 3)) == date(2024, 2, 29)


def test_snap_to_previous_skips_long_holiday_chain() -> None:
    # 2024-09-18 (수) 추석 → 9/17 → 9/16 → 9/15 (일) → 9/14 (토) → 9/13 (금)
    assert DEFAULT_CALENDAR.snap_to_previous(date(2024, 9, 18)) == date(2024, 9, 13)


def test_snap_to_previous_raises_when_no_business_day_found() -> None:
    """2024-01-01 (월 신정) — 직전 영업일이 2023-12-29 인데 범위 밖."""
    with pytest.raises(CalendarRangeError):
        DEFAULT_CALENDAR.snap_to_previous(date(2024, 1, 1))


def test_snap_to_next_returns_same_for_business_day() -> None:
    d = date(2024, 1, 2)
    assert DEFAULT_CALENDAR.snap_to_next(d) == d


def test_snap_to_next_skips_holiday() -> None:
    assert DEFAULT_CALENDAR.snap_to_next(date(2024, 1, 1)) == date(2024, 1, 2)


def test_snap_to_next_skips_weekend() -> None:
    assert DEFAULT_CALENDAR.snap_to_next(date(2024, 3, 2)) == date(2024, 3, 4)


def test_snap_to_next_raises_when_past_max() -> None:
    # max_date + 1 은 range 밖 — snap_to_next 가 _assert_in_range 에서 RangeError
    with pytest.raises(CalendarRangeError):
        DEFAULT_CALENDAR.snap_to_next(DEFAULT_CALENDAR.max_date + timedelta(days=1))


def test_latest_business_day_is_snap_to_previous_alias() -> None:
    d = date(2024, 5, 5)
    assert DEFAULT_CALENDAR.latest_business_day(d) == DEFAULT_CALENDAR.snap_to_previous(d)


# =============================================================================
# 4. next_business_day / previous_business_day (strict) — boundary 메시지
# =============================================================================

def test_next_business_day_is_strict_after_input() -> None:
    assert DEFAULT_CALENDAR.next_business_day(date(2024, 1, 2)) == date(2024, 1, 3)


def test_next_business_day_skips_weekend() -> None:
    assert DEFAULT_CALENDAR.next_business_day(date(2024, 1, 5)) == date(2024, 1, 8)


def test_next_business_day_raises_at_max_boundary_preserves_original_input() -> None:
    """oracle C2 — boundary 에러가 호출자 원본 input 을 보존."""
    # max_date 에서 next_business_day 는 범위 초과로 RangeError
    boundary = DEFAULT_CALENDAR.max_date
    with pytest.raises(CalendarRangeError) as exc:
        DEFAULT_CALENDAR.next_business_day(boundary)
    assert exc.value.original_input == boundary
    assert "next_business_day" in str(exc.value)


def test_previous_business_day_is_strict_before_input() -> None:
    assert DEFAULT_CALENDAR.previous_business_day(date(2024, 1, 3)) == date(2024, 1, 2)


def test_previous_business_day_skips_weekend() -> None:
    assert DEFAULT_CALENDAR.previous_business_day(date(2024, 1, 8)) == date(2024, 1, 5)


def test_previous_business_day_raises_at_min_boundary_preserves_original_input() -> None:
    with pytest.raises(CalendarRangeError) as exc:
        DEFAULT_CALENDAR.previous_business_day(date(2024, 1, 1))
    assert exc.value.original_input == date(2024, 1, 1)
    assert "previous_business_day" in str(exc.value)


# =============================================================================
# 5. business_days_between / business_days_in_range — inclusivity × weekend
# =============================================================================

def test_business_days_in_range_includes_both_endpoints() -> None:
    days = DEFAULT_CALENDAR.business_days_in_range(date(2024, 1, 2), date(2024, 1, 5))
    assert days == [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]


def test_business_days_in_range_skips_weekend_and_holiday() -> None:
    # 2024-09-13 (금) / 9/14, 9/15 주말 / 9/16~9/18 추석 / 9/19 (목)
    days = DEFAULT_CALENDAR.business_days_in_range(date(2024, 9, 13), date(2024, 9, 19))
    assert days == [date(2024, 9, 13), date(2024, 9, 19)]


@pytest.mark.parametrize("inclusivity, expected", [
    ("both", 4),
    ("left", 3),
    ("right", 3),
    ("neither", 2),
])
def test_business_days_between_inclusivity_4_variants(
    inclusivity: str, expected: int
) -> None:
    n = DEFAULT_CALENDAR.business_days_between(
        date(2024, 1, 2), date(2024, 1, 5), inclusivity=inclusivity  # type: ignore[arg-type]
    )
    assert n == expected


# oracle M5 — weekend endpoint × inclusivity 4 변종.
# 2024-01-05 (금), 01-06 (토), 01-07 (일), 01-08 (월) — 모두 영업일이거나 weekend.
@pytest.mark.parametrize("start, end, inclusivity, expected_count", [
    # (금, 월) — 양 endpoint 영업일, 토일이 중간
    (date(2024, 1, 5), date(2024, 1, 8), "both", 2),       # [금, 월] = 금 + 월
    (date(2024, 1, 5), date(2024, 1, 8), "left", 1),       # [금, 월) = 금만
    (date(2024, 1, 5), date(2024, 1, 8), "right", 1),      # (금, 월] = 월만
    (date(2024, 1, 5), date(2024, 1, 8), "neither", 0),    # (금, 월) = 토/일만 = 0
    # (토, 월) — start 가 weekend
    (date(2024, 1, 6), date(2024, 1, 8), "both", 1),       # [토, 월] = 월만
    (date(2024, 1, 6), date(2024, 1, 8), "left", 0),       # [토, 월) = 토일만 = 0
    (date(2024, 1, 6), date(2024, 1, 8), "right", 1),      # (토, 월] = 일,월 → 월만
    (date(2024, 1, 6), date(2024, 1, 8), "neither", 0),    # (토, 월) = 일만 = 0
])
def test_business_days_with_weekend_endpoints(
    start: date, end: date, inclusivity: str, expected_count: int
) -> None:
    n = DEFAULT_CALENDAR.business_days_between(
        start, end, inclusivity=inclusivity  # type: ignore[arg-type]
    )
    assert n == expected_count, (
        f"start={start}({start.strftime('%A')}) end={end}({end.strftime('%A')}) "
        f"inclusivity={inclusivity} 기대 {expected_count} 실제 {n}"
    )


def test_business_days_between_same_day_both() -> None:
    n = DEFAULT_CALENDAR.business_days_between(
        date(2024, 1, 2), date(2024, 1, 2), inclusivity="both"
    )
    assert n == 1


def test_business_days_between_same_day_neither() -> None:
    n = DEFAULT_CALENDAR.business_days_between(
        date(2024, 1, 2), date(2024, 1, 2), inclusivity="neither"
    )
    assert n == 0


def test_business_days_between_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="before start"):
        DEFAULT_CALENDAR.business_days_between(date(2024, 1, 5), date(2024, 1, 2))


def test_business_days_in_range_rejects_end_before_start() -> None:
    with pytest.raises(ValueError):
        DEFAULT_CALENDAR.business_days_in_range(date(2024, 1, 5), date(2024, 1, 2))


def test_business_days_in_range_raises_for_out_of_range_start() -> None:
    with pytest.raises(CalendarRangeError):
        DEFAULT_CALENDAR.business_days_in_range(date(2023, 12, 31), date(2024, 1, 5))


def test_business_days_in_range_raises_for_out_of_range_end() -> None:
    above = DEFAULT_CALENDAR.max_date + timedelta(days=1)
    with pytest.raises(CalendarRangeError):
        DEFAULT_CALENDAR.business_days_in_range(date(2024, 12, 30), above)


# =============================================================================
# 6. CalendarRangeError 의 attributes (original_input 포함)
# =============================================================================

def test_calendar_range_error_carries_attributes() -> None:
    above = DEFAULT_CALENDAR.max_date + timedelta(days=1)
    try:
        DEFAULT_CALENDAR.is_business_day(above)
    except CalendarRangeError as exc:
        assert exc.requested == above
        assert exc.min_date == DEFAULT_CALENDAR.min_date
        assert exc.max_date == DEFAULT_CALENDAR.max_date
        assert exc.original_input == above  # original = requested
    else:
        pytest.fail("CalendarRangeError not raised")


def test_calendar_range_error_is_subclass_of_calendar_error() -> None:
    assert issubclass(CalendarRangeError, CalendarError)


def test_calendar_data_error_is_subclass_of_calendar_error() -> None:
    assert issubclass(CalendarDataError, CalendarError)


def test_calendar_stale_error_is_subclass_of_calendar_error() -> None:
    assert issubclass(CalendarStaleError, CalendarError)


# =============================================================================
# 7. CalendarDataError — schema / hash / 무결성 위반 path
# =============================================================================

def test_load_calendar_rejects_schema_missing_required(
    tmp_path: Path, base_body: dict
) -> None:
    body = copy.deepcopy(base_body)
    del body["closed_days"]
    p = _write_with_fresh_hash(tmp_path, body)
    with pytest.raises(CalendarDataError, match="schema violation"):
        _load_calendar(p, enforce_path_guard=False)


def test_load_calendar_rejects_invalid_version_pattern(
    tmp_path: Path, base_body: dict
) -> None:
    body = copy.deepcopy(base_body)
    body["version"] = "1.0"
    p = _write_with_fresh_hash(tmp_path, body)
    with pytest.raises(CalendarDataError):
        _load_calendar(p, enforce_path_guard=False)


def test_load_calendar_rejects_duplicate_closed_day(
    tmp_path: Path, base_body: dict
) -> None:
    body = copy.deepcopy(base_body)
    body["closed_days"].append({"date": "2024-01-01", "reason": "중복"})
    p = _write_with_fresh_hash(tmp_path, body)
    with pytest.raises(CalendarDataError, match="duplicate"):
        _load_calendar(p, enforce_path_guard=False)


def test_load_calendar_rejects_weekend_in_closed_days(
    tmp_path: Path, base_body: dict
) -> None:
    body = copy.deepcopy(base_body)
    body["closed_days"].append({"date": "2024-03-02", "reason": "토요일"})
    p = _write_with_fresh_hash(tmp_path, body)
    with pytest.raises(CalendarDataError, match="weekend"):
        _load_calendar(p, enforce_path_guard=False)


def test_load_calendar_rejects_closed_day_outside_coverage(
    tmp_path: Path, base_body: dict
) -> None:
    body = copy.deepcopy(base_body)
    # max_date(2026-06-25) 이후 날짜 — 항상 coverage 밖
    body["closed_days"].append({"date": "2027-01-01", "reason": "out of coverage"})
    p = _write_with_fresh_hash(tmp_path, body)
    with pytest.raises(CalendarDataError, match="outside coverage"):
        _load_calendar(p, enforce_path_guard=False)


def test_load_calendar_rejects_inverted_coverage(
    tmp_path: Path, base_body: dict
) -> None:
    body = copy.deepcopy(base_body)
    body["coverage"]["min_date"] = "2024-12-31"
    body["coverage"]["max_date"] = "2024-01-01"
    p = _write_with_fresh_hash(tmp_path, body)
    with pytest.raises(CalendarDataError):
        _load_calendar(p, enforce_path_guard=False)


def test_load_calendar_rejects_additional_property(
    tmp_path: Path, base_body: dict
) -> None:
    body = copy.deepcopy(base_body)
    body["unexpected_field"] = "x"
    p = _write_with_fresh_hash(tmp_path, body)
    with pytest.raises(CalendarDataError):
        _load_calendar(p, enforce_path_guard=False)


def test_load_calendar_rejects_hash_mismatch(
    tmp_path: Path, base_body: dict
) -> None:
    """oracle C4 — declared content_hash 가 실제 계산값과 불일치 시 즉시 fail."""
    body = copy.deepcopy(base_body)
    body["content_hash"] = "sha256:" + "0" * 64
    p = _write_raw(tmp_path, body)  # hash 갱신 X
    with pytest.raises(CalendarDataError, match="content_hash mismatch"):
        _load_calendar(p, enforce_path_guard=False)


def test_load_calendar_detects_silent_data_tamper(
    tmp_path: Path, base_body: dict
) -> None:
    """closed_days 한 줄 변경 → hash mismatch 로 detect."""
    body = copy.deepcopy(base_body)
    # 한 휴장일의 reason 만 1 글자 변경
    body["closed_days"][0]["reason"] = body["closed_days"][0]["reason"] + " "
    p = _write_raw(tmp_path, body)
    with pytest.raises(CalendarDataError, match="content_hash mismatch"):
        _load_calendar(p, enforce_path_guard=False)


def test_load_calendar_accepts_half_day_entry(tmp_path: Path, base_body: dict) -> None:
    """schema 의 half_days 가 entry 를 받음 — M2 도입 대비."""
    body = copy.deepcopy(base_body)
    body["half_days"].append({
        "date": "2024-12-30",
        "reason": "테스트 반장",
        "early_close_time": "12:30",
    })
    p = _write_with_fresh_hash(tmp_path, body)
    cal = _load_calendar(p, enforce_path_guard=False)
    assert date(2024, 12, 30) in cal.half_days
    assert cal.half_day_close_times[date(2024, 12, 30)] == "12:30"
    assert cal.is_business_day(date(2024, 12, 30)) is True


# =============================================================================
# 8. kst_today — timezone 일관성
# =============================================================================

def test_kst_today_returns_date_object() -> None:
    assert isinstance(kst_today(), date)


def test_kst_today_matches_kst_now() -> None:
    """`kst_today` 의 결과가 ZoneInfo("Asia/Seoul") 기준 datetime.date 와 일치."""
    from zoneinfo import ZoneInfo
    expected = datetime.now(tz=ZoneInfo("Asia/Seoul")).date()
    actual = kst_today()
    # 자정 직전 race 를 1 일 차이까지 허용 (테스트 deterministic 강화는 후속).
    assert abs((actual - expected).days) <= 1


# =============================================================================
# 9. assert_not_stale — staleness CI 게이트
# =============================================================================

def test_assert_not_stale_passes_when_recent() -> None:
    """now 가 verified_at 으로부터 짧은 기간 → 통과."""
    cal = DEFAULT_CALENDAR
    cal.assert_not_stale(now=cal.verified_at, max_age_days=365)


def test_assert_not_stale_raises_when_old() -> None:
    """now 가 verified_at + (max_age + 1) → CalendarStaleError."""
    cal = DEFAULT_CALENDAR
    from datetime import timedelta as _td
    too_old = cal.verified_at + _td(days=400)
    with pytest.raises(CalendarStaleError, match="verified_at"):
        cal.assert_not_stale(now=too_old, max_age_days=365)


def test_assert_not_stale_default_max_age() -> None:
    """기본 max_age = 365 일."""
    cal = DEFAULT_CALENDAR
    from datetime import timedelta as _td
    # 364 일 후 — 통과
    cal.assert_not_stale(now=cal.verified_at + _td(days=364))
    # 366 일 후 — fail
    with pytest.raises(CalendarStaleError):
        cal.assert_not_stale(now=cal.verified_at + _td(days=366))


# =============================================================================
# 10. _load_calendar path guard — 임의 path 차단 (oracle C3)
# =============================================================================

def test_load_calendar_rejects_path_outside_repo(tmp_path: Path) -> None:
    """tmp_path 는 repo root 밖 → 가드 켜져 있으면 즉시 CalendarDataError."""
    body_min = {"_": "placeholder"}
    p = tmp_path / "evil.json"
    p.write_text(json.dumps(body_min), encoding="utf-8")
    with pytest.raises(CalendarDataError, match="outside repo root"):
        _load_calendar(p, enforce_path_guard=True)


def test_load_calendar_allows_path_inside_repo() -> None:
    """repo 내 default data path 는 가드 통과 — 기본 동작."""
    cal = _load_calendar(_DATA_PATH, enforce_path_guard=True)
    assert cal.version == DEFAULT_CALENDAR.version


# =============================================================================
# 11. 2 차 oracle 리뷰 fix 검증 — JCS drift / path guard case-insensitive / __all__
# =============================================================================

def test_canonicalize_jcs_matches_factor_pack(base_body: dict) -> None:
    """oracle 2 차 C3 — `_canonicalize_jcs` 가 factor_pack 의 동등 함수와 동일 결과.

    두 모듈이 같은 RFC 8785 JCS subset 을 inline 복제 (의도적, 모듈간 결합 회피).
    drift 발생 시 hash 의미론이 달라져 cross-runtime / cross-module 일관성 깨짐.
    """
    from app.services.factor_pack import canonicalize_jcs as fp_canonicalize

    samples = [
        base_body,
        {"a": 1, "b": [2, 3]},
        {"한글": "값", "english": "value"},
        {"nested": {"deep": {"deeper": [1, 2, {"x": 0}]}}},
        {},
        {"z": 1, "a": 2, "m": 3},  # key 순서 의존성 확인
    ]
    for s in samples:
        assert _canonicalize_jcs(s) == fp_canonicalize(s), (
            f"JCS drift detected for {s!r}"
        )


def test_path_guard_handles_case_insensitive_normcase() -> None:
    """oracle 2 차 C1 — Windows case-insensitive FS 에서도 정상 path 통과.

    POSIX 환경에서는 normcase 가 no-op 이므로 기존 가드와 동등. Windows 에서는
    drive letter / 경로 케이싱 차이가 가드를 잘못 trigger 하지 않아야 함.
    """
    # 기본 data path 는 가드 통과 — 회귀 확인.
    cal = _load_calendar(_DATA_PATH, enforce_path_guard=True)
    assert cal.version == DEFAULT_CALENDAR.version

    # 케이싱 변경 (Windows 만 의미 있음). POSIX 면 동일 path resolve 실패 가능 →
    # 그 경우 case-aware FS 라 skip.
    import os as _os
    swapped = Path(str(_DATA_PATH).swapcase())
    try:
        resolved = swapped.resolve(strict=True)
    except (FileNotFoundError, OSError):
        pytest.skip("case-sensitive FS — swapped path 미존재")
        return
    if _os.path.normcase(str(resolved)) != _os.path.normcase(str(_DATA_PATH.resolve())):
        pytest.skip("FS 가 case 보존 — 가드 우회 risk 없음")
        return
    # Windows + 동일 normcase: 가드가 차단하지 말아야 함.
    cal2 = _load_calendar(swapped, enforce_path_guard=True)
    assert cal2.version == cal.version


def test_fill_content_hash_is_private_not_in_all() -> None:
    """oracle 2 차 C2 — build utility 가 wildcard import 로 노출되지 않음."""
    from app.services import krx_calendar as mod
    assert "_fill_content_hash" not in (mod.__all__ or [])
    # Public surface 명시적 — DEFAULT_CALENDAR 등 핵심만 노출.
    expected_public = {
        "DEFAULT_CALENDAR", "CalendarDataError", "CalendarError",
        "CalendarRangeError", "CalendarStaleError", "Inclusivity",
        "TradingCalendar", "kst_today",
    }
    assert set(mod.__all__) == expected_public


# =============================================================================
# 12. ADR-0008 conformance — As-of default 패턴
# =============================================================================

def test_as_of_default_pattern_via_snap_to_previous() -> None:
    """ADR-0008 D6 — As-of 미지정 시 default = latest_business_day(kst_today())."""
    today = date(2024, 5, 6)  # 월 — 어린이날 대체공휴일
    latest = DEFAULT_CALENDAR.latest_business_day(today)
    assert latest == date(2024, 5, 3)
    assert DEFAULT_CALENDAR.is_business_day(latest) is True


def test_as_of_default_pattern_when_today_is_already_business_day() -> None:
    today = date(2024, 5, 7)
    latest = DEFAULT_CALENDAR.latest_business_day(today)
    assert latest == today
