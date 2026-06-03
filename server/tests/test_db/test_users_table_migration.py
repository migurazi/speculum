"""M2 T69 — users 테이블 migration(0012) + sentinel + FK 검증 (ADR-0021 D2/D3).

검증:
1. 0012 upgrade() → users 테이블 생성 + system sentinel row(00000000-…-0001).
2. watchlists / screener_sets / screen_runs.user_id 에 FK(→ users.id) 추가.
3. 마이그레이션 후 기존 SYSTEM-owned row 의 user_id 불변 (UPDATE 0건, ADR-0021
   D3 — system-owned 유지).
4. downgrade() → FK 제거 + users 테이블 제거.

migration 0012 격리 구동:
    전체 alembic 체인(0001~0011)은 SQLite 에서 직접 실행 불가 — 0004 등이
    PG-only DDL 포함. 따라서 본 테스트는 user 격리 대상 3 테이블(watchlists /
    screener_sets / screen_runs)을 ORM create_all 로 미리 만든 SQLite 위에서
    0012 의 upgrade/downgrade 를 alembic MigrationContext + op 로 직접 구동.
    FK 추가는 batch_alter_table(SQLite 테이블 재생성)로 처리되므로 SQLite 에서
    실제 FK 제약을 검증할 수 있다.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

# screen_run.py:63 의 SYSTEM_USER_ID 와 동일 — migration 0012 의 sentinel.
_SENTINEL: UUID = UUID("00000000-0000-0000-0000-000000000001")

# user_id FK 가 추가될 3 테이블 + 기대 FK constraint 이름.
_FK_TABLES: tuple[tuple[str, str], ...] = (
    ("watchlists", "fk_watchlists_user_id_users"),
    ("screener_sets", "fk_screener_sets_user_id_users"),
    ("screen_runs", "fk_screen_runs_user_id_users"),
)


@pytest.fixture
def engine_with_user_tables() -> Iterator[Engine]:
    """user 격리 대상 3 테이블만 미리 만든 SQLite — 0012 격리 구동 기반.

    watchlists / screener_sets / screen_runs 를 ORM 으로 생성(users 는 migration
    이 만듦). PRAGMA foreign_keys=ON 으로 FK 제약 실제 강제 — 운영 PG 와 동일.
    """
    from sqlalchemy import event

    from app.db.base import Base
    from app.db.orm.screen_runs import ScreenRunSnapshotORM  # noqa: F401
    from app.db.orm.screener_sets import ScreenerSetORM  # noqa: F401
    from app.db.orm.watchlists import (  # noqa: F401
        WatchlistFolderORM,
        WatchlistItemORM,
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _conn_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # user 격리 대상 테이블만 — users 는 migration upgrade 가 생성.
    WatchlistFolderORM.__table__.create(engine)
    WatchlistItemORM.__table__.create(engine)
    ScreenerSetORM.__table__.create(engine)
    ScreenRunSnapshotORM.__table__.create(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _load_0012_module():  # noqa: ANN202
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260601_0012_users_table_fk.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0012", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(op: Operations, fn_name: str) -> None:
    """0012 의 module-level `from alembic import op` proxy 를 본 op 로 바인딩."""
    module = _load_0012_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        getattr(module, fn_name)()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _seed_system_run(engine: Engine) -> UUID:
    """기존 SYSTEM-owned screen_run row 1 건 삽입 (FK 추가 전 — UPDATE 0 검증용).

    운영에서 이 row 는 0012(FK 추가) 이전부터 존재했다 — users 테이블이 아직
    없던 시점. 그 상태를 재현하기 위해 seed insert 는 PRAGMA foreign_keys=OFF
    로 수행(아직 users 미존재). 0012 가 users+sentinel 을 만든 뒤 FK 가 충족된다.
    """
    run_id = uuid4()
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.execute(
            text(
                "INSERT INTO screen_runs "
                "(id, user_id, query, as_of, result_codes, result_hash, "
                " data_versions, computed_at) VALUES "
                "(:id, :uid, '{}', '2024-05-07', '[]', 'sha256:x', "
                " '{}', '2024-05-07 16:30:00')"
            ),
            {"id": str(run_id), "uid": str(_SENTINEL)},
        )
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    return run_id


# =============================================================================
# 1. upgrade — users 테이블 + sentinel row
# =============================================================================

def test_migration_0012_creates_users_and_sentinel(
    engine_with_user_tables: Engine,
) -> None:
    """0012 upgrade() → users 테이블 + sentinel row(00000000-…-0001)."""
    with engine_with_user_tables.connect() as conn:
        op = Operations(MigrationContext.configure(conn))
        _run(op, "upgrade")
        conn.commit()

        assert "users" in inspect(conn).get_table_names()
        columns = {c["name"] for c in inspect(conn).get_columns("users")}
        assert columns == {"id", "created_at"}

        # sentinel row 존재 — ADR-0021 D3.
        rows = conn.execute(text("SELECT id FROM users")).fetchall()
        assert len(rows) == 1
        assert UUID(str(rows[0][0])) == _SENTINEL


# =============================================================================
# 2. upgrade — user_id FK 추가
# =============================================================================

def test_migration_0012_adds_user_id_fk(
    engine_with_user_tables: Engine,
) -> None:
    """watchlists / screener_sets / screen_runs.user_id 에 FK → users.id."""
    with engine_with_user_tables.connect() as conn:
        op = Operations(MigrationContext.configure(conn))
        _run(op, "upgrade")
        conn.commit()

        inspector = inspect(conn)
        for table, _fk_name in _FK_TABLES:
            fks = inspector.get_foreign_keys(table)
            assert any(
                fk["constrained_columns"] == ["user_id"]
                and fk["referred_table"] == "users"
                for fk in fks
            ), f"{table}.user_id FK → users 누락"


def test_migration_0012_fk_enforced_rejects_unknown_user(
    engine_with_user_tables: Engine,
) -> None:
    """FK 강제 — users 에 없는 user_id 로 screen_run insert 시 IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    with engine_with_user_tables.connect() as conn:
        op = Operations(MigrationContext.configure(conn))
        _run(op, "upgrade")
        conn.commit()

    unknown = uuid4()
    with pytest.raises(IntegrityError):  # noqa: PT012
        with engine_with_user_tables.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO screen_runs "
                    "(id, user_id, query, as_of, result_codes, result_hash, "
                    " data_versions, computed_at) VALUES "
                    "(:id, :uid, '{}', '2024-05-07', '[]', 'sha256:x', "
                    " '{}', '2024-05-07 16:30:00')"
                ),
                {"id": str(uuid4()), "uid": str(unknown)},
            )


