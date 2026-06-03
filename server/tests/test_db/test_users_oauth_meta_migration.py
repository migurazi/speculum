"""T68 — users OAuth 메타 migration(0016) upgrade/downgrade 격리 검증.

ADR-0021 D1.1. migration 0012 의 최소 users(id + created_at)에 google_sub +
email 컬럼을 추가하는 0016 을 SQLite 격리 구동으로 검증한다(test_users_table_
migration.py 패턴).

검증:
1. upgrade() → users 에 google_sub + email 컬럼 + uq_users_google_sub 인덱스.
2. sentinel row 의 google_sub / email = NULL (UPDATE 0건 — ADR-0021 D3).
3. google_sub unique 강제 — 같은 sub 중복 INSERT 시 IntegrityError.
4. google_sub NULL 다중 허용 — NULL row 2 개 INSERT 정상(sentinel + 추가).
5. downgrade() → 컬럼 + 인덱스 제거.

격리 구동:
    전체 alembic 체인은 SQLite 에서 직접 실행 불가. users(id + created_at +
    sentinel row)를 raw SQL 로 미리 만든 SQLite 위에서 0016 의 upgrade/downgrade
    를 MigrationContext + op 로 직접 구동.
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

# screen_run.py:63 의 SYSTEM_USER_ID — 0012 가 insert 한 sentinel.
_SENTINEL = "00000000-0000-0000-0000-000000000001"

# 0012 가 만든 users 의 최소 스키마(google_sub/email 부재) — 0016 이전 상태.
_CREATE_USERS_PRE_0016 = """
CREATE TABLE users (
    id BLOB NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id)
)
"""


def _load_0016_module():  # noqa: ANN202
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260602_0016_users_oauth_meta.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0016", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(op: Operations, fn_name: str) -> None:
    module = _load_0016_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        getattr(module, fn_name)()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


@pytest.fixture
def engine_with_pre_0016_users() -> Iterator[Engine]:
    """0016 이전(0012 상태)의 users 테이블 + sentinel row 가 있는 SQLite."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _rec):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    with engine.connect() as conn:
        conn.execute(text(_CREATE_USERS_PRE_0016))
        # sentinel row insert (google_sub/email 컬럼 부재 — 0012 와 동일).
        conn.execute(
            text("INSERT INTO users (id, created_at) VALUES (:id, :ts)"),
            {"id": _SENTINEL, "ts": datetime.now(UTC).isoformat()},
        )
        conn.commit()
    try:
        yield engine
    finally:
        engine.dispose()


# =============================================================================
# 1. upgrade — google_sub + email 컬럼 + unique 인덱스
# =============================================================================

def test_migration_0016_adds_oauth_columns(
    engine_with_pre_0016_users: Engine,
) -> None:
    """0016 upgrade() → google_sub + email 컬럼 + uq_users_google_sub 인덱스."""
    with engine_with_pre_0016_users.connect() as conn:
        op = Operations(MigrationContext.configure(conn))
        _run(op, "upgrade")
        conn.commit()

        cols = {c["name"] for c in inspect(conn).get_columns("users")}
        assert {"id", "created_at", "google_sub", "email"} == cols

        index_names = {ix["name"] for ix in inspect(conn).get_indexes("users")}
        assert "uq_users_google_sub" in index_names
        # unique 여부.
        uq = next(
            ix for ix in inspect(conn).get_indexes("users")
            if ix["name"] == "uq_users_google_sub"
        )
        # SQLite inspector 는 unique 를 1/0(int)로 반환 — truthy 검사.
        assert uq["unique"]
        assert uq["column_names"] == ["google_sub"]


# =============================================================================
# 2. sentinel row 의 google_sub / email = NULL (UPDATE 0건)
# =============================================================================

def test_migration_0016_sentinel_oauth_meta_null(
    engine_with_pre_0016_users: Engine,
) -> None:
    """sentinel row 의 google_sub / email 이 NULL (UPDATE 0건 — ADR-0021 D3)."""
    with engine_with_pre_0016_users.connect() as conn:
        op = Operations(MigrationContext.configure(conn))
        _run(op, "upgrade")
        conn.commit()

        row = conn.execute(
            text(
                "SELECT google_sub, email FROM users WHERE id = :id"
            ),
            {"id": _SENTINEL},
        ).fetchone()
        assert row is not None
        assert row[0] is None  # google_sub NULL
        assert row[1] is None  # email NULL


# =============================================================================
# 3. google_sub unique 강제
# =============================================================================

def test_migration_0016_google_sub_unique_enforced(
    engine_with_pre_0016_users: Engine,
) -> None:
    """같은 google_sub 중복 INSERT → IntegrityError (unique 강제)."""
    from sqlalchemy.exc import IntegrityError

    with engine_with_pre_0016_users.connect() as conn:
        op = Operations(MigrationContext.configure(conn))
        _run(op, "upgrade")
        conn.commit()

    now = datetime.now(UTC).isoformat()
    with engine_with_pre_0016_users.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, created_at, google_sub, email) "
                "VALUES (:id, :ts, :sub, :email)"
            ),
            {"id": str(uuid4()), "ts": now, "sub": "dup-sub", "email": None},
        )
    with pytest.raises(IntegrityError):  # noqa: PT012
        with engine_with_pre_0016_users.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, created_at, google_sub, email) "
                    "VALUES (:id, :ts, :sub, :email)"
                ),
                {"id": str(uuid4()), "ts": now, "sub": "dup-sub", "email": None},
            )


# =============================================================================
# 4. google_sub NULL 다중 허용
# =============================================================================

def test_migration_0016_google_sub_null_multiple_allowed(
    engine_with_pre_0016_users: Engine,
) -> None:
    """google_sub NULL 인 row 를 여러 개 INSERT 가능 (sentinel 외 추가 NULL)."""
    with engine_with_pre_0016_users.connect() as conn:
        op = Operations(MigrationContext.configure(conn))
        _run(op, "upgrade")
        conn.commit()

    now = datetime.now(UTC).isoformat()
    # sentinel(google_sub NULL) 이미 존재 — 추가 NULL row 2 개 INSERT.
    with engine_with_pre_0016_users.begin() as conn:
        for _ in range(2):
            conn.execute(
                text(
                    "INSERT INTO users (id, created_at, google_sub, email) "
                    "VALUES (:id, :ts, NULL, NULL)"
                ),
                {"id": str(uuid4()), "ts": now},
            )

    with engine_with_pre_0016_users.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM users WHERE google_sub IS NULL")
        ).scalar()
        # sentinel + 신규 2 = 3.
        assert count == 3


# =============================================================================
# 5. downgrade — 컬럼 + 인덱스 제거
# =============================================================================

def test_migration_0016_downgrade_removes_columns(
    engine_with_pre_0016_users: Engine,
) -> None:
    """0016 downgrade() → google_sub + email 컬럼 + 인덱스 제거 (users 보존)."""
    with engine_with_pre_0016_users.connect() as conn:
        op = Operations(MigrationContext.configure(conn))
        _run(op, "upgrade")
        conn.commit()
        cols = {c["name"] for c in inspect(conn).get_columns("users")}
        assert "google_sub" in cols

        _run(op, "downgrade")
        conn.commit()

        cols_after = {c["name"] for c in inspect(conn).get_columns("users")}
        assert cols_after == {"id", "created_at"}
        index_names = {ix["name"] for ix in inspect(conn).get_indexes("users")}
        assert "uq_users_google_sub" not in index_names
        # users 테이블 자체는 보존 + sentinel row 보존.
        rows = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        assert rows == 1
