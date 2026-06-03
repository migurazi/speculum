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
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Protocol
from uuid import UUID

from app.repositories.batch_run_repository import BatchCutoff, CitationBatch
from app.repositories.pit_protocols import (
    CorporateActionRecord,
    CorporateActionRepository,
    FinancialRecord,
    FinancialRepository,
    MacroIndicatorRecord,
    MacroIndicatorRepository,
    MarketCapRecord,
    MarketCapRepository,
    NavRecord,
    NavRepository,
    PriceRecord,
    PriceRepository,
    StockSnapshotRecord,
    StockSnapshotRepository,
    TreasurySharesRecord,
    TreasurySharesRepository,
)
from app.services.pit_enforcer import PITEnforcer

__all__ = [
    "FakeCorporateActionRepository",
    "FakeFinancialRepository",
    "FakeMacroIndicatorRepository",
    "FakeMarketCapRepository",
    "FakeNavRepository",
    "FakePriceRepository",
    "FakeStockSnapshotRepository",
    "FakeTreasurySharesRepository",
]


class _HasCitationId(Protocol):
    """cutoff 필터가 의존하는 최소 속성 — record 가 citation_id 보유 (H#4)."""

    citation_id: UUID


def _passes_cutoff(
    record: _HasCitationId,
    *,
    cutoff: BatchCutoff,
    source: str,
    citation_runs: Mapping[UUID, CitationBatch] | None,
) -> bool:
    """record 가 cutoff 이하 batch 가 생산한 것인지 — Fake 의 cutoff 술어 (M1 T48c).

    SQL 의 `_batch_cutoff_predicate` (citation→batch_runs 2-hop join + source +
    lexicographic) 와 동일 의미를 Fake 의 평탄화 map (`citation_runs`) 으로 구현.

    판정:
        - **EXCLUDE_ALL (H#5)**: cutoff 가 sentinel 이면 항상 False — 그 source
          fact 전부 제외.
        - **citation_runs 부재**: cutoff 무시 불가 (재현 정확성). map 이 없으면
          어떤 record 도 batch 를 알 수 없음 → 보수적으로 False (전부 제외).
          테스트는 cutoff 사용 시 citation_runs 를 명시 주입해야 함 (Momus 경고).
        - **record.citation_id 미등록**: 그 record 의 생산 batch 불명 → False.
        - **source 불일치 (H#3)**: entry.source != source → False (타-source
          batch citation 오염 방어).
        - **lexicographic (C#2)**: `(entry.started_at, entry.batch_id) <=
          (cutoff.started_at, cutoff.id)` 아니면 False.
    """
    if cutoff.is_exclude_all:
        return False
    if citation_runs is None:
        # cutoff 가 주어졌는데 batch 매핑이 없음 — 어떤 record 도 cutoff 이하임을
        # 증명할 수 없으므로 보수적으로 전부 제외 (look-ahead 누출 방지).
        return False
    entry = citation_runs.get(record.citation_id)
    if entry is None:
        return False
    if entry.source != source:
        return False
    return cutoff.allows(started_at=entry.started_at, batch_id=entry.batch_id)


