"""Portfolio endpoint 통합 테스트 — M3 #4 (ADR-0029).

테스트 매트릭스:
1. 거래 CRUD — POST/GET/DELETE happy path
2. positions 계산 — 보유수량/평단/원가/실현손익 wire schema
3. positions 평가 — 현재가 조회 시 market_value/unrealized_pnl(세전)
4. positions CA 보정(D5) — 거래일 이후 액면분할 → 수량 2배·평단 1/2
5. IDOR(P0) — 타 user 거래 list 빈 + delete 404
6. 세전만(D6) — 응답에 세금 필드 부재
7. 평가 라벨 0(D3) — positions 응답에 손익률/등급/순위/색 필드 부재
8. 입력 검증 — side/quantity/user_id body 차단(422), 음수(422/400)
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakePriceRepository,
)
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    CorporateActionRecord,
    PriceRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository

_USER_B = "00000000-0000-0000-0000-0000000000bb"

# 종목 lineage — current_code 005930. as_of 기준 active.
_SAMSUNG = StockMasterRecord(
    id=UUID("00000000-0000-0000-0000-000000000001"),
    current_code="005930", current_name="삼성전자", market="KOSPI",
    listing_date=date(2000, 1, 1), delisting_date=None, fiscal_month=12,
    code_history=(
        CodeHistoryEntry("005930", date(2000, 1, 1), None, "initial_listing"),
    ),
)
_LINEAGE_ID = str(_SAMSUNG.id)

_AS_OF = "2024-10-02"  # 영업일 (수). 가격 record 와 일치.


def _price(close: str, on: date) -> PriceRecord:
    return PriceRecord(
        id=uuid4(),
        code="005930",
        code_lineage_id=_SAMSUNG.id,
        effective_date=on,
        open_raw=Decimal(close),
        high_raw=Decimal(close),
        low_raw=Decimal(close),
        close_raw=Decimal(close),
        volume=1000,
        trading_value=Decimal("1000000"),
        close_adjusted=Decimal(close),
        citation_id=uuid4(),
        created_at=datetime(2024, 10, 2, tzinfo=UTC),
    )


def _ca_split(effective: date, ratio: int) -> CorporateActionRecord:
    return CorporateActionRecord(
        id=uuid4(),
        code="005930",
        code_lineage_id=_SAMSUNG.id,
        action_type="split",
        announced_date=effective,
        effective_date=effective,
        payment_date=None,
        ratio=Decimal(ratio),
        cash_amount=None,
        details=MappingProxyType({"split_ratio": ratio}),
        citation_id=uuid4(),
        superseded_by=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _make_client(
    *,
    prices: list[PriceRecord] | None = None,
    actions: list[CorporateActionRecord] | None = None,
) -> TestClient:
    app = create_app(
        stocks_repository=FakeStocksMasterRepository(records=[_SAMSUNG]),
        price_repository=FakePriceRepository(records=prices or []),
        corporate_action_repository=FakeCorporateActionRepository(
            records=actions or [],
        ),
    )
    return TestClient(app)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with _make_client() as c:
        yield c


# =============================================================================
# 거래 CRUD
# =============================================================================

def test_create_transaction(client: TestClient) -> None:
    res = client.post(
        "/api/portfolio/transactions",
        json={
            "code_lineage_id": _LINEAGE_ID,
            "side": "buy",
            "quantity": 100,
            "unit_price": "1000",
            "trade_date": "2024-01-01",
            "fee": "150",
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["side"] == "buy"
    assert body["quantity"] == 100
    assert body["unit_price"] == "1000"
    assert body["fee"] == "150"
    assert "id" in body


def test_list_transactions(client: TestClient) -> None:
    client.post("/api/portfolio/transactions", json={
        "code_lineage_id": _LINEAGE_ID, "side": "buy", "quantity": 10,
        "unit_price": "1000", "trade_date": "2024-01-01",
    })
    client.post("/api/portfolio/transactions", json={
        "code_lineage_id": _LINEAGE_ID, "side": "sell", "quantity": 5,
        "unit_price": "1500", "trade_date": "2024-02-01",
    })
    res = client.get("/api/portfolio/transactions")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2


def test_delete_transaction(client: TestClient) -> None:
    created = client.post("/api/portfolio/transactions", json={
        "code_lineage_id": _LINEAGE_ID, "side": "buy", "quantity": 10,
        "unit_price": "1000", "trade_date": "2024-01-01",
    }).json()
    res = client.delete(f"/api/portfolio/transactions/{created['id']}")
    assert res.status_code == 204
    assert client.get("/api/portfolio/transactions").json()["total"] == 0


def test_delete_unknown_returns_404(client: TestClient) -> None:
    res = client.delete(f"/api/portfolio/transactions/{uuid4()}")
    assert res.status_code == 404


# =============================================================================
# positions 계산
# =============================================================================

def test_positions_accounting_facts(client: TestClient) -> None:
    """보유수량/평단/원가/실현손익 wire schema (현재가 없으면 평가 None)."""
    client.post("/api/portfolio/transactions", json={
        "code_lineage_id": _LINEAGE_ID, "side": "buy", "quantity": 100,
        "unit_price": "1000", "trade_date": "2024-01-01",
    })
    client.post("/api/portfolio/transactions", json={
        "code_lineage_id": _LINEAGE_ID, "side": "buy", "quantity": 100,
        "unit_price": "2000", "trade_date": "2024-02-01",
    })
    res = client.get("/api/portfolio/positions", params={"as_of": _AS_OF})
    assert res.status_code == 200
    positions = res.json()["positions"]
    assert len(positions) == 1
    p = positions[0]
    assert p["code_lineage_id"] == _LINEAGE_ID
    assert p["quantity"] == 200
    assert p["avg_cost"] == "1500"
    assert p["total_cost"] == "300000"
    assert p["realized_pnl"] == "0"
    # 현재가 미조회(price repo 빈) → 평가 None.
    assert p["market_value"] is None
    assert p["unrealized_pnl"] is None


def test_positions_with_market_valuation() -> None:
    """현재가 조회 가능 시 market_value/unrealized_pnl 산술(세전)."""
    with _make_client(prices=[_price("1500", date(2024, 10, 2))]) as client:
        client.post("/api/portfolio/transactions", json={
            "code_lineage_id": _LINEAGE_ID, "side": "buy", "quantity": 100,
            "unit_price": "1000", "trade_date": "2024-01-01",
        })
        res = client.get("/api/portfolio/positions", params={"as_of": _AS_OF})
        p = res.json()["positions"][0]
        # 현재가 1500 × 100 = 150000, 평가손익 = 150000 - 100000 = 50000.
        assert p["market_value"] == "150000"
        assert p["unrealized_pnl"] == "50000"


def test_positions_corporate_action_split_adjustment() -> None:
    """D5 — 거래일 이후 액면분할 1:2 → 수량 2배, 평단 1/2(원가 보존)."""
    actions = [_ca_split(date(2024, 6, 1), 2)]
    with _make_client(actions=actions) as client:
        client.post("/api/portfolio/transactions", json={
            "code_lineage_id": _LINEAGE_ID, "side": "buy", "quantity": 100,
            "unit_price": "2000", "trade_date": "2024-01-01",
        })
        res = client.get("/api/portfolio/positions", params={"as_of": _AS_OF})
        p = res.json()["positions"][0]
        assert p["quantity"] == 200       # 100 × 2.
        assert p["avg_cost"] == "1000"    # 2000 ÷ 2.
        assert p["total_cost"] == "200000"  # 원가 보존.


# =============================================================================
# 가드레일 — IDOR / 세전 / 평가 라벨 0
# =============================================================================

def test_idor_isolation(client: TestClient, monkeypatch) -> None:
    """타 user 거래 list 빈 + delete 404 (user 격리 절대)."""
    created = client.post("/api/portfolio/transactions", json={
        "code_lineage_id": _LINEAGE_ID, "side": "buy", "quantity": 100,
        "unit_price": "1000", "trade_date": "2024-01-01",
    }).json()

    monkeypatch.setenv("SPECULUM_USER_ID", _USER_B)
    # B 의 list 는 빈(A 거래 미반환).
    assert client.get("/api/portfolio/transactions").json()["total"] == 0
    # B 의 positions 도 빈.
    assert client.get(
        "/api/portfolio/positions", params={"as_of": _AS_OF},
    ).json()["positions"] == []
    # B 가 A 거래 삭제 시도 → 404.
    assert client.delete(
        f"/api/portfolio/transactions/{created['id']}",
    ).status_code == 404


def test_positions_no_tax_fields() -> None:
    """세전만(D6) — positions 응답에 세금 필드 부재."""
    with _make_client(prices=[_price("1500", date(2024, 10, 2))]) as client:
        client.post("/api/portfolio/transactions", json={
            "code_lineage_id": _LINEAGE_ID, "side": "buy", "quantity": 100,
            "unit_price": "1000", "trade_date": "2024-01-01",
        })
        p = client.get(
            "/api/portfolio/positions", params={"as_of": _AS_OF},
        ).json()["positions"][0]
        forbidden_tax = {"tax", "after_tax_pnl", "withholding", "capital_gains_tax"}
        assert not (set(p.keys()) & forbidden_tax)


def test_positions_no_evaluation_labels() -> None:
    """평가 라벨 0(D3) — 손익률/등급/순위/색 필드 부재(숫자 사실만)."""
    with _make_client(prices=[_price("1500", date(2024, 10, 2))]) as client:
        client.post("/api/portfolio/transactions", json={
            "code_lineage_id": _LINEAGE_ID, "side": "buy", "quantity": 100,
            "unit_price": "1000", "trade_date": "2024-01-01",
        })
        p = client.get(
            "/api/portfolio/positions", params={"as_of": _AS_OF},
        ).json()["positions"][0]
        # 숫자 사실 필드만.
        assert set(p.keys()) == {
            "code_lineage_id", "quantity", "avg_cost", "total_cost",
            "realized_pnl", "market_value", "unrealized_pnl",
        }
        forbidden = {
            "pnl_rate", "return_rate", "grade", "rank", "color",
            "label", "status", "performance",
        }
        assert not (set(p.keys()) & forbidden)


# =============================================================================
# 입력 검증
# =============================================================================

def test_user_id_in_body_rejected(client: TestClient) -> None:
    """user_id 는 body 미노출 — 주면 422 (IDOR 차단)."""
    res = client.post("/api/portfolio/transactions", json={
        "code_lineage_id": _LINEAGE_ID, "side": "buy", "quantity": 10,
        "unit_price": "1000", "trade_date": "2024-01-01", "user_id": _USER_B,
    })
    assert res.status_code == 422


def test_invalid_side_rejected(client: TestClient) -> None:
    """side 는 'buy'|'sell' Literal — 그 외 422."""
    res = client.post("/api/portfolio/transactions", json={
        "code_lineage_id": _LINEAGE_ID, "side": "short", "quantity": 10,
        "unit_price": "1000", "trade_date": "2024-01-01",
    })
    assert res.status_code == 422


def test_non_positive_quantity_rejected(client: TestClient) -> None:
    res = client.post("/api/portfolio/transactions", json={
        "code_lineage_id": _LINEAGE_ID, "side": "buy", "quantity": 0,
        "unit_price": "1000", "trade_date": "2024-01-01",
    })
    assert res.status_code == 422


def test_extra_field_rejected(client: TestClient) -> None:
    res = client.post("/api/portfolio/transactions", json={
        "code_lineage_id": _LINEAGE_ID, "side": "buy", "quantity": 10,
        "unit_price": "1000", "trade_date": "2024-01-01", "surprise": "x",
    })
    assert res.status_code == 422
