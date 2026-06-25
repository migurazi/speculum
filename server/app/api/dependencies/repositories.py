"""Repository dependency injection — environment 별 구현체 swap point.

oracle T25 자문 Risk-C8 — Fake/Sql 분기를 본 layer 에 집중. T13 wiring 합류로
`app.state.db_sessionmaker` 가 설정돼 있으면 SQL repository 자동 반환, 미설정
이면 기존 Fake 패턴. endpoint 가 직접 Fake import 하지 않도록 강제.

설계:

1. **명시 주입 우선** — `main.create_app(stocks_repository=...)` 로 주입된
   Fake (테스트 fixture 등) 는 항상 우선. `app.state.stocks_repo_override` 에
   저장.

2. **SQL/Fake auto-swap** — 명시 주입 없고 `db_sessionmaker` 있으면 request
   단위 session 으로 SQL repo 생성. 없으면 빈 Fake.

3. **request 단위 session — Depends generator 패턴**:
   `get_db_session_or_none` 가 generator 로 session 을 yield 하고 endpoint 종료
   시 자동 close. FastAPI 의 Depends caching 덕에 같은 request 의 여러 repo
   factory 가 호출돼도 session 은 한 번만 생성/cleanup.

4. **Phase B 미진행 도메인** (runs / watchlist / screener_set) 은 ORM 없음 →
   항상 Fake. SQL 구현은 별도 사이클.

관련 ADR / 문서:
- ADR-0002 D5 / ADR-0009 D6 — DB schema
- M0_PLAN T13 — 본 wiring
- FastAPI Depends generator dependencies — 자동 cleanup 보장
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.adapters.dart_adapter import DartAdapter
from app.adapters.pykrx_adapter import PykrxAdapter
from app.repositories.batch_run_repository import (
    BatchRunRepository,
    FakeBatchRunRepository,
    SqlBatchRunRepository,
)
from app.repositories.citation_repository import (
    CitationRepository,
    FakeCitationRepository,
    SqlCitationRepository,
)
from app.repositories.custom_pack_repository import (
    CustomPackRepository,
    FakeCustomPackRepository,
)
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeDividendRepository,
    FakeFinancialRepository,
    FakeMacroIndicatorRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeStockSnapshotRepository,
    FakeTreasurySharesRepository,
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
    StockSnapshotRepository,
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
from app.repositories.sql_custom_pack_repository import SqlCustomPackRepository
from app.repositories.sql_notes_repository import SqlNotesRepository
from app.repositories.sql_portfolio_repository import SqlPortfolioRepository
from app.repositories.sql_publisher_repository import SqlPublisherRepository
from app.repositories.sql_repositories import (
    SqlCorporateActionRepository,
    SqlDividendRepository,
    SqlFinancialRepository,
    SqlMacroIndicatorRepository,
    SqlMarketCapRepository,
    SqlPriceRepository,
    SqlStocksMasterRepository,
    SqlStockSnapshotRepository,
    SqlTreasurySharesRepository,
)
from app.repositories.sql_user_repositories import (
    SqlScreenerSetRepository,
    SqlScreenRunRepository,
    SqlUserRepository,
    SqlWatchlistRepository,
)
from app.repositories.stocks_master_repository import (
    FakeStocksMasterRepository,
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
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK, LoadedPack


def get_db_session_or_none(request: Request) -> Iterator[Session | None]:
    """Request-scoped DB session — Fake-mode 호환 (None yield).

    SQL wiring 활성 (`app.state.db_sessionmaker` 존재) 이면 session 생성 →
    endpoint 종료 시 자동 close. Fake-only mode 면 None 만 yield (factory 가
    Fake 로 분기).

    FastAPI Depends caching:
        같은 request 에서 본 dependency 가 여러 repo factory 에 의해 의존돼도
        session 은 한 번만 생성. response 후 generator finally 가 cleanup.

    Note (oracle 자문 — session lifecycle):
        본 dependency 는 `yield` 후 명시 `session.close()`. exception path 에서
        rollback 은 SQLAlchemy Session 의 default behavior (commit 안 했으면
        open transaction 이 close 시 rollback). M0 read-heavy endpoint 라
        explicit commit/rollback 미사용.
    """
    sessionmaker = getattr(request.app.state, "db_sessionmaker", None)
    if sessionmaker is None:
        # Fake-only mode — None yield. factory 가 None 분기.
        yield None
        return
    session: Session = sessionmaker()
    # oracle 리뷰 C1 — write endpoint 의 commit 책임 명시 (request 단위 tx).
    # exception path → rollback (uncommitted write 폐기). 정상 path → commit
    # (`SqlCitationRepository.save` 등의 add+flush 가 영구화). read-only endpoint
    # 의 commit 은 no-op.
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    else:
        session.commit()
    finally:
        session.close()


# Annotated 의존성 type — repo factory 들이 본 alias 사용.
_SessionOrNoneDep = Annotated[Session | None, Depends(get_db_session_or_none)]

# 공개 alias — run 생성 endpoint (screen/runs) 가 batch_id freeze 위해 직접
# session 을 받아 `collect_batch_versions(as_of, session)` 호출 (M1 T48b).
# Fake-only mode 면 None → batch_id 키 합류 skip (정책-only 11 키 유지).
SessionOrNoneDep = _SessionOrNoneDep


def get_stocks_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> StocksMasterRepository:
    """현재 active StocksMasterRepository — SQL/Fake auto-swap.

    우선순위 (T13 wiring):
        1. 테스트 명시 주입 (`app.state.stocks_repo_override`).
        2. SQL wiring 활성 + session 존재 → SqlStocksMasterRepository.
        3. 그 외 → 빈 Fake (M0 default).
    """
    override = getattr(request.app.state, "stocks_repo_override", None)
    if override is not None:
        return override
    if session is not None:
        return SqlStocksMasterRepository(session)
    return FakeStocksMasterRepository(records=())


def get_citation_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> CitationRepository:
    """SourceCitation Repository — SQL/Fake auto-swap.

    T23 의 Fidelity 빌드 게이트 + adapter 가 citation 생성 시 사용. M0 wiring
    스코프상 endpoint 가 직접 의존하진 않으나, 후속 adapter (T14~T19) 가 본
    factory 에 의존.
    """
    override = getattr(request.app.state, "citation_repo_override", None)
    if override is not None:
        return override
    if session is not None:
        return SqlCitationRepository(session)
    return FakeCitationRepository()


def get_price_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> PriceRepository:
    """가격 시계열 Repository — SQL/Fake auto-swap (M1 T50 wiring).

    DbFieldProvider 의 `close_price_adjusted` 해소 + 차트 (M1-B) 가 사용.

    우선순위:
        1. 테스트 명시 주입 (`app.state.price_repo_override`).
        2. SQL wiring 활성 → SqlPriceRepository.
        3. 그 외 → 빈 Fake (M0 default — factor N/A).
    """
    override = getattr(request.app.state, "price_repo_override", None)
    if override is not None:
        return override
    if session is not None:
        return SqlPriceRepository(session)
    return FakePriceRepository(records=())


def get_financial_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> FinancialRepository:
    """재무제표 Repository — SQL/Fake auto-swap (M1 T50 wiring).

    DbFieldProvider 의 재무 scalar / series / period_begin / annual 해소가 사용.
    SqlFinancialRepository 가 정정공시 chain (supersede) 을 PITEnforcer 로 해소.

    우선순위:
        1. 테스트 명시 주입 (`app.state.financial_repo_override`).
        2. SQL wiring 활성 → SqlFinancialRepository.
        3. 그 외 → 빈 Fake (M0 default — factor N/A).
    """
    override = getattr(request.app.state, "financial_repo_override", None)
    if override is not None:
        return override
    if session is not None:
        return SqlFinancialRepository(session)
    return FakeFinancialRepository(records=())


def get_corporate_action_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> CorporateActionRepository:
    """Corporate action Repository — SQL/Fake auto-swap (M1 T50 wiring).

    DbFieldProvider 의 `close_price_adjusted` read-time 보정에 사용 (이중 PIT,
    announced_date 기준 active chain). 없으면 보정 미적용 (identity).

    우선순위:
        1. 테스트 명시 주입 (`app.state.corporate_action_repo_override`).
        2. SQL wiring 활성 → SqlCorporateActionRepository.
        3. 그 외 → 빈 Fake (M0 default — 보정 사건 0).
    """
    override = getattr(
        request.app.state, "corporate_action_repo_override", None,
    )
    if override is not None:
        return override
    if session is not None:
        return SqlCorporateActionRepository(session)
    return FakeCorporateActionRepository(records=())


def get_market_cap_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> MarketCapRepository:
    """시가총액 / 발행주식수 Repository — SQL/Fake auto-swap (Phase B wiring).

    DbFieldProvider 의 `market_cap_krx_official` / `shares_issued` 해소가 사용.
    SqlMarketCapRepository 가 `effective_date <= as_of` 중 최신을 PIT 로 반환.

    우선순위:
        1. 테스트 명시 주입 (`app.state.market_cap_repo_override`).
        2. SQL wiring 활성 → SqlMarketCapRepository.
        3. 그 외 → 빈 Fake (M0 default — factor N/A).
    """
    override = getattr(request.app.state, "market_cap_repo_override", None)
    if override is not None:
        return override
    if session is not None:
        return SqlMarketCapRepository(session)
    return FakeMarketCapRepository(records=())


def get_treasury_shares_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> TreasurySharesRepository:
    """자사주 (보통주 자기주식수) Repository — SQL/Fake auto-swap.

    DbFieldProvider 의 `shares_treasury` 해소가 사용. SqlTreasurySharesRepository
    가 정정공시 chain (supersede) 을 PITEnforcer 로 해소. 이 field 가 해소되면
    factor `market-cap:ex-treasury` 가 evaluator 에 의해 자동 실평가됨.

    우선순위:
        1. 테스트 명시 주입 (`app.state.treasury_repo_override`).
        2. SQL wiring 활성 → SqlTreasurySharesRepository.
        3. 그 외 → 빈 Fake (M0 default — shares_treasury N/A).
    """
    override = getattr(request.app.state, "treasury_repo_override", None)
    if override is not None:
        return override
    if session is not None:
        return SqlTreasurySharesRepository(session)
    return FakeTreasurySharesRepository(records=())


def get_macro_indicator_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> MacroIndicatorRepository:
    """ECOS 거시지표 Repository — SQL/Fake auto-swap (M1 T64 wiring).

    매크로 지표는 표시용만 (factor 입력 금지 — ADR-0007 D5). vintage 이중 시간축
    PIT 조회(reference_date + vintage_date <= as_of)를 통해 잠정→확정 재현 보장.

    우선순위:
        1. 테스트 명시 주입 (`app.state.macro_indicator_repo_override`).
        2. SQL wiring 활성 → SqlMacroIndicatorRepository.
        3. 그 외 → 빈 Fake (매크로 데이터 없음, Market Overview 에서 제외).
    """
    override = getattr(request.app.state, "macro_indicator_repo_override", None)
    if override is not None:
        return override
    if session is not None:
        return SqlMacroIndicatorRepository(session)
    return FakeMacroIndicatorRepository(records=[])


def get_stock_snapshot_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> StockSnapshotRepository:
    """precomputed factor snapshot Repository — SQL/Fake auto-swap (ⓓ).

    market_overview 의 read-bypass(precompute lookup → live 평가 skip)용. SQL wiring
    미활성(또는 빈 테이블)이면 빈 Fake → 전부 miss → live 평가 fallback(기존 동작
    완전 보존 — 회귀 0). serve-vs-recompute 판정(data_versions 일치)은 호출자(route)
    책임.

    우선순위:
        1. 테스트 명시 주입 (`app.state.stock_snapshot_repo_override`).
        2. SQL wiring 활성 → SqlStockSnapshotRepository.
        3. 그 외 → 빈 Fake (snapshot 없음 → 전부 live fallback).
    """
    override = getattr(request.app.state, "stock_snapshot_repo_override", None)
    if override is not None:
        return override
    if session is not None:
        return SqlStockSnapshotRepository(session)
    return FakeStockSnapshotRepository(records=[])


def get_factor_evaluator() -> FactorEvaluator:
    """Factor evaluator — state-less, default config."""
    return FactorEvaluator()


def get_dart_adapter(request: Request) -> DartAdapter:
    """DART OpenAPI adapter — 공시목록 on-demand fetch (ADR-0026).

    재무제표/자사주 batch 와 달리, 공시 metadata 는 Stock Detail 진입 시
    실시간 fetch (영속화 X — ADR-0026 D3). 본 adapter 는 request 단위 사용 후
    cleanup (lazy http client). DART_API_KEY env var 또는 명시 주입.

    우선순위:
        1. 테스트 명시 주입 (`app.state.dart_adapter_override`) — MockTransport
           기반 DartAdapter.
        2. 그 외 → default DartAdapter (DART_API_KEY env var 사용).
    """
    override = getattr(request.app.state, "dart_adapter_override", None)
    if override is not None:
        return override
    return DartAdapter()


def get_pykrx_adapter_optional(request: Request) -> PykrxAdapter | None:
    """주가 lazy fetch 용 pykrx adapter — 명시 설정 시에만 활성(테스트 안전).

    종목 상세 차트 endpoint(`/{code}/prices`)가 DB 갭을 KRX 에서 즉석 충전할 때
    사용. **기본은 None** — `app.state.pykrx_adapter` 가 설정된 운영 app
    (`main.app`)에서만 활성화되고, 테스트의 `create_app()` 직접 사용 경로는
    미설정이라 lazy fetch 를 타지 않는다(실 네트워크 호출 0, 회귀 0).

    우선순위:
        1. 테스트/운영 명시 주입 (`app.state.pykrx_adapter_override`).
        2. 운영 app 의 module-level 설정 (`app.state.pykrx_adapter`).
        3. 그 외 → None (lazy fetch 미발동 — DB 에 있는 것만 반환).
    """
    override = getattr(request.app.state, "pykrx_adapter_override", None)
    if override is not None:
        return override
    return getattr(request.app.state, "pykrx_adapter", None)


def get_fact_extractor(request: Request) -> LlmFactExtractor | None:
    """공시 사실추출기(LlmFactExtractor) — on-demand 주입 (ADR-0031 D6).

    LLM 운영 연동은 release blocker(§2.7 경계 + ADR-0006 자문). 따라서 기본은
    **미주입(None)** — route 가 503 으로 변환(운영 LLM 미연동이 정상 상태). 테스트/
    개발은 `app.state.fact_extractor_override` 로 FakeLlmFactExtractor 를 주입해
    파이프라인을 완전 동작시킨다(실제 SDK 없이도 게이트·스키마·디스클레이머 검증).

    우선순위:
        1. 명시 주입 (`app.state.fact_extractor_override`) — fake / 운영 구현체.
        2. 그 외 → None (운영 LLM 미연동 → route 503).
    """
    return getattr(request.app.state, "fact_extractor_override", None)


def get_corp_code_mapping(request: Request) -> CorpCodeMapping:
    """KRX 종목코드 → DART corp_code 매핑 (ADR-0026 — route corp_code 해소).

    corp_code 매핑은 별도 layer (CorpCodeMapping in-memory dict). 운영 boot
    strap 에서 DART corpCode endpoint fetch 후 cache (별도 cycle) — 본 cycle 은
    `app.state.corp_code_mapping_override` 로 주입된 매핑을 재사용. 미주입 시 빈
    매핑 (모든 종목 corp_code 미발견 → route 404).

    우선순위:
        1. 테스트/운영 명시 주입 (`app.state.corp_code_mapping_override`).
        2. 그 외 → 빈 매핑 (`CorpCodeMapping.from_dict({})`).
    """
    override = getattr(request.app.state, "corp_code_mapping_override", None)
    if override is not None:
        return override
    return CorpCodeMapping.from_dict({})


def get_active_pack() -> LoadedPack:
    """M0 single-pack 가정 — DEFAULT_PACK singleton 반환 (T22 / T30 패턴 일관)."""
    return DEFAULT_PACK


def get_runs_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> ScreenRunRepository:
    """Screen Run snapshot repository — SQL/Fake auto-swap (T13 Phase C).

    우선순위:
        1. 테스트 명시 주입 (`app.state.runs_repo_override` 또는 `runs_repo`).
        2. SQL wiring 활성 → SqlScreenRunRepository.
        3. 그 외 → FakeScreenRunRepository.
    """
    override = getattr(request.app.state, "runs_repo_override", None)
    if override is not None:
        return override
    legacy = getattr(request.app.state, "runs_repo", None)
    if legacy is not None:
        return legacy
    if session is not None:
        return SqlScreenRunRepository(session)
    return FakeScreenRunRepository()


def get_watchlist_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> WatchlistRepository:
    """Watchlist CRUD repository — SQL/Fake auto-swap (T13 Phase B).

    우선순위:
        1. 테스트 명시 주입 (`app.state.watchlist_repo` 또는 override).
        2. SQL wiring 활성 (db_sessionmaker 존재) → SqlWatchlistRepository.
        3. 그 외 → FakeWatchlistRepository.
    """
    override = getattr(request.app.state, "watchlist_repo_override", None)
    if override is not None:
        return override
    legacy = getattr(request.app.state, "watchlist_repo", None)
    if legacy is not None:
        return legacy
    if session is not None:
        return SqlWatchlistRepository(session)
    return FakeWatchlistRepository()


def get_screener_set_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> ScreenerSetRepository:
    """ScreenerSet (조건셋) repository — SQL/Fake auto-swap (T13 Phase B)."""
    override = getattr(request.app.state, "screener_set_repo_override", None)
    if override is not None:
        return override
    legacy = getattr(request.app.state, "screener_set_repo", None)
    if legacy is not None:
        return legacy
    if session is not None:
        return SqlScreenerSetRepository(session)
    return FakeScreenerSetRepository()


def get_user_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> UserRepository:
    """User repository — SQL/Fake auto-swap (T68 NextAuth JIT provision).

    `auth.get_current_user`(AUTH_SECRET 설정 경로)가 google_sub → user_id 해소에
    사용. AUTH_SECRET 미설정(fallback) 경로는 본 repository 를 호출하지 않으므로,
    Fake 가 기본 wiring 이어도 기존 무토큰 테스트는 본 repository 를 안 탄다(회귀 0).

    우선순위:
        1. 테스트 명시 주입 (`app.state.user_repo` 또는 override).
        2. SQL wiring 활성 (db_sessionmaker 존재) → SqlUserRepository.
        3. 그 외 → FakeUserRepository.
    """
    override = getattr(request.app.state, "user_repo_override", None)
    if override is not None:
        return override
    legacy = getattr(request.app.state, "user_repo", None)
    if legacy is not None:
        return legacy
    if session is not None:
        return SqlUserRepository(session)
    return FakeUserRepository()


def get_notes_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> NotesRepository:
    """Stock Notes CRUD repository — SQL/Fake auto-swap (T76).

    종목별 사용자 Markdown 메모(USER_PRIVATE). watchlist 와 동형의 user-scoped
    mutable CRUD.

    우선순위:
        1. 테스트 명시 주입 (`app.state.notes_repo` 또는 override).
        2. SQL wiring 활성 (db_sessionmaker 존재) → SqlNotesRepository.
        3. 그 외 → FakeNotesRepository.
    """
    override = getattr(request.app.state, "notes_repo_override", None)
    if override is not None:
        return override
    legacy = getattr(request.app.state, "notes_repo", None)
    if legacy is not None:
        return legacy
    if session is not None:
        return SqlNotesRepository(session)
    return FakeNotesRepository()


def get_portfolio_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> PortfolioRepository:
    """Portfolio 거래내역 repository — SQL/Fake auto-swap (M3 #4, ADR-0029 D1).

    종목별 사용자 수동 입력 거래내역(append-only). notes 와 동형의 user-scoped
    repository. position 계산은 portfolio_position service(stateless)가 수행.

    우선순위:
        1. 테스트 명시 주입 (`app.state.portfolio_repo` 또는 override).
        2. SQL wiring 활성 (db_sessionmaker 존재) → SqlPortfolioRepository.
        3. 그 외 → FakePortfolioRepository.
    """
    override = getattr(request.app.state, "portfolio_repo_override", None)
    if override is not None:
        return override
    legacy = getattr(request.app.state, "portfolio_repo", None)
    if legacy is not None:
        return legacy
    if session is not None:
        return SqlPortfolioRepository(session)
    return FakePortfolioRepository()


def get_custom_pack_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> CustomPackRepository:
    """Custom Pack 영속화 repository — SQL/Fake auto-swap (ADR-0022 D9).

    Factor Lab 사용자 정의 pack 의 user-scoped 저장(append-only immutable).
    notes 와 동형의 user-scoped repository.

    우선순위:
        1. 테스트 명시 주입 (`app.state.custom_pack_repo` 또는 override).
        2. SQL wiring 활성 (db_sessionmaker 존재) → SqlCustomPackRepository.
        3. 그 외 → FakeCustomPackRepository.
    """
    override = getattr(request.app.state, "custom_pack_repo_override", None)
    if override is not None:
        return override
    legacy = getattr(request.app.state, "custom_pack_repo", None)
    if legacy is not None:
        return legacy
    if session is not None:
        return SqlCustomPackRepository(session)
    return FakeCustomPackRepository()


def get_publisher_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> PublisherRepository:
    """Publisher claim repository — SQL/Fake auto-swap (M6 #3, ADR-0034 D4).

    인증 user 의 publisher handle claim(append-only). custom_pack 과 동형의
    user-scoped repository(단, handle 인스턴스 유일 + user 1:1 강제).

    우선순위:
        1. 테스트 명시 주입 (`app.state.publisher_repo` 또는 override).
        2. SQL wiring 활성 (db_sessionmaker 존재) → SqlPublisherRepository.
        3. 그 외 → FakePublisherRepository.
    """
    override = getattr(request.app.state, "publisher_repo_override", None)
    if override is not None:
        return override
    legacy = getattr(request.app.state, "publisher_repo", None)
    if legacy is not None:
        return legacy
    if session is not None:
        return SqlPublisherRepository(session)
    return FakePublisherRepository()


def get_dividend_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> DividendRepository:
    """cash_dividend Repository — SQL/Fake auto-swap (M7 #1, ADR-0035 D6).

    이중 PIT(announced 가용성 축 + effective 발생 축) fetch_dividends 를 제공.
    total return 재투자 계산에서 §2.4 PIT 정합 배당 누적에 사용.

    우선순위:
        1. 테스트 명시 주입 (`app.state.dividend_repo_override`).
        2. SQL wiring 활성 → SqlDividendRepository.
        3. 그 외 → 빈 Fake (배당 사건 0).
    """
    override = getattr(request.app.state, "dividend_repo_override", None)
    if override is not None:
        return override
    if session is not None:
        return SqlDividendRepository(session)
    return FakeDividendRepository(records=())


StocksRepoDep = Annotated[StocksMasterRepository, Depends(get_stocks_repository)]
"""Endpoint type-level dependency hint."""

CitationRepoDep = Annotated[CitationRepository, Depends(get_citation_repository)]

PriceRepoDep = Annotated[PriceRepository, Depends(get_price_repository)]

FinancialRepoDep = Annotated[
    FinancialRepository, Depends(get_financial_repository),
]

CorporateActionRepoDep = Annotated[
    CorporateActionRepository, Depends(get_corporate_action_repository),
]

MarketCapRepoDep = Annotated[
    MarketCapRepository, Depends(get_market_cap_repository),
]

TreasurySharesRepoDep = Annotated[
    TreasurySharesRepository, Depends(get_treasury_shares_repository),
]

MacroIndicatorRepoDep = Annotated[
    MacroIndicatorRepository, Depends(get_macro_indicator_repository),
]

SnapshotRepoDep = Annotated[
    StockSnapshotRepository, Depends(get_stock_snapshot_repository),
]

FactorEvaluatorDep = Annotated[FactorEvaluator, Depends(get_factor_evaluator)]

ActivePackDep = Annotated[LoadedPack, Depends(get_active_pack)]

DartAdapterDep = Annotated[DartAdapter, Depends(get_dart_adapter)]
"""ADR-0026 — 공시목록 on-demand fetch adapter."""

PykrxAdapterOptionalDep = Annotated[
    "PykrxAdapter | None", Depends(get_pykrx_adapter_optional),
]
"""주가 차트 lazy fetch 용 pykrx adapter (미설정=None → lazy 미발동, 테스트 안전)."""

CorpCodeMappingDep = Annotated[
    CorpCodeMapping, Depends(get_corp_code_mapping),
]
"""ADR-0026 — KRX code → DART corp_code 매핑 (route corp_code 해소)."""

FactExtractorDep = Annotated[
    "LlmFactExtractor | None", Depends(get_fact_extractor),
]
"""ADR-0031 D6 — 공시 사실추출기 (미주입=None → route 503, 운영 LLM 미연동 정상)."""

RunsRepoDep = Annotated[ScreenRunRepository, Depends(get_runs_repository)]

WatchlistRepoDep = Annotated[
    WatchlistRepository, Depends(get_watchlist_repository),
]

ScreenerSetRepoDep = Annotated[
    ScreenerSetRepository, Depends(get_screener_set_repository),
]

NotesRepoDep = Annotated[NotesRepository, Depends(get_notes_repository)]

PortfolioRepoDep = Annotated[
    PortfolioRepository, Depends(get_portfolio_repository),
]
"""M3 #4 (ADR-0029 D1) — Portfolio 거래내역 user-scoped repository."""

CustomPackRepoDep = Annotated[
    CustomPackRepository, Depends(get_custom_pack_repository),
]
"""ADR-0022 D9 — Factor Lab 사용자 정의 pack 영속화 repository."""

PublisherRepoDep = Annotated[
    PublisherRepository, Depends(get_publisher_repository),
]
"""M6 #3 (ADR-0034 D4) — 인증 publisher handle claim repository (사칭 차단)."""

DividendRepoDep = Annotated[
    DividendRepository, Depends(get_dividend_repository),
]
"""M7 #1 (ADR-0035 D6) — cash_dividend 이중 PIT repository (total return 재투자 누적)."""

UserRepoDep = Annotated[UserRepository, Depends(get_user_repository)]
"""T68 — auth.get_current_user 가 google_sub → user_id 해소에 사용."""


def get_batch_run_repository(
    request: Request,
    session: _SessionOrNoneDep,
) -> BatchRunRepository:
    """BatchRun repository — SQL/Fake auto-swap (재현 cutoff 해소용).

    reproduce_run 이 snapshot.data_versions 의 krx/dart batch_id 를 BatchCutoff
    로 해소할 때 사용. SQL wiring 활성이면 SqlBatchRunRepository, 비활성이면
    FakeBatchRunRepository (재현 시 None cutoff → 무필터 하위호환).

    우선순위:
        1. 테스트 명시 주입 (`app.state.batch_run_repo_override`).
        2. SQL wiring 활성 → SqlBatchRunRepository.
        3. 그 외 → FakeBatchRunRepository (빈 store — batch_id 미해소 → EXCLUDE_ALL).
    """
    override = getattr(request.app.state, "batch_run_repo_override", None)
    if override is not None:
        return override
    if session is not None:
        return SqlBatchRunRepository(session)
    return FakeBatchRunRepository()


BatchRunRepoDep = Annotated[BatchRunRepository, Depends(get_batch_run_repository)]
