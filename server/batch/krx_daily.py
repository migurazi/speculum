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
2. **scheduler 미통합** — M0 cycle scope. CLI / 운영 script 가 manual 호출.
3. **MarketCap 미저장** — Phase B (별도 cycle). 본 cycle 은 fetch 만 + citation
   save. summary 에 raw rows 포함하여 호출자가 처리.
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
from app.repositories.citation_repository import CitationRepository
from app.repositories.pit_protocols import PriceRecord, PriceRepository
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
        throttle_seconds: 종목 호출 사이 sleep (sec). 0 = 즉시 (test). 운영
            기본 1.5 (ADR-0003 D6).
        session: SQLAlchemy session — 있으면 종목별 SAVEPOINT 활성화
            (oracle T18 M1). DB write 중간 실패 시 SAVEPOINT ROLLBACK 으로
            partial commit 차단. Fake 모드 (in-memory repo) 는 None —
            savepoint 의미 X.
        alert_handler: BatchAlertHandler. None 이면 NullAlertHandler — alert
            no-op. 운영 시 LoggingAlertHandler 또는 SentryAlertHandler 주입
            (T43 / AC-O-02).
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
        throttle_seconds: float = 1.5,
        session: Session | None = None,
        alert_handler: BatchAlertHandler | None = None,
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
        self._throttle = throttle_seconds
        self._session = session
        # None → NullAlertHandler — batch 본체에서 None-check 회피 (alert call
        # site 가 무조건 메서드 호출 OK). Protocol contract.
        self._alert: BatchAlertHandler = (
            alert_handler if alert_handler is not None else NullAlertHandler()
        )

    def run(
        self,
        *,
        as_of: date,
        market: str = "KOSPI",
        dry_run: bool = False,
    ) -> BatchSummary:
        """1 영업일 = 1 시장의 batch 실행.

        Args:
            as_of: 처리 대상일 (KST). 휴장일이면 skip.
            market: "KOSPI" | "KOSDAQ".
            dry_run: True 면 fetch + 충돌 검증만 수행, citation/price DB write
                skip. 운영 verification (release 전 sanity check) / staging
                rehearsal 패턴. summary 의 success_count / conflicts /
                market_cap_rows 는 실 commit 과 동일 — DB 만 unchanged.

        Returns:
            BatchSummary — 모든 결과 집계 (성공/실패/충돌 + dry_run flag).

        Note:
            본 메서드는 raise 하지 않음 — universe fetch 실패도 summary 의
            failures 로 반환. 호출자 (운영 script) 가 summary 를 logging /
            Sentry 로 처리. alert_handler 가 주입되어 있으면 conflict /
            failure / complete 시점에 호출됨.
        """
        batch_id = uuid4()
        started_at = datetime.now(UTC)

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
            self._alert.on_complete(summary)
            return summary

        # 2. Universe fetch — primary (pykrx). 실패 시 on_failure + 즉시 종료.
        try:
            universe_result = self._primary.fetch_universe(
                as_of=as_of, market=market, batch_id=batch_id,
            )
        except AdapterError as exc:
            # universe fetch 실패 = batch 단위 실패. code 가 단일 종목이 아니나
            # alert 채널 통일 위해 sentinel "universe" 사용 — 호출자가 분기.
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
                    price_rows, mc_rows, conflict_result = self._process_code(
                        code=code,
                        as_of=as_of,
                        batch_id=batch_id,
                        dry_run=dry_run,
                    )
                    # Price 영구화 — dry_run 이면 skip. citation 은 _process_code
                    # 안에서 dry_run 분기 (fetch-then-save 순서 유지).
                    if price_rows and not dry_run:
                        self._price_repo.save_prices(price_rows)
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
        self._alert.on_complete(summary)
        return summary

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
            (price_records, market_cap_rows, conflict_result_or_None).

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

        # 4. Conflict detection — fetch 가 모두 성공한 경우만.
        conflict_result: ConflictDetectionResult | None = None
        if verify_ohlcv is not None and self._detector is not None:
            conflict_result = self._detector.compare_ohlcv(
                primary=primary_ohlcv,
                verify=verify_ohlcv,
                primary_source=self._primary.SOURCE_KIND,
                verify_source=self._verify.SOURCE_KIND,
            )

        return price_records, primary_mc.data, conflict_result

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
        # lineage_id 는 stocks_master 가 lineage 단위 entity 이므로 본 cycle
        # 에서는 placeholder UUID (code 별 deterministic). T13 Phase B 에서
        # 실제 lineage 매핑.
        from uuid import NAMESPACE_OID, uuid5

        return tuple(
            PriceRecord(
                id=uuid4(),
                code=row.code,
                code_lineage_id=uuid5(NAMESPACE_OID, f"lineage|{row.code}"),
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
