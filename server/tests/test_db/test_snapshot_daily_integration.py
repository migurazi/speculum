"""SnapshotDailyBatch 실 SQLite 세션 통합 테스트 (ⓓ slice 2a, oracle M2).

단위 테스트(tests/test_batch/test_snapshot_daily.py)는 FakeSession(flush no-op,
begin_nested rollback 없음)이라 **실제 merge UPSERT + flush + SAVEPOINT** 경로가
미검증이었다. 본 테스트는 db_session(실 SQLite, FK PRAGMA ON)으로 run_snapshot_job
을 end-to-end 실행해:
    1. snapshot 이 DB 에 실제 영속(SqlStockSnapshotRepository fetch).
    2. 같은 observed_date 재실행 → session.merge UPSERT 로 중복 누적 없음(PK
       IntegrityError 없이 갱신) — 실 SAVEPOINT/flush 경로 확인.
    3. PRECOMPUTE citation + batch_run FK 가 실 DB 에서 만족.
를 검증한다. value 정확성은 단위 테스트가 커버 — 여기선 영속/UPSERT/트랜잭션 안전.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.converters import (
    citation_record_to_orm,
    market_cap_record_to_orm,
    stocks_master_record_to_orm,
)
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.db.orm.source_citations import SourceCitationORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    MarketCapRecord,
    StockMasterRecord,
)
from app.repositories.sql_repositories import SqlStockSnapshotRepository
from app.services.factor_pack import LoadedPack
from batch.snapshot_daily import run_snapshot_job

_OBSERVED = date(2024, 5, 7)
_CITATION = UUID("00000000-0000-0000-0000-00000000ffff")
_BATCH = UUID("00000000-0000-0000-0000-0000000000bb")
_LINEAGE = UUID("00000000-0000-0000-0000-0000000000aa")
_F1 = "018f9b00-0001-7000-8000-0000000000a1"
_F2 = "018f9b00-0001-7000-8000-0000000000a2"


def _small_pack() -> LoadedPack:
    """factor 2개(shares_issued 참조) 작은 pack — 평가 단순화."""
    body = {
        "factors": [
            {
                "canonical_id": "test:shares-a", "uuid": _F1, "name": "shares A",
                "description": "발행주식수 A",
                "formula": {"ast": {"field": "shares_issued"},
                            "inputs": ["shares_issued"]},
                "unit": "shares",
            },
            {
                "canonical_id": "test:shares-b", "uuid": _F2, "name": "shares B",
                "description": "발행주식수 B",
                "formula": {"ast": {"field": "shares_issued"},
                            "inputs": ["shares_issued"]},
                "unit": "count",
            },
        ]
    }
    return LoadedPack(
        body=body, computed_hash="testhash00000000", factor_count=2,
        pack_slug="test-snapshot-pack", version="1.0.0",
    )


def _seed(session: Session) -> None:
    """FK 충족 최소 시드 — batch_run + citation + stock + market_cap."""
    # FK 의존 순서로 단계 flush — batch_run → citation → (stock, market_cap).
    session.add(BatchRunORM(
        id=_BATCH, market="KOSPI", source="KRX",
        started_at=datetime(2024, 5, 6, 9, 0, tzinfo=UTC),
        ended_at=datetime(2024, 5, 6, 9, 5, tzinfo=UTC),
        success_count=1, status=BATCH_STATUS_SUCCESS,
    ))
    session.flush()
    session.add(citation_record_to_orm(SourceCitation(
        id=_CITATION, source=SourceKind.KRX, identifier="20240507KOSPI",
        retrieved_at=datetime(2024, 5, 7, 17, 0, tzinfo=UTC),
        effective_date=_OBSERVED, adapter_version="1.0.0",
        batch_id=_BATCH, url=None,
        created_at=datetime(2024, 5, 7, 17, 0, tzinfo=UTC),
    )))
    session.flush()
    session.add(stocks_master_record_to_orm(StockMasterRecord(
        id=_LINEAGE, current_code="005930", current_name="삼성전자",
        market="KOSPI", listing_date=date(1975, 6, 11), delisting_date=None,
        fiscal_month=12,
        code_history=(CodeHistoryEntry(
            code="005930", valid_from=date(1975, 6, 11), valid_to=None,
            reason="initial_listing",
        ),),
        ifrs_preference_default="AUTO",
    )))
    session.add(market_cap_record_to_orm(MarketCapRecord(
        id=uuid4(), code="005930", code_lineage_id=_LINEAGE,
        effective_date=_OBSERVED, market_cap=Decimal("5.0e14"),
        shares_outstanding=5_000_000, shares_treasury=None,
        citation_id=_CITATION,
        created_at=datetime(2024, 5, 7, 17, 0, tzinfo=UTC),
    )))
    session.flush()


def test_run_snapshot_job_persists_and_is_idempotent(db_session: Session) -> None:
    """run_snapshot_job(실 세션) → snapshot 영속 + 재실행 UPSERT 멱등(oracle M2)."""
    _seed(db_session)
    pack = _small_pack()

    summary = run_snapshot_job(
        session=db_session, observed_date=_OBSERVED, pack=pack,
    )
    db_session.flush()

    # 1 종목 × 2 factor = 2 snapshot 영속(실 merge/flush/SAVEPOINT 경유).
    assert summary.universe_count == 1
    assert summary.factor_count == 2
    assert summary.snapshots_saved == 2
    assert summary.failure_count == 0

    repo = SqlStockSnapshotRepository(db_session)
    rows = repo.fetch_snapshots_for_code("005930", as_of=_OBSERVED)
    assert {str(r.factor_uuid) for r in rows} == {_F1, _F2}
    # shares_issued = market_cap.shares_outstanding 평가값.
    by_uuid = {str(r.factor_uuid): r for r in rows}
    assert by_uuid[_F1].value == Decimal("5000000")
    assert by_uuid[_F1].value_unit == "shares"
    # PRECOMPUTE citation 이 실 DB 에 영속(batch 가 생성).
    cited = db_session.get(SourceCitationORM, rows[0].citation_id)
    assert cited is not None and cited.source == "PRECOMPUTE"

    # 재실행 — 같은 (code, as_of, factor) UPSERT(merge) → 중복 누적 없이 2건 유지.
    summary2 = run_snapshot_job(
        session=db_session, observed_date=_OBSERVED, pack=pack,
    )
    db_session.flush()
    assert summary2.snapshots_saved == 2
    rows2 = repo.fetch_snapshots_for_code("005930", as_of=_OBSERVED)
    assert len(rows2) == 2  # IntegrityError 없이 overwrite — 멱등.
