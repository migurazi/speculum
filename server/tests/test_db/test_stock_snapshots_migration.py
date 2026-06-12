"""ADR-0002 D5 — stock_snapshots migration(0022) upgrade/downgrade 격리 검증.

검증:
1. 0022 upgrade() → stock_snapshots 테이블 + index + PK + UNIQUE(id) + FK 생성.
2. citation_id FK → source_citations.id (PRAGMA foreign_keys=ON 에서 위반 강제).
3. PK(stock_code, as_of_date, factor_uuid) 중복 INSERT 거부.
4. UNIQUE(id) 중복 거부.
5. **insert 없음** — upgrade() 가 row 를 만들지 않음(빈 테이블).
6. downgrade() → index + 테이블 제거.

격리 구동 패턴(test_custom_packs_migration.py 동형): 전체 alembic 체인은 SQLite 에서
직접 실행 불가하므로, FK 대상(source_citations)만 raw SQL 로 미리 만든 SQLite 위에서
0022 의 upgrade/downgrade 를 MigrationContext + op 로 직접 구동.
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

_CITATION_ID = "00000000-0000-0000-0000-0000000000c1"


def _load_0022_module():  # noqa: ANN202
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260612_0022_stock_snapshots.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0022", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(op: Operations) -> None:
    module = _load_0022_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.upgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _run_downgrade(op: Operations) -> None:
    module = _load_0022_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.downgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


# FK 대상 — source_citations 최소 스키마(id PK 만 필요).
_CREATE_CITATIONS = """
CREATE TABLE source_citations (
    id BLOB NOT NULL PRIMARY KEY,
    created_at DATETIME NOT NULL
)
"""


@pytest.fixture
def engine_with_citations() -> Iterator[Engine]:
    """source_citations(+ sentinel row) 가 있는 SQLite — FK 강제(PRAGMA ON)."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _rec):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    with engine.connect() as conn:
        conn.execute(text(_CREATE_CITATIONS))
        conn.execute(
            text("INSERT INTO source_citations (id, created_at) VALUES (:id, :ts)"),
            {"id": _CITATION_ID, "ts": datetime.now(UTC).isoformat()},
        )
        conn.commit()
    try:
        yield engine
    finally:
        engine.dispose()


def _insert_snapshot(
    conn, *, code: str, as_of: str, factor: str, snap_id: str,  # noqa: ANN001
    citation: str = _CITATION_ID,
) -> None:
    conn.execute(
        text(
            "INSERT INTO stock_snapshots "
            "(id, stock_code, as_of_date, factor_uuid, value, value_unit, "
            "inputs, citation_id, computed_at, data_versions) "
            "VALUES (:id, :code, :aod, :fac, :val, :unit, :inp, :cit, :ts, :dv)"
        ),
        {
            "id": snap_id, "code": code, "aod": as_of, "fac": factor,
            "val": "12.34", "unit": "ratio", "inp": "{}",
            "cit": citation, "ts": datetime.now(UTC).isoformat(), "dv": "{}",
        },
    )


def test_migration_0022_creates_table_and_index(
    engine_with_citations: Engine,
) -> None:
    with engine_with_citations.connect() as conn:
        assert "stock_snapshots" not in inspect(conn).get_table_names()
        ctx = MigrationContext.configure(conn)
        _run_upgrade(Operations(ctx))
        conn.commit()

        insp = inspect(conn)
        assert "stock_snapshots" in insp.get_table_names()
        # 빈 테이블(insert 0).
        cnt = conn.execute(text("SELECT count(*) FROM stock_snapshots")).scalar()
        assert cnt == 0
        # index 생성 확인.
        index_names = {ix["name"] for ix in insp.get_indexes("stock_snapshots")}
        assert "ix_stock_snapshots_code_asof" in index_names
        # 컬럼 확인.
        cols = {c["name"] for c in insp.get_columns("stock_snapshots")}
        assert {
            "id", "stock_code", "as_of_date", "factor_uuid", "value",
            "value_unit", "inputs", "citation_id", "computed_at", "data_versions",
        } <= cols


def test_migration_0022_fk_and_constraints(
    engine_with_citations: Engine,
) -> None:
    fac = str(uuid4())
    with engine_with_citations.connect() as conn:
        ctx = MigrationContext.configure(conn)
        _run_upgrade(Operations(ctx))
        conn.commit()

        # FK 위반 — 존재하지 않는 citation_id.
        with pytest.raises(IntegrityError):
            _insert_snapshot(
                conn, code="005930", as_of="2024-06-01", factor=fac,
                snap_id=str(uuid4()), citation=str(uuid4()),
            )
        conn.rollback()

        # 정상 insert.
        sid = str(uuid4())
        _insert_snapshot(
            conn, code="005930", as_of="2024-06-01", factor=fac, snap_id=sid,
        )
        conn.commit()

        # PK(stock_code, as_of_date, factor_uuid) 중복 거부(다른 id 라도).
        with pytest.raises(IntegrityError):
            _insert_snapshot(
                conn, code="005930", as_of="2024-06-01", factor=fac,
                snap_id=str(uuid4()),
            )
        conn.rollback()

        # UNIQUE(id) 중복 거부(다른 PK 라도 같은 id).
        with pytest.raises(IntegrityError):
            _insert_snapshot(
                conn, code="000660", as_of="2024-06-01", factor=fac, snap_id=sid,
            )
        conn.rollback()


def test_migration_0022_downgrade_drops_table(
    engine_with_citations: Engine,
) -> None:
    with engine_with_citations.connect() as conn:
        ctx = MigrationContext.configure(conn)
        _run_upgrade(Operations(ctx))
        conn.commit()
        assert "stock_snapshots" in inspect(conn).get_table_names()

        _run_downgrade(Operations(ctx))
        conn.commit()
        assert "stock_snapshots" not in inspect(conn).get_table_names()
