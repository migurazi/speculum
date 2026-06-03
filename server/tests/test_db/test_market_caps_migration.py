"""market_caps Alembic migration (0008) + ORM 스키마 검증 — Phase B.

검증:
1. migration 0008 의 `upgrade()` 가 market_caps 테이블을 생성 + 기대 컬럼 /
   PK / UNIQUE(id) / FK(citation_id) / lineage 인덱스.
2. `downgrade()` 가 테이블을 제거.
3. ORM (`Base.metadata.create_all`) 와 migration 이 동일 컬럼 set 산출 (drift 차단).

migration 0008 만 격리 실행:
    전체 alembic 체인 (0001~0007) 은 SQLite 에서 직접 실행 불가 — 0004 가
    PG-only `ALTER COLUMN ... DROP DEFAULT` 를 포함 (운영 PG 대상, SQLite 미지원).
    따라서 본 테스트는 source_citations (FK 대상) 만 ORM create_all 로 미리 만든
    SQLite 위에서 0008 의 upgrade/downgrade 를 alembic MigrationContext + op 로
    직접 구동하여 0008 자체의 정확성을 검증.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

_EXPECTED_COLUMNS = {
    "id",
    "code",
    "code_lineage_id",
    "effective_date",
    "market_cap",
    "shares_outstanding",
    "shares_treasury",
    "citation_id",
    "created_at",
}


@pytest.fixture
def engine_with_citations() -> Iterator[Engine]:
    """source_citations (FK 대상) 만 미리 만든 SQLite — 0008 격리 구동 기반."""
    from app.db.base import Base
    from app.db.orm.source_citations import SourceCitationORM  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    # FK 대상 테이블만 생성 (market_caps 는 migration 이 만듦).
    SourceCitationORM.__table__.create(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_migration_0008_upgrade_creates_market_caps(
    engine_with_citations: Engine,
) -> None:
    """0008 upgrade() → market_caps 테이블 + 기대 schema."""
    with engine_with_citations.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0008_upgrade(op)
        conn.commit()

        inspector = inspect(conn)
        assert "market_caps" in inspector.get_table_names()
        columns = {c["name"] for c in inspector.get_columns("market_caps")}
        assert columns == _EXPECTED_COLUMNS

        pk = inspector.get_pk_constraint("market_caps")
        assert set(pk["constrained_columns"]) == {"code", "effective_date"}

        uniques = inspector.get_unique_constraints("market_caps")
        assert any(u["column_names"] == ["id"] for u in uniques)

        fks = inspector.get_foreign_keys("market_caps")
        assert any(
            fk["constrained_columns"] == ["citation_id"]
            and fk["referred_table"] == "source_citations"
            for fk in fks
        )

        indexes = inspector.get_indexes("market_caps")
        assert any(
            ix["column_names"] == ["code_lineage_id", "effective_date"]
            for ix in indexes
        )

        by_name = {c["name"]: c for c in inspector.get_columns("market_caps")}
        # shares_treasury nullable (pykrx None), shares_outstanding NOT NULL.
        assert by_name["shares_treasury"]["nullable"] is True
        assert by_name["shares_outstanding"]["nullable"] is False


def test_migration_0008_downgrade_drops_market_caps(
    engine_with_citations: Engine,
) -> None:
    """0008 downgrade() → market_caps 제거 (source_citations 보존)."""
    with engine_with_citations.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0008_upgrade(op)
        conn.commit()
        assert "market_caps" in inspect(conn).get_table_names()

        _run_0008_downgrade(op)
        conn.commit()
        names = inspect(conn).get_table_names()
        assert "market_caps" not in names
        assert "source_citations" in names


def test_orm_create_all_matches_migration_columns() -> None:
    """ORM create_all 의 market_caps 컬럼 set 이 migration 기대치와 일치 (drift 차단)."""
    from app.db import orm  # noqa: F401  ← Base.metadata 등록 side-effect
    from app.db.base import Base

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        columns = {
            c["name"] for c in inspect(engine).get_columns("market_caps")
        }
        assert columns == _EXPECTED_COLUMNS
    finally:
        engine.dispose()


# =============================================================================
# 0008 migration 모듈 로드 helper — 파일명에 숫자 prefix 라 importlib 사용.
# =============================================================================

def _load_0008_module():  # noqa: ANN202
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260529_0008_market_caps.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0008", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_0008_upgrade(op: Operations) -> None:
    # 0008 의 upgrade() 는 module-level `from alembic import op` (전역 proxy) 사용
    # — 본 Operations 인스턴스를 proxy 로 바인딩 후 호출.
    module = _load_0008_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.upgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _run_0008_downgrade(op: Operations) -> None:
    module = _load_0008_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.downgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]
