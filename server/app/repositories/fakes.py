"""In-memory Fake Repository — 테스트 fixture + T13 SQLAlchemy 구현체의 의미 동등성 기준.

각 Fake 는 record list 를 생성자에 받아 PIT-aware fetch 로직을 구현. PITEnforcer
와 같은 알고리즘을 직접 사용하여 contract test 의 reference implementation 으로
기능. T13 합류 시 SQLAlchemy 구현체가 동일 contract test suite 를 통과해야 함
(oracle 자문 R2 / 2 차 리뷰 C5).

본 모듈은 `app.repositories.fakes` 로 import 되지만 운영 코드에서 우발적으로
import 하지 않도록 `__all__` 명시 (oracle 2 차 리뷰 M2 / L1).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Sequence
from uuid import UUID

from app.repositories.pit_protocols import (
    CorporateActionRecord,
    CorporateActionRepository,
    FinancialRecord,
    FinancialRepository,
    PriceRecord,
    PriceRepository,
    StockSnapshotRecord,
    StockSnapshotRepository,
)
from app.services.pit_enforcer import PITEnforcer

__all__ = [
    "FakeCorporateActionRepository",
    "FakeFinancialRepository",
    "FakePriceRepository",
    "FakeStockSnapshotRepository",
]


class FakePriceRepository(PriceRepository):
    """In-memory price store. KRX 가격은 supersede 없음 → 단순 필터."""

    def __init__(self, records: Sequence[PriceRecord]) -> None:
        # code 별 index — fetch_prices 의 효율.
        self._by_code: dict[str, list[PriceRecord]] = defaultdict(list)
        for r in records:
            self._by_code[r.code].append(r)
        # 일자 오름차순.
        for code in self._by_code:
            self._by_code[code].sort(key=lambda r: r.effective_date)

    def fetch_prices(
        self,
        code: str,
        *,
        as_of: date,
        start: date,
    ) -> Sequence[PriceRecord]:
        if start > as_of:
            return []
        return [
            r for r in self._by_code.get(code, [])
            if start <= r.effective_date <= as_of
        ]


class FakeFinancialRepository(FinancialRepository):
    """In-memory financial store + supersede chain 해소.

    PITEnforcer.latest_active_by_key 로 (code, account) 별 latest active 를
    `fiscal_period` 별로 그룹화하여 반환. 본 구현이 T13 SQLAlchemy 의 reference.
    """

    def __init__(self, records: Sequence[FinancialRecord]) -> None:
        self._by_code: dict[str, list[FinancialRecord]] = defaultdict(list)
        for r in records:
            self._by_code[r.code].append(r)
        self._enforcer = PITEnforcer()

    def fetch_financials(
        self,
        code: str,
        *,
        as_of: date,
        account: str,
        max_periods: int = 8,
    ) -> Sequence[FinancialRecord]:
        all_records = [
            r for r in self._by_code.get(code, [])
            if r.account == account
        ]
        # fiscal_period 별 latest active (effective_date 기준 PIT).
        latest_by_period = self._enforcer.latest_active_by_key(
            all_records, as_of, key=lambda r: r.fiscal_period
        )
        # 결정성 보장 sort key — id 가 final tiebreaker (oracle 2 차 리뷰 C6).
        sorted_periods = sorted(
            latest_by_period.values(),
            key=lambda r: (r.effective_date, r.fiscal_period, str(r.id)),
        )
        return sorted_periods[-max_periods:]


class FakeCorporateActionRepository(CorporateActionRepository):
    """In-memory corporate action store + 정정공시 chain 해소.

    이중 PIT (announced_date / effective_date) — `fetch_actions` 는 announced 기준.
    PITEnforcer 의 `date_of` strategy 로 announced_date PIT 의미론을 위임 — T13
    SQLAlchemy 구현체와 동일 알고리즘 사용 (oracle 2 차 리뷰 C5).
    """

    def __init__(self, records: Sequence[CorporateActionRecord]) -> None:
        self._by_code: dict[str, list[CorporateActionRecord]] = defaultdict(list)
        for r in records:
            self._by_code[r.code].append(r)
        self._enforcer = PITEnforcer()

    def fetch_actions(
        self,
        code: str,
        *,
        as_of: date,
        action_types: frozenset[str] | None = None,
    ) -> Sequence[CorporateActionRecord]:
        all_records = list(self._by_code.get(code, []))
        if action_types is not None:
            all_records = [r for r in all_records if r.action_type in action_types]

        # 정정공시 chain 해소를 group key 없이 record 단위로 수행 — 여러 별개 사건
        # (split / dividend / buyback) 이 한 record list 에 공존 가능하고, 정정 시
        # effective_date 자체가 바뀌는 시나리오도 cross-chain 단절 없이 처리됨
        # (oracle 2 차 리뷰 NEW-C1).
        active = self._enforcer.filter_active_records(
            all_records, as_of, date_of=lambda r: r.announced_date,
        )
        # 결정성 sort — effective_date, created_at, id final tiebreaker.
        return sorted(
            active,
            key=lambda r: (r.effective_date, r.created_at, str(r.id)),
        )


class FakeStockSnapshotRepository(StockSnapshotRepository):
    """In-memory snapshot store. as_of_date exact match."""

    def __init__(self, records: Sequence[StockSnapshotRecord]) -> None:
        # (code, as_of_date, factor_uuid) → record.
        self._by_key: dict[tuple[str, date, UUID], StockSnapshotRecord] = {}
        self._by_code_asof: dict[tuple[str, date], list[StockSnapshotRecord]] = defaultdict(list)
        for r in records:
            key = (r.stock_code, r.as_of_date, r.factor_uuid)
            self._by_key[key] = r
            self._by_code_asof[(r.stock_code, r.as_of_date)].append(r)

    def fetch_snapshot(
        self,
        code: str,
        *,
        as_of: date,
        factor_uuid: UUID,
    ) -> StockSnapshotRecord | None:
        return self._by_key.get((code, as_of, factor_uuid))

    def fetch_snapshots_for_code(
        self,
        code: str,
        *,
        as_of: date,
        factor_uuids: frozenset[UUID] | None = None,
    ) -> Sequence[StockSnapshotRecord]:
        all_for_key = self._by_code_asof.get((code, as_of), [])
        if factor_uuids is None:
            return list(all_for_key)
        return [r for r in all_for_key if r.factor_uuid in factor_uuids]
