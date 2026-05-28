"""FdrAdapter 단위 테스트 — fixture 기반 (ADR-0003 D8).

테스트 매트릭스:
    1. fetch_ohlcv_by_date_range — 정상 path + canonical 변환
    2. fetch_ohlcv — value estimation warning 명시
    3. fetch_stock_master — KOSPI / KOSDAQ 자동 감지
    4. fetch_stock_master — sector / listing_date nullable 처리
    5. fetch_universe — sorted tuple
    6. 입력 검증 — code / inverted range / unsupported market
    7. 빈 DataFrame → AdapterError
    8. ConnectionError → AdapterRetryError
    9. KeyboardInterrupt propagate (C2 회귀)
    10. AdapterError 재 wrap 안 함 (C2 회귀)
    11. pd.NA / NaN → AdapterError (C1 회귀)
    12. lazy import 실패 → health_check False
    13. Citation 7-tuple 채움 (source=FDR / url=None)
"""

from __future__ import annotations

import builtins
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
import pytest

from app.adapters.base import AdapterError, AdapterRetryError, FetchResult
from app.adapters.fdr_adapter import FdrAdapter
from app.models.source_citation import SourceKind

# =============================================================================
# Helpers — FDR DataFrame fixtures
# =============================================================================

def _ohlcv_df(
    *,
    days: list[date],
    closes: list[float],
) -> pd.DataFrame:
    """FDR DataReader 의 OHLCV DataFrame 모사 — DatetimeIndex + 영문 컬럼."""
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": [1_000_000] * len(days),
            "Change": [0.0] * len(days),  # 등락률 (사용 안 함).
        },
        index=pd.to_datetime(days),
    )


def _stock_listing_df(
    *,
    codes: list[str],
    names: list[str],
    market: str = "KOSPI",
    listing_dates: list[Any] | None = None,
    sectors: list[Any] | None = None,
) -> pd.DataFrame:
    """FDR StockListing(market) 의 DataFrame 모사."""
    listing_dates_norm = listing_dates if listing_dates is not None else [
        pd.Timestamp("2000-01-01")
    ] * len(codes)
    sectors_norm = sectors if sectors is not None else ["전기전자"] * len(codes)
    return pd.DataFrame(
        {
            "Code": codes,
            "Name": names,
            "Market": [market] * len(codes),
            "ListingDate": listing_dates_norm,
            "Sector": sectors_norm,
            "Industry": ["반도체"] * len(codes),
        },
    )


def _make_fdr_mock(
    *,
    ohlcv_df: pd.DataFrame | None = None,
    kospi_listing: pd.DataFrame | None = None,
    kosdaq_listing: pd.DataFrame | None = None,
) -> Any:
    """FinanceDataReader 모듈 mock — DataReader / StockListing."""
    mock = MagicMock()
    mock.DataReader = MagicMock(return_value=ohlcv_df)

    def _listing(market: str) -> pd.DataFrame:
        if market == "KOSPI":
            return kospi_listing if kospi_listing is not None else pd.DataFrame()
        if market == "KOSDAQ":
            return kosdaq_listing if kosdaq_listing is not None else pd.DataFrame()
        return pd.DataFrame()

    mock.StockListing = MagicMock(side_effect=_listing)
    return mock


# =============================================================================
# 1. fetch_ohlcv — canonical 변환
# =============================================================================

def test_fetch_ohlcv_returns_canonical_rows() -> None:
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    closes = [70000.0, 71000.0, 72500.0]
    df = _ohlcv_df(days=days, closes=closes)
    adapter = FdrAdapter(fdr_module=_make_fdr_mock(ohlcv_df=df))

    result = adapter.fetch_ohlcv_by_date_range(
        "005930",
        fromdate=date(2024, 1, 2),
        todate=date(2024, 1, 4),
        batch_id=uuid4(),
    )
    assert isinstance(result, FetchResult)
    assert len(result.data) == 3
    assert [r.trade_date for r in result.data] == days
    assert result.data[0].close == Decimal("70000.0")
    assert result.data[0].volume == 1_000_000


