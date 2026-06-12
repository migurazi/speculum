"""CachingPriceRepository / CachingMarketCapRepository 단위 테스트.

per-code(price·market_cap) N+1 완화의 핵심 — universe 루프 **이전** 1회 bulk
prime 으로 종목별 개별 쿼리 N 회를 청크 쿼리 몇 회로 축약. macro 캐싱과 달리
종목마다 값이 달라 lazy memoize 로는 dedup 이 안 되므로 prime(prefetch) 방식이다.

테스트 매트릭스 (price):
    1. prime 은 inner.fetch_prices_bulk 를 1회만 호출 (per-code fetch 0).
    2. prime 후 fetch_prices 는 캐시 서빙 (inner 추가 호출 0 — N+1 제거).
    3. 캐시 반환이 inner 단건 fetch 와 byte-동일 (window 내 start 필터).
    4. prime window 밖(더 먼 과거 start) → inner 위임 (silent truncate 금지).
    5. 다른 as_of / batch_cutoff → inner 위임 (캐시 키 격리, 재현성).
    6. fetch_codes_with_prices — 존재 판정 위임.
    7. inner 가 bulk 미지원 → per-code 위임으로 graceful degrade (정확성 동일).

테스트 매트릭스 (market_cap):
    8. prime 은 fetch_latest_bulk 1회 / 이후 fetch_latest 캐시 서빙.
    9. 데이터 없는 code → None (단건 fetch_latest 와 동일).
    10. 다른 as_of / batch_cutoff → inner 위임.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.repositories.batch_run_repository import EXCLUDE_ALL_CUTOFF
from app.repositories.caching_repositories import (
    PRICE_PREFETCH_WINDOW_DAYS,
    CachingMarketCapRepository,
    CachingPriceRepository,
)
from app.repositories.fakes import FakeMarketCapRepository, FakePriceRepository
from app.repositories.pit_protocols import MarketCapRecord, PriceRecord

_LINEAGE: UUID = UUID("00000000-0000-0000-0000-0000000000aa")
_CITATION: UUID = UUID("00000000-0000-0000-0000-00000000ffff")


def _price(code: str, eff: date, close: float = 70000.0) -> PriceRecord:
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
        citation_id=_CITATION,
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


class _CountingPrice:
    """FakePriceRepository 위임 + 호출 카운트 (bulk vs per-code 검증용 stub)."""

    def __init__(self, records: list[PriceRecord]) -> None:
        self._fake = FakePriceRepository(records)
        self.bulk_calls = 0
        self.single_calls = 0
        self.existence_calls = 0

    def fetch_prices(self, code, *, as_of, start, batch_cutoff=None):
        self.single_calls += 1
        return self._fake.fetch_prices(
            code, as_of=as_of, start=start, batch_cutoff=batch_cutoff,
        )

    def fetch_prices_bulk(self, codes, *, as_of, start, batch_cutoff=None):
        self.bulk_calls += 1
        return self._fake.fetch_prices_bulk(
            codes, as_of=as_of, start=start, batch_cutoff=batch_cutoff,
        )

    def fetch_codes_with_prices(self, codes, *, as_of, batch_cutoff=None):
        self.existence_calls += 1
        return self._fake.fetch_codes_with_prices(
            codes, as_of=as_of, batch_cutoff=batch_cutoff,
        )

    def save_prices(self, records):
        self._fake.save_prices(records)


class _NoBulkPrice:
    """fetch_prices 만 가진 최소 stub — bulk 미지원 graceful degrade 검증."""

    def __init__(self, records: list[PriceRecord]) -> None:
        self._fake = FakePriceRepository(records)
        self.single_calls = 0

    def fetch_prices(self, code, *, as_of, start, batch_cutoff=None):
        self.single_calls += 1
        return self._fake.fetch_prices(
            code, as_of=as_of, start=start, batch_cutoff=batch_cutoff,
        )


class _CountingMarketCap:
    """FakeMarketCapRepository 위임 + 호출 카운트."""

    def __init__(self, records: list[MarketCapRecord]) -> None:
        self._fake = FakeMarketCapRepository(records)
        self.bulk_calls = 0
        self.single_calls = 0

    def fetch_latest(self, code, *, as_of, batch_cutoff=None):
        self.single_calls += 1
        return self._fake.fetch_latest(code, as_of=as_of, batch_cutoff=batch_cutoff)

    def fetch_latest_bulk(self, codes, *, as_of, batch_cutoff=None):
        self.bulk_calls += 1
        return self._fake.fetch_latest_bulk(
            codes, as_of=as_of, batch_cutoff=batch_cutoff,
        )

    def save_market_caps(self, records):
        self._fake.save_market_caps(records)


# =============================================================================
# 1~2. prime 은 bulk 1회 / 이후 fetch 는 캐시 서빙 (N+1 제거 핵심)
# =============================================================================

def test_price_prime_uses_bulk_once_and_serves_from_cache() -> None:
    as_of = date(2024, 5, 10)
    records = [
        _price("000001", date(2024, 5, 1)),
        _price("000002", date(2024, 5, 1)),
        _price("000003", date(2024, 5, 1)),
    ]
    inner = _CountingPrice(records)
    cache = CachingPriceRepository(inner)

    cache.prime(["000001", "000002", "000003"], as_of=as_of)
    # prime = bulk 1회, per-code fetch 0.
    assert inner.bulk_calls == 1
    assert inner.single_calls == 0

    # 루프 시뮬레이션 — 종목별 fetch 가 전부 캐시 서빙(inner 추가 호출 0).
    for code in ("000001", "000002", "000003"):
        result = cache.fetch_prices(
            code, as_of=as_of, start=as_of.replace(month=4),
        )
        assert len(result) == 1
    assert inner.single_calls == 0  # N+1 제거 — 캐시 서빙.


# =============================================================================
# 3. 캐시 반환이 inner 단건 fetch 와 byte-동일 (window 내 필터)
# =============================================================================

def test_price_cache_byte_identical_to_inner() -> None:
    as_of = date(2024, 5, 10)
    records = [
        _price("000001", date(2024, 1, 15), close=100),
        _price("000001", date(2024, 3, 1), close=110),
        _price("000001", date(2024, 5, 10), close=120),
    ]
    cache = CachingPriceRepository(FakePriceRepository(records))
    cache.prime(["000001"], as_of=as_of)
    reference = FakePriceRepository(records)

    # window 내 여러 start 에 대해 캐시 == inner 단건.
    for start in (date(2024, 1, 1), date(2024, 2, 1), date(2024, 5, 10)):
        cached = cache.fetch_prices("000001", as_of=as_of, start=start)
        ref = reference.fetch_prices("000001", as_of=as_of, start=start)
        assert [r.effective_date for r in cached] == [
            r.effective_date for r in ref
        ]
        assert [r.close_raw for r in cached] == [r.close_raw for r in ref]


# =============================================================================
# 4. prime window 밖(더 먼 과거 start) → inner 위임 (silent truncate 금지)
# =============================================================================

def test_price_out_of_window_start_delegates_to_inner() -> None:
    as_of = date(2024, 5, 10)
    # window(기본 400일) 밖 과거 record + window 내 record.
    old = _price("000001", date(2020, 1, 2), close=50)
    recent = _price("000001", date(2024, 5, 1), close=120)
    inner = _CountingPrice([old, recent])
    cache = CachingPriceRepository(inner)
    cache.prime(["000001"], as_of=as_of)
    inner.single_calls = 0  # prime(bulk) 이후 카운트 리셋.

    # start=date.min 은 prime 하한보다 먼 과거 → inner 위임(캐시 truncate 금지).
    result = cache.fetch_prices("000001", as_of=as_of, start=date.min)
    assert inner.single_calls == 1  # 위임됨.
    # 위임이라 window 밖 old record 까지 포함 (byte-동일성).
    assert {r.effective_date for r in result} == {old.effective_date, recent.effective_date}


def test_price_window_boundary_serves_from_cache() -> None:
    """prime 하한 이상(window 내) start 는 캐시 서빙(위임 아님)."""
    as_of = date(2024, 5, 10)
    inner = _CountingPrice([_price("000001", date(2024, 5, 1))])
    cache = CachingPriceRepository(inner)
    cache.prime(["000001"], as_of=as_of)
    inner.single_calls = 0

    prime_lower = as_of.fromordinal(as_of.toordinal() - PRICE_PREFETCH_WINDOW_DAYS)
    result = cache.fetch_prices("000001", as_of=as_of, start=prime_lower)
    assert inner.single_calls == 0  # 캐시 서빙.
    assert len(result) == 1


# =============================================================================
# 5. 다른 as_of / batch_cutoff → inner 위임 (캐시 키 격리, 재현성)
# =============================================================================

def test_price_different_as_of_delegates() -> None:
    inner = _CountingPrice([_price("000001", date(2024, 5, 1))])
    cache = CachingPriceRepository(inner)
    cache.prime(["000001"], as_of=date(2024, 5, 10))
    inner.single_calls = 0

    cache.fetch_prices("000001", as_of=date(2024, 6, 10), start=date(2024, 1, 1))
    assert inner.single_calls == 1  # 다른 as_of → 위임.


def test_price_different_batch_cutoff_delegates() -> None:
    """prime(None) 과 다른 batch_cutoff 요청은 위임 — 재현 모드/라이브 격리."""
    inner = _CountingPrice([_price("000001", date(2024, 5, 1))])
    cache = CachingPriceRepository(inner)
    cache.prime(["000001"], as_of=date(2024, 5, 10), batch_cutoff=None)
    inner.single_calls = 0

    cache.fetch_prices(
        "000001", as_of=date(2024, 5, 10), start=date(2024, 1, 1),
        batch_cutoff=EXCLUDE_ALL_CUTOFF,
    )
    assert inner.single_calls == 1  # 다른 cutoff → 위임.


# =============================================================================
# 6. fetch_codes_with_prices — 존재 판정 위임
# =============================================================================

def test_price_fetch_codes_with_prices() -> None:
    as_of = date(2024, 5, 10)
    records = [
        _price("000001", date(2024, 5, 1)),
        _price("000002", date(2010, 1, 4)),  # window 밖이나 존재.
    ]
    inner = _CountingPrice(records)
    cache = CachingPriceRepository(inner)

    found = cache.fetch_codes_with_prices(
        ["000001", "000002", "000003"], as_of=as_of,
    )
    assert found == {"000001", "000002"}  # 000003 가격 없음 → 제외.
    assert inner.existence_calls == 1  # bulk 존재 쿼리 위임.


# =============================================================================
# 7. inner bulk 미지원 → per-code 위임 graceful degrade
# =============================================================================

def test_price_graceful_degrade_without_bulk() -> None:
    as_of = date(2024, 5, 10)
    records = [_price("000001", date(2024, 5, 1)), _price("000002", date(2024, 5, 1))]
    inner = _NoBulkPrice(records)
    cache = CachingPriceRepository(inner)

    cache.prime(["000001", "000002"], as_of=as_of)
    # bulk 없음 → prime 이 per-code fetch 로 fallback (정확성 동일, perf 만 차이).
    assert inner.single_calls == 2

    # 그래도 이후 fetch 는 캐시 서빙(추가 호출 0).
    inner.single_calls = 0
    result = cache.fetch_prices("000001", as_of=as_of, start=date(2024, 1, 1))
    assert inner.single_calls == 0
    assert len(result) == 1


# =============================================================================
# 8~10. market_cap
# =============================================================================

def test_market_cap_prime_bulk_once_and_serves() -> None:
    as_of = date(2024, 5, 10)
    records = [_mc("000001", date(2024, 5, 1)), _mc("000002", date(2024, 5, 1))]
    inner = _CountingMarketCap(records)
    cache = CachingMarketCapRepository(inner)

    cache.prime(["000001", "000002"], as_of=as_of)
    assert inner.bulk_calls == 1
    assert inner.single_calls == 0

    for code in ("000001", "000002"):
        rec = cache.fetch_latest(code, as_of=as_of)
        assert rec is not None
    assert inner.single_calls == 0  # 캐시 서빙.


def test_market_cap_absent_code_returns_none() -> None:
    as_of = date(2024, 5, 10)
    inner = _CountingMarketCap([_mc("000001", date(2024, 5, 1))])
    cache = CachingMarketCapRepository(inner)
    cache.prime(["000001", "000099"], as_of=as_of)
    inner.single_calls = 0

    # 데이터 없는 code → None (단건 fetch_latest 와 동일), 위임 없음(키 일치).
    assert cache.fetch_latest("000099", as_of=as_of) is None
    assert inner.single_calls == 0


def test_market_cap_different_key_delegates() -> None:
    inner = _CountingMarketCap([_mc("000001", date(2024, 5, 1))])
    cache = CachingMarketCapRepository(inner)
    cache.prime(["000001"], as_of=date(2024, 5, 10))
    inner.single_calls = 0

    cache.fetch_latest("000001", as_of=date(2024, 6, 10))  # 다른 as_of.
    cache.fetch_latest(
        "000001", as_of=date(2024, 5, 10), batch_cutoff=EXCLUDE_ALL_CUTOFF,
    )  # 다른 cutoff.
    assert inner.single_calls == 2  # 둘 다 위임.


def test_market_cap_byte_identical_to_inner() -> None:
    """캐시 반환 latest 가 inner 단건 fetch_latest 와 동일(tiebreak 포함)."""
    as_of = date(2024, 5, 10)
    records = [
        _mc("000001", date(2024, 3, 1), cap=1e14),
        _mc("000001", date(2024, 5, 1), cap=2e14),  # 최신 — 이게 latest.
    ]
    cache = CachingMarketCapRepository(FakeMarketCapRepository(records))
    cache.prime(["000001"], as_of=as_of)
    reference = FakeMarketCapRepository(records)

    cached = cache.fetch_latest("000001", as_of=as_of)
    ref = reference.fetch_latest("000001", as_of=as_of)
    assert cached is not None and ref is not None
    assert cached.effective_date == ref.effective_date
    assert cached.market_cap == ref.market_cap
