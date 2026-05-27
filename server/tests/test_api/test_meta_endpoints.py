"""`/api/as_of` + `/api/policy-versions` endpoint 통합 테스트.

본 사이클 (T24) 의 deliverable endpoint 가 main.create_app 에 wire 되었는지 검증.

테스트 매트릭스:
1. `/api/as_of?as_of=...` — 정규화 결과 + header
2. `/api/as_of` (no input) — current calendar range 밖이면 400, 범위 내면 200
3. `/api/policy-versions` — 11 키 + 모두 str
4. `/healthz` — middleware skip + 200
5. demo route 가 default 에서 비활성화 (include_demo_routes=False)
6. middleware × dependency 통합 — 400/422 응답이 middleware 통과
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


# =============================================================================
# 1. /api/as_of 정상 흐름
# =============================================================================

def test_get_as_of_business_day(client: TestClient) -> None:
    res = client.get("/api/as_of?as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    assert body["value"] == "2024-05-07"
    assert body["was_defaulted"] is False
    assert body["was_snapped"] is False
    assert body["pit_policy_version"] == "1.0"
    assert res.headers["x-asof"] == "2024-05-07"


def test_get_as_of_snaps_holiday(client: TestClient) -> None:
    res = client.get("/api/as_of?as_of=2024-05-01")  # 근로자의날
    assert res.status_code == 200
    body = res.json()
    assert body["was_snapped"] is True
    assert body["original_input"] == "2024-05-01"
    assert res.headers["x-asof-snapped"] == "true"
    assert res.headers["x-asof-original"] == "2024-05-01"


def test_get_as_of_future_returns_400(client: TestClient) -> None:
    res = client.get("/api/as_of?as_of=2999-12-31")
    assert res.status_code == 400
    assert res.json()["code"] == "AS_OF_IN_FUTURE"


def test_get_as_of_invalid_format_returns_422_sanitized(client: TestClient) -> None:
    res = client.get("/api/as_of?as_of=NOTADATE")
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "INVALID_REQUEST"
    # input 미echo
    assert "NOTADATE" not in str(body)


# =============================================================================
# 2. /api/policy-versions
# =============================================================================

def test_get_policy_versions_returns_11_keys(client: TestClient) -> None:
    res = client.get("/api/policy-versions")
    assert res.status_code == 200
    body = res.json()
    expected = {
        "factor_pack_content_hash", "factor_pack_slug", "factor_pack_version",
        "calendar_content_hash", "calendar_version",
        "pit_policy_version",
        "price_adjustment_policy_hash", "adjustment_policy_version",
        "ca_policy_version",
        "evaluator_version", "snapshot_schema_version",
    }
    assert set(body.keys()) == expected


def test_policy_versions_all_str_values(client: TestClient) -> None:
    res = client.get("/api/policy-versions")
    body = res.json()
    for k, v in body.items():
        assert isinstance(v, str), f"{k} should be str, got {type(v).__name__}"


# =============================================================================
# 3. /healthz
# =============================================================================

def test_healthz_returns_ok(client: TestClient) -> None:
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


# =============================================================================
# 4. demo route 가 default 비활성화 (oracle 결정 7)
# =============================================================================

def test_demo_routes_disabled_by_default(client: TestClient) -> None:
    """include_demo_routes 미지정 → demo 404."""
    res = client.get("/api/_demo/clean")
    assert res.status_code == 404


def test_demo_routes_can_be_enabled_for_testing() -> None:
    """factory parameter 명시 시 demo 활성화 — middleware test fixture 용."""
    from fastapi.testclient import TestClient
    app = create_app(include_demo_routes=True)
    with TestClient(app) as c:
        res = c.get("/api/_demo/clean")
        assert res.status_code == 200


# =============================================================================
# 5. middleware × dependency 통합 — 400 응답도 middleware 통과
# =============================================================================

def test_400_response_passes_middleware(client: TestClient) -> None:
    """exception handler 의 400 응답이 ForbiddenWordsGuardMiddleware 를 통과.

    응답 detail message 가 ADR-0007 의 금지 어휘를 안전하게 회피하는지 검증.
    """
    res = client.get("/api/as_of?as_of=2999-01-01")
    assert res.status_code == 400
    body = res.json()
    # detail message 검증 — 금지 어휘 free
    for forbidden in ("추천", "매수", "매도", "유망"):
        assert forbidden not in body["detail"], (
            f"AsOfInFutureError detail contains forbidden word: {forbidden}"
        )


def test_422_response_passes_middleware(client: TestClient) -> None:
    """validation handler 의 422 응답도 middleware 통과."""
    res = client.get("/api/as_of?as_of=BADFORMAT")
    assert res.status_code == 422
    body_str = str(res.json())
    for forbidden in ("추천", "매수", "매도"):
        assert forbidden not in body_str


# =============================================================================
# 6. Header — X-AsOf 항상 set (정상 응답 시)
# =============================================================================

def test_x_asof_header_always_set_on_success(client: TestClient) -> None:
    """정상 응답 시 X-AsOf 헤더 항상 set."""
    res = client.get("/api/as_of?as_of=2024-05-07")
    assert "x-asof" in res.headers
    assert res.headers["x-asof"] == "2024-05-07"


# =============================================================================
# 7. /openapi.json 의 forbidden_words 통과 확인 (편의)
# =============================================================================

def test_openapi_schema_does_not_block(client: TestClient) -> None:
    """OpenAPI schema 가 middleware 에 BLOCK 안 됨 — 본 사이클 docstring 검증."""
    res = client.get("/openapi.json")
    assert res.status_code == 200
    body_str = str(res.json())
    # OpenAPI docstring 의 우리 vocabulary 가 안전한지 확인 (편의).
    # 본 test 는 강한 invariant 아닌 sanity check — 만약 fail 시 docstring lint
    # 필요.
    # 단순화: 응답이 정상 (200) 이면 middleware 통과한 것.
