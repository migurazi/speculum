"""KOSIS 일배치 — ADR-0036 D8 / M9_PLAN #4 거시지표 적재 orchestrator.

매월 (또는 수동 트리거) KOSIS 통계청 API 로 거시지표를 fetch 하여
`macro_indicators` 테이블에 적재한다.

책임:
1. **per-indicator fetch-then-save** — adapter 시계열 fetch → citation save
   → MacroIndicatorRecord INSERT. citation → macro 순서 (FK 만족).
2. **SAVEPOINT failure isolation** — 한 지표 실패가 다른 지표 적재 막지 않음.
3. **idempotent** — UNIQUE `(indicator_id, reference_date, vintage_date)`.
   같은 날 재수집 시 IntegrityError → skip (중복 INSERT 방지).
4. **rate limit** — KOSIS 분당 1000 회 한도. throttle_seconds default 0.1
   (=분당 600 회, 안전 마진 40%).
5. **observed_date 주입** — vintage_date = observed_date (수집일, ADR-0036 D3).
   reference_date > observed_date 인 미래 row 는 skip + warning (F-08).
6. **batch_runs 영속화** — source="KOSIS", market=None. citation FK 선행
   INSERT 보장 (dart_daily.py 패턴 동일).

KOSIS 지표 목록 (_KOSIS_INDICATORS):
    dart_daily 의 "종목 loop" 에 대응하는 "지표 loop". 각 항목은
    (org_id, tbl_id, itm_id, obj_l, prd_se, label) 5-tuple.
    현재 4 지표 (전부 라이브 실측 검증 2026-06-18) —
      · 실업률    DT_1DA7001S / T80 / objL1=0  (경제활동인구조사, 월별)
      · 고용률    DT_1DA7001S / T90 / objL1=0  (동일 표)
      · 전산업생산 DT_1JH20201 / T1  / objL1=0  (산업활동동향 원지수, 농림어업 제외)
      · 경기선행  DT_1C8015   / T1  / objL1=A00 (경기종합지수, 선행종합지수 2020=100)

    ⚠ 과거 모든 KOSIS getList 가 `err=20 필수요청변수 누락` 으로 실패했는데, 원인은
      계정/키가 아니라 **adapter 의 endpoint 오류**였다(키는 유효). 데이터 조회 정본은
      `Param/statisticsParameterData.do` 이며, `statisticsData.do?method=getList` 는
      동일 파라미터로도 err=20 을 낸다(getMeta 만 그 경로 동작). kosis_adapter.py
      `_KOSIS_API_ENDPOINT` 에서 수정 완료. 키 형식은 raw base64 그대로(디코딩 시 err=11).

설계 결정:
1. **sync orchestration** — codebase 일관 (DartDailyBatch 동일).
2. **scheduler 미통합** — CLI / 운영 script 가 manual 호출.
3. **session.add() 직접 save** — MacroIndicatorRepository Protocol 에 save 메서드
   없음 (append-only 정책, session.add(macro_indicator_record_to_orm(record)) 패턴).
   Fake 테스트는 별도 FakeSession 또는 in-memory list 로 수집.
4. **SAVEPOINT activation** — session 주입 시 지표별 begin_nested(). Fake 모드
   (session=None) 는 nullcontext — no-op.

관련 ADR:
- ADR-0036 D3 (vintage=observed_date 근사), D4 (단일 objL), D5 (SoT 분리),
  D8 (batch 구현)
- ADR-0002 D3 (citation persistence)
- M9_PLAN §0, §3 #4
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
from app.adapters.kosis_adapter import KosisAdapter
from app.db.converters import macro_indicator_record_to_orm
from app.db.orm.batch_runs import BATCH_STATUS_PARTIAL, BATCH_STATUS_SUCCESS
from app.repositories.batch_run_repository import (
    BatchRunRepository,
    SqlBatchRunRepository,
)
from app.repositories.citation_repository import CitationRepository
from app.repositories.pit_protocols import MacroIndicatorRecord
from batch.alerts import BatchAlertHandler, NullAlertHandler

__all__ = ["KosisBatchSummary", "KosisDailyBatch"]

logger = logging.getLogger(__name__)


# =============================================================================
# KOSIS 지표 목록 상수
# =============================================================================

class _KosisIndicator(NamedTuple):
    """단일 KOSIS 지표 fetch 파라미터 묶음."""

    org_id: str
    tbl_id: str
    itm_id: str
    obj_l: str
    prd_se: str  # "M" 월별 등
    label: str   # 로그·summary 식별용


# 통계청 기관 ID — 공통.
_ORG_ID_STATISTICS_KOREA: Final[str] = "101"

# KOSIS 지표 loop 대상. db_field_provider.py _RESOLUTIONS 의 KOSIS 4종과 동일
# (test_kosis_batch_indicator_ids_match_field_resolutions 가 id 일치 강제).
# tbl_id/itm_id/obj_l 전부 라이브 실측 검증됨(2026-06-18, Param/statisticsParameterData.do
# 로 실데이터 반환 확인). 실업률·고용률은 경제활동인구조사 월별 단일 표(DT_1DA7001S)에
# 공존(itm T80/T90), 성별 축 objL1="0"(계). 전산업생산=산업활동동향 DT_1JH20201(원지수
# T1, 산업별 축 objL1="0" 농림어업 제외). 경기선행=경기종합지수 DT_1C8015(T1, 지수별
# 축 objL1="A00" 선행종합지수). 전부 prdSe="M".
_KOSIS_INDICATORS: Final[tuple[_KosisIndicator, ...]] = (
    _KosisIndicator(
        org_id=_ORG_ID_STATISTICS_KOREA,
        tbl_id="DT_1DA7001S",   # 경제활동인구조사 (월별)
        itm_id="T80",           # 실업률
        obj_l="0",              # 성별 = 계
        prd_se="M",
        label="kosis_unemployment_rate",
    ),
    _KosisIndicator(
        org_id=_ORG_ID_STATISTICS_KOREA,
        tbl_id="DT_1DA7001S",   # 동일 표 — 고용률 항목
        itm_id="T90",           # 고용률
        obj_l="0",              # 성별 = 계
        prd_se="M",
        label="kosis_employment_rate",
    ),
    _KosisIndicator(
        org_id=_ORG_ID_STATISTICS_KOREA,
        tbl_id="DT_1JH20201",   # 산업활동동향 전산업생산지수(원지수)
        itm_id="T1",            # 원지수
        obj_l="0",              # 산업별 = 전산업생산지수(농림어업 제외)
        prd_se="M",
        label="kosis_industrial_production",
    ),
    _KosisIndicator(
        org_id=_ORG_ID_STATISTICS_KOREA,
        tbl_id="DT_1C8015",     # 경기종합지수
        itm_id="T1",            # 경기종합지수
        obj_l="A00",            # 지수별 = 선행종합지수(2020=100)
        prd_se="M",
        label="kosis_leading_index",
    ),
)

# 최근 N 개월 기본값 — fetch 범위.
_DEFAULT_MONTHS_BACK: Final[int] = 24


# =============================================================================
# Summary dataclass
# =============================================================================

@dataclass(frozen=True, slots=True)
class KosisBatchSummary:
    """KOSIS 일배치 1 회 실행 결과.

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
# KosisDailyBatch
# =============================================================================

