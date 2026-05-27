"""T13 wiring 통합 테스트 — SQL/Fake auto-swap + lifespan + endpoint flow.

본 모듈은 T13 wiring 사이클의 acceptance test:
1. `database_url=sqlite:///:memory:` 명시 시 create_app 의 lifespan 이
   engine + sessionmaker 를 `app.state` 에 등록.
2. SQL repo 가 endpoint 의 dependency 로 자동 주입됨 (Fake 명시 주입 없을 때).
3. 명시 `stocks_repository=` 주입 시 SQL wiring 활성이어도 명시 Fake 우선.
4. Fake-only mode (no DB URL, no override) 시 빈 Fake fallback.
5. session lifecycle — endpoint 종료 시 자동 close (resource leak 0).

테스트 자체는 SQLite in-memory + Base.metadata.create_all 로 fresh schema.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Final
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.converters import stocks_master_record_to_orm
from app.db.orm import (  # noqa: F401  ← Base.metadata 등록 side-effect
    CorporateActionORM,
    FinancialORM,
    PriceDailyORM,
    SourceCitationORM,
    StocksMasterORM,
)
from app.main import create_app
from app.repositories.pit_protocols import CodeHistoryEntry, StockMasterRecord
from app.repositories.sql_repositories import SqlStocksMasterRepository
from app.repositories.stocks_master_repository import (
    FakeStocksMasterRepository,
)


_SAMSUNG: Final[StockMasterRecord] = StockMasterRecord(
    id=UUID("00000000-0000-0000-0000-000000000001"),
    current_code="005930",
    current_name="삼성전자",
    market="KOSPI",
    listing_date=date(1975, 6, 11),
    delisting_date=None,
    fiscal_month=12,
    code_history=(
        CodeHistoryEntry(
            code="005930",
            valid_from=date(1975, 6, 11),
            valid_to=None,
            reason="initial_listing",
        ),
    ),
)


# =============================================================================
# Fixture — SQLite in-memory + 미리 seed 된 종목 1개
# =============================================================================

@pytest.fixture
def sqlite_engine_with_samsung() -> Iterator[Engine]:
    """Module-private SQLite in-memory + Samsung 종목 1건 seed."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    # seed Samsung — SqlStocksMasterRepository 가 fetch 가능하도록.
    Sm = sessionmaker(bind=engine, expire_on_commit=False)
    with Sm() as session:
        session.add(stocks_master_record_to_orm(_SAMSUNG))
        session.commit()
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


# =============================================================================
# 1. lifespan 이 SQL wiring 활성화 (database_url 명시)
# =============================================================================

def test_lifespan_registers_sessionmaker_when_url_provided() -> None:
    """`database_url` 명시 시 lifespan startup 이 engine + sessionmaker 등록.

    oracle 리뷰 M1 — shutdown 후 state 는 reset 하지 않음 (race 회피). engine
    객체는 그대로지만 dispose() 호출 → pool 의 connection 들 close.
    """
    app = create_app(database_url="sqlite:///:memory:")
    with TestClient(app):
        # TestClient context entry triggers lifespan startup.
        assert app.state.db_engine is not None
        assert app.state.db_sessionmaker is not None
        engine_before = app.state.db_engine
    # context exit triggers shutdown — engine.dispose() 호출. state 자체는 유지
    # (race 회피 정책 — M1 fix). dispose 된 engine 으로 새 session 생성 시 SA
    # 가 raise.
    assert app.state.db_engine is engine_before  # 객체는 그대로


def test_lifespan_skip_when_url_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """URL 미주입 + env var 미설정 → Fake-only mode (db_sessionmaker = None)."""
    monkeypatch.delenv("SPECULUM_DATABASE_URL", raising=False)
    app = create_app()
    with TestClient(app):
        assert app.state.db_engine is None
        assert app.state.db_sessionmaker is None


def test_lifespan_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """`SPECULUM_DATABASE_URL` env var 가 명시 인자 없을 때 fallback."""
    monkeypatch.setenv("SPECULUM_DATABASE_URL", "sqlite:///:memory:")
    app = create_app()
    with TestClient(app):
        assert app.state.db_sessionmaker is not None