# =============================================================================
# 2. value estimation warning — 운영 인식 가능
# =============================================================================

def test_fetch_ohlcv_emits_value_estimation_warning() -> None:
    df = _ohlcv_df(days=[date(2024, 1, 2)], closes=[70000.0])
    adapter = FdrAdapter(fdr_module=_make_fdr_mock(ohlcv_df=df))
    result = adapter.fetch_ohlcv_by_date_range(
        "005930",
        fromdate=date(2024, 1, 2),
        todate=date(2024, 1, 2),
        batch_id=uuid4(),
    )
    assert len(result.warnings) == 1
    assert "estimated as close*volume" in result.warnings[0]
    # value = close × volume.
    assert result.data[0].value == Decimal("70000.0") * Decimal(1_000_000)
    # oracle 리뷰 M1 — estimated_fields metadata 로 ConflictDetector 알림.
    assert result.estimated_fields == frozenset({"value"})


def test_fetch_ohlcv_estimated_fields_default_empty_for_pykrx() -> None:
    """oracle 리뷰 M1 회귀 — pykrx 의 OHLCV 는 estimated_fields 빈 set (기본값).

    T18 ConflictDetector 가 estimated_fields 가 비어 있으면 모든 필드 비교 대상.
    """
    from app.adapters.pykrx_adapter import PykrxAdapter

    # pykrx fixture (한글 컬럼).
    pykrx_df = pd.DataFrame(
        {
            "시가": [70000.0], "고가": [70000.0], "저가": [70000.0],
            "종가": [70000.0], "거래량": [1_000_000], "거래대금": [70_000_000_000],
        },
        index=pd.to_datetime([date(2024, 1, 2)]),
    )
    pykrx_mock = MagicMock()
    pykrx_mock.get_market_ohlcv = MagicMock(return_value=pykrx_df)
    pykrx_adapter = PykrxAdapter(pykrx_module=pykrx_mock)
    pykrx_result = pykrx_adapter.fetch_ohlcv_by_date_range(
        "005930",
        fromdate=date(2024, 1, 2),
        todate=date(2024, 1, 2),
        batch_id=uuid4(),
    )
    assert pykrx_result.estimated_fields == frozenset()


# =============================================================================
# 3. fetch_stock_master — 시장 자동 감지
# =============================================================================

def test_fetch_stock_master_kospi() -> None:
    adapter = FdrAdapter(
        fdr_module=_make_fdr_mock(
            kospi_listing=_stock_listing_df(
                codes=["005930", "000660"],
                names=["삼성전자", "SK하이닉스"],
                market="KOSPI",
            ),
        ),
    )
    result = adapter.fetch_stock_master(
        "005930", as_of=date(2024, 5, 7), batch_id=uuid4(),
    )
    assert result.data.code == "005930"
    assert result.data.name == "삼성전자"
    assert result.data.market == "KOSPI"
    assert result.data.listing_date == date(2000, 1, 1)
    assert result.data.sector_krx == "전기전자"


def test_fetch_stock_master_kosdaq() -> None:
    adapter = FdrAdapter(
        fdr_module=_make_fdr_mock(
            kospi_listing=_stock_listing_df(
                codes=["005930"], names=["삼성전자"],
            ),
            kosdaq_listing=_stock_listing_df(
                codes=["086520"], names=["에코프로"], market="KOSDAQ",
            ),
        ),
    )
    result = adapter.fetch_stock_master(
        "086520", as_of=date(2024, 5, 7), batch_id=uuid4(),
    )
    assert result.data.market == "KOSDAQ"


def test_fetch_stock_master_not_found() -> None:
    adapter = FdrAdapter(
        fdr_module=_make_fdr_mock(
            kospi_listing=_stock_listing_df(
                codes=["005930"], names=["삼성전자"],
            ),
        ),
    )
    with pytest.raises(AdapterError, match="not found in FDR"):
        adapter.fetch_stock_master(
            "999999", as_of=date(2024, 5, 7), batch_id=uuid4(),
        )