class KosisDailyBatch:
    """KOSIS 거시지표 일배치 orchestrator — single-shot run per observed_date.

    Args:
        adapter: KosisAdapter (api_key 또는 KOSIS_API_KEY env).
        citation_repo: SourceCitation persistence.
        session: SQLAlchemy session — 주입 시 지표별 SAVEPOINT 활성화.
            macro_indicator row 도 본 session 으로 add+flush. Fake 모드 (None)
            는 savepoint/write skip → 테스트에서 in-memory FakeSession 사용.
        throttle_seconds: 지표 fetch 사이 sleep (sec). KOSIS 분당 1000 회 한도,
            0.1 = 분당 600 회 (안전 마진). test 시 0.
        alert_handler: BatchAlertHandler. None 이면 NullAlertHandler.
        batch_run_repo: batch_runs 영속화. None + session 있으면
            SqlBatchRunRepository(session) default 구성.
        indicators: 처리할 지표 목록. None 이면 _KOSIS_INDICATORS 전체.
        months_back: fetch 범위 (start_prd 산정 — 현재월 기준 N 개월 전).
    """

    def __init__(
        self,
        *,
        adapter: KosisAdapter,
        citation_repo: CitationRepository,
        session: Session | None = None,
        throttle_seconds: float = 0.1,
        alert_handler: BatchAlertHandler | None = None,
        batch_run_repo: BatchRunRepository | None = None,
        indicators: Sequence[_KosisIndicator] | None = None,
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
        self._indicators: tuple[_KosisIndicator, ...] = tuple(
            indicators if indicators is not None else _KOSIS_INDICATORS
        )
        self._months_back = months_back

    def run(self, *, observed_date: date) -> KosisBatchSummary:
        """KOSIS 지표 목록 전체를 observed_date 기준으로 fetch + 적재.

        Args:
            observed_date: 수집 기준일 (vintage_date 주입값). 오늘 날짜 권장.

        Returns:
            KosisBatchSummary — 지표별 성공/실패/skip 집계.

        Note:
            본 메서드는 raise 하지 않음 — 지표 전체 실패도 summary 로 반환.
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
                start_prd, end_prd = _month_range(observed_date, self._months_back)

                # 지표별 SAVEPOINT — session 있을 때만. Fake 모드는 nullcontext.
                # dart_daily 패턴 동일: begin_nested() 는 예외 시 SAVEPOINT 자동
                # rollback + re-raise → 다음 지표 session 정상 진행 가능.
                savepoint: AbstractContextManager[object] = (
                    self._session.begin_nested()
                    if self._session is not None
                    else nullcontext()
                )
                try:
                    with savepoint:
                        rows_saved = self._process_indicator(
                            indicator=indicator,
                            start_prd=start_prd,
                            end_prd=end_prd,
                            batch_id=batch_id,
                            observed_date=observed_date,
                        )
                        total_rows += rows_saved
                        successes += 1
                except IntegrityError as exc:
                    # SAVEPOINT 자동 rollback 완료 → session 정상 상태.
                    # session.rollback() 직접 호출 금지 — 외부 트랜잭션 abort 유발.
                    logger.info(
                        "KOSIS idempotent skip (UNIQUE 충돌): %s observed_date=%s — %s",
                        indicator.label, observed_date, exc,
                    )
                    skipped += 1
                except AdapterError as exc:
                    # SAVEPOINT 자동 rollback 완료 → 다음 지표 진행 가능.
                    failures.append((indicator.label, str(exc)))
                    self._alert.on_failure(indicator.label, str(exc))
                except Exception as exc:  # noqa: BLE001
                    # SAVEPOINT 자동 rollback 완료 → 다음 지표 진행 가능.
                    reason = f"unexpected: {type(exc).__name__}: {exc}"
                    failures.append((indicator.label, reason))
                    self._alert.on_failure(indicator.label, reason)

                if self._throttle > 0:
                    time.sleep(self._throttle)

        finally:
            # try/finally 보장 — 지표 loop 외부 예외에도 finalize 호출.
            # 좀비 'running' batch_run 방지. run() 은 raise 안 함 계약 유지.
            summary = KosisBatchSummary(
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

        source="KOSIS", market=None (DART 패턴 동일 — 종목 단위 아님).
        """
        if self._batch_run_repo is None:
            return
        self._batch_run_repo.start(
            run_id=batch_id,
            market=None,
            source="KOSIS",
            started_at=started_at,
        )

    def _finalize_batch_run(self, summary: KosisBatchSummary) -> None:
        """배치 종료 시 batch_runs row finalize (UPDATE).

        failure_count>0 이면 status='partial', 아니면 'success'. KOSIS 는 지표
        수가 적어(현재 4개) 1개 실패도 의미있는 부분실패 — 운영 alerting 필요. partial 도
        성공분의 실 데이터를 commit 했으므로 freeze 후보·freshness 자격은
        success 와 동일 (collect_batch_versions / latest_successful 가 둘 다
        수용) — 라벨만 운영 가시성 위해 분리 (dart_daily 동일 패턴).
        """
        if self._batch_run_repo is None:
            return
        status = (
            BATCH_STATUS_PARTIAL if summary.failure_count > 0
            else BATCH_STATUS_SUCCESS
        )
        if summary.failure_count > 0:
            logger.warning(
                "KOSIS 배치 부분 실패 — batch_id=%s failure_count=%d failures=%s "
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
        indicator: _KosisIndicator,
        start_prd: str,
        end_prd: str,
        batch_id: UUID,
        observed_date: date,
    ) -> int:
        """단일 KOSIS 지표 fetch + citation save + macro_indicator save.

        fetch-then-save (DartDailyBatch._process_company 패턴):
            1. adapter.fetch_statistic — 실패 시 raise → SAVEPOINT ROLLBACK.
            2. citation save (FK 선행).
            3. MacroIndicatorRow → MacroIndicatorRecord 변환.
            4. reference_date > observed_date 미래 row skip + warning (F-08).
            5. session.add(macro_indicator_record_to_orm(record)) + flush.
            6. idempotent — UNIQUE 충돌 시 IntegrityError re-raise (session.rollback()
               직접 호출 없음). begin_nested() SAVEPOINT 가 자동 rollback → run()
               의 except IntegrityError 에서 skip 처리 (dart_daily 패턴 동일).

        Args:
            indicator: 지표 파라미터 묶음.
            start_prd: fetch 시작 기간 (YYYYMM 형식).
            end_prd: fetch 종료 기간 (YYYYMM 형식).
            batch_id: 본 배치 UUID.
            observed_date: vintage_date 주입값.

        Returns:
            INSERT 된 macro_indicator row 수.

        Raises:
            AdapterError: fetch 실패.
            IntegrityError: UNIQUE 충돌 (같은 날 재수집) — SAVEPOINT 자동 rollback
                후 run() 에서 catch, session.rollback() 직접 호출 없음.
        """
        result: FetchResult[tuple[MacroIndicatorRow, ...]] = (
            self._adapter.fetch_statistic(
                org_id=indicator.org_id,
                tbl_id=indicator.tbl_id,
                itm_id=indicator.itm_id,
                obj_l=indicator.obj_l,
                prd_se=indicator.prd_se,
                start_prd=start_prd,
                end_prd=end_prd,
                batch_id=batch_id,
                observed_date=observed_date,
            )
        )

        # KosisAdapter: 빈 array 시 citations=() — 데이터 없음, 정상 skip.
        if not result.data:
            logger.debug(
                "KOSIS 빈 결과 (no rows): label=%s start=%s end=%s",
                indicator.label, start_prd, end_prd,
            )
            return 0

        # citation 없으면 FetchResult invariant 위반.
        if not result.citations:
            raise AdapterError(
                f"KOSIS fetch returned rows but no citation for "
                f"label={indicator.label} — FetchResult invariant violation"
            )

        # 1. citation save (FK 선행 — dart_daily 패턴).
        if self._session is not None:
            for c in result.citations:
                self._citation_repo.save(c)

        citation_id = result.citations[0].id
        created_at = datetime.now(UTC)
        rows_saved = 0

        for row in result.data:
            # F-08: reference_date > observed_date 미래 row skip + warning.
            if row.reference_date > observed_date:
                logger.warning(
                    "KOSIS 미래 reference_date skip (F-08): label=%s "
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
                vintage_date=row.vintage_date,  # = observed_date (ADR-0036 D3)
                citation_id=citation_id,
                created_at=created_at,
            )

            if self._session is not None:
                # IntegrityError 는 여기서 catch 하지 않음 — begin_nested()
                # SAVEPOINT 컨텍스트가 자동 rollback + re-raise.
                # session.rollback() 직접 호출 시 외부 트랜잭션 abort(→
                # PendingRollbackError 연쇄 실패) — dart_daily 패턴 동일.
                self._session.add(macro_indicator_record_to_orm(record))
                self._session.flush()
                rows_saved += 1

        return rows_saved


# =============================================================================
# Helpers
# =============================================================================

def _month_range(observed_date: date, months_back: int) -> tuple[str, str]:
    """observed_date 기준 최근 months_back 개월 범위 → (start_prd, end_prd) YYYYMM.

    end_prd = observed_date 의 연월. start_prd = end_prd 에서 (months_back-1) 개월 전.
    월 경계 오류 없이 정수 연산으로 산정.
    """
    end_year = observed_date.year
    end_month = observed_date.month
    # months_back 개월 전 = end 기준 (months_back - 1) 개월 소급.
    total_months = end_year * 12 + end_month - 1  # 0-based total months
    start_total = total_months - (months_back - 1)
    start_year = start_total // 12
    start_month = start_total % 12 + 1

    start_prd = f"{start_year:04d}{start_month:02d}"
    end_prd = f"{end_year:04d}{end_month:02d}"
    return start_prd, end_prd