def test_explicit_url_overrides_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """명시 `database_url` 인자가 env var 보다 우선 — 테스트 격리 보장."""
    # env var 는 invalid URL 로 — 명시 인자가 우선이면 invalid 가 사용되지 않음.
    monkeypatch.setenv("SPECULUM_DATABASE_URL", "postgresql://invalid")
    app = create_app(database_url="sqlite:///:memory:")
    # lifespan startup 이 명시 URL 로 engine 생성. invalid env URL 무시.
    with TestClient(app):
        engine = app.state.db_engine
        assert engine is not None
        assert "sqlite" in str(engine.url)


# =============================================================================
# 2. SQL repository auto-swap — endpoint 가 실제 SQL 경로로 응답
# =============================================================================

def test_endpoint_uses_sql_repository_when_url_provided(
    sqlite_engine_with_samsung: Engine,
) -> None:
    """SQL wiring 활성 + override 없음 → SqlStocksMasterRepository 사용.

    실제 endpoint 응답이 fixture seed 된 종목을 반환하면 SQL 경로 검증.
    """
    # create_app 의 lifespan 이 자체 engine 생성 — sessionmaker 만 patching 으로
    # 미리 seed 된 engine 위에 sessionmaker 부착.
    Sm = sessionmaker(bind=sqlite_engine_with_samsung, expire_on_commit=False)
    app = create_app()
    # state 직접 설정 (lifespan 거치지 않고 SQL wiring 시뮬레이션).
    app.state.db_engine = sqlite_engine_with_samsung
    app.state.db_sessionmaker = Sm

    with TestClient(app) as client:
        # lifespan startup 후 우리가 설정한 state 가 유지되도록 다시 set
        # (lifespan startup 이 None 으로 reset 가능).
        app.state.db_engine = sqlite_engine_with_samsung
        app.state.db_sessionmaker = Sm

        res = client.get("/api/stocks/005930?as_of=2024-05-07")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["code"] == "005930"
        assert body["name"] == "삼성전자"


def test_endpoint_falls_back_to_empty_fake_when_no_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """URL 도 override 도 없으면 빈 Fake fallback → 404."""
    monkeypatch.delenv("SPECULUM_DATABASE_URL", raising=False)
    app = create_app()
    with TestClient(app) as client:
        res = client.get("/api/stocks/005930?as_of=2024-05-07")
        assert res.status_code == 404  # 빈 Fake 라 종목 없음


def test_override_takes_priority_over_sql_wiring(
    sqlite_engine_with_samsung: Engine,
) -> None:
    """명시 `stocks_repository=Fake` 가 SQL wiring 보다 우선.

    SQL 에 Samsung seed 있지만 명시 override 의 빈 Fake → 404.
    """
    empty_fake = FakeStocksMasterRepository(records=())
    Sm = sessionmaker(bind=sqlite_engine_with_samsung, expire_on_commit=False)

    app = create_app(stocks_repository=empty_fake)
    app.state.db_engine = sqlite_engine_with_samsung
    app.state.db_sessionmaker = Sm

    with TestClient(app) as client:
        app.state.db_sessionmaker = Sm  # lifespan 후 재set
        app.state.stocks_repo_override = empty_fake  # ensure override sticky

        res = client.get("/api/stocks/005930?as_of=2024-05-07")
        # 명시 Fake 가 빈 records → 404.
        assert res.status_code == 404


# =============================================================================
# 3. get_db_session_or_none — Fake-only mode 에서 None yield
# =============================================================================

