"""SQL bulk fetch contract test — bulk == N×single (per-code N+1 완화).

`SqlPriceRepository.fetch_prices_bulk` / `fetch_codes_with_prices` 와
`SqlMarketCapRepository.fetch_latest_bulk` 가 종목별 단건 fetch 를 N 회 호출한 것과
**byte-동일 결과** 를 산출하는지 검증. 이것이 깨지면 screen/backtest 의 prime 경로가
단건 경로와 다른 결과 → §2.10 재현성 위반(가장 심각).

테스트 매트릭스:
    1. fetch_prices_bulk == N×fetch_prices (다종목·다일자·범위필터).
    2. fetch_prices_bulk 누락 code → 빈 tuple.
    3. fetch_prices_bulk inverted range(start>as_of) → 전부 빈.
    4. fetch_codes_with_prices == 종목별 fetch_prices(date.min) 존재여부.
    5. fetch_latest_bulk == N×fetch_latest (window function latest + tiebreak).
    6. fetch_latest_bulk 누락 code → dict 부재.
    7. EXCLUDE_ALL_CUTOFF → bulk 전부 제외 (재현성 sentinel 정합).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.converters import (
    citation_record_to_orm,
    market_cap_record_to_orm,
    price_record_to_orm,
)
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.batch_run_repository import EXCLUDE_ALL_CUTOFF, BatchCutoff
from app.repositories.pit_protocols import MarketCapRecord, PriceRecord
from app.repositories.sql_repositories import (
    SqlMarketCapRepository,
    SqlPriceRepository,
)

_LINEAGE: UUID = UUID("00000000-0000-0000-0000-0000000000aa")
_CITATION: UUID = UUID("00000000-0000-0000-0000-00000000ffff")
_BATCH: UUID = UUID("00000000-0000-0000-0000-0000000000bb")


def _seed_citation(session: Session) -> None:
    from app.db.orm.source_citations import SourceCitationORM

    if session.get(SourceCitationORM, _CITATION) is not None:
        return
    citation = SourceCitation(
        id=_CITATION,
        source=SourceKind.KRX,
        identifier="20240520000001",
        retrieved_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
        effective_date=date(2024, 5, 1),
        adapter_version="1.0.0",
        batch_id=_BATCH,
        url=None,
        created_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
    )
    session.add(citation_record_to_orm(citation))
    session.flush()


def _price(
    code: str, eff: date, close: float = 70000.0, *, citation_id: UUID | None = None,
) -> PriceRecord:
    return PriceRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=_LINEAGE,
        effective_date=eff,
        open_raw=Decimal(str(close)),
        high_raw=Decimal(str(close)),
        low_raw=Decimal(str(close)),
        close_raw=Decimal(str(close)),
        volume=1_000_000,
        trading_value=Decimal(str(close)) * Decimal(1_000_000),
        close_adjusted=Decimal(str(close)),
        citation_id=citation_id or _CITATION,
        created_at=datetime(eff.year, eff.month, eff.day, 17, 0, tzinfo=UTC),
    )


def _mc(code: str, eff: date, cap: float = 4e14) -> MarketCapRecord:
    return MarketCapRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=_LINEAGE,
        effective_date=eff,
        market_cap=Decimal(str(cap)),
        shares_outstanding=5_000_000,
        shares_treasury=None,
        citation_id=_CITATION,
        created_at=datetime(eff.year, eff.month, eff.day, 17, 0, tzinfo=UTC),
    )


def _persist_prices(session: Session, records: list[PriceRecord]) -> None:
    _seed_citation(session)
    for r in records:
        session.add(price_record_to_orm(r))
    session.flush()


def _persist_mcs(session: Session, records: list[MarketCapRecord]) -> None:
    _seed_citation(session)
    for r in records:
        session.add(market_cap_record_to_orm(r))
    session.flush()


# =============================================================================
# 1. fetch_prices_bulk == N×fetch_prices
# =============================================================================

def test_price_bulk_matches_single(db_session: Session) -> None:
    records = [
        _price("000001", date(2024, 1, 15), close=100),
        _price("000001", date(2024, 3, 1), close=110),
        _price("000001", date(2024, 5, 10), close=120),
        _price("000002", date(2024, 2, 20), close=200),
        _price("000002", date(2024, 4, 30), close=210),
    ]
    _persist_prices(db_session, records)
    repo = SqlPriceRepository(db_session)
    codes = ["000001", "000002"]
    as_of, start = date(2024, 4, 1), date(2024, 1, 1)

    bulk = repo.fetch_prices_bulk(codes, as_of=as_of, start=start)
    for code in codes:
        single = repo.fetch_prices(code, as_of=as_of, start=start)
        assert [r.effective_date for r in bulk[code]] == [
            r.effective_date for r in single
        ]
        assert [r.close_raw for r in bulk[code]] == [r.close_raw for r in single]


def test_price_bulk_missing_code_empty_tuple(db_session: Session) -> None:
    _persist_prices(db_session, [_price("000001", date(2024, 5, 1))])
    repo = SqlPriceRepository(db_session)
    bulk = repo.fetch_prices_bulk(
        ["000001", "999999"], as_of=date(2024, 5, 10), start=date(2024, 1, 1),
    )
    assert bulk["999999"] == ()
    assert len(bulk["000001"]) == 1


def test_price_bulk_inverted_range_all_empty(db_session: Session) -> None:
    _persist_prices(db_session, [_price("000001", date(2024, 5, 1))])
    repo = SqlPriceRepository(db_session)
    bulk = repo.fetch_prices_bulk(
        ["000001"], as_of=date(2024, 1, 1), start=date(2024, 4, 1),
    )
    assert bulk == {"000001": ()}


# =============================================================================
# 4. fetch_codes_with_prices == 종목별 fetch_prices(date.min) 존재여부
# =============================================================================

def test_codes_with_prices_matches_single_existence(db_session: Session) -> None:
    records = [
        _price("000001", date(2024, 5, 1)),
        _price("000002", date(2010, 1, 4)),  # 오래된 데이터지만 존재.
    ]
    _persist_prices(db_session, records)
    repo = SqlPriceRepository(db_session)
    codes = ["000001", "000002", "000003"]
    as_of = date(2024, 5, 10)

    found = repo.fetch_codes_with_prices(codes, as_of=as_of)
    # 단건 fetch_prices(date.min) 의 "빈 시계열 여부" 와 동일 의미.
    expected = {
        c
        for c in codes
        if repo.fetch_prices(c, as_of=as_of, start=date.min)
    }
    assert found == expected
    assert found == {"000001", "000002"}


# =============================================================================
# 5~6. fetch_latest_bulk == N×fetch_latest (window function latest)
# =============================================================================

def test_market_cap_bulk_matches_single(db_session: Session) -> None:
    records = [
        _mc("000001", date(2024, 3, 1), cap=1e14),
        _mc("000001", date(2024, 5, 1), cap=2e14),  # 최신.
        _mc("000002", date(2024, 4, 10), cap=3e14),
        _mc("000002", date(2024, 5, 8), cap=4e14),  # 최신.
    ]
    _persist_mcs(db_session, records)
    repo = SqlMarketCapRepository(db_session)
    codes = ["000001", "000002"]
    as_of = date(2024, 5, 10)

    bulk = repo.fetch_latest_bulk(codes, as_of=as_of)
    for code in codes:
        single = repo.fetch_latest(code, as_of=as_of)
        assert single is not None
        assert bulk[code].effective_date == single.effective_date
        assert bulk[code].market_cap == single.market_cap


def test_market_cap_bulk_respects_as_of(db_session: Session) -> None:
    """as_of 이전 record 만 latest 후보 — window 랭킹이 as_of 필터 후 적용."""
    records = [
        _mc("000001", date(2024, 3, 1), cap=1e14),
        _mc("000001", date(2024, 5, 1), cap=2e14),  # as_of(4/1) 이후 → 제외.
    ]
    _persist_mcs(db_session, records)
    repo = SqlMarketCapRepository(db_session)
    as_of = date(2024, 4, 1)

    bulk = repo.fetch_latest_bulk(["000001"], as_of=as_of)
    single = repo.fetch_latest("000001", as_of=as_of)
    assert single is not None
    assert bulk["000001"].effective_date == single.effective_date == date(2024, 3, 1)


def test_market_cap_bulk_multi_date_picks_latest(db_session: Session) -> None:
    """code 당 effective_date 여러 개 → window function 이 단건과 동일하게 최신 선택.

    market_caps 는 (code, effective_date) UNIQUE 라 동일 일자 중복이 DB 레벨에서
    불가 → created_at/id tiebreak 는 도달 불가(단건·bulk 모두 방어적 정렬일 뿐).
    실재 시나리오인 다일자에서 ROW_NUMBER 최신 선택이 단건 LIMIT 1 과 일치함을 검증.
    """
    records = [
        _mc("000001", date(2024, 1, 31), cap=1e14),
        _mc("000001", date(2024, 3, 31), cap=2e14),
        _mc("000001", date(2024, 5, 8), cap=3e14),  # 최신.
    ]
    _persist_mcs(db_session, records)
    repo = SqlMarketCapRepository(db_session)
    as_of = date(2024, 5, 10)

    bulk = repo.fetch_latest_bulk(["000001"], as_of=as_of)
    single = repo.fetch_latest("000001", as_of=as_of)
    assert single is not None
    assert bulk["000001"].id == single.id
    assert bulk["000001"].effective_date == date(2024, 5, 8)


def test_market_cap_bulk_missing_code_absent(db_session: Session) -> None:
    _persist_mcs(db_session, [_mc("000001", date(2024, 5, 1))])
    repo = SqlMarketCapRepository(db_session)
    bulk = repo.fetch_latest_bulk(["000001", "999999"], as_of=date(2024, 5, 10))
    assert "999999" not in bulk  # 데이터 없는 code → dict 부재(.get → None).
    assert "000001" in bulk


# =============================================================================
# 7. EXCLUDE_ALL_CUTOFF → bulk 전부 제외 (재현성 sentinel 정합)
# =============================================================================

def test_bulk_exclude_all_cutoff_returns_empty(db_session: Session) -> None:
    _persist_prices(db_session, [_price("000001", date(2024, 5, 1))])
    _persist_mcs(db_session, [_mc("000001", date(2024, 5, 1))])
    price_repo = SqlPriceRepository(db_session)
    mc_repo = SqlMarketCapRepository(db_session)
    as_of = date(2024, 5, 10)

    # EXCLUDE_ALL = 항상 거짓 술어 → 그 source fact 전부 제외 (단건 경로와 동일).
    price_bulk = price_repo.fetch_prices_bulk(
        ["000001"], as_of=as_of, start=date(2024, 1, 1),
        batch_cutoff=EXCLUDE_ALL_CUTOFF,
    )
    assert price_bulk == {"000001": ()}

    found = price_repo.fetch_codes_with_prices(
        ["000001"], as_of=as_of, batch_cutoff=EXCLUDE_ALL_CUTOFF,
    )
    assert found == set()

    mc_bulk = mc_repo.fetch_latest_bulk(
        ["000001"], as_of=as_of, batch_cutoff=EXCLUDE_ALL_CUTOFF,
    )
    assert mc_bulk == {}


# =============================================================================
# survivorship + cutoff — fetch_codes_with_prices 가 backfill row 를 단건과 동일
# 하게 제외 (line 338 을 대체한 신규 의미의 재현성 회귀 가드, oracle L3)
# =============================================================================

def _seed_batch(session: Session, *, batch_id: UUID, started_at: datetime) -> None:
    session.add(BatchRunORM(
        id=batch_id,
        market="KOSPI",
        source="KRX",
        started_at=started_at,
        ended_at=started_at,
        success_count=1,
        status=BATCH_STATUS_SUCCESS,
    ))
    session.flush()


def _seed_citation_for(session: Session, *, cid: UUID, batch_id: UUID) -> None:
    citation = SourceCitation(
        id=cid,
        source=SourceKind.KRX,
        identifier=f"krx-{cid.int % 100000:05d}",
        retrieved_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
        effective_date=date(2024, 5, 1),
        adapter_version="1.0.0",
        batch_id=batch_id,
        url=None,
        created_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
    )
    session.add(citation_record_to_orm(citation))
    session.flush()


def test_codes_with_prices_cutoff_excludes_backfill(db_session: Session) -> None:
    """frozen cutoff 가 backfill batch 의 row 만 가진 종목을 존재집합에서 제외.

    fetch_codes_with_prices 는 line 338 의 fetch_prices(date.min) 존재판정을 대체한
    신규 메서드 — survivorship(missing_price_count→survivorship_complete)에 직결.
    cutoff 적용이 단건 경로와 동일해야 재현 모드에서 같은 missing 판정(§2.10).
    """
    b_frozen = UUID("00000000-0000-0000-0000-0000000000b1")
    b_backfill = UUID("00000000-0000-0000-0000-0000000000b2")
    c_frozen = UUID("00000000-0000-0000-0000-0000000000c1")
    c_backfill = UUID("00000000-0000-0000-0000-0000000000c2")
    # b_frozen(5/1) <= cutoff < b_backfill(5/10) — backfill 은 frozen 이후 batch.
    _seed_batch(db_session, batch_id=b_frozen, started_at=datetime(2024, 5, 1, 9, tzinfo=UTC))
    _seed_batch(db_session, batch_id=b_backfill, started_at=datetime(2024, 5, 10, 9, tzinfo=UTC))
    _seed_citation_for(db_session, cid=c_frozen, batch_id=b_frozen)
    _seed_citation_for(db_session, cid=c_backfill, batch_id=b_backfill)

    # 000001 = frozen batch 가 생산(존재 유지). 000002 = backfill batch 만 생산
    # (cutoff 하 제외 → missing). 둘 다 effective_date<=as_of.
    db_session.add(price_record_to_orm(
        _price("000001", date(2024, 4, 30), citation_id=c_frozen)
    ))
    db_session.add(price_record_to_orm(
        _price("000002", date(2024, 4, 30), citation_id=c_backfill)
    ))
    db_session.flush()

    repo = SqlPriceRepository(db_session)
    codes = ["000001", "000002"]
    as_of = date(2024, 5, 31)
    cutoff = BatchCutoff(started_at=datetime(2024, 5, 1, 9, tzinfo=UTC), id=b_frozen)

    found = repo.fetch_codes_with_prices(codes, as_of=as_of, batch_cutoff=cutoff)
    # 단건 fetch_prices(date.min, cutoff) 존재판정과 동일해야 함.
    expected = {
        c
        for c in codes
        if repo.fetch_prices(c, as_of=as_of, start=date.min, batch_cutoff=cutoff)
    }
    assert found == expected
    assert found == {"000001"}  # backfill-only 000002 는 cutoff 하 제외(missing).
