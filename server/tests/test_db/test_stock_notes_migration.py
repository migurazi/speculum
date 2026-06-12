"""T76 — stock_notes migration(0014) upgrade/downgrade 격리 검증.

검증:
1. migration 0014 upgrade() → stock_notes 테이블 + 2 인덱스 생성.
2. user_id FK → users.id (PRAGMA foreign_keys=ON 환경에서 FK 위반 강제).
3. scope server_default 'user-private' (값 미지정 INSERT).
4. **insert 없음** — upgrade() 가 stock_notes row 를 만들지 않음(시스템 생성 0).
5. downgrade() → 인덱스 + 테이블 제거.

격리 구동 패턴 (test_security_type_migration.py 동형):
    전체 alembic 체인은 SQLite 에서 직접 실행 불가(PG-only DDL 포함). users
    테이블(FK 대상)만 raw SQL 로 미리 만든 SQLite 위에서 0014 의 upgrade/
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

_SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def _load_0014_module():  # noqa: ANN202
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260602_0014_stock_notes.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0014", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_0014_upgrade(op: Operations) -> None:
    module = _load_0014_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.upgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _run_0014_downgrade(op: Operations) -> None:
    module = _load_0014_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.downgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


# users 테이블 (FK 대상) — 0014 이전에 존재해야 함.
_CREATE_USERS = """
CREATE TABLE users (
    id BLOB NOT NULL PRIMARY KEY,
    created_at DATETIME NOT NULL
)
"""


@pytest.fixture
def engine_with_users() -> Iterator[Engine]:
    """users 테이블(+ sentinel row) 이 있는 SQLite — FK 강제(PRAGMA ON)."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _rec):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    with engine.connect() as conn:
        conn.execute(text(_CREATE_USERS))
        conn.execute(
            text("INSERT INTO users (id, created_at) VALUES (:id, :ts)"),
            {"id": _SYSTEM_USER_ID, "ts": datetime.now(UTC).isoformat()},
        )
        conn.commit()
    try:
        yield engine
    finally:
        engine.dispose()


def test_migration_0014_creates_table_and_indexes(
    engine_with_users: Engine,
) -> None:
    """0014 upgrade() → stock_notes 테이블 + 2 인덱스."""
    with engine_with_users.connect() as conn:
        assert "stock_notes" not in inspect(conn).get_table_names()

        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0014_upgrade(op)
        conn.commit()

        insp = inspect(conn)
        assert "stock_notes" in insp.get_table_names()

        cols = {c["name"] for c in insp.get_columns("stock_notes")}
        assert {
            "id", "user_id", "code_lineage_id", "body", "scope",
            "created_at", "updated_at",
        } <= cols

        index_names = {ix["name"] for ix in insp.get_indexes("stock_notes")}
        assert "ix_stock_notes_user_id" in index_names
        assert "ix_stock_notes_user_code" in index_names


def test_migration_0014_no_rows_inserted(engine_with_users: Engine) -> None:
    """0014 upgrade() 가 stock_notes row 를 만들지 않음 (시스템 생성 Notes 0)."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0014_upgrade(op)
        conn.commit()

        count = conn.execute(
            text("SELECT COUNT(*) FROM stock_notes")
        ).scalar()
        assert count == 0


def test_migration_0014_scope_default(engine_with_users: Engine) -> None:
    """scope server_default 'user-private' — 값 미지정 INSERT 시 자동 채움."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0014_upgrade(op)
        conn.commit()

        note_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        conn.execute(
            text(
                "INSERT INTO stock_notes "
                "(id, user_id, code_lineage_id, body, created_at, updated_at) "
                "VALUES (:id, :uid, :code, :body, :c, :u)"
            ),
            {
                "id": note_id,
                "uid": _SYSTEM_USER_ID,
                "code": str(uuid4()),
                "body": "x",
                "c": now,
                "u": now,
            },
        )
        conn.commit()

        scope = conn.execute(
            text("SELECT scope FROM stock_notes WHERE id = :id"),
            {"id": note_id},
        ).scalar()
        assert scope == "user-private"


def test_migration_0014_user_id_fk_enforced(engine_with_users: Engine) -> None:
    """user_id FK → users.id — 존재하지 않는 user 참조 INSERT 는 FK 위반."""
    from sqlalchemy.exc import IntegrityError

    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0014_upgrade(op)
        conn.commit()

        now = datetime.now(UTC).isoformat()
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO stock_notes "
                    "(id, user_id, code_lineage_id, body, scope, "
                    "created_at, updated_at) "
                    "VALUES (:id, :uid, :code, :body, :s, :c, :u)"
                ),
                {
                    "id": str(uuid4()),
                    # users 에 없는 user_id — FK 위반.
                    "uid": "99999999-9999-9999-9999-999999999999",
                    "code": str(uuid4()),
                    "body": "x",
                    "s": "user-private",
                    "c": now,
                    "u": now,
                },
            )
            conn.commit()


def test_migration_0014_downgrade_removes_table(
    engine_with_users: Engine,
) -> None:
    """0014 downgrade() → stock_notes 테이블 + 인덱스 제거."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0014_upgrade(op)
        conn.commit()
        assert "stock_notes" in inspect(conn).get_table_names()

        _run_0014_downgrade(op)
        conn.commit()
        assert "stock_notes" not in inspect(conn).get_table_names()
