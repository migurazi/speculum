"""stock_snapshots 영속 layer 테스트 — Sql/Fake contract 동등성 (ⓓ slice 1).

factor 결과 precomputed snapshot 의 save/fetch round-trip 과 Sql==Fake 동등성 검증.
read 경로(screen/market_overview) 배선은 §2.10 reproduce bypass 설계가 필요한 후속
cycle — 본 테스트는 영속 layer(ORM·converter·repo) 만 검증.

테스트 매트릭스:
    1. save → fetch_snapshot exact match (code, as_of, factor_uuid).
    2. fetch_snapshot 미존재 → None / 다른 as_of → None(exact match).
    3. fetch_snapshots_for_code — (code, as_of) 의 여러 factor + factor_uuids 필터.
    4. value Decimal 정밀도 byte-동일 round-trip(str 저장).
    5. value None(미산정) round-trip.
    6. inputs JSON round-trip.
    7. Sql == Fake 동등(동일 records 로 동일 결과).
    8. 합성 id 결정성(record.id == uuid5 재계산, ORM 저장 id 일치).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import NAMESPACE_OID, UUID, uuid5

from sqlalchemy.orm import Session

from app.db.converters import citation_record_to_orm
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.db.orm.source_citations import SourceCitationORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.fakes import FakeStockSnapshotRepository
from app.repositories.pit_protocols import StockSnapshotRecord
from app.repositories.sql_repositories import SqlStockSnapshotRepository

_CITATION = UUID("00000000-0000-0000-0000-00000000ffff")
_BATCH = UUID("00000000-0000-0000-0000-0000000000bb")
_AS_OF = date(2024, 6, 1)
_FACTOR_A = UUID("00000000-0000-0000-0000-00000000fa01")
_FACTOR_B = UUID("00000000-0000-0000-0000-00000000fb02")


def _seed_citation(session: Session) -> None:
    if session.get(BatchRunORM, _BATCH) is None:
        session.add(BatchRunORM(
            id=_BATCH, market=None, source="DART",
            started_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
            ended_at=datetime(2024, 5, 20, 9, 5, tzinfo=UTC),
            success_count=1, status=BATCH_STATUS_SUCCESS,
        ))
    if session.get(SourceCitationORM, _CITATION) is None:
        session.add(citation_record_to_orm(SourceCitation(
            id=_CITATION, source=SourceKind.DART, identifier="20240520000001",
            retrieved_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
            effective_date=date(2024, 5, 1), adapter_version="1.0.0",
            batch_id=_BATCH, url=None,
            created_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
        )))
    session.flush()


def _snap(
    *,
    code: str = "005930",
    as_of: date = _AS_OF,
    factor_uuid: UUID = _FACTOR_A,
    value: Decimal | None = Decimal("12.34"),
    value_unit: str = "ratio",
    inputs: dict | None = None,
    data_versions: dict | None = None,
) -> StockSnapshotRecord:
    return StockSnapshotRecord(
        stock_code=code,
        as_of_date=as_of,
        factor_uuid=factor_uuid,
        value=value,
        value_unit=value_unit,
        inputs=inputs if inputs is not None else {"numerator": "100", "denom": "8"},
        citation_id=_CITATION,
        computed_at=datetime(2024, 6, 1, 18, 0, tzinfo=UTC),
        data_versions=data_versions if data_versions is not None else {
            "krx_batch_id": "b1", "evaluator_version": "1.0",
        },
    )


# =============================================================================
# 1~2. fetch_snapshot exact match
# =============================================================================

def test_sql_save_fetch_snapshot_exact_match(db_session: Session) -> None:
    _seed_citation(db_session)
    repo = SqlStockSnapshotRepository(db_session)
    rec = _snap()
    repo.save_snapshots([rec])

    got = repo.fetch_snapshot("005930", as_of=_AS_OF, factor_uuid=_FACTOR_A)
    assert got is not None
    assert got.value == Decimal("12.34")
    assert got.factor_uuid == _FACTOR_A
    assert got.id == rec.id  # 합성 id 일치(결정성).


def test_sql_fetch_snapshot_missing_returns_none(db_session: Session) -> None:
    _seed_citation(db_session)
    repo = SqlStockSnapshotRepository(db_session)
    repo.save_snapshots([_snap()])
    # 다른 factor / 다른 as_of / 다른 code → None(exact match).
    assert repo.fetch_snapshot("005930", as_of=_AS_OF, factor_uuid=_FACTOR_B) is None
    assert repo.fetch_snapshot(
        "005930", as_of=date(2024, 5, 1), factor_uuid=_FACTOR_A,
    ) is None
    assert repo.fetch_snapshot("000660", as_of=_AS_OF, factor_uuid=_FACTOR_A) is None


# =============================================================================
# 3. fetch_snapshots_for_code multi-factor + filter
# =============================================================================

def test_sql_fetch_snapshots_for_code_multi_and_filter(db_session: Session) -> None:
    _seed_citation(db_session)
    repo = SqlStockSnapshotRepository(db_session)
    repo.save_snapshots([
        _snap(factor_uuid=_FACTOR_A, value=Decimal("1")),
        _snap(factor_uuid=_FACTOR_B, value=Decimal("2")),
    ])
    all_factors = repo.fetch_snapshots_for_code("005930", as_of=_AS_OF)
    assert {r.factor_uuid for r in all_factors} == {_FACTOR_A, _FACTOR_B}
    # factor_uuids 필터.
    only_a = repo.fetch_snapshots_for_code(
        "005930", as_of=_AS_OF, factor_uuids=frozenset({_FACTOR_A}),
    )
    assert len(only_a) == 1
    assert only_a[0].factor_uuid == _FACTOR_A


# =============================================================================
# 4~6. value/inputs round-trip
# =============================================================================

def test_sql_value_decimal_precision_round_trip(db_session: Session) -> None:
    """고정밀 Decimal 이 str 저장 후 byte-동일 복원(§2.10)."""
    _seed_citation(db_session)
    repo = SqlStockSnapshotRepository(db_session)
    precise = Decimal("0.123456789012345678")
    repo.save_snapshots([_snap(value=precise)])
    got = repo.fetch_snapshot("005930", as_of=_AS_OF, factor_uuid=_FACTOR_A)
    assert got is not None
    assert got.value == precise
    assert str(got.value) == "0.123456789012345678"  # scale 손실 없음.


def test_sql_value_none_round_trip(db_session: Session) -> None:
    """미산정(value None) round-trip — 절대 0 으로 변환 안 함."""
    _seed_citation(db_session)
    repo = SqlStockSnapshotRepository(db_session)
    repo.save_snapshots([_snap(value=None)])
    got = repo.fetch_snapshot("005930", as_of=_AS_OF, factor_uuid=_FACTOR_A)
    assert got is not None
    assert got.value is None


def test_sql_inputs_json_round_trip(db_session: Session) -> None:
    _seed_citation(db_session)
    repo = SqlStockSnapshotRepository(db_session)
    inputs = {"numerator": "500", "denominator": "10", "note": "ttm"}
    repo.save_snapshots([_snap(inputs=inputs)])
    got = repo.fetch_snapshot("005930", as_of=_AS_OF, factor_uuid=_FACTOR_A)
    assert got is not None
    assert dict(got.inputs) == inputs


def test_sql_inputs_decimal_serialized_lossless(db_session: Session) -> None:
    """inputs 가 Decimal/tuple[Decimal] (EvaluationResult.inputs_used 의 실제 shape)
    일 때 JSON 직렬화가 TypeError·float 손실 없이 str 로 무손실 보존(oracle C2).
    """
    _seed_citation(db_session)
    repo = SqlStockSnapshotRepository(db_session)
    inputs = {
        "net_income": Decimal("123.4500"),  # trailing zero 보존.
        "equity_series": (Decimal("1.1"), Decimal("2.2"), Decimal("3.3")),
        "label": "ttm",
    }
    repo.save_snapshots([_snap(inputs=inputs)])
    got = repo.fetch_snapshot("005930", as_of=_AS_OF, factor_uuid=_FACTOR_A)
    assert got is not None
    # Decimal → str(무손실), tuple → list[str], 일반 str 유지.
    assert got.inputs["net_income"] == "123.4500"
    assert got.inputs["equity_series"] == ["1.1", "2.2", "3.3"]
    assert got.inputs["label"] == "ttm"


def test_sql_data_versions_round_trip(db_session: Session) -> None:
    """freeze fingerprint(data_versions) round-trip — read-bypass 결정 축(C1)."""
    _seed_citation(db_session)
    repo = SqlStockSnapshotRepository(db_session)
    dv = {"krx_batch_id": "k9", "dart_batch_id": "d3", "evaluator_version": "1.2"}
    repo.save_snapshots([_snap(data_versions=dv)])
    got = repo.fetch_snapshot("005930", as_of=_AS_OF, factor_uuid=_FACTOR_A)
    assert got is not None
    assert dict(got.data_versions) == dv


def test_sql_save_snapshots_upsert_overwrites(db_session: Session) -> None:
    """save_snapshots 가 UPSERT — 같은 key 재계산 시 IntegrityError 아니라 overwrite
    (Sql/Fake 대칭, derived cache 정정 재산정 routine, oracle M2)."""
    _seed_citation(db_session)
    repo = SqlStockSnapshotRepository(db_session)
    repo.save_snapshots([_snap(value=Decimal("1"))])
    # 같은 (code, as_of, factor) 재계산 — IntegrityError 없이 갱신.
    repo.save_snapshots([_snap(value=Decimal("2"))])
    got = repo.fetch_snapshot("005930", as_of=_AS_OF, factor_uuid=_FACTOR_A)
    assert got is not None and got.value == Decimal("2")
    # for_code 에 중복 누적 없이 1건.
    assert len(repo.fetch_snapshots_for_code("005930", as_of=_AS_OF)) == 1


# =============================================================================
# 7. Sql == Fake 동등성
# =============================================================================

def test_sql_matches_fake(db_session: Session) -> None:
    _seed_citation(db_session)
    records = [
        _snap(factor_uuid=_FACTOR_A, value=Decimal("1.5")),
        _snap(factor_uuid=_FACTOR_B, value=None),
        _snap(code="000660", factor_uuid=_FACTOR_A, value=Decimal("99")),
    ]
    sql_repo = SqlStockSnapshotRepository(db_session)
    sql_repo.save_snapshots(records)
    fake_repo = FakeStockSnapshotRepository(records)

    for code, fac in (
        ("005930", _FACTOR_A), ("005930", _FACTOR_B), ("000660", _FACTOR_A),
    ):
        s = sql_repo.fetch_snapshot(code, as_of=_AS_OF, factor_uuid=fac)
        f = fake_repo.fetch_snapshot(code, as_of=_AS_OF, factor_uuid=fac)
        assert (s is None) == (f is None)
        if s is not None and f is not None:
            assert s.id == f.id
            assert s.value == f.value
    # for_code 동등.
    s_all = {r.factor_uuid for r in sql_repo.fetch_snapshots_for_code("005930", as_of=_AS_OF)}
    f_all = {r.factor_uuid for r in fake_repo.fetch_snapshots_for_code("005930", as_of=_AS_OF)}
    assert s_all == f_all


# =============================================================================
# 8. 합성 id 결정성
# =============================================================================

def test_synthetic_id_deterministic() -> None:
    rec = _snap()
    expected = uuid5(NAMESPACE_OID, f"005930|{_AS_OF.isoformat()}|{_FACTOR_A}")
    assert rec.id == expected
    # Fake save 후 fetch 한 record 도 동일 id.
    fake = FakeStockSnapshotRepository([rec])
    got = fake.fetch_snapshot("005930", as_of=_AS_OF, factor_uuid=_FACTOR_A)
    assert got is not None and got.id == expected


# =============================================================================
# 9. Fake save_snapshots overwrite (재계산 의미)
# =============================================================================

def test_fake_save_overwrites_same_key() -> None:
    fake = FakeStockSnapshotRepository([])
    fake.save_snapshots([_snap(value=Decimal("1"))])
    fake.save_snapshots([_snap(value=Decimal("2"))])  # 같은 key 재계산.
    got = fake.fetch_snapshot("005930", as_of=_AS_OF, factor_uuid=_FACTOR_A)
    assert got is not None and got.value == Decimal("2")
    # for_code 에도 중복 누적 없이 1건.
    assert len(fake.fetch_snapshots_for_code("005930", as_of=_AS_OF)) == 1
