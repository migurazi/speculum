"""ForbiddenWordsGuardMiddleware 단위 테스트.

테스트 매트릭스:
1. Policy 별 동작 (WARN_ONLY / REDACT / BLOCK)
2. Content-Type 별 처리 (JSON only)
3. Skip 경로 (healthcheck)
4. exclude_paths (종목명 등 EXTERNAL_QUOTE)
5. AuditSink 호출 — 검출 사건 기록
6. 환경변수에서 default policy 결정
7. Edge case (빈 응답, parse 실패, large body)
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Environment
from app.middleware.forbidden_words_guard import (
    AuditEvent,
    AuditSink,
    ForbiddenWordsGuardMiddleware,
    ForbiddenWordsPolicy,
    default_policy_for,
)


# =============================================================================
# Test fixtures — 정책별 app + AuditSink spy
# =============================================================================

class _CapturingAudit:
    """테스트용 AuditSink — emit 된 이벤트를 list 에 보존."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


def _make_app(
    *,
    policy: ForbiddenWordsPolicy,
    exclude_keys: frozenset[str] | None = None,
    audit: AuditSink | None = None,
    max_body_bytes: int | None = None,
) -> FastAPI:
    """테스트용 app — middleware 정책 명시 주입."""
    app = FastAPI()
    kwargs: dict[str, object] = {
        "policy": policy,
        "exclude_keys": exclude_keys,
        "audit_sink": audit,
    }
    if max_body_bytes is not None:
        kwargs["max_body_bytes"] = max_body_bytes
    app.add_middleware(ForbiddenWordsGuardMiddleware, **kwargs)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/clean")
    async def clean() -> dict[str, object]:
        return {"title": "조건에 부합하는 종목", "value": 12.3}

    @app.get("/api/dirty")
    async def dirty() -> dict[str, object]:
        return {"title": "오늘의 추천 종목", "message": "Buy now"}

    @app.get("/api/with_stock_name")
    async def with_stock() -> dict[str, object]:
        return {"title": "Detail", "stock_name": "이베스트투자증권"}

    @app.get("/html")
    async def html_endpoint() -> str:
        from starlette.responses import HTMLResponse
        return HTMLResponse("<h1>오늘의 추천</h1>")

    @app.get("/empty")
    async def empty() -> None:
        from starlette.responses import Response
        return Response(content=b"", status_code=204)

    @app.get("/api/dirty_in_excluded_field")
    async def dirty_excluded() -> dict[str, object]:
        # stock_name 안에 금지 어휘 substring — exclude_keys 설정 시 통과해야 함
        return {"title": "검색", "stock_name": "추천산업주식회사"}

    @app.get("/api/with_cookies")
    async def with_cookies() -> Response:
        """multi-value Set-Cookie 테스트용 — oracle v2 C2."""
        from starlette.responses import JSONResponse
        resp = JSONResponse({"title": "조건에 부합하는 종목"})
        resp.set_cookie("session", "abc123")
        resp.set_cookie("csrf", "xyz789")
        return resp

    @app.get("/api/with_etag_dirty")
    async def with_etag_dirty() -> Response:
        """REDACT 시 ETag 가 제거되는지 — oracle v2 C2."""
        from starlette.responses import JSONResponse
        resp = JSONResponse({"title": "오늘의 추천 종목"})
        resp.headers["ETag"] = '"abc-123"'
        return resp

    @app.get("/api/with_background")
    async def with_background(background_tasks: object = None) -> dict[str, object]:
        """BackgroundTask 보존 테스트 — oracle v2 C1."""
        return {"title": "조건에 부합하는 종목", "ok": True}

    @app.get("/api/large_body")
    async def large_body() -> dict[str, object]:
        """body size cap 초과 — oracle v2 C4."""
        return {"title": "조건에 부합하는 종목", "data": "x" * 2_000_000}

    @app.get("/api/deeply_nested")
    async def deeply_nested() -> Response:
        """깊이 폭발 — oracle v2 C3."""
        from starlette.responses import Response as StarletteResponse
        body = b"[" * 5000 + b"1" + b"]" * 5000
        return StarletteResponse(content=body, media_type="application/json")

    return app


