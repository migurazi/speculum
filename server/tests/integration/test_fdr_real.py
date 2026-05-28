"""FDR adapter 실 호출 smoke (ADR-0003 D8, T43).

FDR (FinanceDataReader) 는 Yahoo / Naver finance crawler — pykrx 와 다른
경로의 KRX 가격. ADR-0001 D3 의 cross-check 출처.

테스트 매트릭스 (smoke):
    1. fetch_ohlcv_by_date_range — 005930 최근 ~10 일

휴장일 처리 (test_pykrx_real 과 동일 패턴): window 10 일 + AdapterError →
pytest.skip. nightly 는 flaky 한 휴장 confluence 를 fail 폭증으로 만들지
않고 신호로만.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.adapters.base import AdapterError
from app.adapters.fdr_adapter import FdrAdapter

pytestmark = pytest.mark.integration

# 휴장일 confluence 흡수 window — test_pykrx_real 과 일관.
_WINDOW_DAYS = 10


def _recent_window() -> tuple[date, date]:
    """최근 ~10 일 (fromdate, todate)."""
    todate = date.today() - timedelta(days=1)
    fromdate = todate - timedelta(days=_WINDOW_DAYS)
    return fromdate, todate


@pytest.fixture(scope="module")
def adapter() -> FdrAdapter:
    return FdrAdapter()


def test_fetch_ohlcv_smoke(
    adapter: FdrAdapter,
    fdr_endpoint_available: bool,
) -> None:
    """005930 최근 10 일 OHLCV — close 양수 + estimated_fields={'value'}."""
    _ = fdr_endpoint_available
    fromdate, todate = _recent_window()
    try:
        result = adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=fromdate,
            todate=todate,
            batch_id=uuid4(),
        )
    except AdapterError as exc:
        pytest.skip(f"FDR returned no OHLCV in window {fromdate}~{todate}: {exc}")

    assert len(result.data) >= 1
    row = result.data[0]
    assert row.close > 0
    assert row.code == "005930"
    # FDR adapter 는 value 를 close × volume 으로 추정 — estimated_fields
    # 신호 (T18 ConflictDetector 가 false-positive 회피 위해 의존).
    assert "value" in result.estimated_fields
