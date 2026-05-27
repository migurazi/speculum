"""Speculum FastAPI app — T24 bootstrap + dependency wiring.

7 사이클의 standalone 도메인이 본 app 에서 합류. T25~T28 endpoint 는 별도
사이클에서 router 로 추가.

현재 구성:
- ForbiddenWordsGuardMiddleware — 모든 JSON 응답 검사 (ADR-0007 D4.4)
- AsOfPolicy dependency — `as_of` query param + X-AsOf-* header 자동 (ADR-0008 D6)
- Auth dependency skeleton — M0 single-user `SYSTEM_USER_ID` (T31 NextAuth 전)
- Exception handlers — domain 예외 → JSON + validation input sanitize
- `/api/as_of` (정규화 결과) + `/api/policy-versions` (정책 freeze 집계)
- `/healthz` — middleware skip 대상

관련 ADR:
- ADR-0007 D4.4 — middleware policy 별 응답 처리
- ADR-0008 D6 — as_of API contract
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.exception_handlers import register_exception_handlers
from app.api.routes.meta import router as meta_router
from app.middleware.forbidden_words_guard import ForbiddenWordsGuardMiddleware


def create_app(*, include_demo_routes: bool = False) -> FastAPI:
    """FastAPI app 팩토리.

    Args:
        include_demo_routes: True 면 `_demo_*` middleware 테스트용 endpoint 등록.
            **운영에서는 False (default)** — production binary 에 demo 가 살아
            있을 위험 차단 (oracle 자문 결정 7). 테스트 fixture 가 명시적
            True 주입.

    Returns:
        FastAPI app — middleware + exception handlers + routes wired.
    """
    app = FastAPI(
        title="Speculum",
        description="한국 주식 시장 정량 데이터 탐색기 — 정보 제공 도구",
        version="0.1.0-dev",
    )

    # ADR-0007 D4.4 의 강제 메커니즘 — 환경별 default policy 자동 적용.
    app.add_middleware(ForbiddenWordsGuardMiddleware)

    # Domain exception → JSON handler (ADR-0008 D6 / oracle R1).
    register_exception_handlers(app)

    # Meta endpoint — `/api/as_of`, `/api/policy-versions`.
    app.include_router(meta_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """헬스체크 — middleware skip 대상."""
        return {"status": "ok"}

    if include_demo_routes:
        _register_demo_routes(app)

    return app


def _register_demo_routes(app: FastAPI) -> None:
    """ForbiddenWordsGuardMiddleware 단위 테스트용 demo endpoint.

    `create_app(include_demo_routes=True)` 일 때만 호출. 운영 코드 경로에서는
    실행 X — production binary 에 demo 가 살아 있을 위험 차단.
    """

    @app.get("/api/_demo/clean")
    async def demo_clean() -> dict[str, object]:
        return {
            "title": "조건에 부합하는 종목",
            "items": [{"code": "005930", "name_label": "Samsung Electronics"}],
        }

    @app.get("/api/_demo/dirty")
    async def demo_dirty() -> dict[str, object]:
        """의도적 금지 어휘 — middleware 가 정책별로 처리 검증용."""
        return {
            "title": "오늘의 추천 종목",
            "message": "Buy now",
        }

    @app.get("/api/_demo/excluded_field")
    async def demo_excluded() -> dict[str, object]:
        """`stock_name` 필드에 금지 어휘 substring — exclude_paths 설정 시 통과."""
        return {
            "title": "Stock Detail",
            "stock_name": "이베스트투자증권",
            "value": 12.3,
        }


# uvicorn 등 ASGI runner 가 import 할 진입점.
app = create_app()
