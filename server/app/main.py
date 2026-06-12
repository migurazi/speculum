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

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.dart_adapter import DartAdapter
from app.api.exception_handlers import register_exception_handlers
from app.api.routes.backtest import router as backtest_router
from app.api.routes.custom_packs import router as custom_packs_router
from app.api.routes.factor_packs import router as factor_packs_router
from app.api.routes.market import router as market_router
from app.api.routes.meta import router as meta_router
from app.api.routes.notes import router as notes_router
from app.api.routes.portfolio import router as portfolio_router
from app.api.routes.publishers import router as publishers_router
from app.api.routes.runs import router as runs_router
from app.api.routes.screen import router as screen_router
from app.api.routes.screen_custom import runs_router as custom_runs_router
from app.api.routes.screen_custom import screen_router as custom_screen_router
from app.api.routes.screener_sets import router as screener_sets_router
from app.api.routes.stocks import router as stocks_router
from app.api.routes.tax import router as tax_router
from app.api.routes.watchlists import router as watchlists_router
from app.core.config import (
    assert_auth_config_for_environment,
    get_database_url,
)
from app.db.session import create_engine_from_url, create_sessionmaker
from app.middleware.forbidden_words_guard import ForbiddenWordsGuardMiddleware
from app.repositories.batch_run_repository import (
    BatchRunRepository,
    FakeBatchRunRepository,
)
from app.repositories.custom_pack_repository import (
    CustomPackRepository,
    FakeCustomPackRepository,
)
from app.repositories.notes_repository import (
    FakeNotesRepository,
    NotesRepository,
)
from app.repositories.pit_protocols import (
    CorporateActionRepository,
    DividendRepository,
    FinancialRepository,
    MacroIndicatorRepository,
    MarketCapRepository,
    PriceRepository,
    TreasurySharesRepository,
)
from app.repositories.portfolio_repository import (
    FakePortfolioRepository,
    PortfolioRepository,
)
from app.repositories.publisher_repository import (
    FakePublisherRepository,
    PublisherRepository,
)
from app.repositories.screen_run_repository import (
    FakeScreenRunRepository,
    ScreenRunRepository,
)
from app.repositories.stocks_master_repository import (
    StocksMasterRepository,
)
from app.repositories.user_repository import (
    FakeUserRepository,
    UserRepository,
)
from app.repositories.watchlist_repository import (
    FakeScreenerSetRepository,
    FakeWatchlistRepository,
    ScreenerSetRepository,
    WatchlistRepository,
)
from app.services.corp_code_mapping import CorpCodeMapping
from app.services.disclosure_fact_extraction import LlmFactExtractor
from app.services.llm.anthropic_fact_extractor import build_default_fact_extractor

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
    # ADR-0026 — 공시 metadata 제목 (DART 원문, EXTERNAL_QUOTE).
    "report_name",
    # M3 #4 (ADR-0029 D4) — Portfolio 거래 side('buy'|'sell')는 사용자 본인의
    # 사실 거래 기록(USER_PRIVATE)이지 매매 권유 텍스트가 아니다. 거래내역 회계
    # 사실의 enum 값이므로 forbidden_words("buy"/"sell") 검사 제외. 추천 변질
    # 통로는 D3(평가 라벨 0 — 응답에 손익률/등급/순위/색 필드 부재)이 차단한다.
    "side",
})


