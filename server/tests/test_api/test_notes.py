"""Note CRUD endpoint 통합 테스트 — T76 종목별 사용자 Markdown 메모.

테스트 매트릭스:
1. CRUD happy path — POST/GET/PUT/DELETE
2. body 길이 cap(10000) 초과 → 422 (Pydantic) / repository 422
3. IDOR(P0) — 다른 user 의 메모 list 빈 결과 + PUT/DELETE 404
   (list endpoint 의 user_id predicate 누락 회귀 방지 포함)
4. scope 입력 차단 — body 에 scope 주면 422(extra=forbid)
5. extra field 차단 — 422
6. 404 — 미존재 note PUT/DELETE
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# 테스트 표준 user_id — conftest seed 와 무관하게 Fake 모드라 임의 UUID 가능.
# IDOR 검증용 User B.
_USER_B = "00000000-0000-0000-0000-0000000000bb"

_CODE_A = "11111111-1111-1111-1111-111111111111"
_CODE_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


# =============================================================================
# CRUD happy path
# =============================================================================

def test_create_note(client: TestClient) -> None:
    note_body = "# 삼성전자 메모\n분기 실적 확인 필요"
    res = client.post(
        "/api/notes",
        json={"code_lineage_id": _CODE_A, "body": note_body},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["code_lineage_id"] == _CODE_A
    assert body["body"] == note_body
    # scope 는 서버가 USER_PRIVATE 고정.
    assert body["scope"] == "user-private"
    assert "id" in body
    assert body["created_at"] == body["updated_at"]


def test_list_notes_for_code(client: TestClient) -> None:
    client.post("/api/notes", json={"code_lineage_id": _CODE_A, "body": "메모1"})
    client.post("/api/notes", json={"code_lineage_id": _CODE_A, "body": "메모2"})
    # 다른 종목 메모는 _CODE_A list 에 안 섞임.
    client.post("/api/notes", json={"code_lineage_id": _CODE_B, "body": "다른종목"})

    res = client.get("/api/notes", params={"code_lineage_id": _CODE_A})
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    bodies = {n["body"] for n in body["items"]}
    assert bodies == {"메모1", "메모2"}


def test_update_note(client: TestClient) -> None:
    created = client.post(
        "/api/notes", json={"code_lineage_id": _CODE_A, "body": "원본"}
    ).json()
    note_id = created["id"]

    res = client.put(f"/api/notes/{note_id}", json={"body": "수정본"})
    assert res.status_code == 200
    body = res.json()
    assert body["body"] == "수정본"
    assert body["id"] == note_id
    # mutable — updated_at 갱신, created_at 보존.
    assert body["created_at"] == created["created_at"]


def test_delete_note(client: TestClient) -> None:
    created = client.post(
        "/api/notes", json={"code_lineage_id": _CODE_A, "body": "지울 메모"}
    ).json()
    note_id = created["id"]

    res = client.delete(f"/api/notes/{note_id}")
    assert res.status_code == 204

    # 삭제 후 list 빈.
    res2 = client.get("/api/notes", params={"code_lineage_id": _CODE_A})
    assert res2.json()["total"] == 0


# =============================================================================
# body 길이 cap
# =============================================================================

def test_create_note_body_too_long_returns_422(client: TestClient) -> None:
    # Pydantic Field(max_length=10000) — 초과 시 422.
    res = client.post(
        "/api/notes",
        json={"code_lineage_id": _CODE_A, "body": "x" * 10001},
    )
    assert res.status_code == 422


def test_create_note_empty_body_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/notes", json={"code_lineage_id": _CODE_A, "body": ""}
    )
    assert res.status_code == 422


# =============================================================================
# scope / extra field 차단
# =============================================================================

def test_create_note_scope_input_rejected(client: TestClient) -> None:
    """scope 는 입력 안 받음 — body 에 주면 422 (extra=forbid)."""
    res = client.post(
        "/api/notes",
        json={
            "code_lineage_id": _CODE_A,
            "body": "메모",
            "scope": "user-shared",
        },
    )
    assert res.status_code == 422


def test_create_note_user_id_input_rejected(client: TestClient) -> None:
    """user_id 는 body 에 미노출 — 주면 422 (IDOR 차단)."""
    res = client.post(
        "/api/notes",
        json={
            "code_lineage_id": _CODE_A,
            "body": "메모",
            "user_id": _USER_B,
        },
    )
    assert res.status_code == 422


def test_create_note_extra_field_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/notes",
        json={"code_lineage_id": _CODE_A, "body": "메모", "surprise": "x"},
    )
    assert res.status_code == 422


# =============================================================================
# 404 — 미존재
# =============================================================================

def test_update_unknown_note_returns_404(client: TestClient) -> None:
    res = client.put(f"/api/notes/{uuid4()}", json={"body": "x"})
    assert res.status_code == 404


def test_delete_unknown_note_returns_404(client: TestClient) -> None:
    res = client.delete(f"/api/notes/{uuid4()}")
    assert res.status_code == 404


# =============================================================================
# IDOR(P0) — 다른 user 접근 차단 (list endpoint 포함)
# =============================================================================

def test_note_idor_protection(client: TestClient, monkeypatch) -> None:
    """User A 의 메모는 User B 에게 안 보이고, B 가 수정/삭제 시도 시 404.

    **list endpoint 커버 필수** — list_for_code 의 user_id predicate 누락 시
    전 사용자 메모 누출(가장 위험한 회귀). 본 테스트가 그 게이트.
    """
    # User A (default SYSTEM_USER_ID) 의 메모 생성.
    created = client.post(
        "/api/notes", json={"code_lineage_id": _CODE_A, "body": "A's secret"}
    ).json()
    note_id = created["id"]

    # User B 로 전환.
    monkeypatch.setenv("SPECULUM_USER_ID", _USER_B)

    # B 의 같은 종목 list — 빈 (user_id predicate 가 A 메모 차단).
    res_list = client.get("/api/notes", params={"code_lineage_id": _CODE_A})
    assert res_list.status_code == 200
    assert res_list.json()["items"] == []
    assert res_list.json()["total"] == 0

    # B 가 A 의 메모 수정 시도 → 404 (owner mismatch).
    res_put = client.put(f"/api/notes/{note_id}", json={"body": "stolen"})
    assert res_put.status_code == 404

    # B 가 A 의 메모 삭제 시도 → 404.
    res_del = client.delete(f"/api/notes/{note_id}")
    assert res_del.status_code == 404


def test_note_idor_list_does_not_leak_other_user(
    client: TestClient, monkeypatch,
) -> None:
    """A·B 각각 같은 종목에 메모 — 각자 list 는 자기 것만.

    user_id predicate 누락 회귀의 직접 게이트 (code_lineage_id 만 필터하면
    두 user 메모가 섞여 누출).
    """
    # User A 메모.
    client.post("/api/notes", json={"code_lineage_id": _CODE_A, "body": "A note"})

    # User B 로 전환 후 같은 종목에 메모.
    monkeypatch.setenv("SPECULUM_USER_ID", _USER_B)
    client.post("/api/notes", json={"code_lineage_id": _CODE_A, "body": "B note"})

    # B 의 list — B 것만 (A note 누출 X).
    res_b = client.get("/api/notes", params={"code_lineage_id": _CODE_A})
    assert res_b.json()["total"] == 1
    assert res_b.json()["items"][0]["body"] == "B note"

    # A 로 복귀 — A 것만.
    monkeypatch.delenv("SPECULUM_USER_ID", raising=False)
    res_a = client.get("/api/notes", params={"code_lineage_id": _CODE_A})
    assert res_a.json()["total"] == 1
    assert res_a.json()["items"][0]["body"] == "A note"
