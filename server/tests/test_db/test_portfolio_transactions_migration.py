"""M3 #4 — portfolio_transactions migration(0019) upgrade/downgrade 격리 검증.

검증:
1. migration 0019 upgrade() → portfolio_transactions 테이블 + 2 인덱스 생성.
2. user_id FK → users.id (PRAGMA foreign_keys=ON 환경에서 FK 위반 강제).
3. fee server_default '0' (값 미지정 INSERT).
4. **insert 없음** — upgrade() 가 거래 row 를 만들지 않음(시스템 생성 거래 0).
5. 세금 컬럼 부재(ADR-0029 D6 — 세전만).
6. downgrade() → 인덱스 + 테이블 제거.

격리 구동 패턴 (test_stock_notes_migration.py 동형):
    전체 alembic 체인은 SQLite 에서 직접 실행 불가(PG-only DDL 포함). users
    테이블(FK 대상)만 raw SQL 로 미리 만든 SQLite 위에서 0019 의 upgrade/
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


def _load_0019_module():  # noqa: ANN202
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260602_0019_portfolio_transactions.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0019", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_0019_upgrade(op: Operations) -> None:
    module = _load_0019_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.upgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _run_0019_downgrade(op: Operations) -> None:
    module = _load_0019_module()
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


def test_migration_0019_creates_table_and_indexes(
    engine_with_users: Engine,
) -> None:
    """0019 upgrade() → portfolio_transactions 테이블 + 2 인덱스."""
    with engine_with_users.connect() as conn:
        assert "portfolio_transactions" not in inspect(conn).get_table_names()

        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0019_upgrade(op)
        conn.commit()

        insp = inspect(conn)
        assert "portfolio_transactions" in insp.get_table_names()

        cols = {c["name"] for c in insp.get_columns("portfolio_transactions")}
        assert {
            "id", "user_id", "code_lineage_id", "side", "quantity",
            "unit_price", "trade_date", "fee", "created_at",
        } == cols
        # 세금 컬럼 부재(ADR-0029 D6 — 세전만).
        assert "tax" not in cols

        index_names = {
            ix["name"] for ix in insp.get_indexes("portfolio_transactions")
        }
        assert "ix_portfolio_transactions_user_id" in index_names
        assert "ix_portfolio_transactions_user_code" in index_names


def test_migration_0019_no_rows_inserted(engine_with_users: Engine) -> None:
    """0019 upgrade() 가 거래 row 를 만들지 않음 (시스템 생성 거래 0)."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0019_upgrade(op)
        conn.commit()

        count = conn.execute(
            text("SELECT COUNT(*) FROM portfolio_transactions")
        ).scalar()
        assert count == 0


def test_migration_0019_fee_default(engine_with_users: Engine) -> None:
    """fee server_default '0' — 값 미지정 INSERT 시 자동 채움."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0019_upgrade(op)
        conn.commit()

        tx_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        conn.execute(
            text(
                "INSERT INTO portfolio_transactions "
                "(id, user_id, code_lineage_id, side, quantity, unit_price, "
                "trade_date, created_at) "
                "VALUES (:id, :uid, :code, :side, :qty, :price, :td, :c)"
            ),
            {
                "id": tx_id,
                "uid": _SYSTEM_USER_ID,
                "code": str(uuid4()),
                "side": "buy",
                "qty": 100,
                "price": "1000",
                "td": "2024-01-01",
                "c": now,
            },
        )
        conn.commit()

        fee = conn.execute(
            text("SELECT fee FROM portfolio_transactions WHERE id = :id"),
            {"id": tx_id},
        ).scalar()
        assert str(fee) == "0"


def test_migration_0019_user_id_fk_enforced(engine_with_users: Engine) -> None:
    """user_id FK → users.id — 존재하지 않는 user 참조 INSERT 는 FK 위반."""
    from sqlalchemy.exc import IntegrityError

    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0019_upgrade(op)
        conn.commit()

        now = datetime.now(UTC).isoformat()
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO portfolio_transactions "
                    "(id, user_id, code_lineage_id, side, quantity, "
                    "unit_price, trade_date, fee, created_at) "
                    "VALUES (:id, :uid, :code, :side, :qty, :price, :td, "
                    ":fee, :c)"
                ),
                {
                    "id": str(uuid4()),
                    # users 에 없는 user_id — FK 위반.
                    "uid": "99999999-9999-9999-9999-999999999999",
                    "code": str(uuid4()),
                    "side": "buy",
                    "qty": 1,
                    "price": "1",
                    "td": "2024-01-01",
                    "fee": "0",
                    "c": now,
                },
            )
            conn.commit()


def test_migration_0019_downgrade_removes_table(
    engine_with_users: Engine,
) -> None:
    """0019 downgrade() → portfolio_transactions 테이블 + 인덱스 제거."""
    with engine_with_users.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0019_upgrade(op)
        conn.commit()
        assert "portfolio_transactions" in inspect(conn).get_table_names()

        _run_0019_downgrade(op)
        conn.commit()
        assert "portfolio_transactions" not in inspect(conn).get_table_names()
