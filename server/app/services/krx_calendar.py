"""KRX 영업일 캘린더 — ADR-0008 D2 의 영구 진실 source.

본 모듈은 한국 주식 시장의 영업일·휴장일·반장(half-day) 을 정적 JSON 데이터로
관리한다. 모든 PIT (Point-in-Time) 분석·As-of 처리·일배치 트리거가 본 모듈에
의존하므로 정확성이 핵심.

설계 원칙:

1. **Standalone** — DB / 외부망 의존 X. 단위 테스트가 격리 가능.
2. **Immutable verified data** — JSON 파일이 single source of truth. 운영 코드
   path 와 build/sync 도구 path 가 분리. pykrx 등의 외부 source 는 데이터 갱신
   build 스크립트에서만 사용 (운영 import graph 에 진입 X). `content_hash`
   (factor_pack 과 동일 의미론) 로 변조 감지.
3. **Fidelity 우선** — verified 범위 밖 요청은 silent 하게 추정하지 않고
   `CalendarRangeError` 로 명시적 fail. 데이터 없음 ≠ 데이터 부정확. 사용자는
   구별 가능해야 함 (8 기둥 §2.1 Fidelity). `verified_at` / `verified_by` /
   `verified_source` 는 UI 의 citation 표시용으로 인스턴스에 보존.
4. **타임존 강제** — `kst_today()` helper 가 ZoneInfo("Asia/Seoul") 기준으로
   *오늘의 KRX 일자* 를 반환. 모든 호출자가 본 함수만 사용하도록. 직접
   `date.today()` 사용 시 서버 timezone 에 따라 silent bug 발생 (서버가 UTC
   인 경우 KST 09:00 까지의 9 시간이 직전일자로 잘못 판정됨).

Inclusivity 정책:
- `business_days_*` 함수의 `inclusivity` 기본값은 "both" — SQL `BETWEEN` 의
  의미론과 일치. Python `range()` 의 left-inclusive 와는 다르므로 호출자가
  type literal 로 명시 가능 (`"both" / "neither" / "left" / "right"`).

관련 ADR / 문서:
- ADR-0008 D2 — 영업일 처리, half-day, KRX 캘린더 일급 source
- ADR-0008 D6 — As-of 미지정 시 default = `kst_today()` 결과
- ADR-0008 D7 — Screen Run snapshot 의 `data_versions.calendar_version` (후속)
- ADR-0002 D4 — content_hash + semver immutability (factor_pack 과 공유)
- M0_PLAN T17 / AC-D-04 — 지난 3 년 검증 (v1.0.0 은 2024 단년 verified)
- docs/work-orders/t17.1-calendar-coverage-extension.md (후속 확장)

운영 정책 (verified 범위 확장):
- v1.0.0 = 2024 단년. 후속 work-order T17.1 으로 2020~2023 + 2025~2027 확장.
- 연간 KRX 공식 휴장일 발표 후 patch/minor release.
- T18 일배치가 매일 KRX 공식 캘린더 발표와 cross-check → 불일치 시 alert.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Iterator, Literal, Mapping
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

# 운영 코드의 wildcard import 시 노출되는 public surface. `_fill_content_hash` 등
# build-time utility 는 의도적으로 제외 — 운영 코드 path 에서의 우발적 호출 방지
# (oracle 2 차 리뷰 C2).
__all__ = [
    "DEFAULT_CALENDAR",
    "CalendarDataError",
    "CalendarError",
    "CalendarRangeError",
    "CalendarStaleError",
    "Inclusivity",
    "TradingCalendar",
    "kst_today",
]

# =============================================================================
# Path constants — factor_pack.py 와 동형
# =============================================================================

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_SCHEMA_PATH: Final[Path] = _REPO_ROOT / "shared" / "schemas" / "krx-calendar-v1.json"
_DATA_PATH: Final[Path] = (
    _REPO_ROOT / "shared" / "data" / "calendar" / "krx-calendar-v1.json"
)

# content_hash 필드는 자기 자신을 hash 입력에서 제외 (factor_pack 과 동일 의미론).
_HASH_FIELD: Final[str] = "content_hash"

# verified_at staleness 판정의 default — CI 게이트가 사용. 1 년 이상 stale 이면 fail.
_DEFAULT_STALE_THRESHOLD_DAYS: Final[int] = 365

# =============================================================================
# KST timezone helper — 타임존 silent bug 차단의 single entry point
# =============================================================================

_KST: Final[ZoneInfo] = ZoneInfo("Asia/Seoul")


def kst_today() -> date:
    """현재 KST 기준 오늘 일자.

    서버 timezone 과 무관하게 항상 KST (UTC+9) 기준. ADR-0008 D6 의 As-of default
    fill 이 본 함수의 결과를 사용해야 한다. 서버가 UTC 일 때 `date.today()` 를
    쓰면 KST 09:00 KST 까지의 9 시간이 잘못된 일자로 판정되는 silent bug 발생.

    Returns:
        KST 기준 오늘. KRX 영업일 여부는 별도 — 본 함수는 오늘이 토요일/휴장일
        이어도 그 일자를 반환. 영업일 snap 은 호출자가 `snap_to_previous()` 등으로.
    """
    return datetime.now(tz=_KST).date()


# =============================================================================
# Errors
# =============================================================================

class CalendarError(Exception):
    """본 모듈의 모든 예외 base."""


class CalendarRangeError(CalendarError):
    """요청 일자가 verified coverage 범위 밖.

    Attributes:
        requested: 호출자가 전달한 일자 — boundary case 에서 사용자가 디버깅 가능.
        min_date: 캘린더의 verified 최소 (inclusive).
        max_date: 캘린더의 verified 최대 (inclusive).
        original_input: 호출자가 실제로 전달한 input (next/previous_business_day
            처럼 함수 내부에서 ±1day 변형 후 본 예외를 던지는 경우, 원본 input 보존).
            None 이면 `requested` 와 동일.
    """

    def __init__(
        self,
        requested: date,
        min_date: date,
        max_date: date,
        *,
        original_input: date | None = None,
        context: str = "",
    ) -> None:
        original = original_input if original_input is not None else requested
        ctx_suffix = f" (input: {original.isoformat()}, {context})" if context else (
            f" (input: {original.isoformat()})" if original != requested else ""
        )
        super().__init__(
            f"date {requested.isoformat()} is outside verified KRX calendar range "
            f"[{min_date.isoformat()}, {max_date.isoformat()}]{ctx_suffix}. "
            f"verified 범위 확장은 캘린더 데이터 갱신 후 가능."
        )
        self.requested: Final[date] = requested
        self.min_date: Final[date] = min_date
        self.max_date: Final[date] = max_date
        self.original_input: Final[date] = original


class CalendarDataError(CalendarError):
    """JSON 데이터 자체의 schema / 무결성 / hash 위반."""


class CalendarStaleError(CalendarError):
    """`verified_at` 이 stale 한계를 초과 — CI 게이트가 운영 캘린더 갱신 강제."""


# =============================================================================
# Inclusivity literal — business_days_between 의미론 (oracle W2)
# =============================================================================

# inclusive 양 끝 / 양 끝 제외 / 좌만 포함 / 우만 포함. SQL BETWEEN 과 Python
# range() 의 의미론 차이로 인한 호출자 실수를 type 으로 강제 차단. default =
# "both" — SQL BETWEEN 과 일치 (호출자가 DB query 와 같은 의도 표현 시 자연).
Inclusivity = Literal["both", "neither", "left", "right"]


# =============================================================================
# RFC 8785 JCS — factor_pack 과 동일 의미론. 캘린더의 content_hash 계산.
# (별도 모듈 분리는 후속 정리 — 지금은 inline 복제, 두 곳 동기화 부담 명시.)
# =============================================================================

def _canonicalize_jcs(value: Any) -> bytes:
    """RFC 8785 JCS subset — sort_keys + 공백제거 + ensure_ascii=False.

    factor_pack.canonicalize_jcs 와 동일 의미론. 두 모듈이 같은 hash 패턴을
    공유하나, 모듈간 import 결합을 피하기 위해 inline 복제. 정책 변경 시 두 곳
    모두 수정 (또는 별도 hashing util 모듈로 추출).
    """
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _compute_content_hash(body: dict[str, Any]) -> str:
    """`content_hash` 필드 제외 후 JCS + SHA-256. 반환: 'sha256:<hex>'."""
    without_hash = {k: v for k, v in body.items() if k != _HASH_FIELD}
    digest = hashlib.sha256(_canonicalize_jcs(without_hash)).hexdigest()
    return f"sha256:{digest}"


# =============================================================================
# TradingCalendar — 데이터 + API
# =============================================================================

@dataclass(frozen=True, slots=True)
class TradingCalendar:
    """검증된 KRX 영업일 캘린더.

    Immutable — `DEFAULT_CALENDAR` 의 module-level singleton 으로 1 회 로드.
    테스트는 `_load_calendar(path)` 로 별도 instance 생성 (factor_pack 의 패턴).

    Attributes:
        closed_days: 평일 휴장일 set. 토/일은 본 set 에 포함 X — 코드가 자동 처리.
        half_days: 반장 set. 현재 빈 set (1998 폐지). M0 에서는 internal 만 노출
            (`is_half_day` API 미공개) — M2 ADR 에서 의미론 결정 후 노출.
        min_date: Verified 범위 시작 (inclusive).
        max_date: Verified 범위 끝 (inclusive).
        version: 캘린더 데이터 semver (Screen Run snapshot 의 `calendar_version` 용).
        content_hash: 'sha256:<hex>' — 데이터 변조 감지.
        verified_at: 마지막 KRX 공식 캘린더 cross-check 일.
        verified_by: 검증 수행자 식별.
        verified_source: 검증에 사용된 1차 자료 URL (UI citation 용).
        half_day_close_times: 반장 일자 → "HH:MM" 매핑 (M2 도입 대비, 현재 빈 mapping).
    """

    closed_days: frozenset[date]
    half_days: frozenset[date]
    min_date: date
    max_date: date
    version: str
    content_hash: str
    verified_at: date
    verified_by: str
    verified_source: str
    # MappingProxyType 으로 사실상 immutable — frozen dataclass 의 약속과 일관.
    half_day_close_times: Mapping[date, str] = field(default_factory=lambda: MappingProxyType({}))

    # ---------------------------------------------------------------------
    # 범위 검사 — 모든 public method 의 사전 조건
    # ---------------------------------------------------------------------

    def _assert_in_range(self, d: date) -> None:
        """`d` 가 [min_date, max_date] (inclusive) 범위 내인지 검사."""
        if d < self.min_date or d > self.max_date:
            raise CalendarRangeError(d, self.min_date, self.max_date)

    # ---------------------------------------------------------------------
    # 핵심 판정
    # ---------------------------------------------------------------------

    def is_business_day(self, d: date) -> bool:
        """`d` 가 KRX 영업일인가 — 토/일/휴장일이 아니면 True.

        반장(half-day) 도 영업일로 처리 (ADR-0008 D2). 단, half-day 의 특수성
        (장 종료 시각 다름) 은 별도 ADR 의 의미론 — 본 API 는 단순 boolean.

        Raises:
            CalendarRangeError: `d` 가 verified 범위 밖.
        """
        self._assert_in_range(d)
        # weekday: 월=0 .. 일=6. 토(5)/일(6) 자동 휴장.
        if d.weekday() >= 5:
            return False
        return d not in self.closed_days

    def is_closed(self, d: date) -> bool:
        """`d` 가 KRX 휴장일인가 — `is_business_day` 의 역.

        Raises:
            CalendarRangeError: `d` 가 verified 범위 밖.
        """
        return not self.is_business_day(d)

    # ---------------------------------------------------------------------
    # Snap — 휴장일 입력의 nearest 영업일 변환
    # ---------------------------------------------------------------------

    def snap_to_previous(self, d: date) -> date:
        """`d` 가 영업일이면 그대로, 휴장일이면 가장 가까운 직전 영업일.

        ADR-0008 D1.1 의 picker 가 휴장일 선택 시도 → 가장 가까운 이전 영업일로
        snap 의 implementation. ADR-0008 D6 의 As-of default 처리도 동형
        (가장 최근 영업일).

        Raises:
            CalendarRangeError: `d` 또는 snap 결과가 verified 범위 밖.
        """
        self._assert_in_range(d)
        cursor = d
        while cursor >= self.min_date:
            if self._is_business_day_unchecked(cursor):
                return cursor
            cursor -= timedelta(days=1)
        # min_date 이전까지 거슬러 올라가도 영업일 없음 — 운영상 발생 불가지만
        # 데이터 corruption 의 fail-fast 차원에서 명시적 예외.
        raise CalendarRangeError(
            cursor + timedelta(days=1), self.min_date, self.max_date,
            original_input=d, context="snap_to_previous: no business day found in range",
        )

    def snap_to_next(self, d: date) -> date:
        """`d` 가 영업일이면 그대로, 휴장일이면 가장 가까운 다음 영업일."""
        self._assert_in_range(d)
        cursor = d
        while cursor <= self.max_date:
            if self._is_business_day_unchecked(cursor):
                return cursor
            cursor += timedelta(days=1)
        raise CalendarRangeError(
            cursor - timedelta(days=1), self.min_date, self.max_date,
            original_input=d, context="snap_to_next: no business day found in range",
        )

    def latest_business_day(self, d: date) -> date:
        """`snap_to_previous` 의 alias. ADR-0008 D6 의 "가장 최근 영업일" 의도."""
        return self.snap_to_previous(d)

    # ---------------------------------------------------------------------
    # Strict next / previous — 입력일 자체는 제외
    # ---------------------------------------------------------------------

    def next_business_day(self, d: date) -> date:
        """`d` 보다 strictly 이후의 첫 영업일.

        `snap_to_next` 와 달리 `d` 가 영업일이어도 그 다음을 반환. 일배치의
        "내일 처리할 영업일" 계산에 사용.

        Raises:
            CalendarRangeError: `d` 가 범위 밖이거나, `d` 다음 영업일이 max_date
                를 초과하는 경우 — `original_input` 으로 호출자 원본 일자 보존.
        """
        self._assert_in_range(d)
        next_day = d + timedelta(days=1)
        if next_day > self.max_date:
            raise CalendarRangeError(
                next_day, self.min_date, self.max_date,
                original_input=d,
                context="next_business_day: no business day strictly after input within verified range",
            )
        try:
            return self.snap_to_next(next_day)
        except CalendarRangeError as exc:
            raise CalendarRangeError(
                exc.requested, self.min_date, self.max_date,
                original_input=d,
                context="next_business_day: no business day strictly after input within verified range",
            ) from exc

    def previous_business_day(self, d: date) -> date:
        """`d` 보다 strictly 이전의 마지막 영업일.

        Raises:
            CalendarRangeError: `d` 가 범위 밖이거나, `d` 이전 영업일이 min_date
                미만으로 떨어지는 경우 — `original_input` 으로 호출자 원본 일자 보존.
        """
        self._assert_in_range(d)
        prev_day = d - timedelta(days=1)
        if prev_day < self.min_date:
            raise CalendarRangeError(
                prev_day, self.min_date, self.max_date,
                original_input=d,
                context="previous_business_day: no business day strictly before input within verified range",
            )
        try:
            return self.snap_to_previous(prev_day)
        except CalendarRangeError as exc:
            raise CalendarRangeError(
                exc.requested, self.min_date, self.max_date,
                original_input=d,
                context="previous_business_day: no business day strictly before input within verified range",
            ) from exc

    # ---------------------------------------------------------------------
    # Range queries — inclusivity 4 변종, default "both" (SQL BETWEEN 호환)
    # ---------------------------------------------------------------------

    def business_days_between(
        self,
        start: date,
        end: date,
        *,
        inclusivity: Inclusivity = "both",
    ) -> int:
        """`start` 와 `end` 사이의 영업일 수.

        Args:
            start: 시작 일자.
            end: 종료 일자.
            inclusivity: 양 끝점 포함 정책 — "both" (default, [start, end]) /
                "neither" ((start, end)) / "left" ([start, end)) / "right" ((start, end]).
                default = "both" 는 SQL `BETWEEN` 의미론 (호출자가 DB 쿼리와 같은
                의도 표현 시 자연). Python `range()` 의 left-inclusive 와는 다름.

        Raises:
            CalendarRangeError: start 또는 end 가 verified 범위 밖.
            ValueError: end < start.
        """
        if end < start:
            raise ValueError(f"end {end} is before start {start}")
        # business_days_in_range 가 즉시 list 를 만드므로 len 이 sum 보다 명확.
        return len(self.business_days_in_range(start, end, inclusivity=inclusivity))

    def business_days_in_range(
        self,
        start: date,
        end: date,
        *,
        inclusivity: Inclusivity = "both",
    ) -> list[date]:
        """`start` 와 `end` 사이의 영업일 list (오름차순, eager evaluation).

        본 함수는 generator 가 아니라 즉시 list 를 반환. `inclusivity` 의미론에
        따라 effective range 가 [eff_start, eff_end] 가 됨 (양쪽 inclusive
        계산 후 endpoint 포함 여부로 ±1day 조정).

        Raises:
            CalendarRangeError: 범위 밖.
            ValueError: end < start.
        """
        if end < start:
            raise ValueError(f"end {end} is before start {start}")
        self._assert_in_range(start)
        self._assert_in_range(end)

        # inclusivity 에 따라 effective 범위 조정.
        eff_start = start if inclusivity in ("both", "left") else start + timedelta(days=1)
        eff_end = end if inclusivity in ("both", "right") else end - timedelta(days=1)
        # eff_end < eff_start 가능 (예: same-day with "neither") → 빈 list.
        if eff_end < eff_start:
            return []

        return [
            d for d in _iter_dates(eff_start, eff_end)
            if self._is_business_day_unchecked(d)
        ]

    # ---------------------------------------------------------------------
    # Staleness 검사 — CI 게이트가 사용
    # ---------------------------------------------------------------------

    def assert_not_stale(
        self,
        *,
        now: date | None = None,
        max_age_days: int = _DEFAULT_STALE_THRESHOLD_DAYS,
    ) -> None:
        """`verified_at` 이 `max_age_days` 이내인지 검사. CI 게이트 / 배포 전 호출.

        Args:
            now: 비교 기준 일자. None 이면 `kst_today()`. 테스트가 결정성 위해 주입.
            max_age_days: 허용 stale 일수 (default 365 = 1 년).

        Raises:
            CalendarStaleError: `verified_at` 가 `now - max_age_days` 이전.
        """
        anchor = now if now is not None else kst_today()
        age = (anchor - self.verified_at).days
        if age > max_age_days:
            raise CalendarStaleError(
                f"KRX calendar verified_at {self.verified_at.isoformat()} is "
                f"{age} days old (>{max_age_days}); 갱신 work-order 진행 필요."
            )

    # ---------------------------------------------------------------------
    # Half-day — internal only (M0 미노출, M2 ADR 후 공개)
    # ---------------------------------------------------------------------

    def _is_half_day_unchecked(self, d: date) -> bool:
        """범위 검사 없이 half-day 여부. M0 에서는 internal 만 사용."""
        return d in self.half_days

    # ---------------------------------------------------------------------
    # 범위 검사를 우회한 내부 fast path — public 호출자가 _assert_in_range 를
    # 이미 통과한 후 반복 호출하는 경우 (e.g., snap 의 cursor loop).
    # ---------------------------------------------------------------------

    def _is_business_day_unchecked(self, d: date) -> bool:
        if d.weekday() >= 5:
            return False
        return d not in self.closed_days


def _iter_dates(start: date, end: date) -> Iterator[date]:
    """[start, end] inclusive 의 일자 generator."""
    cursor = start
    one_day = timedelta(days=1)
    while cursor <= end:
        yield cursor
        cursor += one_day


# =============================================================================
# Schema + JSON loading
# =============================================================================

def _load_schema() -> dict:
    """JSON schema 1 회 로드 — factor_pack.py 와 동형 패턴."""
    with _SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


_SCHEMA: Final[dict] = _load_schema()
_VALIDATOR: Final[Draft202012Validator] = Draft202012Validator(_SCHEMA)


def _assert_path_under_repo(path: Path) -> Path:
    """`path` 가 `_REPO_ROOT` 하위 (symlink resolution 후) 인지 강제.

    공격 surface 좁히기 — 호출자가 임의 path 의 JSON 을 로드하지 못하도록.
    캘린더 데이터는 repo 의 verified content 만 신뢰. 테스트는 `tmp_path` (보통
    `%TEMP%`) 를 쓰므로 별도 escape hatch 필요 — 본 함수는 default loader 에만
    적용하고 테스트용 loader 는 `enforce_path_guard=False` 로 우회.

    Windows case-insensitive FS 대응 (oracle 2 차 리뷰 C1):
        `Path.is_relative_to()` 는 대소문자 구분 비교. NTFS 등 case-insensitive
        FS 에서 drive letter / 폴더 케이싱이 다르면 정상 경로도 차단됨. 따라서
        `os.path.normcase()` 적용 후 비교 — Windows 면 lower-case 화, POSIX 면 no-op.
    """
    resolved = path.resolve()
    repo = _REPO_ROOT.resolve()
    # normcase: Windows → lower-case + 경로 separator normalize. POSIX → 그대로.
    resolved_norm = os.path.normcase(str(resolved))
    repo_norm = os.path.normcase(str(repo))
    # Trailing sep 보장으로 prefix match 의 디렉토리 경계 명확화.
    if not (resolved_norm == repo_norm or resolved_norm.startswith(repo_norm + os.sep)):
        raise CalendarDataError(
            f"calendar path {resolved} is outside repo root {repo} — "
            f"임의 path 의 캘린더 로드는 보안 정책상 금지."
        )
    return resolved


def _load_calendar(path: Path, *, enforce_path_guard: bool = True) -> TradingCalendar:
    """주어진 path 의 캘린더 JSON 을 schema 검증 + hash 검증 후 TradingCalendar 반환.

    검증 순서 — fail-fast:
    1. (선택) path 가 repo root 하위
    2. JSON parse
    3. JSON schema (구조)
    4. content_hash 일치 (factor_pack 과 동일 의미론)
    5. closed_days 가 토/일을 포함하지 않음 (자동 휴장 처리와 중복)
    6. closed_days / half_days date 가 coverage 내
    7. coverage.min_date <= coverage.max_date

    Args:
        path: 캘린더 JSON 경로.
        enforce_path_guard: True (default) 면 path 가 repo root 하위인지 검사.
            테스트가 `tmp_path` 사용 시 False 로 우회 — 운영 import path 에는
            본 함수 직접 노출 X (`DEFAULT_CALENDAR` 만 사용).

    Raises:
        CalendarDataError: schema / hash / 무결성 위반.
    """
    if enforce_path_guard:
        path = _assert_path_under_repo(path)
    with path.open(encoding="utf-8") as f:
        body = json.load(f)

    try:
        _VALIDATOR.validate(body)
    except ValidationError as exc:
        loc = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise CalendarDataError(f"schema violation at {loc}: {exc.message}") from exc

    # Content hash — factor_pack 과 동일 패턴.
    declared_hash = body[_HASH_FIELD]
    computed_hash = _compute_content_hash(body)
    if declared_hash != computed_hash:
        raise CalendarDataError(
            f"content_hash mismatch — declared={declared_hash} computed={computed_hash}. "
            f"데이터 변조 또는 hash stale — fill_content_hash() 로 갱신."
        )

    min_date = date.fromisoformat(body["coverage"]["min_date"])
    max_date = date.fromisoformat(body["coverage"]["max_date"])
    if min_date > max_date:
        raise CalendarDataError(
            f"coverage.min_date ({min_date}) > max_date ({max_date})"
        )

    closed_days = _parse_day_entries(body["closed_days"], min_date, max_date,
                                     allow_weekend=False, kind="closed_days")
    half_days_raw = body["half_days"]
    half_days = _parse_day_entries(half_days_raw, min_date, max_date,
                                   allow_weekend=True, kind="half_days")
    half_day_times = MappingProxyType({
        date.fromisoformat(e["date"]): e["early_close_time"]
        for e in half_days_raw
    })

    return TradingCalendar(
        closed_days=closed_days,
        half_days=half_days,
        min_date=min_date,
        max_date=max_date,
        version=body["version"],
        content_hash=computed_hash,
        verified_at=date.fromisoformat(body["verified_at"]),
        verified_by=body["verified_by"],
        verified_source=body["verified_source"],
        half_day_close_times=half_day_times,
    )


def _parse_day_entries(
    entries: list[dict],
    min_date: date,
    max_date: date,
    *,
    allow_weekend: bool,
    kind: str,
) -> frozenset[date]:
    """day entry list 를 frozenset[date] 로 변환 + 범위·중복·weekend 검사.

    Args:
        entries: JSON 의 dict list.
        min_date / max_date: coverage 범위 (inclusive).
        allow_weekend: closed_days 는 평일만 (False), half_days 는 weekend 도 가능 (True).
        kind: 에러 메시지의 필드명.
    """
    result: set[date] = set()
    for entry in entries:
        d = date.fromisoformat(entry["date"])
        if d < min_date or d > max_date:
            raise CalendarDataError(
                f"{kind} contains date {d} outside coverage "
                f"[{min_date}, {max_date}]"
            )
        if not allow_weekend and d.weekday() >= 5:
            raise CalendarDataError(
                f"{kind} contains weekend date {d} ({entry.get('reason', '')!r}) "
                f"— 토/일은 자동 휴장이므로 명시적으로 포함하지 말 것"
            )
        if d in result:
            raise CalendarDataError(
                f"{kind} contains duplicate date {d}"
            )
        result.add(d)
    return frozenset(result)


# =============================================================================
# Build-time utility — content_hash placeholder 갱신 (private, __all__ 미노출)
# =============================================================================

def _fill_content_hash(path: Path) -> str:
    """`content_hash` 를 실제 계산값으로 갱신. **Build-time / 테스트 전용**.

    개발자가 closed_days 추가/수정 후 본 함수 호출 → hash 자동 갱신. 운영 코드
    에서는 호출 금지 — immutable verified data 약속 위반. `_` prefix + `__all__`
    누락으로 wildcard import 차단 (oracle 2 차 리뷰 C2). 운영 환경에서 명시적
    동의 없이 본 함수가 실행되면 silent 한 데이터 변조와 동등.

    Returns:
        새 hash 값.
    """
    with path.open(encoding="utf-8") as f:
        body = json.load(f)
    new_hash = _compute_content_hash(body)
    body[_HASH_FIELD] = new_hash
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(body, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")
    return new_hash


# =============================================================================
# Module-level default — factor_pack.py 의 _SCHEMA 패턴과 동형
# =============================================================================

DEFAULT_CALENDAR: Final[TradingCalendar] = _load_calendar(_DATA_PATH)
"""기본 KRX 캘린더 — module import 시 1 회 로드. 운영 코드의 single source."""
