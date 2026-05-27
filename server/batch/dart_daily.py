"""DART 일배치 — ADR-0003 + M0_PLAN T19 의 재무제표 orchestrator.

매일 03:00 KST 트리거 (현재는 manual entry — APScheduler 통합은 별도 cycle).
DART OpenAPI 의 재무제표 fetch + canonical FinancialStatementRow 변환 + DB
write.

책임:
1. **corp_code 매핑** — KRX stock code → DART corp_code lookup (CorpCodeMapping).
2. **CFS / OFS 분리 fetch** — ADR-0005 의 두 정의 모두 영구화. service layer
   가 default 선택.
3. **FinancialStatementRow → FinancialRecord 변환** — adapter canonical →
   DB schema. lineage_id placeholder (T18 와 동일 한계 — 별도 cycle backfill).
4. **citation → financial 순서** — FK 만족. fetch-then-save (T18 와 동일).
5. **회사별 failure isolation** — 한 회사 fetch 실패가 batch 전체 멈춤 X.
6. **rate limit** — ADR-0003 D6 의 DART 분당 throttling. M0 default 6.0s
   간격 (분당 ~10 회 — 일 10,000 호출 한도 안전 마진).

본 cycle scope (MVP):
- 재무제표 fetch + DB write 만. corporate action 은 별도 cycle (T16 phase B).
- 정정공시 supersede chain 자동 처리 X — 호출자가 superseded_by 명시 설정
  (별도 cycle).
- 신규/미공시 공시 detection X — 매번 fresh fetch (idempotent 의무 = 같은
  rcept_no 는 같은 데이터).

설계 결정 (T18 패턴 재사용):
1. **sync orchestration** — codebase 일관.
2. **scheduler 미통합** — manual entry. CLI/script 가 호출.
3. **per-company savepoint X** — T18 의 M1 backlog 와 동일 한계 (M0 출하 전).

관련 ADR:
- ADR-0003 D2 (canonical), D6 (rate limit)
- ADR-0005 (CFS/OFS 두 정의 모두 영구화)
- ADR-0002 D3 (citation persistence)
- ADR-0009 D5 (정정공시 chain — 본 cycle 미구현)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Sequence
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

from app.adapters.base import (
    AdapterError,
    FetchResult,
    FinancialStatementRow,
    IfrsType,
)
from app.adapters.dart_adapter import DartAdapter
from app.repositories.citation_repository import CitationRepository
from app.repositories.pit_protocols import (
    FinancialRecord,
    FinancialRepository,
)
from app.services.corp_code_mapping import CorpCodeMapping

__all__ = ["DartBatchSummary", "DartDailyBatch"]


@dataclass(frozen=True, slots=True)
class DartBatchSummary:
    """DART 일배치 1 회 실행 결과.

    Attributes:
        batch_id: 본 실행의 UUID. 모든 citation 의 batch_id 와 일치.
        fiscal_year: 처리 대상 사업연도.
        fiscal_quarter: 처리 대상 분기 (1~4).
        target_count: 처리 시도한 회사 수.
        success_count: CFS + OFS 모두 성공한 회사 수 (또는 partial — 옵션 분기).
        failure_count: 회사별 fetch 실패 수.
        skipped_count: corp_code 매핑 누락으로 skip 한 회사 수.
        failures: (stock_code, reason) 결정적 정렬.
        skipped_codes: 매핑 누락된 stock_code list (운영 alerting).
        total_rows_saved: financial 레코드 누적 저장 수 (CFS+OFS 합).
        started_at / ended_at: UTC tz-aware.
    """

    batch_id: UUID
    fiscal_year: int
    fiscal_quarter: int
    target_count: int
    success_count: int
    failure_count: int
    skipped_count: int
    failures: Sequence[tuple[str, str]]
    skipped_codes: Sequence[str]
    total_rows_saved: int
    started_at: datetime
    ended_at: datetime


class DartDailyBatch:
    """DART 일배치 orchestrator — single-shot run per (year, quarter).

    Args:
        adapter: DartAdapter (생성자에 api_key 또는 env var).
        corp_mapping: KRX↔DART code 매핑.
        citation_repo: SourceCitation persistence.
        financial_repo: FinancialRecord persistence.
        ifrs_types: 영구화할 IFRS 종류 list. default = (CFS, OFS) 둘 다.
            ADR-0005 의 dual variant 정책.
        throttle_seconds: 회사 호출 사이 sleep. ADR-0003 D6 의 DART 분당
            throttling. test=0, 운영 default 6.0 (일 10,000 호출 안전 마진).
    """

    def __init__(
        self,
        *,
        adapter: DartAdapter,
        corp_mapping: CorpCodeMapping,
        citation_repo: CitationRepository,
        financial_repo: FinancialRepository,
        ifrs_types: Sequence[IfrsType] = (IfrsType.CFS, IfrsType.OFS),
        throttle_seconds: float = 6.0,
    ) -> None:
        self._adapter = adapter
        self._mapping = corp_mapping
        self._citation_repo = citation_repo
        self._financial_repo = financial_repo
        self._ifrs_types = tuple(ifrs_types)
        self._throttle = throttle_seconds

    def run(
        self,
        *,
        stock_codes: Sequence[str],
        fiscal_year: int,
        fiscal_quarter: int,
    ) -> DartBatchSummary:
        """1 회사 list × (year, quarter) batch 실행.

        Args:
            stock_codes: 처리 대상 KRX 종목코드 list. 운영 시 pykrx universe
                또는 사용자 watchlist.
            fiscal_year: 사업연도 (2000~2100).
            fiscal_quarter: 1~4 (4=연간/사업보고서).

        Returns:
            DartBatchSummary — 회사별 성공/실패/skip 집계.

        Note:
            본 메서드는 raise 하지 않음 — universe 전체 실패도 summary 로 반환.
            adapter 가 api_key 미설정 등 영구 실패 시 모든 회사가 failure 로 분류.
        """
        batch_id = uuid4()
        started_at = datetime.now(timezone.utc)

        successes = 0
        failures: list[tuple[str, str]] = []
        skipped: list[str] = []
        total_rows = 0

        for stock_code in stock_codes:
            corp_code = self._mapping.to_corp_code(stock_code)
            if corp_code is None:
                # corp_code 매핑 누락 — skip + alert.
                skipped.append(stock_code)
                continue

            try:
                rows_saved = self._process_company(
                    stock_code=stock_code,
                    corp_code=corp_code,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    batch_id=batch_id,
                )
                total_rows += rows_saved
                successes += 1
            except AdapterError as exc:
                failures.append((stock_code, str(exc)))
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    (stock_code, f"unexpected: {type(exc).__name__}: {exc}"),
                )

            if self._throttle > 0:
                time.sleep(self._throttle)

        return DartBatchSummary(
            batch_id=batch_id,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            target_count=len(stock_codes),
            success_count=successes,
            failure_count=len(failures),
            skipped_count=len(skipped),
            failures=tuple(sorted(failures)),
            skipped_codes=tuple(sorted(skipped)),
            total_rows_saved=total_rows,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
        )

    # =========================================================================
    # 내부 — 회사별 처리 (T18 fetch-then-save 패턴 재사용)
    # =========================================================================

    def _process_company(
        self,
        *,
        stock_code: str,
        corp_code: str,
        fiscal_year: int,
        fiscal_quarter: int,
        batch_id: UUID,
    ) -> int:
        """단일 회사의 CFS + OFS fetch + DB write.

        fetch-then-save (T18 oracle 리뷰 C1 패턴) — 모든 fetch 가 성공한 후에만
        DB write 시작. 중간 실패 시 어떤 row 도 영구화 안 됨.

        Returns:
            저장된 FinancialRecord 수.

        Raises:
            AdapterError: 어느 한 IFRS type 의 fetch 도 실패. 운영 의미상 두
                IFRS 다 실패해야 회사 실패 — 아래 정책으로 CFS 성공 + OFS 실패도
                fail 처리 (정확한 데이터만 영구화).
        """
        # 1. 모든 IFRS type fetch (in-memory only).
        fetched: list[FetchResult[tuple[FinancialStatementRow, ...]]] = []
        for ifrs_type in self._ifrs_types:
            result = self._adapter.fetch_financial_statement(
                code=stock_code,
                corp_code=corp_code,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                ifrs_type=ifrs_type,
                batch_id=batch_id,
            )
            fetched.append(result)

        # 2. 모든 fetch 성공 → DB write 시작.
        # oracle 리뷰 M1 — `citations[0]` guard. DartAdapter 가 항상 1 citation
        # 반환하나 Protocol 계약상 빈 list 금지 runtime 검증 없음. 새 adapter
        # 변형이 0 또는 2 개 반환 시 IndexError → 운영 alert 의 가독성 보강.
        rows_saved = 0
        for result in fetched:
            if not result.citations:
                raise AdapterError(
                    f"DART fetch returned no citation for code={stock_code} "
                    f"corp={corp_code} {fiscal_year}Q{fiscal_quarter} — "
                    f"FetchResult invariant violation"
                )
            # citation 먼저 (FK 만족).
            for c in result.citations:
                self._citation_repo.save(c)
            # adapter row → DB record 변환 후 bulk save.
            db_records = _convert_to_financial_records(
                rows=result.data,
                citation_id=result.citations[0].id,
            )
            if db_records:
                self._financial_repo.save_financials(db_records)
                rows_saved += len(db_records)

        return rows_saved


# =============================================================================
# Conversion helpers
# =============================================================================

def _convert_to_financial_records(
    *,
    rows: Sequence[FinancialStatementRow],
    citation_id: UUID,
) -> tuple[FinancialRecord, ...]:
    """adapter canonical FinancialStatementRow → DB FinancialRecord 변환.

    - fiscal_period: `f"{fiscal_year}Q{fiscal_quarter}"` (예: "2023Q4").
    - lineage_id: T18 동일 placeholder (uuid5 deterministic). T13 Phase B 의
      stocks_master 합류 시 backfill migration 필요.
    - ifrs_type 매핑: IfrsType enum → DB schema string ("consolidated" /
      "separate" — IfrsType.value).
    - superseded_by: 본 cycle 미설정 (정정공시 처리는 별도 cycle).
    """
    db_records: list[FinancialRecord] = []
    created_at = datetime.now(timezone.utc)
    for row in rows:
        fiscal_period = f"{row.fiscal_year}Q{row.fiscal_quarter}"
        lineage_id = uuid5(NAMESPACE_OID, f"lineage|{row.code}")
        db_records.append(
            FinancialRecord(
                id=uuid4(),
                code=row.code,
                code_lineage_id=lineage_id,
                effective_date=row.effective_date,
                fiscal_period=fiscal_period,
                account=row.account,
                value=row.value,
                unit=row.unit,
                ifrs_type=row.ifrs_type.value,  # enum.value = "consolidated"/"separate".
                citation_id=citation_id,
                superseded_by=None,
                created_at=created_at,
            )
        )
    return tuple(db_records)
