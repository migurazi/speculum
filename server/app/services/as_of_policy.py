"""As-of 일자 정규화 정책 — ADR-0008 D6 의 implementation.

본 모듈은 사용자/시스템 입력 as_of 일자를 다음 규칙으로 정규화한다:

1. `None` → `kst_today()` (ADR-0008 D6 default)
2. 미래 일자 → `AsOfInFutureError` (ADR-0008 D6 — 미래 거부)
3. KRX 휴장일 → 가장 가까운 직전 영업일로 snap (`was_snapped=True`)
4. KRX verified 캘린더 범위 밖 → `AsOfOutOfRangeError`

API layer (T24~T28) / 일배치 (T18/T19) / Frontend picker (T34) 가 모두 본 모듈
의존. PITEnforcer 는 본 모듈의 결과를 받아 record-level PIT 강제 (oracle 자문 §4
— 의존 그래프 정상화: PIT 가 AsOfPolicy 의존, 역방향 X).

타임존 — date-only 강제 (oracle R1):
    입력은 `date` 타입만. datetime / ISO 문자열은 API gateway 단계에서 변환. KST
    가정 — 서버 timezone 과 무관. ADR-0008 D8 의 일 단위 PIT 와 일관.

Race condition 한계 (oracle R4):
    `as_of == kst_today()` 인 경우, 그 시점의 KRX 종가 / DART 공시가 아직
    일배치로 들어오지 않았을 수 있다. M0 에서는 명시적 한계만 docstring 으로
    노출. M1 ADR 에서 "장중 vs 장후" 정밀도 재검토.

관련 ADR / 문서:
- ADR-0008 D6 (API contract — default fill, header), D8 (일 단위 한계)
- ADR-0002 D3 (effective_date 의 1차 자료 정의)
- M0_PLAN T17 / T21 / T34
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Literal

from app.services.krx_calendar import (
    DEFAULT_CALENDAR,
    CalendarRangeError,
    TradingCalendar,
    kst_today,
)

__all__ = [
    "AsOfInFutureError",
    "AsOfOutOfRangeError",
    "AsOfPolicy",
    "AsOfPolicyError",
    "NormalizedAsOf",
    "PIT_POLICY_VERSION",
]

# PIT 정책 버전 — Screen Run snapshot 의 `data_versions.pit_policy_version` 으로
# freeze. 정책 변경 (chain 해소 알고리즘 / tie-break / half-day 처리 등) 시 새
# 버전. ADR-0009 D8 의 `corporate-action-policy v1.0` 패턴과 동형 (oracle R3).
PIT_POLICY_VERSION: Final[str] = "1.0"


# =============================================================================
# Errors
# =============================================================================

class AsOfPolicyError(Exception):
    """본 모듈의 모든 예외 base."""


class AsOfInFutureError(AsOfPolicyError):
    """`as_of` 가 오늘 (KST) 보다 미래.

    Attributes:
        requested: 입력 일자.
        today_kst: 비교 기준 (kst_today 또는 명시 주입).
    """

    def __init__(self, requested: date, today_kst: date) -> None:
        super().__init__(
            f"as_of {requested.isoformat()} is in the future "
            f"(today KST = {today_kst.isoformat()}). "
            f"ADR-0008 D6: 미래 as_of 거부."
        )
        self.requested: Final[date] = requested
        self.today_kst: Final[date] = today_kst


class AsOfOutOfRangeError(AsOfPolicyError):
    """`as_of` 가 KRX verified 캘린더 범위 밖.

    `CalendarRangeError` 를 도메인 의미로 rewrap (oracle 자문 §5 — calendar layer
    leak 방지).
    """

    def __init__(self, requested: date, min_date: date, max_date: date) -> None:
        super().__init__(
            f"as_of {requested.isoformat()} is outside verified KRX calendar range "
            f"[{min_date.isoformat()}, {max_date.isoformat()}]. "
            f"운영 캘린더 갱신 후 가능 (T17.1 work-order)."
        )
        self.requested: Final[date] = requested
        self.min_date: Final[date] = min_date
        self.max_date: Final[date] = max_date


# =============================================================================
# Result type
# =============================================================================

@dataclass(frozen=True, slots=True)
class NormalizedAsOf:
    """정규화 결과 — API response header 결정에 필요한 정보 모두 보존.

    Attributes:
        value: 최종 정규화된 영업일 일자.
        was_defaulted: 입력이 None 이어서 `kst_today` 로 fill 했는지 (True 면
            response 에 `X-AsOf-Defaulted: true`).
        was_snapped: 입력이 휴장일이라 직전 영업일로 snap 했는지 (True 면
            response 에 warning header / toast 알림 — ADR-0008 D1.1).
        original_input: 호출자가 실제로 전달한 input. None 이거나 휴장일 그대로.
            디버깅 / 사용자 알림에 사용.
        pit_policy_version: 본 정규화 결과를 만든 정책의 버전. Screen Run snapshot
            freeze key. `PIT_POLICY_VERSION` 의 copy — instance 가 자체 보관해
            policy 가 바뀌어도 옛 snapshot 재현 가능.
        was_degraded: browse 모드에서 as_of 가 verified 캘린더 범위 밖이라 weekday
            근사(휴장일 미반영)로 graceful degrade 했는지 (ROADMAP_v2 §2.3 R3 —
            가용성 우선). True 면 response 에 `X-AsOf-Degraded: true`. strict 모드
            (frozen 경로)는 항상 False — 범위 밖이면 `AsOfOutOfRangeError`. **데이터
            정확성이 아닌 가용성 degrade** 이므로 frozen/재현 경로에는 절대 미적용.
    """

    value: date
    was_defaulted: bool
    was_snapped: bool
    original_input: date | None
    pit_policy_version: str
    # additive(default False) — 기존 strict 경로 생성자·소비처 무변경. browse degrade
    # 경로에서만 True. frozen 경로는 strict 라 절대 True 불가.
    was_degraded: bool = False


# =============================================================================
# browse degrade 헬퍼 — verified 캘린더 범위 밖의 weekday 근사 (ROADMAP_v2 §2.3)
# =============================================================================

def _previous_weekday(d: date) -> date:
    """`d` 가 평일이면 그대로, 토/일이면 직전 금요일.

    browse degrade 전용 — verified 캘린더 범위 밖이라 **공식 휴장일 데이터가 없을
    때** 가용성 우선(R3)으로 영업일을 근사한다. 토(weekday 5)→금, 일(6)→금. 평일
    이면 그대로(임시공휴일·대체공휴일은 반영 못 함 — 그래서 `was_degraded=True` 로
    근사임을 고지). frozen/재현 경로는 이 근사를 쓰지 않고 verified 캘린더를 강제.
    """
    wd = d.weekday()  # 월=0 .. 토=5, 일=6
    if wd < 5:
        return d
    return d - timedelta(days=wd - 4)  # 토→-1(금), 일→-2(금)


# =============================================================================
# AsOfPolicy
# =============================================================================

class AsOfPolicy:
    """As-of 일자 정규화의 single entry point.

    Class 로 묶었지만 state 는 없음. 두 entry point:

    - `normalize(input_value)` — **운영 전용**. `today` 인자 없음 — `kst_today()`
      자동 사용. API layer / 일배치 / Frontend 가 호출.
    - `_normalize_with_today(input_value, today=...)` — **테스트 전용**. underscore
      prefix + `__all__` 미노출. 운영 코드의 우발적 PIT 우회 차단 (oracle 2 차
      리뷰 M1).

    `calendar` 는 양쪽 모두 default `DEFAULT_CALENDAR` — 테스트가 별도 calendar 로
    격리 시 inject 가능.
    """

    @staticmethod
    def normalize(
        input_value: date | None,
        *,
        calendar: TradingCalendar = DEFAULT_CALENDAR,
        mode: Literal["strict", "browse"] = "strict",
    ) -> NormalizedAsOf:
        """**운영 전용** — As-of 일자를 정규화. `today` 는 항상 `kst_today()`.

        Args:
            input_value: 호출자가 전달한 일자. None 이면 default fill (ADR-0008 D6).
            calendar: 영업일/휴장일 판정 source. default = DEFAULT_CALENDAR.
            mode: "strict"(기본, frozen/재현 경로) 면 verified 범위 밖 → 400.
                "browse"(가용성 우선, ROADMAP_v2 §2.3) 면 범위 밖을 weekday 근사로
                graceful degrade(`was_degraded=True`) — 오늘 날짜 200 보장(R3).

        Returns:
            NormalizedAsOf — 정규화된 영업일 + 메타데이터.

        Raises:
            AsOfInFutureError: `input_value > kst_today()` (browse 도 미래 불가).
            AsOfOutOfRangeError: strict 모드에서 정규화 결과가 verified 범위 밖.

        Note (M0 race condition — oracle R4):
            `input_value == kst_today()` 이고 그날이 영업일이면 그대로 정규화. 그
            시점의 KRX 종가 / DART 공시가 아직 일배치로 들어오지 않았을 수 있다.
            M1 ADR 에서 "장 마감 후" 시점 보정 결정.
        """
        return AsOfPolicy._normalize_with_today(
            input_value, today=kst_today(), calendar=calendar, mode=mode
        )

    @staticmethod
    def _normalize_with_today(
        input_value: date | None,
        *,
        today: date,
        calendar: TradingCalendar = DEFAULT_CALENDAR,
        mode: Literal["strict", "browse"] = "strict",
    ) -> NormalizedAsOf:
        """**테스트/내부 전용** — `today` 를 명시 주입한 정규화.

        본 함수는 underscore prefix + `__all__` 미노출. 운영 코드는 `normalize`
        를 호출해야 하며, 운영 호출 site 가 본 함수를 사용하면 우회로 간주.

        Args / Returns / Raises: `normalize` 와 동일하나 `today` 가 required.

        browse degrade(ROADMAP_v2 §2.3): verified 캘린더 범위 밖(`CalendarRangeError`)
        일 때 strict 면 `AsOfOutOfRangeError`, browse 면 `_previous_weekday` 근사 +
        `was_degraded=True`. 미래 거부·범위 내 휴장일 snap 은 두 모드 공통(정확성).
        """
        # 1. None → default
        if input_value is None:
            try:
                value = calendar.latest_business_day(today)
            except CalendarRangeError as exc:
                # browse: today 가 verified 범위 밖(예: 2026 vs 2024 캘린더) →
                # weekday 근사로 degrade(가용성 우선). strict: 400.
                if mode == "browse":
                    return NormalizedAsOf(
                        value=_previous_weekday(today),
                        was_defaulted=True,
                        was_snapped=False,
                        original_input=None,
                        pit_policy_version=PIT_POLICY_VERSION,
                        was_degraded=True,
                    )
                raise AsOfOutOfRangeError(
                    today, calendar.min_date, calendar.max_date
                ) from exc
            return NormalizedAsOf(
                value=value,
                was_defaulted=True,
                was_snapped=(value != today),
                original_input=None,
                pit_policy_version=PIT_POLICY_VERSION,
            )

        # 2. 미래 거부 — input > today (browse 도 미래 불가)
        if input_value > today:
            raise AsOfInFutureError(input_value, today)

        # 3. Calendar 범위 검사 + 휴장일 snap
        try:
            if calendar.is_business_day(input_value):
                return NormalizedAsOf(
                    value=input_value,
                    was_defaulted=False,
                    was_snapped=False,
                    original_input=input_value,
                    pit_policy_version=PIT_POLICY_VERSION,
                )
            # 휴장일 → snap to previous (ADR-0008 D1.1).
            # ADR-0008 D6 는 was_snapped=True 일 때 API layer 가 사용자에게
            # 알림 (toast / response header / banner) 을 보내야 함을 명시.
            # 본 모듈은 result type 에 was_snapped 만 노출 — header 이름 (예:
            # `X-AsOf-Snapped: true`) 은 T24~T28 API layer 의 contract.
            snapped = calendar.snap_to_previous(input_value)
            return NormalizedAsOf(
                value=snapped,
                was_defaulted=False,
                was_snapped=True,
                original_input=input_value,
                pit_policy_version=PIT_POLICY_VERSION,
            )
        except CalendarRangeError as exc:
            # browse: 범위 밖 입력 → weekday 근사 degrade. strict: 400.
            if mode == "browse":
                return NormalizedAsOf(
                    value=_previous_weekday(input_value),
                    was_defaulted=False,
                    was_snapped=False,
                    original_input=input_value,
                    pit_policy_version=PIT_POLICY_VERSION,
                    was_degraded=True,
                )
            raise AsOfOutOfRangeError(
                input_value, calendar.min_date, calendar.max_date
            ) from exc
