"""Sync engine + sessionmaker + FastAPI Depends generator.

본 모듈은 SQLAlchemy 2 sync ORM 의 운영 부트스트랩. `create_engine` 으로
PostgreSQL (psycopg3) 또는 SQLite (built-in `sqlite3`) URL 모두 받아들이고,
운영 `main.create_app()` 에서 sessionmaker 를 `app.state.db_sessionmaker` 에
박아 endpoint 의 `Depends(get_session)` 가 그 sessionmaker 로부터 한 request =
한 session 패턴 실현.

설계 결정 (T13):

1. **sync session 선택** — 기존 Repository Protocol (`pit_protocols.py`, 등) 이
   모두 sync `def` 시그니처. Protocol 변경 없이 SQL 구현체 드롭인 위해 sync
   유지. ARCHITECTURE.md §3.1 의 "(Async)" 는 M1+ 합류 시 별도 사이클로 전환.

2. **engine 은 사용자 책임 (lifespan)** — 본 모듈은 factory 만 제공. 운영
   `main.create_app()` 또는 테스트 `conftest` 가 명시적으로 engine 생성·dispose.
   process 종료 시 connection pool flush 보장.

3. **expire_on_commit=False** — commit 후 attribute access 가 추가 IO 를 발동하지
   않도록. ORM ↔ Record dataclass 변환은 commit 후에도 수행 가능해야 함.

4. **`get_session` = generator (yield)** — context manager 패턴. exception 발생
   시 자동 rollback, 정상 종료 시 commit. M0 = endpoint 단위 transaction.

5. **URL scheme 보존** — PostgreSQL 운영: `postgresql+psycopg://`. SQLite 테스트:
   `sqlite:///:memory:` 또는 file 경로. 호출자가 적절한 driver scheme 선택.

관련 ADR:
- ADR-0008 D5 — PIT 강제 (Repository layer 에서 as_of 의무)
- M0_PLAN T13 — PostgreSQL 16 + Alembic
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

__all__ = [
    "create_engine_from_url",
    "create_sessionmaker",
    "get_session",
]


def create_engine_from_url(
    url: str,
    *,
    echo: bool = False,
    pool_size: int = 5,
    max_overflow: int = 10,
) -> Engine:
    """Sync engine factory — dialect-aware pool args.

    Args:
        url: SQLAlchemy DSN — `postgresql+psycopg://user:pass@host:port/db` 또는
            `sqlite:///:memory:`. SQLite 는 connection-per-thread 모델이라 pool
            args (pool_size/max_overflow) 가 default pool class 와 호환되지
            않음 → 본 factory 가 자동 분기.
        echo: SQL 로그 노출 — dev 디버깅용. PROD 는 False.
        pool_size: connection pool base size. SQLite 는 무시.
        max_overflow: pool 초과 시 임시 connection 허용 수. SQLite 는 무시.

    Returns:
        Engine — lifespan 의 stop hook 에서 `engine.dispose()` 필수.

    Note:
        SQLite in-memory `:memory:` 는 connection 별로 별도 DB. 같은 engine 의
        session 들이 공유하려면 conftest 에서 `poolclass=StaticPool` + `connect_
        args={"check_same_thread": False}` 명시 (본 factory 는 default pool 사용).
    """
    # SQLite 는 default SingletonThreadPool 사용 — pool_size/max_overflow 미지원.
    # PostgreSQL 등 다른 dialect 는 QueuePool default — pool args 지원.
    if url.startswith("sqlite"):
        return create_engine(url, echo=echo, future=True)
    return create_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        future=True,
    )


def create_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    """Engine 으로부터 sessionmaker 생성.

    `expire_on_commit=False` — commit 후 ORM ↔ Record 변환이 추가 IO 없이 안전.
    `autoflush=True` — repository 가 명시적 flush 호출 없이 query 가능.
    """
    return sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
        autoflush=True,
    )


def get_session(
    sessionmaker_: sessionmaker[Session],
) -> Iterator[Session]:
    """FastAPI Depends — request 단위 session.

    M0 = 한 request = 한 session = 한 transaction. exception 시 rollback, 정상
    종료 시 commit. read-only endpoint 의 commit 은 no-op.

    Note:
        FastAPI 의 Depends 는 본 factory 의 0-arg wrapper 가 필요 (sessionmaker
        는 `request.app.state.db_sessionmaker` 에서 lookup). `api/dependencies/
        db.py` 가 wrapper 제공 — 본 모듈은 라이브러리.
    """
    with sessionmaker_() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        else:
            # 명시적 commit 안 했으면 자동 commit (M0 한 request = 한 tx).
            session.commit()
