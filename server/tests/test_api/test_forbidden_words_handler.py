"""ForbiddenWordsAssertError exception handler 회귀 — Momus M0 review V12/W6.

`assert_clean` 의 ValueError 메시지가 server-side log 용 어휘 echo 보존 (audit
attribute) + handler 가 response body 에 어휘 echo 0 보장.

본 test 는 handler 가 명시 등록된 후 응답 본문이 generic 한지 검증 — middleware
의 2 차 방어 없이도 handler 1 차 차단.

관련:
- ADR-0007 D4.4.2 (응답 본문 echo 정책)
- Momus M0 review V12 / W6
- server/app/api/exception_handlers.py forbidden_words_handler
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.exception_handlers import register_exception_handlers
from app.services.forbidden_words import (
    CheckScope,
    ForbiddenWordsAssertError,
    assert_clean,
)


def _make_app() -> FastAPI:
    """assert_clean 호출이 raise 하는 minimal app — handler 등록 검증용."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise-dirty")
    async def raise_dirty() -> dict:
        # endpoint 가 명시적으로 dirty text 검증 — 운영 의미상 builtin pack 의
        # 변조 또는 사용자 input 검증 시점에 raise 가능.
        assert_clean("매수 추천 종목", scope=CheckScope.SYSTEM, context="test_endpoint")
        return {"ok": True}

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = _make_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_handler_returns_500_without_word_echo(client: TestClient) -> None:
    """Handler 가 500 + generic message 응답 — 어휘 echo 0 (ADR-0007 D4.4.2)."""
    res = client.get("/raise-dirty")

    assert res.status_code == 500
    body = res.json()
    assert body["code"] == "FORBIDDEN_WORDS_GUARD_INTERNAL"
    # response body 에 검출 어휘 echo 0 — Momus V12/W6 의 핵심 요구.
    raw = res.text
    assert "매수" not in raw
    assert "추천" not in raw
    assert "scope=" not in raw
    assert "Forbidden words detected" not in raw
    # generic message 만.
    assert "서버 내부 오류" in body["detail"]


def test_handler_catches_subclass_specifically() -> None:
    """ForbiddenWordsAssertError instance 의 ValueError sub-class invariant."""
    try:
        assert_clean(
            "매수 추천 종목", scope=CheckScope.SYSTEM, context="invariant_test",
        )
    except ForbiddenWordsAssertError as exc:
        # backward compat: ValueError catch 도 작동.
        assert isinstance(exc, ValueError)
        # audit attr 보존 (handler 가 log emit 에 사용).
        assert exc.scope == CheckScope.SYSTEM
        assert exc.context == "invariant_test"
        assert len(exc.matches) >= 1
    else:
        pytest.fail("assert_clean 이 raise 안 함")
