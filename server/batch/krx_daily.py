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
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Sequence
from uuid import UUID, uuid4

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

    def run(
        self,
        *,
        as_of: date,
        market: str = "KOSPI",
    ) -> BatchSummary:
        """1 영업일 = 1 시장의 batch 실행.

        Args:
            as_of: 처리 대상일 (KST). 휴장일이면 skip.
            market: "KOSPI" | "KOSDAQ".

        Returns:
            BatchSummary — 모든 결과 집계 (성공/실패/충돌).

        Note:
            본 메서드는 raise 하지 않음 — universe fetch 실패도 summary 의
            failures 로 반환. 호출자 (운영 script) 가 summary 를 logging /
            Sentry 로 처리.
        """
        batch_id = uuid4()
        started_at = datetime.now(timezone.utc)

        # 1. 휴장일 skip.
        if not self._calendar.is_business_day(as_of):
            return self._make_summary(
                batch_id=batch_id,
                as_of=as_of,
                market=market,
                skipped_reason="non_business_day",
                universe=(),
                successes=0,
                failures=(),
                conflicts=(),
                market_cap_rows=(),
                started_at=started_at,
            )

        # 2. Universe fetch — primary (pykrx).
        try:
            universe_result = self._primary.fetch_universe(
                as_of=as_of, market=market, batch_id=batch_id,
            )
        except AdapterError as exc:
            return self._make_summary(
                batch_id=batch_id,
                as_of=as_of,
                market=market,
                skipped_reason=f"universe_fetch_failed: {exc}",
                universe=(),
                successes=0,
                failures=(),
                conflicts=(),
                market_cap_rows=(),
                started_at=started_at,
            )

        # universe citation 도 영구화.
        self._save_citations(universe_result.citations)
        universe = universe_result.data

        # 3. 종목별 처리 — failure isolation.
        successes = 0
        failures: list[tuple[str, str]] = []
        conflicts: list[ConflictDetectionResult] = []
        market_cap_rows: list[MarketCapRow] = []

        for code in universe:
            try:
                price_rows, mc_rows, conflict_result = self._process_code(
                    code=code, as_of=as_of, batch_id=batch_id,
                )
                # Price 영구화 — citation 먼저 save 한 후 price (FK 만족).
                if price_rows:
                    self._price_repo.save_prices(price_rows)
                if mc_rows:
                    market_cap_rows.extend(mc_rows)
                if conflict_result is not None:
                    conflicts.append(conflict_result)
                successes += 1
            except AdapterError as exc:
                failures.append((code, str(exc)))
            except Exception as exc:  # noqa: BLE001
                # 예상치 못한 예외 — failure isolation 유지하되 운영 logging
                # 단서 보존. 호출자가 stacktrace 정밀 조사.
                failures.append((code, f"unexpected: {type(exc).__name__}: {exc}"))

            # rate limit — 운영 시 throttle, test 시 0.
            if self._throttle > 0:
                time.sleep(self._throttle)

        return self._make_summary(
            batch_id=batch_id,
            as_of=as_of,
            market=market,
            skipped_reason=None,
            universe=universe,
            successes=successes,
            failures=tuple(sorted(failures)),
            conflicts=tuple(conflicts),
            market_cap_rows=tuple(market_cap_rows),
            started_at=started_at,
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
        # 본 단계 이후 raise 시 partial commit 가능성 — orchestrator 의 명시적
        # transaction savepoint 는 별도 cycle (oracle 리뷰 C1 P1 backlog).
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
        from uuid import uuid5, NAMESPACE_OID

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
                # T20 미적용 — raw 와 동일. 후속 일배치가 update.
                close_adjusted=row.close,
                citation_id=citation_id,
                created_at=datetime.now(timezone.utc),
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
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
        )