# =============================================================================
# 4. nullable listing_date / sector
# =============================================================================

def test_optional_date_falls_back_to_none_on_unparsable_value() -> None:
    """oracle 리뷰 M2 회귀 — `_index_to_date` 가 raise 하는 값도 None fallback.

    FDR 가 ListingDate 컬럼에 정수 0 같은 invalid 값 반환 시 종목 전체 fetch
    실패로 전파되면 안 됨.
    """
    df = _stock_listing_df(
        codes=["005930"],
        names=["삼성전자"],
        listing_dates=[0],  # invalid — int 0 은 _index_to_date 가 raise.
        sectors=["전기전자"],
    )
    adapter = FdrAdapter(fdr_module=_make_fdr_mock(kospi_listing=df))
    # fetch 자체는 성공 + listing_date 만 None.
    result = adapter.fetch_stock_master(
        "005930", as_of=date(2024, 5, 7), batch_id=uuid4(),
    )
    assert result.data.listing_date is None
    assert result.data.name == "삼성전자"


def test_fetch_stock_master_handles_nullable_fields() -> None:
    """FDR StockListing 의 ListingDate=NaT / Sector=NaN 도 None 으로 매핑."""
    df = _stock_listing_df(
        codes=["005930"],
        names=["삼성전자"],
        listing_dates=[pd.NaT],
        sectors=[np.nan],
    )
    adapter = FdrAdapter(fdr_module=_make_fdr_mock(kospi_listing=df))
    result = adapter.fetch_stock_master(
        "005930", as_of=date(2024, 5, 7), batch_id=uuid4(),
    )
    assert result.data.listing_date is None
    assert result.data.sector_krx is None


# =============================================================================
# 5. fetch_universe — sorted
# =============================================================================

def test_fetch_universe_returns_sorted_codes() -> None:
    df = _stock_listing_df(
        codes=["035420", "000660", "005930"],
        names=["NAVER", "SK하이닉스", "삼성전자"],
    )
    adapter = FdrAdapter(fdr_module=_make_fdr_mock(kospi_listing=df))
    result = adapter.fetch_universe(
        as_of=date(2024, 5, 7), market="KOSPI", batch_id=uuid4(),
    )
    assert result.data == ("000660", "005930", "035420")


def test_fetch_universe_unsupported_market() -> None:
    adapter = FdrAdapter(fdr_module=_make_fdr_mock())
    with pytest.raises(AdapterError, match="unsupported market"):
        adapter.fetch_universe(
            as_of=date(2024, 5, 7), market="KONEX", batch_id=uuid4(),
        )


# =============================================================================
# 6. 입력 검증
# =============================================================================

def test_invalid_code_raises() -> None:
    adapter = FdrAdapter(fdr_module=_make_fdr_mock())
    with pytest.raises(AdapterError, match="6-digit numeric"):
        adapter.fetch_ohlcv_by_date_range(
            "abc",
            fromdate=date(2024, 1, 2), todate=date(2024, 1, 3),
            batch_id=uuid4(),
        )


def test_inverted_range_raises() -> None:
    adapter = FdrAdapter(fdr_module=_make_fdr_mock())
    with pytest.raises(AdapterError, match="inverted range"):
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 3), todate=date(2024, 1, 2),
            batch_id=uuid4(),
        )


# =============================================================================
# 7. 빈 DataFrame → AdapterError
# =============================================================================

def test_empty_dataframe_raises_adapter_error() -> None:
    adapter = FdrAdapter(fdr_module=_make_fdr_mock(ohlcv_df=pd.DataFrame()))
    with pytest.raises(AdapterError, match="empty OHLCV"):
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 2), todate=date(2024, 1, 3),
            batch_id=uuid4(),
        )


# =============================================================================
# 8. ConnectionError → AdapterRetryError
# =============================================================================

