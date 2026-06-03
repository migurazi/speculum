"""POST /api/backtest route 통합 테스트 — pack resolve·freeze·디스클로저 (ADR-0027).

검증:
- 빌트인 pack 으로 백테스트 실행 → 200 + freeze(result_hash) + 사실 통계 wire schema.
- disclaimer_required=true 고정 (D4 디스클레이머 게이트).
- survivorship 디스클로저 — survivorship_complete / missing_price_ratio 노출 (D3).
- 거래비용 가정 노출 + override (D2).
- 미존재 custom pack → 404. 기간 역전(end<start) → 422.
- 동일 입력 두 번 → result_hash byte 동일 (재현 §2.10).
- 평가 라벨/등급 0 — 응답에 판단 어휘 없음 (D6).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.fakes import FakeMarketCapRepository, FakePriceRepository
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    MarketCapRecord,
    PriceRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository

_CODE = "005930"
_BUILTIN_FACTOR = "market-cap:krx-official"

# 분기말 영업일 가격 — 단조 증가 (양의 수익률).
_Q_DATES = [
    date(2022, 3, 31),
    date(2022, 6, 30),
    date(2022, 9, 30),
    date(2022, 12, 31),
]
_PRICES = [Decimal(100), Decimal(110), Decimal(121), Decimal("133.1")]


def _stock(code: str, *, security_type: str = "common") -> StockMasterRecord:
    return StockMasterRecord(
        id=UUID(int=int(code)),
        current_code=code,
        current_name=f"종목{code}",
        market="KOSPI",
        listing_date=date(2018, 1, 1),
        delisting_date=None,
        fiscal_month=12,
        code_history=(
            CodeHistoryEntry(code, date(2018, 1, 1), None, "initial_listing"),
        ),
        security_type=security_type,
    )


def _records() -> tuple[list[PriceRecord], list[MarketCapRecord]]:
    prices: list[PriceRecord] = []
    mcaps: list[MarketCapRecord] = []
    for d, close in zip(_Q_DATES, _PRICES, strict=True):
        prices.append(PriceRecord(
            id=uuid4(), code=_CODE, code_lineage_id=UUID(int=int(_CODE)),
            effective_date=d, open_raw=close, high_raw=close, low_raw=close,
            close_raw=close, volume=1000, trading_value=close * Decimal(1000),
            close_adjusted=close, citation_id=uuid4(),
            created_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
        ))
        mcaps.append(MarketCapRecord(
            id=uuid4(), code=_CODE, code_lineage_id=UUID(int=int(_CODE)),
            effective_date=d, market_cap=close * Decimal(1_000_000),
            shares_outstanding=1_000_000, shares_treasury=None,
            citation_id=uuid4(),
            created_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
        ))
    return prices, mcaps


@pytest.fixture
def client() -> Iterator[TestClient]:
    prices, mcaps = _records()
    app = create_app(
        stocks_repository=FakeStocksMasterRepository(records=[_stock(_CODE)]),
        price_repository=FakePriceRepository(records=prices),
        market_cap_repository=FakeMarketCapRepository(records=mcaps),
    )
    with TestClient(app) as c:
        yield c


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "pack_slug": "speculum-builtin",
        "pack_version": "1.0.0",
        "conditions": [{"factor": _BUILTIN_FACTOR, "op": ">", "value": "0"}],
        "start": "2022-01-01",
        "end": "2022-12-31",
        "rebalance": "quarterly",
    }
    body.update(overrides)
    return body


# =============================================================================
# 1. 기본 실행 + wire schema
# =============================================================================

def test_backtest_returns_freeze_stats_and_disclaimer(client: TestClient) -> None:
    """빌트인 pack 백테스트 → 200 + freeze + stats + 디스클레이머 게이트."""
    res = client.post("/api/backtest", json=_body())
    assert res.status_code == 200, res.text
    body = res.json()

    # freeze 블록 — 재현 식별자.
    freeze = body["freeze"]
    assert freeze["result_hash"].startswith("sha256:")
    assert freeze["pack_content_hash"].startswith("sha256:")
    assert freeze["rebalance"] == "quarterly"
    assert freeze["start"] == "2022-01-01"
    assert freeze["end"] == "2022-12-31"
    # 거래비용 가정 노출 (D2).
    assert freeze["cost_assumptions"]["tax_bps"] == "15"
    assert freeze["cost_assumptions"]["commission_bps"] == "1.5"

    # stats — 사실 통계 (D6). 가격 단조 증가 → 누적수익률 양수.
    stats = body["stats"]
    assert Decimal(stats["cumulative_return"]) > 0
    assert "mdd" in stats and "volatility" in stats and "turnover" in stats

    # equity curve — 분기말 4 점.
    assert len(body["equity_curve"]) == 4
    assert body["equity_curve"][0]["date"] == "2022-03-31"

    # 디스클레이머 게이트 (D4) + survivorship 디스클로저 (D3).
    assert body["disclaimer_required"] is True
    assert body["survivorship_complete"] is True
    assert body["missing_price_ratio"] == "0"


def test_backtest_no_evaluation_labels(client: TestClient) -> None:
    """응답에 평가 라벨/등급 어휘 0 — 사실 통계만 (D6)."""
    res = client.post("/api/backtest", json=_body())
    text = res.text
    # 등급/평가 어휘가 응답에 없어야 한다 (No Advice §2.2 / D6).
    for word in ["우수", "양호", "추천", "최고", "rating", "grade", "score"]:
        assert word not in text


# =============================================================================
# 2. survivorship 디스클로저 (D3)
# =============================================================================

def test_backtest_survivorship_incomplete_when_price_missing() -> None:
    """universe 에 있으나 가격 부재 종목 → survivorship_complete False, ratio>0."""
    prices, mcaps = _records()
    # B 종목을 universe(stocks)에만 추가 — price/market_cap 미제공 → 가격 누락.
    app = create_app(
        stocks_repository=FakeStocksMasterRepository(
            records=[_stock(_CODE), _stock("000002")],
        ),
        price_repository=FakePriceRepository(records=prices),
        market_cap_repository=FakeMarketCapRepository(records=mcaps),
    )
    with TestClient(app) as c:
        res = c.post("/api/backtest", json=_body())
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["survivorship_complete"] is False
        assert Decimal(body["missing_price_ratio"]) > 0


# =============================================================================
# 3. 거래비용 override (D2)
# =============================================================================

def test_backtest_cost_override_exposed(client: TestClient) -> None:
    """거래비용 override → 결과 freeze 에 그 값 노출 (0 도 숨김 아닌 노출)."""
    res = client.post(
        "/api/backtest",
        json=_body(cost_tax_bps=0, cost_commission_bps=0),
    )
    assert res.status_code == 200, res.text
    cost = res.json()["freeze"]["cost_assumptions"]
    # float 0 입력 → Decimal("0.0") str (값 0 의 명시 노출 — D2). 숨김 아님.
    assert Decimal(cost["tax_bps"]) == 0
    assert Decimal(cost["commission_bps"]) == 0


# =============================================================================
# 4. pack resolve 실패 + 입력 검증
# =============================================================================

def test_backtest_unknown_custom_pack_404(client: TestClient) -> None:
    """미존재 custom pack slug → 404."""
    res = client.post(
        "/api/backtest", json=_body(pack_slug="user/nonexistent"),
    )
    assert res.status_code == 404


def test_backtest_end_before_start_422(client: TestClient) -> None:
    """end < start → 422 (기간 검증)."""
    res = client.post(
        "/api/backtest", json=_body(start="2022-12-31", end="2022-01-01"),
    )
    assert res.status_code == 422


# =============================================================================
# 5. 재현 — 동일 입력 동일 result_hash (§2.10)
# =============================================================================

def test_backtest_deterministic_result_hash(client: TestClient) -> None:
    """동일 입력 두 번 → result_hash byte 동일 (재현성 §2.10)."""
    r1 = client.post("/api/backtest", json=_body()).json()
    r2 = client.post("/api/backtest", json=_body()).json()
    assert r1["freeze"]["result_hash"] == r2["freeze"]["result_hash"]
    assert r1["stats"] == r2["stats"]
    assert r1["equity_curve"] == r2["equity_curve"]


def test_backtest_different_cost_different_hash(client: TestClient) -> None:
    """거래비용 가정이 다르면 result_hash 도 다름 (비용이 freeze 입력 — D5)."""
    base = client.post("/api/backtest", json=_body()).json()
    cheap = client.post(
        "/api/backtest", json=_body(cost_tax_bps=0, cost_commission_bps=0),
    ).json()
    assert base["freeze"]["result_hash"] != cheap["freeze"]["result_hash"]
