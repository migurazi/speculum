"""pykrx ETF NAV adapter 실 호출 smoke (ADR-0023 D3-a).

본 test 는 KRX 정보데이터시스템이 도달 가능하고 pykrx 가 설치된 환경에서만
실행. CI 야간 runner 의 외부 가용성 신호 + adapter 회귀 가드.

테스트 대상: 069500 (KODEX 200 ETF) — 거래량 풍부 + KRX 대표 ETF.

휴장일 처리:
    `_recent_window()` 가 today-1 부터 ~10 일 range. 빈 결과 / AdapterError 시
    fail 대신 pytest.skip (test_pykrx_real.py 패턴 일관).
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.adapters.base import AdapterError
from app.adapters.pykrx_adapter import PykrxAdapter

pytestmark = pytest.mark.integration

_WINDOW_DAYS = 10
_ETF_CODE = "069500"  # KODEX 200


def _recent_window() -> tuple[date, date]:
    todate = date.today() - timedelta(days=1)
    fromdate = todate - timedelta(days=_WINDOW_DAYS)
    return fromdate, todate


@pytest.fixture(scope="module")
def adapter() -> PykrxAdapter:
    return PykrxAdapter()


def test_fetch_etf_nav_smoke(
    adapter: PykrxAdapter,
    pykrx_endpoint_available: bool,
) -> None:
    """069500 최근 10 일 ETF NAV — NAV 양수 + market_price 양수 + citation."""
    _ = pykrx_endpoint_available
    fromdate, todate = _recent_window()
    try:
        result = adapter.fetch_etf_nav(
            _ETF_CODE,
            start=fromdate,
            end=todate,
            batch_id=uuid4(),
        )
    except AdapterError as exc:
        pytest.skip(
            f"pykrx returned no ETF NAV in window {fromdate}~{todate}: {exc}"
        )

    assert len(result.data) >= 1
    row = result.data[0]
    assert row.nav > 0, "NAV 는 양수여야 함"
    assert row.market_price > 0, "시장가 는 양수여야 함"
    assert row.code == _ETF_CODE
    assert len(result.citations) >= 1
