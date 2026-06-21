"""배당 일배치 — M7 #2 Slice 2 의 현금배당 수집 orchestrator.

금융위 공공데이터(GetStocDiviInfoService_V2)에서 종목별 현금배당을 수집해
`DividendRepository.save_dividends` 로 영구화한다. Slice 1 의 인프라
(`CrnoMapping` 종목코드→crno, `FscDividendAdapter`)를 배선해, 종목코드만으로
배당 적재 경로를 완성한다(crno 입력은 매핑이 해소).

책임 (DartDailyBatch 패턴 미러):
1. **crno 매핑** — KRX 종목코드 → 법인등록번호(crno) lookup(CrnoMapping). 미매핑
   종목은 skip + alert(failure 와 다른 카테고리 — crno 매핑 갱신 신호).
2. **fetch-then-dedup-then-save** — adapter 가 (record, citation) 쌍을 생산.
   배치는 기존 배당과 **자연키 dedup**(아래) 후 신규만 citation→dividend 순서로
   저장(FK 만족).
3. **재실행 멱등성 (핵심)** — `corporate_actions` 는 자연키 UNIQUE 제약이 없고
   record.id 가 uuid4(비결정적)라, 순진하게 insert 하면 재실행 시 같은 배당이
   중복 적재되어 total-return 재투자가 **이중 계산**된다(§2.10). 따라서 insert 전
   기존 배당을 조회해 자연키 `(effective_date, 배당종류)` 로 skip 한다(first-wins).
   배당종류는 `details["type"]`(adapter 가 stckDvdnRcdNm 으로 채움). 자연키 근거:
   같은 종목·같은 배당락일·같은 배당종류 = 같은 배당 사건(adapter citation
   identifier `crno|bas_dt|stckDvdnRcdNm` 와 1:1, effective_date=prev_biz(bas_dt)).
   **배당 정정(per_share 변경)은 M7 §1 known-limit 으로 유보** — first-wins skip
   이 정정분 이중계산을 막는다(정정 chain 탐지는 후속 마일스톤).
4. **회사별 failure isolation + SAVEPOINT** — 한 종목 실패가 배치 전체를 멈추지
   않음. session 주입 시 `begin_nested()` 로 회사별 원자성(partial commit 차단).
5. **데이터 갭 가시화 (§2.1)** — adapter 의 `[DATA_GAP]` warning(캘린더 범위 밖
   배당락일·totalCount drift)은 조용히 삼키지 않고 WARNING 로그 + summary
   data_gap_count 로 노출(종목 failure 와 구분 — 부분 데이터로도 success).
6. **batch_runs 영속화** — source="FSC"(snapshot_versions 의 dividend_batch_id
   키가 source FSC 의 최신 배치를 freeze). DartDailyBatch 와 동일 패턴.

scope (Slice 2):
- 현금배당 수집 + dedup-insert. **배당 정정 supersede chain 은 미구현**(§1
  known-limit — first-wins skip 으로 이중계산만 방지). corporate_action 일반
  (split/bonus)은 본 배치 범위 외(FSC 배당 API 는 현금배당만).

관련 ADR / 문서:
- ADR-0035 D3 (배당 출처 = 금융위), D6 (이중 PIT announced/effective)
- ADR-0009 D2 (cash_dividend action_type), D5 (정정 chain — 본 cycle 미구현)
- ADR-0002 D3 (citation persistence)
- M7_PLAN §1 known-limit (배당 정정 reproduce 유보), [[m7-crno-mapping-infra]]
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.adapters.base import AdapterError
from app.adapters.fsc_dividend_adapter import (
    DATA_GAP_PREFIX,
    FscDividendAdapter,
    TradingCalendarProtocol,
)
from app.db.orm.batch_runs import BATCH_STATUS_PARTIAL, BATCH_STATUS_SUCCESS
from app.repositories.batch_run_repository import (
    BatchRunRepository,
    SqlBatchRunRepository,
)
from app.repositories.citation_repository import CitationRepository
from app.repositories.pit_protocols import (
    CorporateActionRecord,
    DividendRepository,
)
from app.services.crno_mapping import CrnoMapping
from batch.alerts import BatchAlertHandler, NullAlertHandler

logger = logging.getLogger(__name__)

__all__ = ["DividendBatchSummary", "DividendDailyBatch"]


@dataclass(frozen=True, slots=True)
class DividendBatchSummary:
    """배당 일배치 1 회 실행 결과.

    Attributes:
        batch_id: 본 실행의 UUID. 모든 citation 의 batch_id 와 일치 +
            batch_runs.id(source=FSC) — snapshot dividend_batch_id freeze 입력.
        begin_bas_dt / end_bas_dt: 배당기준일 조회 기간(YYYYMMDD).
        target_count: 처리 시도한 종목 수.
        success_count: fetch + (dedup) save 성공한 종목 수.
        failure_count: 종목별 fetch/save 실패 수.
        skipped_count: crno 매핑 누락으로 skip 한 종목 수.
        dividends_saved: 신규 적재된 배당 record 수(dedup skip 제외, CFS/OFS 없음).
        dedup_skipped: 기존과 자연키 중복으로 skip 한 배당 수(멱등 재실행 신호).
        data_gap_count: adapter `[DATA_GAP]` warning 수(재수집 판단 대상).
        failures: (stock_code, reason) 결정적 정렬.
        skipped_codes: crno 매핑 누락 종목코드(운영 alerting).
        dry_run: True 면 DB write skip — fetch + 매핑 + dedup 검증만.
        started_at / ended_at: UTC tz-aware.
    """

    batch_id: UUID
    begin_bas_dt: str
    end_bas_dt: str
    target_count: int
    success_count: int
    failure_count: int
    skipped_count: int
    dividends_saved: int
    dedup_skipped: int
    data_gap_count: int
    failures: Sequence[tuple[str, str]]
    skipped_codes: Sequence[str]
    dry_run: bool
    started_at: datetime
    ended_at: datetime


class DividendDailyBatch:
    """배당 일배치 orchestrator — single-shot run per (기간).

    Args:
        adapter: FscDividendAdapter(생성자에 api_key 또는 env var).
        crno_mapping: 종목코드 → crno 매핑(CrnoBootstrap.load 결과, Slice 1).
        citation_repo: SourceCitation persistence.
        dividend_repo: DividendRepository(cash_dividend persistence + dedup 조회).
        trading_calendar: 배당락일(effective_date) 산출용 거래일 캘린더 — adapter
            에 주입(TradingCalendar 가 구조 만족). 외부 KRX 데이터라 직접 구현 X.
        throttle_seconds: 종목 호출 사이 sleep(공공데이터포털 rate limit). test=0,
            운영 default 1.0.
        session: SQLAlchemy session. 주입 시 회사별 SAVEPOINT(begin_nested) 활성.
            Fake 모드는 None.
        alert_handler: BatchAlertHandler. None 이면 NullAlertHandler.
        batch_run_repo: batch_runs 영속화. None + session 있으면
            `SqlBatchRunRepository(session)` default(source="FSC").
    """

    def __init__(
        self,
        *,
        adapter: FscDividendAdapter,
        crno_mapping: CrnoMapping,
        citation_repo: CitationRepository,
        dividend_repo: DividendRepository,
        trading_calendar: TradingCalendarProtocol,
        throttle_seconds: float = 1.0,
        session: Session | None = None,
        alert_handler: BatchAlertHandler | None = None,
        batch_run_repo: BatchRunRepository | None = None,
    ) -> None:
        self._adapter = adapter
        self._mapping = crno_mapping
        self._citation_repo = citation_repo
        self._dividend_repo = dividend_repo
        self._calendar = trading_calendar
        self._throttle = throttle_seconds
        self._session = session
        self._alert: BatchAlertHandler = (
            alert_handler if alert_handler is not None else NullAlertHandler()
        )
        self._batch_run_repo: BatchRunRepository | None = (
            batch_run_repo
            if batch_run_repo is not None
            else (SqlBatchRunRepository(session) if session is not None else None)
        )

    def run(
        self,
        *,
        stock_codes: Sequence[str],
        begin_bas_dt: str,
        end_bas_dt: str,
        dry_run: bool = False,
    ) -> DividendBatchSummary:
        """종목 list × 배당기준일 기간 배치 실행.

        Args:
            stock_codes: 처리 대상 KRX 종목코드 list.
            begin_bas_dt / end_bas_dt: 배당기준일 조회 기간(YYYYMMDD). adapter 가
                crno 전체 history 수신 후 이 기간으로 클라이언트 필터(V2 서버필터
                부재).
            dry_run: True 면 fetch + crno 매핑 + dedup 만, citation/dividend write
                skip.

        Returns:
            DividendBatchSummary — 종목별 성공/실패/skip + dedup/data_gap 집계.

        Note:
            본 메서드는 raise 하지 않음 — 전체 실패도 summary 로 반환(DartDailyBatch
            동형). adapter api_key 미설정 등 영구 실패 시 전 종목 failure 분류.
        """
        batch_id = uuid4()
        started_at = datetime.now(UTC)
        self._start_batch_run(
            batch_id=batch_id, started_at=started_at, dry_run=dry_run,
        )

        successes = 0
        failures: list[tuple[str, str]] = []
        skipped: list[str] = []
        dividends_saved = 0
        dedup_skipped = 0
        data_gap_count = 0

        for stock_code in stock_codes:
            crno = self._mapping.to_crno(stock_code)
            if crno is None:
                # crno 매핑 누락 — skip + alert(failure 와 별도 카테고리). 운영
                # 의미: CrnoBootstrap 갱신 필요(비상장/외국법인/jurir 부재 등).
                skipped.append(stock_code)
                self._alert.on_failure(
                    stock_code, "skipped: crno mapping missing",
                )
                continue

            # 회사별 SAVEPOINT — session 있고 not dry_run 이면 begin_nested().
            savepoint: AbstractContextManager[object] = (
                self._session.begin_nested()
                if self._session is not None and not dry_run
                else nullcontext()
            )
            try:
                with savepoint:
                    saved, skipped_dup, gaps = self._process_company(
                        stock_code=stock_code,
                        crno=crno,
                        begin_bas_dt=begin_bas_dt,
                        end_bas_dt=end_bas_dt,
                        batch_id=batch_id,
                        dry_run=dry_run,
                    )
                    dividends_saved += saved
                    dedup_skipped += skipped_dup
                    data_gap_count += gaps
                    successes += 1
            except AdapterError as exc:
                failures.append((stock_code, str(exc)))
                self._alert.on_failure(stock_code, str(exc))
            except Exception as exc:  # noqa: BLE001
                reason = f"unexpected: {type(exc).__name__}: {exc}"
                failures.append((stock_code, reason))
                self._alert.on_failure(stock_code, reason)

            if self._throttle > 0:
                time.sleep(self._throttle)

        summary = DividendBatchSummary(
            batch_id=batch_id,
            begin_bas_dt=begin_bas_dt,
            end_bas_dt=end_bas_dt,
            target_count=len(stock_codes),
            success_count=successes,
            failure_count=len(failures),
            skipped_count=len(skipped),
            dividends_saved=dividends_saved,
            dedup_skipped=dedup_skipped,
            data_gap_count=data_gap_count,
            failures=tuple(sorted(failures)),
            skipped_codes=tuple(sorted(skipped)),
            dry_run=dry_run,
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
        self, *, batch_id: UUID, started_at: datetime, dry_run: bool,
    ) -> None:
        """배치 시작 시 batch_runs row INSERT(status='running', source='FSC').

        citation 보다 먼저 존재해야 회사별 SAVEPOINT RELEASE 시점의 FK 검사 통과.
        market=None(배당은 회사 단위). dry_run / Fake 모드 no-op.
        """
        if self._batch_run_repo is None or dry_run:
            return
        self._batch_run_repo.start(
            run_id=batch_id, market=None, source="FSC", started_at=started_at,
        )

    def _finalize_batch_run(self, summary: DividendBatchSummary) -> None:
        """배치 종료 시 batch_runs finalize. failure_count>0 → PARTIAL, else SUCCESS.

        partial 도 성공분 실데이터를 commit 했으므로 freeze/freshness 자격은 success
        와 동일(collect_batch_versions 가 둘 다 수용) — 라벨만 운영 가시성 분리
        (DartDailyBatch 동형).
        """
        if self._batch_run_repo is None or summary.dry_run:
            return
        status = (
            BATCH_STATUS_PARTIAL if summary.failure_count > 0
            else BATCH_STATUS_SUCCESS
        )
        if summary.failure_count > 0:
            logger.warning(
                "배당 배치 부분 실패 — batch_id=%s failure_count=%d "
                "(status=partial 로 저장)",
                summary.batch_id, summary.failure_count,
            )
        self._batch_run_repo.finalize(
            run_id=summary.batch_id,
            ended_at=summary.ended_at,
            success_count=summary.success_count,
            status=status,
        )

    # =========================================================================
    # 내부 — 종목별 처리 (fetch → dedup → save)
    # =========================================================================

    def _process_company(
        self,
        *,
        stock_code: str,
        crno: str,
        begin_bas_dt: str,
        end_bas_dt: str,
        batch_id: UUID,
        dry_run: bool,
    ) -> tuple[int, int, int]:
        """단일 종목의 배당 fetch + dedup + save.

        Returns:
            (dividends_saved, dedup_skipped, data_gap_count).

        Raises:
            AdapterError: fetch 실패(api_key/HTTP/resultCode) — 회사 단위 isolation.
        """
        result = self._adapter.fetch_cash_dividends(
            code=stock_code,
            crno=crno,
            begin_bas_dt=begin_bas_dt,
            end_bas_dt=end_bas_dt,
            batch_id=batch_id,
            trading_calendar=self._calendar,
        )

        # §2.1 — [DATA_GAP] warning 은 재수집/격리 판단 대상이라 WARNING 로그 + 카운트.
        # alert.on_failure 는 쓰지 않는다(oracle M1): 본 종목은 부분 데이터로도
        # success 로 집계되므로, on_failure(=종목 실패 의미)로 보내면 운영자에게
        # "ERROR alert + success 동시" 모순 신호가 된다. data_gap_count 가 summary
        # 로 on_complete 에 집계되어 가시성은 유지된다. benign warning(우선주·0배당·
        # 배당기준일 부재)은 debug 로그만(노이즈 회피).
        data_gaps = 0
        for w in result.warnings:
            if w.startswith(DATA_GAP_PREFIX):
                data_gaps += 1
                logger.warning("배당 데이터 갭 (code=%s): %s", stock_code, w)
            else:
                logger.debug("배당 benign skip (code=%s): %s", stock_code, w)

        records = result.data
        citations = result.citations
        if not records:
            return 0, 0, data_gaps

        # dedup — 기존 배당의 자연키 set 으로 신규만 선별(재실행 멱등성 핵심).
        # fetch_all_dividends_bulk 는 raw 전체 vintage(PIT 무관, **action_type 무관**)
        # 반환 → 이미 적재된 모든 배당과 비교(과거 run 포함). _dividend_key 로
        # (effective_date, 배당종류). **cash_dividend 만 비교**(oracle M2): bulk 가
        # split/bonus 등 타 action_type 도 반환하는데, 그들의 details 엔 "type" 키가
        # 없어 _dividend_key 가 (effective_date, "") 로 정규화된다 → 같은 배당락일의
        # stckDvdnRcdNm="" 현금배당과 cross-action_type 충돌해 신규 배당을 silent
        # drop 할 수 있다. 본 배치는 cash_dividend 만 insert 하므로 기존도
        # cash_dividend 로 좁혀 비교한다.
        existing = self._dividend_repo.fetch_all_dividends_bulk(
            (stock_code,),
        ).get(stock_code, ())
        seen_keys = {
            _dividend_key(r)
            for r in existing
            if r.action_type == "cash_dividend"
        }

        new_records: list[CorporateActionRecord] = []
        new_citations = []
        dedup_skipped = 0
        # data[i] ↔ citations[i] 는 adapter 가 같은 순서로 생산(병렬 tuple).
        for record, citation in zip(records, citations, strict=True):
            key = _dividend_key(record)
            if key in seen_keys:
                # 이미 적재된 배당(과거 run 또는 같은 fetch 내 중복) — skip(first-wins).
                dedup_skipped += 1
                continue
            seen_keys.add(key)  # 같은 fetch 내 중복도 방어(adapter stckDvdnRcdNm 빈값 등).
            new_records.append(record)
            new_citations.append(citation)

        if not new_records:
            return 0, dedup_skipped, data_gaps

        if dry_run:
            return len(new_records), dedup_skipped, data_gaps

        # citation → dividend 순서(FK 만족). citation 은 신규 배당분만 저장하므로
        # id 충돌(SourceCitationError) 없음(dedup 이 기존분을 사전 제외).
        for citation in new_citations:
            self._citation_repo.save(citation)
        self._dividend_repo.save_dividends(new_records)
        return len(new_records), dedup_skipped, data_gaps


def _dividend_key(record: CorporateActionRecord) -> tuple[str, str]:
    """배당 자연키 — (effective_date ISO, 배당종류).

    재실행 멱등 dedup 의 비교 키. effective_date(배당락일)는
    prev_business_day(배당기준일)로 결정적이고, 배당종류(`details["type"]` =
    adapter 가 채운 stckDvdnRcdNm)는 같은 배당락일의 결산/중간 배당을 구별한다. 둘이
    같으면 같은 배당 사건(adapter citation identifier `crno|bas_dt|stckDvdnRcdNm` 와
    1:1). per_share 는 키에 **미포함** — 정정(금액 변경) 시에도 first-wins skip
    으로 이중계산을 막기 위함(M7 §1 known-limit, 정정 chain 은 후속).

    details 가 dict 가 아니거나 type 부재면 "" 로 정규화(저장·fetch 양측 일관).
    """
    div_type = ""
    details = record.details
    if isinstance(details, dict):
        div_type = str(details.get("type", ""))
    return (record.effective_date.isoformat(), div_type)
