"""T68 NextAuth JWT 인증 골격 — AUTH_SECRET 게이팅 + JWT 검증 통합 테스트.

ADR-0021 D1.1 (2026-06-02 개정). 본 테스트는 `auth.get_current_user` 의 이분
게이팅을 전수 검증한다:

매트릭스:
1. **fallback 회귀 (P0)** — AUTH_SECRET 미설정 시 무토큰 user-scoped 호출이
   기존 SYSTEM_USER_ID 동작(200). (기존 watchlists/notes/screener_sets 테스트
   전체가 동일 경로 — 회귀 0 의 직접 증거.)
2. **JWT 검증** — AUTH_SECRET 설정 시 유효 JWT → 200 실유저. 서명 불일치 /
   만료 / Bearer 없음 → 401.
3. **JIT provision** — 새 google_sub → 신규 user(uuid4). 같은 sub 재호출 → 동일
   user_id.
4. **401 게이트** — AUTH_SECRET 설정 + 무토큰 → user-scoped 401.
5. **IDOR** — user A JWT 로 생성한 자원을 user B JWT 가 접근 시 404.
6. **fact 익명 유지** — AUTH_SECRET 설정해도 /api/screen · /api/runs/reproduce 는
   무토큰 200 (CurrentUserDep 미사용 — 함정 D).
7. **재현성** — user A 가 저장한 run 을 user B 가 reproduce → result_hash byte 동일.

설계 주의:
    - pytest 중에는 config._load_dotenv_files 가 skip → AUTH_SECRET 은 환경에서만
      온다. monkeypatch.setenv 로 명시 설정/미설정 제어.
    - get_auth_secret 은 매 호출 os.environ 재평가 → monkeypatch 가 즉시 반영.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# 테스트용 HS256 secret — NextAuth AUTH_SECRET 와 동일 역할(대칭키).
_SECRET = "test-auth-secret-32bytes-minimum-xx"
_WRONG_SECRET = "totally-different-secret-value-yyyy"

# fact 라우트(screen/runs) 검증용 — DEFAULT_PACK 에 존재하는 유효 factor + as_of.
_AS_OF_STR = "2024-05-07"
_FACTOR_ID = "eps:basic-ttm-consolidated-ifrs"
_SCREEN_BODY = {
    "conditions": [{"factor": _FACTOR_ID, "op": ">", "value": "0"}],
    "selected_factors": [_FACTOR_ID],
}


def _make_jwt(
    *,
    sub: str,
    email: str | None = "user@example.com",
    secret: str = _SECRET,
    exp_offset: int = 3600,
) -> str:
    """mock NextAuth JWT (HS256). exp = now + exp_offset(초). 음수면 만료."""
    payload: dict[str, object] = {
        "sub": sub,
        "exp": int(time.time()) + exp_offset,
    }
    if email is not None:
        payload["email"] = email
    return jwt.encode(payload, secret, algorithm="HS256")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# 1. fallback 회귀 (P0) — AUTH_SECRET 미설정 → 기존 SYSTEM_USER_ID 동작
# =============================================================================

@pytest.fixture
def fallback_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """AUTH_SECRET 미설정 — fallback(SYSTEM_USER_ID) 모드. 무토큰 200."""
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.delenv("SPECULUM_USER_ID", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_fallback_no_token_user_scoped_200(fallback_client: TestClient) -> None:
    """AUTH_SECRET 미설정 + 무토큰 → user-scoped(watchlists) 200 (회귀 0)."""
    res = fallback_client.get("/api/watchlists")
    assert res.status_code == 200


def test_fallback_create_and_list_roundtrip(fallback_client: TestClient) -> None:
    """fallback 모드에서 watchlists CRUD 가 SYSTEM_USER_ID 로 동작."""
    create = fallback_client.post("/api/watchlists", json={"name": "Core"})
    assert create.status_code == 201
    listing = fallback_client.get("/api/watchlists")
    assert listing.json()["total"] == 1


def test_fallback_runs_list_200(fallback_client: TestClient) -> None:
    """fallback 모드 — /api/runs (user-scoped) 무토큰 200."""
    res = fallback_client.get("/api/runs")
    assert res.status_code == 200


# =============================================================================
# AUTH_SECRET 설정 fixture
# =============================================================================

@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """AUTH_SECRET 설정 — 운영 모드. Bearer JWT 필수.

    Fake repository 기본 wiring (FakeUserRepository 가 JIT provision, FakeWatchlist
    가 user-scoped CRUD — FK 없음). 같은 app 인스턴스 내 user_repo 가 in-memory
    유지되어 같은 sub 재호출 시 동일 user_id 해소.
    """
    monkeypatch.setenv("AUTH_SECRET", _SECRET)
    app = create_app()
    with TestClient(app) as c:
        yield c


# =============================================================================
# 2. JWT 검증 — 유효 / 서명불일치 / 만료 / Bearer 없음
# =============================================================================

def test_valid_jwt_returns_200(auth_client: TestClient) -> None:
    """유효 mock JWT → 200 + 실 user (watchlists 생성)."""
    token = _make_jwt(sub="google-123")
    res = auth_client.post(
        "/api/watchlists", json={"name": "내 폴더"}, headers=_bearer(token),
    )
    assert res.status_code == 201


def test_jwt_without_exp_returns_401(auth_client: TestClient) -> None:
    """exp 클레임 없는 서명 토큰 → 401 (require=["exp"], M2.1 conformance 이슈 2).

    exp 없는 토큰은 영구 유효가 되므로 거부. NextAuth 토큰은 항상 exp 를 갖지만
    게이트가 그 동작에만 의존하지 않게 명시 강제. _make_jwt 는 항상 exp 를 넣으므로
    여기서는 exp 없이 직접 서명.
    """
    token = jwt.encode(
        {"sub": "google-no-exp", "email": "x@example.com"},
        _SECRET, algorithm="HS256",
    )
    res = auth_client.post(
        "/api/watchlists", json={"name": "x"}, headers=_bearer(token),
    )
    assert res.status_code == 401


def test_wrong_signature_returns_401(auth_client: TestClient) -> None:
    """서명 불일치(다른 secret 으로 서명) → 401."""
    token = _make_jwt(sub="google-123", secret=_WRONG_SECRET)
    res = auth_client.get("/api/watchlists", headers=_bearer(token))
    assert res.status_code == 401


def test_expired_jwt_returns_401(auth_client: TestClient) -> None:
    """만료(exp 과거) → 401."""
    token = _make_jwt(sub="google-123", exp_offset=-10)
    res = auth_client.get("/api/watchlists", headers=_bearer(token))
    assert res.status_code == 401


def test_no_bearer_returns_401(auth_client: TestClient) -> None:
    """Bearer 헤더 없음 → 401."""
    res = auth_client.get("/api/watchlists")
    assert res.status_code == 401


def test_malformed_authorization_header_returns_401(
    auth_client: TestClient,
) -> None:
    """Bearer 스킴 아님(예: Basic) → 401."""
    res = auth_client.get(
        "/api/watchlists", headers={"Authorization": "Basic abc"},
    )
    assert res.status_code == 401


def test_token_without_sub_returns_401(auth_client: TestClient) -> None:
    """sub 클레임 없는 JWT → 401 (사용자 식별 불가)."""
    payload = {"exp": int(time.time()) + 3600, "email": "x@y.z"}
    token = jwt.encode(payload, _SECRET, algorithm="HS256")
    res = auth_client.get("/api/watchlists", headers=_bearer(token))
    assert res.status_code == 401


# =============================================================================
# 3. JIT provision — 같은 sub = 같은 user_id, 다른 sub = 다른 user
# =============================================================================

def test_jit_provision_same_sub_same_user(auth_client: TestClient) -> None:
    """같은 google_sub 재호출 → 같은 user (자기 폴더가 보임)."""
    token = _make_jwt(sub="google-same")
    # 첫 호출 — JIT provision + 폴더 생성.
    auth_client.post(
        "/api/watchlists", json={"name": "P1"}, headers=_bearer(token),
    )
    # 같은 sub 의 새 토큰(exp 만 다름) — 같은 user 로 해소되어 폴더가 보임.
    token2 = _make_jwt(sub="google-same", exp_offset=7200)
    listing = auth_client.get("/api/watchlists", headers=_bearer(token2))
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


def test_jit_provision_different_sub_isolated(auth_client: TestClient) -> None:
    """다른 google_sub → 다른 user (서로의 폴더 미가시)."""
    token_a = _make_jwt(sub="google-aaa")
    token_b = _make_jwt(sub="google-bbb")
    auth_client.post(
        "/api/watchlists", json={"name": "A"}, headers=_bearer(token_a),
    )
    # user B 의 list — 빈(자기 자원 없음).
    listing_b = auth_client.get("/api/watchlists", headers=_bearer(token_b))
    assert listing_b.json()["total"] == 0


# =============================================================================
# 4 & 5. IDOR — user A 자원을 user B 가 접근 시 404
# =============================================================================

def test_idor_user_b_cannot_access_user_a_resource(
    auth_client: TestClient,
) -> None:
    """user A JWT 로 만든 폴더를 user B JWT 가 수정/삭제 시 404."""
    token_a = _make_jwt(sub="idor-a")
    token_b = _make_jwt(sub="idor-b")
    created = auth_client.post(
        "/api/watchlists", json={"name": "A의 폴더"}, headers=_bearer(token_a),
    )
    fid = created.json()["id"]
    # user B 가 A 의 폴더 수정 → 404.
    res_put = auth_client.put(
        f"/api/watchlists/{fid}",
        json={"name": "탈취"},
        headers=_bearer(token_b),
    )
    assert res_put.status_code == 404
    res_del = auth_client.delete(
        f"/api/watchlists/{fid}", headers=_bearer(token_b),
    )
    assert res_del.status_code == 404


# =============================================================================
# 6. fact 익명 유지 — AUTH_SECRET 설정해도 screen / reproduce 무토큰 200 (함정 D)
# =============================================================================

def test_screen_anonymous_even_with_auth_secret(
    auth_client: TestClient,
) -> None:
    """AUTH_SECRET 설정 + 무토큰 → /api/screen 200 (CurrentUserDep 미사용).

    as_of 는 query param. 인증 미적용이라 무토큰에도 401 아님(함정 D).
    """
    res = auth_client.post(
        f"/api/screen?as_of={_AS_OF_STR}", json=_SCREEN_BODY,
    )
    assert res.status_code == 200


def test_reproduce_anonymous_even_with_auth_secret(
    auth_client: TestClient,
) -> None:
    """AUTH_SECRET 설정 + 무토큰 → /api/runs/reproduce 200 (CurrentUserDep 미사용).

    먼저 user A 가 run 저장 → export → 그 export JSON 을 무토큰으로 reproduce.
    reproduce 가 인증을 요구하지 않음을 확인(함정 D).
    """
    token_a = _make_jwt(sub="anon-repro-a")
    save = auth_client.post(
        f"/api/runs?as_of={_AS_OF_STR}",
        json=_SCREEN_BODY,
        headers=_bearer(token_a),
    )
    assert save.status_code == 201
    run_id = save.json()["id"]
    export = auth_client.get(
        f"/api/runs/{run_id}/export", headers=_bearer(token_a),
    )
    assert export.status_code == 200
    # 무토큰 reproduce — 인증 미적용.
    res = auth_client.post("/api/runs/reproduce", json=export.json())
    assert res.status_code == 200
    assert res.status_code != 401


# =============================================================================
# 7. 재현성 불변식 — user A 의 run 을 user B 가 reproduce → result_hash byte 동일
# =============================================================================

def test_reproduce_result_hash_user_independent(
    auth_client: TestClient,
) -> None:
    """user A 가 저장한 run 을 user B(다른 JWT)가 reproduce → result_hash 동일.

    user_id 는 result_hash 입력이 아니다(ADR-0021 D5). 두 user 의 reproduce 결과
    result_hash 가 byte 동일해야 한다.
    """
    token_a = _make_jwt(sub="repro-a")
    token_b = _make_jwt(sub="repro-b")
    # user A 가 run 저장.
    save = auth_client.post(
        f"/api/runs?as_of={_AS_OF_STR}",
        json=_SCREEN_BODY,
        headers=_bearer(token_a),
    )
    assert save.status_code == 201
    run = save.json()
    run_id = run["id"]
    # user A 의 export JSON.
    export = auth_client.get(
        f"/api/runs/{run_id}/export", headers=_bearer(token_a),
    )
    assert export.status_code == 200
    # user B(다른 JWT)가 같은 export 를 reproduce → result_hash byte 동일.
    repro_b = auth_client.post(
        "/api/runs/reproduce", json=export.json(), headers=_bearer(token_b),
    )
    assert repro_b.status_code == 200
    # result_hash 가 원본과 byte 동일 — user 무관(ADR-0021 D5).
    assert repro_b.json()["result_hash"] == run["result_hash"]


# =============================================================================
# 8. get_optional_user — 정의 동작 (미배선이나 함수 자체 검증)
# =============================================================================

def test_optional_user_fallback_when_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTH_SECRET 미설정 → get_optional_user 가 SYSTEM_USER_ID 반환."""
    from unittest.mock import MagicMock

    from app.api.dependencies.auth import get_optional_user

    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.delenv("SPECULUM_USER_ID", raising=False)
    req = MagicMock()
    req.headers = {}
    ctx = get_optional_user(req, MagicMock())
    assert ctx is not None
    assert ctx.is_system is True


def test_optional_user_none_when_secret_set_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTH_SECRET 설정 + 무토큰 → get_optional_user None (익명 허용)."""
    from unittest.mock import MagicMock

    from app.api.dependencies.auth import get_optional_user

    monkeypatch.setenv("AUTH_SECRET", _SECRET)
    req = MagicMock()
    req.headers = {}
    ctx = get_optional_user(req, MagicMock())
    assert ctx is None


def test_optional_user_real_when_secret_set_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTH_SECRET 설정 + 유효 토큰 → get_optional_user 실유저(is_system False)."""
    from unittest.mock import MagicMock

    from app.api.dependencies.auth import get_optional_user
    from app.repositories.user_repository import FakeUserRepository

    monkeypatch.setenv("AUTH_SECRET", _SECRET)
    token = _make_jwt(sub="opt-real")
    req = MagicMock()
    req.headers = {"Authorization": f"Bearer {token}"}
    repo = FakeUserRepository()
    ctx = get_optional_user(req, repo)
    assert ctx is not None
    assert ctx.is_system is False
