"""KrxDailyBatch / DartDailyBatch 의 alert_handler + dry_run 동작 단위 test.

T43-B (일배치 dry-run + adapter 충돌 alert) 의 단위 회귀 가드. 외부 호출 없는
in-memory Fake adapter / repo 로 alert hook 의 호출 순서와 dry_run 의 DB
write skip 의도를 검증.

테스트 매트릭스:
    A1. NullAlertHandler 가 default — 명시 None 시 no-op.
    A2. CapturingAlertHandler — conflict / failure / complete 호출 횟수/페이로드.
    A3. dry_run=True — 정상 path 에서 citation/price save 호출 0.
    A4. dry_run + conflict — alert.on_conflict 는 여전히 호출 (검증 의도 보존).
    A5. dry_run + universe fetch 실패 — on_failure("universe", ...) + on_complete.
    A6. DartDailyBatch dry_run — financial save 호출 0 + on_complete summary.dry_run.
    A7. DartDailyBatch corp_code 매핑 누락 → on_failure("skipped: ...") + skipped_codes.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pandas as pd
import pytest

from app.adapters.dart_adapter import DartAdapter
from app.adapters.fdr_adapter import FdrAdapter
from app.adapters.pykrx_adapter import PykrxAdapter
from app.repositories.citation_repository import FakeCitationRepository
from app.repositories.fakes import (
    FakeFinancialRepository,
    FakePriceRepository,
)
from app.repositories.pit_protocols import FinancialRepository
from app.services.conflict_detector import ConflictDetectionResult
from app.services.corp_code_mapping import CorpCodeMapping
from app.services.krx_calendar import DEFAULT_CALENDAR
from batch.alerts import (
    BatchAlertHandler,
    LoggingAlertHandler,
    NullAlertHandler,
)
from batch.dart_daily import DartBatchSummary, DartDailyBatch
from batch.krx_daily import BatchSummary, KrxDailyBatch

# =============================================================================
# Fixtures — pykrx / FDR / DART mock helpers (test_krx_daily 와 일관 패턴)
# =============================================================================


def _ohlcv_df(*, day: date, close: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "시가": [close], "고가": [close], "저가": [close],
            "종가": [close], "거래량": [1_000_000], "거래대금": [close * 1_000_000],
        },
        index=pd.to_datetime([day]),
    )


def _market_cap_df(*, day: date, market_cap: int, shares: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "시가총액": [market_cap],
            "거래량": [1_000_000],
            "거래대금": [market_cap * 1_000],
            "상장주식수": [shares],
        },
        index=pd.to_datetime([day]),
    )


def _fdr_ohlcv_df(*, day: date, close: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [close], "High": [close], "Low": [close],
            "Close": [close], "Volume": [1_000_000], "Change": [0.0],
        },
        index=pd.to_datetime([day]),
    )


def _make_pykrx_mock(
    *,
    universe: list[str],
    ohlcv_close_by_code: dict[str, float] | None = None,
    fail_universe: bool = False,
) -> Any:
    """pykrx mock — alert test 범위에 필요한 부분만."""
    ohlcv_close_by_code = ohlcv_close_by_code or {}
    mock = MagicMock()

    def _ohlcv(fromdate: str, todate: str, code: str) -> pd.DataFrame:
        close = ohlcv_close_by_code.get(code, 70000.0)
        d = date.fromisoformat(f"{fromdate[:4]}-{fromdate[4:6]}-{fromdate[6:8]}")
        return _ohlcv_df(day=d, close=close)

    def _mc(fromdate: str, todate: str, code: str) -> pd.DataFrame:
        d = date.fromisoformat(f"{fromdate[:4]}-{fromdate[4:6]}-{fromdate[6:8]}")
        return _market_cap_df(day=d, market_cap=400_000_000_000_000, shares=5_000_000_000)

    if fail_universe:
        mock.get_market_ticker_list = MagicMock(
            side_effect=ConnectionError("krx down"),
        )
    else:
        mock.get_market_ticker_list = MagicMock(return_value=list(universe))
    mock.get_market_ohlcv = MagicMock(side_effect=_ohlcv)
    mock.get_market_cap_by_date = MagicMock(side_effect=_mc)
    return mock


def _make_fdr_mock(*, ohlcv_close_by_code: dict[str, float]) -> Any:
    def _data_reader(code: str, fromdate: str, todate: str) -> pd.DataFrame:
        close = ohlcv_close_by_code.get(code, 70000.0)
        return _fdr_ohlcv_df(day=date.fromisoformat(fromdate), close=close)

    mock = MagicMock()
    mock.DataReader = MagicMock(side_effect=_data_reader)
    return mock


@pytest.fixture
def business_day() -> date:
    """v1.0.0 calendar 의 verified 영업일."""
    return date(2024, 5, 7)


# =============================================================================
# Capturing alert handler — Protocol 구현체로 호출 기록
# =============================================================================


class CapturingAlertHandler:
    """모든 alert 호출을 list 에 기록하는 test handler.

    Protocol contract 검증 — runtime_checkable 의 isinstance 통과 + 호출
    순서 / 페이로드를 assertion 가능하게 보존.
    """

    def __init__(self) -> None:
        self.conflicts: list[ConflictDetectionResult] = []
        self.failures: list[tuple[str, str]] = []
        self.completions: list[object] = []

    def on_conflict(self, conflict: ConflictDetectionResult) -> None:
        self.conflicts.append(conflict)

    def on_failure(self, code: str, reason: str) -> None:
        self.failures.append((code, reason))

    def on_complete(self, summary: object) -> None:
        self.completions.append(summary)


# =============================================================================
# A1. NullAlertHandler 가 default (None 주입 시)
# =============================================================================


def test_null_alert_handler_is_default(business_day: date) -> None:
    """alert_handler 미주입 시 NullAlertHandler 가 적용 → 호출 무해."""
    pykrx_mock = _make_pykrx_mock(universe=["005930"])
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=FakeCitationRepository(),
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
    )
    # alert_handler 주입 없음 — silent 통과 + 정상 결과 반환.
    summary = batch.run(as_of=business_day, market="KOSPI")
    assert summary.success_count == 1


def test_null_alert_handler_satisfies_protocol() -> None:
    """NullAlertHandler 가 Protocol contract 통과 (runtime_checkable)."""
    handler = NullAlertHandler()
    assert isinstance(handler, BatchAlertHandler)


def test_logging_alert_handler_satisfies_protocol() -> None:
    """LoggingAlertHandler 도 Protocol contract 통과."""
    handler = LoggingAlertHandler()
    assert isinstance(handler, BatchAlertHandler)


# =============================================================================
# A2. CapturingAlertHandler — 호출 순서/페이로드
# =============================================================================


def test_alert_handler_on_complete_called_once_on_success(
    business_day: date,
) -> None:
    """정상 path → on_complete 1 회, conflict/failure 0 회."""
    handler = CapturingAlertHandler()
    pykrx_mock = _make_pykrx_mock(universe=["005930"])
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=FakeCitationRepository(),
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
        alert_handler=handler,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    assert len(handler.completions) == 1
    assert isinstance(handler.completions[0], BatchSummary)
    assert handler.completions[0].batch_id == summary.batch_id
    assert handler.conflicts == []
    assert handler.failures == []


def test_alert_handler_on_conflict_called_when_fdr_disagrees(
    business_day: date,
) -> None:
    """pykrx close=70000 vs FDR close=70500 → 0.7% diff > 0.1% threshold.

    on_conflict 1 회 + on_complete 1 회.
    """
    handler = CapturingAlertHandler()
    pykrx_mock = _make_pykrx_mock(
        universe=["005930"],
        ohlcv_close_by_code={"005930": 70000.0},
    )
    fdr_mock = _make_fdr_mock(ohlcv_close_by_code={"005930": 70500.0})
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=FdrAdapter(fdr_module=fdr_mock),
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=FakeCitationRepository(),
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
        alert_handler=handler,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    assert summary.success_count == 1
    assert len(handler.conflicts) == 1
    assert len(handler.conflicts[0].conflicts) > 0
    # close field 의 충돌.
    fields = {c.field for c in handler.conflicts[0].conflicts}
    assert "close" in fields
    assert len(handler.completions) == 1


def test_alert_handler_on_conflict_not_called_when_aligned(
    business_day: date,
) -> None:
    """pykrx close == FDR close → ConflictDetectionResult 가 empty → alert skip."""
    handler = CapturingAlertHandler()
    pykrx_mock = _make_pykrx_mock(
        universe=["005930"],
        ohlcv_close_by_code={"005930": 70000.0},
    )
    fdr_mock = _make_fdr_mock(ohlcv_close_by_code={"005930": 70000.0})
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=FdrAdapter(fdr_module=fdr_mock),
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=FakeCitationRepository(),
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
        alert_handler=handler,
    )
    batch.run(as_of=business_day, market="KOSPI")
    # ConflictDetectionResult 자체는 summary.conflicts 에 들어가나
    # conflicts/missing 이 비어있어 alert 는 skip (false-positive 회피).
    assert handler.conflicts == []


# =============================================================================
# A3. dry_run — DB write skip
# =============================================================================


def test_dry_run_skips_citation_and_price_save(business_day: date) -> None:
    """dry_run=True 면 FakeCitationRepository / FakePriceRepository 의 save 미호출."""
    pykrx_mock = _make_pykrx_mock(universe=["005930"])
    citation_repo = FakeCitationRepository()
    price_repo = FakePriceRepository(records=())
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=citation_repo,
        price_repo=price_repo,
        throttle_seconds=0,
    )
    summary = batch.run(as_of=business_day, market="KOSPI", dry_run=True)
    # 정상 success_count — fetch 자체는 동일 수행.
    assert summary.success_count == 1
    assert summary.dry_run is True
    # 그러나 DB persist 는 0.
    assert citation_repo.fetch_by_batch(summary.batch_id) == ()
    saved = price_repo.fetch_prices(
        "005930", as_of=business_day, start=business_day,
    )
    # FakePriceRepository.fetch_prices 는 list 반환 — empty 검증은 len.
    assert len(saved) == 0


def test_dry_run_still_triggers_conflict_alert(business_day: date) -> None:
    """dry_run 도 ConflictDetector 는 작동 — 검증 의도가 dry_run 본질."""
    handler = CapturingAlertHandler()
    pykrx_mock = _make_pykrx_mock(
        universe=["005930"],
        ohlcv_close_by_code={"005930": 70000.0},
    )
    fdr_mock = _make_fdr_mock(ohlcv_close_by_code={"005930": 70500.0})
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=FdrAdapter(fdr_module=fdr_mock),
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=FakeCitationRepository(),
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
        alert_handler=handler,
    )
    summary = batch.run(as_of=business_day, market="KOSPI", dry_run=True)
    assert summary.dry_run is True
    assert len(handler.conflicts) == 1


# =============================================================================
# A4. universe fetch 실패 → on_failure("universe", ...) + on_complete
# =============================================================================


def test_universe_fetch_failure_triggers_alert(business_day: date) -> None:
    handler = CapturingAlertHandler()
    pykrx_mock = _make_pykrx_mock(universe=[], fail_universe=True)
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=FakeCitationRepository(),
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
        alert_handler=handler,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    assert summary.skipped_reason is not None
    assert "universe_fetch_failed" in summary.skipped_reason
    # universe sentinel 로 on_failure 호출.
    assert len(handler.failures) == 1
    assert handler.failures[0][0] == "universe"
    assert len(handler.completions) == 1


# =============================================================================
# A5/A6/A7. DartDailyBatch — dry_run + alert + corp_code 매핑 누락
# =============================================================================


def _make_dart_adapter_mock(*, account_count: int = 5) -> Any:
    """DartAdapter mock — fetch_financial_statement 가 row 반환."""
    from datetime import UTC, datetime
    from datetime import date as _date
    from decimal import Decimal
    from uuid import uuid4

    from app.adapters.base import (
        FetchResult,
        FinancialStatementRow,
        IfrsType,
    )
    from app.models.source_citation import SourceCitation, SourceKind

    def _build_citation() -> SourceCitation:
        return SourceCitation(
            id=uuid4(),
            source=SourceKind.DART,
            identifier="mock|00126380|2023|4|consolidated",
            url="https://opendart.fss.or.kr/",
            effective_date=_date(2024, 3, 31),
            adapter_version="1.0.0",
            batch_id=uuid4(),
            retrieved_at=datetime.now(UTC),
        )

    def _build_row(account: str, ifrs: IfrsType) -> FinancialStatementRow:
        return FinancialStatementRow(
            code="005930",
            fiscal_year=2023,
            fiscal_quarter=4,
            effective_date=_date(2024, 3, 31),
            account=account,
            value=Decimal("1000000"),
            unit="krw",
            ifrs_type=ifrs,
            rcept_no="20240331000001",
            currency="KRW",
            effective_date_precise=True,
        )

    def _fetch(
        *, code: str, corp_code: str, fiscal_year: int,
        fiscal_quarter: int, ifrs_type: IfrsType, batch_id: UUID,
    ) -> FetchResult[tuple[FinancialStatementRow, ...]]:
        rows = tuple(
            _build_row(f"account_{i}", ifrs_type)
            for i in range(account_count)
        )
        return FetchResult(data=rows, citations=(_build_citation(),))

    adapter = MagicMock(spec=DartAdapter)
    adapter.fetch_financial_statement = MagicMock(side_effect=_fetch)
    return adapter


def test_dart_dry_run_skips_save() -> None:
    handler = CapturingAlertHandler()
    citation_repo = FakeCitationRepository()
    # MagicMock(spec=FinancialRepository) — save_financials 호출 횟수 검증용.
    # FakeFinancialRepository 는 save 가 silent 누적 → mock spy 가 dry_run
    # 검증의 가장 명확한 신호.
    financial_repo = MagicMock(spec=FinancialRepository)
    mapping = CorpCodeMapping.from_dict({"005930": "00126380"})
    adapter = _make_dart_adapter_mock(account_count=5)

    batch = DartDailyBatch(
        adapter=adapter,
        corp_mapping=mapping,
        citation_repo=citation_repo,
        financial_repo=financial_repo,
        throttle_seconds=0,
        alert_handler=handler,
    )
    summary = batch.run(
        stock_codes=["005930"],
        fiscal_year=2023,
        fiscal_quarter=4,
        dry_run=True,
    )
    assert summary.dry_run is True
    assert summary.success_count == 1
    # CFS + OFS 두 IFRS type × 5 account = 10 row "fetched" (dry-run 의미 —
    # total_rows_saved 는 dry_run 시 fetch 된 row 수).
    assert summary.total_rows_saved == 10
    # 실제 financial save 호출 0.
    financial_repo.save_financials.assert_not_called()
    # citation 도 미저장.
    assert citation_repo.fetch_by_batch(summary.batch_id) == ()
    assert len(handler.completions) == 1
    assert handler.completions[0] is summary


def test_dart_corp_code_missing_triggers_skip_alert() -> None:
    """corp_code 매핑 누락 → skipped + on_failure("skipped: ...")."""
    handler = CapturingAlertHandler()
    mapping = CorpCodeMapping.from_dict({})  # 매핑 0 개.
    adapter = _make_dart_adapter_mock()
    batch = DartDailyBatch(
        adapter=adapter,
        corp_mapping=mapping,
        citation_repo=FakeCitationRepository(),
        financial_repo=FakeFinancialRepository(records=()),
        throttle_seconds=0,
        alert_handler=handler,
    )
    summary = batch.run(
        stock_codes=["005930"],
        fiscal_year=2023,
        fiscal_quarter=4,
    )
    assert summary.skipped_count == 1
    assert summary.skipped_codes == ("005930",)
    # skipped → on_failure 호출 (alert 채널 통일 — reason prefix 로 구분).
    assert handler.failures == [("005930", "skipped: corp_code mapping missing")]
    # complete 도 1 회.
    assert len(handler.completions) == 1
    assert isinstance(handler.completions[0], DartBatchSummary)
