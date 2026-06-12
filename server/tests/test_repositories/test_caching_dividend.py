"""CachingDividendRepository 단위 테스트.

total_return field(배당 재투자 §2.4)의 per-code N+1(universe 종목마다 fetch_dividends)
완화 — 루프 전 1회 bulk prime. CachingCorporateActionRepository 와 동형이되 dual-PIT
(announced 가용성 축 + effective 발생 축) 해소라는 점만 다르다: raw vintage(전체
corporate_action) 적재 후 serve-time 에 공유 helper(resolve_dividends)로 announced
chain 해소 → cash_dividend 필터 → effective<=as_of 필터.
SQL bulk==단건 byte-동일은 test_db/test_sql_bulk_financial_treasury.py 가 검증.

테스트 매트릭스:
    1. prime 은 inner.fetch_all_dividends_bulk 1회만 호출(per-code single 0).
    2. prime 후 fetch_dividends 는 캐시 서빙(inner single 추가 0 — N+1 제거).
    3. 캐시 반환이 inner 단건 fetch_dividends 와 byte-동일.
    4. dual-PIT — effective_date > as_of 인 미발생 배당 제외(effective 축).
    5. dual-PIT — announced chain superseded cash_dividend 제외(announced 축).
    6. cross-action_type chain truncation 방지(전체 vintage 적재 — cash_dividend
       orig 이 split successor 로 superseded → orig 부활 오판 없음).
    7. 다른 as_of → inner 위임(캐시 키 격리). 미prime → inner 위임.
    8. inner bulk 미지원 → graceful degrade.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.repositories.caching_repositories import CachingDividendRepository
from app.repositories.fakes import FakeDividendRepository
from app.repositories.pit_protocols import CorporateActionRecord

_LINEAGE = UUID("00000000-0000-0000-0000-0000000000aa")
_CITATION = UUID("00000000-0000-0000-0000-00000000ffff")
_AS_OF = date(2024, 6, 1)
_CODES = ["005930", "000660", "035420"]


def _ca(
    *,
    code: str,
    action_type: str,
    announced: date,
    effective: date,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> CorporateActionRecord:
    return CorporateActionRecord(
        id=record_id or uuid4(),
        code=code,
        code_lineage_id=_LINEAGE,
        action_type=action_type,
        announced_date=announced,
        effective_date=effective,
        payment_date=None,
        ratio=Decimal("2"),
        cash_amount=None,
        details={"per_share": "500"},
        citation_id=_CITATION,
        superseded_by=superseded_by,
        created_at=created_at or datetime(
            announced.year, announced.month, announced.day, 9, 0, tzinfo=UTC,
        ),
    )


class _CountingDividend:
    """FakeDividendRepository 위임 + bulk vs single 호출 카운트."""

    def __init__(self, records: list[CorporateActionRecord]) -> None:
        self._fake = FakeDividendRepository(records)
        self.bulk_calls = 0
        self.single_calls = 0

    def fetch_dividends(self, code, *, as_of):
        self.single_calls += 1
        return self._fake.fetch_dividends(code, as_of=as_of)

    def fetch_all_dividends_bulk(self, codes):
        self.bulk_calls += 1
        return self._fake.fetch_all_dividends_bulk(codes)


class _NoBulkDividend:
    """fetch_dividends 만 가진 최소 stub — bulk 미지원 graceful degrade 검증."""

    def __init__(self, records: list[CorporateActionRecord]) -> None:
        self._fake = FakeDividendRepository(records)
        self.single_calls = 0

    def fetch_dividends(self, code, *, as_of):
        self.single_calls += 1
        return self._fake.fetch_dividends(code, as_of=as_of)


def _universe_dividends() -> list[CorporateActionRecord]:
    """3종목 × (cash_dividend announced·effective 둘 다 <= as_of) + 무관 split."""
    recs: list[CorporateActionRecord] = []
    for code in _CODES:
        # 평가 대상 cash_dividend(두 축 모두 통과).
        recs.append(_ca(
            code=code, action_type="cash_dividend",
            announced=date(2024, 4, 1), effective=date(2024, 4, 10),
        ))
        # split — fetch_dividends 결과에서 제외되어야(cash_dividend 필터).
        recs.append(_ca(
            code=code, action_type="split",
            announced=date(2024, 3, 1), effective=date(2024, 3, 15),
        ))
    return recs


# =============================================================================
# 1~2. prime 1회 bulk + 캐시 서빙
# =============================================================================

def test_dividend_prime_once_then_serves_from_cache() -> None:
    inner = _CountingDividend(_universe_dividends())
    cached = CachingDividendRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)
    assert inner.bulk_calls == 1
    assert inner.single_calls == 0
    for code in _CODES:
        divs = cached.fetch_dividends(code, as_of=_AS_OF)
        assert len(divs) == 1  # cash_dividend 만(split 제외).
        assert divs[0].action_type == "cash_dividend"
    assert inner.single_calls == 0  # N+1 제거.


# =============================================================================
# 3. 캐시 == 단건 byte-동일
# =============================================================================

def test_dividend_cache_byte_identical_to_single() -> None:
    records = _universe_dividends()
    single_ref = FakeDividendRepository(records)
    cached = CachingDividendRepository(_CountingDividend(records))
    cached.prime(_CODES, as_of=_AS_OF)
    for code in _CODES:
        via_cache = list(cached.fetch_dividends(code, as_of=_AS_OF))
        via_single = list(single_ref.fetch_dividends(code, as_of=_AS_OF))
        assert [r.id for r in via_cache] == [r.id for r in via_single]
        assert [r.action_type for r in via_cache] == [
            r.action_type for r in via_single
        ]
        assert [r.effective_date for r in via_cache] == [
            r.effective_date for r in via_single
        ]


# =============================================================================
# 4. dual-PIT effective 축 — effective > as_of 미발생 배당 제외
# =============================================================================

def test_dividend_effective_axis_excludes_future_exdate() -> None:
    """announced <= as_of 이지만 effective > as_of 인 배당(배당락 미발생)은 제외."""
    records = [
        _ca(
            code="005930", action_type="cash_dividend",
            announced=date(2024, 5, 1), effective=date(2024, 4, 10),
        ),  # 두 축 통과.
        _ca(
            code="005930", action_type="cash_dividend",
            announced=date(2024, 5, 1), effective=date(2024, 7, 1),
        ),  # effective 미래 → 제외.
    ]
    single_ref = FakeDividendRepository(records)
    cached = CachingDividendRepository(_CountingDividend(records))
    cached.prime(["005930"], as_of=_AS_OF)
    via_cache = list(cached.fetch_dividends("005930", as_of=_AS_OF))
    via_single = list(single_ref.fetch_dividends("005930", as_of=_AS_OF))
    assert [r.id for r in via_cache] == [r.id for r in via_single]
    assert len(via_cache) == 1
    assert via_cache[0].effective_date == date(2024, 4, 10)


# =============================================================================
# 5. dual-PIT announced 축 — superseded cash_dividend 제외(chain 해소)
# =============================================================================

def test_dividend_announced_axis_resolves_supersede_chain() -> None:
    orig_id, succ_id = uuid4(), uuid4()
    records = [
        _ca(
            code="005930", action_type="cash_dividend",
            announced=date(2024, 4, 1), effective=date(2024, 4, 10),
            record_id=orig_id, superseded_by=succ_id,
            created_at=datetime(2024, 4, 1, tzinfo=UTC),
        ),
        _ca(
            code="005930", action_type="cash_dividend",
            announced=date(2024, 4, 15), effective=date(2024, 4, 20),
            record_id=succ_id, created_at=datetime(2024, 4, 15, tzinfo=UTC),
        ),
    ]
    single_ref = FakeDividendRepository(records)
    cached = CachingDividendRepository(_CountingDividend(records))
    cached.prime(["005930"], as_of=_AS_OF)
    via_cache = list(cached.fetch_dividends("005930", as_of=_AS_OF))
    via_single = list(single_ref.fetch_dividends("005930", as_of=_AS_OF))
    assert [r.id for r in via_cache] == [r.id for r in via_single]
    # 옛 row 는 superseded → 후속만 active.
    assert len(via_cache) == 1
    assert via_cache[0].id == succ_id


# =============================================================================
# 6. cross-action_type chain truncation 방지(전체 vintage 적재)
# =============================================================================

def test_dividend_cross_action_type_chain_not_truncated() -> None:
    """cash_dividend(orig) 가 split(succ) 로 superseded — orig 부활 오판 없음.

    raw 적재가 전체 corporate_action 이므로 successor(split)가 record_by_id 에 존재
    → orig 이 보수적 active 복원되지 않고 정상 superseded 로 제외.
    """
    orig_id, succ_id = uuid4(), uuid4()
    records = [
        _ca(
            code="005930", action_type="cash_dividend",
            announced=date(2024, 4, 1), effective=date(2024, 4, 10),
            record_id=orig_id, superseded_by=succ_id,
            created_at=datetime(2024, 4, 1, tzinfo=UTC),
        ),
        _ca(
            code="005930", action_type="split",
            announced=date(2024, 4, 15), effective=date(2024, 4, 20),
            record_id=succ_id, created_at=datetime(2024, 4, 15, tzinfo=UTC),
        ),
    ]
    single_ref = FakeDividendRepository(records)
    cached = CachingDividendRepository(_CountingDividend(records))
    cached.prime(["005930"], as_of=_AS_OF)
    via_cache = list(cached.fetch_dividends("005930", as_of=_AS_OF))
    via_single = list(single_ref.fetch_dividends("005930", as_of=_AS_OF))
    assert [r.id for r in via_cache] == [r.id for r in via_single]
    # orig(cash_dividend) 는 superseded, succ 는 split → cash_dividend 0건.
    assert via_cache == []


# =============================================================================
# 7~8. 캐시 키 격리 / 미prime / graceful degrade
# =============================================================================

def test_dividend_different_as_of_delegates_to_inner() -> None:
    inner = _CountingDividend(_universe_dividends())
    cached = CachingDividendRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)
    inner.single_calls = 0
    cached.fetch_dividends("005930", as_of=date(2023, 1, 1))
    assert inner.single_calls == 1


def test_dividend_unprimed_delegates_to_inner() -> None:
    inner = _CountingDividend(_universe_dividends())
    cached = CachingDividendRepository(inner)
    cached.fetch_dividends("005930", as_of=_AS_OF)
    assert inner.single_calls == 1
    assert inner.bulk_calls == 0


def test_dividend_graceful_degrade_without_bulk() -> None:
    inner = _NoBulkDividend(_universe_dividends())
    cached = CachingDividendRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)  # bulk 없음 → 미prime.
    divs = cached.fetch_dividends("005930", as_of=_AS_OF)
    assert len(divs) == 1
    assert divs[0].action_type == "cash_dividend"
    assert inner.single_calls == 1
