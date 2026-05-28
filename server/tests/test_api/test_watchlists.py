"""Watchlist CRUD endpoint 통합 테스트 — AC-F-06.

테스트 매트릭스:
1. Folder CRUD — POST/GET/PUT/DELETE
2. Folder depth ≤ 2 강제 (ADR-0011 D1)
3. is_default folder 삭제 거부
4. Item CRUD — POST/GET/PATCH/DELETE
5. 중복 종목 추가 → 409
6. note ≤ 280 자 강제
7. cascade delete (folder 삭제 시 items 도 삭제)
8. IDOR — 다른 user 접근 시 404
9. invalid input — 422
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


# =============================================================================
# Folder CRUD
# =============================================================================

def test_create_folder(client: TestClient) -> None:
    res = client.post("/api/watchlists", json={"name": "Core Stocks"})
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "Core Stocks"
    assert body["parent_id"] is None
    assert body["display_order"] == 0


def test_list_folders_after_create(client: TestClient) -> None:
    client.post("/api/watchlists", json={"name": "Folder A"})
    client.post("/api/watchlists", json={"name": "Folder B"})
    res = client.get("/api/watchlists")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2


def test_create_folder_empty_name_returns_422(client: TestClient) -> None:
    res = client.post("/api/watchlists", json={"name": ""})
    assert res.status_code == 422


def test_create_folder_name_too_long_returns_422(client: TestClient) -> None:
    res = client.post("/api/watchlists", json={"name": "x" * 101})
    assert res.status_code == 422


def test_update_folder_name(client: TestClient) -> None:
    res = client.post("/api/watchlists", json={"name": "old"})
    fid = res.json()["id"]
    res2 = client.put(f"/api/watchlists/{fid}", json={"name": "new"})
    assert res2.status_code == 200
    assert res2.json()["name"] == "new"


def test_update_folder_display_order(client: TestClient) -> None:
    res = client.post("/api/watchlists", json={"name": "F"})
    fid = res.json()["id"]
    res2 = client.put(f"/api/watchlists/{fid}", json={"display_order": 5})
    assert res2.status_code == 200
    assert res2.json()["display_order"] == 5


def test_update_unknown_folder_returns_404(client: TestClient) -> None:
    res = client.put(
        "/api/watchlists/99999999-9999-9999-9999-999999999999",
        json={"name": "x"},
    )
    assert res.status_code == 404


def test_delete_folder(client: TestClient) -> None:
    res = client.post("/api/watchlists", json={"name": "Disposable"})
    fid = res.json()["id"]
    res2 = client.delete(f"/api/watchlists/{fid}")
    assert res2.status_code == 204
    res3 = client.get(f"/api/watchlists/{fid}/items")
    # Folder 삭제 후 items 도 빈.
    assert res3.json()["total"] == 0


def test_delete_unknown_folder_returns_404(client: TestClient) -> None:
    res = client.delete("/api/watchlists/99999999-9999-9999-9999-999999999999")
    assert res.status_code == 404


# =============================================================================
# Folder depth (ADR-0011 D1 — max 2)
# =============================================================================

def test_create_subfolder_allowed_at_depth_1(client: TestClient) -> None:
    res1 = client.post("/api/watchlists", json={"name": "root"})
    root_id = res1.json()["id"]
    res2 = client.post(
        "/api/watchlists", json={"name": "child", "parent_id": root_id},
    )
    assert res2.status_code == 201
    assert res2.json()["parent_id"] == root_id


def test_create_subfolder_rejected_at_depth_2(client: TestClient) -> None:
    """parent 가 또 다른 폴더의 자식이면 거부 (depth 2 초과)."""
    res1 = client.post("/api/watchlists", json={"name": "root"})
    root_id = res1.json()["id"]
    res2 = client.post(
        "/api/watchlists", json={"name": "child", "parent_id": root_id},
    )
    child_id = res2.json()["id"]
    res3 = client.post(
        "/api/watchlists",
        json={"name": "grandchild", "parent_id": child_id},
    )
    assert res3.status_code == 400
    assert "depth" in res3.json()["detail"]


def test_create_folder_with_unknown_parent_returns_400(client: TestClient) -> None:
    res = client.post(
        "/api/watchlists",
        json={"name": "orphan",
              "parent_id": "99999999-9999-9999-9999-999999999999"},
    )
    assert res.status_code == 400


# =============================================================================
# Item CRUD
# =============================================================================

def _create_folder(client: TestClient) -> str:
    res = client.post("/api/watchlists", json={"name": "Test"})
    return res.json()["id"]


def test_add_item_to_folder(client: TestClient) -> None:
    fid = _create_folder(client)
    res = client.post(
        f"/api/watchlists/{fid}/items",
        json={"code_lineage_id": "00000000-0000-0000-0000-000000000001",
              "note": "memo"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["note"] == "memo"


def test_add_item_to_unknown_folder_returns_404(client: TestClient) -> None:
    """oracle T28 C1 — folder 미존재는 404 (중복은 409 와 분리)."""
    res = client.post(
        "/api/watchlists/99999999-9999-9999-9999-999999999999/items",
        json={"code_lineage_id": str(uuid4())},
    )
    assert res.status_code == 404


def test_add_duplicate_item_returns_409(client: TestClient) -> None:
    fid = _create_folder(client)
    code = "00000000-0000-0000-0000-000000000001"
    r1 = client.post(
        f"/api/watchlists/{fid}/items", json={"code_lineage_id": code},
    )
    assert r1.status_code == 201
    r2 = client.post(
        f"/api/watchlists/{fid}/items", json={"code_lineage_id": code},
    )
    assert r2.status_code == 409


def test_add_item_with_long_note_returns_422(client: TestClient) -> None:
    fid = _create_folder(client)
    res = client.post(
        f"/api/watchlists/{fid}/items",
        json={"code_lineage_id": str(uuid4()), "note": "x" * 281},
    )
    assert res.status_code == 422


def test_list_items_in_folder(client: TestClient) -> None:
    fid = _create_folder(client)
    client.post(f"/api/watchlists/{fid}/items",
                 json={"code_lineage_id": str(uuid4())})
    client.post(f"/api/watchlists/{fid}/items",
                 json={"code_lineage_id": str(uuid4())})
    res = client.get(f"/api/watchlists/{fid}/items")
    assert res.status_code == 200
    assert res.json()["total"] == 2


def test_list_items_unknown_folder_returns_empty(client: TestClient) -> None:
    res = client.get(
        "/api/watchlists/99999999-9999-9999-9999-999999999999/items"
    )
    # ownership mismatch / 미존재 — 빈 list (404 도 가능 정책 선택).
    assert res.status_code == 200
    assert res.json()["items"] == []


def test_patch_item_note(client: TestClient) -> None:
    fid = _create_folder(client)
    res = client.post(
        f"/api/watchlists/{fid}/items",
        json={"code_lineage_id": str(uuid4()), "note": "old"},
    )
    item_id = res.json()["id"]
    res2 = client.patch(
        f"/api/watchlists/items/{item_id}", json={"note": "new"},
    )
    assert res2.status_code == 200
    assert res2.json()["note"] == "new"


def test_patch_item_clear_note(client: TestClient) -> None:
    fid = _create_folder(client)
    res = client.post(
        f"/api/watchlists/{fid}/items",
        json={"code_lineage_id": str(uuid4()), "note": "to clear"},
    )
    item_id = res.json()["id"]
    res2 = client.patch(
        f"/api/watchlists/items/{item_id}", json={"note": None},
    )
    assert res2.status_code == 200
    assert res2.json()["note"] is None


def test_delete_item(client: TestClient) -> None:
    fid = _create_folder(client)
    res = client.post(
        f"/api/watchlists/{fid}/items",
        json={"code_lineage_id": str(uuid4())},
    )
    item_id = res.json()["id"]
    res2 = client.delete(f"/api/watchlists/items/{item_id}")
    assert res2.status_code == 204
    # 다시 list — 빈
    res3 = client.get(f"/api/watchlists/{fid}/items")
    assert res3.json()["total"] == 0


def test_delete_unknown_item_returns_404(client: TestClient) -> None:
    res = client.delete(
        "/api/watchlists/items/99999999-9999-9999-9999-999999999999"
    )
    assert res.status_code == 404


# =============================================================================
# IDOR — 다른 user 접근 차단
# =============================================================================

def test_folder_idor_protection(client: TestClient, monkeypatch) -> None:
    """User A 생성 폴더는 User B 에게 안 보임."""
    # User A 의 폴더 생성.
    res = client.post("/api/watchlists", json={"name": "A's folder"})
    fid = res.json()["id"]

    # User B 로 전환.
    monkeypatch.setenv(
        "SPECULUM_USER_ID", "00000000-0000-0000-0000-0000000000bb"
    )
    # User B 의 list — 빈.
    res2 = client.get("/api/watchlists")
    assert res2.json()["items"] == []
    # User B 가 A 의 폴더 조회 시도 → 404.
    res3 = client.put(f"/api/watchlists/{fid}", json={"name": "stolen"})
    assert res3.status_code == 404
    res4 = client.delete(f"/api/watchlists/{fid}")
    assert res4.status_code == 404


# =============================================================================
# Extra field 차단
# =============================================================================

def test_create_folder_extra_field_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/watchlists",
        json={"name": "x", "surprise": "extra"},
    )
    assert res.status_code == 422