# =============================================================================
# 1. WARN_ONLY — 응답 그대로 + 감사 로그
# =============================================================================

def test_warn_only_passes_clean_response() -> None:
    audit = _CapturingAudit()
    client = TestClient(_make_app(policy=ForbiddenWordsPolicy.WARN_ONLY, audit=audit))
    res = client.get("/api/clean")
    assert res.status_code == 200
    assert res.json()["title"] == "조건에 부합하는 종목"
    assert audit.events == []


def test_warn_only_passes_dirty_response_but_records_audit() -> None:
    """WARN_ONLY — 응답 그대로 통과, 감사 로그만."""
    audit = _CapturingAudit()
    client = TestClient(_make_app(policy=ForbiddenWordsPolicy.WARN_ONLY, audit=audit))
    res = client.get("/api/dirty")
    assert res.status_code == 200
    # 응답 본문은 원본 그대로 (어휘 echo)
    body = res.json()
    assert "추천" in body["title"]
    assert "Buy" in body["message"]
    # 감사 이벤트 기록됨
    assert len(audit.events) == 1
    event = audit.events[0]
    assert event.path == "/api/dirty"
    assert event.method == "GET"
    assert event.policy is ForbiddenWordsPolicy.WARN_ONLY
    assert event.match_count > 0
    assert "Buy" in event.match_words or "추천" in event.match_words


# =============================================================================
# 2. REDACT — 검출 어휘를 별표로 치환
# =============================================================================

def test_redact_replaces_forbidden_words_with_asterisks() -> None:
    audit = _CapturingAudit()
    client = TestClient(_make_app(policy=ForbiddenWordsPolicy.REDACT, audit=audit))
    res = client.get("/api/dirty")
    assert res.status_code == 200
    body = res.json()
    # 어휘 자체가 노출되면 안 됨
    assert "추천" not in body["title"]
    assert "Buy" not in body["message"]
    # 별표로 치환되었는지
    assert "**" in body["title"] or "***" in body["title"]
    # 감사 이벤트
    assert len(audit.events) == 1
    assert audit.events[0].policy is ForbiddenWordsPolicy.REDACT


def test_redact_preserves_clean_response_unchanged() -> None:
    client = TestClient(_make_app(policy=ForbiddenWordsPolicy.REDACT))
    res = client.get("/api/clean")
    body = res.json()
    assert body["title"] == "조건에 부합하는 종목"
    assert body["value"] == 12.3


# =============================================================================
# 3. BLOCK — generic 500 응답
# =============================================================================

def test_block_returns_generic_500_without_echoing_word() -> None:
    """BLOCK — 응답 본문에 검출 어휘가 echo 되지 않아야 함 (oracle B4)."""
    audit = _CapturingAudit()
    client = TestClient(
        _make_app(policy=ForbiddenWordsPolicy.BLOCK, audit=audit),
        raise_server_exceptions=False,
    )
    res = client.get("/api/dirty")
    assert res.status_code == 500
    body = res.json()
    # 어휘 echo 없음
    assert "추천" not in str(body)
    assert "Buy" not in str(body)
    assert body.get("code") == "FORBIDDEN_WORDS_GUARD_BLOCK"
    # 감사 이벤트
    assert len(audit.events) == 1
    assert audit.events[0].policy is ForbiddenWordsPolicy.BLOCK
    assert audit.events[0].status_code == 500


def test_block_passes_clean_response() -> None:
    client = TestClient(_make_app(policy=ForbiddenWordsPolicy.BLOCK))
    res = client.get("/api/clean")
    assert res.status_code == 200


# =============================================================================
# 4. Content-Type — JSON only
# =============================================================================

