"""FDR adapter 실 호출 smoke (ADR-0003 D8, T43).

FDR (FinanceDataReader) 는 Yahoo / Naver finance crawler — pykrx 와 다른
경로의 KRX 가격. ADR-0001 D3 의 cross-check 출처.

테스트 매트릭스 (smoke):
    1. fetch_ohlcv_by_date_range — 005930 최근 ~10 일
    2. fetch_universe — KOSPI StockListing 기반 활성 종목 list
    3. StockListing 실 컬럼 계약 (Code/Name/Market) drift 가드

휴장일 처리 (test_pykrx_real 과 동일 패턴): window 10 일 + AdapterError →
pytest.skip. nightly 는 flaky 한 휴장 confluence 를 fail 폭증으로 만들지
않고 신호로만.

drift 가드 동기: FDR / pykrx adapter 가 mock 으로만 검증되어 실 응답 schema
변경 (예: pykrx OHLCV 시계열의 거래대금 컬럼 부재, FDR StockListing 의
ListingDate/Sector 제거) 이 mock 뒤에 숨어 실 적재 시에야 드러난 이력이 있다.
본 smoke 가 adapter 가 의존하는 실 컬럼 계약을 nightly 로 고정한다.
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


def test_fetch_universe_smoke(
    adapter: FdrAdapter,
    fdr_endpoint_available: bool,
) -> None:
    """KOSPI universe — FDR StockListing('Code') 기반. 비어있지 않고 6자리 코드.

    FDR fetch_universe 는 StockListing 의 Code 컬럼에 의존. pykrx 의 universe
    엔드포인트 (get_market_ticker_list) 가 단독 장애일 때 FDR 이 활성 종목 list
    를 제공하는지 nightly 로 신호 (cross-check 출처 가용성).
    """
    _ = fdr_endpoint_available
    try:
        result = adapter.fetch_universe(
            as_of=date.today() - timedelta(days=1),
            market="KOSPI",
            batch_id=uuid4(),
        )
    except AdapterError as exc:
        pytest.skip(f"FDR StockListing unavailable: {exc}")

    # KOSPI 는 상시 800+ 종목 — 빈 list 는 endpoint 장애 신호.
    assert len(result.data) > 100
    # 6자리 zero-padded 숫자 코드 계약 (fetch_universe 가 astype(str) 정렬).
    assert all(c.isdigit() and len(c) == 6 for c in result.data[:20])


def test_stock_listing_column_contract(
    fdr_endpoint_available: bool,
) -> None:
    """FDR StockListing 의 필수 컬럼 계약 (Code/Name/Market) drift 가드.

    adapter (fetch_universe / fetch_stock_master) 가 의존하는 컬럼이 FDR 버전업
    으로 사라지면 실 적재가 깨진다 — 본 테스트가 그 drift 를 nightly 로 포착.

    참고: ListingDate / Sector 는 현 FDR (0.9.202) StockListing 에서 제거됨 →
    adapter 가 row.get + optional 로 graceful None 처리하므로 **의무 계약이
    아니다** (단언하지 않음). FDR 이 추후 재추가/재제거해도 본 가드는 무관.
    """
    _ = fdr_endpoint_available
    import FinanceDataReader as fdr

    from app.adapters.fdr_adapter import _FDR_LISTING_COLUMNS

    try:
        df = fdr.StockListing("KOSPI")
    except Exception as exc:  # noqa: BLE001 — 외부 라이브러리 임의 예외 → skip.
        pytest.skip(f"FDR StockListing unavailable: {exc}")

    cols = set(df.columns)
    # adapter 가 무조건 읽는 필수 컬럼 (graceful 아님).
    for key in ("code", "name", "market"):
        expected = _FDR_LISTING_COLUMNS[key]
        assert expected in cols, (
            f"FDR StockListing 컬럼 계약 위반: {expected!r} 부재 — "
            f"adapter ({key}) 가 깨진다. _FDR_LISTING_COLUMNS 점검 필요."
        )
