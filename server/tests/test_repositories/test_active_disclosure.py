"""fetch_active_disclosure / fetch_active_treasury_disclosure + 다중 active head
corruption assertion — Fake repository 단위 테스트 (DART 정정공시 배치 read-only 경로).

핵심:
    1. active(superseded_by IS NULL) head 의 (rcept_no, rows) 반환.
    2. active 전무 → (None, ()) / (None, None).
    3. **다중 rcept_no(중복 active head) → PITDataCorruptionError** (no-UNIQUE
       가드의 fail-loud 방어선, oracle Critical). financials(account 축) + treasury
       (account 축 없음) 양쪽.
    4. update_superseded_by — chain 1 회성·self-FK 무결성 불변식.
    5. Caching 위임 — inner 위임 동작.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.repositories.caching_repositories import (
    CachingFinancialRepository,
    CachingTreasurySharesRepository,
)
from app.repositories.fakes import (
    FakeFinancialRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    FinancialRecord,
    TreasurySharesRecord,
)
from app.repositories.sql_repositories import AppendOnlyViolationError
from app.services.pit_enforcer import PITDataCorruptionError

_LINEAGE = UUID("00000000-0000-0000-0000-0000000000aa")
_NOW = datetime(2024, 1, 1, tzinfo=UTC)


def _fin(
    *,
    record_id: UUID,
    account: str,
    value: str,
    citation_id: UUID,
    ifrs_type: str = "consolidated",
    fiscal_period: str = "2023Q4",
    superseded_by: UUID | None = None,
    effective_date: date = date(2024, 5, 1),
) -> FinancialRecord:
    return FinancialRecord(
        id=record_id,
        code="005930",
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        account=account,
        value=Decimal(value),
        unit="krw",
        ifrs_type=ifrs_type,
        citation_id=citation_id,
        superseded_by=superseded_by,
        created_at=_NOW,
    )


def _tre(
    *,
    record_id: UUID,
    shares: int,
    citation_id: UUID,
    fiscal_period: str = "2023Q4",
    superseded_by: UUID | None = None,
) -> TreasurySharesRecord:
    return TreasurySharesRecord(
        id=record_id,
        code="005930",
        code_lineage_id=_LINEAGE,
        effective_date=date(2024, 5, 1),
        fiscal_period=fiscal_period,
        shares_treasury=shares,
        citation_id=citation_id,
        superseded_by=superseded_by,
        created_at=_NOW,
    )


# =============================================================================
# financial — fetch_active_disclosure
# =============================================================================

def test_fetch_active_disclosure_returns_active_head() -> None:
    c1 = uuid4()
    r1 = _fin(record_id=uuid4(), account="total_assets", value="100",
              citation_id=c1)
    r2 = _fin(record_id=uuid4(), account="total_equity", value="40",
              citation_id=c1)
    repo = FakeFinancialRepository(
        records=(r1, r2),
        citation_identifiers={c1: "20240501000001"},
    )
    rcept, rows = repo.fetch_active_disclosure("005930", "2023Q4", "consolidated")
    assert rcept == "20240501000001"
    assert {r.account for r in rows} == {"total_assets", "total_equity"}


def test_fetch_active_disclosure_empty_returns_none() -> None:
    repo = FakeFinancialRepository(records=())
    assert repo.fetch_active_disclosure("005930", "2023Q4", "consolidated") == (
        None, (),
    )


def test_fetch_active_disclosure_excludes_superseded() -> None:
    c_old, c_new = uuid4(), uuid4()
    new_id = uuid4()
    old = _fin(record_id=uuid4(), account="total_assets", value="100",
               citation_id=c_old, superseded_by=new_id)
    new = _fin(record_id=new_id, account="total_assets", value="150",
               citation_id=c_new)
    repo = FakeFinancialRepository(
        records=(old, new),
        citation_identifiers={c_old: "20240501000001", c_new: "20240815000009"},
    )
    rcept, rows = repo.fetch_active_disclosure("005930", "2023Q4", "consolidated")
    assert rcept == "20240815000009"
    assert len(rows) == 1 and str(rows[0].value) == "150"


def test_fetch_active_disclosure_multi_rcept_raises_corruption() -> None:
    # 두 active row 가 서로 다른 rcept_no (중복 active head) → corruption.
    c1, c2 = uuid4(), uuid4()
    r1 = _fin(record_id=uuid4(), account="total_assets", value="100",
              citation_id=c1)
    r2 = _fin(record_id=uuid4(), account="total_equity", value="40",
              citation_id=c2)
    repo = FakeFinancialRepository(
        records=(r1, r2),
        citation_identifiers={c1: "20240501000001", c2: "20240502000002"},
    )
    with pytest.raises(PITDataCorruptionError, match="multiple active rcept_no"):
        repo.fetch_active_disclosure("005930", "2023Q4", "consolidated")


def test_fetch_active_disclosure_ifrs_independent() -> None:
    # 같은 (code, fiscal_period) 의 CFS / OFS active 가 독립.
    c_cfs, c_ofs = uuid4(), uuid4()
    cfs = _fin(record_id=uuid4(), account="total_assets", value="100",
               citation_id=c_cfs, ifrs_type="consolidated")
    ofs = _fin(record_id=uuid4(), account="total_assets", value="90",
               citation_id=c_ofs, ifrs_type="separate")
    repo = FakeFinancialRepository(
        records=(cfs, ofs),
        citation_identifiers={c_cfs: "20240501000001", c_ofs: "20240501000002"},
    )
    rcept_cfs, _ = repo.fetch_active_disclosure(
        "005930", "2023Q4", "consolidated")
    rcept_ofs, _ = repo.fetch_active_disclosure(
        "005930", "2023Q4", "separate")
    assert rcept_cfs == "20240501000001"
    assert rcept_ofs == "20240501000002"


# =============================================================================
# financial — update_superseded_by
# =============================================================================

def test_update_superseded_by_sets_chain() -> None:
    c = uuid4()
    old_id, new_id = uuid4(), uuid4()
    old = _fin(record_id=old_id, account="total_assets", value="100",
               citation_id=c)
    new = _fin(record_id=new_id, account="total_assets", value="150",
               citation_id=c)
    repo = FakeFinancialRepository(records=(old, new))
    repo.update_superseded_by(old_id, new_id)
    # old 가 supersede 됨 — active head 는 new 만.
    rcept, rows = repo.fetch_active_disclosure(
        "005930", "2023Q4", "consolidated")
    # citation_identifiers 미주입 → "" 단일 → 정상.
    assert len(rows) == 1 and rows[0].id == new_id


def test_update_superseded_by_already_superseded_raises() -> None:
    c = uuid4()
    old_id, new_id = uuid4(), uuid4()
    old = _fin(record_id=old_id, account="a", value="1", citation_id=c,
               superseded_by=new_id)
    new = _fin(record_id=new_id, account="a", value="2", citation_id=c)
    repo = FakeFinancialRepository(records=(old, new))
    with pytest.raises(AppendOnlyViolationError, match="이미 superseded"):
        repo.update_superseded_by(old_id, new_id)


def test_update_superseded_by_missing_successor_raises() -> None:
    c = uuid4()
    old_id = uuid4()
    old = _fin(record_id=old_id, account="a", value="1", citation_id=c)
    repo = FakeFinancialRepository(records=(old,))
    with pytest.raises(AppendOnlyViolationError, match="successor"):
        repo.update_superseded_by(old_id, uuid4())


# =============================================================================
# treasury — fetch_active_treasury_disclosure (account 축 없음)
# =============================================================================

def test_fetch_active_treasury_returns_single() -> None:
    c = uuid4()
    rec = _tre(record_id=uuid4(), shares=5000, citation_id=c)
    repo = FakeTreasurySharesRepository(
        records=(rec,),
        citation_identifiers={c: "20240501000001"},
    )
    rcept, row = repo.fetch_active_treasury_disclosure("005930", "2023Q4")
    assert rcept == "20240501000001"
    assert row is not None and row.shares_treasury == 5000


def test_fetch_active_treasury_empty_returns_none() -> None:
    repo = FakeTreasurySharesRepository(records=())
    assert repo.fetch_active_treasury_disclosure("005930", "2023Q4") == (
        None, None,
    )


def test_fetch_active_treasury_multi_active_raises() -> None:
    # account 축이 없으므로 active 2건 = corruption (같은 rcept 라도 row 수로 판정).
    c1, c2 = uuid4(), uuid4()
    r1 = _tre(record_id=uuid4(), shares=5000, citation_id=c1)
    r2 = _tre(record_id=uuid4(), shares=6000, citation_id=c2)
    repo = FakeTreasurySharesRepository(
        records=(r1, r2),
        citation_identifiers={c1: "20240501000001", c2: "20240502000002"},
    )
    with pytest.raises(PITDataCorruptionError, match="multiple active treasury"):
        repo.fetch_active_treasury_disclosure("005930", "2023Q4")


def test_treasury_update_superseded_by() -> None:
    c = uuid4()
    old_id, new_id = uuid4(), uuid4()
    old = _tre(record_id=old_id, shares=5000, citation_id=c)
    new = _tre(record_id=new_id, shares=6000, citation_id=c)
    repo = FakeTreasurySharesRepository(records=(old, new))
    repo.update_superseded_by(old_id, new_id)
    _, row = repo.fetch_active_treasury_disclosure("005930", "2023Q4")
    assert row is not None and row.id == new_id


# =============================================================================
# Caching 위임
# =============================================================================

def test_caching_financial_delegates_active_disclosure() -> None:
    c = uuid4()
    rec = _fin(record_id=uuid4(), account="total_assets", value="100",
               citation_id=c)
    inner = FakeFinancialRepository(
        records=(rec,), citation_identifiers={c: "20240501000001"},
    )
    caching = CachingFinancialRepository(inner)
    rcept, rows = caching.fetch_active_disclosure(
        "005930", "2023Q4", "consolidated")
    assert rcept == "20240501000001" and len(rows) == 1


def test_caching_treasury_delegates_active_disclosure() -> None:
    c = uuid4()
    rec = _tre(record_id=uuid4(), shares=5000, citation_id=c)
    inner = FakeTreasurySharesRepository(
        records=(rec,), citation_identifiers={c: "20240501000001"},
    )
    caching = CachingTreasurySharesRepository(inner)
    rcept, row = caching.fetch_active_treasury_disclosure("005930", "2023Q4")
    assert rcept == "20240501000001" and row is not None