def test_html_response_not_inspected() -> None:
    """HTML 응답은 middleware 가 검사 안 함 — 통과."""
    audit = _CapturingAudit()
    client = TestClient(_make_app(policy=ForbiddenWordsPolicy.BLOCK, audit=audit))
    res = client.get("/html")
    assert res.status_code == 200
    # HTML 안에 "추천" 이 있어도 BLOCK 안 됨
    assert "추천" in res.text
    assert audit.events == []


def test_empty_response_passes() -> None:
    client = TestClient(_make_app(policy=ForbiddenWordsPolicy.BLOCK))
    res = client.get("/empty")
    assert res.status_code == 204


# =============================================================================
# 5. Skip 경로 — healthcheck
# =============================================================================

def test_healthz_skips_inspection() -> None:
    audit = _CapturingAudit()
    client = TestClient(_make_app(policy=ForbiddenWordsPolicy.BLOCK, audit=audit))
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    assert audit.events == []


# =============================================================================
# 6. exclude_paths — 종목명 등 (oracle C1)
# =============================================================================

def test_exclude_keys_allows_company_name_with_forbidden_substring() -> None:
    """`stock_name` 의 값에 '추천' 같은 substring 이 있어도 검사 제외."""
    audit = _CapturingAudit()
    app = _make_app(
        policy=ForbiddenWordsPolicy.BLOCK,
        exclude_keys=frozenset({"stock_name"}),
        audit=audit,
    )
    client = TestClient(app)
    res = client.get("/api/dirty_in_excluded_field")
    # exclude_keys 가 작동하면 BLOCK 안 됨
    assert res.status_code == 200
    body = res.json()
    assert body["stock_name"] == "추천산업주식회사"
    assert audit.events == []


def test_without_exclude_keys_blocks_company_name() -> None:
    """exclude_keys 없으면 회사명 안의 substring 도 검출됨."""
    audit = _CapturingAudit()
    app = _make_app(policy=ForbiddenWordsPolicy.BLOCK, audit=audit)
    client = TestClient(app, raise_server_exceptions=False)
    res = client.get("/api/dirty_in_excluded_field")
    assert res.status_code == 500
    assert len(audit.events) == 1


# =============================================================================
# oracle v2 C1 — BackgroundTask 보존
# =============================================================================

def test_background_task_is_preserved_through_middleware() -> None:
    """BackgroundTask 가 middleware 의 body 재구성 과정에서 보존되어야 함."""
    from starlette.background import BackgroundTask
    from starlette.responses import JSONResponse

    executed: list[bool] = []

    def _bg_task() -> None:
        executed.append(True)

    app = FastAPI()
    app.add_middleware(
        ForbiddenWordsGuardMiddleware,
        policy=ForbiddenWordsPolicy.WARN_ONLY,
    )

    @app.get("/api/with_bg")
    async def with_bg() -> JSONResponse:
        return JSONResponse(
            {"title": "조건에 부합하는 종목"},
            background=BackgroundTask(_bg_task),
        )

    client = TestClient(app)
    res = client.get("/api/with_bg")
    assert res.status_code == 200
    # BackgroundTask 가 실행되어야 함
    assert executed == [True]


# =============================================================================
# oracle v2 C2 — Multi-value 헤더 (Set-Cookie) 보존
# =============================================================================

def test_multiple_set_cookie_headers_are_preserved() -> None:
    audit = _CapturingAudit()
    client = TestClient(
        _make_app(policy=ForbiddenWordsPolicy.WARN_ONLY, audit=audit)
    )
    res = client.get("/api/with_cookies")
    # 응답에 cookie 2 개 모두 존재해야 함
    cookies_header = res.headers.get_list("set-cookie") if hasattr(res.headers, "get_list") else None
    if cookies_header is None:
        # httpx Headers fallback — list 변환
        cookies_header = [
            v for k, v in res.headers.multi_items() if k.lower() == "set-cookie"
        ]
    assert len(cookies_header) == 2, f"expected 2 Set-Cookie headers, got {cookies_header}"


