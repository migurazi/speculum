"""CachingMacroIndicatorRepository 단위 테스트 — request-scoped memoize 정확성.

테스트 매트릭스:
    1. 같은 (indicator_id, as_of) 반복 → inner 1회만 호출 (N×M → M 핵심).
    2. negative caching — None 결과도 캐시 (데이터 없는 indicator 반복 fetch 제거).
    3. 다른 indicator_id → 별도 inner 호출.
    4. 다른 as_of → 별도 inner 호출 (캐시 키에 as_of 포함).
    5. 반환값 정확성 — inner 결과를 그대로 반환 (값 변형 없음).
    6. 캐시 hit 결과가 첫 조회와 동일 객체 (memoize identity).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from app.repositories.caching_repositories import CachingMacroIndicatorRepository
from app.repositories.pit_protocols import MacroIndicatorRecord


def _record(indicator_id: str, value: str) -> MacroIndicatorRecord:
    """테스트용 MacroIndicatorRecord 생성."""
    return MacroIndicatorRecord(
        id=uuid4(),
        indicator_id=indicator_id,
        reference_date=date(2024, 4, 1),
        value=Decimal(value),
        unit="연%",
        vintage_date=date(2024, 5, 1),
        citation_id=uuid4(),
        created_at=datetime(2024, 5, 1, tzinfo=UTC),
    )


class _CountingMacro:
    """fetch_latest 호출을 기록하는 inner stub (MacroIndicatorRepository 호환).

    records: indicator_id → 반환 record (없으면 None 반환 — negative 케이스).
    calls: (indicator_id, as_of) 호출 이력.
    """

    def __init__(self, records: dict[str, MacroIndicatorRecord] | None = None) -> None:
        self._records = records or {}
        self.calls: list[tuple[str, date]] = []

    def fetch_latest(
        self, indicator_id: str, *, as_of: date,
    ) -> MacroIndicatorRecord | None:
        self.calls.append((indicator_id, as_of))
        return self._records.get(indicator_id)


# =============================================================================
# 1. 같은 키 반복 → inner 1회
# =============================================================================

def test_same_key_calls_inner_once() -> None:
    """같은 (indicator_id, as_of) 100회 조회 → inner fetch_latest 1회."""
    rec = _record("722Y001/0101000", "3.5")
    inner = _CountingMacro({"722Y001/0101000": rec})
    cache = CachingMacroIndicatorRepository(inner)
    as_of = date(2024, 5, 7)

    results = [
        cache.fetch_latest("722Y001/0101000", as_of=as_of) for _ in range(100)
    ]

    # inner 는 단 1회 — N×M → M 의 핵심.
    assert len(inner.calls) == 1
    # 모든 결과가 동일 값.
    assert all(r is not None and r.value == Decimal("3.5") for r in results)


# =============================================================================
# 2. negative caching — None 도 캐시
# =============================================================================

def test_none_result_is_cached() -> None:
    """데이터 없는 indicator (None 반환) 도 캐시 → 반복 fetch 제거."""
    inner = _CountingMacro({})  # 빈 records — 항상 None.
    cache = CachingMacroIndicatorRepository(inner)
    as_of = date(2024, 5, 7)

    r1 = cache.fetch_latest("999Y999/0", as_of=as_of)
    r2 = cache.fetch_latest("999Y999/0", as_of=as_of)

    assert r1 is None
    assert r2 is None
    # None 도 sentinel 로 캐시 — inner 1회만 (negative caching).
    assert len(inner.calls) == 1


# =============================================================================
# 3. 다른 indicator_id → 별도 호출
# =============================================================================

def test_distinct_indicators_call_inner_separately() -> None:
    """다른 indicator_id 는 별도 캐시 엔트리 → 각 1회 inner 호출."""
    inner = _CountingMacro({
        "722Y001/0101000": _record("722Y001/0101000", "3.5"),
        "901Y009/0": _record("901Y009/0", "114.2"),
    })
    cache = CachingMacroIndicatorRepository(inner)
    as_of = date(2024, 5, 7)

    cache.fetch_latest("722Y001/0101000", as_of=as_of)
    cache.fetch_latest("901Y009/0", as_of=as_of)
    cache.fetch_latest("722Y001/0101000", as_of=as_of)  # 재조회 — 캐시 hit.
    cache.fetch_latest("901Y009/0", as_of=as_of)         # 재조회 — 캐시 hit.

    # indicator 2종 각 1회 = 2회 (재조회는 hit).
    assert len(inner.calls) == 2
    called_ids = {c[0] for c in inner.calls}
    assert called_ids == {"722Y001/0101000", "901Y009/0"}


# =============================================================================
# 4. 다른 as_of → 별도 호출 (캐시 키에 as_of 포함)
# =============================================================================

def test_distinct_as_of_call_inner_separately() -> None:
    """같은 indicator 라도 as_of 다르면 별도 조회 (PIT 결과가 다를 수 있음)."""
    inner = _CountingMacro({"722Y001/0101000": _record("722Y001/0101000", "3.5")})
    cache = CachingMacroIndicatorRepository(inner)

    cache.fetch_latest("722Y001/0101000", as_of=date(2024, 5, 7))
    cache.fetch_latest("722Y001/0101000", as_of=date(2024, 6, 7))

    # as_of 2종 → inner 2회.
    assert len(inner.calls) == 2


# =============================================================================
# 5. 반환값 정확성 — inner 결과 그대로
# =============================================================================

def test_returns_inner_record_unchanged() -> None:
    """캐싱이 값을 변형하지 않음 — inner record 를 그대로 반환."""
    rec = _record("722Y001/0101000", "2.75")
    inner = _CountingMacro({"722Y001/0101000": rec})
    cache = CachingMacroIndicatorRepository(inner)

    result = cache.fetch_latest("722Y001/0101000", as_of=date(2024, 5, 7))

    assert result is rec  # 동일 객체 — 변형/복사 없음.


# =============================================================================
# 6. 캐시 hit identity — 첫 조회와 동일 객체
# =============================================================================

def test_cache_hit_returns_same_object() -> None:
    """두 번째 조회가 첫 조회와 동일 객체 (memoize identity)."""
    rec = _record("722Y001/0101000", "3.5")
    inner = _CountingMacro({"722Y001/0101000": rec})
    cache = CachingMacroIndicatorRepository(inner)
    as_of = date(2024, 5, 7)

    first = cache.fetch_latest("722Y001/0101000", as_of=as_of)
    second = cache.fetch_latest("722Y001/0101000", as_of=as_of)

    assert first is second
