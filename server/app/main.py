"""Speculum FastAPI app — minimal scaffold.

본 파일은 M0 T0 scaffold. 실제 router · DB · auth 는 Phase 1 (T13~) 에서 확장.

현재 구성:
- ForbiddenWordsGuardMiddleware — 모든 JSON 응답 검사 (ADR-0007 D4.4)
- /healthz — middleware skip 대상 헬스체크
- /api/_demo/* — middleware 단위 테스트용 demo endpoint
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.responses import JSONResponse

from app.middleware.forbidden_words_guard import ForbiddenWordsGuardMiddleware


def create_app() -> FastAPI:
    """FastAPI app 팩토리. 테스트가 환경별 middleware 설정을 주입할 수 있도록 분리."""
    app = FastAPI(
        title="Speculum",
        description="한국 주식 시장 정량 데이터 탐색기 — 정보 제공 도구",
        version="0.1.0-dev",
    )

    # ADR-0007 D4.4 의 강제 메커니즘 — 환경별 default policy 자동 적용.
    # 테스트는 별도 app instance 에서 policy 를 명시 override.
    app.add_middleware(ForbiddenWordsGuardMiddleware)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """헬스체크 — middleware skip 대상."""
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # demo endpoint — middleware 단위 테스트 용. 실제 운영에서는 제거 (M0 release).
    # ------------------------------------------------------------------

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

    return app


# uvicorn 등 ASGI runner 가 import 할 진입점.
app = create_app()