def test_redact_strips_etag_header() -> None:
    """REDACT 모드는 body 변형으로 ETag 가 무효화되므로 헤더 제거."""
    audit = _CapturingAudit()
    client = TestClient(
        _make_app(policy=ForbiddenWordsPolicy.REDACT, audit=audit)
    )
    res = client.get("/api/with_etag_dirty")
    assert res.status_code == 200
    # ETag 헤더가 제거되었어야 함
    assert "etag" not in {k.lower() for k in res.headers.keys()}


# =============================================================================
# oracle v2 C3 — JSON parse 의 RecursionError 보호
# =============================================================================

def test_deeply_nested_json_does_not_crash_middleware() -> None:
    """깊이 5000 의 nested JSON — json.loads 가 RecursionError 던져도 middleware
    가 swallow + 응답 통과 (oracle v2 C3)."""
    audit = _CapturingAudit()
    client = TestClient(
        _make_app(policy=ForbiddenWordsPolicy.REDACT, audit=audit)
    )
    res = client.get("/api/deeply_nested")
    # middleware 가 crash 하지 않고 응답 통과
    assert res.status_code == 200


# =============================================================================
# oracle v2 C4 — Body size cap
# =============================================================================

def test_large_body_skips_inspection_and_returns_marker() -> None:
    """max_body_bytes 초과 시 검사 skip + marker 본문 + audit."""
    audit = _CapturingAudit()
    client = TestClient(
        _make_app(
            policy=ForbiddenWordsPolicy.REDACT,
            audit=audit,
            max_body_bytes=1024,  # 1 KiB cap
        )
    )
    res = client.get("/api/large_body")
    assert res.status_code == 200
    # marker text 반환
    assert b"too large" in res.content.lower()
    # audit 이벤트에 special marker
    assert len(audit.events) == 1
    assert audit.events[0].match_words == ("__body_too_large__",)


# =============================================================================
# oracle v2 C5 — REDACT 가 NFKC normalize 후 정확 span 치환
# =============================================================================

def test_redact_handles_nfkc_fullwidth_evasion() -> None:
    """fullwidth `Ｂｕｙ` → 정상 검출 + redact."""
    app = FastAPI()
    app.add_middleware(
        ForbiddenWordsGuardMiddleware, policy=ForbiddenWordsPolicy.REDACT,
    )

    @app.get("/api/fullwidth")
    async def fullwidth() -> dict[str, str]:
        return {"title": "Ｂｕｙ now"}

    client = TestClient(app)
    res = client.get("/api/fullwidth")
    body = res.json()
    # 어휘 자체는 응답에 나타나지 않아야 함 (NFKC 후 검사 + 별표)
    assert "Buy" not in body["title"]
    assert "Ｂｕｙ" not in body["title"]
    assert "*" in body["title"]


# =============================================================================
# oracle v2 H1 — AuditSink throw 가 응답을 차단하지 않음
# =============================================================================

class _ThrowingAudit:
    def emit(self, event: AuditEvent) -> None:
        raise RuntimeError("audit sink down")


def test_audit_sink_exception_does_not_block_response() -> None:
    """AuditSink.emit 이 throw 해도 middleware 는 응답을 정상 처리해야 함."""
    client = TestClient(
        _make_app(policy=ForbiddenWordsPolicy.REDACT, audit=_ThrowingAudit())
    )
    res = client.get("/api/dirty")
    # audit 실패하더라도 REDACT 는 정상 적용
    assert res.status_code == 200
    body = res.json()
    assert "추천" not in body["title"]


# =============================================================================
# 7. 감사 이벤트 — 개인정보 분리 (oracle G5 + ADR-0006 D6)
# =============================================================================

