"""treasury_shares Alembic migration (0009) + ORM 스키마 검증.

검증:
1. migration 0009 의 `upgrade()` 가 treasury_shares 테이블 생성 + 기대 컬럼 /
   PK(id) / FK(citation_id, superseded_by) / 인덱스 (code_date / superseded_by /
   lineage_date).
2. `downgrade()` 가 테이블 제거 (source_citations 보존).
3. ORM (`Base.metadata.create_all`) 와 migration 이 동일 컬럼 set (drift 차단).

migration 0009 만 격리 실행 (test_market_caps_migration.py 패턴):
    전체 alembic 체인은 SQLite 미지원 (0004 의 PG-only ALTER COLUMN). 따라서
    source_citations (FK 대상) 만 ORM create_all 로 미리 만든 SQLite 위에서
    0009 의 upgrade/downgrade 를 MigrationContext + op 로 직접 구동.

    SQLite 는 ADR-0020 조건부 트리거를 생성하지 않음 (migration 의 dialect 분기).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

# 0009 migration 직후 컬럼 set (effective_date_precise 추가 전).
_COLUMNS_AFTER_0009 = {
    "id",
    "code",
    "code_lineage_id",
    "effective_date",
    "fiscal_period",
    "shares_treasury",
    "citation_id",
    "superseded_by",
    "created_at",
}
# 0010 (effective_date_precise BOOLEAN 추가) 적용 후 = 현행 ORM 컬럼 set.
_COLUMNS_AFTER_0010 = _COLUMNS_AFTER_0009 | {"effective_date_precise"}


@pytest.fixture
def engine_with_citations() -> Iterator[Engine]:
    """source_citations (FK 대상) 만 미리 만든 SQLite — 0009 격리 구동 기반."""
    from app.db.base import Base
    from app.db.orm.source_citations import SourceCitationORM  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    SourceCitationORM.__table__.create(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_migration_0009_upgrade_creates_treasury_shares(
    engine_with_citations: Engine,
) -> None:
    """0009 upgrade() → treasury_shares 테이블 + 기대 schema."""
    with engine_with_citations.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0009_upgrade(op)
        conn.commit()

        inspector = inspect(conn)
        assert "treasury_shares" in inspector.get_table_names()
        columns = {c["name"] for c in inspector.get_columns("treasury_shares")}
        assert columns == _COLUMNS_AFTER_0009

        pk = inspector.get_pk_constraint("treasury_shares")
        assert set(pk["constrained_columns"]) == {"id"}

        fks = inspector.get_foreign_keys("treasury_shares")
        assert any(
            fk["constrained_columns"] == ["citation_id"]
            and fk["referred_table"] == "source_citations"
            for fk in fks
        )
        # superseded_by self-FK (정정 chain).
        assert any(
            fk["constrained_columns"] == ["superseded_by"]
            and fk["referred_table"] == "treasury_shares"
            for fk in fks
        )

        indexes = inspector.get_indexes("treasury_shares")
        assert any(
            ix["column_names"] == ["code", "effective_date"] for ix in indexes
        )
        assert any(
            ix["column_names"] == ["superseded_by"] for ix in indexes
        )
        assert any(
            ix["column_names"] == ["code_lineage_id", "effective_date"]
            for ix in indexes
        )

        by_name = {
            c["name"]: c for c in inspector.get_columns("treasury_shares")
        }
        # shares_treasury NOT NULL (결측은 row 미생성 — financials 패턴).
        assert by_name["shares_treasury"]["nullable"] is False
        assert by_name["superseded_by"]["nullable"] is True


def test_migration_0009_downgrade_drops_treasury_shares(
    engine_with_citations: Engine,
) -> None:
    """0009 downgrade() → treasury_shares 제거 (source_citations 보존)."""
    with engine_with_citations.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0009_upgrade(op)
        conn.commit()
        assert "treasury_shares" in inspect(conn).get_table_names()

        _run_0009_downgrade(op)
        conn.commit()
        names = inspect(conn).get_table_names()
        assert "treasury_shares" not in names
        assert "source_citations" in names


def test_orm_create_all_matches_migration_columns() -> None:
    """ORM create_all 의 treasury_shares 컬럼 set 이 migration 기대치 일치 (drift)."""
    from app.db import orm  # noqa: F401  ← Base.metadata 등록 side-effect
    from app.db.base import Base

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        columns = {
            c["name"] for c in inspect(engine).get_columns("treasury_shares")
        }
        assert columns == _COLUMNS_AFTER_0010
    finally:
        engine.dispose()


def test_migration_0010_adds_effective_date_precise(
    engine_with_citations: Engine,
) -> None:
    """0010 upgrade() → financials + treasury_shares 에 effective_date_precise 추가.

    0009 로 treasury_shares 생성 + 최소 financials 테이블 생성 후 0010 적용 →
    두 테이블에 effective_date_precise BOOLEAN NOT NULL DEFAULT false 추가.
    downgrade 시 제거 (ADR-0012 D6). 0010 은 두 테이블 모두 ALTER 하므로 격리
    구동 시 financials (최소 stub) 도 존재해야 함.
    """
    from sqlalchemy import text

    with engine_with_citations.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0009_upgrade(op)
        # 0010 의 financials ALTER 대상 — 최소 stub 테이블 (컬럼 존재 여부만 관심).
        conn.execute(
            text(
                "CREATE TABLE financials ("
                "id BLOB PRIMARY KEY, superseded_by BLOB)"
            )
        )
        conn.commit()
        cols_before = {
            c["name"] for c in inspect(conn).get_columns("treasury_shares")
        }
        assert "effective_date_precise" not in cols_before

        _run_0010_upgrade(op)
        conn.commit()
        fin_cols = {
            c["name"] for c in inspect(conn).get_columns("financials")
        }
        assert "effective_date_precise" in fin_cols
        by_name = {
            c["name"]: c for c in inspect(conn).get_columns("treasury_shares")
        }
        assert "effective_date_precise" in by_name
        # NOT NULL (server_default false 로 기존 row backfill 불요).
        assert by_name["effective_date_precise"]["nullable"] is False

        _run_0010_downgrade(op)
        conn.commit()
        cols_after_down = {
            c["name"] for c in inspect(conn).get_columns("treasury_shares")
        }
        assert "effective_date_precise" not in cols_after_down


# =============================================================================
# 0009 migration 모듈 로드 helper — 파일명 숫자 prefix 라 importlib 사용.
# =============================================================================

def _load_0009_module():  # noqa: ANN202
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260529_0009_treasury_shares.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0009", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_0009_upgrade(op: Operations) -> None:
    module = _load_0009_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.upgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _run_0009_downgrade(op: Operations) -> None:
    module = _load_0009_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.downgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _load_0010_module():  # noqa: ANN202
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260529_0010_effective_date_precise.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0010", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_0010_upgrade(op: Operations) -> None:
    module = _load_0010_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.upgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _run_0010_downgrade(op: Operations) -> None:
    module = _load_0010_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.downgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]
