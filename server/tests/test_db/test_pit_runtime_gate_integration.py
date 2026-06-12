"""PIT runtime gate 통합 회귀 테스트 — M5 #1 / Momus H1.

각 SQL repository 의 fetch 경로가 반환 직전 PITEnforcer 의 사후 assert 를 실제로
거치는지 배선 회귀를 고정한다.

전략:
    repo._enforcer 를 "assert 에서 무조건 LookAheadError 를 던지는 stub" 으로 교체한 뒤,
    정상 데이터(non-empty result 보장) 로 fetch 를 호출하면 LookAheadError 가 전파되어야
    한다. assert 호출 코드를 누가 제거하면 stub 이 호출되지 않으므로 테스트가 실패 →
    배선 회귀가 즉시 탐지된다.

stub 설계:
    _AssertRaisingEnforcer  — assert_no_lookahead       override (Price / MarketCap /
                              Financial / TreasuryShares / CorporateAction).
    _VintageAssertRaisingEnforcer — assert_no_vintage_lookahead override (MacroIndicator).

    records 가 비어있으면 raise 하지 않는다 (빈 결과에 대해 assert 가 통과하는 것은
    정상 동작이므로 거짓양성 방지). fixture 는 fetch 결과가 1+ record 임을 보장한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.db.converters import (
    citation_record_to_orm,
    corporate_action_record_to_orm,
    financial_record_to_orm,
    macro_indicator_record_to_orm,
    market_cap_record_to_orm,
    price_record_to_orm,
    treasury_shares_record_to_orm,
)
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.db.orm.source_citations import SourceCitationORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.pit_protocols import (
    CorporateActionRecord,
    FinancialRecord,
    MacroIndicatorRecord,
    MarketCapRecord,
    PriceRecord,
    TreasurySharesRecord,
)
from app.repositories.sql_repositories import (
    SqlCorporateActionRepository,
    SqlFinancialRepository,
    SqlMacroIndicatorRepository,
    SqlMarketCapRepository,
    SqlPriceRepository,
    SqlTreasurySharesRepository,
)
from app.services.pit_enforcer import LookAheadError, PITEnforcer

# =============================================================================
# 테스트 전용 고정 UUID / 상수
# =============================================================================

_CID: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000ff")
_LINEAGE: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000aa")
_BATCH: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000bb")
_CODE: Final[str] = "005930"
_ACCOUNT: Final[str] = "net_income_consolidated_ifrs"
_INDICATOR: Final[str] = "722Y001/0101000"

# fetch 호출 시 정상 데이터가 보이는 as_of — 각 fixture 의 effective_date 이후.
_AS_OF: Final[date] = date(2024, 9, 1)

# =============================================================================
# stub enforcer — assert 에서 무조건 LookAheadError 를 raise
# =============================================================================


class _AssertRaisingEnforcer(PITEnforcer):
    """assert_no_lookahead 를 override 하여 records 가 1+ 이면 무조건 raise.

    records 가 비어있으면 raise 하지 않는다 — 빈 결과에 대해 assert 가 통과하는
    것이 정상이므로 거짓양성 방지 (fixture 는 non-empty 결과를 보장해야 함).
    다른 메서드(filter_active_records / latest_active_by_key 등)는 부모 구현을
    그대로 사용하여 fetch 로직 자체에 영향을 주지 않는다.
    """

    def assert_no_lookahead(
        self,
        records: Sequence,  # type: ignore[override]
        as_of: date,
        *,
        date_of=None,  # type: ignore[override]
    ) -> None:
        if records:
            raise LookAheadError(uuid4(), as_of, as_of)


class _VintageAssertRaisingEnforcer(PITEnforcer):
    """assert_no_vintage_lookahead 를 override 하여 records 가 1+ 이면 무조건 raise.

    MacroIndicatorRepository 전용 stub (이중 시간축 vintage assert 경로).
    """

    def assert_no_vintage_lookahead(
        self,
        records: Sequence,  # type: ignore[override]
        as_of: date,
    ) -> None:
        if records:
            raise LookAheadError(uuid4(), as_of, as_of)


# =============================================================================
# FK 공통 seed helper
# =============================================================================


def _seed_fk(
    session: Session,
    *,
    source: str = "KRX",
    cid: UUID = _CID,
    batch: UUID = _BATCH,
) -> None:
    """citation_id / batch_id FK 만족용 최소 seed."""
    if session.get(BatchRunORM, batch) is None:
        session.add(
            BatchRunORM(
                id=batch,
                market=None,
                source=source,
                started_at=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
                ended_at=datetime(2024, 1, 1, 0, 1, tzinfo=UTC),
                success_count=1,
                status=BATCH_STATUS_SUCCESS,
            )
        )
    if session.get(SourceCitationORM, cid) is None:
        session.add(citation_record_to_orm(SourceCitation(
            id=cid,
            source=SourceKind.KRX if source == "KRX" else SourceKind.DART,
            identifier="test-seed-001",
            retrieved_at=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
            effective_date=date(2024, 1, 1),
            adapter_version="1.0.0",
            batch_id=batch,
            url=None,
            created_at=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        )))
    session.flush()


# =============================================================================
# 테스트 1 — SqlPriceRepository.fetch_prices
# =============================================================================


def test_price_fetch_goes_through_enforcer_assert(db_session: Session) -> None:
    """사후 assert 배선 회귀 — fetch 가 enforcer.assert 를 거치지 않으면(코드 제거 시)
    본 테스트 실패. WHERE 1차 필터와 별개의 defense-in-depth 고정(M5 #1 / Momus H1).

    non-empty 보장: effective_date=2024-06-01 (< _AS_OF=2024-09-01) price row 삽입.
    stub 이 호출되지 않으면 LookAheadError 미발생 → pytest.raises 실패.
    """
    _seed_fk(db_session, source="KRX")
    price_row = PriceRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=date(2024, 6, 1),
        open_raw=Decimal("70000"),
        high_raw=Decimal("70000"),
        low_raw=Decimal("70000"),
        close_raw=Decimal("70000"),
        volume=1_000_000,
        trading_value=Decimal("70000000000"),
        close_adjusted=Decimal("70000"),
        citation_id=_CID,
        created_at=datetime(2024, 6, 1, 17, 0, tzinfo=UTC),
    )
    db_session.add(price_record_to_orm(price_row))
    db_session.flush()

    repo = SqlPriceRepository(db_session)
    repo._enforcer = _AssertRaisingEnforcer()

    with pytest.raises(LookAheadError):
        repo.fetch_prices(_CODE, as_of=_AS_OF, start=date(2024, 1, 1))


# =============================================================================
# 테스트 2 — SqlMarketCapRepository.fetch_latest
# =============================================================================


def test_market_cap_fetch_goes_through_enforcer_assert(db_session: Session) -> None:
    """사후 assert 배선 회귀 — fetch 가 enforcer.assert 를 거치지 않으면(코드 제거 시)
    본 테스트 실패. WHERE 1차 필터와 별개의 defense-in-depth 고정(M5 #1 / Momus H1).

    non-empty 보장: effective_date=2024-06-01 (< _AS_OF) market_cap row 삽입.
    fetch_latest 는 단건 반환 — None 이 아닌 record 가 있어야 stub 이 호출됨.
    """
    _seed_fk(db_session, source="KRX")
    mc_row = MarketCapRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=date(2024, 6, 1),
        market_cap=Decimal("400000000000000"),
        shares_outstanding=5_969_782_550,
        shares_treasury=None,
        citation_id=_CID,
        created_at=datetime(2024, 6, 1, 17, 0, tzinfo=UTC),
    )
    db_session.add(market_cap_record_to_orm(mc_row))
    db_session.flush()

    repo = SqlMarketCapRepository(db_session)
    repo._enforcer = _AssertRaisingEnforcer()

    with pytest.raises(LookAheadError):
        repo.fetch_latest(_CODE, as_of=_AS_OF)


# =============================================================================
# 테스트 3 — SqlFinancialRepository.fetch_financials
# =============================================================================


def test_financial_fetch_goes_through_enforcer_assert(db_session: Session) -> None:
    """사후 assert 배선 회귀 — fetch 가 enforcer.assert 를 거치지 않으면(코드 제거 시)
    본 테스트 실패. WHERE 1차 필터와 별개의 defense-in-depth 고정(M5 #1 / Momus H1).

    non-empty 보장: effective_date=2024-04-15 (< _AS_OF) financial row 삽입.
    latest_active_by_key 가 1+ active record 를 반환한 뒤 assert 가 호출되어야 함.
    """
    _seed_fk(db_session, source="KRX")  # source 무관 — citation FK 충족이 목적
    fin_row = FinancialRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=date(2024, 4, 15),
        fiscal_period="2024Q1",
        account=_ACCOUNT,
        value=Decimal("1000000000000"),
        unit="krw",
        ifrs_type="consolidated",
        effective_date_precise=False,
        citation_id=_CID,
        superseded_by=None,
        created_at=datetime(2024, 4, 15, 9, 0, tzinfo=UTC),
    )
    db_session.add(financial_record_to_orm(fin_row))
    db_session.flush()

    repo = SqlFinancialRepository(db_session)
    repo._enforcer = _AssertRaisingEnforcer()

    with pytest.raises(LookAheadError):
        repo.fetch_financials(_CODE, as_of=_AS_OF, account=_ACCOUNT)


# =============================================================================
# 테스트 4 — SqlTreasurySharesRepository.fetch_latest_active
# =============================================================================


def test_treasury_shares_fetch_goes_through_enforcer_assert(
    db_session: Session,
) -> None:
    """사후 assert 배선 회귀 — fetch 가 enforcer.assert 를 거치지 않으면(코드 제거 시)
    본 테스트 실패. WHERE 1차 필터와 별개의 defense-in-depth 고정(M5 #1 / Momus H1).

    non-empty 보장: effective_date=2024-03-30 (< _AS_OF) treasury_shares row 삽입.
    latest_active_by_key 가 1+ active record 를 반환한 뒤 assert 가 호출되어야 함.
    """
    _seed_fk(db_session, source="KRX")
    ts_row = TreasurySharesRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=date(2024, 3, 30),
        fiscal_period="2024Q1",
        shares_treasury=500_000,
        effective_date_precise=False,
        citation_id=_CID,
        superseded_by=None,
        created_at=datetime(2024, 3, 30, 9, 0, tzinfo=UTC),
    )
    db_session.add(treasury_shares_record_to_orm(ts_row))
    db_session.flush()

    repo = SqlTreasurySharesRepository(db_session)
    repo._enforcer = _AssertRaisingEnforcer()

    with pytest.raises(LookAheadError):
        repo.fetch_latest_active(_CODE, as_of=_AS_OF)


# =============================================================================
# 테스트 5 — SqlCorporateActionRepository.fetch_actions
# =============================================================================


def test_corporate_action_fetch_goes_through_enforcer_assert(
    db_session: Session,
) -> None:
    """사후 assert 배선 회귀 — fetch 가 enforcer.assert 를 거치지 않으면(코드 제거 시)
    본 테스트 실패. WHERE 1차 필터와 별개의 defense-in-depth 고정(M5 #1 / Momus H1).

    non-empty 보장: announced_date=2024-04-15 (< _AS_OF) corporate_action row 삽입.
    filter_active_records 가 announced_date 축으로 1+ active record 를 반환한 뒤
    assert_no_lookahead(date_of=announced_date) 가 호출되어야 함.
    """
    _seed_fk(db_session, source="KRX")
    ca_row = CorporateActionRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        action_type="split",
        announced_date=date(2024, 4, 15),
        effective_date=date(2024, 5, 15),  # 권리락일 — 정상적으로 announced_date 이후 가능
        payment_date=None,
        ratio=Decimal("2"),
        cash_amount=None,
        details={},
        citation_id=_CID,
        superseded_by=None,
        created_at=datetime(2024, 4, 15, 9, 0, tzinfo=UTC),
    )
    db_session.add(corporate_action_record_to_orm(ca_row))
    db_session.flush()

    repo = SqlCorporateActionRepository(db_session)
    repo._enforcer = _AssertRaisingEnforcer()

    with pytest.raises(LookAheadError):
        repo.fetch_actions(_CODE, as_of=_AS_OF)


# =============================================================================
# 테스트 6 — SqlMacroIndicatorRepository.fetch_latest
# =============================================================================


def test_macro_indicator_fetch_goes_through_vintage_enforcer_assert(
    db_session: Session,
) -> None:
    """사후 assert 배선 회귀 — fetch 가 enforcer.assert_no_vintage_lookahead 를
    거치지 않으면(코드 제거 시) 본 테스트 실패.
    WHERE 1차 필터와 별개의 defense-in-depth 고정(M5 #1 / Momus H1).

    macro 는 이중 시간축(reference_date / vintage_date) — _VintageAssertRaisingEnforcer
    가 assert_no_vintage_lookahead override. non-empty 보장: reference_date=2024-05-01
    / vintage_date=2024-05-10 (모두 < _AS_OF=2024-09-01) row 삽입.
    """
    # macro source 는 ECOS — BatchRunORM.source 도 ECOS 로 seed
    ecos_batch = UUID("00000000-0000-0000-0000-0000000000ee")
    ecos_cid = UUID("00000000-0000-0000-0000-00000000eeee")
    if db_session.get(BatchRunORM, ecos_batch) is None:
        db_session.add(
            BatchRunORM(
                id=ecos_batch,
                market=None,
                source="ECOS",
                started_at=datetime(2024, 6, 1, 0, 0, tzinfo=UTC),
                ended_at=datetime(2024, 6, 1, 0, 1, tzinfo=UTC),
                success_count=1,
                status=BATCH_STATUS_SUCCESS,
            )
        )
    if db_session.get(SourceCitationORM, ecos_cid) is None:
        db_session.add(citation_record_to_orm(SourceCitation(
            id=ecos_cid,
            source=SourceKind.ECOS,
            identifier="ecos-gate-test-001",
            retrieved_at=datetime(2024, 6, 1, 0, 0, tzinfo=UTC),
            effective_date=date(2024, 6, 1),
            adapter_version="1.0.0",
            batch_id=ecos_batch,
            url=None,
            created_at=datetime(2024, 6, 1, 0, 0, tzinfo=UTC),
        )))
    db_session.flush()

    macro_row = MacroIndicatorRecord(
        id=uuid4(),
        indicator_id=_INDICATOR,
        reference_date=date(2024, 5, 1),
        value=Decimal("3.50"),
        unit="percent",
        vintage_date=date(2024, 5, 10),
        citation_id=ecos_cid,
        created_at=datetime(2024, 6, 1, 0, 0, tzinfo=UTC),
    )
    db_session.add(macro_indicator_record_to_orm(macro_row))
    db_session.flush()

    repo = SqlMacroIndicatorRepository(db_session)
    repo._enforcer = _VintageAssertRaisingEnforcer()

    with pytest.raises(LookAheadError):
        repo.fetch_latest(_INDICATOR, as_of=_AS_OF)