def test_audit_event_does_not_include_request_or_response_body() -> None:
    """AuditEvent 에 사용자 입력 / 응답 본문이 그대로 포함되어선 안 됨."""
    audit = _CapturingAudit()
    client = TestClient(_make_app(policy=ForbiddenWordsPolicy.WARN_ONLY, audit=audit))
    client.get("/api/dirty")
    assert len(audit.events) == 1
    event = audit.events[0]
    # AuditEvent 의 필드: path, method, policy, match_words (canonical), match_count, status_code
    # 응답 본문 자체는 없음
    assert "오늘의" not in str(event)
    assert "message" not in event.match_words


# =============================================================================
# 8. default_policy_for — 환경별 매핑
# =============================================================================

@pytest.mark.parametrize(
    "env,expected",
    [
        # oracle v2 H3 — DEV / STAGING default 가 REDACT 로 격상.
        (Environment.DEV, ForbiddenWordsPolicy.REDACT),
        (Environment.STAGING, ForbiddenWordsPolicy.REDACT),
        (Environment.PROD, ForbiddenWordsPolicy.BLOCK),
    ],
)
def test_default_policy_for_environment(
    env: Environment, expected: ForbiddenWordsPolicy
) -> None:
    # 환경변수 override 가 영향을 안 주도록 격리.
    with _env("SPECULUM_FORBIDDEN_POLICY", None):
        assert default_policy_for(env) is expected


def test_warn_only_requires_explicit_opt_in_env_var() -> None:
    """WARN_ONLY 는 명시적 환경변수로만 진입 가능 (oracle v2 H3)."""
    with _env("SPECULUM_FORBIDDEN_POLICY", "warn-only"):
        for env in (Environment.DEV, Environment.STAGING, Environment.PROD):
            assert default_policy_for(env) is ForbiddenWordsPolicy.WARN_ONLY


def test_unknown_policy_env_value_falls_back_to_env_default() -> None:
    with _env("SPECULUM_FORBIDDEN_POLICY", "qaz_unknown"):
        assert default_policy_for(Environment.PROD) is ForbiddenWordsPolicy.BLOCK
        assert default_policy_for(Environment.DEV) is ForbiddenWordsPolicy.REDACT


# =============================================================================
# 9. 환경변수 통합
# =============================================================================

@contextmanager
def _env(name: str, value: str | None) -> Iterator[None]:
    """환경변수 임시 설정 (테스트 후 복원)."""
    import os

    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


def test_environment_variable_resolves_to_dev_by_default() -> None:
    from app.core.config import get_environment
    with _env("SPECULUM_ENV", None):
        assert get_environment() is Environment.DEV


def test_environment_variable_unknown_value_falls_back_to_dev() -> None:
    from app.core.config import get_environment
    with _env("SPECULUM_ENV", "qaz_unknown"):
        assert get_environment() is Environment.DEV


def test_environment_variable_prod() -> None:
    from app.core.config import get_environment
    with _env("SPECULUM_ENV", "prod"):
        assert get_environment() is Environment.PROD


# =============================================================================
# 10. main.create_app — minimal app 통합 점검
# =============================================================================

def test_create_app_serves_demo_routes() -> None:
    """main.create_app 의 demo endpoint 가 middleware 통과 — oracle v2 H3 반영.

    default 환경 = DEV → REDACT. demo_dirty 는 어휘가 별표로 치환되어 200 반환.
    """
    from app.main import create_app

    # 환경변수 격리 — 호스트의 SPECULUM_FORBIDDEN_POLICY 가 테스트에 영향 주지 않도록.
    # T24 후속: demo route 는 명시적 factory parameter 로만 활성 (oracle 결정 7).
    with _env("SPECULUM_FORBIDDEN_POLICY", None):
        client = TestClient(create_app(include_demo_routes=True))
        res = client.get("/api/_demo/clean")
        assert res.status_code == 200
        res2 = client.get("/api/_demo/dirty")
        # REDACT — 응답은 200 이나 어휘 별표 치환
        assert res2.status_code == 200
        body = res2.json()
        assert "추천" not in body["title"]
        assert "Buy" not in body["message"]


def test_create_app_healthz() -> None:
    from app.main import create_app

    client = TestClient(create_app())
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
