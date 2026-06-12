"""CachingFinancialRepository / CachingTreasurySharesRepository 단위 테스트.

per-code(financial·treasury) N+1 완화 — universe 루프 **이전** 1회 bulk prime 으로
종목별 개별 쿼리 N 회를 청크 쿼리 몇 회로 축약. price 와 두 가지가 다르다:
    - **raw vintage 적재**: account/ifrs_type 이 serve-time 가변(factor 별 다른
      account)이라 resolved 가 아닌 raw row 를 적재, serve-time 에 공유 helper
      (pit_resolution)로 단건과 byte-동일 해소.
    - **date 사전필터 없음**(oracle 설계검토 C1): chain integrity 가 candidate
      전체에 의존하므로 전체 vintage 적재.

테스트 매트릭스 (financial):
    1. prime 은 inner.fetch_all_financials_bulk 1회만 호출 (per-code single 0).
    2. prime 후 fetch_financials 는 캐시 서빙 (inner single 추가 호출 0 — N+1 제거).
    3. 캐시 반환이 inner 단건 fetch 와 byte-동일 (해소·sort·truncate 동일).
    4. serve-time 에 account 가 가변이어도 각각 정확 (raw 적재의 핵심 이점).
    5. 정정 chain 이 serve-time 에 해소 (옛 row superseded → 후속 반환).
    6. 다른 as_of / batch_cutoff → inner 위임 (캐시 키 격리, 재현성).
    7. 미prime → inner 위임.
    8. inner 가 bulk 미지원 → graceful degrade (미prime, 매 fetch inner 위임).

테스트 매트릭스 (treasury):
    9. prime 은 fetch_all_treasury_bulk 1회 / 이후 fetch_latest_active 캐시 서빙.
    10. 캐시 반환이 단건 fetch_latest_active 와 byte-동일.
    11. 데이터 없는 code → None.
    12. 다른 as_of / batch_cutoff → inner 위임.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.repositories.batch_run_repository import EXCLUDE_ALL_CUTOFF
from app.repositories.caching_repositories import (
    CachingFinancialRepository,
    CachingTreasurySharesRepository,
)
from app.repositories.fakes import (
    FakeFinancialRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import FinancialRecord, TreasurySharesRecord
from app.services.pit_enforcer import PITDataCorruptionError

_LINEAGE: UUID = UUID("00000000-0000-0000-0000-0000000000aa")
_CITATION: UUID = UUID("00000000-0000-0000-0000-00000000ffff")


def _fin(
    *,
    code: str,
    fiscal_period: str,
    account: str,
    value: str,
    effective_date: date,
    ifrs_type: str = "consolidated",
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> FinancialRecord:
    return FinancialRecord(
        id=record_id or uuid4(),
        code=code,
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        account=account,
        value=Decimal(value),
        unit="krw",
        ifrs_type=ifrs_type,
        citation_id=_CITATION,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


def _treasury(
    *,
    code: str,
    fiscal_period: str,
    effective_date: date,
    shares_treasury: int,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> TreasurySharesRecord:
    return TreasurySharesRecord(
        id=record_id or uuid4(),
        code=code,
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        shares_treasury=shares_treasury,
        effective_date_precise=False,
        citation_id=_CITATION,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


class _CountingFinancial:
    """FakeFinancialRepository 위임 + bulk vs single 호출 카운트."""

    def __init__(self, records: list[FinancialRecord]) -> None:
        self._fake = FakeFinancialRepository(records)
        self.bulk_calls = 0
        self.single_calls = 0

    def fetch_financials(
        self, code, *, as_of, account, ifrs_type=None, max_periods=8,
        batch_cutoff=None,
    ):
        self.single_calls += 1
        return self._fake.fetch_financials(
            code, as_of=as_of, account=account, ifrs_type=ifrs_type,
            max_periods=max_periods, batch_cutoff=batch_cutoff,
        )

    def fetch_all_financials_bulk(self, codes, *, batch_cutoff=None):
        self.bulk_calls += 1
        return self._fake.fetch_all_financials_bulk(
            codes, batch_cutoff=batch_cutoff,
        )

    def fetch_restatement_history(self, code, *, fiscal_period=None, as_of=None):
        return self._fake.fetch_restatement_history(
            code, fiscal_period=fiscal_period, as_of=as_of,
        )

    def save_financials(self, records):
        self._fake.save_financials(records)


class _NoBulkFinancial:
    """fetch_financials 만 가진 최소 stub — bulk 미지원 graceful degrade 검증."""

    def __init__(self, records: list[FinancialRecord]) -> None:
        self._fake = FakeFinancialRepository(records)
        self.single_calls = 0

    def fetch_financials(
        self, code, *, as_of, account, ifrs_type=None, max_periods=8,
        batch_cutoff=None,
    ):
        self.single_calls += 1
        return self._fake.fetch_financials(
            code, as_of=as_of, account=account, ifrs_type=ifrs_type,
            max_periods=max_periods, batch_cutoff=batch_cutoff,
        )


class _CountingTreasury:
    """FakeTreasurySharesRepository 위임 + bulk vs single 호출 카운트."""

    def __init__(self, records: list[TreasurySharesRecord]) -> None:
        self._fake = FakeTreasurySharesRepository(records)
        self.bulk_calls = 0
        self.single_calls = 0

    def fetch_latest_active(self, code, *, as_of, batch_cutoff=None):
        self.single_calls += 1
        return self._fake.fetch_latest_active(
            code, as_of=as_of, batch_cutoff=batch_cutoff,
        )

    def fetch_all_treasury_bulk(self, codes, *, batch_cutoff=None):
        self.bulk_calls += 1
        return self._fake.fetch_all_treasury_bulk(codes, batch_cutoff=batch_cutoff)

    def save_treasury_shares(self, records):
        self._fake.save_treasury_shares(records)


_AS_OF = date(2024, 6, 1)
_CODES = ["005930", "000660", "035420"]


def _universe_financials() -> list[FinancialRecord]:
    """3종목 × 여러 account/fiscal_period (일부 정정 chain 포함)."""
    recs: list[FinancialRecord] = []
    for code in _CODES:
        recs.append(_fin(
            code=code, fiscal_period="2024Q1", account="net_income",
            value="100", effective_date=date(2024, 5, 15),
        ))
        recs.append(_fin(
            code=code, fiscal_period="2023Q4", account="net_income",
            value="80", effective_date=date(2024, 3, 30),
        ))
        recs.append(_fin(
            code=code, fiscal_period="2024Q1", account="total_equity",
            value="5000", effective_date=date(2024, 5, 15),
        ))
    return recs


# =============================================================================
# 1~2. prime 1회 bulk + 캐시 서빙 (N+1 제거)
# =============================================================================

def test_financial_prime_calls_bulk_once_then_serves_from_cache() -> None:
    inner = _CountingFinancial(_universe_financials())
    cached = CachingFinancialRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)

    assert inner.bulk_calls == 1
    assert inner.single_calls == 0

    # 3종목 × net_income fetch → 전부 캐시 서빙 (inner single 0 유지).
    for code in _CODES:
        result = cached.fetch_financials(
            code, as_of=_AS_OF, account="net_income", max_periods=8,
        )
        assert len(result) == 2  # 2024Q1 + 2023Q4
    assert inner.single_calls == 0  # N+1 제거 — 단건 위임 0.


# =============================================================================
# 3. 캐시 == 단건 byte-동일
# =============================================================================

def test_financial_cache_byte_identical_to_single_fetch() -> None:
    records = _universe_financials()
    inner = _CountingFinancial(records)
    single_ref = FakeFinancialRepository(records)
    cached = CachingFinancialRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)

    for code in _CODES:
        for account in ("net_income", "total_equity"):
            via_cache = list(cached.fetch_financials(
                code, as_of=_AS_OF, account=account,
            ))
            via_single = list(single_ref.fetch_financials(
                code, as_of=_AS_OF, account=account,
            ))
            # id·value·fiscal_period·정렬 전부 동일해야 함 (§2.10 재현성).
            assert [r.id for r in via_cache] == [r.id for r in via_single]
            assert [r.value for r in via_cache] == [r.value for r in via_single]


# =============================================================================
# 4. account serve-time 가변 — raw 적재의 핵심 이점
# =============================================================================

def test_financial_serve_time_account_variance_single_prime() -> None:
    """한 번 prime 으로 종목당 여러 account 를 정확히 서빙 (bulk 1회 유지)."""
    inner = _CountingFinancial(_universe_financials())
    cached = CachingFinancialRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)

    ni = cached.fetch_financials("005930", as_of=_AS_OF, account="net_income")
    eq = cached.fetch_financials("005930", as_of=_AS_OF, account="total_equity")
    assert ni[-1].value == Decimal("100")
    assert eq[-1].value == Decimal("5000")
    # 미존재 account → 빈 결과 (N/A 자연 처리).
    none_acc = cached.fetch_financials("005930", as_of=_AS_OF, account="ZZZ")
    assert none_acc == ()
    assert inner.bulk_calls == 1
    assert inner.single_calls == 0


# =============================================================================
# 5. 정정 chain serve-time 해소
# =============================================================================

def test_financial_supersede_chain_resolved_at_serve_time() -> None:
    orig_id, succ_id = uuid4(), uuid4()
    records = [
        _fin(
            code="005930", fiscal_period="2024Q1", account="net_income",
            value="100", effective_date=date(2024, 5, 15),
            record_id=orig_id, superseded_by=succ_id,
            created_at=datetime(2024, 5, 15, tzinfo=UTC),
        ),
        _fin(
            code="005930", fiscal_period="2024Q1", account="net_income",
            value="130", effective_date=date(2024, 5, 15),
            record_id=succ_id,
            created_at=datetime(2024, 5, 28, tzinfo=UTC),
        ),
    ]
    cached = CachingFinancialRepository(_CountingFinancial(records))
    cached.prime(["005930"], as_of=_AS_OF)
    result = cached.fetch_financials("005930", as_of=_AS_OF, account="net_income")
    # 후속(정정) row 반환 — 옛 row 는 superseded.
    assert len(result) == 1
    assert result[0].id == succ_id
    assert result[0].value == Decimal("130")


# =============================================================================
# 6~7. 캐시 키 격리 / 미prime → inner 위임
# =============================================================================

def test_financial_different_as_of_delegates_to_inner() -> None:
    inner = _CountingFinancial(_universe_financials())
    cached = CachingFinancialRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)
    inner.single_calls = 0
    # prime 과 다른 as_of → 캐시 cover 못 함 → inner 위임 (silent truncate 금지).
    cached.fetch_financials("005930", as_of=date(2023, 1, 1), account="net_income")
    assert inner.single_calls == 1


def test_financial_different_cutoff_delegates_to_inner() -> None:
    inner = _CountingFinancial(_universe_financials())
    cached = CachingFinancialRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF, batch_cutoff=None)
    inner.single_calls = 0
    cached.fetch_financials(
        "005930", as_of=_AS_OF, account="net_income",
        batch_cutoff=EXCLUDE_ALL_CUTOFF,
    )
    assert inner.single_calls == 1


def test_financial_unprimed_delegates_to_inner() -> None:
    inner = _CountingFinancial(_universe_financials())
    cached = CachingFinancialRepository(inner)
    # prime 미호출 → 매 fetch inner 위임.
    cached.fetch_financials("005930", as_of=_AS_OF, account="net_income")
    assert inner.single_calls == 1
    assert inner.bulk_calls == 0


# =============================================================================
# 8. graceful degrade — inner bulk 미지원
# =============================================================================

def test_financial_graceful_degrade_without_bulk() -> None:
    inner = _NoBulkFinancial(_universe_financials())
    cached = CachingFinancialRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)  # bulk 없음 → 미prime
    result = cached.fetch_financials("005930", as_of=_AS_OF, account="net_income")
    # 정확성 동일 — inner 단건 위임으로 서빙.
    assert len(result) == 2
    assert inner.single_calls == 1


# =============================================================================
# 9~12. treasury
# =============================================================================

def _universe_treasury() -> list[TreasurySharesRecord]:
    recs: list[TreasurySharesRecord] = []
    for code in _CODES:
        recs.append(_treasury(
            code=code, fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=100,
        ))
        recs.append(_treasury(
            code=code, fiscal_period="2024Q1",
            effective_date=date(2024, 5, 15), shares_treasury=200,
        ))
    return recs


def test_treasury_prime_once_then_serves_from_cache() -> None:
    inner = _CountingTreasury(_universe_treasury())
    cached = CachingTreasurySharesRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)
    assert inner.bulk_calls == 1
    assert inner.single_calls == 0
    for code in _CODES:
        rec = cached.fetch_latest_active(code, as_of=_AS_OF)
        assert rec is not None
        assert rec.fiscal_period == "2024Q1"
        assert rec.shares_treasury == 200
    assert inner.single_calls == 0  # N+1 제거.


def test_treasury_cache_byte_identical_to_single() -> None:
    records = _universe_treasury()
    single_ref = FakeTreasurySharesRepository(records)
    cached = CachingTreasurySharesRepository(_CountingTreasury(records))
    cached.prime(_CODES, as_of=_AS_OF)
    for code in _CODES:
        via_cache = cached.fetch_latest_active(code, as_of=_AS_OF)
        via_single = single_ref.fetch_latest_active(code, as_of=_AS_OF)
        assert via_cache is not None and via_single is not None
        assert via_cache.id == via_single.id
        assert via_cache.shares_treasury == via_single.shares_treasury


def test_treasury_missing_code_returns_none() -> None:
    cached = CachingTreasurySharesRepository(_CountingTreasury(_universe_treasury()))
    cached.prime(_CODES, as_of=_AS_OF)
    # universe 에 prime 됐으나 데이터 없는 code → None (단건과 동일).
    assert cached.fetch_latest_active("999999", as_of=_AS_OF) is None


def test_treasury_different_as_of_delegates() -> None:
    inner = _CountingTreasury(_universe_treasury())
    cached = CachingTreasurySharesRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)
    inner.single_calls = 0
    cached.fetch_latest_active("005930", as_of=date(2023, 1, 1))
    assert inner.single_calls == 1


def test_treasury_supersede_chain_resolved_at_serve_time() -> None:
    orig_id, succ_id = uuid4(), uuid4()
    records = [
        _treasury(
            code="005930", fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=100,
            record_id=orig_id, superseded_by=succ_id,
            created_at=datetime(2024, 3, 30, tzinfo=UTC),
        ),
        _treasury(
            code="005930", fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30), shares_treasury=150,
            record_id=succ_id,
            created_at=datetime(2024, 4, 10, tzinfo=UTC),
        ),
    ]
    cached = CachingTreasurySharesRepository(_CountingTreasury(records))
    cached.prime(["005930"], as_of=_AS_OF)
    rec = cached.fetch_latest_active("005930", as_of=_AS_OF)
    assert rec is not None
    assert rec.id == succ_id
    assert rec.shares_treasury == 150


# =============================================================================
# C1 회귀 가드 (oracle 구현후 리뷰 L3) — bulk+serve 가 단건과 등가
# =============================================================================

def test_financial_chain_corruption_raises_identically_to_single() -> None:
    """C1 load-bearing 증명 — cycle chain 이 candidate 에 있으면 단건과 **동일하게**
    PITDataCorruptionError. bulk 가 date 사전필터로 chain 을 쪼개 corruption 검사를
    회피하지 **않음**(전체 vintage 적재)을 직접 검증.
    """
    a_id, b_id = uuid4(), uuid4()
    # 같은 (code, fiscal_period, account) 의 cycle: A→B→A (chain integrity 위반).
    records = [
        _fin(
            code="005930", fiscal_period="2024Q1", account="net_income",
            value="100", effective_date=date(2024, 5, 15),
            record_id=a_id, superseded_by=b_id,
        ),
        _fin(
            code="005930", fiscal_period="2024Q1", account="net_income",
            value="130", effective_date=date(2024, 5, 15),
            record_id=b_id, superseded_by=a_id,
        ),
    ]
    single = FakeFinancialRepository(records)
    cached = CachingFinancialRepository(_CountingFinancial(records))
    cached.prime(["005930"], as_of=_AS_OF)

    # 단건도 caching 도 동일하게 raise (등가성 — C1 의 핵심 주장).
    with pytest.raises(PITDataCorruptionError):
        single.fetch_financials("005930", as_of=_AS_OF, account="net_income")
    with pytest.raises(PITDataCorruptionError):
        cached.fetch_financials("005930", as_of=_AS_OF, account="net_income")


def test_financial_input_order_independent_serve() -> None:
    """raw 적재 순서가 셔플돼도 결정적 sort 로 단건과 동일 결과(회귀 가드)."""
    recs = [
        _fin(
            code="005930", fiscal_period="2023Q4", account="net_income",
            value="80", effective_date=date(2024, 3, 30),
        ),
        _fin(
            code="005930", fiscal_period="2024Q1", account="net_income",
            value="100", effective_date=date(2024, 5, 15),
        ),
    ]
    single = FakeFinancialRepository(recs)
    # 역순으로 적재한 inner 로 caching prime — sort 가 결정적이면 결과 동일.
    cached = CachingFinancialRepository(_CountingFinancial(list(reversed(recs))))
    cached.prime(["005930"], as_of=_AS_OF)

    via_cache = list(cached.fetch_financials(
        "005930", as_of=_AS_OF, account="net_income",
    ))
    via_single = list(single.fetch_financials(
        "005930", as_of=_AS_OF, account="net_income",
    ))
    assert [r.id for r in via_cache] == [r.id for r in via_single]
    assert [r.value for r in via_cache] == [r.value for r in via_single]


def test_financial_cross_account_supersede_chain_matches_single() -> None:
    """successor 가 다른 account 인 cross-account chain(SQL 주석 :488-491 케이스) —
    account 필터가 chain 해소 **이전**이라 successor 가 빠져 보수적 active. 단건과
    caching 이 동일 동작(account-before-resolution 일관성 회귀 가드).
    """
    orig_id, succ_id = uuid4(), uuid4()
    records = [
        _fin(
            code="005930", fiscal_period="2024Q1", account="net_income",
            value="100", effective_date=date(2024, 5, 15),
            record_id=orig_id, superseded_by=succ_id,
        ),
        # successor 가 다른 account — 소급 account remapping 가정.
        _fin(
            code="005930", fiscal_period="2024Q1", account="operating_income",
            value="130", effective_date=date(2024, 5, 20),
            record_id=succ_id,
        ),
    ]
    single = FakeFinancialRepository(records)
    cached = CachingFinancialRepository(_CountingFinancial(records))
    cached.prime(["005930"], as_of=_AS_OF)

    via_cache = list(cached.fetch_financials(
        "005930", as_of=_AS_OF, account="net_income",
    ))
    via_single = list(single.fetch_financials(
        "005930", as_of=_AS_OF, account="net_income",
    ))
    assert [r.id for r in via_cache] == [r.id for r in via_single]


def test_caching_update_superseded_by_delegates_to_inner() -> None:
    """update_superseded_by 가 inner 로 위임되는지(M1 — write 경로 Protocol 완전성)."""
    calls: list[tuple[UUID, UUID]] = []

    class _SpyInner(_CountingFinancial):
        def update_superseded_by(self, record_id, successor_id):
            calls.append((record_id, successor_id))

    cached = CachingFinancialRepository(_SpyInner([]))
    rid, sid = uuid4(), uuid4()
    cached.update_superseded_by(rid, sid)
    assert calls == [(rid, sid)]
