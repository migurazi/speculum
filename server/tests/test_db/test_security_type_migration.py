"""ADR-0023 D2 — security_type migration(0013) + ORM + Record round-trip 검증.

검증:
1. migration 0013 upgrade() → stocks_master 에 security_type 컬럼 추가.
2. 기존 row 가 server_default 'common' 으로 backfill 됨.
3. 값 미지정 INSERT → 'common' default.
4. downgrade() → security_type 컬럼 제거.
5. ORM create_all 의 stocks_master 컬럼 set 이 security_type 포함.
6. ORM ↔ StockMasterRecord round-trip 에서 security_type 보존.
7. validate_security_type — 미지원 값 거부.
8. list_active 반환 Record 에 security_type 포함 (전 종목 common).

migration 0013 격리 구동 패턴 (test_treasury_shares_migration.py 패턴):
    전체 alembic 체인은 SQLite 에서 직접 실행 불가 (0004 등 PG-only DDL).
    source_citations (FK 대상) 만 ORM create_all 로 미리 만든 SQLite 위에서
    0013 의 upgrade/downgrade 를 MigrationContext + op 로 직접 구동.
    stocks_master 는 0013 이전 스키마(security_type 없는)를 raw SQL 로 생성.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

# =============================================================================
# migration 0013 모듈 로드 helper
# =============================================================================

def _load_0013_module():  # noqa: ANN202
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260601_0013_security_type.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0013", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_0013_upgrade(op: Operations) -> None:
    module = _load_0013_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.upgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _run_0013_downgrade(op: Operations) -> None:
    module = _load_0013_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.downgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


# stocks_master 의 0013 이전(security_type 없음) 최소 스키마.
# 기존 컬럼(id, current_code, current_name, market, listing_date, delisting_date,
# fiscal_month, code_history, ifrs_preference_default)만 포함.
_CREATE_STOCKS_MASTER_PRE_0013 = """
CREATE TABLE stocks_master (
    id BLOB NOT NULL PRIMARY KEY,
    current_code VARCHAR(12),
    current_name VARCHAR(200) NOT NULL,
    market VARCHAR(16) NOT NULL,
    listing_date DATE NOT NULL,
    delisting_date DATE,
    fiscal_month INTEGER NOT NULL,
    code_history JSON NOT NULL,
    ifrs_preference_default VARCHAR(16) NOT NULL DEFAULT 'AUTO'
)
"""


# =============================================================================
# fixture
# =============================================================================

@pytest.fixture
def engine_pre_0013() -> Iterator[Engine]:
    """stocks_master (security_type 없는 구 스키마) 가 있는 SQLite."""
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text(_CREATE_STOCKS_MASTER_PRE_0013))
        conn.commit()
    try:
        yield engine
    finally:
        engine.dispose()


# =============================================================================
# migration 0013 테스트
# =============================================================================

def test_migration_0013_upgrade_adds_security_type(
    engine_pre_0013: Engine,
) -> None:
    """0013 upgrade() → stocks_master 에 security_type 컬럼 추가."""
    with engine_pre_0013.connect() as conn:
        # 0013 이전: security_type 없음.
        cols_before = {c["name"] for c in inspect(conn).get_columns("stocks_master")}
        assert "security_type" not in cols_before

        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0013_upgrade(op)
        conn.commit()

        cols_after = {c["name"] for c in inspect(conn).get_columns("stocks_master")}
        assert "security_type" in cols_after

        # NOT NULL 검증 — SQLite inspect 에서 nullable=False 확인.
        col_meta = {c["name"]: c for c in inspect(conn).get_columns("stocks_master")}
        assert col_meta["security_type"]["nullable"] is False


def test_migration_0013_backfill_existing_rows(
    engine_pre_0013: Engine,
) -> None:
    """0013 upgrade() → 기존 row 의 security_type 이 'common' 으로 backfill."""
    # 0013 이전에 row 삽입.
    lineage_id = uuid4()
    with engine_pre_0013.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO stocks_master "
                "(id, current_code, current_name, market, listing_date, "
                "fiscal_month, code_history) "
                "VALUES (:id, :code, :name, :market, :ld, :fm, :ch)"
            ),
            {
                "id": str(lineage_id),
                "code": "005930",
                "name": "삼성전자",
                "market": "KOSPI",
                "ld": "1975-06-11",
                "fm": 12,
                "ch": "[]",
            },
        )
        conn.commit()

        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0013_upgrade(op)
        conn.commit()

        row = conn.execute(
            text("SELECT security_type FROM stocks_master WHERE current_code = '005930'")
        ).fetchone()
        assert row is not None
        assert row[0] == "common"


def test_migration_0013_default_on_new_insert(
    engine_pre_0013: Engine,
) -> None:
    """0013 upgrade() 이후 값 미지정 INSERT → security_type = 'common' default."""
    with engine_pre_0013.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0013_upgrade(op)
        conn.commit()

        new_id = uuid4()
        conn.execute(
            text(
                "INSERT INTO stocks_master "
                "(id, current_code, current_name, market, listing_date, "
                "fiscal_month, code_history) "
                "VALUES (:id, :code, :name, :market, :ld, :fm, :ch)"
            ),
            {
                "id": str(new_id),
                "code": "000660",
                "name": "SK하이닉스",
                "market": "KOSPI",
                "ld": "1996-12-26",
                "fm": 12,
                "ch": "[]",
            },
        )
        conn.commit()

        row = conn.execute(
            text("SELECT security_type FROM stocks_master WHERE current_code = '000660'")
        ).fetchone()
        assert row is not None
        assert row[0] == "common"


def test_migration_0013_downgrade_removes_security_type(
    engine_pre_0013: Engine,
) -> None:
    """0013 downgrade() → security_type 컬럼 제거."""
    with engine_pre_0013.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0013_upgrade(op)
        conn.commit()
        assert "security_type" in {
            c["name"] for c in inspect(conn).get_columns("stocks_master")
        }

        _run_0013_downgrade(op)
        conn.commit()
        assert "security_type" not in {
            c["name"] for c in inspect(conn).get_columns("stocks_master")
        }


def test_orm_create_all_includes_security_type() -> None:
    """ORM create_all 의 stocks_master 컬럼 set 에 security_type 포함 (drift 차단)."""
    from app.db import orm  # noqa: F401  ← Base.metadata 등록 side-effect
    from app.db.base import Base

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        cols = {c["name"] for c in inspect(engine).get_columns("stocks_master")}
        assert "security_type" in cols
    finally:
        engine.dispose()


# =============================================================================
# domain validate_security_type
# =============================================================================

def test_validate_security_type_accepts_valid_values() -> None:
    """validate_security_type — 허용값 4 종 통과."""
    from app.db.orm.stocks_master import validate_security_type

    for v in ("common", "preferred", "etf", "reit"):
        assert validate_security_type(v) == v


def test_validate_security_type_rejects_invalid() -> None:
    """validate_security_type — 미지원 값 ValueError raise."""
    from app.db.orm.stocks_master import validate_security_type

    with pytest.raises(ValueError, match="security_type 허용값 외"):
        validate_security_type("unknown")

    with pytest.raises(ValueError, match="security_type 허용값 외"):
        validate_security_type("COMMON")  # 대문자 거부

    with pytest.raises(ValueError, match="security_type 허용값 외"):
        validate_security_type("")


# =============================================================================
# ORM ↔ StockMasterRecord round-trip
# =============================================================================

def test_stocks_master_converter_round_trip_preserves_security_type(
    db_engine: Engine,
) -> None:
    """ORM → Record → ORM 변환 시 security_type 보존 (round-trip 무손실)."""
    from app.db.converters import stocks_master_orm_to_record, stocks_master_record_to_orm
    from app.repositories.pit_protocols import CodeHistoryEntry, StockMasterRecord

    lineage_id = uuid4()
    for sec_type in ("common", "preferred", "etf", "reit"):
        record = StockMasterRecord(
            id=lineage_id,
            current_code="005930",
            current_name="삼성전자",
            market="KOSPI",
            listing_date=date(1975, 6, 11),
            delisting_date=None,
            fiscal_month=12,
            code_history=(
                CodeHistoryEntry(
                    code="005930",
                    valid_from=date(1975, 6, 11),
                    valid_to=None,
                    reason="initial_listing",
                ),
            ),
            ifrs_preference_default="AUTO",
            security_type=sec_type,
        )

        # Record → ORM → Record round-trip.
        orm = stocks_master_record_to_orm(record)
        assert orm.security_type == sec_type

        restored = stocks_master_orm_to_record(orm)
        assert restored.security_type == sec_type


# =============================================================================
# list_active 반환 Record 에 security_type 포함
# =============================================================================

def test_list_active_returns_security_type(db_engine: Engine) -> None:
    """list_active 반환 StockMasterRecord 에 security_type 이 포함됨 (전 종목 common)."""
    from app.db.converters import stocks_master_record_to_orm
    from app.repositories.pit_protocols import CodeHistoryEntry, StockMasterRecord
    from app.repositories.sql_repositories import SqlStocksMasterRepository

    lineage_id = uuid4()
    as_of = date(2024, 6, 28)

    record = StockMasterRecord(
        id=lineage_id,
        current_code="005930",
        current_name="삼성전자",
        market="KOSPI",
        listing_date=date(1975, 6, 11),
        delisting_date=None,
        fiscal_month=12,
        code_history=(
            CodeHistoryEntry(
                code="005930",
                valid_from=date(1975, 6, 11),
                valid_to=None,
                reason="initial_listing",
            ),
        ),
        ifrs_preference_default="AUTO",
        security_type="common",
    )

    Sessionmaker = sessionmaker(bind=db_engine, expire_on_commit=False)
    with Sessionmaker() as session:
        session.add(stocks_master_record_to_orm(record))
        session.commit()

        repo = SqlStocksMasterRepository(session)
        active = repo.list_active(as_of=as_of)

    assert len(active) == 1
    assert active[0].security_type == "common"


# =============================================================================
# V-M2-4 — security_type CHECK constraint (m2-conformance-review)
# =============================================================================

def _load_0015_module():  # noqa: ANN202
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260602_0015_security_type_check.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0015", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_module(op: Operations, module, direction: str) -> None:  # noqa: ANN001
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        getattr(module, direction)()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _insert_stock_sql(conn, *, code: str, security_type: str) -> None:  # noqa: ANN001
    conn.execute(
        text(
            "INSERT INTO stocks_master "
            "(id, current_code, current_name, market, listing_date, "
            "fiscal_month, code_history, security_type) "
            "VALUES (:id, :code, :name, 'KOSPI', '2020-01-01', 12, '[]', :st)"
        ),
        {"id": str(uuid4()), "code": code, "name": "테스트", "st": security_type},
    )


def test_orm_check_rejects_invalid_security_type(db_engine: Engine) -> None:
    """ORM CHECK constraint — 미지원 security_type INSERT → IntegrityError (V-M2-4).

    application validate_security_type 우회(배치 직접 INSERT 등)를 DB 가 거부.
    create_all DB 에 적용된 CheckConstraint(__table_args__) 검증.
    """
    from sqlalchemy.exc import IntegrityError

    from app.db.orm.stocks_master import StocksMasterORM

    Sessionmaker = sessionmaker(bind=db_engine, expire_on_commit=False)
    with Sessionmaker() as session:
        session.add(StocksMasterORM(
            id=uuid4(), current_code="111111", current_name="가짜자산군",
            market="KOSPI", listing_date=date(2020, 1, 1), fiscal_month=12,
            code_history=[], ifrs_preference_default="AUTO",
            security_type="bond",  # 미지원 → CHECK 위반.
        ))
        with pytest.raises(IntegrityError):
            session.commit()


def test_orm_check_accepts_all_valid_security_types(db_engine: Engine) -> None:
    """ORM CHECK constraint — 허용 4 종(common/preferred/etf/reit) 모두 통과."""
    from app.db.orm.stocks_master import StocksMasterORM

    Sessionmaker = sessionmaker(bind=db_engine, expire_on_commit=False)
    with Sessionmaker() as session:
        for i, st in enumerate(("common", "preferred", "etf", "reit")):
            session.add(StocksMasterORM(
                id=uuid4(), current_code=f"22000{i}", current_name="유효자산군",
                market="KOSPI", listing_date=date(2020, 1, 1), fiscal_month=12,
                code_history=[], ifrs_preference_default="AUTO",
                security_type=st,
            ))
        session.commit()  # CHECK 위반 0 — 4 종 모두 삽입 성공.
        count = session.execute(
            text("SELECT COUNT(*) FROM stocks_master")
        ).scalar()
    assert count == 4


def test_migration_0015_check_rejects_invalid(engine_pre_0013: Engine) -> None:
    """0013(security_type 컬럼) + 0015(CHECK) upgrade 후 미지원 값 INSERT 거부."""
    from sqlalchemy.exc import IntegrityError

    with engine_pre_0013.connect() as conn:
        # 0013: security_type 컬럼 추가.
        _run_module(Operations(MigrationContext.configure(conn)),
                    _load_0013_module(), "upgrade")
        conn.commit()
        # 0015: CHECK constraint 추가(batch_alter_table 재생성).
        _run_module(Operations(MigrationContext.configure(conn)),
                    _load_0015_module(), "upgrade")
        conn.commit()

        # 유효 값은 통과.
        _insert_stock_sql(conn, code="330590", security_type="reit")
        conn.commit()

        # 미지원 값은 CHECK 위반 → IntegrityError.
        with pytest.raises(IntegrityError):
            _insert_stock_sql(conn, code="999999", security_type="bond")
            conn.commit()


def test_migration_0015_downgrade_drops_check(engine_pre_0013: Engine) -> None:
    """0015 downgrade → CHECK 제거 (미지원 값 INSERT 가 다시 통과)."""
    with engine_pre_0013.connect() as conn:
        _run_module(Operations(MigrationContext.configure(conn)),
                    _load_0013_module(), "upgrade")
        conn.commit()
        _run_module(Operations(MigrationContext.configure(conn)),
                    _load_0015_module(), "upgrade")
        conn.commit()
        _run_module(Operations(MigrationContext.configure(conn)),
                    _load_0015_module(), "downgrade")
        conn.commit()

        # CHECK 제거됨 — 미지원 값도 DB 수용(application layer 만 강제).
        _insert_stock_sql(conn, code="999999", security_type="bond")
        conn.commit()
        row = conn.execute(
            text("SELECT security_type FROM stocks_master WHERE current_code='999999'")
        ).fetchone()
        assert row is not None and row[0] == "bond"


def test_list_active_security_type_default_common(db_engine: Engine) -> None:
    """list_active — security_type 기본값 없이 INSERT 된 row 도 'common' 반환."""
    # ORM 의 server_default 로 security_type 이 설정되는지 확인.
    from app.db.orm.stocks_master import StocksMasterORM
    from app.repositories.sql_repositories import SqlStocksMasterRepository

    lineage_id = uuid4()
    as_of = date(2024, 6, 28)

    # security_type 을 명시하지 않고 ORM row 생성 — server_default/default 로 채워짐.
    orm_row = StocksMasterORM(
        id=lineage_id,
        current_code="000660",
        current_name="SK하이닉스",
        market="KOSPI",
        listing_date=date(1996, 12, 26),
        fiscal_month=12,
        code_history=[],
        ifrs_preference_default="AUTO",
        # security_type 미지정 — ORM Python-side default("common")가 적용.
    )

    Sessionmaker = sessionmaker(bind=db_engine, expire_on_commit=False)
    with Sessionmaker() as session:
        session.add(orm_row)
        session.commit()

        repo = SqlStocksMasterRepository(session)
        active = repo.list_active(as_of=as_of)

    assert len(active) == 1
    assert active[0].security_type == "common"
