"""Survivorship backfill 배치 — 폐지 종목 과거 OHLCV 소급 수집 (ADR-0027 release blocker).

## 왜 필요한가 (ADR-0027 §7.3 survivorship)

백테스트는 과거 시점 t 의 universe 를 `stocks_master.list_active(as_of=t)` 로 복원한다
(PIT, ADR-0009 D7). 설계는 완비됐으나 **데이터가 비어 있다** — KRX 일배치
(`krx_daily.py`)는 pykrx `get_market_ticker_list(날짜)` = **현재 활성 종목만** 수집하므로,
서비스 개시 이전에 이미 폐지된 종목의 과거 OHLCV 가 DB 에 없다. 그 결과 과거 universe
에서 폐지 종목이 누락되면 survivorship bias 로 백테스트 결과가 환상이 된다.

본 배치가 그 공백을 메운다 — `list_delisted_before(cutoff)` 로 폐지 종목 전체를 조회하고,
각 종목의 상장~폐지 기간 OHLCV 를 pykrx 로 소급 fetch 해 `prices` 에 저장한다. pykrx 의
`get_market_ohlcv(from, to, code)` 는 **폐지 종목도 상장 당시 기간 데이터를 반환**하므로
backfill 이 성립한다. 채워진 가격은 백테스트 엔진의 `missing_price_count` 를 낮춰
`survivorship_complete=True` 에 다가가게 한다(ADR-0027 D3).

## 설계 (krx_daily.py 패턴 일관 + backfill 특화)

- **per-종목 SAVEPOINT + failure isolation** — 한 종목 실패가 batch 전체를 멈추지 않음.
- **citation → price 순서** — adapter citation 을 먼저 save 한 뒤 price 의 FK 연결.
- **중복 회피(idempotent)** — 이미 DB 에 있는 날짜는 fetch 후 필터해 재저장하지 않는다.
  재실행/부분실패 후 재시도해도 SQL UNIQUE 충돌(IntegrityError)을 일으키지 않는다.
- **code_history 윈도우** — 종목코드 변경 이력(ADR-0009 D6)을 따라 각 code 의 유효
  기간만 그 code 로 fetch. 단일 code(대부분)면 상장~폐지 한 윈도우.
- **빈 윈도우 관용** — pykrx 가 특정 code/기간에 데이터를 안 주면(폐지 직전 단기 임시
  코드 등) 그 윈도우만 skip(empty_windows 집계). 종목 전체 실패로 보지 않는다.
- **citation source = "PYKRX"** (adapter SOURCE_KIND) — 정규 KRX 일배치 가격과 **동형**.
  따라서 backfill row 는 일반 가격과 구분 없이 백테스트에 편입되고, 재현 cutoff(T48c)도
  동일하게 적용된다(backfill 이후 frozen 백테스트는 backfill row 를 정상 포함).

## 운영 위치 (release blocker)

본 배치 **코드**는 완결이나, 실제 폐지 종목 과거 데이터 수집은 **운영 cycle**(외부 pykrx
호출·throttle·기간)이다. ADR-0027 release blocker. backfill 완비 전에는 백테스트 결과에
D3 survivorship 디스클로저가 항상 노출된다.

관련 ADR: ADR-0027(백테스트·survivorship), ADR-0009 D6/D7(code_history·survivorship 보존),
ADR-0003 D6(throttle), ADR-0002 D3(citation persistence).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.adapters.base import AdapterError, OHLCVRow
from app.adapters.pykrx_adapter import PykrxAdapter
from app.db.orm.batch_runs import BATCH_STATUS_PARTIAL, BATCH_STATUS_SUCCESS
from app.repositories.batch_run_repository import (
    BatchRunRepository,
    SqlBatchRunRepository,
)
from app.repositories.citation_repository import CitationRepository
from app.repositories.pit_protocols import (
    PriceRecord,
    PriceRepository,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import StocksMasterRepository
from app.services.lineage import lineage_id_for_code
from batch.alerts import BatchAlertHandler, NullAlertHandler

logger = logging.getLogger(__name__)

__all__ = ["BackfillSummary", "SurvivorshipBackfillBatch"]


@dataclass(frozen=True, slots=True)
class _FetchWindow:
    """단일 code 의 OHLCV fetch 구간 — code_history 한 entry 에서 도출.

    Attributes:
        code: 이 구간에 유효했던 KRX 종목코드.
        start: 구간 시작일(inclusive).
        end: 구간 종료일(inclusive).
    """

    code: str
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    """survivorship backfill 1 회 실행 결과.

    Attributes:
        batch_id: 본 실행의 UUID(모든 citation 의 batch_id 와 일치).
        cutoff: 폐지 종목 universe 기준일(delisting_date <= cutoff).
        market: 처리 시장("KOSPI" | "KOSDAQ" | None=전 시장).
        delisted_universe_size: cutoff/market 필터 후 처리 대상 폐지 종목 수.
        success_count: 예외 없이 처리된 종목 수(0행 저장 포함).
        failure_count: 종목별 처리 실패 수(failures 와 일관).
        failures: (code, reason) tuple list — 결정적 정렬.
        rows_saved: 신규 저장된 가격 row 총수(중복 제외 후).
        rows_skipped_existing: 이미 DB 에 있어 재저장하지 않은 row 총수.
        empty_windows: pykrx 가 데이터를 주지 않은(빈) fetch 윈도우 수.
        dry_run: True 면 DB write 없이 fetch + 집계만(검증/리허설).
        started_at / ended_at: 실행 시각(UTC).
    """

    batch_id: UUID
    cutoff: date
    market: str | None
    delisted_universe_size: int
    success_count: int
    failure_count: int
    failures: Sequence[tuple[str, str]]
    rows_saved: int
    rows_skipped_existing: int
    empty_windows: int
    dry_run: bool
    started_at: datetime
    ended_at: datetime


class SurvivorshipBackfillBatch:
    """폐지 종목 과거 OHLCV 소급 수집 orchestrator — single-shot run.

    Args:
        primary_adapter: pykrx 1차 출처(폐지 종목 과거 OHLCV 도 반환).
        stocks_master_repo: 폐지 universe 조회(`list_delisted_before`).
        citation_repo: SourceCitation persistence(price FK 선행).
        price_repo: PriceRecord persistence + 중복 회피용 조회(`fetch_prices`).
        throttle_seconds: 종목 호출 사이 sleep(sec). 0=즉시(test). 운영 1.5(ADR-0003 D6).
        session: SQLAlchemy session — 있으면 종목별 SAVEPOINT(begin_nested) 활성화로
            DB write 중간 실패 시 본 종목 write 만 rollback. Fake 모드는 None.
        alert_handler: BatchAlertHandler. None 이면 NullAlertHandler(no-op).
        batch_run_repo: batch_runs 영속화. None + session 있으면
            `SqlBatchRunRepository(session)` default. session 없으면(Fake) skip.
    """

    def __init__(
        self,
        *,
        primary_adapter: PykrxAdapter,
        stocks_master_repo: StocksMasterRepository,
        citation_repo: CitationRepository,
        price_repo: PriceRepository,
        throttle_seconds: float = 1.5,
        session: Session | None = None,
        alert_handler: BatchAlertHandler | None = None,
        batch_run_repo: BatchRunRepository | None = None,
    ) -> None:
        self._primary = primary_adapter
        self._stocks_master_repo = stocks_master_repo
        self._citation_repo = citation_repo
        self._price_repo = price_repo
        self._throttle = throttle_seconds
        self._session = session
        self._alert: BatchAlertHandler = (
            alert_handler if alert_handler is not None else NullAlertHandler()
        )
        # batch_runs 영속화 — krx_daily 와 동일 default 구성.
        self._batch_run_repo: BatchRunRepository | None = (
            batch_run_repo
            if batch_run_repo is not None
            else (SqlBatchRunRepository(session) if session is not None else None)
        )

    def run(
        self,
        *,
        cutoff: date,
        market: str | None = None,
        dry_run: bool = False,
    ) -> BackfillSummary:
        """폐지 종목 universe 의 과거 OHLCV 를 소급 수집.

        Args:
            cutoff: 폐지 universe 기준일 — delisting_date <= cutoff 인 종목만.
                보통 today(전 폐지 종목).
            market: "KOSPI" | "KOSDAQ" 면 그 시장 폐지 종목만. None 이면 전 시장.
            dry_run: True 면 DB write 없이 fetch + 집계만(검증/리허설).

        Returns:
            BackfillSummary — 처리 종목·저장/중복/빈윈도우 집계.

        Note:
            본 메서드는 raise 하지 않음 — 종목별 실패는 summary.failures 로 반환.
            호출자(운영 script)가 logging/Sentry 로 처리.
        """
        batch_id = uuid4()
        started_at = datetime.now(UTC)
        self._start_batch_run(
            batch_id=batch_id, market=market, started_at=started_at, dry_run=dry_run,
        )

        # 폐지 universe — list_active 의 보집합. market 필터(선택).
        universe = self._stocks_master_repo.list_delisted_before(cutoff=cutoff)
        if market is not None:
            universe = tuple(r for r in universe if r.market == market)

        successes = 0
        failures: list[tuple[str, str]] = []
        rows_saved = 0
        rows_skipped = 0
        empty_windows = 0

        for record in universe:
            label = self._record_label(record)
            # 종목당 SAVEPOINT — session 있고 not dry_run 일 때만(krx_daily 패턴).
            savepoint: AbstractContextManager[object] = (
                self._session.begin_nested()
                if self._session is not None and not dry_run
                else nullcontext()
            )
            try:
                with savepoint:
                    saved, skipped, empties = self._backfill_record(
                        record=record, batch_id=batch_id, dry_run=dry_run,
                    )
                    rows_saved += saved
                    rows_skipped += skipped
                    empty_windows += empties
                    successes += 1
            except AdapterError as exc:
                # SAVEPOINT rollback 으로 본 종목 write 무효화(non-dry_run).
                failures.append((label, str(exc)))
                self._alert.on_failure(label, str(exc))
            except Exception as exc:  # noqa: BLE001
                reason = f"unexpected: {type(exc).__name__}: {exc}"
                failures.append((label, reason))
                self._alert.on_failure(label, reason)

            if self._throttle > 0:
                time.sleep(self._throttle)

        summary = BackfillSummary(
            batch_id=batch_id,
            cutoff=cutoff,
            market=market,
            delisted_universe_size=len(universe),
            success_count=successes,
            failure_count=len(failures),
            failures=tuple(sorted(failures)),
            rows_saved=rows_saved,
            rows_skipped_existing=rows_skipped,
            empty_windows=empty_windows,
            dry_run=dry_run,
            started_at=started_at,
            ended_at=datetime.now(UTC),
        )
        self._finalize_batch_run(summary)
        self._alert.on_complete(summary)
        return summary

    # =========================================================================
    # 내부 helper — 종목별 backfill
    # =========================================================================

    def _backfill_record(
        self,
        *,
        record: StockMasterRecord,
        batch_id: UUID,
        dry_run: bool,
    ) -> tuple[int, int, int]:
        """단일 폐지 종목의 code_history 윈도우별 OHLCV fetch + 중복 회피 저장.

        Returns:
            (rows_saved, rows_skipped_existing, empty_windows).

        Raises:
            AdapterError: 윈도우 도출 불가(code_history 부재 등) 시. 빈 윈도우(데이터
                없음)는 raise 하지 않고 empty_windows 로 집계(부분 backfill 허용).
        """
        windows = self._fetch_windows(record)
        if not windows:
            raise AdapterError(
                f"no fetch window derivable for lineage={record.id} "
                f"(code_history empty / delisting_date missing)"
            )

        saved = 0
        skipped = 0
        empties = 0
        for window in windows:
            try:
                result = self._primary.fetch_ohlcv_by_date_range(
                    window.code,
                    fromdate=window.start,
                    todate=window.end,
                    batch_id=batch_id,
                )
            except AdapterError:
                # 빈 DataFrame 등 — 이 윈도우만 skip(폐지 직전 단기 임시코드 등에서
                # 데이터 부재 가능). 종목 전체 실패로 보지 않는다(부분 backfill).
                empties += 1
                continue

            # 중복 회피 — 이미 DB 에 있는 날짜는 재저장 안 함(SQL UNIQUE 충돌 방지 +
            # 재실행 idempotency). cutoff=None 으로 전 batch 의 기존 row 조회.
            existing_dates = {
                r.effective_date
                for r in self._price_repo.fetch_prices(
                    window.code, as_of=window.end, start=window.start,
                )
            }
            new_rows = tuple(
                row for row in result.data if row.trade_date not in existing_dates
            )
            skipped += len(result.data) - len(new_rows)
            if not new_rows:
                # 전부 기존 row — citation 도 save 하지 않는다(orphan citation 차단).
                continue

            if not dry_run:
                # citation → price 순서(FK). FetchResult invariant: citations 비어
                # 있으면 안 됨(adapter 가 보장하나 방어).
                if not result.citations:
                    raise AdapterError(
                        f"OHLCV fetch returned no citation for code={window.code} "
                        f"— FetchResult invariant violation"
                    )
                self._save_citations(result.citations)
                price_records = self._build_price_records(
                    ohlcv_rows=new_rows,
                    citation_id=result.citations[0].id,
                )
                self._price_repo.save_prices(price_records)
            saved += len(new_rows)

        return saved, skipped, empties

    @staticmethod
    def _fetch_windows(record: StockMasterRecord) -> tuple[_FetchWindow, ...]:
        """code_history → fetch 윈도우 목록. 각 code 의 유효기간만 그 code 로 fetch.

        한 entry 의 유효구간 = `[valid_from, valid_to or delisting_date]`. 종료일은
        delisting_date 로 clamp(폐지 이후는 fetch 안 함). 단일 entry(대부분)면 상장~
        폐지 한 윈도우. delisting_date 가 None(활성)이면 backfill 대상 아님 → 빈 목록.
        """
        if record.delisting_date is None:
            return ()
        delist = record.delisting_date
        windows: list[_FetchWindow] = []
        for entry in record.code_history:
            end = entry.valid_to if entry.valid_to is not None else delist
            # 폐지일 이후로 새지 않도록 clamp. 거꾸로(start>end) 인 비정상 entry skip.
            end = min(end, delist)
            if entry.valid_from > end:
                continue
            windows.append(
                _FetchWindow(code=entry.code, start=entry.valid_from, end=end)
            )
        return tuple(windows)

    @staticmethod
    def _record_label(record: StockMasterRecord) -> str:
        """실패/alert 표기용 종목 식별 — current_code(폐지면 None) 우선, 없으면 code_history."""
        if record.current_code:
            return record.current_code
        if record.code_history:
            return record.code_history[-1].code
        return str(record.id)

    @staticmethod
    def _lineage_id_for_code(code: str) -> UUID:
        """code → code_lineage_id (deterministic placeholder).

        krx_daily.KrxDailyBatch._lineage_id_for_code 와 **동일 규약** — backfill row 와
        정규 일배치 row 가 같은 lineage 해소를 공유해야 prices 테이블이 정합(T13 Phase B
        실제 lineage 매핑 전까지 placeholder).

        공식은 `app.services.lineage.lineage_id_for_code` 공유 helper 로 위임(단일 출처).
        기존 호출부/테스트는 그대로 보존.
        """
        return lineage_id_for_code(code)

    def _build_price_records(
        self,
        *,
        ohlcv_rows: Sequence[OHLCVRow],
        citation_id: UUID,
    ) -> tuple[PriceRecord, ...]:
        """OHLCVRow → PriceRecord. krx_daily 와 동일 변환(close_adjusted=raw, read-time 보정)."""
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
                trading_value=row.value,
                # corporate action 보정은 read-time(M1) — 저장은 raw(krx_daily 일관).
                close_adjusted=row.close,
                citation_id=citation_id,
                created_at=datetime.now(UTC),
            )
            for row in ohlcv_rows
        )

    def _save_citations(self, citations: Sequence) -> None:
        """Citation bulk save — adapter 가 fetch 마다 새 uuid4 생성(krx_daily 일관)."""
        for c in citations:
            self._citation_repo.save(c)

    # =========================================================================
    # 내부 helper — batch_runs 영속화
    # =========================================================================

    def _start_batch_run(
        self,
        *,
        batch_id: UUID,
        market: str | None,
        started_at: datetime,
        dry_run: bool,
    ) -> None:
        """배치 시작 시 batch_runs row INSERT(status='running'). dry_run/Fake 모드 skip.

        source="KRX" — 정규 일배치와 동일 논리 출처(citation source 는 adapter 의
        "PYKRX"). citation FK 가 SAVEPOINT RELEASE 시점에 검사되므로 먼저 존재해야 함.
        """
        if self._batch_run_repo is None or dry_run:
            return
        self._batch_run_repo.start(
            run_id=batch_id, market=market, source="KRX", started_at=started_at,
        )

    def _finalize_batch_run(self, summary: BackfillSummary) -> None:
        """배치 종료 시 batch_runs finalize(UPDATE). dry_run/Fake 모드 skip.

        backfill 은 휴장일/universe-fetch-실패 같은 skip 사유가 없으나 종목별
        실패는 가능 → failure_count>0 이면 status='partial', 아니면 'success'
        (universe 가 비어도 정상 실행). success_count = 처리 종목 수. partial 도
        성공분의 실 데이터를 commit 했으므로 freeze 후보·freshness 자격은 success
        와 동일 (collect_batch_versions / latest_successful 가 둘 다 수용) — 라벨만
        운영 가시성 위해 분리.
        """
        if self._batch_run_repo is None or summary.dry_run:
            return
        status = (
            BATCH_STATUS_PARTIAL if summary.failure_count > 0
            else BATCH_STATUS_SUCCESS
        )
        if summary.failure_count > 0:
            logger.warning(
                "survivorship backfill 배치 부분 실패 — batch_id=%s "
                "failure_count=%d (status=partial 로 저장)",
                summary.batch_id, summary.failure_count,
            )
        self._batch_run_repo.finalize(
            run_id=summary.batch_id,
            ended_at=summary.ended_at,
            success_count=summary.success_count,
            status=status,
        )
