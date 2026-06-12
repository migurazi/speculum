"""MacroIndicatorRepository — vintage 이중 시간축 PIT 조회 테스트.

Fake / Sql 동등성 + PIT 재현성(잠정→확정) 검증.

테스트 매트릭스:
    1. 기본 latest — 단일 reference, 단일 vintage.
    2. 미래 vintage 제외 — reference <= as_of 이나 vintage > as_of → 해당 row 제외,
       더 이른 vintage 선택.
    3. 미래 reference 제외 — reference > as_of row 무시.
    4. 잠정→확정 재현 — 같은 reference 에 vintage=잠정/확정 2 row.
       as_of=잠정vintage → 잠정값, as_of=확정vintage 이후 → 확정값.
    5. 데이터 없음 → None.
    6. 여러 reference 중 최신 reference 선택.
    7. Fake / Sql contract 동등성 — SQL 저장 후 Fake 와 동일 결과 확인.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.converters import citation_record_to_orm, macro_indicator_record_to_orm
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.db.orm.source_citations import SourceCitationORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.fakes import FakeMacroIndicatorRepository
from app.repositories.pit_protocols import MacroIndicatorRecord
from app.repositories.sql_repositories import SqlMacroIndicatorRepository

# 테스트 전용 고정 UUID — 결정성 보장.
_CID = UUID("00000000-0000-0000-0000-00000000cccc")
_BATCH = UUID("00000000-0000-0000-0000-0000000000dd")
_INDICATOR = "722Y001/0101000"  # ECOS 기준금리 식별자 예시


def _seed_fk_rows(session: Session) -> None:
    """citation_id FK + batch_id FK 만족용 dummy row."""
    if session.get(BatchRunORM, _BATCH) is None:
        session.add(
            BatchRunORM(
                id=_BATCH, market=None, source="ECOS",
                started_at=datetime(2024, 6, 1, 0, 0, tzinfo=UTC),
                ended_at=datetime(2024, 6, 1, 0, 5, tzinfo=UTC),
                success_count=1, status=BATCH_STATUS_SUCCESS,
            )
        )
    if session.get(SourceCitationORM, _CID) is None:
        session.add(citation_record_to_orm(SourceCitation(
            id=_CID,
            source=SourceKind.ECOS,
            identifier="ecos-test-001",
            retrieved_at=datetime(2024, 6, 1, 0, 0, tzinfo=UTC),
            effective_date=date(2024, 6, 1),
            adapter_version="1.0.0",
            batch_id=_BATCH,
            url=None,
            created_at=datetime(2024, 6, 1, 0, 0, tzinfo=UTC),
        )))
    session.flush()


def _rec(
    *,
    indicator_id: str = _INDICATOR,
    reference_date: date,
    vintage_date: date,
    value: Decimal = Decimal("3.50"),
    unit: str = "percent",
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> MacroIndicatorRecord:
    """테스트용 MacroIndicatorRecord 생성 헬퍼."""
    return MacroIndicatorRecord(
        id=record_id or uuid4(),
        indicator_id=indicator_id,
        reference_date=reference_date,
        value=value,
        unit=unit,
        vintage_date=vintage_date,
        citation_id=_CID,
        created_at=created_at or datetime(2024, 6, 1, 0, 0, tzinfo=UTC),
    )


def _insert(session: Session, record: MacroIndicatorRecord) -> None:
    """ORM row 를 DB 에 직접 insert (repository save 메서드 없음 — append-only)."""
    session.add(macro_indicator_record_to_orm(record))
    session.flush()


# =============================================================================
# 1. 기본 latest — 단일 reference, 단일 vintage
# =============================================================================

def test_fake_basic_latest() -> None:
    """단일 reference/vintage row — fetch_latest 가 해당 record 반환."""
    rec = _rec(
        reference_date=date(2024, 5, 1),
        vintage_date=date(2024, 5, 10),
        value=Decimal("3.50"),
    )
    repo = FakeMacroIndicatorRepository([rec])
    result = repo.fetch_latest(_INDICATOR, as_of=date(2024, 6, 1))
    assert result is not None
    assert result.reference_date == date(2024, 5, 1)
    assert result.value == Decimal("3.50")


def test_sql_basic_latest(db_session: Session) -> None:
    """SQL — 단일 reference/vintage row."""
    _seed_fk_rows(db_session)
    rec = _rec(
        reference_date=date(2024, 5, 1),
        vintage_date=date(2024, 5, 10),
        value=Decimal("3.50"),
    )
    _insert(db_session, rec)
    repo = SqlMacroIndicatorRepository(db_session)
    result = repo.fetch_latest(_INDICATOR, as_of=date(2024, 6, 1))
    assert result is not None
    assert result.reference_date == date(2024, 5, 1)
    assert result.value == Decimal("3.50")


# =============================================================================
# 2. 미래 vintage 제외 — vintage > as_of 인 row 는 무시, 더 이른 vintage 선택
# =============================================================================

def test_fake_future_vintage_excluded() -> None:
    """vintage_date > as_of 인 row 는 as_of 시점에 알 수 없는 관측 → 제외.

    같은 reference_date 에 이른 vintage(허용)와 늦은 vintage(미래, 제외) 두 row.
    as_of 가 늦은 vintage 이전 → 이른 vintage 값 반환.
    """
    rec_prov = _rec(
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 15),   # as_of 이전 → 허용
        value=Decimal("3.50"),
    )
    rec_final = _rec(
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 5, 15),   # as_of 이후 → 제외
        value=Decimal("3.25"),
    )
    repo = FakeMacroIndicatorRepository([rec_prov, rec_final])
    # as_of = 2024-05-01: vintage 2024-04-15 는 허용, 2024-05-15 는 제외.
    result = repo.fetch_latest(_INDICATOR, as_of=date(2024, 5, 1))
    assert result is not None
    assert result.vintage_date == date(2024, 4, 15)
    assert result.value == Decimal("3.50")


def test_sql_future_vintage_excluded(db_session: Session) -> None:
    """SQL — vintage_date > as_of 제외."""
    _seed_fk_rows(db_session)
    rec_prov = _rec(
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 15),
        value=Decimal("3.50"),
    )
    rec_final = _rec(
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 5, 15),
        value=Decimal("3.25"),
    )
    _insert(db_session, rec_prov)
    _insert(db_session, rec_final)
    repo = SqlMacroIndicatorRepository(db_session)
    result = repo.fetch_latest(_INDICATOR, as_of=date(2024, 5, 1))
    assert result is not None
    assert result.vintage_date == date(2024, 4, 15)
    assert result.value == Decimal("3.50")


# =============================================================================
# 3. 미래 reference 제외 — reference_date > as_of row 무시
# =============================================================================

def test_fake_future_reference_excluded() -> None:
    """reference_date > as_of 인 row 는 아직 도달하지 않은 기준 기간 → 제외."""
    rec_past = _rec(
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 15),
        value=Decimal("3.50"),
    )
    rec_future_ref = _rec(
        reference_date=date(2024, 7, 1),   # as_of(2024-06-01) 이후 기준 기간 → 제외
        vintage_date=date(2024, 5, 1),
        value=Decimal("4.00"),
    )
    repo = FakeMacroIndicatorRepository([rec_past, rec_future_ref])
    result = repo.fetch_latest(_INDICATOR, as_of=date(2024, 6, 1))
    assert result is not None
    assert result.reference_date == date(2024, 4, 1)
    assert result.value == Decimal("3.50")


def test_sql_future_reference_excluded(db_session: Session) -> None:
    """SQL — reference_date > as_of 제외."""
    _seed_fk_rows(db_session)
    rec_past = _rec(
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 15),
        value=Decimal("3.50"),
    )
    rec_future_ref = _rec(
        reference_date=date(2024, 7, 1),
        vintage_date=date(2024, 5, 1),
        value=Decimal("4.00"),
    )
    _insert(db_session, rec_past)
    _insert(db_session, rec_future_ref)
    repo = SqlMacroIndicatorRepository(db_session)
    result = repo.fetch_latest(_INDICATOR, as_of=date(2024, 6, 1))
    assert result is not None
    assert result.reference_date == date(2024, 4, 1)
    assert result.value == Decimal("3.50")


# =============================================================================
# 4. 잠정→확정 재현 — 같은 reference 에 vintage=잠정/확정 2 row.
#    as_of = 잠정 vintage → 잠정값, as_of = 확정 vintage 이후 → 확정값.
# =============================================================================

def test_fake_provisional_to_final_reproduction() -> None:
    """vintage 이중 시간축 잠정→확정 재현 — Fake.

    같은 reference_date(2024-01-01)에 잠정 vintage(2024-01-15)와 확정
    vintage(2024-03-01) 두 row.

    - as_of=2024-02-01: vintage<=as_of 조건에 잠정(2024-01-15)만 통과 → 잠정값(3.50).
    - as_of=2024-03-15: 확정(2024-03-01)도 통과 → ORDER BY vintage DESC → 확정값(3.25).
    """
    rec_prov = _rec(
        reference_date=date(2024, 1, 1),
        vintage_date=date(2024, 1, 15),   # 잠정치 공표
        value=Decimal("3.50"),
    )
    rec_final = _rec(
        reference_date=date(2024, 1, 1),
        vintage_date=date(2024, 3, 1),    # 확정치 공표
        value=Decimal("3.25"),
    )
    repo = FakeMacroIndicatorRepository([rec_prov, rec_final])

    # as_of 가 잠정·확정 사이 → 잠정값 재현.
    prov_result = repo.fetch_latest(_INDICATOR, as_of=date(2024, 2, 1))
    assert prov_result is not None
    assert prov_result.vintage_date == date(2024, 1, 15)
    assert prov_result.value == Decimal("3.50"), "as_of 가 잠정·확정 사이 → 잠정값"

    # as_of 가 확정 vintage 이후 → 확정값 재현.
    final_result = repo.fetch_latest(_INDICATOR, as_of=date(2024, 3, 15))
    assert final_result is not None
    assert final_result.vintage_date == date(2024, 3, 1)
    assert final_result.value == Decimal("3.25"), "as_of 가 확정 vintage 이후 → 확정값"


def test_sql_provisional_to_final_reproduction(db_session: Session) -> None:
    """vintage 이중 시간축 잠정→확정 재현 — SQL.

    Fake 와 동일 시나리오 — SQL 구현도 동일 재현성 보장 확인.
    """
    _seed_fk_rows(db_session)
    rec_prov = _rec(
        reference_date=date(2024, 1, 1),
        vintage_date=date(2024, 1, 15),
        value=Decimal("3.50"),
    )
    rec_final = _rec(
        reference_date=date(2024, 1, 1),
        vintage_date=date(2024, 3, 1),
        value=Decimal("3.25"),
    )
    _insert(db_session, rec_prov)
    _insert(db_session, rec_final)
    repo = SqlMacroIndicatorRepository(db_session)

    # as_of 가 잠정·확정 사이.
    prov_result = repo.fetch_latest(_INDICATOR, as_of=date(2024, 2, 1))
    assert prov_result is not None
    assert prov_result.vintage_date == date(2024, 1, 15)
    assert prov_result.value == Decimal("3.50"), "as_of 가 잠정·확정 사이 → 잠정값"

    # as_of 가 확정 vintage 이후.
    final_result = repo.fetch_latest(_INDICATOR, as_of=date(2024, 3, 15))
    assert final_result is not None
    assert final_result.vintage_date == date(2024, 3, 1)
    assert final_result.value == Decimal("3.25"), "as_of 가 확정 vintage 이후 → 확정값"


# =============================================================================
# 5. 데이터 없음 → None
# =============================================================================

def test_fake_empty_returns_none() -> None:
    repo = FakeMacroIndicatorRepository([])
    assert repo.fetch_latest(_INDICATOR, as_of=date(2024, 6, 1)) is None


def test_sql_empty_returns_none(db_session: Session) -> None:
    _seed_fk_rows(db_session)
    repo = SqlMacroIndicatorRepository(db_session)
    assert repo.fetch_latest(_INDICATOR, as_of=date(2024, 6, 1)) is None


def test_fake_no_matching_indicator_returns_none() -> None:
    """다른 indicator_id 의 row 가 있어도 해당 indicator 없으면 None."""
    rec = _rec(
        indicator_id="OTHER_INDICATOR",
        reference_date=date(2024, 5, 1),
        vintage_date=date(2024, 5, 10),
    )
    repo = FakeMacroIndicatorRepository([rec])
    assert repo.fetch_latest(_INDICATOR, as_of=date(2024, 6, 1)) is None


# =============================================================================
# 6. 여러 reference 중 최신 reference 선택
# =============================================================================

def test_fake_multiple_references_selects_latest() -> None:
    """여러 reference_date 가 있을 때 reference_date 가 가장 최신인 row 반환."""
    rec_old = _rec(
        reference_date=date(2024, 3, 1),
        vintage_date=date(2024, 3, 15),
        value=Decimal("3.50"),
    )
    rec_mid = _rec(
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 15),
        value=Decimal("3.25"),
    )
    rec_new = _rec(
        reference_date=date(2024, 5, 1),
        vintage_date=date(2024, 5, 15),
        value=Decimal("3.00"),
    )
    repo = FakeMacroIndicatorRepository([rec_old, rec_mid, rec_new])
    result = repo.fetch_latest(_INDICATOR, as_of=date(2024, 6, 1))
    assert result is not None
    assert result.reference_date == date(2024, 5, 1)
    assert result.value == Decimal("3.00")


def test_sql_multiple_references_selects_latest(db_session: Session) -> None:
    """SQL — 여러 reference_date 중 최신 선택."""
    _seed_fk_rows(db_session)
    rec_old = _rec(
        reference_date=date(2024, 3, 1),
        vintage_date=date(2024, 3, 15),
        value=Decimal("3.50"),
    )
    rec_mid = _rec(
        reference_date=date(2024, 4, 1),
        vintage_date=date(2024, 4, 15),
        value=Decimal("3.25"),
    )
    rec_new = _rec(
        reference_date=date(2024, 5, 1),
        vintage_date=date(2024, 5, 15),
        value=Decimal("3.00"),
    )
    for r in [rec_old, rec_mid, rec_new]:
        _insert(db_session, r)
    repo = SqlMacroIndicatorRepository(db_session)
    result = repo.fetch_latest(_INDICATOR, as_of=date(2024, 6, 1))
    assert result is not None
    assert result.reference_date == date(2024, 5, 1)
    assert result.value == Decimal("3.00")


# =============================================================================
# 7. Fake / Sql contract 동등성
# =============================================================================

def test_sql_fake_contract_equivalence(db_session: Session) -> None:
    """SQL 저장 후 Fake 와 동일 결과 — contract 동등성.

    여러 reference/vintage 조합에서 SQL 과 Fake 가 동일 row 를 선택하는지 확인.
    """
    _seed_fk_rows(db_session)
    records = [
        _rec(
            reference_date=date(2024, 3, 1),
            vintage_date=date(2024, 3, 15),
            value=Decimal("3.75"),
        ),
        _rec(
            reference_date=date(2024, 4, 1),
            vintage_date=date(2024, 4, 10),
            value=Decimal("3.50"),
        ),
        _rec(
            reference_date=date(2024, 4, 1),
            vintage_date=date(2024, 5, 5),   # 2024-04 기준 확정치
            value=Decimal("3.45"),
        ),
        _rec(
            reference_date=date(2024, 5, 1),
            vintage_date=date(2024, 5, 20),
            value=Decimal("3.25"),
        ),
    ]
    sql_repo = SqlMacroIndicatorRepository(db_session)
    for r in records:
        _insert(db_session, r)
    fake_repo = FakeMacroIndicatorRepository(records)

    # 시나리오 A: 확정치 이후 as_of — SQL 과 Fake 가 동일 row 선택.
    as_of_a = date(2024, 6, 1)
    sql_a = sql_repo.fetch_latest(_INDICATOR, as_of=as_of_a)
    fake_a = fake_repo.fetch_latest(_INDICATOR, as_of=as_of_a)
    assert sql_a is not None and fake_a is not None
    assert sql_a.reference_date == fake_a.reference_date == date(2024, 5, 1)
    assert sql_a.value == fake_a.value == Decimal("3.25")

    # 시나리오 B: 2024-04 확정 vintage 이전 as_of — 2024-04 잠정값, SQL/Fake 동일.
    as_of_b = date(2024, 4, 30)
    sql_b = sql_repo.fetch_latest(_INDICATOR, as_of=as_of_b)
    fake_b = fake_repo.fetch_latest(_INDICATOR, as_of=as_of_b)
    assert sql_b is not None and fake_b is not None
    assert sql_b.reference_date == fake_b.reference_date == date(2024, 4, 1)
    assert sql_b.vintage_date == fake_b.vintage_date == date(2024, 4, 10)
    assert sql_b.value == fake_b.value == Decimal("3.50")