def test_connection_error_maps_to_retry_error() -> None:
    mock = MagicMock()
    mock.DataReader = MagicMock(side_effect=ConnectionError("yahoo down"))
    adapter = FdrAdapter(fdr_module=mock)
    with pytest.raises(AdapterRetryError, match="network error"):
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 2), todate=date(2024, 1, 3),
            batch_id=uuid4(),
        )


# =============================================================================
# 9. KeyboardInterrupt propagate (C2 회귀)
# =============================================================================

def test_keyboard_interrupt_propagates() -> None:
    mock = MagicMock()
    mock.DataReader = MagicMock(side_effect=KeyboardInterrupt())
    adapter = FdrAdapter(fdr_module=mock)
    with pytest.raises(KeyboardInterrupt):
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 2), todate=date(2024, 1, 3),
            batch_id=uuid4(),
        )


# =============================================================================
# 10. AdapterError re-raise (C2 회귀)
# =============================================================================

def test_existing_adapter_error_not_rewrapped() -> None:
    original = AdapterError("specific reason")
    mock = MagicMock()
    mock.DataReader = MagicMock(side_effect=original)
    adapter = FdrAdapter(fdr_module=mock)
    with pytest.raises(AdapterError) as exc_info:
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 2), todate=date(2024, 1, 3),
            batch_id=uuid4(),
        )
    assert exc_info.value is original


# =============================================================================
# 11. pd.NA / NaN → AdapterError (C1 회귀)
# =============================================================================

def test_nan_in_dataframe_raises_adapter_error() -> None:
    df = pd.DataFrame(
        {
            "Open": [70000.0],
            "High": [70000.0],
            "Low": [70000.0],
            "Close": [np.nan],  # NaN.
            "Volume": [1_000_000],
            "Change": [0.0],
        },
        index=pd.to_datetime([date(2024, 1, 2)]),
    )
    adapter = FdrAdapter(fdr_module=_make_fdr_mock(ohlcv_df=df))
    with pytest.raises(AdapterError, match="NaN/NA"):
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 2), todate=date(2024, 1, 2),
            batch_id=uuid4(),
        )


# =============================================================================
# 12. lazy import 실패 → health_check False
# =============================================================================

def test_lazy_import_failure_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def _block_fdr(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("FinanceDataReader"):
            raise ImportError(f"blocked import of {name}")
        return original_import(name, *args, **kwargs)

    # sys.modules cache 제거 — 이미 imported 면 patch effect 없음.
    monkeypatch.delitem(sys.modules, "FinanceDataReader", raising=False)
    monkeypatch.setattr(builtins, "__import__", _block_fdr)

    adapter = FdrAdapter()
    assert adapter.health_check() is False


# =============================================================================
# 13. Citation 7-tuple 채움
# =============================================================================

def test_citation_seven_tuple_for_ohlcv() -> None:
    df = _ohlcv_df(days=[date(2024, 1, 2)], closes=[70000.0])
    adapter = FdrAdapter(fdr_module=_make_fdr_mock(ohlcv_df=df))
    batch_id = UUID("00000000-0000-0000-0000-0000000000aa")

    result = adapter.fetch_ohlcv_by_date_range(
        "005930",
        fromdate=date(2024, 1, 2), todate=date(2024, 1, 2),
        batch_id=batch_id,
    )
    cit = result.citations[0]
    assert cit.source == SourceKind.FDR
    assert cit.adapter_version == "1.0.0"
    assert cit.batch_id == batch_id
    assert cit.effective_date == date(2024, 1, 2)
    assert "005930" in cit.identifier
    # FDR 은 외부 source 다중 — url=None.
    assert cit.url is None
    # retrieved_at = UTC tz-aware.
    assert cit.retrieved_at.tzinfo is not None
    assert cit.retrieved_at.utcoffset() == datetime(
        2024, 1, 1, tzinfo=UTC,
    ).utcoffset()
