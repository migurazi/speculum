"""MacroIndicatorRecord + converter + migration 0011 + vintage 이중 시간축 검증.

테스트 범위:
    1. MacroIndicatorRecord frozen dataclass 모델 기본 특성.
    2. macro_indicator_record_to_orm / macro_indicator_orm_to_record round-trip
       무손실.
    3. migration 0011 upgrade() → macro_indicators 테이블 + 기대 컬럼 /
       PK(id) / FK(citation_id) / UNIQUE(indicator_id, reference_date, vintage_date) /
       인덱스 (indicator_vintage_ref, indicator_ref).
    4. migration 0011 downgrade() → 테이블 제거 (source_citations 보존).
    5. ORM create_all 컬럼 set 이 migration 0011 기대치와 일치 (drift 차단).
    6. 같은 (indicator_id, reference_date) 에 vintage_date 다른 2 row 삽입 가능
       (잠정→확정 누적 시나리오).
    7. 같은 (indicator_id, reference_date, vintage_date) 중복 insert → UNIQUE 위반.

treasury_shares_migration 테스트 패턴 모방:
    - migration 0011 격리 구동 (importlib + MigrationContext + Operations).
    - source_citations (FK 대상) 만 ORM create_all 로 미리 생성한 SQLite 위에서 실행.
    - SQLite 는 ADR-0020 트리거를 생성하지 않음 (dialect 분기 확인 불필요).
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# =============================================================================
# 0011 migration 격리 로드 helper
# =============================================================================

def _load_0011_module():  # noqa: ANN202
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260601_0011_macro_indicators.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0011", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_0011_upgrade(op: Operations) -> None:
    module = _load_0011_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.upgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


def _run_0011_downgrade(op: Operations) -> None:
    module = _load_0011_module()
    Operations._install_proxy(op)  # type: ignore[attr-defined]
    try:
        module.downgrade()
    finally:
        op._remove_proxy()  # type: ignore[attr-defined]


# migration 0011 적용 후 기대 컬럼 set.
_COLUMNS_AFTER_0011 = {
    "id",
    "indicator_id",
    "reference_date",
    "value",
    "unit",
    "vintage_date",
    "citation_id",
    "created_at",
}


# =============================================================================
# fixture — source_citations 선행 생성 SQLite
# =============================================================================

@pytest.fixture
def engine_with_citations() -> Iterator[Engine]:
    """source_citations (FK 대상) 만 미리 만든 SQLite — 0011 격리 구동 기반.

    treasury_shares_migration 테스트 패턴 동일.
    """
    from app.db.base import Base
    from app.db.orm.source_citations import SourceCitationORM  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    SourceCitationORM.__table__.create(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


# =============================================================================
# 1. MacroIndicatorRecord 모델 기본 특성
# =============================================================================

def test_macro_indicator_record_frozen() -> None:
    """frozen=True — record 생성 후 field 변경 시 FrozenInstanceError."""
    from app.repositories.pit_protocols import MacroIndicatorRecord

    record = MacroIndicatorRecord(
        id=uuid4(),
        indicator_id="722Y001/0101000",
        reference_date=_d("2024-01-01"),
        value=Decimal("3.50"),
        unit="percent",
        vintage_date=_d("2024-01-15"),
        citation_id=uuid4(),
        created_at=_utcnow(),
    )
    # frozen dataclass 는 attribute 변경 시 FrozenInstanceError.
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.value = Decimal("3.75")  # type: ignore[misc]


def test_macro_indicator_record_slots() -> None:
    """slots=True — __slots__ 가 정의돼 있고 __dict__ 없음."""
    from app.repositories.pit_protocols import MacroIndicatorRecord

    record = MacroIndicatorRecord(
        id=uuid4(),
        indicator_id="722Y001/0101000",
        reference_date=_d("2024-01-01"),
        value=Decimal("3.50"),
        unit="percent",
        vintage_date=_d("2024-01-15"),
        citation_id=uuid4(),
        created_at=_utcnow(),
    )
    # slots=True 이면 __dict__ 속성이 없음.
    assert not hasattr(record, "__dict__")


def test_macro_indicator_record_fields() -> None:
    """MacroIndicatorRecord 필드 목록이 vintage 이중 시간축 포함 기대치와 일치."""
    import dataclasses

    from app.repositories.pit_protocols import MacroIndicatorRecord

    field_names = {f.name for f in dataclasses.fields(MacroIndicatorRecord)}
    expected = {
        "id", "indicator_id", "reference_date", "value",
        "unit", "vintage_date", "citation_id", "created_at",
    }
    assert field_names == expected


# =============================================================================
# 2. converter round-trip
# =============================================================================

def test_macro_indicator_converter_round_trip(db_session: Session) -> None:
    """record → orm → record round-trip 무손실.

    db_session (conftest) 의 create_all 로 macro_indicators 테이블이 이미 존재
    (MacroIndicatorORM 이 orm/__init__.py 에 등록됨).
    """
    from app.db.converters import (
        macro_indicator_orm_to_record,
        macro_indicator_record_to_orm,
    )
    from app.db.orm.source_citations import SourceCitationORM
    from app.repositories.pit_protocols import MacroIndicatorRecord

    # source_citations FK 충족을 위해 citation row 선삽입.
    citation_id = uuid4()
    db_session.add(
        SourceCitationORM(
            id=citation_id,
            source="ECOS",
            identifier="ECOS-batch-001",
            retrieved_at=_utcnow(),
            effective_date=_d("2024-01-15"),
            adapter_version="1.0.0",
            batch_id=uuid4(),
            url=None,
            created_at=_utcnow(),
        )
    )
    db_session.flush()

    record = MacroIndicatorRecord(
        id=uuid4(),
        indicator_id="722Y001/0101000",
        reference_date=_d("2024-01-01"),
        value=Decimal("3.50"),
        unit="percent",
        vintage_date=_d("2024-01-15"),
        citation_id=citation_id,
        created_at=_utcnow(),
    )

    orm = macro_indicator_record_to_orm(record)
    db_session.add(orm)
    db_session.flush()

    # ORM → record 복원 후 원본과 동일한지 field 별 확인.
    restored = macro_indicator_orm_to_record(orm)
    assert restored.id == record.id
    assert restored.indicator_id == record.indicator_id
    assert restored.reference_date == record.reference_date
    assert restored.value == record.value
    assert restored.unit == record.unit
    assert restored.vintage_date == record.vintage_date
    assert restored.citation_id == record.citation_id
    # created_at 은 datetime precision 차이(SQLite 마이크로초 손실) 를 허용하지 않고
    # isoformat string 비교 (분 단위 이상 정확도 확인).
    assert restored.created_at.replace(microsecond=0) == record.created_at.replace(microsecond=0)


# =============================================================================
# 3. migration 0011 upgrade → 테이블 + 스키마 검증
# =============================================================================

def test_migration_0011_upgrade_creates_macro_indicators(
    engine_with_citations: Engine,
) -> None:
    """0011 upgrade() → macro_indicators 테이블 + 기대 스키마."""
    with engine_with_citations.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0011_upgrade(op)
        conn.commit()

        inspector = inspect(conn)
        assert "macro_indicators" in inspector.get_table_names()
        columns = {c["name"] for c in inspector.get_columns("macro_indicators")}
        assert columns == _COLUMNS_AFTER_0011

        pk = inspector.get_pk_constraint("macro_indicators")
        assert set(pk["constrained_columns"]) == {"id"}

        fks = inspector.get_foreign_keys("macro_indicators")
        assert any(
            fk["constrained_columns"] == ["citation_id"]
            and fk["referred_table"] == "source_citations"
            for fk in fks
        )

        # UNIQUE(indicator_id, reference_date, vintage_date) 존재 확인.
        # SQLite 에서 create_table 내 UniqueConstraint 는 get_unique_constraints() 에
        # 표시되고, 별도 CREATE UNIQUE INDEX 는 get_indexes() 에 표시됨.
        # 두 경로를 모두 확인하여 dialect 차이를 흡수.
        indexes = inspector.get_indexes("macro_indicators")
        unique_col_sets_from_indexes = [
            frozenset(ix["column_names"]) for ix in indexes if ix.get("unique")
        ]
        unique_constraints = inspector.get_unique_constraints("macro_indicators")
        unique_col_sets_from_constraints = [
            frozenset(uc["column_names"]) for uc in unique_constraints
        ]
        expected_unique = frozenset({"indicator_id", "reference_date", "vintage_date"})
        all_unique_col_sets = unique_col_sets_from_indexes + unique_col_sets_from_constraints
        assert expected_unique in all_unique_col_sets, (
            f"UNIQUE(indicator_id, reference_date, vintage_date) 미발견. "
            f"발견된 unique sets (indexes): {unique_col_sets_from_indexes}, "
            f"(constraints): {unique_col_sets_from_constraints}"
        )

        # PIT 조회 hot path 인덱스 존재 확인.
        all_col_sets = [frozenset(ix["column_names"]) for ix in indexes]
        assert frozenset({"indicator_id", "vintage_date", "reference_date"}) in all_col_sets, (
            "ix_macro_indicators_indicator_vintage_ref 인덱스 미발견"
        )
        assert frozenset({"indicator_id", "reference_date"}) in all_col_sets, (
            "ix_macro_indicators_indicator_ref 인덱스 미발견"
        )

        # 컬럼 nullable 속성 확인 — 모든 컬럼 NOT NULL.
        by_name = {c["name"]: c for c in inspector.get_columns("macro_indicators")}
        for col in ("indicator_id", "reference_date", "value", "unit", "vintage_date",
                    "citation_id", "created_at"):
            assert by_name[col]["nullable"] is False, f"{col} 은 NOT NULL 이어야 함"


# =============================================================================
# 4. migration 0011 downgrade → 테이블 제거
# =============================================================================

def test_migration_0011_downgrade_drops_macro_indicators(
    engine_with_citations: Engine,
) -> None:
    """0011 downgrade() → macro_indicators 제거 (source_citations 보존)."""
    with engine_with_citations.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        _run_0011_upgrade(op)
        conn.commit()
        assert "macro_indicators" in inspect(conn).get_table_names()

        _run_0011_downgrade(op)
        conn.commit()
        names = inspect(conn).get_table_names()
        assert "macro_indicators" not in names
        assert "source_citations" in names


# =============================================================================
# 5. ORM create_all 컬럼 drift 검증
# =============================================================================

def test_orm_create_all_matches_migration_columns() -> None:
    """ORM create_all 의 macro_indicators 컬럼 set 이 migration 0011 기대치와 일치."""
    from app.db import orm  # noqa: F401  ← Base.metadata 등록 side-effect
    from app.db.base import Base

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        columns = {
            c["name"] for c in inspect(engine).get_columns("macro_indicators")
        }
        assert columns == _COLUMNS_AFTER_0011
    finally:
        engine.dispose()


# =============================================================================
# 6. 같은 (indicator_id, reference_date) 에 vintage 다른 2 row 삽입 가능
# =============================================================================

def test_multiple_vintages_for_same_reference_date(db_session: Session) -> None:
    """잠정→확정 개정: 같은 (indicator_id, reference_date) 에 vintage_date 다른
    2 row 모두 insert 가능.

    vintage 이중 시간축의 핵심 시나리오 — 잠정치(vintage_date=2024-01-15) 와
    확정치(vintage_date=2024-02-28) 가 공존해야 PIT look-ahead 0 이 보장됨.
    """
    from app.db.converters import macro_indicator_record_to_orm
    from app.db.orm.source_citations import SourceCitationORM
    from app.repositories.pit_protocols import MacroIndicatorRecord

    citation_id = uuid4()
    db_session.add(
        SourceCitationORM(
            id=citation_id,
            source="ECOS",
            identifier="ECOS-vintage-test",
            retrieved_at=_utcnow(),
            effective_date=_d("2024-02-28"),
            adapter_version="1.0.0",
            batch_id=uuid4(),
            url=None,
            created_at=_utcnow(),
        )
    )
    db_session.flush()

    indicator_id = "722Y001/0101000"
    ref_date = _d("2024-01-01")

    # 잠정치 (vintage_date = 2024-01-15, 속보).
    record_provisional = MacroIndicatorRecord(
        id=uuid4(),
        indicator_id=indicator_id,
        reference_date=ref_date,
        value=Decimal("3.50"),
        unit="percent",
        vintage_date=_d("2024-01-15"),
        citation_id=citation_id,
        created_at=_utcnow(),
    )
    # 확정치 (vintage_date = 2024-02-28, 개정).
    record_final = MacroIndicatorRecord(
        id=uuid4(),
        indicator_id=indicator_id,
        reference_date=ref_date,
        value=Decimal("3.52"),  # 잠정 3.50 → 확정 3.52
        unit="percent",
        vintage_date=_d("2024-02-28"),
        citation_id=citation_id,
        created_at=_utcnow(),
    )

    db_session.add(macro_indicator_record_to_orm(record_provisional))
    db_session.add(macro_indicator_record_to_orm(record_final))
    # 두 row 모두 UNIQUE 위반 없이 flush 성공해야 함.
    db_session.flush()

    from app.db.orm.macro_indicators import MacroIndicatorORM
    rows = db_session.query(MacroIndicatorORM).filter_by(
        indicator_id=indicator_id,
    ).all()
    assert len(rows) == 2
    vintages = {r.vintage_date for r in rows}
    assert vintages == {_d("2024-01-15"), _d("2024-02-28")}


# =============================================================================
# 7. UNIQUE(indicator_id, reference_date, vintage_date) 위반 케이스
# =============================================================================

def test_duplicate_vintage_raises_unique_violation(db_session: Session) -> None:
    """같은 (indicator_id, reference_date, vintage_date) 중복 insert → IntegrityError."""
    from app.db.converters import macro_indicator_record_to_orm
    from app.db.orm.source_citations import SourceCitationORM
    from app.repositories.pit_protocols import MacroIndicatorRecord

    citation_id = uuid4()
    db_session.add(
        SourceCitationORM(
            id=citation_id,
            source="ECOS",
            identifier="ECOS-dup-test",
            retrieved_at=_utcnow(),
            effective_date=_d("2024-01-15"),
            adapter_version="1.0.0",
            batch_id=uuid4(),
            url=None,
            created_at=_utcnow(),
        )
    )
    db_session.flush()

    indicator_id = "722Y001/0101000"
    ref_date = _d("2024-01-01")
    vintage_date = _d("2024-01-15")

    record1 = MacroIndicatorRecord(
        id=uuid4(),
        indicator_id=indicator_id,
        reference_date=ref_date,
        value=Decimal("3.50"),
        unit="percent",
        vintage_date=vintage_date,
        citation_id=citation_id,
        created_at=_utcnow(),
    )
    # 다른 id/value 이지만 (indicator_id, reference_date, vintage_date) 동일.
    record2 = MacroIndicatorRecord(
        id=uuid4(),
        indicator_id=indicator_id,
        reference_date=ref_date,
        value=Decimal("9.99"),  # value 달라도 UNIQUE 위반
        unit="percent",
        vintage_date=vintage_date,  # 동일 vintage_date → 충돌
        citation_id=citation_id,
        created_at=_utcnow(),
    )

    db_session.add(macro_indicator_record_to_orm(record1))
    db_session.flush()

    db_session.add(macro_indicator_record_to_orm(record2))
    with pytest.raises(IntegrityError):
        db_session.flush()


# =============================================================================
# 공용 helper
# =============================================================================


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


def _utcnow() -> datetime:
    return datetime.now(UTC)
