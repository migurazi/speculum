"""KRX 일배치 — ADR-0003 + M0_PLAN T18 의 orchestrator.

매일 KRX 16:30 KST 트리거 (현재는 manual entry — APScheduler 통합은 별도
cycle). 책임:

1. **휴장일 skip** — TradingCalendar.is_business_day check.
2. **Universe fetch** — pykrx 의 KOSPI/KOSDAQ 활성 종목 list.
3. **종목별 OHLCV + market_cap fetch** — pykrx primary.
4. **FDR cross-check (optional)** — ConflictDetector 가 estimated_fields 제외
   비교. conflict 는 summary 에 list, alert 는 호출자 책임.
5. **Citation save + Price save** — SqlCitationRepository → SqlPriceRepository
   순서 (citation_id FK 만족).
6. **Failure isolation** — 한 종목 fetch 실패가 batch 전체 멈춤 X. summary
   의 failures 로 alerting.
7. **Rate limit** — 종목 사이 sleep (configurable, test 시 0). ADR-0003 D6
   의 1.5s throttling 의 simplified version.

설계 결정:

1. **sync orchestration** — codebase 정책 일관. async 전환은 M1+ backlog.
2. **scheduler 통합 완료** — `batch.scheduler` 의 `run_krx_job` / `_do_krx` 가
   본 orchestrator 를 KOSPI+KOSDAQ 시장별로 실행 (`--job krx` 또는 `all`).
   pykrx/FDR 출처라 API 키 불필요 → 키 미발급 상태에서도 가격/시총 적재 가능.
3. **MarketCap 저장 (Phase B 합류)** — `market_cap_repo` 주입 시 fetch 한
   market_cap row 를 MarketCapRecord 로 변환하여 영구화. citation 은 prices 와
   동일하게 종목별 SAVEPOINT 안에서 먼저 save → market_cap row 의 citation_id
   FK 연결. `market_cap_repo` None (Fake-only 호환) 이면 fetch 만 + summary 에
   raw rows 포함 (기존 동작). 자사주 (shares_treasury) 는 pykrx None 그대로
   영구화 — 절대 0 으로 가정 안 함 (DART 보강 별도 cycle, T48c).
4. **신규/폐지 detection 미구현** — universe diff 는 별도 stocks_master.upsert
   사이클 (T13 Phase B 와 묶음).

관련 ADR:
- ADR-0003 D4 (충돌 처리), D6 (rate limit)
- ADR-0008 D2 (KRX 영업일 캘린더)
- ADR-0002 D3 (Source Citation persistence)
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.adapters.base import (
    AdapterError,
    FetchResult,
    MarketCapRow,
    OHLCVRow,
)
from app.adapters.fdr_adapter import FdrAdapter
from app.adapters.pykrx_adapter import PykrxAdapter
from app.db.orm.batch_runs import BATCH_STATUS_SKIPPED, BATCH_STATUS_SUCCESS
from app.repositories.batch_run_repository import (
    BatchRunRepository,
    SqlBatchRunRepository,
)
from app.repositories.citation_repository import CitationRepository
from app.repositories.pit_protocols import (
    MarketCapRecord,
    MarketCapRepository,
    PriceRecord,
    PriceRepository,
)
from app.services.conflict_detector import (
    ConflictDetectionResult,
    ConflictDetector,
)
from app.services.krx_calendar import TradingCalendar
from batch.alerts import BatchAlertHandler, NullAlertHandler

__all__ = ["BatchSummary", "KrxDailyBatch"]


@dataclass(frozen=True, slots=True)
class BatchSummary:
    """일배치 1 회 실행 결과.

    Attributes:
        batch_id: 본 실행의 UUID. 모든 citation 의 batch_id 와 일치.
        as_of: 처리 대상 일자 (KST).
        market: "KOSPI" | "KOSDAQ".
        skipped_reason: skip 시 사유 ("non_business_day" 등). 정상 실행은 None.
        universe_size: as_of 시점 활성 종목 수. skip 시 0.
        success_count: OHLCV + market_cap 성공한 종목 수.
        failure_count: 종목별 fetch 실패 수 (failures list 와 일관).
        failures: 종목별 실패 정보 — (code, reason) tuple list. 결정적 정렬.
        conflicts: pykrx vs FDR cross-check 의 ConflictReport 집계. FDR 미사용
            시 빈 list.
        market_cap_rows: 수집된 시가총액 row (Phase B 의 DB write 대기). 호출자
            가 추후 save_market_caps (별도 cycle) 로 처리.
        dry_run: True 면 본 batch 가 DB write skip — fetch + 검증만. 운영
            verification / staging 의 release 직전 sanity check 패턴.
        started_at: batch 시작 시각 (UTC).
        ended_at: batch 종료 시각 (UTC).
    """

    batch_id: UUID
    as_of: date
    market: str
    skipped_reason: str | None
    universe_size: int
    success_count: int
    failure_count: int
    failures: Sequence[tuple[str, str]]
    conflicts: Sequence[ConflictDetectionResult]
    market_cap_rows: Sequence[MarketCapRow]
    dry_run: bool
    started_at: datetime
    ended_at: datetime


class KrxDailyBatch:
    """KRX 일배치 orchestrator — single-shot run.

    Args:
        primary_adapter: pykrx 1차 출처.
        verify_adapter: FDR cross-check 옵션. None 이면 cross-check skip.
        conflict_detector: ConflictDetector. verify_adapter 가 있을 때 사용.
            None + verify_adapter 있으면 default detector 생성.
        calendar: KRX 영업일 캘린더 (TradingCalendar). 호출자 주입 — 운영은
            DEFAULT_CALENDAR, test 는 fixture.
        citation_repo: SourceCitation persistence.
        price_repo: PriceRecord persistence (citation 의존).
        market_cap_repo: MarketCapRecord persistence (citation 의존). None 이면
            (Fake-only 호환 / 명시 미주입) market_cap 영구화 skip — fetch 만 +
            summary 에 raw rows 포함 (기존 Phase A 동작).
        throttle_seconds: 종목 호출 사이 sleep (sec). 0 = 즉시 (test). 운영
            기본 1.5 (ADR-0003 D6).
        session: SQLAlchemy session — 있으면 종목별 SAVEPOINT 활성화
            (oracle T18 M1). DB write 중간 실패 시 SAVEPOINT ROLLBACK 으로
            partial commit 차단. Fake 모드 (in-memory repo) 는 None —
            savepoint 의미 X.
        alert_handler: BatchAlertHandler. None 이면 NullAlertHandler — alert
            no-op. 운영 시 LoggingAlertHandler 또는 SentryAlertHandler 주입
            (T43 / AC-O-02).
        batch_run_repo: M1 T48a 의 batch_runs 영속화 repository. None + session
            있으면 `SqlBatchRunRepository(session)` 를 default 로 구성 (배치
            종료 시 BatchSummary → batch_runs INSERT). session 도 None 이면
            (Fake 모드) batch_runs 영속화 skip. dry_run 도 skip.
    """

    def __init__(
        self,
        *,
        primary_adapter: PykrxAdapter,
        verify_adapter: FdrAdapter | None,
        conflict_detector: ConflictDetector | None,
        calendar: TradingCalendar,
        citation_repo: CitationRepository,
        price_repo: PriceRepository,
        market_cap_repo: MarketCapRepository | None = None,
        throttle_seconds: float = 1.5,
        session: Session | None = None,
        alert_handler: BatchAlertHandler | None = None,
        batch_run_repo: BatchRunRepository | None = None,
    ) -> None:
        self._primary = primary_adapter
        self._verify = verify_adapter
        self._detector = (
            conflict_detector
            if conflict_detector is not None
            else (ConflictDetector() if verify_adapter is not None else None)
        )
        self._calendar = calendar
        self._citation_repo = citation_repo
        self._price_repo = price_repo
        self._market_cap_repo = market_cap_repo
        self._throttle = throttle_seconds
        self._session = session
        # None → NullAlertHandler — batch 본체에서 None-check 회피 (alert call
        # site 가 무조건 메서드 호출 OK). Protocol contract.
        self._alert: BatchAlertHandler = (
            alert_handler if alert_handler is not None else NullAlertHandler()
        )
        # M1 T48a — batch_runs 영속화. 명시 주입 없으면 session 있을 때
        # SqlBatchRunRepository 를 default 로 구성. session 없으면 (Fake 모드)
        # None 유지하여 영속화 skip.
        self._batch_run_repo: BatchRunRepository | None = (
            batch_run_repo
            if batch_run_repo is not None
            else (SqlBatchRunRepository(session) if session is not None else None)
        )

    def run(
        self,
        *,
        as_of: date,
        market: str = "KOSPI",
        dry_run: bool = False,
        codes: Sequence[str] | None = None,
    ) -> BatchSummary:
        """1 영업일 = 1 시장의 batch 실행.

        Args:
            as_of: 처리 대상일 (KST). 휴장일이면 skip.
            market: "KOSPI" | "KOSDAQ".
            dry_run: True 면 fetch + 충돌 검증만 수행, citation/price DB write
                skip. 운영 verification (release 전 sanity check) / staging
                rehearsal 패턴. summary 의 success_count / conflicts /
                market_cap_rows 는 실 commit 과 동일 — DB 만 unchanged.
            codes: 처리 대상 종목코드 subset. None (기본) 이면 fetch_universe 가
                반환한 시장 전체. 지정 시 universe fetch 를 **생략**하고 요청 코드
                를 직접 처리 — 빠른 스모크/검증 적재용 (DART 배치의 stock_codes 와
                동일 ergonomics). KRX universe 엔드포인트가 개별 OHLCV 와 별개 API
                라 단독 장애 가능하므로 (universe-bypass), 타겟 스모크가 그에 묶이지
                않게 한다. 각 코드 유효성은 per-code fetch 가 검증 (존재하지 않는
                코드 → AdapterError → summary.failures 에 LOUD 노출). dedup +
                입력 순서 보존. universe citation 은 없음 (fetch 안 함; per-code
                citation 이 provenance 기록). **주의**: codes 만 주고 양 시장을
                돌면 같은 코드가 두 run 에서 처리돼 2 번째 run 에서 PK 중복 실패가
                날 수 있으니 --market 동반 권장.

        Returns:
            BatchSummary — 모든 결과 집계 (성공/실패/충돌 + dry_run flag).
            codes 지정 시 universe_size 는 요청 코드 (dedup) 수.

        Note:
            본 메서드는 raise 하지 않음 — universe fetch 실패도 summary 의
            failures 로 반환. 호출자 (운영 script) 가 summary 를 logging /
            Sentry 로 처리. alert_handler 가 주입되어 있으면 conflict /
            failure / complete 시점에 호출됨.
        """
        batch_id = uuid4()
        started_at = datetime.now(UTC)

        # M1 T48a — batch_runs row 를 배치 시작 시 INSERT (status='running').
        # source_citations.batch_id FK 가 종목별 SAVEPOINT RELEASE 시점에 검사
        # (SQLite) 되므로 citation 보다 먼저 존재해야 함. dry_run / Fake 모드는
        # no-op. 종료 시 _finalize_batch_run 이 success/skipped 로 UPDATE.
        self._start_batch_run(
            batch_id=batch_id, market=market, started_at=started_at,
            dry_run=dry_run,
        )

        # 1. 휴장일 skip — on_complete 만 호출 (failure / conflict 없음).
        if not self._calendar.is_business_day(as_of):
            summary = self._make_summary(
                batch_id=batch_id,
                as_of=as_of,
                market=market,
                skipped_reason="non_business_day",
                universe=(),
                successes=0,
                failures=(),
                conflicts=(),
                market_cap_rows=(),
                dry_run=dry_run,
                started_at=started_at,
            )
            self._finalize_batch_run(summary)
            self._alert.on_complete(summary)
            return summary

        # 2. Universe 결정.
        #
        #    codes 미지정 → fetch_universe (pykrx) 로 시장 전체 universe (운영
        #      일배치 경로). 실패 (빈 universe 포함 — adapter 가 빈 ticker list 를
        #      AdapterError 로 변환) 시 on_failure + 즉시 종료.
        #
        #    codes 지정 → universe fetch 를 **생략**하고 요청 코드를 직접 처리.
        #      KRX universe 엔드포인트 (get_market_ticker_list) 는 개별 OHLCV
        #      (get_market_ohlcv) 와 별개 KRX API 라 단독 장애가 가능하다 (라이브
        #      스모크에서 OHLCV 정상·universe 만 빈 반환 확인). 타겟 스모크가
        #      universe 엔드포인트에 묶이지 않도록 명시 코드는 직접 처리하고, 각
        #      코드 유효성은 per-code OHLCV/market_cap fetch 가 검증한다 (존재하지
        #      않는 코드 → AdapterError → failure isolation 으로 summary.failures
        #      에 LOUD 노출; 직전 cycle 의 silent exclude 보다 관측성↑). universe
        #      citation 은 없음 (fetch 안 함) — per-code citation 이 실제 fetch
        #      provenance 를 기록한다.
        #
        #      주의: codes 만 주고 markets 가 양 시장 (KOSPI+KOSDAQ) 이면 같은 코드
        #      가 두 시장 run 에서 모두 처리되어 2 번째 run 에서 PK 중복 실패가 날
        #      수 있다 → 스모크 시 --market 동반 권장 (run_krx_job / CLI 문서).
        if codes is not None:
            # dedup + 입력 순서 보존 (결정적 처리 순서).
            universe: Sequence[str] = list(dict.fromkeys(codes))
        else:
            try:
                universe_result = self._primary.fetch_universe(
                    as_of=as_of, market=market, batch_id=batch_id,
                )
            except AdapterError as exc:
                # universe fetch 실패 = batch 단위 실패. code 가 단일 종목이
                # 아니나 alert 채널 통일 위해 sentinel "universe" 사용.
                self._alert.on_failure("universe", str(exc))
                summary = self._make_summary(
                    batch_id=batch_id,
                    as_of=as_of,
                    market=market,
                    skipped_reason=f"universe_fetch_failed: {exc}",
                    universe=(),
                    successes=0,
                    failures=(),
                    conflicts=(),
                    market_cap_rows=(),
                    dry_run=dry_run,
                    started_at=started_at,
                )
                self._finalize_batch_run(summary)
                self._alert.on_complete(summary)
                return summary

            # universe citation 영구화 — dry_run 이면 skip.
            if not dry_run:
                self._save_citations(universe_result.citations)
            universe = universe_result.data

        # 3. 종목별 처리 — failure isolation.
        successes = 0
        failures: list[tuple[str, str]] = []
        conflicts: list[ConflictDetectionResult] = []
        market_cap_rows: list[MarketCapRow] = []

        for code in universe:
            # oracle T18 M1 — 종목당 SAVEPOINT. session 있으면 begin_nested()
            # 가 SAVEPOINT 발행 → exception 시 SAVEPOINT 까지 rollback (다른
            # 종목의 in-flight write 는 보존). Fake 모드 (session=None) 는
            # nullcontext — no-op. dry_run 도 nullcontext — DB write 없음.
            savepoint: AbstractContextManager[object] = (
                self._session.begin_nested()
                if self._session is not None and not dry_run
                else nullcontext()
            )
            try:
                with savepoint:
                    (
                        price_rows, mc_rows, mc_records, conflict_result,
                    ) = self._process_code(
                        code=code,
                        as_of=as_of,
                        batch_id=batch_id,
                        dry_run=dry_run,
                    )
                    # Price 영구화 — dry_run 이면 skip. citation 은 _process_code
                    # 안에서 dry_run 분기 (fetch-then-save 순서 유지).
                    if price_rows and not dry_run:
                        self._price_repo.save_prices(price_rows)
                    # MarketCap 영구화 (Phase B) — repo 주입 + not dry_run 일 때.
                    # citation 은 _process_code 가 이미 save (citation → market_cap
                    # 순서). repo None (Fake-only) 이면 fetch 만 + summary raw rows.
                    if (
                        mc_records
                        and not dry_run
                        and self._market_cap_repo is not None
                    ):
                        self._market_cap_repo.save_market_caps(mc_records)
                    if mc_rows:
                        market_cap_rows.extend(mc_rows)
                    if conflict_result is not None:
                        conflicts.append(conflict_result)
                        # conflict report 가 비어있지 않을 때만 alert — empty
                        # ConflictDetectionResult 는 정상 (false-positive 회피
                        # 의 의미). missing_in_* 도 신호 가치 → 포함.
                        if (
                            conflict_result.conflicts
                            or conflict_result.missing_in_primary
                            or conflict_result.missing_in_verify
                        ):
                            self._alert.on_conflict(conflict_result)
                    successes += 1
            except AdapterError as exc:
                # SAVEPOINT 가 with 종료 시 rollback 완료 — citation/price 등
                # 본 종목의 write 모두 무효화 (non-dry_run 한정).
                failures.append((code, str(exc)))
                self._alert.on_failure(code, str(exc))
            except Exception as exc:  # noqa: BLE001
                # 예상치 못한 예외 — failure isolation 유지하되 운영 logging
                # 단서 보존. 호출자가 stacktrace 정밀 조사.
                reason = f"unexpected: {type(exc).__name__}: {exc}"
                failures.append((code, reason))
                self._alert.on_failure(code, reason)

            # rate limit — 운영 시 throttle, test 시 0. dry_run 도 운영 시뮬레이션
            # 의미 보존 위해 동일 throttle.
            if self._throttle > 0:
                time.sleep(self._throttle)

        summary = self._make_summary(
            batch_id=batch_id,
            as_of=as_of,
            market=market,
            skipped_reason=None,
            universe=universe,
            successes=successes,
            failures=tuple(sorted(failures)),
            conflicts=tuple(conflicts),
            market_cap_rows=tuple(market_cap_rows),
            dry_run=dry_run,
            started_at=started_at,
        )
        self._finalize_batch_run(summary)
        self._alert.on_complete(summary)
        return summary

    # =========================================================================
    # 내부 helper — batch_runs 영속화 (M1 T48a)
    # =========================================================================

    def _start_batch_run(
        self,
        *,
        batch_id: UUID,
        market: str,
        started_at: datetime,
        dry_run: bool,
    ) -> None:
        """배치 시작 시 batch_runs row INSERT (status='running').

        조건: `batch_run_repo` 존재 AND not dry_run. dry_run / Fake 모드
        (session=None → repo None) 는 skip. citation 보다 먼저 row 가 존재해야
        종목별 SAVEPOINT RELEASE 시점의 FK 검사를 통과 (orm/batch_runs.py
        docstring).
        """
        if self._batch_run_repo is None or dry_run:
            return
        self._batch_run_repo.start(
            run_id=batch_id,
            market=market,
            source="KRX",
            started_at=started_at,
        )

    def _finalize_batch_run(self, summary: BatchSummary) -> None:
        """배치 종료 시 batch_runs row finalize (UPDATE).

        조건: `batch_run_repo` 존재 AND not dry_run.

        status 매핑: `skipped_reason` 이 있으면 (휴장일 / universe fetch 실패 →
        데이터 미생산) "skipped", 정상 실행은 "success". T48b 의
        collect_batch_versions 는 "success" 만 freeze 후보로 사용.
        """
        if self._batch_run_repo is None or summary.dry_run:
            return
        status = (
            BATCH_STATUS_SKIPPED
            if summary.skipped_reason is not None
            else BATCH_STATUS_SUCCESS
        )
        self._batch_run_repo.finalize(
            run_id=summary.batch_id,
            ended_at=summary.ended_at,
            success_count=summary.success_count,
            status=status,
        )

    # =========================================================================
    # 내부 helper — 종목별 처리
    # =========================================================================

    def _process_code(
        self,
        *,
        code: str,
        as_of: date,
        batch_id: UUID,
        dry_run: bool = False,
    ) -> tuple[
        Sequence[PriceRecord],
        Sequence[MarketCapRow],
        Sequence[MarketCapRecord],
        ConflictDetectionResult | None,
    ]:
        """단일 종목의 OHLCV + market_cap fetch + (옵션) FDR conflict 검출.

        oracle 리뷰 C1 — **fetch-then-save** 순서 강제:
            1. 모든 fetch (OHLCV primary, market_cap primary, optional FDR) 를
               먼저 완료. fetch 실패 시 어떤 citation/price 도 DB 에 들어가지
               않음 (orphan citation 차단).
            2. 모든 fetch 가 성공한 후에 citation save → price record 빌드.
            3. price save 는 호출자 (run) 가 수행 — citation save 와 같은
               session 내 sequential.

        Args:
            dry_run: True 면 citation save skip (DB write 없음). PriceRecord
                는 빌드되어 반환 (호출자가 dry_run 분기로 save_prices skip).
                conflict detection 은 동일 수행 — alert 의도 보존.

        Returns:
            (price_records, market_cap_rows, market_cap_records,
             conflict_result_or_None). `market_cap_rows` 는 summary 의 raw 집계
            (Phase A 호환), `market_cap_records` 는 citation_id FK 연결 완료된
            영구화 대상 (`market_cap_repo` 가 있으면 호출자가 save).

        Raises:
            AdapterError: fetch 실패. 호출자가 failure 분류. 본 단계 raise
                시 어떤 DB write 도 발생하지 않음 (in-memory 만).
        """
        # 1. fetch all — DB write 전 in-memory 수집.
        primary_ohlcv = self._primary.fetch_ohlcv_by_date_range(
            code, fromdate=as_of, todate=as_of, batch_id=batch_id,
        )
        primary_mc = self._primary.fetch_market_cap_by_date_range(
            code, fromdate=as_of, todate=as_of, batch_id=batch_id,
        )

        verify_ohlcv: FetchResult[tuple[OHLCVRow, ...]] | None = None
        if self._verify is not None and self._detector is not None:
            try:
                verify_ohlcv = self._verify.fetch_ohlcv_by_date_range(
                    code, fromdate=as_of, todate=as_of, batch_id=batch_id,
                )
            except AdapterError:
                # FDR 실패는 critical 아님 — primary 성공이면 종목 처리 성공.
                verify_ohlcv = None

        # 2. 모든 fetch 성공 → 이제 DB write 시작 (citation save 순서).
        # 호출자 (KrxDailyBatch.run) 가 종목별 SAVEPOINT 안에서 호출 — DB
        # write 중간 실패 시 본 종목의 모든 citation/price 가 SAVEPOINT
        # ROLLBACK 으로 무효화 (oracle T18 M1 적용 완료). dry_run 이면 citation
        # save skip — 검증 의도만 보존.
        if not dry_run:
            self._save_citations(primary_ohlcv.citations)
            self._save_citations(primary_mc.citations)
            if verify_ohlcv is not None:
                self._save_citations(verify_ohlcv.citations)

        # 3. PriceRecord 빌드 — oracle 리뷰 L7: citations[0] guard.
        if not primary_ohlcv.citations:
            raise AdapterError(
                f"primary OHLCV fetch returned no citation for code={code} — "
                f"FetchResult invariant violation"
            )
        price_records = self._build_price_records(
            ohlcv_rows=primary_ohlcv.data,
            citation_id=primary_ohlcv.citations[0].id,
        )

        # market_cap record 빌드 — citation_id FK 연결 (prices 와 동일 패턴).
        # primary_mc.citations[0] guard — FetchResult invariant.
        if not primary_mc.citations:
            raise AdapterError(
                f"primary market_cap fetch returned no citation for "
                f"code={code} — FetchResult invariant violation"
            )
        market_cap_records = self._build_market_cap_records(
            market_cap_rows=primary_mc.data,
            citation_id=primary_mc.citations[0].id,
        )

        # 4. Conflict detection — fetch 가 모두 성공한 경우만.
        conflict_result: ConflictDetectionResult | None = None
        if verify_ohlcv is not None and self._detector is not None:
            conflict_result = self._detector.compare_ohlcv(
                primary=primary_ohlcv,
                verify=verify_ohlcv,
                primary_source=self._primary.SOURCE_KIND,
                verify_source=self._verify.SOURCE_KIND,
            )

        return (
            price_records, primary_mc.data, market_cap_records, conflict_result,
        )

    @staticmethod
    def _lineage_id_for_code(code: str) -> UUID:
        """code → code_lineage_id (deterministic placeholder).

        stocks_master 가 lineage 단위 entity 이므로 본 cycle 에서는 code 별
        deterministic placeholder UUID. T13 Phase B 에서 실제 lineage 매핑.
        prices / market_cap 이 동일 lineage 해소를 공유 (work order Phase B 요구).
        """
        from uuid import NAMESPACE_OID, uuid5

        return uuid5(NAMESPACE_OID, f"lineage|{code}")

    def _build_price_records(
        self,
        *,
        ohlcv_rows: Sequence[OHLCVRow],
        citation_id: UUID,
    ) -> tuple[PriceRecord, ...]:
        """OHLCVRow (canonical) → PriceRecord (DB schema) 변환.

        ADR-0001 D1 — `close_adjusted` 는 본 cycle 미산정 (T20 Corporate Action
        보정 엔진 미적용). close_raw 와 동일 값 채움 — T20 일배치 합류 시
        업데이트 (M2 backlog: snapshot 이력화).
        """
        return tuple(
            PriceRecord(
                id=uuid4(),
                code=row.code,
                code_lineage_id=self._lineage_id_for_code(row.code),
                effective_date=row.trade_date,
                open_raw=row.open,
                high_raw=row.high,
                low_raw=row.low,
                close_raw=row.close,
                volume=row.volume,
                # 거래대금 — Momus M0 review V3 fix. OHLCVRow.value 가 pykrx
                # 의 `거래대금` 컬럼 (이미 fetch 중). factor pack 의
                # `volume-turnover:avg-20d` 입력 schema 준비.
                trading_value=row.value,
                # T20 미적용 — raw 와 동일. 후속 일배치가 update.
                close_adjusted=row.close,
                citation_id=citation_id,
                created_at=datetime.now(UTC),
            )
            for row in ohlcv_rows
        )

    def _build_market_cap_records(
        self,
        *,
        market_cap_rows: Sequence[MarketCapRow],
        citation_id: UUID,
    ) -> tuple[MarketCapRecord, ...]:
        """MarketCapRow (canonical) → MarketCapRecord (DB schema) 변환.

        prices 와 동일 lineage 해소 (`_lineage_id_for_code`) 재사용. 자사주
        (shares_treasury) 는 pykrx None 그대로 보존 — 절대 0 으로 가정 안 함
        (silent 오류 회피, DART 보강 별도 cycle, T48c).
        """
        return tuple(
            MarketCapRecord(
                id=uuid4(),
                code=row.code,
                code_lineage_id=self._lineage_id_for_code(row.code),
                effective_date=row.trade_date,
                market_cap=row.market_cap,
                shares_outstanding=row.shares_outstanding,
                # pykrx None 그대로 — DART 보강 전까지 N/A (0 가정 금지).
                shares_treasury=row.shares_treasury,
                citation_id=citation_id,
                created_at=datetime.now(UTC),
            )
            for row in market_cap_rows
        )

    def _save_citations(self, citations: Sequence) -> None:
        """Citation bulk save — adapter 가 매 fetch 마다 새 uuid4 생성.

        oracle 리뷰 C2 — 기존의 `fetch_by_id` dedup loop 는 no-op 였음
        (`PykrxAdapter._make_citation` 이 매번 새 uuid4 → 충돌 확률 0).
        N+1 SELECT 비용만 들고 dedup 효과 없음 → 제거.

        진짜 idempotency 가 필요한 경우 (retry / 부분 재실행) 는 natural key
        (source, identifier, effective_date) 의 DB UNIQUE constraint 도입 +
        ON CONFLICT DO NOTHING — 별도 cycle (M0 출하 전 검토).
        """
        for c in citations:
            self._citation_repo.save(c)

    def _make_summary(
        self,
        *,
        batch_id: UUID,
        as_of: date,
        market: str,
        skipped_reason: str | None,
        universe: Sequence[str],
        successes: int,
        failures: Sequence[tuple[str, str]],
        conflicts: Sequence[ConflictDetectionResult],
        market_cap_rows: Sequence[MarketCapRow],
        dry_run: bool,
        started_at: datetime,
    ) -> BatchSummary:
        return BatchSummary(
            batch_id=batch_id,
            as_of=as_of,
            market=market,
            skipped_reason=skipped_reason,
            universe_size=len(universe),
            success_count=successes,
            failure_count=len(failures),
            failures=tuple(failures),
            conflicts=tuple(conflicts),
            market_cap_rows=tuple(market_cap_rows),
            dry_run=dry_run,
            started_at=started_at,
            ended_at=datetime.now(UTC),
        )
