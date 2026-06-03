"""PIT Enforcer 단위 테스트 + Fake Repository contract test suite.

테스트 매트릭스 (oracle 자문 R2 의 5 시나리오 + 추가):
1. filter_records_at_or_before — 묵시적 필터
2. assert_no_lookahead — fail-fast
3. latest_active_record — supersede chain 3 단계
4. latest_active_record — tie-break by created_at
5. latest_active_record — effective_date == as_of 의 경계 (inclusive)
6. latest_active_record — as_of < min(effective_date) → NoActiveRecordError
7. latest_active_record — cycle 데이터 → PITDataCorruptionError
8. latest_active_record — chain depth 초과 → PITDataCorruptionError
9. latest_active_by_key — fiscal_period 별 그룹화
10. Fake Repository — PriceRepository / FinancialRepository / CorporateActionRepository / StockSnapshotRepository
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

import pytest

from app.repositories import (
    CorporateActionRecord,
    FinancialRecord,
    PriceRecord,
    StockSnapshotRecord,
)
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeFinancialRepository,
    FakePriceRepository,
    FakeStockSnapshotRepository,
)
from app.services.as_of_policy import PIT_POLICY_VERSION
from app.services.pit_enforcer import (
    LookAheadError,
    NoActiveRecordError,
    PITDataCorruptionError,
    PITEnforcer,
    PITError,
)

# =============================================================================
# Helpers — 테스트용 record builder. 의미 없는 필드는 placeholder.
# =============================================================================

_DUMMY_CITATION_ID: Final[UUID] = UUID("00000000-0000-0000-0000-00000000ffff")
_DUMMY_LINEAGE_ID: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000aa")


def _fin(
    *,
    fiscal_period: str,
    account: str = "net_income_consolidated_ifrs",
    code: str = "005930",
    value: float = 100.0,
    effective_date: date,
    created_at: datetime | None = None,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
) -> FinancialRecord:
    return FinancialRecord(
        id=record_id or uuid4(),
        code=code,
        code_lineage_id=_DUMMY_LINEAGE_ID,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        account=account,
        value=Decimal(str(value)),
        unit="krw",
        ifrs_type="consolidated",
        citation_id=_DUMMY_CITATION_ID,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


def _price(
    *,
    code: str = "005930",
    effective_date: date,
    close: float = 70000.0,
) -> PriceRecord:
    return PriceRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=_DUMMY_LINEAGE_ID,
        effective_date=effective_date,
        open_raw=Decimal(str(close)),
        high_raw=Decimal(str(close)),
        low_raw=Decimal(str(close)),
        close_raw=Decimal(str(close)),
        volume=1_000_000,
        trading_value=Decimal(str(close)) * Decimal(1_000_000),
        close_adjusted=Decimal(str(close)),
        citation_id=_DUMMY_CITATION_ID,
        created_at=datetime(effective_date.year, effective_date.month,
                            effective_date.day, 17, 0, tzinfo=UTC),
    )


def _ca(
    *,
    action_type: str = "split",
    code: str = "005930",
    announced_date: date,
    effective_date: date,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> CorporateActionRecord:
    return CorporateActionRecord(
        id=record_id or uuid4(),
        code=code,
        code_lineage_id=_DUMMY_LINEAGE_ID,
        action_type=action_type,
        announced_date=announced_date,
        effective_date=effective_date,
        payment_date=None,
        ratio=Decimal("50"),
        cash_amount=None,
        details={},
        citation_id=_DUMMY_CITATION_ID,
        superseded_by=superseded_by,
        created_at=created_at or datetime(announced_date.year, announced_date.month,
                                          announced_date.day, 9, 0, tzinfo=UTC),
    )


# =============================================================================
# 1. filter_records_at_or_before
# =============================================================================

def test_filter_records_at_or_before_includes_equal_date() -> None:
    """effective_date == as_of → inclusive (oracle 자문 §3 경계)."""
    enforcer = PITEnforcer()
    r1 = _fin(fiscal_period="2024Q1", effective_date=date(2024, 4, 1))
    r2 = _fin(fiscal_period="2024Q2", effective_date=date(2024, 7, 1))
    result = enforcer.filter_records_at_or_before([r1, r2], date(2024, 4, 1))
    assert result == [r1]


def test_filter_records_at_or_before_excludes_future() -> None:
    enforcer = PITEnforcer()
    r1 = _fin(fiscal_period="2024Q1", effective_date=date(2024, 4, 1))
    r2 = _fin(fiscal_period="2024Q2", effective_date=date(2024, 7, 1))
    result = enforcer.filter_records_at_or_before([r1, r2], date(2024, 5, 1))
    assert result == [r1]


def test_filter_preserves_input_order() -> None:
    enforcer = PITEnforcer()
    r1 = _fin(fiscal_period="2024Q2", effective_date=date(2024, 7, 1))
    r2 = _fin(fiscal_period="2024Q1", effective_date=date(2024, 4, 1))
    # input 순서 [r1, r2] — filter 가 같은 순서로 보존.
    result = enforcer.filter_records_at_or_before([r1, r2], date(2024, 8, 1))
    assert result == [r1, r2]


# =============================================================================
# 2. assert_no_lookahead — fail-fast
# =============================================================================

def test_assert_no_lookahead_passes_when_all_valid() -> None:
    enforcer = PITEnforcer()
    r1 = _fin(fiscal_period="2024Q1", effective_date=date(2024, 4, 1))
    enforcer.assert_no_lookahead([r1], date(2024, 5, 1))  # no raise


def test_assert_no_lookahead_raises_on_future_record() -> None:
    enforcer = PITEnforcer()
    r1 = _fin(fiscal_period="2024Q2", effective_date=date(2024, 7, 1),
              record_id=UUID("00000000-0000-0000-0000-000000000001"))
    with pytest.raises(LookAheadError) as exc:
        enforcer.assert_no_lookahead([r1], date(2024, 5, 1))
    assert exc.value.record_id == UUID("00000000-0000-0000-0000-000000000001")
    assert exc.value.effective_date == date(2024, 7, 1)
    assert exc.value.as_of == date(2024, 5, 1)


# =============================================================================
# 3. latest_active_record — supersede chain 3 단계 (oracle R2 시나리오 1)
# =============================================================================

def test_latest_active_record_3_stage_supersede_chain() -> None:
    """v1 → v2 → v3 정정 chain. as_of 시점별 active 가 달라야 함.

    Timeline:
        - 2024-04-15: v1 발표 (effective 2024-04-15, supersede 안 됨 처음)
        - 2024-05-20: v2 발표 (정정, effective 2024-04-15 같음, v1.superseded_by=v2.id)
        - 2024-08-10: v3 발표 (정정, effective 2024-04-15 같음, v2.superseded_by=v3.id)
    """
    enforcer = PITEnforcer()
    v1_id = UUID("00000000-0000-0000-0000-0000000000a1")
    v2_id = UUID("00000000-0000-0000-0000-0000000000a2")
    v3_id = UUID("00000000-0000-0000-0000-0000000000a3")

    v1 = _fin(
        fiscal_period="2024Q1",
        value=100,
        effective_date=date(2024, 4, 15),
        created_at=datetime(2024, 4, 15, 9, 0, tzinfo=UTC),
        superseded_by=v2_id,
        record_id=v1_id,
    )
    v2 = _fin(
        fiscal_period="2024Q1",
        value=110,
        effective_date=date(2024, 5, 20),  # 정정 effective
        created_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
        superseded_by=v3_id,
        record_id=v2_id,
    )
    v3 = _fin(
        fiscal_period="2024Q1",
        value=115,
        effective_date=date(2024, 8, 10),
        created_at=datetime(2024, 8, 10, 9, 0, tzinfo=UTC),
        record_id=v3_id,
    )
    records = [v1, v2, v3]

    # as_of=2024-05-01: v1 만 발효 (v2,v3 미래) → v1 active.
    assert enforcer.latest_active_record(records, date(2024, 5, 1)).id == v1_id

    # as_of=2024-06-01: v1, v2 발효. v1.superseded_by=v2, v2.effective(2024-05-20) <= as_of
    # → v1 은 그 시점에 이미 supersede. v2 가 active (v3 미래).
    assert enforcer.latest_active_record(records, date(2024, 6, 1)).id == v2_id

    # as_of=2024-09-01: 모두 발효, v3 가 head → v3 active.
    assert enforcer.latest_active_record(records, date(2024, 9, 1)).id == v3_id


# =============================================================================
# 4. tie-break by created_at (oracle R2 시나리오 2)
# =============================================================================

def test_latest_active_record_tie_breaks_by_created_at() -> None:
    """같은 effective_date 의 두 record (회계기간 소급 정정) → max(created_at) 우선."""
    enforcer = PITEnforcer()
    r_old = _fin(
        fiscal_period="2024Q1",
        value=100,
        effective_date=date(2024, 4, 15),
        created_at=datetime(2024, 4, 15, 9, 0, tzinfo=UTC),
    )
    r_new = _fin(
        fiscal_period="2024Q1",
        value=120,
        effective_date=date(2024, 4, 15),
        created_at=datetime(2024, 6, 1, 9, 0, tzinfo=UTC),
    )
    # 두 record 모두 active (supersede X), 같은 effective_date.
    result = enforcer.latest_active_record([r_old, r_new], date(2024, 7, 1))
    assert result == r_new


# =============================================================================
# 5. inclusive boundary (oracle R2 시나리오 3)
# =============================================================================

def test_latest_active_record_effective_date_equal_as_of_is_inclusive() -> None:
    enforcer = PITEnforcer()
    r = _fin(fiscal_period="2024Q1", effective_date=date(2024, 4, 15))
    result = enforcer.latest_active_record([r], date(2024, 4, 15))
    assert result == r


# =============================================================================
# 6. NoActiveRecordError (oracle R2 시나리오 4)
# =============================================================================

def test_latest_active_record_raises_when_no_candidates() -> None:
    enforcer = PITEnforcer()
    r = _fin(fiscal_period="2024Q1", effective_date=date(2024, 4, 15))
    with pytest.raises(NoActiveRecordError) as exc:
        enforcer.latest_active_record([r], date(2024, 4, 1))
    assert exc.value.as_of == date(2024, 4, 1)


def test_latest_active_record_raises_when_empty_list() -> None:
    enforcer = PITEnforcer()
    with pytest.raises(NoActiveRecordError):
        enforcer.latest_active_record([], date(2024, 5, 1))


# =============================================================================
# 7. PITDataCorruptionError — cycle (oracle R2 시나리오 5)
# =============================================================================

def test_latest_active_record_detects_cycle() -> None:
    """A.superseded_by=B, B.superseded_by=A — cycle → corruption."""
    enforcer = PITEnforcer()
    a_id = UUID("00000000-0000-0000-0000-0000000000c1")
    b_id = UUID("00000000-0000-0000-0000-0000000000c2")
    a = _fin(fiscal_period="2024Q1", effective_date=date(2024, 4, 1),
             superseded_by=b_id, record_id=a_id)
    b = _fin(fiscal_period="2024Q1", effective_date=date(2024, 5, 1),
             superseded_by=a_id, record_id=b_id)
    with pytest.raises(PITDataCorruptionError, match="cycle"):
        enforcer.latest_active_record([a, b], date(2024, 6, 1))


# =============================================================================
# 8. PITDataCorruptionError — chain depth
# =============================================================================

def test_latest_active_record_enforces_max_chain_depth() -> None:
    """chain 이 max_chain_depth 초과 → corruption (timeout 없이 즉시)."""
    enforcer = PITEnforcer(max_chain_depth=3)
    # 5 단계 linear chain
    ids = [UUID(f"00000000-0000-0000-0000-00000000d{i:03}") for i in range(5)]
    records = []
    for i, rid in enumerate(ids):
        next_id = ids[i + 1] if i + 1 < len(ids) else None
        records.append(_fin(
            fiscal_period="2024Q1",
            effective_date=date(2024, 1, i + 1),
            superseded_by=next_id,
            record_id=rid,
        ))
    with pytest.raises(PITDataCorruptionError, match="depth"):
        # as_of 가 모든 record 이후 → chain 끝까지 walk 시도 → depth 초과.
        enforcer.latest_active_record(records, date(2024, 12, 31))


def test_init_rejects_zero_or_negative_max_chain_depth() -> None:
    with pytest.raises(ValueError):
        PITEnforcer(max_chain_depth=0)
    with pytest.raises(ValueError):
        PITEnforcer(max_chain_depth=-1)


# =============================================================================
# 9. latest_active_by_key — fiscal_period 그룹화
# =============================================================================

def test_latest_active_by_key_groups_by_fiscal_period() -> None:
    enforcer = PITEnforcer()
    q1_v1 = _fin(fiscal_period="2024Q1", value=100, effective_date=date(2024, 4, 15))
    q1_v2 = _fin(fiscal_period="2024Q1", value=110, effective_date=date(2024, 5, 20),
                 created_at=datetime(2024, 5, 20, tzinfo=UTC))
    q2 = _fin(fiscal_period="2024Q2", value=200, effective_date=date(2024, 7, 15))
    q3_future = _fin(fiscal_period="2024Q3", value=300, effective_date=date(2024, 10, 15))

    result = enforcer.latest_active_by_key(
        [q1_v1, q1_v2, q2, q3_future],
        date(2024, 8, 1),
        key=lambda r: r.fiscal_period,
    )
    assert set(result.keys()) == {"2024Q1", "2024Q2"}  # Q3 미발효 — skip
    assert result["2024Q1"] == q1_v2  # 같은 fiscal 의 더 최근
    assert result["2024Q2"] == q2


# =============================================================================
# 10. PITError hierarchy + policy version
# =============================================================================

def test_error_hierarchy() -> None:
    for cls in (LookAheadError, NoActiveRecordError, PITDataCorruptionError):
        assert issubclass(cls, PITError)


def test_pit_enforcer_carries_policy_version() -> None:
    enforcer = PITEnforcer()
    assert enforcer.pit_policy_version == PIT_POLICY_VERSION == "1.0"


# =============================================================================
# 11. Fake Repository — PriceRepository
# =============================================================================

def test_fake_price_repository_filters_by_range() -> None:
    records = [
        _price(effective_date=date(2024, 1, 5)),
        _price(effective_date=date(2024, 2, 5)),
        _price(effective_date=date(2024, 3, 5)),
    ]
    repo = FakePriceRepository(records)
    result = repo.fetch_prices("005930", as_of=date(2024, 2, 28), start=date(2024, 1, 1))
    assert len(result) == 2
    assert all(r.effective_date <= date(2024, 2, 28) for r in result)


def test_fake_price_repository_returns_empty_for_unknown_code() -> None:
    repo = FakePriceRepository([])
    assert repo.fetch_prices("000000", as_of=date(2024, 12, 1), start=date(2024, 1, 1)) == []


def test_fake_price_repository_rejects_inverted_range() -> None:
    """start > as_of → 빈 list (의도적 — 호출자 책임)."""
    repo = FakePriceRepository([_price(effective_date=date(2024, 5, 1))])
    assert repo.fetch_prices("005930", as_of=date(2024, 1, 1), start=date(2024, 12, 1)) == []


# =============================================================================
# 12. Fake Repository — FinancialRepository (supersede chain 의 reference 구현)
# =============================================================================

def test_fake_financial_repository_resolves_supersede_chain() -> None:
    """T13 SQLAlchemy 구현체의 contract reference — 같은 시나리오로 양쪽 검증."""
    v1_id = UUID("00000000-0000-0000-0000-0000000000e1")
    v2_id = UUID("00000000-0000-0000-0000-0000000000e2")
    v1 = _fin(
        fiscal_period="2024Q1",
        value=100,
        effective_date=date(2024, 4, 15),
        superseded_by=v2_id,
        record_id=v1_id,
        created_at=datetime(2024, 4, 15, tzinfo=UTC),
    )
    v2 = _fin(
        fiscal_period="2024Q1",
        value=120,
        effective_date=date(2024, 5, 20),
        record_id=v2_id,
        created_at=datetime(2024, 5, 20, tzinfo=UTC),
    )
    repo = FakeFinancialRepository([v1, v2])

    # as_of=2024-05-01: v1 만 발효 → v1 반환
    result = repo.fetch_financials("005930", as_of=date(2024, 5, 1),
                                   account="net_income_consolidated_ifrs")
    assert len(result) == 1
    assert result[0].id == v1_id

    # as_of=2024-06-01: v1 supersede → v2 반환
    result = repo.fetch_financials("005930", as_of=date(2024, 6, 1),
                                   account="net_income_consolidated_ifrs")
    assert len(result) == 1
    assert result[0].id == v2_id


def test_fake_financial_repository_limits_to_max_periods() -> None:
    records = [
        _fin(fiscal_period=f"2024Q{q}", effective_date=date(2024, q * 3, 1))
        for q in range(1, 5)
    ]
    repo = FakeFinancialRepository(records)
    result = repo.fetch_financials(
        "005930", as_of=date(2024, 12, 31),
        account="net_income_consolidated_ifrs",
        max_periods=2,
    )
    assert len(result) == 2
    # 최근 2 개 (effective_date 오름차순 후 tail) — Q3, Q4
    assert result[-1].fiscal_period == "2024Q4"


# =============================================================================
# 13. Fake Repository — CorporateActionRepository (이중 PIT)
# =============================================================================

def test_fake_corporate_action_repository_uses_announced_date_for_pit() -> None:
    """announced_date <= as_of 가 정보 가용성 — effective 미래여도 사건은 알려진 상태."""
    # 2024-03-01 공시, 2024-03-15 발효
    ca = _ca(announced_date=date(2024, 3, 1), effective_date=date(2024, 3, 15))
    repo = FakeCorporateActionRepository([ca])

    # as_of=2024-03-05: 공시는 됐지만 발효 전 — 그래도 announced 기준이라 포함.
    result = repo.fetch_actions("005930", as_of=date(2024, 3, 5))
    assert len(result) == 1


def test_fake_corporate_action_repository_filters_by_action_types() -> None:
    splits = _ca(action_type="split", announced_date=date(2024, 3, 1),
                 effective_date=date(2024, 3, 15))
    dividend = _ca(action_type="cash_dividend", announced_date=date(2024, 3, 1),
                   effective_date=date(2024, 3, 15))
    repo = FakeCorporateActionRepository([splits, dividend])

    result = repo.fetch_actions("005930", as_of=date(2024, 4, 1),
                                 action_types=frozenset(["split"]))
    assert len(result) == 1
    assert result[0].action_type == "split"


def test_fake_corporate_action_repository_resolves_supersede_via_announced() -> None:
    """정정공시 — 옛 announce 가 supersede 됐는지 successor.announced_date 기준."""
    v1_id = UUID("00000000-0000-0000-0000-0000000000f1")
    v2_id = UUID("00000000-0000-0000-0000-0000000000f2")
    v1 = _ca(announced_date=date(2024, 3, 1), effective_date=date(2024, 3, 15),
             superseded_by=v2_id, record_id=v1_id)
    v2 = _ca(announced_date=date(2024, 4, 1), effective_date=date(2024, 3, 15),
             record_id=v2_id)
    repo = FakeCorporateActionRepository([v1, v2])

    # as_of=2024-03-15: v1 만 announce 됐고 v2 미래 → v1 active.
    result = repo.fetch_actions("005930", as_of=date(2024, 3, 15))
    assert len(result) == 1
    assert result[0].id == v1_id

    # as_of=2024-04-15: v1, v2 모두 announce → v1 supersede, v2 active.
    result = repo.fetch_actions("005930", as_of=date(2024, 4, 15))
    assert len(result) == 1
    assert result[0].id == v2_id


# =============================================================================
# 14. Fake Repository — StockSnapshotRepository
# =============================================================================

def test_fake_stock_snapshot_repository_exact_match() -> None:
    factor_uuid = UUID("00000000-0000-0000-0000-000000000fa1")
    snap = StockSnapshotRecord(
        stock_code="005930",
        as_of_date=date(2024, 5, 1),
        factor_uuid=factor_uuid,
        value=Decimal("12.34"),
        value_unit="ratio",
        inputs={},
        citation_id=_DUMMY_CITATION_ID,
        computed_at=datetime(2024, 5, 1, 17, 0, tzinfo=UTC),
    )
    repo = FakeStockSnapshotRepository([snap])

    # 정확 매치
    assert repo.fetch_snapshot("005930", as_of=date(2024, 5, 1),
                               factor_uuid=factor_uuid) == snap
    # 다른 as_of → None
    assert repo.fetch_snapshot("005930", as_of=date(2024, 5, 2),
                               factor_uuid=factor_uuid) is None
    # 다른 code → None
    assert repo.fetch_snapshot("000660", as_of=date(2024, 5, 1),
                               factor_uuid=factor_uuid) is None


def test_fake_stock_snapshot_repository_fetches_multiple_factors() -> None:
    factor_a = UUID("00000000-0000-0000-0000-000000000fa1")
    factor_b = UUID("00000000-0000-0000-0000-000000000fa2")
    snap_a = StockSnapshotRecord(
        stock_code="005930", as_of_date=date(2024, 5, 1), factor_uuid=factor_a,
        value=Decimal("12.34"), value_unit="ratio", inputs={},
        citation_id=_DUMMY_CITATION_ID,
        computed_at=datetime(2024, 5, 1, tzinfo=UTC),
    )
    snap_b = StockSnapshotRecord(
        stock_code="005930", as_of_date=date(2024, 5, 1), factor_uuid=factor_b,
        value=Decimal("1.2"), value_unit="ratio", inputs={},
        citation_id=_DUMMY_CITATION_ID,
        computed_at=datetime(2024, 5, 1, tzinfo=UTC),
    )
    repo = FakeStockSnapshotRepository([snap_a, snap_b])

    all_for_key = repo.fetch_snapshots_for_code("005930", as_of=date(2024, 5, 1))
    assert len(all_for_key) == 2

    filtered = repo.fetch_snapshots_for_code(
        "005930", as_of=date(2024, 5, 1), factor_uuids=frozenset([factor_a])
    )
    assert len(filtered) == 1
    assert filtered[0].factor_uuid == factor_a


# =============================================================================
# 15. StockSnapshotRecord — PITRecord property 호환
# =============================================================================

# =============================================================================
# 15-additional. oracle 2 차 리뷰 추가 회귀 — fan-in / 3-cycle / id final tiebreak /
#                 date_of strategy / id 캐싱 / __all__ 가시성
# =============================================================================

def test_chain_fan_in_two_predecessors_pointing_to_same_successor() -> None:
    """fan-in (한 successor 를 두 predecessor 가 가리킴, oracle C2).

    ADR-0009 D5 의 의도는 1:1 chain (한 정정공시 = 한 옛 record supersede) 이지만,
    schema 상 두 옛 record 가 같은 successor 를 가리키는 fan-in 데이터가 들어올 수
    있다. 두 predecessor 모두 supersede 된 상태 — successor 만 active.
    """
    enforcer = PITEnforcer()
    head_id = UUID("00000000-0000-0000-0000-0000000000bb")
    a_id = UUID("00000000-0000-0000-0000-0000000000b1")
    b_id = UUID("00000000-0000-0000-0000-0000000000b2")
    a = _fin(fiscal_period="2024Q1", effective_date=date(2024, 1, 1),
             superseded_by=head_id, record_id=a_id,
             created_at=datetime(2024, 1, 1, tzinfo=UTC))
    b = _fin(fiscal_period="2024Q1", effective_date=date(2024, 2, 1),
             superseded_by=head_id, record_id=b_id,
             created_at=datetime(2024, 2, 1, tzinfo=UTC))
    head = _fin(fiscal_period="2024Q1", effective_date=date(2024, 3, 1),
                record_id=head_id,
                created_at=datetime(2024, 3, 1, tzinfo=UTC))
    result = enforcer.latest_active_record([a, b, head], date(2024, 6, 1))
    assert result.id == head_id


def test_three_cycle_with_intermediate_future_date_is_detected() -> None:
    """A→B→C→A 3-cycle. 본 검증은 `_validate_chain_integrity` 가 walk 끝까지 가서 cycle detect.

    C1 finding 의 회귀 — 이전 1-step 알고리즘은 `successor.effective_date > as_of`
    early-return 으로 cycle 잠재 통과. 현재는 integrity 검증이 active 판정 전.
    """
    enforcer = PITEnforcer()
    a_id = UUID("00000000-0000-0000-0000-0000000000c1")
    b_id = UUID("00000000-0000-0000-0000-0000000000c2")
    c_id = UUID("00000000-0000-0000-0000-0000000000c3")
    a = _fin(fiscal_period="2024Q1", effective_date=date(2024, 1, 1),
             superseded_by=b_id, record_id=a_id)
    b = _fin(fiscal_period="2024Q1", effective_date=date(2024, 2, 1),
             superseded_by=c_id, record_id=b_id)
    # C 가 A 를 가리켜 cycle. C.effective 가 as_of 이후라도 integrity 검사가 먼저.
    c = _fin(fiscal_period="2024Q1", effective_date=date(2024, 4, 1),
             superseded_by=a_id, record_id=c_id)
    with pytest.raises(PITDataCorruptionError, match="cycle"):
        enforcer.latest_active_record([a, b, c], date(2024, 3, 1))


def test_latest_active_record_id_is_final_tiebreaker() -> None:
    """oracle C6 — 같은 effective_date + 같은 created_at 의 두 record → id 로 결정성 확보."""
    enforcer = PITEnforcer()
    same_dt = datetime(2024, 4, 1, tzinfo=UTC)
    id_low = UUID("00000000-0000-0000-0000-000000000001")
    id_high = UUID("00000000-0000-0000-0000-0000000000ff")
    r1 = _fin(fiscal_period="2024Q1", effective_date=date(2024, 4, 15),
              created_at=same_dt, record_id=id_low)
    r2 = _fin(fiscal_period="2024Q1", effective_date=date(2024, 4, 15),
              created_at=same_dt, record_id=id_high)
    result = enforcer.latest_active_record([r1, r2], date(2024, 5, 1))
    # str(id_high) > str(id_low) → r2 우선.
    assert result.id == id_high


def test_latest_active_record_date_of_strategy_for_announced_date() -> None:
    """oracle C5 — date_of strategy 가 announced_date 등 다른 컬럼 PIT 지원."""
    enforcer = PITEnforcer()
    # 2024-03-01 공시 / 2024-03-15 발효.
    ca = _ca(announced_date=date(2024, 3, 1), effective_date=date(2024, 3, 15))
    # as_of=2024-03-10: announced <= as_of 통과 but effective > as_of.
    result = enforcer.latest_active_record(
        [ca], date(2024, 3, 10), date_of=lambda r: r.announced_date
    )
    assert result.id == ca.id


def test_stock_snapshot_record_id_is_cached() -> None:
    """oracle C4 — id 가 __post_init__ 에서 한 번만 계산되어 캐싱."""
    snap = StockSnapshotRecord(
        stock_code="005930", as_of_date=date(2024, 5, 1),
        factor_uuid=UUID("00000000-0000-0000-0000-000000000fa1"),
        value=Decimal("12.34"), value_unit="ratio", inputs={},
        citation_id=_DUMMY_CITATION_ID,
        computed_at=datetime(2024, 5, 1, tzinfo=UTC),
    )
    id_a = snap.id
    id_b = snap.id
    # 같은 instance → 같은 객체 (`is`) — property 가 매번 새 UUID 만들면 `is` False.
    assert id_a is id_b


def test_pit_enforcer_no_object_setattr_cargo_culting() -> None:
    """oracle M6 — pit_policy_version 이 정상 인스턴스 attribute (frozen 우회 X)."""
    enforcer = PITEnforcer()
    # 직접 할당이 가능 (Final type hint 는 mypy 만 강제, runtime 차단 X)
    # — Final 의 의도는 표명일 뿐. cargo culting 의 부재 확인.
    assert enforcer.pit_policy_version == "1.0"


def test_corporate_action_supersede_with_changing_effective_date() -> None:
    """oracle 2 차 NEW-C1 회귀 — 정정공시가 effective_date 도 변경하는 경우.

    v1 (announced 2024-03-01, effective 2024-03-15) → v2 (announced 2024-04-01,
    effective 2024-03-20, 발효일 정정).

    이전 알고리즘은 `(action_type, effective_date)` 를 group key 로 써서 v1/v2
    가 다른 group → cross-group chain 단절 → v1 이 false-active.
    `filter_active_records` 는 group key 불필요 — chain 정체성으로 자연 해소.
    """
    v1_id = UUID("00000000-0000-0000-0000-0000000000d1")
    v2_id = UUID("00000000-0000-0000-0000-0000000000d2")
    v1 = _ca(
        action_type="split",
        announced_date=date(2024, 3, 1),
        effective_date=date(2024, 3, 15),
        superseded_by=v2_id,
        record_id=v1_id,
    )
    v2 = _ca(
        action_type="split",
        announced_date=date(2024, 4, 1),
        effective_date=date(2024, 3, 20),  # 발효일이 정정에서 바뀜
        record_id=v2_id,
    )
    repo = FakeCorporateActionRepository([v1, v2])

    # as_of=2024-04-15: 둘 다 announced. v1 은 v2 가 announce 된 시점에
    # supersede → v2 만 active.
    result = repo.fetch_actions("005930", as_of=date(2024, 4, 15))
    assert len(result) == 1, f"expected v2 only, got {[r.id for r in result]}"
    assert result[0].id == v2_id


def test_filter_active_records_handles_multi_chain_independently() -> None:
    """여러 별개 chain 이 한 records list 에 공존 시 각 chain head 가 모두 결과에."""
    enforcer = PITEnforcer()
    # Chain A: a1 → a2 (정정)
    a1_id = UUID("00000000-0000-0000-0000-0000000000a1")
    a2_id = UUID("00000000-0000-0000-0000-0000000000a2")
    a1 = _ca(
        action_type="split",
        announced_date=date(2024, 1, 1), effective_date=date(2024, 1, 15),
        superseded_by=a2_id, record_id=a1_id,
    )
    a2 = _ca(
        action_type="split",
        announced_date=date(2024, 2, 1), effective_date=date(2024, 1, 20),
        record_id=a2_id,
    )
    # Chain B: 별개 사건 b1 (정정 없음)
    b1 = _ca(
        action_type="cash_dividend",
        announced_date=date(2024, 3, 1), effective_date=date(2024, 3, 15),
    )
    active = enforcer.filter_active_records(
        [a1, a2, b1], date(2024, 6, 1), date_of=lambda r: r.announced_date
    )
    assert {r.id for r in active} == {a2_id, b1.id}


def test_filter_active_records_validates_chain_integrity() -> None:
    """cycle 발생 시 filter_active_records 도 PITDataCorruptionError."""
    enforcer = PITEnforcer()
    a_id = UUID("00000000-0000-0000-0000-0000000000aa")
    b_id = UUID("00000000-0000-0000-0000-0000000000bb")
    a = _ca(announced_date=date(2024, 1, 1), effective_date=date(2024, 1, 15),
            superseded_by=b_id, record_id=a_id)
    b = _ca(announced_date=date(2024, 2, 1), effective_date=date(2024, 2, 15),
            superseded_by=a_id, record_id=b_id)
    with pytest.raises(PITDataCorruptionError, match="cycle"):
        enforcer.filter_active_records([a, b], date(2024, 6, 1),
                                       date_of=lambda r: r.announced_date)


def test_fakes_all_export_is_explicit() -> None:
    """oracle L1 / M2 — `__all__` 명시로 wildcard import 명확화."""
    from app.repositories import fakes as fakes_mod
    expected = {
        "FakeCorporateActionRepository",
        "FakeFinancialRepository",
        "FakeMacroIndicatorRepository",
        "FakeMarketCapRepository",
        "FakeNavRepository",
        "FakePriceRepository",
        "FakeStockSnapshotRepository",
        "FakeTreasurySharesRepository",
    }
    assert set(fakes_mod.__all__) == expected


def test_stock_snapshot_record_satisfies_pit_record_protocol() -> None:
    factor_uuid = UUID("00000000-0000-0000-0000-000000000fa1")
    snap = StockSnapshotRecord(
        stock_code="005930", as_of_date=date(2024, 5, 1), factor_uuid=factor_uuid,
        value=Decimal("12.34"), value_unit="ratio", inputs={},
        citation_id=_DUMMY_CITATION_ID,
        computed_at=datetime(2024, 5, 1, tzinfo=UTC),
    )
    # property 노출 — effective_date / id / created_at
    assert snap.effective_date == snap.as_of_date
    assert isinstance(snap.id, UUID)
    assert snap.created_at == snap.computed_at