# =============================================================================
# 3. 마이그레이션 후 기존 SYSTEM run 의 user_id 불변 (UPDATE 0건 — ADR-0021 D3)
# =============================================================================

def test_migration_0012_preserves_existing_system_user_id(
    engine_with_user_tables: Engine,
) -> None:
    """기존 SYSTEM-owned run 의 user_id 가 마이그레이션 후 sentinel 그대로(UPDATE 0)."""
    run_id = _seed_system_run(engine_with_user_tables)

    with engine_with_user_tables.connect() as conn:
        op = Operations(MigrationContext.configure(conn))
        _run(op, "upgrade")
        conn.commit()

        row = conn.execute(
            text("SELECT user_id FROM screen_runs WHERE id = :id"),
            {"id": str(run_id)},
        ).fetchone()
        assert row is not None
        # user_id 가 sentinel 그대로 — 재귀속/폐기 없음 (system-owned 유지).
        assert UUID(str(row[0])) == _SENTINEL


# =============================================================================
# 4. downgrade — FK 제거 + users 테이블 제거
# =============================================================================

def test_migration_0012_downgrade_drops_users_and_fk(
    engine_with_user_tables: Engine,
) -> None:
    """0012 downgrade() → users 제거 + user_id FK 제거 (격리 대상 테이블 보존)."""
    with engine_with_user_tables.connect() as conn:
        op = Operations(MigrationContext.configure(conn))
        _run(op, "upgrade")
        conn.commit()
        assert "users" in inspect(conn).get_table_names()

        _run(op, "downgrade")
        conn.commit()

        names = inspect(conn).get_table_names()
        assert "users" not in names
        # 격리 대상 테이블은 보존.
        for table, _fk_name in _FK_TABLES:
            assert table in names
            fks = inspect(conn).get_foreign_keys(table)
            assert not any(
                fk["referred_table"] == "users" for fk in fks
            ), f"{table} 의 users FK 가 downgrade 후에도 잔존"
