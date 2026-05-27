"""KrxDailyBatch 단위 테스트 + ConflictDetector 통합 검증.

테스트 매트릭스:
    1. 휴장일 입력 → skipped_reason="non_business_day"
    2. universe fetch 실패 → skipped_reason
    3. 정상 path — 종목 처리 성공 + citation/price save 검증
    4. 종목별 failure isolation — 한 종목 실패가 다른 종목 처리 막지 않음
    5. ConflictDetector 통합 — pykrx vs FDR 충돌 검출 + estimated_fields 제외
    6. FDR 실패 시 primary 성공이면 종목 successful — conflict_result=None
    7. citation dedup — 같은 batch 의 중복 id save 시 skip
    8. throttle=0 으로 test 빠르게
    9. ConflictDetector standalone — 단순 비교 + missing dates
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Sequence
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pandas as pd
import pytest

from app.adapters.base import (
    AdapterError,
    FetchResult,
    MarketCapRow,
    OHLCVRow,
)
from app.adapters.fdr_adapter import FdrAdapter
from app.adapters.pykrx_adapter import PykrxAdapter
from app.repositories.citation_repository import FakeCitationRepository
from app.repositories.fakes import FakePriceRepository
from app.services.conflict_detector import (
    ConflictDetectionResult,
    ConflictDetector,
    ConflictReport,
)
from app.services.krx_calendar import DEFAULT_CALENDAR, TradingCalendar
from batch.krx_daily import BatchSummary, KrxDailyBatch


# =============================================================================
# Fixtures
# =============================================================================

def _ohlcv_df(*, day: date, close: float) -> pd.DataFrame:
    """pykrx 한글 컬럼 OHLCV DataFrame — 단일 일."""
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
    """FDR 영문 컬럼 OHLCV DataFrame — 단일 일."""
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
    market_cap_by_code: dict[str, int] | None = None,
    fail_codes: set[str] | None = None,
) -> Any:
    """pykrx module mock — universe + 종목별 OHLCV/market_cap."""
    fail_codes = fail_codes or set()
    ohlcv_close_by_code = ohlcv_close_by_code or {}
    market_cap_by_code = market_cap_by_code or {}

    mock = MagicMock()

    def _ohlcv(fromdate: str, todate: str, code: str) -> pd.DataFrame:
        if code in fail_codes:
            raise ConnectionError(f"simulated fetch failure for {code}")
        close = ohlcv_close_by_code.get(code, 70000.0)
        return _ohlcv_df(day=date.fromisoformat(_yyyymmdd_to_iso(fromdate)),
                         close=close)

    def _market_cap(fromdate: str, todate: str, code: str) -> pd.DataFrame:
        if code in fail_codes:
            raise ConnectionError(f"simulated fetch failure for {code}")
        mc = market_cap_by_code.get(code, 400_000_000_000_000)
        return _market_cap_df(
            day=date.fromisoformat(_yyyymmdd_to_iso(fromdate)),
            market_cap=mc, shares=5_000_000_000,
        )

    mock.get_market_ohlcv = MagicMock(side_effect=_ohlcv)
    mock.get_market_cap_by_date = MagicMock(side_effect=_market_cap)
    mock.get_market_ticker_list = MagicMock(return_value=list(universe))
    return mock


def _make_fdr_mock(
    *,
    ohlcv_close_by_code: dict[str, float],
) -> Any:
    """FDR module mock — DataReader 만."""
    def _data_reader(code: str, fromdate: str, todate: str) -> pd.DataFrame:
        close = ohlcv_close_by_code.get(code, 70000.0)
        # FDR 의 todate-style: "2024-05-07". fromdate 도 iso.
        return _fdr_ohlcv_df(
            day=date.fromisoformat(fromdate), close=close,
        )

    mock = MagicMock()
    mock.DataReader = MagicMock(side_effect=_data_reader)
    return mock


def _yyyymmdd_to_iso(yyyymmdd: str) -> str:
    """`20240507` → `2024-05-07`."""
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


@pytest.fixture
def business_day() -> date:
    """v1.0.0 calendar 의 verified 범위 내 영업일."""
    return date(2024, 5, 7)  # 화요일.


@pytest.fixture
def holiday() -> date:
    """KRX 휴장일 (어린이날 대체)."""
    return date(2024, 5, 6)  # 월요일 — 어린이날 대체공휴일.


# =============================================================================
# 1. 휴장일 skip
# =============================================================================

def test_run_skips_on_non_business_day(holiday: date) -> None:
    """휴장일 입력 → skipped_reason, fetch 호출 없음."""
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
    summary = batch.run(as_of=holiday, market="KOSPI")
    assert summary.skipped_reason == "non_business_day"
    assert summary.universe_size == 0
    # universe fetch 호출 X — pykrx mock 의 get_market_ticker_list 미호출.
    pykrx_mock.get_market_ticker_list.assert_not_called()


# =============================================================================
# 2. universe fetch 실패
# =============================================================================

def test_run_handles_universe_fetch_failure(business_day: date) -> None:
    pykrx_mock = MagicMock()
    pykrx_mock.get_market_ticker_list = MagicMock(
        side_effect=ConnectionError("krx down"),
    )
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=FakeCitationRepository(),
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    assert summary.skipped_reason is not None
    assert "universe_fetch_failed" in summary.skipped_reason
    assert summary.success_count == 0


# =============================================================================
# 3. 정상 path — citation/price save
# =============================================================================

def test_run_normal_path_saves_citations_and_prices(
    business_day: date,
) -> None:
    universe = ["005930", "000660"]
    pykrx_mock = _make_pykrx_mock(
        universe=universe,
        ohlcv_close_by_code={"005930": 70000.0, "000660": 130000.0},
    )
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
    summary = batch.run(as_of=business_day, market="KOSPI")

    assert summary.skipped_reason is None
    assert summary.universe_size == 2
    assert summary.success_count == 2
    assert summary.failure_count == 0
    # Price 가 영구화됐는지 확인 — 양 종목 모두 fetch.
    samsung_prices = price_repo.fetch_prices(
        "005930", as_of=business_day, start=business_day,
    )
    assert len(samsung_prices) == 1
    assert samsung_prices[0].close_raw == Decimal("70000.0")
    sk_prices = price_repo.fetch_prices(
        "000660", as_of=business_day, start=business_day,
    )
    assert len(sk_prices) == 1
    assert sk_prices[0].close_raw == Decimal("130000.0")
    # market_cap_rows 가 summary 에 포함 (Phase B 의 save 대기).
    assert len(summary.market_cap_rows) == 2
    # Citation 도 batch_id 별 있음.
    cited = citation_repo.fetch_by_batch(summary.batch_id)
    # 종목당 universe + ohlcv + market_cap = 5 (universe 1 + 종목 2개 × 2).
    assert len(cited) == 5


# =============================================================================
# 4. failure isolation
# =============================================================================

def test_run_isolates_failed_codes(business_day: date) -> None:
    universe = ["005930", "000660", "035420"]
    pykrx_mock = _make_pykrx_mock(
        universe=universe,
        fail_codes={"000660"},  # 1 종목만 실패.
    )
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=FakeCitationRepository(),
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    assert summary.universe_size == 3
    assert summary.success_count == 2
    assert summary.failure_count == 1
    assert len(summary.failures) == 1
    assert summary.failures[0][0] == "000660"
    assert "network error" in summary.failures[0][1] or "fetch failure" in summary.failures[0][1]


# =============================================================================
# 5. ConflictDetector 통합 (FDR cross-check + 충돌 검출)
# =============================================================================

def test_run_detects_ohlcv_conflict_via_fdr(business_day: date) -> None:
    """pykrx close=70000 vs FDR close=70500 → 0.7% diff > threshold 0.1%."""
    pykrx_mock = _make_pykrx_mock(
        universe=["005930"],
        ohlcv_close_by_code={"005930": 70000.0},
    )
    fdr_mock = _make_fdr_mock(
        ohlcv_close_by_code={"005930": 70500.0},  # 0.7% 차이.
    )
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=FdrAdapter(fdr_module=fdr_mock),
        conflict_detector=None,  # default
        calendar=DEFAULT_CALENDAR,
        citation_repo=FakeCitationRepository(),
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    assert summary.success_count == 1
    assert len(summary.conflicts) == 1
    conflict_result = summary.conflicts[0]
    # close 차이가 검출됨. value 는 estimated_fields 라 비교 X.
    conflict_fields = {c.field for c in conflict_result.conflicts}
    assert "close" in conflict_fields
    assert "value" not in conflict_result.skipped_fields or True  # 일치 검증.
    assert "value" in conflict_result.skipped_fields


# =============================================================================
# 6. FDR 실패는 primary 성공 막지 않음
# =============================================================================

def test_run_handles_fdr_failure_gracefully(business_day: date) -> None:
    pykrx_mock = _make_pykrx_mock(
        universe=["005930"],
        ohlcv_close_by_code={"005930": 70000.0},
    )
    fdr_mock = MagicMock()
    fdr_mock.DataReader = MagicMock(side_effect=ConnectionError("yahoo down"))

    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=FdrAdapter(fdr_module=fdr_mock),
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=FakeCitationRepository(),
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    # primary 는 성공 → success.
    assert summary.success_count == 1
    # conflict_result 는 FDR 실패라 None 처리 — list 에 없음 (또는 length 0).
    assert len(summary.conflicts) == 0


# =============================================================================
# 7. Citation dedup
# =============================================================================

def test_citation_dedup_idempotency(business_day: date) -> None:
    """같은 batch 의 fetch 가 같은 citation id 를 만들지 않도록 — fetch 마다 새 id.

    본 테스트는 idempotency 자체 검증 — orchestrator 의 `_save_citations` 가
    이미 save 된 id 를 skip.
    """
    pykrx_mock = _make_pykrx_mock(universe=["005930"])
    citation_repo = FakeCitationRepository()
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=citation_repo,
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    # 같은 id 의 citation 이 두 번 save 안 됨 → fetch_by_batch 가 정확한 개수.
    assert summary.success_count == 1
    # 같은 batch 의 모든 id 가 unique.
    cited = citation_repo.fetch_by_batch(summary.batch_id)
    ids = [c.id for c in cited]
    assert len(set(ids)) == len(ids)  # 중복 없음.


# =============================================================================
# 8/9. ConflictDetector standalone
# =============================================================================

# =============================================================================
# C1 회귀 — fetch-then-save 순서가 orphan citation 차단
# =============================================================================

def test_no_orphan_citation_when_market_cap_fetch_fails(
    business_day: date,
) -> None:
    """oracle 리뷰 C1 회귀 — OHLCV 성공 후 market_cap 실패 시 citation 저장 X.

    fetch-then-save 정책: 모든 fetch 가 성공해야 citation 저장 시작. 종목
    실패 시 어떤 citation 도 영구화 안 됨.
    """
    pykrx_mock = MagicMock()
    pykrx_mock.get_market_ticker_list = MagicMock(return_value=["005930"])
    pykrx_mock.get_market_ohlcv = MagicMock(
        return_value=_ohlcv_df(day=business_day, close=70000.0),
    )
    # market_cap 실패 — OHLCV 는 성공.
    pykrx_mock.get_market_cap_by_date = MagicMock(
        side_effect=ConnectionError("market_cap down"),
    )

    citation_repo = FakeCitationRepository()
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=citation_repo,
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    # 종목 처리 실패.
    assert summary.failure_count == 1
    # universe citation 만 있고 종목별 citation 은 없음 (fetch-then-save 효과).
    cited = citation_repo.fetch_by_batch(summary.batch_id)
    # universe fetch 1 개만 영구화 — OHLCV / market_cap 둘 다 in-memory 였다가 폐기.
    assert len(cited) == 1
    assert cited[0].identifier.startswith("universe|")


# =============================================================================
# C2 회귀 — dedup no-op 제거 (citation save 가 매번 새 id)
# =============================================================================

def test_citation_save_does_not_call_fetch_by_id_in_loop(
    business_day: date,
) -> None:
    """oracle 리뷰 C2 회귀 — _save_citations 의 fetch_by_id N+1 제거.

    citation_repo.fetch_by_id 는 호출되지 않음 (adapter 가 매 호출 새 uuid4).
    """
    pykrx_mock = _make_pykrx_mock(universe=["005930"])
    # fetch_by_id 호출 추적용 spy.
    citation_repo = FakeCitationRepository()
    original_fetch = citation_repo.fetch_by_id
    fetch_calls: list[Any] = []

    def _spy_fetch(cid: UUID) -> Any:
        fetch_calls.append(cid)
        return original_fetch(cid)

    citation_repo.fetch_by_id = _spy_fetch  # type: ignore[assignment]

    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=citation_repo,
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    assert summary.success_count == 1
    # _save_citations 가 fetch_by_id 를 호출하지 않음.
    assert fetch_calls == []


def test_conflict_detector_excludes_estimated_fields() -> None:
    """estimated_fields 가 비교에서 제외됨 — value (FDR 추정) 차이는 무시."""
    primary = FetchResult[tuple[OHLCVRow, ...]](
        data=(
            OHLCVRow(
                code="005930", trade_date=date(2024, 5, 7),
                open=Decimal("70000"), high=Decimal("70000"),
                low=Decimal("70000"), close=Decimal("70000"),
                volume=1_000_000, value=Decimal("70000000000"),
            ),
        ),
        citations=(),
        estimated_fields=frozenset(),
    )
    verify = FetchResult[tuple[OHLCVRow, ...]](
        data=(
            OHLCVRow(
                code="005930", trade_date=date(2024, 5, 7),
                open=Decimal("70000"), high=Decimal("70000"),
                low=Decimal("70000"), close=Decimal("70000"),
                volume=1_000_000,
                value=Decimal("70500000000"),  # 0.7% 차이 — 무시되어야.
            ),
        ),
        citations=(),
        estimated_fields=frozenset({"value"}),
    )
    detector = ConflictDetector()
    result = detector.compare_ohlcv(primary, verify)
    # value 차이가 있지만 estimated_fields 라 conflict 없음.
    assert len(result.conflicts) == 0
    assert "value" in result.skipped_fields


def test_conflict_detector_detects_close_diff_over_threshold() -> None:
    """close 가 0.5% 차이 — default threshold 0.1% 초과."""
    primary = FetchResult[tuple[OHLCVRow, ...]](
        data=(
            OHLCVRow(
                code="005930", trade_date=date(2024, 5, 7),
                open=Decimal("70000"), high=Decimal("70000"),
                low=Decimal("70000"), close=Decimal("70000"),
                volume=1_000_000, value=Decimal("70000000000"),
            ),
        ),
        citations=(),
        estimated_fields=frozenset(),
    )
    verify = FetchResult[tuple[OHLCVRow, ...]](
        data=(
            OHLCVRow(
                code="005930", trade_date=date(2024, 5, 7),
                open=Decimal("70000"), high=Decimal("70000"),
                low=Decimal("70000"),
                close=Decimal("70350"),  # 0.5% 차이.
                volume=1_000_000, value=Decimal("70350000000"),
            ),
        ),
        citations=(),
        estimated_fields=frozenset({"value"}),
    )
    result = ConflictDetector().compare_ohlcv(primary, verify)
    close_conflicts = [c for c in result.conflicts if c.field == "close"]
    assert len(close_conflicts) == 1
    assert close_conflicts[0].diff_ratio > Decimal("0.001")


def test_conflict_detector_missing_dates() -> None:
    """primary 에만 있는 일자 vs verify 에만 있는 일자."""
    p_day = date(2024, 5, 7)
    v_day = date(2024, 5, 8)
    primary = FetchResult[tuple[OHLCVRow, ...]](
        data=(
            OHLCVRow(
                code="005930", trade_date=p_day,
                open=Decimal("70000"), high=Decimal("70000"),
                low=Decimal("70000"), close=Decimal("70000"),
                volume=1_000_000, value=Decimal("70000000000"),
            ),
        ),
        citations=(),
    )
    verify = FetchResult[tuple[OHLCVRow, ...]](
        data=(
            OHLCVRow(
                code="005930", trade_date=v_day,
                open=Decimal("70000"), high=Decimal("70000"),
                low=Decimal("70000"), close=Decimal("70000"),
                volume=1_000_000, value=Decimal("70000000000"),
            ),
        ),
        citations=(),
    )
    result = ConflictDetector().compare_ohlcv(primary, verify)
    assert result.conflicts == ()  # common date 없음.
    assert result.missing_in_primary == (v_day,)
    assert result.missing_in_verify == (p_day,)
