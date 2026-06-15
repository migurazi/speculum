"""ECOS 일배치 — ADR-0003 D8 / T64 거시지표 적재 orchestrator.

매일 (또는 수동 트리거) 한국은행 ECOS API 로 거시지표를 fetch 하여
`macro_indicators` 테이블에 적재한다. KosisDailyBatch 의 자매 배치 — 동일한
fetch-then-save / SAVEPOINT / idempotent 패턴을 ECOS fetch_statistic 시그니처에
맞춰 미러한다 (ADR-0036 D5: ECOS 가 거시지표 1차 SoT).

책임:
1. **per-indicator fetch-then-save** — adapter 시계열 fetch → citation save
   → MacroIndicatorRecord INSERT. citation → macro 순서 (FK 만족).
2. **SAVEPOINT failure isolation** — 한 지표 실패가 다른 지표 적재 막지 않음.
3. **idempotent** — UNIQUE `(indicator_id, reference_date, vintage_date)`.
   같은 날 재수집 시 IntegrityError → skip (중복 INSERT 방지).
4. **rate limit** — ECOS 분당 한도. throttle_seconds default 0.5
   (보수적 — KOSIS 보다 ECOS 응답이 느릴 수 있음, ecos_adapter timeout 30s).
5. **observed_date 주입** — vintage_date = observed_date (관측 시점 근사,
   ECOS 는 공표일/개정일 미제공 — base.py MacroIndicatorRow docstring).
   reference_date > observed_date 인 미래 row 는 skip + warning (F-08).
6. **batch_runs 영속화** — source="ECOS", market=None. citation FK 선행
   INSERT 보장 (dart_daily.py / kosis_daily.py 패턴 동일).

ECOS 지표 목록 (_ECOS_INDICATORS):
    dart_daily 의 "종목 loop" 에 대응하는 "지표 loop". 각 항목은
    (stat_code, item_code, cycle, label) 4-tuple. db_field_provider.py
    _RESOLUTIONS 의 ECOS 2종 (ecos_base_rate=722Y001/0101000,
    ecos_cpi=901Y009/0) 과 동일. EcosAdapter.make_indicator_id 가
    "STAT_CODE/ITEM_CODE" 로 결합하므로 indicator_id 가 _RESOLUTIONS 와 1:1.

설계 결정:
1. **sync orchestration** — codebase 일관 (DartDailyBatch/KosisDailyBatch 동일).
2. **scheduler 미통합** — CLI (batch/scheduler.py) 가 manual 호출.
3. **session.add() 직접 save** — MacroIndicatorRepository Protocol 에 save 메서드
   없음 (append-only 정책, session.add(macro_indicator_record_to_orm(record)) 패턴).
   Fake 테스트는 in-memory FakeSession 으로 수집 (kosis_daily 와 동일).
4. **SAVEPOINT activation** — session 주입 시 지표별 begin_nested(). Fake 모드
   (session=None) 는 nullcontext — no-op.
5. **월별(M) cycle 만 지원** — 현 2 지표 모두 cycle="M". 비월별 cycle 지표는
   _period_range 가 ValueError → 지표 단위 failure 로 격리 (확장 시 cycle 분기
   추가). ECOS 기준금리는 일별(D)도 제공하나 월별로 등록 (월말/월평균 기준).

관련 ADR:
- ADR-0003 D2 (canonical), D3 (citation), D8 (batch)
- ADR-0002 D3 (citation persistence)
- ADR-0036 D5 (ECOS 1차 SoT)
- T62 MacroIndicatorRecord (vintage 이중 시간축), T63 EcosAdapter
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final, NamedTuple
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.base import AdapterError, FetchResult, MacroIndicatorRow
from app.adapters.ecos_adapter import EcosAdapter
from app.db.converters import macro_indicator_record_to_orm
from app.db.orm.batch_runs import BATCH_STATUS_PARTIAL, BATCH_STATUS_SUCCESS
from app.repositories.batch_run_repository import (
    BatchRunRepository,
    SqlBatchRunRepository,
)
from app.repositories.citation_repository import CitationRepository
from app.repositories.pit_protocols import MacroIndicatorRecord
from batch.alerts import BatchAlertHandler, NullAlertHandler

__all__ = ["EcosBatchSummary", "EcosDailyBatch"]

logger = logging.getLogger(__name__)


# =============================================================================
# ECOS 지표 목록 상수
# =============================================================================

class _EcosIndicator(NamedTuple):
    """단일 ECOS 지표 fetch 파라미터 묶음.

    Attributes:
        stat_code: ECOS 통계표코드 (예: "722Y001").
        item_code: ECOS 항목코드1 (예: "0101000").
        cycle: 시계열 주기 — 현재 "M" (월별) 만 지원.
        label: 로그·summary 식별용. db_field_provider field 명과 일관.
    """

    stat_code: str
    item_code: str
    cycle: str   # "M" 월별 (현 지표 모두 동일)
    label: str


# ECOS 지표 loop 대상. db_field_provider.py _RESOLUTIONS 의 ECOS 2종과 동일.
# indicator_id = make_indicator_id(stat_code, item_code) = "STAT_CODE/ITEM_CODE".
_ECOS_INDICATORS: Final[tuple[_EcosIndicator, ...]] = (
    _EcosIndicator(
        stat_code="722Y001",
        item_code="0101000",
        cycle="M",
        label="ecos_base_rate",       # 한국은행 기준금리.
    ),
    _EcosIndicator(
        stat_code="901Y009",
        item_code="0",
        cycle="M",
        label="ecos_cpi",             # 소비자물가지수.
    ),
)

# 최근 N 개월 기본값 — fetch 범위 (kosis_daily 와 동일 default).
_DEFAULT_MONTHS_BACK: Final[int] = 24


# =============================================================================
# Summary dataclass
# =============================================================================

@dataclass(frozen=True, slots=True)
class EcosBatchSummary:
    """ECOS 일배치 1 회 실행 결과.

    Attributes:
        batch_id: 본 실행의 UUID. 모든 citation 의 batch_id 와 일치.
        observed_date: fetch 기준일 (vintage_date 주입값).
        target_count: 처리 시도한 지표 수.
        success_count: fetch + save 성공한 지표 수.
        failure_count: 지표별 fetch/save 실패 수.
        skipped_count: idempotent skip (중복 UNIQUE) 포함 기타 skip 수.
        total_rows_saved: 실제 INSERT 된 macro_indicator row 합계.
        failures: (label, reason) 정렬 tuple.
        started_at / ended_at: UTC tz-aware.
    """

    batch_id: UUID
    observed_date: date
    target_count: int
    success_count: int
    failure_count: int
    skipped_count: int
    total_rows_saved: int
    failures: Sequence[tuple[str, str]]
    started_at: datetime
    ended_at: datetime


# =============================================================================
# EcosDailyBatch
# =============================================================================

class EcosDailyBatch:
    """ECOS 거시지표 일배치 orchestrator — single-shot run per observed_date.

    Args:
        adapter: EcosAdapter (api_key 또는 ECOS_API_KEY env).
        citation_repo: SourceCitation persistence.
        session: SQLAlchemy session — 주입 시 지표별 SAVEPOINT 활성화.
            macro_indicator row 도 본 session 으로 add+flush. Fake 모드 (None)
            는 savepoint/write skip → 테스트에서 in-memory FakeSession 사용.
        throttle_seconds: 지표 fetch 사이 sleep (sec). ECOS rate limit 보수
            마진. test 시 0.
        alert_handler: BatchAlertHandler. None 이면 NullAlertHandler.
        batch_run_repo: batch_runs 영속화. None + session 있으면
            SqlBatchRunRepository(session) default 구성.
        indicators: 처리할 지표 목록. None 이면 _ECOS_INDICATORS 전체.
        months_back: fetch 범위 (start 산정 — 현재월 기준 N 개월 전).
    """

    def __init__(
        self,
        *,
        adapter: EcosAdapter,
        citation_repo: CitationRepository,
        session: Session | None = None,
        throttle_seconds: float = 0.5,
        alert_handler: BatchAlertHandler | None = None,
        batch_run_repo: BatchRunRepository | None = None,
        indicators: Sequence[_EcosIndicator] | None = None,
        months_back: int = _DEFAULT_MONTHS_BACK,
    ) -> None:
        self._adapter = adapter
        self._citation_repo = citation_repo
        self._session = session
        self._throttle = throttle_seconds
        self._alert: BatchAlertHandler = (
            alert_handler if alert_handler is not None else NullAlertHandler()
        )
        self._batch_run_repo: BatchRunRepository | None = (
            batch_run_repo
            if batch_run_repo is not None
            else (SqlBatchRunRepository(session) if session is not None else None)
        )
        self._indicators: tuple[_EcosIndicator, ...] = tuple(
            indicators if indicators is not None else _ECOS_INDICATORS
        )
        # months_back < 1 이면 _period_range 가 start > end 를 만들어 ECOS 가 빈
        # 결과 → 전 지표 silent skip (success 인데 0 row). 명시 검증으로 차단.
        if months_back < 1:
            raise ValueError(
                f"months_back must be >= 1, got {months_back}"
            )
        self._months_back = months_back

    def run(self, *, observed_date: date) -> EcosBatchSummary:
        """ECOS 지표 목록 전체를 observed_date 기준으로 fetch + 적재.

        Args:
            observed_date: 수집 기준일 (vintage_date 주입값). 오늘 날짜 권장.

        Returns:
            EcosBatchSummary — 지표별 성공/실패/skip 집계.

        Note:
            본 메서드는 raise 하지 않음 — 지표 전체 실패도 summary 로 반환
            (kosis_daily / dart_daily 계약 동일).
        """
        batch_id = uuid4()
        started_at = datetime.now(UTC)

        # batch_runs INSERT (status='running') — citation FK 보장을 위해 선행.
        self._start_batch_run(batch_id=batch_id, started_at=started_at)

        successes = 0
        failures: list[tuple[str, str]] = []
        skipped = 0
        total_rows = 0

        try:
            for indicator in self._indicators:
                # 지표별 SAVEPOINT — session 있을 때만. Fake 모드는 nullcontext.
                # begin_nested() 는 예외 시 SAVEPOINT 자동 rollback + re-raise →
                # 다음 지표 session 정상 진행 가능 (kosis_daily 패턴 동일).
                savepoint: AbstractContextManager[object] = (
                    self._session.begin_nested()
                    if self._session is not None
                    else nullcontext()
                )
                try:
                    with savepoint:
                        rows_saved = self._process_indicator(
                            indicator=indicator,
                            batch_id=batch_id,
                            observed_date=observed_date,
                        )
                        total_rows += rows_saved
                        successes += 1
                except IntegrityError as exc:
                    # SAVEPOINT 자동 rollback 완료 → session 정상 상태.
                    # session.rollback() 직접 호출 금지 — 외부 트랜잭션 abort 유발.
                    logger.info(
                        "ECOS idempotent skip (UNIQUE 충돌): %s observed_date=%s — %s",
                        indicator.label, observed_date, exc,
                    )
                    skipped += 1
                except AdapterError as exc:
                    # SAVEPOINT 자동 rollback 완료 → 다음 지표 진행 가능.
                    failures.append((indicator.label, str(exc)))
                    self._alert.on_failure(indicator.label, str(exc))
                except Exception as exc:  # noqa: BLE001
                    # SAVEPOINT 자동 rollback 완료 → 다음 지표 진행 가능.
                    # _period_range 의 ValueError (비월별 cycle) 도 여기서 격리.
                    reason = f"unexpected: {type(exc).__name__}: {exc}"
                    failures.append((indicator.label, reason))
                    self._alert.on_failure(indicator.label, reason)

                if self._throttle > 0:
                    time.sleep(self._throttle)

        finally:
            # try/finally 보장 — 지표 loop 외부 예외에도 finalize 호출.
            # 좀비 'running' batch_run 방지. run() 은 raise 안 함 계약 유지.
            summary = EcosBatchSummary(
                batch_id=batch_id,
                observed_date=observed_date,
                target_count=len(self._indicators),
                success_count=successes,
                failure_count=len(failures),
                skipped_count=skipped,
                total_rows_saved=total_rows,
                failures=tuple(sorted(failures)),
                started_at=started_at,
                ended_at=datetime.now(UTC),
            )
            self._finalize_batch_run(summary)

        self._alert.on_complete(summary)
        return summary

    # =========================================================================
    # 내부 — batch_runs 영속화
    # =========================================================================

    def _start_batch_run(
        self,
        *,
        batch_id: UUID,
        started_at: datetime,
    ) -> None:
        """배치 시작 시 batch_runs row INSERT (status='running').

        source="ECOS", market=None (KOSIS/DART 패턴 동일 — 종목 단위 아님).
        """
        if self._batch_run_repo is None:
            return
        self._batch_run_repo.start(
            run_id=batch_id,
            market=None,
            source="ECOS",
            started_at=started_at,
        )

    def _finalize_batch_run(self, summary: EcosBatchSummary) -> None:
        """배치 종료 시 batch_runs row finalize (UPDATE).

        failure_count>0 이면 status='partial', 아니면 'success'. partial 도
        성공분의 실 데이터를 commit 했으므로 freeze 후보·freshness 자격은
        success 와 동일 (collect_batch_versions / latest_successful 가 둘 다
        수용) — 라벨만 운영 가시성 위해 분리 (kosis/dart 동일 패턴).
        """
        if self._batch_run_repo is None:
            return
        status = (
            BATCH_STATUS_PARTIAL if summary.failure_count > 0
            else BATCH_STATUS_SUCCESS
        )
        if summary.failure_count > 0:
            logger.warning(
                "ECOS 배치 부분 실패 — batch_id=%s failure_count=%d failures=%s "
                "(status=partial 로 저장)",
                summary.batch_id, summary.failure_count, summary.failures,
            )
        self._batch_run_repo.finalize(
            run_id=summary.batch_id,
            ended_at=summary.ended_at,
            success_count=summary.success_count,
            status=status,
        )

    # =========================================================================
    # 내부 — 지표별 처리
    # =========================================================================

    def _process_indicator(
        self,
        *,
        indicator: _EcosIndicator,
        batch_id: UUID,
        observed_date: date,
    ) -> int:
        """단일 ECOS 지표 fetch + citation save + macro_indicator save.

        fetch-then-save (KosisDailyBatch._process_indicator 패턴 동일):
            1. _period_range — cycle 별 (start, end) 산정. 비월별 cycle 은
               ValueError → run() 의 except 에서 지표 단위 격리.
            2. adapter.fetch_statistic — 실패 시 raise → SAVEPOINT ROLLBACK.
            3. 빈 결과 (INFO-200) → rows_saved=0 정상 return.
            4. citation save (FK 선행).
            5. MacroIndicatorRow → MacroIndicatorRecord 변환.
            6. reference_date > observed_date 미래 row skip + warning (F-08).
            7. session.add(macro_indicator_record_to_orm(record)) + flush.
            8. idempotent — UNIQUE 충돌 시 IntegrityError re-raise (SAVEPOINT
               자동 rollback → run() 의 except IntegrityError 에서 skip).

        Returns:
            INSERT 된 macro_indicator row 수.

        Raises:
            AdapterError: fetch 실패 / FetchResult invariant 위반.
            IntegrityError: UNIQUE 충돌 (같은 날 재수집) — SAVEPOINT 자동 rollback
                후 run() 에서 catch, session.rollback() 직접 호출 없음.
            ValueError: 비월별 cycle (현 미지원) — run() 의 generic except 격리.
        """
        start, end = _period_range(
            observed_date, self._months_back, indicator.cycle,
        )

        result: FetchResult[tuple[MacroIndicatorRow, ...]] = (
            self._adapter.fetch_statistic(
                stat_code=indicator.stat_code,
                item_code=indicator.item_code,
                cycle=indicator.cycle,
                start=start,
                end=end,
                batch_id=batch_id,
                observed_date=observed_date,
            )
        )

        # EcosAdapter: INFO-200 (데이터없음) 시 data=() citations=() — 정상 skip.
        if not result.data:
            logger.debug(
                "ECOS 빈 결과 (no rows): label=%s start=%s end=%s",
                indicator.label, start, end,
            )
            return 0

        # citation 없으면 FetchResult invariant 위반.
        if not result.citations:
            raise AdapterError(
                f"ECOS fetch returned rows but no citation for "
                f"label={indicator.label} — FetchResult invariant violation"
            )

        # 1. citation save (FK 선행 — kosis_daily / dart_daily 패턴).
        if self._session is not None:
            for c in result.citations:
                self._citation_repo.save(c)

        # EcosAdapter.fetch_statistic 은 retrieval 당 단일 citation 계약 (M-5) —
        # 전 row 가 같은 fetch 의 산물이므로 citations[0] 을 모든 record 에 공유.
        citation_id = result.citations[0].id
        created_at = datetime.now(UTC)
        rows_saved = 0

        for row in result.data:
            # F-08: reference_date > observed_date 미래 row skip + warning.
            if row.reference_date > observed_date:
                logger.warning(
                    "ECOS 미래 reference_date skip (F-08): label=%s "
                    "reference_date=%s > observed_date=%s",
                    indicator.label, row.reference_date, observed_date,
                )
                continue

            record = MacroIndicatorRecord(
                id=uuid4(),
                indicator_id=row.indicator_id,
                reference_date=row.reference_date,
                value=row.value,
                unit=row.unit,
                vintage_date=row.vintage_date,  # = observed_date (관측 근사).
                citation_id=citation_id,
                created_at=created_at,
            )

            if self._session is not None:
                # IntegrityError 는 여기서 catch 하지 않음 — begin_nested()
                # SAVEPOINT 컨텍스트가 자동 rollback + re-raise.
                # session.rollback() 직접 호출 시 외부 트랜잭션 abort(→
                # PendingRollbackError 연쇄 실패) — kosis_daily 패턴 동일.
                self._session.add(macro_indicator_record_to_orm(record))
                self._session.flush()
                rows_saved += 1

        return rows_saved


# =============================================================================
# Helpers
# =============================================================================

def _period_range(
    observed_date: date, months_back: int, cycle: str,
) -> tuple[str, str]:
    """observed_date 기준 fetch 범위 → (start, end) ECOS TIME 형식 string.

    cycle 별 ECOS TIME 형식 (ecos_adapter._CYCLE_TIME_LENGTHS 와 일관):
        M (월별): YYYYMM. end = observed_date 의 연월, start = (months_back-1)
            개월 전. 월 경계 오류 없이 정수 연산으로 산정 (_month_range 동일).

    현재 _ECOS_INDICATORS 가 모두 cycle="M" 이므로 월별만 지원. 다른 cycle
    (D/Q/A/S) 지표 추가 시 본 helper 에 분기 추가 — 그 전까지는 ValueError 로
    명시 실패시켜 잘못된 형식의 fetch 를 차단한다 (silent 오류 회피).

    Args:
        observed_date: 수집 기준일.
        months_back: 월별 cycle 의 소급 개월 수 (>= 1).
        cycle: "M" (현 유일 지원).

    Returns:
        (start, end) — cycle 형식 string tuple.

    Raises:
        ValueError: 비월별 cycle (미지원) — 호출자 (run loop) 가 지표 단위 격리.
    """
    if cycle != "M":
        raise ValueError(
            f"ECOS _period_range 미지원 cycle={cycle!r} — 현재 월별(M) 만 지원. "
            f"D/Q/A/S 지표 추가 시 분기 구현 필요."
        )
    end_year = observed_date.year
    end_month = observed_date.month
    # months_back 개월 전 = end 기준 (months_back - 1) 개월 소급.
    total_months = end_year * 12 + end_month - 1  # 0-based total months
    start_total = total_months - (months_back - 1)
    start_year = start_total // 12
    start_month = start_total % 12 + 1

    start = f"{start_year:04d}{start_month:02d}"
    end = f"{end_year:04d}{end_month:02d}"
    return start, end
