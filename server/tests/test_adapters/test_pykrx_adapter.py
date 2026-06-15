"""PykrxAdapter 단위 테스트 — fixture 기반 (ADR-0003 D8).

테스트 매트릭스:
    1. fetch_ohlcv_by_date_range — 정상 path + Citation 7-tuple 채움
    2. fetch_market_cap_by_date_range — 정상 path + shares_treasury None
    3. fetch_stock_master — KOSPI / KOSDAQ 시장 자동 감지
    4. fetch_universe — KOSPI/KOSDAQ universe 정렬
    5. 입력 검증 — 잘못된 code / inverted range / unsupported market
    6. 빈 DataFrame → AdapterError
    7. ConnectionError → AdapterRetryError
    8. lazy import — 미설치 시 AdapterError
    9. ABC 강제 — SOURCE_KIND / ADAPTER_VERSION 미정의 subclass 거부
    10. Decimal 정확성 — float 우회로 binary 부동소수 오차 없음

모든 테스트는 pykrx 미설치 환경에서도 통과 — mock module 주입.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pandas as pd
import pytest

from app.adapters.base import (
    AdapterError,
    AdapterRetryError,
    DataSourceAdapter,
    FetchResult,
)
from app.adapters.pykrx_adapter import PykrxAdapter
from app.models.source_citation import SourceKind

# =============================================================================
# Helpers — fixture DataFrame 생성
# =============================================================================

def _ohlcv_df(
    *,
    days: list[date],
    closes: list[float],
) -> pd.DataFrame:
    """pykrx 의 OHLCV DataFrame 모사 — Timestamp index + 한글 컬럼."""
    return pd.DataFrame(
        {
            "시가": closes,  # 단순화: open=high=low=close (fixture 의미는 close).
            "고가": closes,
            "저가": closes,
            "종가": closes,
            "거래량": [1_000_000] * len(days),
            "거래대금": [c * 1_000_000 for c in closes],
        },
        index=pd.to_datetime(days),
    )


def _market_cap_df(
    *,
    days: list[date],
    market_caps: list[int],
    shares: list[int],
) -> pd.DataFrame:
    """pykrx 의 market_cap_by_date DataFrame 모사."""
    return pd.DataFrame(
        {
            "시가총액": market_caps,
            "거래량": [1_000_000] * len(days),
            "거래대금": [c * 1_000 for c in market_caps],
            "상장주식수": shares,
        },
        index=pd.to_datetime(days),
    )


def _make_pykrx_mock(
    *,
    ohlcv_df: pd.DataFrame | None = None,
    market_cap_df: pd.DataFrame | None = None,
    ticker_name: str = "삼성전자",
    kospi_tickers: list[str] | None = None,
    kosdaq_tickers: list[str] | None = None,
) -> Any:
    """pykrx.stock 모듈 mock — 각 함수가 fixture 반환."""
    mock = MagicMock()
    mock.get_market_ohlcv = MagicMock(return_value=ohlcv_df)
    mock.get_market_cap_by_date = MagicMock(return_value=market_cap_df)
    mock.get_market_ticker_name = MagicMock(return_value=ticker_name)

    def _ticker_list(_date: str, *, market: str) -> list[str]:
        if market == "KOSPI":
            return list(kospi_tickers or [])
        if market == "KOSDAQ":
            return list(kosdaq_tickers or [])
        return []

    mock.get_market_ticker_list = MagicMock(side_effect=_ticker_list)
    return mock


# =============================================================================
# 1. fetch_ohlcv_by_date_range — 정상 path
# =============================================================================

def test_fetch_ohlcv_returns_canonical_rows() -> None:
    """OHLCV DataFrame → tuple[OHLCVRow] canonical 변환 + sort."""
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    closes = [70000.0, 71000.0, 72500.0]
    df = _ohlcv_df(days=days, closes=closes)
    adapter = PykrxAdapter(pykrx_module=_make_pykrx_mock(ohlcv_df=df))

    batch_id = uuid4()
    result = adapter.fetch_ohlcv_by_date_range(
        "005930",
        fromdate=date(2024, 1, 2),
        todate=date(2024, 1, 4),
        batch_id=batch_id,
    )
    assert isinstance(result, FetchResult)
    assert len(result.data) == 3
    # trade_date 오름차순.
    assert [r.trade_date for r in result.data] == days
    # close = Decimal — float 비교가 아니라 Decimal 정확성.
    assert result.data[0].close == Decimal("70000.0")
    assert result.data[2].close == Decimal("72500.0")
    # volume = int.
    assert result.data[0].volume == 1_000_000


def test_fetch_ohlcv_uses_real_trading_value_when_present() -> None:
    """거래대금 컬럼 존재 → 정밀값 사용 (추정 아님), warning/estimated_fields 없음."""
    # 거래대금 = 88_888_888_888 — close × volume (70000 × 1_000_000 = 70_000_000_000)
    # 과 다른 값으로 두어 "정밀 컬럼 사용" 을 추정과 구분 검증.
    df = pd.DataFrame(
        {
            "시가": [70000.0], "고가": [70000.0], "저가": [70000.0],
            "종가": [70000.0], "거래량": [1_000_000],
            "거래대금": [88_888_888_888],
        },
        index=pd.to_datetime([date(2024, 1, 2)]),
    )
    adapter = PykrxAdapter(pykrx_module=_make_pykrx_mock(ohlcv_df=df))
    result = adapter.fetch_ohlcv_by_date_range(
        "005930",
        fromdate=date(2024, 1, 2), todate=date(2024, 1, 2), batch_id=uuid4(),
    )
    # 정밀 거래대금 컬럼 값 사용 (close × volume 추정 70_000_000_000 이 아님).
    assert result.data[0].value == Decimal("88888888888")
    assert result.warnings == ()
    assert result.estimated_fields == frozenset()


def test_fetch_ohlcv_estimates_trading_value_when_column_absent() -> None:
    """거래대금 컬럼 부재 → close × volume 추정 + warning + estimated_fields.

    실 pykrx get_market_ohlcv **시계열** 은 거래대금을 제공하지 않는다 (시가/고가/
    저가/종가/거래량/등락률만 — 라이브 확인). 이때 FdrAdapter 와 동일하게 close ×
    volume 으로 추정하고, ConflictDetector 가 value 비교를 제외하도록
    estimated_fields={"value"} 로 신호한다. 이 가드가 없으면 KeyError('거래대금')
    로 전 종목 적재가 실패한다 (라이브 스모크에서 확인된 실 버그).
    """
    df = _ohlcv_df(days=[date(2024, 1, 2)], closes=[70000.0]).drop(
        columns=["거래대금"],
    )
    adapter = PykrxAdapter(pykrx_module=_make_pykrx_mock(ohlcv_df=df))
    result = adapter.fetch_ohlcv_by_date_range(
        "005930",
        fromdate=date(2024, 1, 2), todate=date(2024, 1, 2), batch_id=uuid4(),
    )
    # close(70000) × volume(1_000_000) = 70_000_000_000.
    assert result.data[0].value == Decimal("70000000000")
    assert result.estimated_fields == frozenset({"value"})
    assert result.warnings
    assert "estimated" in result.warnings[0]


def test_fetch_ohlcv_citation_seven_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    """Citation 의 7 필드가 모두 채워짐 + source=PYKRX + batch_id 전달."""
    df = _ohlcv_df(days=[date(2024, 1, 2)], closes=[70000.0])
    adapter = PykrxAdapter(pykrx_module=_make_pykrx_mock(ohlcv_df=df))

    # retrieved_at 결정성 — datetime.now() patch.
    fixed_now = datetime(2024, 5, 20, 9, 30, tzinfo=UTC)
    monkeypatch.setattr(
        "app.adapters.pykrx_adapter.datetime",
        type("_dt", (), {"now": staticmethod(lambda tz=None: fixed_now)})(),
    )

    batch_id = UUID("00000000-0000-0000-0000-0000000000aa")
    result = adapter.fetch_ohlcv_by_date_range(
        "005930",
        fromdate=date(2024, 1, 2),
        todate=date(2024, 1, 2),
        batch_id=batch_id,
    )
    assert len(result.citations) == 1
    cit = result.citations[0]
    assert cit.source == SourceKind.PYKRX
    assert cit.adapter_version == "1.0.0"
    assert cit.batch_id == batch_id
    assert cit.effective_date == date(2024, 1, 2)
    assert "005930" in cit.identifier
    # Momus M0 review W1 — KRX 종목 deep link 부재. citation url 은 None.
    # FDR adapter 와 일관 — 추후 KRX OPEN API (ADR-0018 답변 후) 합류 시
    # 정확한 deep link.
    assert cit.url is None
    assert cit.retrieved_at.utcoffset() == fixed_now.utcoffset()


# =============================================================================
# 2. fetch_market_cap_by_date_range
# =============================================================================

def test_fetch_market_cap_returns_canonical_rows() -> None:
    df = _market_cap_df(
        days=[date(2024, 1, 2), date(2024, 1, 3)],
        market_caps=[400_000_000_000_000, 410_000_000_000_000],
        shares=[5_969_782_550, 5_969_782_550],
    )
    adapter = PykrxAdapter(pykrx_module=_make_pykrx_mock(market_cap_df=df))

    result = adapter.fetch_market_cap_by_date_range(
        "005930",
        fromdate=date(2024, 1, 2),
        todate=date(2024, 1, 3),
        batch_id=uuid4(),
    )
    assert len(result.data) == 2
    assert result.data[0].market_cap == Decimal("400000000000000")
    assert result.data[0].shares_outstanding == 5_969_782_550
    # pykrx 는 shares_treasury 미제공 → None.
    assert result.data[0].shares_treasury is None


# =============================================================================
# 3. fetch_stock_master — 시장 자동 감지
# =============================================================================

def test_fetch_stock_master_kospi() -> None:
    adapter = PykrxAdapter(
        pykrx_module=_make_pykrx_mock(
            ticker_name="삼성전자",
            kospi_tickers=["005930", "000660"],
            kosdaq_tickers=[],
        ),
    )
    result = adapter.fetch_stock_master(
        "005930", as_of=date(2024, 5, 7), batch_id=uuid4(),
    )
    assert result.data.code == "005930"
    assert result.data.name == "삼성전자"
    assert result.data.market == "KOSPI"
    # listing / delisting / sector 는 pykrx 미제공 → None.
    assert result.data.listing_date is None


def test_fetch_stock_master_kosdaq() -> None:
    adapter = PykrxAdapter(
        pykrx_module=_make_pykrx_mock(
            ticker_name="에코프로",
            kospi_tickers=[],
            kosdaq_tickers=["086520"],
        ),
    )
    result = adapter.fetch_stock_master(
        "086520", as_of=date(2024, 5, 7), batch_id=uuid4(),
    )
    assert result.data.market == "KOSDAQ"


def test_fetch_stock_master_not_in_kospi_or_kosdaq() -> None:
    adapter = PykrxAdapter(
        pykrx_module=_make_pykrx_mock(
            ticker_name="x", kospi_tickers=[], kosdaq_tickers=[],
        ),
    )
    with pytest.raises(AdapterError, match="not found in KOSPI/KOSDAQ"):
        adapter.fetch_stock_master(
            "999999", as_of=date(2024, 5, 7), batch_id=uuid4(),
        )


# =============================================================================
# 4. fetch_universe — 정렬
# =============================================================================

def test_fetch_universe_returns_sorted_tuple() -> None:
    adapter = PykrxAdapter(
        pykrx_module=_make_pykrx_mock(
            kospi_tickers=["000660", "005930", "035420"],
        ),
    )
    result = adapter.fetch_universe(
        as_of=date(2024, 5, 7), market="KOSPI", batch_id=uuid4(),
    )
    assert result.data == ("000660", "005930", "035420")


def test_fetch_universe_unsupported_market() -> None:
    adapter = PykrxAdapter(pykrx_module=_make_pykrx_mock())
    with pytest.raises(AdapterError, match="unsupported market"):
        adapter.fetch_universe(
            as_of=date(2024, 5, 7), market="KONEX", batch_id=uuid4(),
        )


def test_fetch_universe_empty_list_raises_adapter_error() -> None:
    """빈 ticker list → AdapterError. pykrx 의 universe 엔드포인트가 JSON 디코드
    실패를 빈 list 로 흡수해 반환하는 단독 장애를 None 검사만으로는 놓쳐 적재
    0 건 "성공" 으로 silent 통과하던 것을 차단 (라이브 스모크에서 확인)."""
    adapter = PykrxAdapter(
        pykrx_module=_make_pykrx_mock(kospi_tickers=[]),  # 빈 universe.
    )
    with pytest.raises(AdapterError, match="empty ticker_list"):
        adapter.fetch_universe(
            as_of=date(2024, 5, 7), market="KOSPI", batch_id=uuid4(),
        )


# =============================================================================
# 5. 입력 검증
# =============================================================================

def test_invalid_code_raises() -> None:
    adapter = PykrxAdapter(pykrx_module=_make_pykrx_mock())
    with pytest.raises(AdapterError, match="6-digit numeric"):
        adapter.fetch_ohlcv_by_date_range(
            "abc", fromdate=date(2024, 1, 2), todate=date(2024, 1, 3),
            batch_id=uuid4(),
        )
    with pytest.raises(AdapterError, match="6-digit numeric"):
        adapter.fetch_ohlcv_by_date_range(
            "12345", fromdate=date(2024, 1, 2), todate=date(2024, 1, 3),
            batch_id=uuid4(),
        )


def test_inverted_range_raises() -> None:
    adapter = PykrxAdapter(pykrx_module=_make_pykrx_mock())
    with pytest.raises(AdapterError, match="inverted range"):
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 3),
            todate=date(2024, 1, 2),
            batch_id=uuid4(),
        )


# =============================================================================
# 6. 빈 DataFrame → AdapterError
# =============================================================================

def test_empty_dataframe_raises_adapter_error() -> None:
    empty_df = pd.DataFrame()
    adapter = PykrxAdapter(pykrx_module=_make_pykrx_mock(ohlcv_df=empty_df))
    with pytest.raises(AdapterError, match="empty OHLCV"):
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 2),
            todate=date(2024, 1, 3),
            batch_id=uuid4(),
        )


# =============================================================================
# 7. ConnectionError → AdapterRetryError
# =============================================================================

def test_connection_error_maps_to_retry_error() -> None:
    mock = MagicMock()
    mock.get_market_ohlcv = MagicMock(side_effect=ConnectionError("net down"))
    adapter = PykrxAdapter(pykrx_module=mock)
    with pytest.raises(AdapterRetryError, match="network error"):
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 2),
            todate=date(2024, 1, 3),
            batch_id=uuid4(),
        )


def test_generic_error_maps_to_adapter_error() -> None:
    mock = MagicMock()
    mock.get_market_ohlcv = MagicMock(side_effect=ValueError("bad data"))
    adapter = PykrxAdapter(pykrx_module=mock)
    with pytest.raises(AdapterError, match="pykrx call failed"):
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 2),
            todate=date(2024, 1, 3),
            batch_id=uuid4(),
        )


# =============================================================================
# 8. lazy import — 미설치 시 AdapterError (sys.modules 차단)
# =============================================================================

def test_lazy_import_failure_raises_adapter_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pykrx 모듈 import 실패 → AdapterError. 운영에서 pip install 누락 시 fail-fast."""
    import builtins

    original_import = builtins.__import__

    def _block_pykrx(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("pykrx"):
            raise ImportError(f"blocked import of {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_pykrx)

    # 명시 mock 없이 생성 — health_check 가 lazy import 시도 + 실패.
    adapter = PykrxAdapter()
    assert adapter.health_check() is False


# =============================================================================
# 9. ABC 강제 — SOURCE_KIND / ADAPTER_VERSION 누락 거부
# =============================================================================

def test_subclass_must_define_source_kind() -> None:
    """__init_subclass__ 가 SOURCE_KIND 미정의 subclass 거부."""
    with pytest.raises(TypeError, match="SOURCE_KIND"):
        class _Bad(DataSourceAdapter):  # type: ignore[misc]
            ADAPTER_VERSION = "1.0.0"

            def health_check(self) -> bool:
                return True


def test_subclass_must_define_adapter_version() -> None:
    with pytest.raises(TypeError, match="ADAPTER_VERSION"):
        class _Bad(DataSourceAdapter):  # type: ignore[misc]
            SOURCE_KIND = "TEST"

            def health_check(self) -> bool:
                return True


# =============================================================================
# 10. Decimal 정확성 — float 오차 회피
# =============================================================================

# =============================================================================
# C1 회귀 — pd.isna 와 Decimal.is_finite 방어선
# =============================================================================

def test_pd_na_in_dataframe_raises_adapter_error() -> None:
    """oracle 리뷰 C1 회귀 — pd.NA / NaN 검출이 모든 numpy/pandas type 통과."""
    import numpy as np
    df = pd.DataFrame(
        {
            "시가": [70000.0],
            "고가": [70000.0],
            "저가": [70000.0],
            "종가": [np.nan],  # NaN
            "거래량": [1_000_000],
            "거래대금": [70000.0],
        },
        index=pd.to_datetime([date(2024, 1, 2)]),
    )
    adapter = PykrxAdapter(pykrx_module=_make_pykrx_mock(ohlcv_df=df))
    with pytest.raises(AdapterError, match="NaN/NA"):
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 2),
            todate=date(2024, 1, 2),
            batch_id=uuid4(),
        )


