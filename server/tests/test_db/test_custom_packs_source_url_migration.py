"""ADR-0032 D3 — custom_packs.source_url migration(0020) 격리 검증.

검증:
1. 0020 upgrade() → custom_packs 에 source_url 컬럼 추가(nullable).
2. 기존 row(source_url 미지정)는 NULL 로 보존 — backfill 없음(ADR-0020 정신).
3. 새 row 에 source_url 저장/조회 가능.
4. 0020 downgrade() → source_url 컬럼 제거.

격리 구동 패턴(test_custom_packs_migration.py 0017 동형):
    SQLite 위에 users + 0017 로 custom_packs 를 만든 뒤, 0020 의 upgrade/downgrade
    만 MigrationContext + Operations 로 직접 구동(0018/0019 중간 컬럼과 무관 —
    0020 은 source_url ADD COLUMN 만 수행).
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
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load_module(filename: str, name: str):  # noqa: ANN202
    spec = importlib.util.spec_from_file_location(name, _VERSIONS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(op: Operations, module, direction: str) -> None:  # noqa: ANN001
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        getattr(module, direction)()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


_CREATE_USERS = """
CREATE TABLE users (
    id BLOB NOT NULL PRIMARY KEY,
    created_at DATETIME NOT NULL
)
"""


@pytest.fixture
def engine_with_table() -> Iterator[Engine]:
    """users + 0017 로 custom_packs 까지 만든 SQLite — 0020 ADD COLUMN 대상."""
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
        # 0017 로 custom_packs 생성(0018/0019 컬럼은 0020 과 무관 — 생략).
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run(op, _load_module("20260602_0017_custom_packs.py", "_m0017"), "upgrade")
        conn.commit()
    try:
        yield engine
    finally:
        engine.dispose()


def _insert_pack(conn, *, slug: str) -> str:  # noqa: ANN001
    pack_id = str(uuid4())
    conn.execute(
        text(
            "INSERT INTO custom_packs "
            "(id, user_id, pack_slug, version, content_hash, body, "
            "factor_count, created_at) "
            "VALUES (:id, :uid, :slug, '1.0.0', :h, '{}', 1, :c)"
        ),
        {
            "id": pack_id,
            "uid": _SYSTEM_USER_ID,
            "slug": slug,
            "h": "sha256:" + "a" * 64,
            "c": datetime.now(UTC).isoformat(),
        },
    )
    return pack_id


def _module_0020():  # noqa: ANN202
    return _load_module("20260604_0020_custom_packs_source_url.py", "_m0020")


def test_0020_adds_source_url_column(engine_with_table: Engine) -> None:
    """0020 upgrade() → source_url 컬럼 추가(nullable)."""
    with engine_with_table.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("custom_packs")}
        assert "source_url" not in cols

        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run(op, _module_0020(), "upgrade")
        conn.commit()

        col = next(
            c for c in inspect(conn).get_columns("custom_packs")
            if c["name"] == "source_url"
        )
        assert col["nullable"] is True


def test_0020_preserves_existing_rows_as_null(
    engine_with_table: Engine,
) -> None:
    """기존 row 는 backfill 없이 source_url = NULL 로 보존(ADR-0020)."""
    with engine_with_table.connect() as conn:
        pack_id = _insert_pack(conn, slug="user/old")
        conn.commit()

        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run(op, _module_0020(), "upgrade")
        conn.commit()

        val = conn.execute(
            text("SELECT source_url FROM custom_packs WHERE id = :id"),
            {"id": pack_id},
        ).scalar()
        assert val is None


def test_0020_new_row_can_store_source_url(
    engine_with_table: Engine,
) -> None:
    """upgrade 후 새 row 에 source_url 저장/조회 가능."""
    with engine_with_table.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run(op, _module_0020(), "upgrade")
        conn.commit()

        url = "https://example.com/p.json"
        conn.execute(
            text(
                "INSERT INTO custom_packs "
                "(id, user_id, pack_slug, version, content_hash, body, "
                "factor_count, created_at, source_url) "
                "VALUES (:id, :uid, 'user/new', '1.0.0', :h, '{}', 1, :c, :u)"
            ),
            {
                "id": str(uuid4()),
                "uid": _SYSTEM_USER_ID,
                "h": "sha256:" + "b" * 64,
                "c": datetime.now(UTC).isoformat(),
                "u": url,
            },
        )
        conn.commit()
        got = conn.execute(
            text("SELECT source_url FROM custom_packs WHERE pack_slug='user/new'")
        ).scalar()
        assert got == url


def test_0020_downgrade_removes_column(engine_with_table: Engine) -> None:
    """0020 downgrade() → source_url 컬럼 제거."""
    with engine_with_table.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        module = _module_0020()
        _run(op, module, "upgrade")
        conn.commit()
        assert "source_url" in {
            c["name"] for c in inspect(conn).get_columns("custom_packs")
        }

        _run(op, module, "downgrade")
        conn.commit()
        assert "source_url" not in {
            c["name"] for c in inspect(conn).get_columns("custom_packs")
        }
