"""CachingCorporateActionRepository 단위 테스트.

backtest 보유수익률(_portfolio_return → _adjusted_closes_at)의 per-code N+1
(holdings 각 종목마다 fetch_actions) 완화 — 루프 전 1회 bulk prime(ⓒ). financial/
treasury 와 동형: raw vintage 적재 후 serve-time 에 공유 helper(resolve_active_
actions)로 announced_date chain 해소(action_types serve-time 가변이라 raw 적재).
SQL bulk==단건 byte-동일은 test_db/test_sql_bulk_financial_treasury.py 가 검증.

테스트 매트릭스:
    1. prime 은 inner.fetch_all_actions_bulk 1회만 호출(per-code single 0).
    2. prime 후 fetch_actions 는 캐시 서빙(inner single 추가 0 — N+1 제거).
    3. 캐시 반환이 inner 단건 fetch_actions 와 byte-동일(announced_date 해소·sort).
    4. action_types serve-time 가변 — raw 적재라 종류 필터를 serve 시 적용.
    5. 정정 chain serve-time 해소(announced_date superseded → 후속 반환).
    6. 다른 as_of → inner 위임(캐시 키 격리). 미prime → inner 위임.
    7. inner bulk 미지원 → graceful degrade.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.repositories.caching_repositories import CachingCorporateActionRepository
from app.repositories.fakes import FakeCorporateActionRepository
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
        details={},
        citation_id=_CITATION,
        superseded_by=superseded_by,
        created_at=created_at or datetime(
            announced.year, announced.month, announced.day, 9, 0, tzinfo=UTC,
        ),
    )


class _CountingCA:
    """FakeCorporateActionRepository 위임 + bulk vs single 호출 카운트."""

    def __init__(self, records: list[CorporateActionRecord]) -> None:
        self._fake = FakeCorporateActionRepository(records)
        self.bulk_calls = 0
        self.single_calls = 0

    def fetch_actions(self, code, *, as_of, action_types=None):
        self.single_calls += 1
        return self._fake.fetch_actions(
            code, as_of=as_of, action_types=action_types,
        )

    def fetch_all_actions_bulk(self, codes):
        self.bulk_calls += 1
        return self._fake.fetch_all_actions_bulk(codes)


class _NoBulkCA:
    """fetch_actions 만 가진 최소 stub — bulk 미지원 graceful degrade 검증."""

    def __init__(self, records: list[CorporateActionRecord]) -> None:
        self._fake = FakeCorporateActionRepository(records)
        self.single_calls = 0

    def fetch_actions(self, code, *, as_of, action_types=None):
        self.single_calls += 1
        return self._fake.fetch_actions(
            code, as_of=as_of, action_types=action_types,
        )


def _universe_actions() -> list[CorporateActionRecord]:
    """3종목 × 여러 action_type/시점."""
    recs: list[CorporateActionRecord] = []
    for code in _CODES:
        recs.append(_ca(
            code=code, action_type="split",
            announced=date(2024, 3, 1), effective=date(2024, 3, 15),
        ))
        recs.append(_ca(
            code=code, action_type="cash_dividend",
            announced=date(2024, 4, 1), effective=date(2024, 4, 10),
        ))
    return recs


# =============================================================================
# 1~2. prime 1회 bulk + 캐시 서빙
# =============================================================================

def test_ca_prime_once_then_serves_from_cache() -> None:
    inner = _CountingCA(_universe_actions())
    cached = CachingCorporateActionRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)
    assert inner.bulk_calls == 1
    assert inner.single_calls == 0
    for code in _CODES:
        actions = cached.fetch_actions(code, as_of=_AS_OF)
        assert len(actions) == 2  # split + cash_dividend
    assert inner.single_calls == 0  # N+1 제거.


# =============================================================================
# 3. 캐시 == 단건 byte-동일
# =============================================================================

def test_ca_cache_byte_identical_to_single() -> None:
    records = _universe_actions()
    single_ref = FakeCorporateActionRepository(records)
    cached = CachingCorporateActionRepository(_CountingCA(records))
    cached.prime(_CODES, as_of=_AS_OF)
    for code in _CODES:
        via_cache = list(cached.fetch_actions(code, as_of=_AS_OF))
        via_single = list(single_ref.fetch_actions(code, as_of=_AS_OF))
        assert [r.id for r in via_cache] == [r.id for r in via_single]
        assert [r.action_type for r in via_cache] == [
            r.action_type for r in via_single
        ]


# =============================================================================
# 4. action_types serve-time 가변
# =============================================================================

def test_ca_serve_time_action_types_filter() -> None:
    inner = _CountingCA(_universe_actions())
    cached = CachingCorporateActionRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)
    only_split = cached.fetch_actions(
        "005930", as_of=_AS_OF, action_types=frozenset({"split"}),
    )
    assert len(only_split) == 1
    assert only_split[0].action_type == "split"
    # 같은 prime 으로 다른 종류 필터도 정확(raw 적재 이점).
    only_div = cached.fetch_actions(
        "005930", as_of=_AS_OF, action_types=frozenset({"cash_dividend"}),
    )
    assert len(only_div) == 1
    assert only_div[0].action_type == "cash_dividend"
    assert inner.bulk_calls == 1
    assert inner.single_calls == 0


# =============================================================================
# 5. 정정 chain serve-time 해소
# =============================================================================

def test_ca_supersede_chain_resolved_at_serve_time() -> None:
    orig_id, succ_id = uuid4(), uuid4()
    records = [
        _ca(
            code="005930", action_type="split",
            announced=date(2024, 3, 1), effective=date(2024, 3, 15),
            record_id=orig_id, superseded_by=succ_id,
            created_at=datetime(2024, 3, 1, tzinfo=UTC),
        ),
        _ca(
            code="005930", action_type="split",
            announced=date(2024, 3, 5), effective=date(2024, 3, 20),
            record_id=succ_id,
            created_at=datetime(2024, 3, 5, tzinfo=UTC),
        ),
    ]
    cached = CachingCorporateActionRepository(_CountingCA(records))
    cached.prime(["005930"], as_of=_AS_OF)
    actions = cached.fetch_actions("005930", as_of=_AS_OF)
    # 옛 row 는 superseded → 후속만 active.
    assert len(actions) == 1
    assert actions[0].id == succ_id


# =============================================================================
# 6~7. 캐시 키 격리 / 미prime / graceful degrade
# =============================================================================

def test_ca_different_as_of_delegates_to_inner() -> None:
    inner = _CountingCA(_universe_actions())
    cached = CachingCorporateActionRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)
    inner.single_calls = 0
    cached.fetch_actions("005930", as_of=date(2023, 1, 1))
    assert inner.single_calls == 1


def test_ca_unprimed_delegates_to_inner() -> None:
    inner = _CountingCA(_universe_actions())
    cached = CachingCorporateActionRepository(inner)
    cached.fetch_actions("005930", as_of=_AS_OF)
    assert inner.single_calls == 1
    assert inner.bulk_calls == 0


def test_ca_graceful_degrade_without_bulk() -> None:
    inner = _NoBulkCA(_universe_actions())
    cached = CachingCorporateActionRepository(inner)
    cached.prime(_CODES, as_of=_AS_OF)  # bulk 없음 → 미prime.
    actions = cached.fetch_actions("005930", as_of=_AS_OF)
    assert len(actions) == 2
    assert inner.single_calls == 1