# =============================================================================
# C2 회귀 — BaseException propagate + AdapterError re-raise
# =============================================================================

def test_keyboard_interrupt_propagates() -> None:
    """oracle 리뷰 C2 회귀 — KeyboardInterrupt 가 AdapterError 로 흡수되지 않음."""
    mock = MagicMock()
    mock.get_market_ohlcv = MagicMock(side_effect=KeyboardInterrupt())
    adapter = PykrxAdapter(pykrx_module=mock)
    with pytest.raises(KeyboardInterrupt):
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 2),
            todate=date(2024, 1, 3),
            batch_id=uuid4(),
        )


def test_existing_adapter_error_not_rewrapped() -> None:
    """oracle 리뷰 C2 회귀 — `_call_pykrx` 가 AdapterError 를 재 wrap 하지 않음."""
    original = AdapterError("specific reason")
    mock = MagicMock()
    mock.get_market_ohlcv = MagicMock(side_effect=original)
    adapter = PykrxAdapter(pykrx_module=mock)
    with pytest.raises(AdapterError) as exc_info:
        adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=date(2024, 1, 2),
            todate=date(2024, 1, 3),
            batch_id=uuid4(),
        )
    # 같은 인스턴스 — 재 wrap 시 다른 exception 일 것.
    assert exc_info.value is original


def test_decimal_conversion_avoids_float_drift() -> None:
    """`Decimal(0.1)` 의 binary 오차 (0.1000000000000000055...) 회피.

    pykrx DataFrame 의 float64 → Decimal 변환 시 str() 경유로 정확성.
    """
    df = pd.DataFrame(
        {
            "시가": [0.1, 0.2, 0.3],
            "고가": [0.1, 0.2, 0.3],
            "저가": [0.1, 0.2, 0.3],
            "종가": [0.1, 0.2, 0.3],
            "거래량": [1, 1, 1],
            "거래대금": [0.1, 0.2, 0.3],
        },
        index=pd.to_datetime(
            [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        ),
    )
    adapter = PykrxAdapter(pykrx_module=_make_pykrx_mock(ohlcv_df=df))
    result = adapter.fetch_ohlcv_by_date_range(
        "005930",
        fromdate=date(2024, 1, 2),
        todate=date(2024, 1, 4),
        batch_id=uuid4(),
    )
    # 정확 Decimal — "0.1" round-trip.
    assert result.data[0].close == Decimal("0.1")
    assert result.data[1].close == Decimal("0.2")
    assert result.data[2].close == Decimal("0.3")
