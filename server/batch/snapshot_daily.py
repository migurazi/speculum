"""stock_snapshots precompute 일배치 — ADR-0002 D5 의 derived snapshot producer.

universe(active 보통주) × factor(pack) 를 FactorEvaluator 로 평가하여 그 결과를
`stock_snapshots` 테이블에 적재하는 **§2.10-안전한 producer** 다. Screen / Backtest
의 라이브 평가 경로(routes/screen.py screen_active_codes)와 **동일한 평가기·동일한
provider·동일한 caching repo bulk prime** 을 미러하여, 미리 계산된 snapshot 이
라이브 평가와 byte-동일한 산출이 되도록 한다 (read-bypass cycle 의 정확성 전제).

EcosDailyBatch 의 orchestrator 구조를 그대로 미러:
1. **batch_runs 라이프사이클** — _start_batch_run(running) → _finalize_batch_run
   (success). source="PRECOMPUTE", market=None (종목 단위 아님 — 전 universe 1배치).
2. **per-item SAVEPOINT 격리** — 종목별 begin_nested() (session 있을 때). 한 종목
   평가/저장 실패가 다른 종목 적재를 막지 않음 (SAVEPOINT 자동 rollback + 다음
   종목 정상 진행). Fake 모드(session=None)는 nullcontext — no-op.
3. **run() 은 raise 안 함 계약** — 종목 전체 실패도 summary 로 반환 (ecos/kosis/dart
   동일). 좀비 'running' batch_run 방지 위해 finalize 는 try/finally.
4. **alert_handler** — on_failure(종목 단위) / on_complete(summary).

핵심 설계 결정:
- **PRECOMPUTE citation** (ADR-0002 amendment ⓓ) — snapshot 은 1차 자료가 아닌
  derived 결과다. 각 snapshot row 의 citation_id 는 본 배치 run 을 가리키는 단일
  PRECOMPUTE citation 으로, "이 값은 (observed_date, batch_id) 에 precompute 된
  파생값" 임을 provenance 로 남긴다. per-input 1차 출처(가격·재무 citation)는
  inputs/pack 으로 복원 가능 (StockSnapshotRecord docstring). citation 을 snapshot
  보다 **먼저** save 하여 FK 를 만족한다 (citation → snapshot 순서, ecos 패턴 동일).
- **data_versions freeze** — collect_run_data_versions(observed_date, session, pack)
  로 산정 당시 정책/batch fingerprint 를 freeze 하여 각 record.data_versions 에
  저장. 후속 read-bypass cycle 이 reproduce 의 frozen data_versions 와 비교해
  "serve vs recompute" 를 결정적으로 판정하는 축 (StockSnapshotRecord docstring,
  screen_runs.data_versions 와 대칭). session=None(Fake) 이면 정책-only 키 — 정상.
- **cutoff 없음(live precompute)** — 본 배치는 "지금 시점(observed_date)의 최신
  데이터로 미리 계산" 하는 라이브 precompute 다. reproduce 의 frozen batch cutoff
  주입과 무관하므로 caching repo prime 에 batch_cutoff 를 넘기지 않는다(=무필터,
  screen 라이브 경로와 동일). 정정공시 발생 시 다음 배치가 재계산하여 같은 key 를
  overwrite 한다.
- **UPSERT** — save_snapshots 는 (stock_code, as_of_date, factor_uuid) 키로 UPSERT
  (StockSnapshotRecord docstring — derived recomputable cache). 따라서 같은
  observed_date 재실행 시 IntegrityError 가 아니라 갱신 → idempotent. ecos 의
  INSERT-only(IntegrityError skip)와 의도적으로 상이하므로 별도 skip 분기 불요.
- **per-code N+1 완화** — screen_active_codes 와 동형으로 루프 **이전** 1회 bulk
  prime(price/market_cap/financial/treasury/corporate_action) + macro request-scoped
  memoize. 종목별 개별 fetch N round-trip → 청크 쿼리 몇 회로 축약. prime window
  밖/미prime 요청은 inner 위임이라 정확성 동일.

관련 ADR:
- ADR-0002 D5 (stock_snapshots), amendment ⓓ (PRECOMPUTE SourceKind)
- ADR-0023 D7 (universe 자산군 사전 필터 — security_types)
- §2.10 (재현성 — 라이브 평가와 byte-동일 산출)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.orm.batch_runs import BATCH_STATUS_PARTIAL, BATCH_STATUS_SUCCESS
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.batch_run_repository import (
    BatchRunRepository,
    SqlBatchRunRepository,
)
from app.repositories.caching_repositories import (
    CachingCorporateActionRepository,
    CachingDividendRepository,
    CachingFinancialRepository,
    CachingMacroIndicatorRepository,
    CachingMarketCapRepository,
    CachingPriceRepository,
    CachingTreasurySharesRepository,
)
from app.repositories.citation_repository import CitationRepository
from app.repositories.pit_protocols import (
    CorporateActionRepository,
    DividendRepository,
    FinancialRepository,
    MacroIndicatorRepository,
    MarketCapRepository,
    PriceRepository,
    StockSnapshotRecord,
    StockSnapshotRepository,
    TreasurySharesRepository,
)
from app.repositories.stocks_master_repository import StocksMasterRepository
from app.services.db_field_provider import DbFieldProvider
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK, LoadedPack
from app.services.snapshot_versions import collect_run_data_versions
from app.services.total_return_adjuster import TotalReturnAdjuster
from batch.alerts import BatchAlertHandler, NullAlertHandler

__all__ = ["SnapshotBatchSummary", "SnapshotDailyBatch", "run_snapshot_job"]

logger = logging.getLogger(__name__)


# =============================================================================
# Summary dataclass
# =============================================================================

@dataclass(frozen=True, slots=True)
class SnapshotBatchSummary:
    """stock_snapshots precompute 일배치 1 회 실행 결과.

    Attributes:
        batch_id: 본 실행의 UUID. PRECOMPUTE citation 의 batch_id 와 일치.
        observed_date: precompute 기준일 (= snapshot 의 as_of_date).
        universe_count: 사전 필터(active + security_type) 통과 universe 종목 수.
        factor_count: 평가 대상 factor 수 (pack body["factors"]).
        success_count: 평가/저장 성공한 종목 수.
        failure_count: 종목 단위 평가/저장 실패 수.
        skipped_count: skip 수 (현재 미사용 — UPSERT 라 중복 skip 없음. 향후 확장
            여지를 위해 필드 보존, ecos summary 와 대칭).
        snapshots_saved: 실제 저장된 snapshot record 합계 (= 성공 종목 × factor 수).
        failures: (stock_code, reason) 정렬 tuple.
        started_at / ended_at: UTC tz-aware.
    """

    batch_id: UUID
    observed_date: date
    universe_count: int
    factor_count: int
    success_count: int
    failure_count: int
    skipped_count: int
    snapshots_saved: int
    failures: Sequence[tuple[str, str]]
    started_at: datetime
    ended_at: datetime


# =============================================================================
# SnapshotDailyBatch
# =============================================================================

class SnapshotDailyBatch:
    """stock_snapshots precompute 일배치 orchestrator — single-shot run per observed_date.

    universe × factor 를 FactorEvaluator 로 평가해 stock_snapshots 에 UPSERT 한다.
    Screen 라이브 경로(screen_active_codes)와 동일 평가기/provider/caching prime 을
    미러 — precompute 결과가 라이브 평가와 byte-동일 (§2.10 read-bypass 정확성).

    Args:
        session: SQLAlchemy session — 주입 시 종목별 SAVEPOINT 활성화 + snapshot
            flush + batch_run/citation 영속화. Fake 모드(None)는 savepoint/flush
            skip, save_snapshots 는 in-memory Fake 가 수집 (단위 테스트).
        stocks_repo: universe(list_active) 조회.
        price_repo / financial_repo / market_cap_repo / treasury_repo /
            corporate_action_repo / macro_repo: factor 평가 입력 repository. 루프
            이전 caching 래퍼로 1회 bulk prime(macro 는 memoize) → per-code N+1 완화.
        dividend_repo: cash_dividend repository (total_return_trailing_1y factor
            평가용, M7). None 이면 해당 factor 정식 N/A (DbFieldProvider both-or-
            neither — dividend_repo None 이면 total_return_adjuster 도 None).
        snapshot_repo: stock_snapshots UPSERT 대상.
        citation_repo: PRECOMPUTE citation 영속화 (FK 선행).
        pack: 평가 대상 factor pack. default=DEFAULT_PACK (빌트인 — content_hash 불변).
        evaluator: FactorEvaluator. None 이면 기본 인스턴스 생성.
        batch_run_repo: batch_runs 영속화. None + session 있으면
            SqlBatchRunRepository(session) default (ecos 패턴).
        alert_handler: BatchAlertHandler. None 이면 NullAlertHandler.
        security_types: universe 자산군 사전 필터 (ADR-0023 D7). default=("common",)
            — 보통주만 (screen 기본 동작 불변).
    """

    def __init__(
        self,
        *,
        session: Session | None,
        stocks_repo: StocksMasterRepository,
        price_repo: PriceRepository,
        financial_repo: FinancialRepository,
        market_cap_repo: MarketCapRepository,
        treasury_repo: TreasurySharesRepository,
        corporate_action_repo: CorporateActionRepository,
        macro_repo: MacroIndicatorRepository,
        dividend_repo: DividendRepository | None = None,
        snapshot_repo: StockSnapshotRepository,
        citation_repo: CitationRepository,
        pack: LoadedPack = DEFAULT_PACK,
        evaluator: FactorEvaluator | None = None,
        batch_run_repo: BatchRunRepository | None = None,
        alert_handler: BatchAlertHandler | None = None,
        security_types: tuple[str, ...] = ("common",),
    ) -> None:
        self._session = session
        self._stocks_repo = stocks_repo
        self._price_repo = price_repo
        self._financial_repo = financial_repo
        self._market_cap_repo = market_cap_repo
        self._treasury_repo = treasury_repo
        self._corporate_action_repo = corporate_action_repo
        self._macro_repo = macro_repo
        self._dividend_repo = dividend_repo
        self._snapshot_repo = snapshot_repo
        self._citation_repo = citation_repo
        self._pack = pack
        self._evaluator: FactorEvaluator = (
            evaluator if evaluator is not None else FactorEvaluator()
        )
        self._batch_run_repo: BatchRunRepository | None = (
            batch_run_repo
            if batch_run_repo is not None
            else (SqlBatchRunRepository(session) if session is not None else None)
        )
        self._alert: BatchAlertHandler = (
            alert_handler if alert_handler is not None else NullAlertHandler()
        )
        self._security_types = security_types

    def run(self, *, observed_date: date) -> SnapshotBatchSummary:
        """universe × factor 를 평가하여 stock_snapshots 에 UPSERT.

        Args:
            observed_date: precompute 기준일 (= snapshot 의 as_of_date / PIT 기준).
                오늘 날짜 권장 (라이브 precompute).

        Returns:
            SnapshotBatchSummary — 종목별 성공/실패 집계 + 저장 row 수.

        Note:
            본 메서드는 raise 하지 않음 — 종목 전체 실패도 summary 로 반환
            (ecos/kosis/dart 계약 동일). batch_run finalize 는 try/finally 보장.

        후속 read-bypass consumer 의 전제 (oracle M3 — 완전성 추론 금지):
            배치는 부분 실패(일부 종목만 저장)가 가능하다. 부분 실패 시 status 는
            'partial' (failure_count>0), 전부 성공 시 'success' 로 저장된다 —
            그러나 'success' 라벨도 per-record 완전성을 보장하지 않는다(백필
            in-flight 가능). 따라서 후속 screen/market read-bypass 는 **status 로
            "이 as_of 의 snapshot 이 완전하다"고 추론하면 안 된다** (partial 도입
            으로 이 불변은 오히려 강화됨). 반드시 per-(code, factor) snapshot
            존재를 확인하고 miss 시 live 평가로 fallback 해야 한다(또한
            data_versions 불일치·reproduce frozen cutoff 시에도 bypass 금지). 본
            producer 는 freeze fingerprint 만 충실히 저장하고, serve-vs-recompute
            판정 책임은 consumer 에 있다.
        """
        batch_id = uuid4()
        started_at = datetime.now(UTC)

        # batch_runs INSERT(status='running') — citation FK 보장을 위해 선행.
        # source="PRECOMPUTE", market=None (전 universe 1배치 — 종목 단위 아님).
        self._start_batch_run(
            batch_id=batch_id, started_at=started_at, source="PRECOMPUTE",
        )

        success_count = 0
        snapshots_saved = 0
        failures: list[tuple[str, str]] = []
        # finally 의 summary 가 참조 — setup 실패 시에도 정의 보장(좀비 running 방지).
        universe_records: list = []
        factors: Sequence[dict] = ()

        # setup(freeze·citation save·universe·prime) 을 try 로 감싼다 — 실패 시 run()
        # 이 raise 하지 않고(계약) global failure 로 기록 + universe 비워 루프 skip,
        # 아래 loop 의 finally 가 finalize 를 보장(oracle L1 — citation save/prime 이
        # try 밖이면 좀비 'running' batch_run + 계약 위반). 부분 setup 상태로 평가
        # 진입 방지를 위해 실패 시 universe_records=[] (cached_* 미정의여도 미참조).
        try:
            # 산정 당시 freeze fingerprint — 각 snapshot.data_versions 에 저장.
            # session=None(Fake) 이면 정책-only 키 (batch_id 합류 skip — 정상).
            data_versions = collect_run_data_versions(
                observed_date, self._session, self._pack,
            )

            # PRECOMPUTE citation — snapshot 은 derived 결과이므로 본 배치 run 을
            # 가리키는 단일 파생 citation 을 모든 snapshot record 가 공유한다.
            # citation 을 snapshot 보다 먼저 save 하여 FK 만족(citation → snapshot).
            citation = SourceCitation(
                id=uuid4(),
                source=SourceKind.PRECOMPUTE,
                identifier=(
                    f"SNAPSHOT_PRECOMPUTE|{observed_date.isoformat()}|{batch_id}"
                ),
                retrieved_at=started_at,
                effective_date=observed_date,
                adapter_version="1.0.0",
                batch_id=batch_id,
                url=None,
            )
            if self._session is not None:
                self._citation_repo.save(citation)
            citation_id = citation.id

            # universe 사전 필터 — 폐지 lineage(current_code None) 제외 + ADR-0023
            # D7 자산군 필터 (screen_active_codes 와 동일 정신).
            universe_records = [
                r
                for r in self._stocks_repo.list_active(as_of=observed_date)
                if r.current_code and r.security_type in self._security_types
            ]
            universe_codes = [r.current_code for r in universe_records]

            # per-code N+1 완화 — 루프 **이전** 1회 bulk prime (screen_active_codes
            # 미러). cutoff 없음(live precompute) — batch_cutoff 미주입 = 무필터.
            # prime window 밖/미prime 요청은 inner 위임이라 정확성 동일. macro 는
            # 종목-무관이라 prime 이 아닌 request-scoped memoize.
            cached_price = (
                self._price_repo
                if isinstance(self._price_repo, CachingPriceRepository)
                else CachingPriceRepository(self._price_repo)
            )
            cached_price.prime(universe_codes, as_of=observed_date)

            cached_market_cap = (
                self._market_cap_repo
                if isinstance(self._market_cap_repo, CachingMarketCapRepository)
                else CachingMarketCapRepository(self._market_cap_repo)
            )
            cached_market_cap.prime(universe_codes, as_of=observed_date)

            cached_financial = (
                self._financial_repo
                if isinstance(self._financial_repo, CachingFinancialRepository)
                else CachingFinancialRepository(self._financial_repo)
            )
            cached_financial.prime(universe_codes, as_of=observed_date)

            cached_treasury = (
                self._treasury_repo
                if isinstance(
                    self._treasury_repo, CachingTreasurySharesRepository
                )
                else CachingTreasurySharesRepository(self._treasury_repo)
            )
            cached_treasury.prime(universe_codes, as_of=observed_date)

            # corporate_action 도 bulk prime — live screen_active_codes 가 이제 CA 를
            # CachingCorporateActionRepository 로 prime 하므로(screen.py, M1 해소
            # cycle) 배치도 동일 prime 으로 **대칭**(byte-동일 캐시라 값 불변, 후속
            # read-bypass 가 비교할 live 와 동일 코드 경로). CA chain(split/dividend/
            # buyback) 해소 fetch_actions 의 per-stock N+1 제거. batch_cutoff 없음
            # (CA freeze 불가, D4). None 가드 — screen/backtest 와 방어 일관(oracle
            # L1, batch 는 항상 CA 주입이나 Fake/테스트 None 구성 대비).
            cached_ca = self._corporate_action_repo
            if cached_ca is not None:
                cached_ca = (
                    cached_ca
                    if isinstance(cached_ca, CachingCorporateActionRepository)
                    else CachingCorporateActionRepository(cached_ca)
                )
                cached_ca.prime(universe_codes, as_of=observed_date)

            # dividend 도 bulk prime — total_return field(배당 재투자 §2.4)의 종목별
            # fetch_dividends per-code N+1 제거(screen·backtest·CA 와 동형). batch_
            # cutoff 없음(dividend freeze 불가, D4). None 가드 — both-or-neither
            # (dividend None → adjuster None) 보존. raw 적재 후 serve-time 해소라
            # byte-동일(read-bypass 가 비교할 live screen 과 동일 코드 경로).
            cached_dividend = self._dividend_repo
            if cached_dividend is not None:
                cached_dividend = (
                    cached_dividend
                    if isinstance(cached_dividend, CachingDividendRepository)
                    else CachingDividendRepository(cached_dividend)
                )
                cached_dividend.prime(universe_codes, as_of=observed_date)

            # macro 는 (indicator_id, as_of) 당 1회 memoize (prime 없음 — 종목-무관).
            cached_macro = (
                self._macro_repo
                if isinstance(self._macro_repo, CachingMacroIndicatorRepository)
                else CachingMacroIndicatorRepository(self._macro_repo)
            )

            factors = self._pack.body["factors"]
            # 배치 내 전 record 의 computed_at 통일 (산정 시각 일관 — freeze 의도).
            computed_at = datetime.now(UTC)
        except Exception as exc:  # noqa: BLE001
            # setup 실패(freeze/citation save/list_active/prime) — 배치 전체 격리.
            # universe 를 비워 평가 루프 skip → 부분 setup 상태 평가 방지. run() 은
            # raise 하지 않고 finally 가 finalize(좀비 running 방지).
            reason = f"setup failed: {type(exc).__name__}: {exc}"
            failures.append(("__batch__", reason))
            self._alert.on_failure("__batch__", reason)
            universe_records = []

        try:
            for record in universe_records:
                code = record.current_code
                # 종목별 SAVEPOINT — session 있을 때만. begin_nested() 는 예외 시
                # SAVEPOINT 자동 rollback + re-raise → 다음 종목 session 정상 진행.
                # Fake 모드(None)는 nullcontext — no-op.
                savepoint: AbstractContextManager[object] = (
                    self._session.begin_nested()
                    if self._session is not None
                    else nullcontext()
                )
                try:
                    with savepoint:
                        records = self._evaluate_stock(
                            code=code,
                            observed_date=observed_date,
                            factors=factors,
                            citation_id=citation_id,
                            computed_at=computed_at,
                            data_versions=data_versions,
                            cached_price=cached_price,
                            cached_financial=cached_financial,
                            cached_ca=cached_ca,
                            cached_market_cap=cached_market_cap,
                            cached_treasury=cached_treasury,
                            cached_macro=cached_macro,
                            cached_dividend=cached_dividend,
                        )
                        # save_snapshots 는 항상 호출 (Fake in-memory 수집 포함).
                        # flush 는 session 있을 때만 (FK/UPSERT 확정).
                        self._snapshot_repo.save_snapshots(records)
                        if self._session is not None:
                            self._session.flush()
                    success_count += 1
                    snapshots_saved += len(records)
                except Exception as exc:  # noqa: BLE001
                    # SAVEPOINT 자동 rollback 완료 → 다음 종목 진행 가능. 종목
                    # 단위 격리 (한 종목 평가/저장 실패가 배치 전체 abort 안 함).
                    # IntegrityError 별도 skip 불요 — UPSERT 라 중복 충돌 없음.
                    reason = f"{type(exc).__name__}: {exc}"
                    failures.append((code, reason))
                    self._alert.on_failure(code, reason)

        finally:
            # try/finally — 루프 외부 예외에도 finalize 호출 (좀비 'running' 방지).
            summary = SnapshotBatchSummary(
                batch_id=batch_id,
                observed_date=observed_date,
                universe_count=len(universe_records),
                factor_count=len(factors),
                success_count=success_count,
                failure_count=len(failures),
                skipped_count=0,
                snapshots_saved=snapshots_saved,
                failures=tuple(sorted(failures)),
                started_at=started_at,
                ended_at=datetime.now(UTC),
            )
            self._finalize_batch_run(summary)

        self._alert.on_complete(summary)
        return summary

    # =========================================================================
    # 내부 — 종목별 평가
    # =========================================================================

    def _evaluate_stock(
        self,
        *,
        code: str,
        observed_date: date,
        factors: Sequence[dict],
        citation_id: UUID,
        computed_at: datetime,
        data_versions: object,
        cached_price: PriceRepository,
        cached_financial: FinancialRepository,
        cached_ca: CorporateActionRepository,
        cached_market_cap: MarketCapRepository,
        cached_treasury: TreasurySharesRepository,
        cached_macro: MacroIndicatorRepository,
        cached_dividend: DividendRepository | None,
    ) -> list[StockSnapshotRecord]:
        """단일 종목의 전 factor 평가 → StockSnapshotRecord 리스트 구성.

        screen_active_codes 의 _build_provider 와 동일 인자 — 동일 평가 의미.
        N/A factor 도 record 로 저장한다 (value=None, na_reason 은 inputs 로 복원
        가능). 라이브 평가와 동일하게 항상 모든 factor 의 결과를 남겨야 lookup 이
        "미산정"과 "데이터 결손"을 구별 가능 (StockSnapshotRecord value None 의미).
        """
        # both-or-neither (DbFieldProvider assert) — dividend_repo None 이면
        # total_return_adjuster 도 None (screen.py _build_provider 와 동일).
        provider = DbFieldProvider(
            code=code,
            as_of=observed_date,
            price_repo=cached_price,
            financial_repo=cached_financial,
            corporate_action_repo=cached_ca,
            market_cap_repo=cached_market_cap,
            treasury_repo=cached_treasury,
            macro_repo=cached_macro,
            dividend_repo=cached_dividend,
            total_return_adjuster=(
                TotalReturnAdjuster() if cached_dividend is not None else None
            ),
            factor_pack=self._pack,
            evaluator=self._evaluator,
        )

        records: list[StockSnapshotRecord] = []
        for f in factors:
            result = self._evaluator.evaluate(f, provider, as_of=observed_date)
            records.append(
                StockSnapshotRecord(
                    stock_code=code,
                    as_of_date=observed_date,
                    factor_uuid=UUID(f["uuid"]),
                    value=result.value,
                    value_unit=f["unit"],
                    inputs=dict(result.inputs_used),
                    citation_id=citation_id,
                    computed_at=computed_at,
                    data_versions=dict(data_versions),  # type: ignore[arg-type]
                )
            )
        return records

    # =========================================================================
    # 내부 — batch_runs 영속화 (ecos_daily 와 동일)
    # =========================================================================

    def _start_batch_run(
        self,
        *,
        batch_id: UUID,
        started_at: datetime,
        source: str,
    ) -> None:
        """배치 시작 시 batch_runs row INSERT (status='running').

        source="PRECOMPUTE", market=None (전 universe 1배치 — 종목 단위 아님).
        batch_run_repo None(Fake) 이면 no-op.
        """
        if self._batch_run_repo is None:
            return
        self._batch_run_repo.start(
            run_id=batch_id,
            market=None,
            source=source,
            started_at=started_at,
        )

    def _finalize_batch_run(self, summary: SnapshotBatchSummary) -> None:
        """배치 종료 시 batch_runs row finalize (UPDATE).

        failure_count>0 이면 status='partial', 아니면 'success'. partial 도
        성공분의 snapshot row 를 commit 했으므로 freeze 후보·freshness 자격은
        success 와 동일 (collect_batch_versions / latest_successful 가 둘 다
        수용) — 라벨만 운영 가시성 위해 분리 (ecos/kosis/dart 동일 패턴).
        batch_run_repo None(Fake) 이면 no-op.

        Note (consumer 불변 — partial 도입으로 오히려 강화):
            consumer 는 batch_run.status 로 데이터 완전성을 추론하면 안 된다.
            partial 은 물론 success 라벨도 per-record 완전성을 보장하지 않으므로
            (백필 in-flight 가능), consumer 는 항상 per-record 존재 확인 +
            live fallback 해야 한다.
        """
        if self._batch_run_repo is None:
            return
        status = (
            BATCH_STATUS_PARTIAL if summary.failure_count > 0
            else BATCH_STATUS_SUCCESS
        )
        if summary.failure_count > 0:
            logger.warning(
                "snapshot precompute 배치 부분 실패 — batch_id=%s "
                "failure_count=%d failures=%s (status=partial 로 저장)",
                summary.batch_id, summary.failure_count, summary.failures,
            )
        self._batch_run_repo.finalize(
            run_id=summary.batch_id,
            ended_at=summary.ended_at,
            success_count=summary.success_count,
            status=status,
        )


# =============================================================================
# scheduler 용 순수 함수 (run_dart_job 패턴 미러)
# =============================================================================

def run_snapshot_job(
    *,
    session: Session,
    observed_date: date,
    pack: LoadedPack = DEFAULT_PACK,
) -> SnapshotBatchSummary:
    """stock_snapshots precompute 일배치 실행 — session 단위 트랜잭션.

    scheduler 가 호출하는 진입점 (run_dart_job 패턴 동일). session 으로 Sql repo
    전부를 구성하여 SnapshotDailyBatch 를 실행한다. dividend_repo 도 주입 —
    total_return_trailing_1y factor 평가용 (M7).

    Args:
        session: SQLAlchemy session.
        observed_date: precompute 기준일.
        pack: 평가 대상 factor pack. default=DEFAULT_PACK.

    Returns:
        SnapshotBatchSummary.
    """
    # 지역 import — scheduler 경로에서만 필요한 무거운 Sql repo 묶음.
    from app.repositories.citation_repository import SqlCitationRepository
    from app.repositories.sql_repositories import (
        SqlCorporateActionRepository,
        SqlDividendRepository,
        SqlFinancialRepository,
        SqlMacroIndicatorRepository,
        SqlMarketCapRepository,
        SqlPriceRepository,
        SqlStocksMasterRepository,
        SqlStockSnapshotRepository,
        SqlTreasurySharesRepository,
    )

    batch = SnapshotDailyBatch(
        session=session,
        stocks_repo=SqlStocksMasterRepository(session),
        price_repo=SqlPriceRepository(session),
        financial_repo=SqlFinancialRepository(session),
        market_cap_repo=SqlMarketCapRepository(session),
        treasury_repo=SqlTreasurySharesRepository(session),
        corporate_action_repo=SqlCorporateActionRepository(session),
        macro_repo=SqlMacroIndicatorRepository(session),
        dividend_repo=SqlDividendRepository(session),
        snapshot_repo=SqlStockSnapshotRepository(session),
        citation_repo=SqlCitationRepository(session),
        pack=pack,
    )
    summary = batch.run(observed_date=observed_date)
    logger.info(
        "snapshot precompute 배치 완료 — universe=%d factor=%d success=%d "
        "failure=%d snapshots_saved=%d (observed_date=%s)",
        summary.universe_count, summary.factor_count, summary.success_count,
        summary.failure_count, summary.snapshots_saved, observed_date,
    )
    return summary
