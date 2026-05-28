"""Speculum FastAPI app — T24 bootstrap + dependency wiring.

7 사이클의 standalone 도메인이 본 app 에서 합류. T25~T28 endpoint 는 별도
사이클에서 router 로 추가. T13 wiring 사이클에서 SQL repository auto-swap
+ engine lifespan 추가.

현재 구성:
- ForbiddenWordsGuardMiddleware — 모든 JSON 응답 검사 (ADR-0007 D4.4)
- AsOfPolicy dependency — `as_of` query param + X-AsOf-* header 자동 (ADR-0008 D6)
- Auth dependency skeleton — M0 single-user `SYSTEM_USER_ID` (T31 NextAuth 전)
- Exception handlers — domain 예외 → JSON + validation input sanitize
- `/api/as_of` (정규화 결과) + `/api/policy-versions` (정책 freeze 집계)
- `/healthz` — middleware skip 대상
- **T13 wiring**: `SPECULUM_DATABASE_URL` env var 있을 때 SQL engine
  lifespan + `app.state.db_sessionmaker` 등록. dependencies/repositories.py
  의 factory 가 자동 swap.

관련 ADR:
- ADR-0007 D4.4 — middleware policy 별 응답 처리
- ADR-0008 D6 — as_of API contract
- ADR-0002 D5 / ADR-0009 D6 — DB schema (T13)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI

from app.api.exception_handlers import register_exception_handlers
from app.api.routes.meta import router as meta_router
from app.api.routes.runs import router as runs_router
from app.api.routes.screen import router as screen_router
from app.api.routes.screener_sets import router as screener_sets_router
from app.api.routes.stocks import router as stocks_router
from app.api.routes.watchlists import router as watchlists_router
from app.core.config import get_database_url
from app.db.session import create_engine_from_url, create_sessionmaker
from app.middleware.forbidden_words_guard import ForbiddenWordsGuardMiddleware
from app.repositories.screen_run_repository import (
    FakeScreenRunRepository,
    ScreenRunRepository,
)
from app.repositories.stocks_master_repository import (
    StocksMasterRepository,
)
from app.repositories.watchlist_repository import (
    FakeScreenerSetRepository,
    FakeWatchlistRepository,
    ScreenerSetRepository,
    WatchlistRepository,
)

# 종목명·회사명·DART 공시 제목 등 EXTERNAL_QUOTE scope path — 금지 어휘 검사 제외.
# oracle T25 자문 Risk-C1 — 회사명 "이베스트투자증권" 같은 substring 이 BLOCK
# 되지 않도록. 본 set 의 key 가 dict 의 nested 어디서든 등장하면 그 값 검사 제외.
_EXTERNAL_QUOTE_EXCLUDE_KEYS: Final[frozenset[str]] = frozenset({
    # Stock master / detail (T25)
    "name", "current_name", "company_name", "name_en",
    "alt_names",
    "stock_name", "stock_label",
    # Disclosure metadata (T19 DART)
    "title", "report_title",
})


def create_app(
    *,
    include_demo_routes: bool = False,
    stocks_repository: StocksMasterRepository | None = None,
    runs_repository: ScreenRunRepository | None = None,
    watchlist_repository: WatchlistRepository | None = None,
    screener_set_repository: ScreenerSetRepository | None = None,
    database_url: str | None = None,
) -> FastAPI:
    """FastAPI app 팩토리.

    Args:
        include_demo_routes: True 면 `_demo_*` middleware 테스트용 endpoint 등록.
            **운영에서는 False (default)** — production binary 에 demo 가 살아
            있을 위험 차단 (oracle 자문 결정 7). 테스트 fixture 가 명시적
            True 주입.
        stocks_repository: 종목 마스터 Repository 주입. None 이면 (1) SQL
            wiring 활성 시 SqlStocksMasterRepository, (2) 비활성 시 빈 Fake.
            테스트가 fixture 로 채운 Fake 를 주입 가능.
        runs_repository: Screen Run snapshot repository — T13 Phase B 진행 전이
            라 SQL 미지원. None 이면 FakeScreenRunRepository.
        watchlist_repository: Watchlist CRUD repository — T13 Phase B 진행 전이
            라 SQL 미지원. None 이면 FakeWatchlistRepository.
        screener_set_repository: ScreenerSet CRUD — Phase B. None 이면 Fake.
        database_url: SQL engine DSN. None 이면 `SPECULUM_DATABASE_URL` 환경변수
            확인. 둘 다 None 이면 Fake-only mode (T13 wiring 비활성).
            테스트가 명시적 in-memory URL 주입 가능.

    Returns:
        FastAPI app — middleware + exception handlers + routes + lifespan wired.

    Note (T13 wiring):
        SQL engine 은 lifespan startup 에서 생성, shutdown 에서 dispose. 명시적
        `database_url` 주입이 환경변수보다 우선 — 테스트는 fixture 단계에서
        DSN 지정 가능.
    """
    # 효과적 DB URL — 명시 인자 > 환경변수 > None.
    # oracle 리뷰 L2 — 빈 문자열도 None 으로 정규화 (명시 `database_url=""` 의도
    # 가 SQLAlchemy create_engine 의 raise 로 이어지지 않도록).
    explicit_url = (database_url or "").strip() or None
    effective_db_url = explicit_url if explicit_url is not None else get_database_url()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # startup — DB URL 있으면 engine + sessionmaker 생성, app.state 등록.
        if effective_db_url is not None:
            engine = create_engine_from_url(effective_db_url)
            app.state.db_engine = engine
            app.state.db_sessionmaker = create_sessionmaker(engine)
        else:
            # Fake-only mode — 명시적 None marker (factory 가 분기).
            app.state.db_engine = None
            app.state.db_sessionmaker = None

        try:
            yield
        finally:
            # oracle 리뷰 M1 — shutdown 의 race 회피: state reset 제거.
            # `engine.dispose()` 만 호출. dispose 된 engine 에서 새 session
            # 생성 시 SQLAlchemy 가 명시 에러 raise → silent Fake fall-through
            # 차단. in-flight request 의 outstanding session 은 dispose 가 그
            # connection 만 idle 시 close.
            engine = getattr(app.state, "db_engine", None)
            if engine is not None:
                engine.dispose()

    app = FastAPI(
        title="Speculum",
        description="한국 주식 시장 정량 데이터 탐색기 — 정보 제공 도구",
        version="0.1.0-dev",
        lifespan=lifespan,
    )

    # ADR-0007 D4.4 의 강제 메커니즘 — 환경별 default policy 자동 적용.
    # 회사명 등 EXTERNAL_QUOTE scope 는 exclude_keys 로 검사 제외 (oracle T25 C1).
    app.add_middleware(
        ForbiddenWordsGuardMiddleware,
        exclude_keys=_EXTERNAL_QUOTE_EXCLUDE_KEYS,
    )

    # Repository 명시 주입 — endpoint dependency 가 app.state 에서 fetch.
    # 명시 주입 우선, 미주입 시 factory 가 SQL/Fake auto-swap (T13 wiring).
    # Phase B 미진행 도메인 (runs / watchlist / screener_set) 은 항상 Fake.
    app.state.stocks_repo_override = stocks_repository
    app.state.runs_repo = runs_repository or FakeScreenRunRepository()
    app.state.watchlist_repo = watchlist_repository or FakeWatchlistRepository()
    app.state.screener_set_repo = (
        screener_set_repository or FakeScreenerSetRepository()
    )

    # Domain exception → JSON handler (ADR-0008 D6 / oracle R1).
    register_exception_handlers(app)

    # Meta endpoint — `/api/as_of`, `/api/policy-versions`.
    app.include_router(meta_router)
    # T25/T27 — /api/stocks/{code}, /api/stocks/search, /api/stocks/compare.
    app.include_router(stocks_router)
    # T26 — POST /api/screen (실행만).
    app.include_router(screen_router)
    # T26 + T40 — /api/runs (Save Run + recent + fetch + diff).
    app.include_router(runs_router)
    # T28 — /api/watchlists (CRUD) + /api/screener-sets (조건셋).
    app.include_router(watchlists_router)
    app.include_router(screener_sets_router)

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
