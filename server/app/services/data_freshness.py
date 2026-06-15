"""데이터 신선도(stale) 진단 — M5 #4a (T-M5-04a, ADR-0033 D6).

source(KRX/DART)별 최신 성공 batch 의 시각과 stale 여부를 **사실로 고지**한다
(8 기둥 §2.1 Fidelity — "데이터 기준일 + stale 여부"). 본 모듈은 판정·해석 문구를
일절 생성하지 않는다 — `is_stale` bool 과 경과 일수(`elapsed_days`) 같은 **사실만**
산출하고, "위험"·"부정확" 등 평가 어휘는 쓰지 않는다 (§2.2 No Advice / §2.7
Observation). client 표시 layer (#6) 가 중립 톤으로 그 사실을 노출한다.

stale 임계 (ADR-0033 D6):
    - KRX (가격·시총·종목): 최신 성공 batch 가 **3 영업일** 초과 경과 시 stale.
      영업일(주말·휴장 제외) 기준 — KRX 데이터는 영업일마다 생산되므로 주말 경과를
      stale 로 오판하지 않도록 KRX 캘린더의 business_days_between 사용.
    - DART (재무): 분기 공시 주기 근사. ADR-0033 D6 은 "분기 + 60 일" 을 명시하나,
      본 구현은 운영 단순화로 **100 calendar days** 임계를 사용 (분기 90일 + 공시
      lag 근사). 임계는 ADR-0033 Consequences(Neutral) 에서 "운영 데이터로 재튜닝
      가능" 으로 명시된 값 — 정확한 분기 경계 계산이 아닌 보수적 calendar-days 근사.

결정성 (테스트):
    - `now` 는 파라미터 주입 — 본 모듈은 `datetime.now()` 를 직접 호출하지 않는다.
      엔드포인트(`routes/meta.py`)만 서버 현재 UTC 를 주입한다.
    - calendar 는 검증된 KRX 캘린더 인스턴스(`TradingCalendar`) 주입 — 운영은
      `DEFAULT_CALENDAR`, 테스트는 별도 instance 로 결정성 확보.

관련:
- ADR-0033 D6 — stale 임계 (3 영업일 / 분기+60일)
- M5_PLAN §3 #4 T-M5-04a — 신선도 진단
- `app/repositories/batch_run_repository.py` — latest_successful (데이터 기준일 source)
- `app/services/krx_calendar.py` — business_days_between (영업일 경과 계산)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final

from app.db.orm.batch_runs import BATCH_STATUS_PARTIAL
from app.services.krx_calendar import CalendarRangeError

if TYPE_CHECKING:
    from app.repositories.batch_run_repository import BatchRunRepository
    from app.services.krx_calendar import TradingCalendar

__all__ = [
    "DataFreshness",
    "SourceFreshness",
    "assess_data_freshness",
]

# batch_runs.source 값 — collect_batch_versions 와 동일 문자열 (typo drift 차단).
_SOURCE_KRX: Final[str] = "KRX"
_SOURCE_DART: Final[str] = "DART"
_SOURCE_KOSIS: Final[str] = "KOSIS"

# stale 임계 (ADR-0033 D6) — 사실 비교 상수일 뿐 판정 아님.
# KRX: 최신 성공 batch 가 3 영업일 초과 경과 시 stale (영업일 = 주말·휴장 제외).
_KRX_STALE_BUSINESS_DAYS: Final[int] = 3
# DART: 분기 공시 주기 근사 — ADR-0033 D6 "분기 + 60일" 의 운영 calendar-days
# 근사값. 재튜닝 가능 (ADR-0033 Consequences Neutral — "운영 데이터로 재튜닝 가능").
_DART_STALE_CALENDAR_DAYS: Final[int] = 100
# KOSIS: 월간 공표 + lag 근사 — 월간 통계 공표 주기(30일) + 집계·공표 lag 여유.
# ADR-0036 D7. 재튜닝 가능.
_KOSIS_STALE_CALENDAR_DAYS: Final[int] = 45


@dataclass(frozen=True, slots=True)
class SourceFreshness:
    """한 source 의 신선도 사실 — 판정·해석 문구 없음 (§2.1 Fidelity).

    Attributes:
        source: "KRX" | "DART" | "KOSIS".
        latest_batch_at: 최신 성공 batch 의 종료 시각(ended_at, UTC tz-aware).
            그 source 의 성공 batch 가 한 번도 없으면 None (데이터 없음).
        is_stale: stale 여부 (사실 — 임계 초과 경과 또는 데이터 없음).
            batch 부재 = 데이터 없음 = stale (True).
        elapsed_days: 최신 성공 batch 종료일부터 `now` 까지 경과 calendar days.
            batch 부재면 None (경과 산정 대상 없음). KRX 도 표시는 calendar days
            (사실) — stale 판정만 영업일 기준 (영업일 경과 ≠ calendar 경과).
        latest_batch_partial: 신선도 기준이 된 최신 batch 가 부분 실패(status=
            'partial' — 일부 종목/회사 fetch 실패하고도 성공분은 commit)였는지의
            사실. batch 부재 또는 완전 성공(status='success')이면 False. 해석·판정
            문구 없음 (§2.1 Fidelity / §2.2 No Advice) — "partial 이라 부정확/위험"
            같은 평가 어휘는 client layer 도 쓰지 않는다. BATCH_STATUS_PARTIAL 도
            freeze 후보·freshness 소스 자격을 가지므로(latest_successful 가 success+
            partial 수용) 이 필드가 True 여도 latest_batch_at/elapsed_days 산정에는
            영향 없다 — 단지 "그 batch 가 부분 실패였다"는 부가 사실의 노출.
    """

    source: str
    latest_batch_at: datetime | None
    is_stale: bool
    elapsed_days: int | None
    latest_batch_partial: bool


@dataclass(frozen=True, slots=True)
class DataFreshness:
    """KRX·DART·KOSIS source 신선도 진단 결과 + 진단 기준 시각.

    Attributes:
        krx: KRX source 신선도.
        dart: DART source 신선도.
        kosis: KOSIS source 신선도 (월간 공표 주기, M9 #3, ADR-0036 D7).
        as_of: 본 진단의 기준 시각(now, UTC tz-aware) — 사실 재현/감사용.
    """

    krx: SourceFreshness
    dart: SourceFreshness
    kosis: SourceFreshness
    as_of: datetime


def assess_data_freshness(
    *,
    now: datetime,
    batch_run_repo: BatchRunRepository,
    calendar: TradingCalendar,
) -> DataFreshness:
    """source별 최신 성공 batch 시각 + stale 여부를 사실로 산출 (M5 #4a).

    판정·해석 문구를 생성하지 않는다 — `is_stale` bool 과 `elapsed_days` 사실만
    (§2.1 Fidelity / §2.2 No Advice). `now` 는 호출자(엔드포인트) 주입 — 본 함수는
    `datetime.now()` 를 직접 호출하지 않는다 (결정성).

    Args:
        now: 진단 기준 시각 (UTC tz-aware). 테스트는 결정성 위해 주입.
        batch_run_repo: latest_successful(source) 로 최신 성공 batch 조회.
        calendar: 검증된 KRX 캘린더 — KRX 영업일 경과 계산 (business_days_between).

    Returns:
        `DataFreshness` — krx/dart/kosis 각 SourceFreshness + as_of(now).
    """
    krx = _assess_krx(now=now, batch_run_repo=batch_run_repo, calendar=calendar)
    dart = _assess_dart(now=now, batch_run_repo=batch_run_repo)
    kosis = _assess_kosis(now=now, batch_run_repo=batch_run_repo)
    return DataFreshness(krx=krx, dart=dart, kosis=kosis, as_of=now)


def _assess_krx(
    *,
    now: datetime,
    batch_run_repo: BatchRunRepository,
    calendar: TradingCalendar,
) -> SourceFreshness:
    """KRX 신선도 — stale = 최신 성공 batch 종료일~now 영업일 수 > 3 (ADR-0033 D6).

    batch 부재면 데이터 없음 = stale (is_stale=True, elapsed_days=None). 영업일
    계산은 KRX 캘린더 `business_days_between(..., inclusivity="right")` — batch
    종료일 자체는 제외하고 그 **이후** now_date 까지의 영업일 수 (= 경과 영업일).
    M5 지시서의 `(ended_at.date(), now.date())` 표기(시작 제외·끝 포함)와 일치
    하며, batch 종료일이 영업일이 아니어도(이론상) 정확. 같은 영업일/직후는
    경과 0 → not stale, 4 영업일째 경과부터 stale (> 3).
    """
    record = batch_run_repo.latest_successful(_SOURCE_KRX)
    if record is None:
        # 데이터 없음 — stale (look-ahead 와 무관, 단순 부재 사실).
        # batch 부재 = partial 도 아님 (False).
        return SourceFreshness(
            source=_SOURCE_KRX,
            latest_batch_at=None,
            is_stale=True,
            elapsed_days=None,
            latest_batch_partial=False,
        )

    batch_date = record.ended_at.date()
    now_date = now.date()
    # elapsed_days 는 calendar days 사실 (표시용 — KRX 도 calendar 로 노출).
    elapsed_days = (now_date - batch_date).days

    # stale 판정만 영업일 기준. inclusivity="right" = batch 종료일 제외, now_date
    # 포함 → "batch 종료일 이후 경과 영업일 수". now_date <= batch_date (같은 날 /
    # 시계 역행) 면 경과 0 (음수 stale 방지 — business_days_between 은 end<start
    # 시 ValueError 라 사전 분기).
    if now_date <= batch_date:
        elapsed_business_days = 0
    else:
        try:
            elapsed_business_days = calendar.business_days_between(
                batch_date, now_date, inclusivity="right",
            )
        except CalendarRangeError:
            # batch 종료일 또는 now 가 verified 캘린더 범위 밖 — 영업일 수 산정
            # 불가. 그러나 calendar days 경과(elapsed_days)는 임계(3 영업일 ~ 약
            # 5 calendar days)를 크게 초과한 상태이므로 stale 로 사실 고지(사실:
            # 범위 밖일 만큼 오래됨). 영업일 추정은 하지 않음(silent 추정 금지,
            # §2.1) — 임계 초과를 calendar days 로 보수 판정.
            elapsed_business_days = elapsed_days
    is_stale = elapsed_business_days > _KRX_STALE_BUSINESS_DAYS
    return SourceFreshness(
        source=_SOURCE_KRX,
        latest_batch_at=record.ended_at,
        is_stale=is_stale,
        elapsed_days=elapsed_days,
        latest_batch_partial=record.status == BATCH_STATUS_PARTIAL,
    )


def _assess_dart(
    *,
    now: datetime,
    batch_run_repo: BatchRunRepository,
) -> SourceFreshness:
    """DART 신선도 — stale = 최신 성공 batch 종료일~now 경과 > 100 calendar days.

    분기 공시 주기 근사 (ADR-0033 D6 "분기 + 60일" 의 운영 calendar-days 근사,
    재튜닝 가능). batch 부재면 데이터 없음 = stale (is_stale=True, elapsed_days=None).
    KRX 와 달리 영업일이 아닌 calendar days — 재무 공시는 영업일 주기가 아니라
    분기 주기이므로 단순 경과 일수가 자연스러운 사실.
    """
    record = batch_run_repo.latest_successful(_SOURCE_DART)
    if record is None:
        # batch 부재 = 데이터 없음 = stale, partial 도 아님 (False) — _assess_krx 동일.
        return SourceFreshness(
            source=_SOURCE_DART,
            latest_batch_at=None,
            is_stale=True,
            elapsed_days=None,
            latest_batch_partial=False,
        )

    elapsed_days = (now.date() - record.ended_at.date()).days
    is_stale = elapsed_days > _DART_STALE_CALENDAR_DAYS
    return SourceFreshness(
        source=_SOURCE_DART,
        latest_batch_at=record.ended_at,
        is_stale=is_stale,
        elapsed_days=elapsed_days,
        latest_batch_partial=record.status == BATCH_STATUS_PARTIAL,
    )


def _assess_kosis(
    *,
    now: datetime,
    batch_run_repo: BatchRunRepository,
) -> SourceFreshness:
    """KOSIS 신선도 — stale = 최신 성공 batch 종료일~now 경과 > 45 calendar days.

    월간 공표 주기 근사 (ADR-0036 D7 — 월간 통계 30일 주기 + 집계·공표 lag 여유).
    `_assess_dart` calendar-days 패턴 동일. batch 부재면 stale (데이터 없음).
    """
    record = batch_run_repo.latest_successful(_SOURCE_KOSIS)
    if record is None:
        # batch 부재 = 데이터 없음 = stale, partial 도 아님 (False) — _assess_krx 동일.
        return SourceFreshness(
            source=_SOURCE_KOSIS,
            latest_batch_at=None,
            is_stale=True,
            elapsed_days=None,
            latest_batch_partial=False,
        )

    elapsed_days = (now.date() - record.ended_at.date()).days
    is_stale = elapsed_days > _KOSIS_STALE_CALENDAR_DAYS
    return SourceFreshness(
        source=_SOURCE_KOSIS,
        latest_batch_at=record.ended_at,
        is_stale=is_stale,
        elapsed_days=elapsed_days,
        latest_batch_partial=record.status == BATCH_STATUS_PARTIAL,
    )