class FakePriceRepository(PriceRepository):
    """In-memory price store. KRX 가격은 supersede 없음 → 단순 필터."""

    def __init__(
        self,
        records: Sequence[PriceRecord],
        *,
        citation_runs: Mapping[UUID, CitationBatch] | None = None,
    ) -> None:
        # code 별 index — fetch_prices 의 효율.
        self._by_code: dict[str, list[PriceRecord]] = defaultdict(list)
        for r in records:
            self._by_code[r.code].append(r)
        # 일자 오름차순.
        for code in self._by_code:
            self._by_code[code].sort(key=lambda r: r.effective_date)
        # M1 T48c — citation_id → 생산 batch (started_at, id, source) 평탄화 map.
        # SQL 의 source_citations→batch_runs join 의 Fake 등가물. cutoff 필터 시만.
        self._citation_runs = citation_runs

    def fetch_prices(
        self,
        code: str,
        *,
        as_of: date,
        start: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> Sequence[PriceRecord]:
        if start > as_of:
            return []
        candidates = [
            r for r in self._by_code.get(code, [])
            if start <= r.effective_date <= as_of
        ]
        # M1 T48c 재현 — cutoff 주입 시 frozen 이하 KRX batch 가 생산한 row 만
        # (backfill 제외, C#1). default None = 무필터 (회귀 0).
        if batch_cutoff is not None:
            candidates = [
                r for r in candidates
                if _passes_cutoff(
                    r, cutoff=batch_cutoff, source="KRX",
                    citation_runs=self._citation_runs,
                )
            ]
        return candidates

    def save_prices(self, records: Sequence[PriceRecord]) -> None:
        """T18 합류 — bulk insert. 같은 (code, date) overwrite 의미 (Fake 정책).

        SQL 구현체는 UNIQUE constraint 로 중복 raise — 호출자가 dedup 해야 함.
        Fake 는 단순화: 같은 (code, effective_date) 가 있으면 새 record 로 교체.
        """
        for new_record in records:
            bucket = self._by_code[new_record.code]
            # 같은 (code, effective_date) 의 기존 record 제거 후 새로 삽입.
            bucket[:] = [
                r for r in bucket
                if r.effective_date != new_record.effective_date
            ]
            bucket.append(new_record)
        # 영향받은 code 만 재정렬.
        for new_record in records:
            self._by_code[new_record.code].sort(
                key=lambda r: r.effective_date,
            )


class FakeMarketCapRepository(MarketCapRepository):
    """In-memory market_cap store. KRX 시가총액은 supersede 없음 → 단순 필터.

    PriceRepository 와 동일 PIT 의미 — `effective_date <= as_of` 중 최신.
    """

    def __init__(
        self,
        records: Sequence[MarketCapRecord],
        *,
        citation_runs: Mapping[UUID, CitationBatch] | None = None,
    ) -> None:
        # code 별 index — fetch_latest 의 효율.
        self._by_code: dict[str, list[MarketCapRecord]] = defaultdict(list)
        for r in records:
            self._by_code[r.code].append(r)
        # 일자 오름차순.
        for code in self._by_code:
            self._by_code[code].sort(key=lambda r: r.effective_date)
        # M1 T48c — citation_id → 생산 batch 평탄화 map (cutoff 필터 시만).
        self._citation_runs = citation_runs

    def fetch_latest(
        self,
        code: str,
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> MarketCapRecord | None:
        candidates = [
            r for r in self._by_code.get(code, [])
            if r.effective_date <= as_of
        ]
        # M1 T48c 재현 — cutoff 주입 시 frozen 이하 KRX batch 가 생산한 row 로
        # 한정한 뒤 max (backfill row 제외, C#1). default None = 무필터 (회귀 0).
        if batch_cutoff is not None:
            candidates = [
                r for r in candidates
                if _passes_cutoff(
                    r, cutoff=batch_cutoff, source="KRX",
                    citation_runs=self._citation_runs,
                )
            ]
        if not candidates:
            return None
        # effective_date 최신 — 동일 일자 tie 시 created_at, id final tiebreaker
        # (결정성). KRX 시가총액은 정정 없으나 방어적 결정성 sort.
        return max(
            candidates,
            key=lambda r: (r.effective_date, r.created_at, str(r.id)),
        )

    def save_market_caps(self, records: Sequence[MarketCapRecord]) -> None:
        """Phase B 합류 — bulk insert. 같은 (code, date) overwrite (Fake 정책).

        SQL 구현체는 UNIQUE constraint 로 중복 raise — 호출자가 dedup 해야 함.
        Fake 는 단순화: 같은 (code, effective_date) 가 있으면 새 record 로 교체
        (FakePriceRepository.save_prices 와 동일).
        """
        for new_record in records:
            bucket = self._by_code[new_record.code]
            bucket[:] = [
                r for r in bucket
                if r.effective_date != new_record.effective_date
            ]
            bucket.append(new_record)
        for new_record in records:
            self._by_code[new_record.code].sort(
                key=lambda r: r.effective_date,
            )


class FakeFinancialRepository(FinancialRepository):
    """In-memory financial store + supersede chain 해소.

    PITEnforcer.latest_active_by_key 로 (code, account) 별 latest active 를
    `fiscal_period` 별로 그룹화하여 반환. 본 구현이 T13 SQLAlchemy 의 reference.
    """

    def __init__(
        self,
        records: Sequence[FinancialRecord],
        *,
        citation_runs: Mapping[UUID, CitationBatch] | None = None,
    ) -> None:
        self._by_code: dict[str, list[FinancialRecord]] = defaultdict(list)
        for r in records:
            self._by_code[r.code].append(r)
        self._enforcer = PITEnforcer()
        # M1 T48c — citation_id → 생산 batch 평탄화 map (cutoff 필터 시만).
        self._citation_runs = citation_runs

    def save_financials(self, records: Sequence[FinancialRecord]) -> None:
        """T19 DART 일배치 합류 — bulk insert. 같은 id overwrite (Fake 단순화).

        SQL 구현체는 PK 중복 시 IntegrityError — 호출자가 정정공시 시
        superseded_by 만 지정 + 새 id 부여 의무.
        """
        for new_record in records:
            bucket = self._by_code[new_record.code]
            # 같은 id 가 있으면 제거 후 새로 삽입 (overwrite 의미).
            bucket[:] = [r for r in bucket if r.id != new_record.id]
            bucket.append(new_record)

    def fetch_restatement_history(
        self,
        code: str,
        *,
        fiscal_period: str | None = None,
        as_of: date | None = None,
    ) -> list[FinancialRecord]:
        """정정공시 이력 — 모든 vintage(active + superseded) 반환.

        read-only — _by_code 의 기존 데이터를 필터·정렬만. 어떠한 변경도 없음
        (ADR-0020 append-only 불변식 미변경). Protocol 의 계약과 동일.

        PIT look-ahead 차단: as_of 주어지면 effective_date <= as_of 만 통과.
        fiscal_period 주어지면 해당 분기만. 정렬: (fiscal_period, effective_date, id).
        """
        candidates = list(self._by_code.get(code, []))
        # fiscal_period 필터 — None 이면 전 분기.
        if fiscal_period is not None:
            candidates = [r for r in candidates if r.fiscal_period == fiscal_period]
        # PIT look-ahead 차단 — as_of 이후 공시된 vintage 제외 (§2.4).
        if as_of is not None:
            candidates = [r for r in candidates if r.effective_date <= as_of]
        # (fiscal_period asc, effective_date asc, id asc) — 정정 chain 시간순, 결정적.
        candidates.sort(key=lambda r: (r.fiscal_period, r.effective_date, str(r.id)))
        return candidates

    def fetch_financials(
        self,
        code: str,
        *,
        as_of: date,
        account: str,
        ifrs_type: str | None = None,
        max_periods: int = 8,
        batch_cutoff: BatchCutoff | None = None,
    ) -> Sequence[FinancialRecord]:
        all_records = [
            r for r in self._by_code.get(code, [])
            if r.account == account
            and (ifrs_type is None or r.ifrs_type == ifrs_type)
        ]
        # M1 T48c 재현 — cutoff 주입 시 frozen 이하 DART batch 가 생산한 row 로
        # candidate 한정. 이후 정정 (successor) row 가 빠지면 latest_active_by_key
        # 의 보수 분기 (pit_enforcer.py:306-311) 가 원 row active 복원 → 정정 전
        # 값 재현 (C#1, load-bearing). 빈 candidates group 은 NoActiveRecordError
        # group 내부 흡수 → silent skip (M#6). default None = 무필터 (회귀 0).
        if batch_cutoff is not None:
            all_records = [
                r for r in all_records
                if _passes_cutoff(
                    r, cutoff=batch_cutoff, source="DART",
                    citation_runs=self._citation_runs,
                )
            ]
        # fiscal_period 별 latest active (effective_date 기준 PIT). ifrs_type 은
        # 그룹화 이전 필터 (위) — 연결/별도 별개 chain (ADR-0005) 충돌 방지.
        latest_by_period = self._enforcer.latest_active_by_key(
            all_records, as_of, key=lambda r: r.fiscal_period
        )
        # 결정성 보장 sort key — id 가 final tiebreaker (oracle 2 차 리뷰 C6).
        sorted_periods = sorted(
            latest_by_period.values(),
            key=lambda r: (r.effective_date, r.fiscal_period, str(r.id)),
        )
        return sorted_periods[-max_periods:]


class FakeTreasurySharesRepository(TreasurySharesRepository):
    """In-memory 자사주 store + supersede chain 해소 (financials 패턴).

    PITEnforcer.latest_active_by_key 로 code 별 fiscal_period chain 을 as_of-time
    해소한 뒤 가장 최신 fiscal_period 1 건을 반환. FakeFinancialRepository 와
    동일 알고리즘 — T13 SQLAlchemy 의 reference.
    """

    def __init__(
        self,
        records: Sequence[TreasurySharesRecord],
        *,
        citation_runs: Mapping[UUID, CitationBatch] | None = None,
    ) -> None:
        self._by_code: dict[str, list[TreasurySharesRecord]] = defaultdict(list)
        for r in records:
            self._by_code[r.code].append(r)
        self._enforcer = PITEnforcer()
        # M1 T48c — citation_id → 생산 batch 평탄화 map (cutoff 필터 시만).
        self._citation_runs = citation_runs

    def fetch_latest_active(
        self,
        code: str,
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> TreasurySharesRecord | None:
        all_records = list(self._by_code.get(code, []))
        # M1 T48c 재현 — financials 와 동일 정정 chain × cutoff 보수 분기 복원
        # (C#1, source='DART'). default None = 무필터 (회귀 0).
        if batch_cutoff is not None:
            all_records = [
                r for r in all_records
                if _passes_cutoff(
                    r, cutoff=batch_cutoff, source="DART",
                    citation_runs=self._citation_runs,
                )
            ]
        # fiscal_period 별 latest active (effective_date 기준 PIT 정정 chain 해소).
        latest_by_period = self._enforcer.latest_active_by_key(
            all_records, as_of, key=lambda r: r.fiscal_period
        )
        if not latest_by_period:
            return None
        # 가장 최신 fiscal_period — effective_date, fiscal_period, id final
        # tiebreaker (SqlTreasurySharesRepository 와 동일 결정성).
        return max(
            latest_by_period.values(),
            key=lambda r: (r.effective_date, r.fiscal_period, str(r.id)),
        )

    def save_treasury_shares(
        self, records: Sequence[TreasurySharesRecord],
    ) -> None:
        """DART 일배치 합류 — bulk insert. 같은 id overwrite (Fake 단순화).

        SQL 구현체는 PK 중복 시 IntegrityError — 호출자가 정정공시 시
        superseded_by 만 지정 + 새 id 부여 의무 (FakeFinancialRepository 동일).
        """
        for new_record in records:
            bucket = self._by_code[new_record.code]
            bucket[:] = [r for r in bucket if r.id != new_record.id]
            bucket.append(new_record)


class FakeMacroIndicatorRepository(MacroIndicatorRepository):
    """In-memory ECOS 거시지표 store — vintage 이중 시간축 PIT 조회.

    SqlMacroIndicatorRepository 와 동일 규약을 in-memory 로 구현 — contract 동등성
    기준 구현체. 생성자에 record list 를 주입하고 fetch_latest 가 동일 PIT 필터 +
    정렬 + 첫 번째 row 선택 로직을 수행.
    """

    def __init__(
        self,
        records: list[MacroIndicatorRecord] | None = None,
    ) -> None:
        # indicator_id 별 index — fetch_latest 의 효율.
        self._by_indicator: dict[str, list[MacroIndicatorRecord]] = defaultdict(list)
        for r in records or []:
            self._by_indicator[r.indicator_id].append(r)

    def fetch_latest(
        self,
        indicator_id: str,
        *,
        as_of: date,
    ) -> MacroIndicatorRecord | None:
        # vintage 이중 시간축 PIT 필터 — SQL 구현과 동일 조건.
        #   reference_date <= as_of: 미래 기준 기간 제외.
        #   vintage_date   <= as_of: as_of 이후 공표된 관측 제외 (잠정→확정 재현 축).
        candidates = [
            r for r in self._by_indicator.get(indicator_id, [])
            if r.reference_date <= as_of and r.vintage_date <= as_of
        ]
        if not candidates:
            return None
        # SQL 의 ORDER BY reference_date DESC, vintage_date DESC LIMIT 1 과 동일.
        # reference_date 최신 기간 우선, 동일 기간 내 vintage_date 최신 관측 우선.
        return max(
            candidates,
            key=lambda r: (r.reference_date, r.vintage_date),
        )


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


class FakeNavRepository(NavRepository):
    """In-memory ETF NAV store — ADR-0023 D3-a, R4 deferred.

    KRX NAV 는 정정 안 됨 → supersede 없음. FakeMarketCapRepository 와 동일
    PIT 의미론 (`effective_date <= as_of` 중 최신).

    canonical_id / factor pack 미등록 상태. SqlNavRepository 는 R4 합류 시 구현.
    """

    def __init__(self, records: Sequence[NavRecord]) -> None:
        # code 별 index — get_nav 의 효율.
        self._by_code: dict[str, list[NavRecord]] = defaultdict(list)
        for r in records:
            self._by_code[r.code].append(r)
        # 일자 오름차순.
        for code in self._by_code:
            self._by_code[code].sort(key=lambda r: r.effective_date)

    def get_nav(
        self,
        code: str,
        *,
        as_of: date,
    ) -> NavRecord | None:
        """`effective_date <= as_of` 중 가장 최신 NavRecord. 없으면 None."""
        candidates = [
            r for r in self._by_code.get(code, [])
            if r.effective_date <= as_of
        ]
        if not candidates:
            return None
        # effective_date 최신 — 동일 일자 tie 시 created_at, id final tiebreaker.
        return max(
            candidates,
            key=lambda r: (r.effective_date, r.created_at, str(r.id)),
        )

    def save_navs(self, records: Sequence[NavRecord]) -> None:
        """NavRecord bulk insert. 같은 (code, effective_date) overwrite (Fake 정책).

        SQL 구현체는 UNIQUE constraint 로 중복 raise — 호출자가 dedup 해야 함.
        Fake 는 단순화: 같은 (code, effective_date) 가 있으면 새 record 로 교체.
        """
        for new_record in records:
            bucket = self._by_code[new_record.code]
            bucket[:] = [
                r for r in bucket
                if r.effective_date != new_record.effective_date
            ]
            bucket.append(new_record)
        for new_record in records:
            self._by_code[new_record.code].sort(
                key=lambda r: r.effective_date,
            )
