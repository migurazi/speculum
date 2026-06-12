"""POST /api/tax/securities-transaction (M3 #5) — 증권거래세 계산 endpoint.

검증 핵심:
- 계산 정확성(시장구분 × 효력일 × 세율)이 wire 응답에 정확히 반영.
- disclaimer_required 상수 true(ADR-0030 D3 — 프론트 게이트 트리거).
- wire snake_case 계약: tax_amount / applied_rate / effective_date /
  legal_source / disclaimer_required.
- 인증 불요(공개 산식, ADR-0030 D2) — Authorization 헤더 없이 200.
- 잘못된 market / 음수 금액 / 범위 밖 거래일 → 422.
- 양도세 endpoint 부재(ADR-0030 D1) — 그런 route 자체가 없음.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """증권거래세 endpoint 는 외부 repository 의존 없음(공개 산식 — body 만)."""
    app = create_app()
    with TestClient(app) as c:
        yield c


_PATH = "/api/tax/securities-transaction"


# =============================================================================
# 계산 정확성 + wire 계약
# =============================================================================

def test_kospi_2025_calculation(client: TestClient) -> None:
    """코스피 2025 — 1,000,000 × 0.15% = 1,500 원 + wire 계약 검증."""
    resp = client.post(_PATH, json={
        "market": "kospi",
        "trade_amount": "1000000",
        "trade_date": "2025-06-02",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tax_amount"] == "1500"
    assert body["applied_rate"] == "0.0015"
    assert body["effective_date"] == "2025-01-01"
    assert "증권거래세법" in body["legal_source"]
    # ADR-0030 D3 — 상수 true(프론트 디스클레이머 게이트).
    assert body["disclaimer_required"] is True
    # wire 계약 — snake_case 필드만(양도세·개별 상황 필드 부재).
    assert set(body.keys()) == {
        "tax_amount", "applied_rate", "effective_date",
        "legal_source", "disclaimer_required",
    }


def test_kosdaq_2024_past_rate(client: TestClient) -> None:
    """코스닥 2024 거래 — 과거 효력 세율 0.18% 적용."""
    resp = client.post(_PATH, json={
        "market": "kosdaq",
        "trade_amount": "2000000",
        "trade_date": "2024-07-01",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied_rate"] == "0.0018"
    assert body["tax_amount"] == "3600"  # 2,000,000 × 0.0018
    assert body["effective_date"] == "2024-01-01"


def test_konex_rate(client: TestClient) -> None:
    """코넥스 — 0.10%."""
    resp = client.post(_PATH, json={
        "market": "konex",
        "trade_amount": "5000000",
        "trade_date": "2025-06-02",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied_rate"] == "0.0010"
    assert body["tax_amount"] == "5000"


def test_unlisted_rate(client: TestClient) -> None:
    """비상장 — 0.35%."""
    resp = client.post(_PATH, json={
        "market": "unlisted",
        "trade_amount": "1000000",
        "trade_date": "2025-06-02",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied_rate"] == "0.0035"
    assert body["tax_amount"] == "3500"


def test_numeric_trade_amount_coercion(client: TestClient) -> None:
    """trade_amount 가 JSON number 여도 Decimal coercion(정밀 유지)."""
    resp = client.post(_PATH, json={
        "market": "kospi",
        "trade_amount": 1000000,
        "trade_date": "2025-06-02",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["tax_amount"] == "1500"


def test_no_auth_required(client: TestClient) -> None:
    """인증 불요(공개 산식, ADR-0030 D2) — Authorization 헤더 없이 200."""
    resp = client.post(_PATH, json={
        "market": "kospi",
        "trade_amount": "1000000",
        "trade_date": "2025-06-02",
    })
    assert resp.status_code == 200, resp.text


def test_disclaimer_required_always_true(client: TestClient) -> None:
    """disclaimer_required 는 모든 시장구분에서 상수 true(ADR-0030 D3)."""
    for market in ("kospi", "kosdaq", "konex", "unlisted"):
        resp = client.post(_PATH, json={
            "market": market,
            "trade_amount": "1000000",
            "trade_date": "2025-06-02",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["disclaimer_required"] is True


# =============================================================================
# 422 검증
# =============================================================================

def test_invalid_market_422(client: TestClient) -> None:
    """4 시장구분 외 market — 422(Pydantic Literal 거부)."""
    resp = client.post(_PATH, json={
        "market": "nyse",
        "trade_amount": "1000000",
        "trade_date": "2025-06-02",
    })
    assert resp.status_code == 422


def test_negative_trade_amount_422(client: TestClient) -> None:
    """음수 거래금액 — 422(Pydantic ge=0 거부)."""
    resp = client.post(_PATH, json={
        "market": "kospi",
        "trade_amount": "-100",
        "trade_date": "2025-06-02",
    })
    assert resp.status_code == 422


def test_invalid_trade_date_422(client: TestClient) -> None:
    """잘못된 날짜 형식 — 422."""
    resp = client.post(_PATH, json={
        "market": "kospi",
        "trade_amount": "1000000",
        "trade_date": "not-a-date",
    })
    assert resp.status_code == 422


def test_trade_date_before_table_422(client: TestClient) -> None:
    """세율 테이블 범위 밖(과거) 거래일 — service 위반 → 422."""
    resp = client.post(_PATH, json={
        "market": "kospi",
        "trade_amount": "1000000",
        "trade_date": "2020-01-01",
    })
    assert resp.status_code == 422


def test_missing_field_422(client: TestClient) -> None:
    """필수 필드 누락 — 422."""
    resp = client.post(_PATH, json={"market": "kospi"})
    assert resp.status_code == 422


def test_extra_field_forbidden_422(client: TestClient) -> None:
    """extra=forbid — 개별 상황 입력(대주주 등) 시도 시 422(ADR-0030 D2 경계)."""
    resp = client.post(_PATH, json={
        "market": "kospi",
        "trade_amount": "1000000",
        "trade_date": "2025-06-02",
        "is_major_shareholder": True,  # 개별 상황 입력 — 계약에 없음.
    })
    assert resp.status_code == 422


# =============================================================================
# 양도세 endpoint 부재 — ADR-0030 D1
# =============================================================================

def test_no_capital_gains_endpoint(client: TestClient) -> None:
    """양도세 endpoint 미구현(ADR-0030 D1) — 그런 경로 자체가 없음(404/405)."""
    resp = client.post("/api/tax/capital-gains", json={})
    assert resp.status_code in (404, 405)


def test_decimal_precision_via_wire(client: TestClient) -> None:
    """소수 거래금액 — wire 응답이 Decimal 정밀 유지(원 단위 quantize)."""
    resp = client.post(_PATH, json={
        "market": "kospi",
        "trade_amount": "123456.78",
        "trade_date": "2025-06-02",
    })
    assert resp.status_code == 200, resp.text
    # 123456.78 × 0.0015 = 185.18517 → 185
    assert resp.json()["tax_amount"] == "185"
    assert Decimal(resp.json()["applied_rate"]) == Decimal("0.0015")
