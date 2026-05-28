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

from app.repositories.citation_repository import (
    CitationRepository,
    FakeCitationRepository,
    SqlCitationRepository,
)
from app.repositories.screen_run_repository import (
    FakeScreenRunRepository,
    ScreenRunRepository,
)
from app.repositories.sql_repositories import SqlStocksMasterRepository
from app.repositories.sql_user_repositories import (
    SqlScreenerSetRepository,
    SqlScreenRunRepository,
    SqlWatchlistRepository,
)
from app.repositories.stocks_master_repository import (
    FakeStocksMasterRepository,
    StocksMasterRepository,
)
from app.repositories.watchlist_repository import (
    FakeScreenerSetRepository,
    FakeWatchlistRepository,
    ScreenerSetRepository,
    WatchlistRepository,
)
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


def get_factor_evaluator() -> FactorEvaluator:
    """Factor evaluator — state-less, default config."""
    return FactorEvaluator()


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


StocksRepoDep = Annotated[StocksMasterRepository, Depends(get_stocks_repository)]
"""Endpoint type-level dependency hint."""

CitationRepoDep = Annotated[CitationRepository, Depends(get_citation_repository)]

FactorEvaluatorDep = Annotated[FactorEvaluator, Depends(get_factor_evaluator)]

ActivePackDep = Annotated[LoadedPack, Depends(get_active_pack)]

RunsRepoDep = Annotated[ScreenRunRepository, Depends(get_runs_repository)]

WatchlistRepoDep = Annotated[
    WatchlistRepository, Depends(get_watchlist_repository),
]

ScreenerSetRepoDep = Annotated[
    ScreenerSetRepository, Depends(get_screener_set_repository),
]
