"""Publisher claim endpoint 통합 테스트 — M6 #3 (ADR-0034 D4).

테스트 매트릭스:
1. claim happy path — POST /api/publishers 200, handle/created_at 응답.
2. 예약 handle — canonical/v1 tier 사칭(community/user/speculum-builtin/speculum)
   → 422.
3. 중복 — 같은 user 두 번째 handle(user 1:1) → 409, 같은 handle 다른 user(인스턴스
   유일)는 repository 단위 테스트가 커버(endpoint 는 단일 user fallback).
4. idempotent — 같은 user + 같은 handle 재요청 → 200(기존 반환).
5. handle pattern 위반 — 대문자/특수문자/하이픈 시작 → 422(Pydantic).
6. user_id body 차단 — extra field → 422(IDOR).

Fake 모드 + AUTH_SECRET 미설정에서 get_current_user 가 SYSTEM_USER_ID 로 해소
(custom_packs/notes 선례) — 단일 user 경로. 인스턴스 유일(타 user) 충돌은
repository 단위 테스트가 검증한다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


# =============================================================================
# claim happy path
# =============================================================================

def test_claim_publisher_happy(client: TestClient) -> None:
    res = client.post("/api/publishers", json={"handle": "alice"})
    assert res.status_code == 200
    body = res.json()
    assert body["handle"] == "alice"
    assert "created_at" in body
    # user_id 미노출(IDOR).
    assert "user_id" not in body


# =============================================================================
# 예약 handle — 422 (canonical/v1 tier 사칭 차단)
# =============================================================================

@pytest.mark.parametrize(
    "handle", ["community", "user", "speculum-builtin", "speculum"],
)
def test_claim_reserved_handle_422(client: TestClient, handle: str) -> None:
    res = client.post("/api/publishers", json={"handle": handle})
    assert res.status_code == 422


# =============================================================================
# 중복 — user 1:1 (409)
# =============================================================================

def test_claim_second_handle_same_user_409(client: TestClient) -> None:
    assert client.post("/api/publishers", json={"handle": "alice"}).status_code == 200
    # 같은 user 가 다른 handle claim — user 1:1 위반 → 409.
    res = client.post("/api/publishers", json={"handle": "alice2"})
    assert res.status_code == 409


# =============================================================================
# idempotent — 같은 handle 재요청
# =============================================================================

def test_claim_idempotent_same_handle(client: TestClient) -> None:
    first = client.post("/api/publishers", json={"handle": "alice"})
    assert first.status_code == 200
    again = client.post("/api/publishers", json={"handle": "alice"})
    assert again.status_code == 200
    assert again.json()["handle"] == "alice"


# =============================================================================
# handle pattern 위반 — 422 (Pydantic Field)
# =============================================================================

@pytest.mark.parametrize(
    "handle",
    [
        "Alice",        # 대문자 불가.
        "-alice",       # 하이픈 시작 불가.
        "alice_pack",   # 언더스코어 불가.
        "alice/pack",   # 슬래시 불가.
        "@alice",       # @ 불가.
        "",             # 빈 문자열.
        "a" * 65,       # 64 초과.
    ],
)
def test_claim_invalid_handle_pattern_422(
    client: TestClient, handle: str,
) -> None:
    res = client.post("/api/publishers", json={"handle": handle})
    assert res.status_code == 422


# =============================================================================
# user_id body 차단 (IDOR — extra=forbid)
# =============================================================================

def test_claim_rejects_user_id_in_body(client: TestClient) -> None:
    res = client.post(
        "/api/publishers",
        json={"handle": "alice", "user_id": "00000000-0000-0000-0000-0000000000bb"},
    )
    assert res.status_code == 422
