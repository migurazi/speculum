"""라우트 불변식 — FastAPI 버전 무관 안전성 회귀 가드."""

from __future__ import annotations

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import create_app


def test_cors_preflight_allows_dev_frontend_origin() -> None:
    """브라우저 dev frontend(localhost:3000) → backend(:8000) 교차 출처 허용.

    CORSMiddleware 부재 시 preflight(OPTIONS)가 405 → 브라우저 "Failed to fetch"
    로 모든 데이터 조회 실패 (TestClient 는 same-process 라 CORS 미경유 → 놓치기
    쉬움). 본 테스트가 CORS 누락 회귀를 잡는다.
    """
    app = create_app()
    with TestClient(app) as client:
        res = client.options(
            "/api/watchlists",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert res.status_code in (200, 204), res.status_code
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_no_204_route_declares_response_model() -> None:
    """204 No Content 라우트는 response_model 을 갖지 않아야 함 (body 불가).

    최신 FastAPI 는 `-> None` 반환 annotation 을 NoneType response_model 로 추론
    → truthy 가 되면 `routing.py` 의 `if self.response_model:` 분기에서
    "Status code 204 must not have a response body" assert 가 발화하여 app
    import 자체가 실패. DELETE 라우트들이 `response_model=None` 명시로 추론을 끈
    상태인지 검증 — 누락 회귀 시 (구버전 FastAPI 에선 통과하더라도 신버전에서
    터지는) 본 테스트가 잡는다.
    """
    app = create_app()
    offenders = [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.status_code == 204
        and route.response_model is not None
    ]
    assert offenders == [], (
        f"204 라우트가 response_model 보유 (response_model=None 명시 필요): {offenders}"
    )
