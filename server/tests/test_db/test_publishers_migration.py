"""ADR-0034 D4 — publishers migration(0021) upgrade/downgrade 격리 검증.

검증:
1. migration 0021 upgrade() → publishers 테이블 + handle unique Index + UNIQUE.
2. user_id FK → users.id (PRAGMA foreign_keys=ON 환경에서 FK 위반 강제).
3. UNIQUE(user_id) — 같은 user 두 번째 row INSERT 거부(user 1:1).
4. handle unique Index — 같은 handle 두 user INSERT 거부(인스턴스 유일, 사칭 차단).
5. **insert 없음** — upgrade() 가 publishers row 를 만들지 않음(시스템 생성 0).
6. **updated_at 컬럼 없음** — append-only 의 DB 차원 확인.
7. downgrade() → Index + 테이블 제거.

격리 구동 패턴 (test_custom_packs_migration.py 동형):
    전체 alembic 체인은 SQLite 에서 직접 실행 불가(PG-only DDL 포함). users
    테이블(FK 대상)만 raw SQL 로 미리 만든 SQLite 위에서 0021 의 upgrade/
    downgrade 를 MigrationContext + op 로 직접 구동.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

_SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
_USER_A = "00000000-0000-0000-0000-00000000000a"


def _load_0021_module():  # noqa: ANN202
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260605_0021_publishers.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0021", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_0021_upgrade(op: Operations) -> None:
    module = _load_0021_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.upgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _run_0021_downgrade(op: Operations) -> None:
    module = _load_0021_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.downgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


_CREATE_USERS = """
CREATE TABLE users (
    id BLOB NOT NULL PRIMARY KEY,
    created_at DATETIME NOT NULL
)
"""


@pytest.fixture
def engine_with_users() -> Iterator[Engine]:
    """users 테이블(+ 표준 user row) 이 있는 SQLite — FK 강제(PRAGMA ON)."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _rec):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    with engine.connect() as conn:
        conn.execute(text(_CREATE_USERS))
        now = datetime.now(UTC).isoformat()
        for uid in (_SYSTEM_USER_ID, _USER_A):
            conn.execute(
                text("INSERT INTO users (id, created_at) VALUES (:id, :ts)"),
                {"id": uid, "ts": now},
            )
        conn.commit()
    try:
        yield engine
    finally:
        engine.dispose()


def _insert_publisher(conn, *, user_id: str, handle: str) -> str:  # noqa: ANN001
    pub_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    conn.execute(
        text(
            "INSERT INTO publishers (id, user_id, handle, created_at) "
            "VALUES (:id, :uid, :h, :c)"
        ),
        {"id": pub_id, "uid": user_id, "h": handle, "c": now},
    )
    return pub_id


def test_migration_0021_creates_table_and_index(
    engine_with_users: Engine,
) -> None:
    """0021 upgrade() → publishers 테이블 + handle unique Index + UNIQUE(user_id)."""
    with engine_with_users.connect() as conn:
        assert "publishers" not in inspect(conn).get_table_names()

        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0021_upgrade(op)
        conn.commit()

        insp = inspect(conn)
        assert "publishers" in insp.get_table_names()

        cols = {c["name"] for c in insp.get_columns("publishers")}
        assert {"id", "user_id", "handle", "created_at"} <= cols
        # append-only — updated_at 컬럼 없음.
        assert "updated_at" not in cols

        index_names = {ix["name"] for ix in insp.get_indexes("publishers")}
        assert "uq_publishers_handle" in index_names


def test_migration_0021_no_rows_inserted(engine_with_users: Engine) -> None:
    """0021 upgrade() 가 publishers row 를 만들지 않음 (시스템 생성 0)."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0021_upgrade(op)
        conn.commit()

        count = conn.execute(
            text("SELECT COUNT(*) FROM publishers")
        ).scalar()
        assert count == 0


def test_migration_0021_unique_user_id(engine_with_users: Engine) -> None:
    """UNIQUE(user_id) — 같은 user 두 번째 row INSERT 거부(user 1:1)."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0021_upgrade(op)
        conn.commit()

        _insert_publisher(conn, user_id=_USER_A, handle="alice")
        conn.commit()

        with pytest.raises(IntegrityError):
            _insert_publisher(conn, user_id=_USER_A, handle="alice2")
            conn.commit()


def test_migration_0021_unique_handle(engine_with_users: Engine) -> None:
    """handle unique Index — 같은 handle 두 user INSERT 거부(인스턴스 유일)."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0021_upgrade(op)
        conn.commit()

        _insert_publisher(conn, user_id=_USER_A, handle="alice")
        conn.commit()

        with pytest.raises(IntegrityError):
            _insert_publisher(conn, user_id=_SYSTEM_USER_ID, handle="alice")
            conn.commit()


def test_migration_0021_user_id_fk_enforced(engine_with_users: Engine) -> None:
    """user_id FK → users.id — 존재하지 않는 user 참조 INSERT 는 FK 위반."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0021_upgrade(op)
        conn.commit()

        with pytest.raises(IntegrityError):
            _insert_publisher(
                conn,
                user_id="99999999-9999-9999-9999-999999999999",
                handle="ghost",
            )
            conn.commit()


def test_migration_0021_downgrade_removes_table(
    engine_with_users: Engine,
) -> None:
    """0021 downgrade() → publishers 테이블 + Index 제거."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0021_upgrade(op)
        conn.commit()
        assert "publishers" in inspect(conn).get_table_names()

        _run_0021_downgrade(op)
        conn.commit()
        assert "publishers" not in inspect(conn).get_table_names()