def create_app(
    *,
    include_demo_routes: bool = False,
    stocks_repository: StocksMasterRepository | None = None,
    price_repository: PriceRepository | None = None,
    financial_repository: FinancialRepository | None = None,
    corporate_action_repository: CorporateActionRepository | None = None,
    market_cap_repository: MarketCapRepository | None = None,
    treasury_repository: TreasurySharesRepository | None = None,
    macro_indicator_repository: MacroIndicatorRepository | None = None,
    runs_repository: ScreenRunRepository | None = None,
    batch_run_repository: BatchRunRepository | None = None,
    watchlist_repository: WatchlistRepository | None = None,
    screener_set_repository: ScreenerSetRepository | None = None,
    notes_repository: NotesRepository | None = None,
    portfolio_repository: PortfolioRepository | None = None,
    custom_pack_repository: CustomPackRepository | None = None,
    publisher_repository: PublisherRepository | None = None,
    dividend_repository: DividendRepository | None = None,
    user_repository: UserRepository | None = None,
    dart_adapter: DartAdapter | None = None,
    corp_code_mapping: CorpCodeMapping | None = None,
    fact_extractor: LlmFactExtractor | None = None,
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
        price_repository: 가격 시계열 Repository — M1 T50 FieldProvider 의
            `close_price_adjusted` 해소 + 차트. None 이면 SQL/Fake auto-swap.
        financial_repository: 재무제표 Repository — M1 T50 FieldProvider 의
            재무 scalar / series / period_begin / annual 해소. None 이면 auto-swap.
        corporate_action_repository: Corporate action Repository — M1 T50
            read-time 가격 보정. None 이면 auto-swap (Fake = 보정 사건 0).
        market_cap_repository: 시가총액 / 발행주식수 Repository — Phase B
            FieldProvider 의 `market_cap_krx_official` / `shares_issued` 해소.
            None 이면 SQL/Fake auto-swap.
        treasury_repository: 자사주 Repository — FieldProvider 의
            `shares_treasury` 해소 (→ factor `market-cap:ex-treasury` 자동
            실평가). None 이면 SQL/Fake auto-swap.
        macro_indicator_repository: ECOS 거시지표 Repository — Market Overview
            의 표시용 매크로 지표 조회 (T64). None 이면 SQL/Fake auto-swap.
            매크로는 표시용만 — factor 입력 금지 (ADR-0007 D5).
        runs_repository: Screen Run snapshot repository — T13 Phase B 진행 전이
            라 SQL 미지원. None 이면 FakeScreenRunRepository.
        batch_run_repository: BatchRun repository — reproduce_run 의 frozen
            batch_id → BatchCutoff 해소용 (M2 T80). None 이면 SQL wiring 활성
            시 SqlBatchRunRepository, 비활성 시 FakeBatchRunRepository.
        watchlist_repository: Watchlist CRUD repository — T13 Phase B 진행 전이
            라 SQL 미지원. None 이면 FakeWatchlistRepository.
        screener_set_repository: ScreenerSet CRUD — Phase B. None 이면 Fake.
        notes_repository: Stock Notes CRUD repository — T76 종목별 사용자
            Markdown 메모(USER_PRIVATE). None 이면 SQL wiring 활성 시
            SqlNotesRepository, 비활성 시 FakeNotesRepository.
        portfolio_repository: Portfolio 거래내역 repository — M3 #4(ADR-0029 D1)
            종목별 사용자 수동 입력 거래내역(append-only). None 이면 SQL wiring
            활성 시 SqlPortfolioRepository, 비활성 시 FakePortfolioRepository.
        custom_pack_repository: Custom Pack 영속화 repository — ADR-0022 D9
            Factor Lab 사용자 정의 pack(append-only immutable). None 이면 SQL
            wiring 활성 시 SqlCustomPackRepository, 비활성 시 Fake.
        publisher_repository: Publisher claim repository — M6 #3(ADR-0034 D4)
            인증 user 의 publisher handle claim(append-only, handle 인스턴스 유일
            + user 1:1). None 이면 SQL wiring 활성 시 SqlPublisherRepository,
            비활성 시 FakePublisherRepository.
        user_repository: User repository — T68 NextAuth JWT 인증 경로의
            google_sub → user_id JIT provision. None 이면 SQL wiring 활성 시
            SqlUserRepository, 비활성 시 FakeUserRepository. **AUTH_SECRET
            미설정(기본)이면 auth 가 본 repository 를 호출하지 않음(회귀 0).**
        dart_adapter: DART OpenAPI adapter — ADR-0026 공시 metadata on-demand
            fetch (list.json). None 이면 factory 가 default DartAdapter
            (DART_API_KEY env var). 테스트가 MockTransport 기반 주입 가능.
        corp_code_mapping: KRX code → DART corp_code 매핑 — ADR-0026 route
            corp_code 해소. None 이면 빈 매핑 (모든 종목 corp_code 미발견 → 404).
            운영 boot strap / 테스트가 명시 주입.
        fact_extractor: 공시 사실추출기(LlmFactExtractor) — ADR-0031 D6. 운영 LLM
            연동은 release blocker(§2.7 경계 + ADR-0006 자문)이므로 None 이 정상
            상태(route 503). 테스트/개발이 FakeLlmFactExtractor 를 주입해 게이트·
            스키마·디스클레이머 파이프라인을 실제 SDK 없이 검증.
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

    # 운영(PROD) 환경에서 AUTH_SECRET 필수 — 부재 시 startup fail (conformance
    # 이슈 3). 미설정 = 인증 single-user fallback silent degrade 차단. dev/staging
    # 은 fallback 허용(회귀 0). 테스트(SPECULUM_ENV 미설정 → DEV)는 무영향.
    assert_auth_config_for_environment()

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

    # CORS — dev frontend(localhost:3000)가 다른 origin(:8000)의 API를 호출하므로
    # preflight(OPTIONS) 처리 + 응답 헤더 허용 필요 (없으면 브라우저 "Failed to
    # fetch" / OPTIONS 405). `SPECULUM_CORS_ORIGINS`(comma) 로 override, default 는
    # 로컬 dev origin. ForbiddenWordsGuard 뒤에 추가 → outermost 로 동작(preflight
    # 를 먼저 처리). 운영 배포 시 실제 frontend origin 을 env 로 지정.
    _cors_default = "http://localhost:3000,http://127.0.0.1:3000"
    _cors_origins = [
        o.strip()
        for o in os.environ.get("SPECULUM_CORS_ORIGINS", _cors_default).split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Repository 명시 주입 — endpoint dependency 가 app.state 에서 fetch.
    # 명시 주입 우선, 미주입 시 factory 가 SQL/Fake auto-swap (T13 wiring).
    # Phase B 미진행 도메인 (runs / watchlist / screener_set) 은 항상 Fake.
    app.state.stocks_repo_override = stocks_repository
    # M1 T50 — FieldProvider 가 사용하는 price / financial / corporate_action
    # repository 명시 주입 (테스트 fixture). None 이면 factory 가 SQL/Fake auto-swap.
    app.state.price_repo_override = price_repository
    app.state.financial_repo_override = financial_repository
    app.state.corporate_action_repo_override = corporate_action_repository
    # Phase B — market_cap / shares 영구화 repository 명시 주입.
    app.state.market_cap_repo_override = market_cap_repository
    # 자사주 (treasury_shares) 영구화 repository 명시 주입 — `shares_treasury`
    # 해소 → factor `market-cap:ex-treasury` 자동 실평가.
    app.state.treasury_repo_override = treasury_repository
    # ECOS 거시지표 repository 명시 주입 — Market Overview 표시용 (T64).
    # 매크로는 factor 입력 금지 (ADR-0007 D5). None 이면 factory 가 SQL/Fake auto-swap.
    app.state.macro_indicator_repo_override = macro_indicator_repository
    app.state.runs_repo = runs_repository or FakeScreenRunRepository()
    # M2 T80 — reproduce_run 의 frozen batch_id 해소용 repository 명시 주입.
    # None 이면 factory 가 SQL/Fake auto-swap (get_batch_run_repository).
    app.state.batch_run_repo_override = batch_run_repository or FakeBatchRunRepository()
    app.state.watchlist_repo = watchlist_repository or FakeWatchlistRepository()
    app.state.screener_set_repo = (
        screener_set_repository or FakeScreenerSetRepository()
    )
    # T76 — 종목별 사용자 Markdown 메모 repository 명시 주입(테스트 fixture).
    # None 이면 factory(get_notes_repository)가 SQL/Fake auto-swap.
    app.state.notes_repo = notes_repository or FakeNotesRepository()
    # M3 #4(ADR-0029 D1) — Portfolio 거래내역 repository 명시 주입(테스트 fixture).
    # None 이면 factory(get_portfolio_repository)가 SQL/Fake auto-swap.
    app.state.portfolio_repo = portfolio_repository or FakePortfolioRepository()
    # ADR-0022 D9 — Factor Lab 사용자 정의 pack 영속화 repository 명시 주입.
    # None 이면 factory(get_custom_pack_repository)가 SQL/Fake auto-swap.
    app.state.custom_pack_repo = (
        custom_pack_repository or FakeCustomPackRepository()
    )
    # M6 #3(ADR-0034 D4) — 인증 publisher handle claim repository 명시 주입.
    # None 이면 factory(get_publisher_repository)가 SQL/Fake auto-swap.
    app.state.publisher_repo = (
        publisher_repository or FakePublisherRepository()
    )
    # M7 #1(ADR-0035 D6) — cash_dividend 이중 PIT repository 명시 주입.
    # None 이면 factory(get_dividend_repository)가 SQL/Fake auto-swap.
    app.state.dividend_repo_override = dividend_repository
    # T68 — NextAuth JWT 인증 경로의 google_sub → user_id JIT provision repository.
    # AUTH_SECRET 미설정(기본)이면 auth 가 본 repository 를 호출하지 않음(회귀 0).
    # None 이면 factory(get_user_repository)가 SQL/Fake auto-swap.
    app.state.user_repo = user_repository or FakeUserRepository()
    # ADR-0026 — 공시 metadata on-demand fetch (DART list.json). 명시 주입
    # (MockTransport 기반 DartAdapter / 운영 corp_code 매핑) 우선. None 이면
    # factory 가 default DartAdapter (DART_API_KEY) / 빈 매핑 (모든 종목 404).
    app.state.dart_adapter_override = dart_adapter
    app.state.corp_code_mapping_override = corp_code_mapping
    # ADR-0031 D6 — 공시 사실추출기(LlmFactExtractor). 명시 주입(fact_extractor=)
    # 우선(테스트 FakeLlmFactExtractor / 운영 구현체). 미주입이면 env 기반 factory
    # (build_default_fact_extractor)가 ANTHROPIC_API_KEY 설정 시 AnthropicFactExtractor
    # 를, 미설정/pytest 면 None 을 반환 → route 503(운영 LLM 미연동이 정상 상태,
    # release blocker §2.7 경계 + ADR-0006 자문). 키 미설정 시 기존 동작 그대로(회귀 0).
    app.state.fact_extractor_override = (
        fact_extractor or build_default_fact_extractor()
    )

    # Domain exception → JSON handler (ADR-0008 D6 / oracle R1).
    register_exception_handlers(app)

    # Meta endpoint — `/api/as_of`, `/api/policy-versions`.
    app.include_router(meta_router)
    # M2 T71 — POST /api/factor-packs/validate (custom pack 실시간 검증).
    app.include_router(factor_packs_router)
    # T25/T27 — /api/stocks/{code}, /api/stocks/search, /api/stocks/compare.
    app.include_router(stocks_router)
    # T26 — POST /api/screen (실행만).
    app.include_router(screen_router)
    # ADR-0025 D4 — POST /api/screen/custom (custom pack 실행, CurrentUserDep).
    app.include_router(custom_screen_router)
    # M1 T60 — GET /api/market-overview (집계 통계만, 랭킹·추천 0).
    app.include_router(market_router)
    # T26 + T40 — /api/runs (Save Run + recent + fetch + diff).
    app.include_router(runs_router)
    # ADR-0025 D5 — POST /api/runs/custom (custom pack Save Run, freeze).
    app.include_router(custom_runs_router)
    # ADR-0027 — POST /api/backtest (PIT rebalance 백테스트, CurrentUserDep).
    app.include_router(backtest_router)
    # T28 — /api/watchlists (CRUD) + /api/screener-sets (조건셋).
    app.include_router(watchlists_router)
    app.include_router(screener_sets_router)
    # T76 — /api/notes (종목별 사용자 Markdown 메모 CRUD, USER_PRIVATE).
    app.include_router(notes_router)
    # M3 #4(ADR-0029) — /api/portfolio (거래내역 CRUD + position 계산, USER_PRIVATE).
    # 회계 ≠ 평가 분리(D3) — 숫자 사실만, 세전만(D6), 수동 입력만(D4).
    app.include_router(portfolio_router)
    # ADR-0022 D9 — /api/factor-packs/saved (사용자 정의 pack 영속화, user-scoped).
    app.include_router(custom_packs_router)
    # M6 #3(ADR-0034 D4) — POST /api/publishers (인증 publisher handle claim,
    # 사칭 차단). user↔handle 1:1, handle 인스턴스 유일.
    app.include_router(publishers_router)
    # M3 #5(ADR-0030) — POST /api/tax/securities-transaction (증권거래세 단순
    # 산식만, 인증 불요). 양도세 미구현(D1 세무사법 경계), disclaimer_required=true(D3).
    app.include_router(tax_router)

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
