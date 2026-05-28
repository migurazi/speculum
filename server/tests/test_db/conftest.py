"""DB test fixture — SQLite in-memory engine + create_all schema.

본 conftest 는 `tests/test_db/` 하위 모듈 전용 fixture. SQLAlchemy 2 sync
engine 위에 Base.metadata.create_all 로 5 core 테이블을 즉시 생성하고
function-scoped session 으로 isolation 보장.

설계:
    - **SQLite in-memory + StaticPool** — 같은 engine 의 multiple connection 이
      같은 in-memory DB 를 공유. (default 는 connection 마다 새 DB.)
    - **function scope session** — 테스트마다 fresh schema. fixture cleanup 에서
      `Base.metadata.drop_all` 로 row + 인덱스 모두 제거.
    - **`echo=False`** — pytest output 청결. 디버깅 시 `SPECULUM_SQL_ECHO=1`
      환경변수 hook 으로 enable (M0 backlog).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.orm import (  # noqa: F401  ← Base.metadata 등록 side-effect
    CorporateActionORM,
    FinancialORM,
    PriceDailyORM,
    ScreenerSetORM,
    ScreenRunSnapshotORM,
    SourceCitationORM,
    StocksMasterORM,
    WatchlistFolderORM,
    WatchlistItemORM,
)


@pytest.fixture
def db_engine() -> Iterator[Engine]:
    """SQLite in-memory engine — 같은 engine 의 다중 session 이 같은 DB 공유.

    StaticPool + check_same_thread=False 로 thread 격리 완화. pytest 단일 thread
    가정상 안전.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    # SQLite 는 PRAGMA foreign_keys=ON 이 connection 단위 default OFF — ON 강제
    # 하여 운영 PG 와 동일한 FK behavior (특히 ondelete CASCADE) 보장.
    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _conn_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """function-scoped session — 각 테스트가 격리된 session.

    M0 의 한 request = 한 session 패턴 일관. session 종료 시 rollback (테스트는
    commit 명시 안 하면 row 가 사라짐을 확인).
    """
    Sessionmaker = sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=True)
    with Sessionmaker() as session:
        try:
            yield session
        finally:
            session.rollback()
