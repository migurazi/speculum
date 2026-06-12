"""ScreenerSet CRUD endpoint 통합 테스트 — AC-F-07.

테스트 매트릭스:
1. CRUD — POST/GET list/GET by id/DELETE
2. user_id IDOR 차단
3. 입력 검증 — name length, conditions/factors min/max
4. 정렬 — updated_at desc
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

_VALID_SET = {
    "name": "Cheap stocks",
    "conditions": [{"factor": "per:ttm", "op": "<", "value": "10"}],
    "selected_factors": ["per:ttm"],
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_create_screener_set(client: TestClient) -> None:
    res = client.post("/api/screener-sets", json=_VALID_SET)
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "Cheap stocks"
    assert len(body["conditions"]) == 1
    assert len(body["selected_factors"]) == 1


def test_list_screener_sets(client: TestClient) -> None:
    client.post("/api/screener-sets", json={**_VALID_SET, "name": "set 1"})
    client.post("/api/screener-sets", json={**_VALID_SET, "name": "set 2"})
    res = client.get("/api/screener-sets")
    assert res.status_code == 200
    assert res.json()["total"] == 2


def test_get_screener_set_by_id(client: TestClient) -> None:
    res = client.post("/api/screener-sets", json=_VALID_SET)
    sid = res.json()["id"]
    res2 = client.get(f"/api/screener-sets/{sid}")
    assert res2.status_code == 200
    assert res2.json()["id"] == sid


def test_get_unknown_screener_set_returns_404(client: TestClient) -> None:
    res = client.get(
        "/api/screener-sets/99999999-9999-9999-9999-999999999999"
    )
    assert res.status_code == 404


def test_delete_screener_set(client: TestClient) -> None:
    res = client.post("/api/screener-sets", json=_VALID_SET)
    sid = res.json()["id"]
    res2 = client.delete(f"/api/screener-sets/{sid}")
    assert res2.status_code == 204
    res3 = client.get(f"/api/screener-sets/{sid}")
    assert res3.status_code == 404


def test_delete_unknown_screener_set_returns_404(client: TestClient) -> None:
    res = client.delete(
        "/api/screener-sets/99999999-9999-9999-9999-999999999999"
    )
    assert res.status_code == 404


# =============================================================================
# 입력 검증
# =============================================================================

def test_create_empty_name_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/screener-sets", json={**_VALID_SET, "name": ""}
    )
    assert res.status_code == 422


def test_create_name_too_long_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/screener-sets", json={**_VALID_SET, "name": "x" * 101}
    )
    assert res.status_code == 422


def test_create_empty_conditions_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/screener-sets", json={**_VALID_SET, "conditions": []}
    )
    assert res.status_code == 422


def test_create_empty_selected_factors_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/screener-sets",
        json={**_VALID_SET, "selected_factors": []},
    )
    assert res.status_code == 422


def test_create_too_many_conditions_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/screener-sets",
        json={**_VALID_SET, "conditions": [_VALID_SET["conditions"][0]] * 33},
    )
    assert res.status_code == 422


def test_create_extra_field_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/screener-sets",
        json={**_VALID_SET, "surprise": "x"},
    )
    assert res.status_code == 422


# =============================================================================
# IDOR
# =============================================================================

def test_screener_set_idor_protection(client: TestClient, monkeypatch) -> None:
    """User A 의 set 은 User B 에게 안 보임."""
    res = client.post("/api/screener-sets", json=_VALID_SET)
    sid = res.json()["id"]

    monkeypatch.setenv(
        "SPECULUM_USER_ID", "00000000-0000-0000-0000-0000000000bb"
    )
    # User B 의 list — 빈.
    res2 = client.get("/api/screener-sets")
    assert res2.json()["items"] == []
    # User B 가 A 의 set 조회 / 삭제 시도 → 404.
    res3 = client.get(f"/api/screener-sets/{sid}")
    assert res3.status_code == 404
    res4 = client.delete(f"/api/screener-sets/{sid}")
    assert res4.status_code == 404


# =============================================================================
# Ordering
# =============================================================================

def test_list_orders_by_updated_at_desc(client: TestClient) -> None:
    """가장 최근에 만든 게 먼저.

    동일 timestamp tie (Windows datetime.now 해상도 ~16ms 로 빠른 연속 생성 시)
    에도 결정적이어야 함 — repository 가 삽입(생성) 순서를 tiebreaker 로 사용하므로
    클럭 해상도와 무관하게 second (나중 생성) 가 먼저.
    """
    _r1 = client.post("/api/screener-sets",
                      json={**_VALID_SET, "name": "first"})
    _r2 = client.post("/api/screener-sets",
                      json={**_VALID_SET, "name": "second"})
    res = client.get("/api/screener-sets")
    items = res.json()["items"]
    # second 가 first 보다 먼저.
    assert items[0]["name"] == "second"
    assert items[1]["name"] == "first"
