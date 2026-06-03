"""ADR-0022 D9 — custom_packs migration(0017) upgrade/downgrade 격리 검증.

검증:
1. migration 0017 upgrade() → custom_packs 테이블 + 2 인덱스 + UNIQUE 생성.
2. user_id FK → users.id (PRAGMA foreign_keys=ON 환경에서 FK 위반 강제).
3. UNIQUE(user_id, pack_slug, version) — 같은 키 중복 INSERT 거부(append-only).
4. **insert 없음** — upgrade() 가 custom_packs row 를 만들지 않음(시스템 생성 0).
5. **updated_at 컬럼 없음** — append-only immutable 의 DB 차원 확인.
6. downgrade() → 인덱스 + 테이블 제거.

격리 구동 패턴 (test_stock_notes_migration.py 동형):
    전체 alembic 체인은 SQLite 에서 직접 실행 불가(PG-only DDL 포함). users
    테이블(FK 대상)만 raw SQL 로 미리 만든 SQLite 위에서 0017 의 upgrade/
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


def _load_0017_module():  # noqa: ANN202
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260602_0017_custom_packs.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0017", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_0017_upgrade(op: Operations) -> None:
    module = _load_0017_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.upgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _run_0017_downgrade(op: Operations) -> None:
    module = _load_0017_module()
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


def _insert_pack(conn, *, user_id: str, slug: str, version: str) -> str:  # noqa: ANN001
    pack_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    conn.execute(
        text(
            "INSERT INTO custom_packs "
            "(id, user_id, pack_slug, version, content_hash, body, "
            "factor_count, created_at) "
            "VALUES (:id, :uid, :slug, :ver, :h, :body, :fc, :c)"
        ),
        {
            "id": pack_id,
            "uid": user_id,
            "slug": slug,
            "ver": version,
            "h": "sha256:" + "a" * 64,
            "body": "{}",
            "fc": 1,
            "c": now,
        },
    )
    return pack_id


def test_migration_0017_creates_table_and_indexes(
    engine_with_users: Engine,
) -> None:
    """0017 upgrade() → custom_packs 테이블 + 2 인덱스 + UNIQUE."""
    with engine_with_users.connect() as conn:
        assert "custom_packs" not in inspect(conn).get_table_names()

        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0017_upgrade(op)
        conn.commit()

        insp = inspect(conn)
        assert "custom_packs" in insp.get_table_names()

        cols = {c["name"] for c in insp.get_columns("custom_packs")}
        assert {
            "id", "user_id", "pack_slug", "version", "content_hash",
            "body", "factor_count", "created_at",
        } <= cols
        # append-only immutable — updated_at 컬럼 없음.
        assert "updated_at" not in cols

        index_names = {ix["name"] for ix in insp.get_indexes("custom_packs")}
        assert "ix_custom_packs_user_id" in index_names
        assert "ix_custom_packs_user_pack" in index_names


def test_migration_0017_no_rows_inserted(engine_with_users: Engine) -> None:
    """0017 upgrade() 가 custom_packs row 를 만들지 않음 (시스템 생성 0)."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0017_upgrade(op)
        conn.commit()

        count = conn.execute(
            text("SELECT COUNT(*) FROM custom_packs")
        ).scalar()
        assert count == 0


def test_migration_0017_unique_user_slug_version(
    engine_with_users: Engine,
) -> None:
    """UNIQUE(user_id, pack_slug, version) — 같은 키 중복 INSERT 거부."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0017_upgrade(op)
        conn.commit()

        _insert_pack(
            conn, user_id=_SYSTEM_USER_ID, slug="user/p", version="1.0.0",
        )
        conn.commit()

        with pytest.raises(IntegrityError):
            _insert_pack(
                conn, user_id=_SYSTEM_USER_ID, slug="user/p", version="1.0.0",
            )
            conn.commit()


def test_migration_0017_user_id_fk_enforced(
    engine_with_users: Engine,
) -> None:
    """user_id FK → users.id — 존재하지 않는 user 참조 INSERT 는 FK 위반."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0017_upgrade(op)
        conn.commit()

        with pytest.raises(IntegrityError):
            _insert_pack(
                conn,
                user_id="99999999-9999-9999-9999-999999999999",
                slug="user/p",
                version="1.0.0",
            )
            conn.commit()


def test_migration_0017_downgrade_removes_table(
    engine_with_users: Engine,
) -> None:
    """0017 downgrade() → custom_packs 테이블 + 인덱스 제거."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0017_upgrade(op)
        conn.commit()
        assert "custom_packs" in inspect(conn).get_table_names()

        _run_0017_downgrade(op)
        conn.commit()
        assert "custom_packs" not in inspect(conn).get_table_names()