def test_get_db_session_yields_none_in_fake_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fake-only mode 에서 dependency 가 None yield + endpoint 동작 그대로."""
    monkeypatch.delenv("SPECULUM_DATABASE_URL", raising=False)
    # 빈 Fake — search 의 빈 결과 정상.
    app = create_app(stocks_repository=FakeStocksMasterRepository(records=()))
    with TestClient(app) as client:
        res = client.get("/api/stocks/search?q=삼성&as_of=2024-05-07")
        assert res.status_code == 200
        assert res.json()["items"] == []


# =============================================================================
# 4. session leak 검증 — endpoint 호출 후 cleanup
# =============================================================================

def test_session_commits_writes_on_success(
    sqlite_engine_with_samsung: Engine,
) -> None:
    """oracle 리뷰 C1 회귀 — endpoint 정상 종료 시 session 의 write 가 commit.

    `get_db_session_or_none` 의 `else: commit` path 검증. SqlCitationRepository
    의 save 가 add+flush 만 호출하므로 commit 없으면 close 시 rollback → 데이터
    영구 손실. 본 test 가 commit 작동을 검증.
    """
    from app.api.dependencies.repositories import get_db_session_or_none
    from app.repositories.citation_repository import SqlCitationRepository
    from app.models.source_citation import SourceCitation, SourceKind
    from datetime import datetime, timezone
    from uuid import uuid4

    Sm = sessionmaker(bind=sqlite_engine_with_samsung, expire_on_commit=False)

    # generator dependency 를 직접 driver 처럼 사용 — endpoint lifecycle 시뮬레이션.
    from types import SimpleNamespace
    mock_request_a = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(db_sessionmaker=Sm)),
    )

    gen = get_db_session_or_none(mock_request_a)  # type: ignore[arg-type]
    session = next(gen)
    assert session is not None

    cid = uuid4()
    citation = SourceCitation(
        id=cid,
        source=SourceKind.DART,
        identifier="commit_test",
        retrieved_at=datetime(2024, 5, 20, 9, 0, tzinfo=timezone.utc),
        effective_date=date(2024, 5, 20),
        adapter_version="1.0.0",
        batch_id=uuid4(),
        url=None,
        created_at=datetime(2024, 5, 20, 9, 0, tzinfo=timezone.utc),
    )
    SqlCitationRepository(session).save(citation)
    # generator 종료 — `else: commit` + `finally: close`.
    with pytest.raises(StopIteration):
        next(gen)

    # 새 session 으로 fetch — commit 됐으면 보임.
    with Sm() as verify_session:
        fetched = SqlCitationRepository(verify_session).fetch_by_id(cid)
        assert fetched is not None
        assert fetched.id == cid


def test_session_rolls_back_writes_on_exception(
    sqlite_engine_with_samsung: Engine,
) -> None:
    """oracle 리뷰 C1 회귀 — endpoint 에서 exception 발생 시 write 가 rollback.

    `get_db_session_or_none` 의 `except: rollback` path 검증.
    """
    from app.api.dependencies.repositories import get_db_session_or_none
    from app.repositories.citation_repository import SqlCitationRepository
    from app.models.source_citation import SourceCitation, SourceKind
    from datetime import datetime, timezone
    from uuid import uuid4

    Sm = sessionmaker(bind=sqlite_engine_with_samsung, expire_on_commit=False)

    from types import SimpleNamespace
    mock_request_b = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(db_sessionmaker=Sm)),
    )

    gen = get_db_session_or_none(mock_request_b)  # type: ignore[arg-type]
    session = next(gen)
    assert session is not None

    cid = uuid4()
    citation = SourceCitation(
        id=cid,
        source=SourceKind.DART,
        identifier="rollback_test",
        retrieved_at=datetime(2024, 5, 20, 9, 0, tzinfo=timezone.utc),
        effective_date=date(2024, 5, 20),
        adapter_version="1.0.0",
        batch_id=uuid4(),
        url=None,
        created_at=datetime(2024, 5, 20, 9, 0, tzinfo=timezone.utc),
    )
    SqlCitationRepository(session).save(citation)
    # exception 주입 — generator 가 rollback 후 raise.
    with pytest.raises(RuntimeError, match="boom"):
        gen.throw(RuntimeError("boom"))

    # 새 session 으로 fetch — rollback 됐으면 안 보임.
    with Sm() as verify_session:
        fetched = SqlCitationRepository(verify_session).fetch_by_id(cid)
        assert fetched is None


def test_multiple_requests_share_engine_no_leak(
    sqlite_engine_with_samsung: Engine,
) -> None:
    """여러 request 처리 후에도 endpoint 가 정상 응답 + engine 재사용.

    Session 누수가 있으면 StaticPool 의 단일 connection 이 stuck — 다음 request
    가 hang 또는 fail. 정상 cleanup 검증.
    """
    Sm = sessionmaker(bind=sqlite_engine_with_samsung, expire_on_commit=False)
    app = create_app()
    app.state.db_engine = sqlite_engine_with_samsung
    app.state.db_sessionmaker = Sm

    with TestClient(app) as client:
        app.state.db_sessionmaker = Sm

        # 5 회 연속 request — leak 있으면 두번째 부터 stuck.
        for _ in range(5):
            res = client.get("/api/stocks/005930?as_of=2024-05-07")
            assert res.status_code == 200
            assert res.json()["code"] == "005930"
