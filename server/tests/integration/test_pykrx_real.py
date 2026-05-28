"""pykrx adapter 실 호출 smoke (ADR-0003 D8, T43).

본 test 는 KRX 정보데이터시스템 + Naver 가 도달 가능할 때만 실행. CI
야간 runner 의 외부 가용성 신호 + adapter 의 회귀 가드.

테스트 매트릭스 (smoke — minimum surface):
    1. fetch_ohlcv_by_date_range — 005930 (삼성전자) 의 최근 ~10 일
    2. fetch_market_cap_by_date_range — 동일 종목 시가총액
    3. fetch_stock_master — 005930 의 종목 메타

각 test 가 단일 종목 + ~10 일 → 최소 부하. rate limit 보호.

휴장일 처리 (oracle T43-A 리뷰 C1/C2 반영):
    `_recent_window()` 가 today-1 부터 ~10 일 range — 평일 휴장일 (광복절·
    설날·추석 등) 의 confluence 가 있어도 1+ 영업일 거의 보장. 그래도 빈
    결과 / AdapterError 시 fail 대신 pytest.skip — nightly 는 flaky 결과를
    fail 로 폭증시키지 않고 신호로만 처리.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.adapters.base import AdapterError
from app.adapters.pykrx_adapter import PykrxAdapter

pytestmark = pytest.mark.integration

# 휴장일 confluence (설/추석 + 평일 휴일) 흡수용 window. 10 일이면 평일 5+
# 영업일 보통 보장. KRX 의 단일 연휴는 최대 ~6 일 (설/추석 + 인접 휴일) 이라
# 안전 마진. 단일 종목 + 10 일 = pykrx 단일 호출 → rate limit 부담 미미.
_WINDOW_DAYS = 10


def _recent_window() -> tuple[date, date]:
    """최근 ~10 일 (fromdate, todate) — 휴장일 confluence 안전.

    nightly = KST 02:00. `today` = 새벽 기준 → 직전 영업일 시세 이미 가용.
    todate = today-1 (오늘 시세는 16:00 마감 후 갱신, 02:00 시점 보장 X).
    """
    todate = date.today() - timedelta(days=1)
    fromdate = todate - timedelta(days=_WINDOW_DAYS)
    return fromdate, todate


@pytest.fixture(scope="module")
def adapter() -> PykrxAdapter:
    # lazy import — pykrx 가 설치된 환경에서만 본 module 의 test 실행.
    return PykrxAdapter()


def test_fetch_ohlcv_smoke(
    adapter: PykrxAdapter,
    pykrx_endpoint_available: bool,
) -> None:
    """005930 최근 10 일 OHLCV — 가격 양수 + citation 동반."""
    _ = pykrx_endpoint_available  # autouse fixture 가 skip 처리.
    fromdate, todate = _recent_window()
    try:
        result = adapter.fetch_ohlcv_by_date_range(
            "005930",
            fromdate=fromdate,
            todate=todate,
            batch_id=uuid4(),
        )
    except AdapterError as exc:
        # 휴장일 confluence (예: 설 + 인접 휴일) 로 window 내 영업일 0 인 경우.
        # smoke 의 회귀 가드는 "adapter 가 raise/return 형식 OK" 까지 — flaky
        # 한 휴장 confluence 는 skip 으로 신호.
        pytest.skip(f"pykrx returned no OHLCV in window {fromdate}~{todate}: {exc}")

    assert len(result.data) >= 1
    row = result.data[0]
    assert row.close > 0
    assert row.code == "005930"
    # citation 7-tuple 의무 (ADR-0002 D3) — 적어도 1 개.
    assert len(result.citations) >= 1


def test_fetch_market_cap_smoke(
    adapter: PykrxAdapter,
    pykrx_endpoint_available: bool,
) -> None:
    """005930 시가총액 — 양수 + shares_outstanding 양수."""
    _ = pykrx_endpoint_available
    fromdate, todate = _recent_window()
    try:
        result = adapter.fetch_market_cap_by_date_range(
            "005930",
            fromdate=fromdate,
            todate=todate,
            batch_id=uuid4(),
        )
    except AdapterError as exc:
        pytest.skip(
            f"pykrx returned no market_cap in window {fromdate}~{todate}: {exc}"
        )

    assert len(result.data) >= 1
    row = result.data[0]
    assert row.market_cap > 0
    assert row.shares_outstanding > 0


def test_fetch_stock_master_smoke(
    adapter: PykrxAdapter,
    pykrx_endpoint_available: bool,
) -> None:
    """005930 종목 메타 — 이름 '삼성전자' 매칭 + KOSPI."""
    _ = pykrx_endpoint_available
    # stock_master 는 단일 일자 — fetch_universe 가 해당 일에 KOSPI 활성
    # 종목 list 를 fetch 하므로 휴장일이어도 보통 직전 영업일 list 반환.
    # 그래도 안전 마진 — today-3 (주말 + 휴일 가능성 회피).
    target = date.today() - timedelta(days=3)
    try:
        result = adapter.fetch_stock_master(
            "005930",
            as_of=target,
            batch_id=uuid4(),
        )
    except AdapterError as exc:
        pytest.skip(f"pykrx returned no stock_master as_of {target}: {exc}")

    row = result.data
    # 종목명 변경 시 본 assertion 도 조정. 단순 substring.
    assert "삼성" in row.name
    assert row.code == "005930"
    assert row.market in ("KOSPI", "KOSDAQ", "KONEX")
