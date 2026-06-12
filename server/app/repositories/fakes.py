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
from dataclasses import replace
from datetime import date
from typing import Protocol
from uuid import UUID

from app.repositories.batch_run_repository import BatchCutoff, CitationBatch
from app.repositories.pit_protocols import (
    CorporateActionRecord,
    CorporateActionRepository,
    DividendRepository,
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
from app.repositories.pit_resolution import (
    resolve_active_actions,
    resolve_dividends,
    resolve_financial_periods,
    resolve_latest_treasury,
)
from app.repositories.sql_repositories import AppendOnlyViolationError
from app.services.pit_enforcer import PITEnforcer

__all__ = [
    "FakeCorporateActionRepository",
    "FakeDividendRepository",
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

    def fetch_prices_bulk(
        self,
        codes: Sequence[str],
        *,
        as_of: date,
        start: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> dict[str, tuple[PriceRecord, ...]]:
        """여러 code 의 [start, as_of] 시계열 bulk fetch — 단건 fetch_prices 에 위임.

        CachingPriceRepository prime 의 Fake 등가물. 단건 fetch_prices 를 code 마다
        호출하므로 SQL 의 IN 쿼리와 **byte-동일 결과** 보장(같은 알고리즘 단일
        source — SQL/Fake contract 동등성). 누락 code 는 빈 tuple.
        """
        return {
            c: tuple(
                self.fetch_prices(
                    c, as_of=as_of, start=start, batch_cutoff=batch_cutoff,
                )
            )
            for c in dict.fromkeys(codes)
        }

    def fetch_codes_with_prices(
        self,
        codes: Sequence[str],
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> set[str]:
        """as_of 이하 가격이 1건이라도 있는 code 집합 — 단건 존재판정에 위임.

        `fetch_prices(start=date.min)` 의 "빈 시계열 여부" 와 동일 의미(SQL DISTINCT
        쿼리의 Fake 등가물). _select_portfolio survivorship(ADR-0027 D3)용.
        """
        return {
            c
            for c in dict.fromkeys(codes)
            if c
            and self.fetch_prices(
                c, as_of=as_of, start=date.min, batch_cutoff=batch_cutoff,
            )
        }

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

    def fetch_latest_bulk(
        self,
        codes: Sequence[str],
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> dict[str, MarketCapRecord]:
        """여러 code 의 latest market_cap(<=as_of) bulk fetch — 단건 fetch_latest 위임.

        CachingMarketCapRepository prime 의 Fake 등가물. 단건 fetch_latest 를 code
        마다 호출 → SQL window-function bulk 와 동일 결과(같은 tiebreak). 데이터
        없는 code 는 dict 부재(caching .get → None, 단건 None 과 동일).
        """
        result: dict[str, MarketCapRecord] = {}
        for c in dict.fromkeys(codes):
            rec = self.fetch_latest(c, as_of=as_of, batch_cutoff=batch_cutoff)
            if rec is not None:
                result[c] = rec
        return result

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
        candidates = list(self._by_code.get(code, []))
        # M1 T48c 재현 — cutoff 주입 시 frozen 이하 DART batch 가 생산한 row 로
        # candidate 한정. 이후 정정 (successor) row 가 빠지면 latest_active_by_key
        # 의 보수 분기 (pit_enforcer.py:306-311) 가 원 row active 복원 → 정정 전
        # 값 재현 (C#1, load-bearing). 빈 candidates group 은 NoActiveRecordError
        # group 내부 흡수 → silent skip (M#6). default None = 무필터 (회귀 0).
        if batch_cutoff is not None:
            candidates = [
                r for r in candidates
                if _passes_cutoff(
                    r, cutoff=batch_cutoff, source="DART",
                    citation_runs=self._citation_runs,
                )
            ]
        # account/ifrs 필터 + fiscal_period chain 해소 + sort + truncate 는
        # pit_resolution 공유 helper (Sql·Caching 과 단일 출처). candidates 는
        # cutoff-필터된 전체 vintage(account 필터 이전). Fake 는 reference 라
        # assert 미포함(기존 동작 보존).
        return resolve_financial_periods(
            candidates,
            as_of,
            account=account,
            ifrs_type=ifrs_type,
            max_periods=max_periods,
            enforcer=self._enforcer,
        )

    def fetch_all_financials_bulk(
        self,
        codes: Sequence[str],
        *,
        batch_cutoff: BatchCutoff | None = None,
    ) -> dict[str, tuple[FinancialRecord, ...]]:
        """여러 code 의 전체 vintage raw row 를 bulk 반환 — SqlFinancialRepository.
        fetch_all_financials_bulk 의 Fake 등가물(CachingFinancialRepository.prime 용).

        단건 fetch_financials 의 candidate set(account 필터·해소 이전, cutoff-필터된
        전체 vintage)과 동일하게 code 별 raw row 를 반환. account 필터·date 필터 미적용
        (oracle 설계검토 C1 — chain integrity 등가성). 누락 code 는 빈 tuple.
        """
        result: dict[str, tuple[FinancialRecord, ...]] = {}
        for code in dict.fromkeys(codes):
            candidates = list(self._by_code.get(code, []))
            if batch_cutoff is not None:
                candidates = [
                    r for r in candidates
                    if _passes_cutoff(
                        r, cutoff=batch_cutoff, source="DART",
                        citation_runs=self._citation_runs,
                    )
                ]
            result[code] = tuple(candidates)
        return result


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
        candidates = list(self._by_code.get(code, []))
        # M1 T48c 재현 — financials 와 동일 정정 chain × cutoff 보수 분기 복원
        # (C#1, source='DART'). default None = 무필터 (회귀 0).
        if batch_cutoff is not None:
            candidates = [
                r for r in candidates
                if _passes_cutoff(
                    r, cutoff=batch_cutoff, source="DART",
                    citation_runs=self._citation_runs,
                )
            ]
        # fiscal_period chain 해소 + 최신 1건은 pit_resolution 공유 helper (Sql·
        # Caching 과 단일 출처). Fake 는 reference 라 assert 미포함(기존 동작 보존).
        return resolve_latest_treasury(
            candidates, as_of, enforcer=self._enforcer,
        )

    def fetch_all_treasury_bulk(
        self,
        codes: Sequence[str],
        *,
        batch_cutoff: BatchCutoff | None = None,
    ) -> dict[str, tuple[TreasurySharesRecord, ...]]:
        """여러 code 의 전체 vintage 자사주 raw row 를 bulk 반환 —
        SqlTreasurySharesRepository.fetch_all_treasury_bulk 의 Fake 등가물.

        단건 fetch_latest_active 의 candidate set(해소·date 필터 이전, cutoff-필터된
        전체 vintage)과 동일. 누락 code 는 빈 tuple (oracle 설계검토 C1 동일 근거).
        """
        result: dict[str, tuple[TreasurySharesRecord, ...]] = {}
        for code in dict.fromkeys(codes):
            candidates = list(self._by_code.get(code, []))
            if batch_cutoff is not None:
                candidates = [
                    r for r in candidates
                    if _passes_cutoff(
                        r, cutoff=batch_cutoff, source="DART",
                        citation_runs=self._citation_runs,
                    )
                ]
            result[code] = tuple(candidates)
        return result

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
        # action_types 필터 + announced_date chain 해소(group key 없이 record 단위 —
        # split/dividend/buyback 공존, 정정 시 effective_date 변경도 cross-chain 단절
        # 없이 처리, oracle 2 차 NEW-C1) + 결정성 sort 는 pit_resolution 공유 helper
        # (Sql·Caching 과 단일 출처). Fake 는 reference 라 assert 미포함(기존 동작 보존).
        return resolve_active_actions(
            list(self._by_code.get(code, [])),
            as_of,
            action_types=action_types,
            enforcer=self._enforcer,
        )

    def fetch_all_actions_bulk(
        self,
        codes: Sequence[str],
    ) -> dict[str, tuple[CorporateActionRecord, ...]]:
        """여러 code 의 전체 vintage raw 사건을 bulk 반환 — SqlCorporateActionRepository.
        fetch_all_actions_bulk 의 Fake 등가물(CachingCorporateActionRepository.prime 용).

        단건 fetch_actions 의 candidate set(action_types·해소 이전 전체 vintage)과
        동일하게 code 별 raw 를 반환. 누락 code 는 빈 tuple.
        """
        return {
            code: tuple(self._by_code.get(code, []))
            for code in dict.fromkeys(codes)
        }


class FakeDividendRepository(DividendRepository):
    """In-memory cash_dividend store + 이중 PIT (announced/effective) 적용.

    SqlDividendRepository 와 동일 contract 를 in-memory 로 구현 (contract 동등성
    기준 구현체). 이중 PIT 설계는 DividendRepository docstring(pit_protocols.py) 참조.

    announced_date 축 — PITEnforcer.filter_active_records(date_of=announced_date)
    로 supersede chain 해소 포함. effective_date 축 — 추가 필터
    (effective_date <= as_of).
    """

    def __init__(self, records: Sequence[CorporateActionRecord]) -> None:
        self._by_code: dict[str, list[CorporateActionRecord]] = defaultdict(list)
        for r in records:
            self._by_code[r.code].append(r)
        self._enforcer = PITEnforcer()

    def fetch_dividends(
        self,
        code: str,
        *,
        as_of: date,
    ) -> Sequence[CorporateActionRecord]:
        # announced 축 chain 해소(전체 corporate_action 집합 기준 — cash_dividend 로
        # 좁힌 부분 차집합이 아님, chain truncation 금지) → chain 해소 후 cash_dividend
        # 필터 → effective<=as_of 필터 → 결정성 sort 는 pit_resolution 공유 helper
        # (Sql·CachingDividendRepository 와 단일 출처). __init__ 이 모든 action_type
        # record 를 보관하므로 _by_code[code] 가 곧 전체 set. Fake 는 reference 라
        # assert 미포함(기존 동작 보존 — resolve_active_actions 와 동일 경계).
        return resolve_dividends(
            list(self._by_code.get(code, [])),
            as_of,
            enforcer=self._enforcer,
        )

    def fetch_all_dividends_bulk(
        self,
        codes: Sequence[str],
    ) -> dict[str, tuple[CorporateActionRecord, ...]]:
        """여러 code 의 전체 vintage raw 사건을 bulk 반환 — SqlDividendRepository.
        fetch_all_dividends_bulk 의 Fake 등가물(CachingDividendRepository.prime 용).

        단건 fetch_dividends 의 candidate set(action_type·해소 이전 전체 vintage 의
        corporate_action — cash_dividend 로 좁히지 **않음**)과 동일하게 code 별 raw 를
        반환. 누락 code 는 빈 tuple. fetch_all_actions_bulk 와 동일 구조.
        """
        return {
            code: tuple(self._by_code.get(code, []))
            for code in dict.fromkeys(codes)
        }

    def save_dividends(
        self, records: Sequence[CorporateActionRecord],
    ) -> None:
        """append-only insert (SqlDividendRepository.save_dividends 와 동일 contract).

        - action_type != "cash_dividend" 는 ValueError.
        - insert 시 superseded_by 가 non-NULL 이면 ValueError (정정공시는
          update_superseded_by 로). insert 는 항상 superseded_by=NULL.
        - batch 전체 검증 후 mutate (atomic — 부분 mutate 후 raise 금지).
        """
        # 전체 batch 를 먼저 검증 — 위반 시 어떤 record 도 저장하지 않음 (atomic).
        for r in records:
            if r.action_type != "cash_dividend":
                raise ValueError(
                    f"DividendRepository 는 cash_dividend 만 허용 — "
                    f"record {r.id} 의 action_type={r.action_type!r}"
                )
            if r.superseded_by is not None:
                raise ValueError(
                    f"insert 시 superseded_by 금지 — record {r.id} 의 "
                    f"superseded_by={r.superseded_by!r}. 정정공시 supersede 는 "
                    f"update_superseded_by 를 사용"
                )
        # 검증 통과 후에만 mutate.
        for r in records:
            bucket = self._by_code[r.code]
            # Fake 는 동일 id overwrite (SQL 은 IntegrityError).
            bucket[:] = [e for e in bucket if e.id != r.id]
            bucket.append(r)

    def update_superseded_by(
        self, record_id: UUID, successor_id: UUID,
    ) -> None:
        """정정공시 chain — cash_dividend 원 row 의 superseded_by 를 NULL→successor set.

        SqlDividendRepository.update_superseded_by 와 동일 불변식 (대상/successor
        부재 또는 이미 superseded 면 AppendOnlyViolationError). cash_dividend 대상만
        허용 — 대상이 다른 action_type 이면 not-found 와 동일하게 취급(본 store 는
        cash_dividend 만 보관).
        """
        target: CorporateActionRecord | None = None
        target_code: str | None = None
        target_index: int | None = None
        for code, bucket in self._by_code.items():
            for i, e in enumerate(bucket):
                if e.id == record_id:
                    target, target_code, target_index = e, code, i
                    break
            if target is not None:
                break
        if target is None or target.action_type != "cash_dividend":
            raise AppendOnlyViolationError(
                f"cash_dividend row {record_id} 부재 — update_superseded_by 불가"
            )
        if target.superseded_by is not None:
            raise AppendOnlyViolationError(
                f"cash_dividend row {record_id} 는 이미 superseded "
                f"(superseded_by={target.superseded_by}) — chain 1 회성 위반"
            )
        successor_exists = any(
            e.id == successor_id
            for bucket in self._by_code.values()
            for e in bucket
        )
        if not successor_exists:
            raise AppendOnlyViolationError(
                f"successor cash_dividend row {successor_id} 부재 — "
                f"self-FK 무결성 위반"
            )
        # frozen dataclass — superseded_by 만 바꾼 새 record 로 교체 (immutability).
        assert target_code is not None and target_index is not None
        self._by_code[target_code][target_index] = replace(
            target, superseded_by=successor_id,
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

    def save_snapshots(self, records: Sequence[StockSnapshotRecord]) -> None:
        """precompute 합류 — 같은 key overwrite (Fake 단순화, FakeFinancial 패턴).

        SQL 구현체는 PK 중복 시 IntegrityError — 호출자(일배치)가 재계산 시
        선삭제/dedup 책임(save_financials 동일 정책). Fake 는 (code, as_of, factor)
        key overwrite 로 단순화하되, _by_code_asof 의 stale 중복도 함께 정리한다.
        """
        for r in records:
            key = (r.stock_code, r.as_of_date, r.factor_uuid)
            self._by_key[key] = r
            bucket = self._by_code_asof[(r.stock_code, r.as_of_date)]
            # 같은 factor 의 옛 row 제거 후 추가(overwrite 의미 — 중복 누적 방지).
            bucket[:] = [b for b in bucket if b.factor_uuid != r.factor_uuid]
            bucket.append(r)


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
