"""PIT Enforcer — record-level look-ahead 차단 + 정정공시 chain 해소.

본 모듈은 Speculum 의 가장 차별화된 정책 (8 기둥 §2.4 Point-in-Time Correctness)
의 backbone. Repository Protocol layer 와 함께 모든 historical 쿼리가 통과해야
하는 외곽 경계 (perimeter).

설계 원칙:

1. **Default = 묵시적 필터 (filter-out)** — `filter_records_at_or_before` 가
   `effective_date <= as_of` 인 record 만 통과. 호출자가 lookahead 가능한 record
   를 받지 못함.
2. **Assert layer (regression detection)** — `assert_no_lookahead` 가 결과 list 를
   한 번 더 검사. 묵시적 필터가 깨졌을 때 fail-fast. 운영 부담은 list 1 회 traversal.
3. **As-of-time supersede** — 정정공시 chain 은 *현재 시점* 의 active 가 아니라
   *as_of 시점* 의 active 를 해소 (oracle 자문 §3). successor 의 effective_date 가
   as_of 보다 미래면 옛 record 가 그 시점에는 여전히 active.
4. **Fail-fast on corruption** — cycle / chain depth 초과 시 `PITDataCorruptionError`
   즉시 raise. 무한 loop 방지.
5. **Tie-break = `created_at`** — 같은 `effective_date` 의 두 record (회계기간 소급
   정정) 는 announce 시점 (`created_at`) 늦은 것이 우선.

본 모듈은 standalone — DB / 외부망 의존 X. Repository Protocol 위에서만 작동.
SQLAlchemy 구현체 (T13 합류 후) 가 같은 의미를 query optimization 으로 달성.

관련 ADR / 문서:
- ADR-0008 D5 (PIT Enforcer 인터페이스), D8 (일 단위 PIT)
- ADR-0002 D3 (Source Citation effective_date)
- ADR-0009 D5 (supersede chain — 본 모듈의 알고리즘 core)
- M0_PLAN T21 / AC-P-04 (raw query path 0 보증)
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from datetime import date
from typing import Final, TypeVar
from uuid import UUID

from app.repositories.pit_protocols import PITRecord, SupersedableRecord
from app.services.as_of_policy import PIT_POLICY_VERSION

__all__ = [
    "PITEnforcer",
    "PITError",
    "LookAheadError",
    "NoActiveRecordError",
    "PITDataCorruptionError",
]

# Chain depth hard limit — 정상 운영에서 한 fiscal_period 의 정정공시가 100 회
# 누적되는 일은 없음. 넘으면 데이터 corruption (oracle 자문 §3 P1).
_DEFAULT_MAX_CHAIN_DEPTH: Final[int] = 100


# =============================================================================
# Errors — `from exc` 강제 (oracle R5)
# =============================================================================

class PITError(Exception):
    """PIT 정책 위반의 모든 예외 base."""


class LookAheadError(PITError):
    """결과 list 에 `effective_date > as_of` 인 record 포함 — server contract 위반.

    묵시적 필터 (`filter_records_at_or_before`) 가 깨졌을 때 `assert_no_lookahead`
    가 던지는 fail-fast 예외. 사용자 fault 아닌 server-side regression — API
    layer 에서 500 + Sentry alert 권장.
    """

    def __init__(
        self,
        record_id: UUID,
        effective_date: date,
        as_of: date,
    ) -> None:
        super().__init__(
            f"record {record_id} has effective_date {effective_date.isoformat()} "
            f"> as_of {as_of.isoformat()} — server PIT contract violated."
        )
        self.record_id: Final[UUID] = record_id
        self.effective_date: Final[date] = effective_date
        self.as_of: Final[date] = as_of


class NoActiveRecordError(PITError):
    """`as_of` 시점에 active 였던 record 가 없음.

    예: 종목이 `as_of` 이후 상장됐거나, 그 시점 이전 재무제표가 아직 공시되지
    않은 경우. 도메인별 처리 — API 가 404 또는 빈 결과로 변환.
    """

    def __init__(self, as_of: date, context: str = "") -> None:
        suffix = f" ({context})" if context else ""
        super().__init__(
            f"no active record at as_of {as_of.isoformat()}{suffix}."
        )
        self.as_of: Final[date] = as_of


class PITDataCorruptionError(PITError):
    """`superseded_by` chain 의 cycle 또는 depth 초과 — 데이터 corruption.

    정상 운영에서 발생 불가. 발생 시 Sentry alert + 운영팀 즉시 조사.
    """


# =============================================================================
# PITEnforcer service
# =============================================================================

# Generic Record TypeVar — PIT 알고리즘이 임의의 PITRecord 호환 객체에 작동.
_R = TypeVar("_R", bound=PITRecord)
_S = TypeVar("_S", bound=SupersedableRecord)
# latest_active_by_key 의 key 가 반환하는 group identity — hashable + stable.
_K = TypeVar("_K", bound=Hashable)


class PITEnforcer:
    """Record-level PIT 정책 enforcement.

    State-less — 모든 method 가 입력 list + as_of 를 받아 결과 반환. 인스턴스화
    하는 이유는 (1) `max_chain_depth` 같은 instance config 보존, (2) DI 패턴으로
    테스트 격리.

    Attributes:
        max_chain_depth: supersede chain 의 hard limit. 초과 시
            `PITDataCorruptionError`.
        pit_policy_version: 본 인스턴스가 따르는 정책 버전. Screen Run snapshot
            freeze key.
    """

    def __init__(self, *, max_chain_depth: int = _DEFAULT_MAX_CHAIN_DEPTH) -> None:
        if max_chain_depth < 1:
            raise ValueError(f"max_chain_depth must be >= 1, got {max_chain_depth}")
        self.max_chain_depth: Final[int] = max_chain_depth
        self.pit_policy_version: Final[str] = PIT_POLICY_VERSION

    # ---------------------------------------------------------------------
    # 묵시적 필터 — default path
    # ---------------------------------------------------------------------

    def filter_records_at_or_before(
        self,
        records: Sequence[_R],
        as_of: date,
    ) -> list[_R]:
        """`effective_date <= as_of` 인 record 만 반환 (입력 순서 보존).

        가장 빈번한 호출 — Repository 가 record list 를 받아 호출자에게 넘기기
        전에 본 메서드로 한 번 거른다. 결과 list 는 lookahead-safe.
        """
        return [r for r in records if r.effective_date <= as_of]

    # ---------------------------------------------------------------------
    # Assert layer — 묵시적 필터 검증의 마지막 보루
    # ---------------------------------------------------------------------

    def assert_no_lookahead(
        self,
        records: Sequence[_R],
        as_of: date,
    ) -> None:
        """`records` 의 모든 record 가 `effective_date <= as_of` 인지 검사.

        Raises:
            LookAheadError: 위반하는 첫 record (등장 순서) 에서 즉시 raise.
        """
        for r in records:
            if r.effective_date > as_of:
                raise LookAheadError(r.id, r.effective_date, as_of)

    # ---------------------------------------------------------------------
    # 정정공시 chain 해소 — 핵심 알고리즘
    # ---------------------------------------------------------------------

    def latest_active_record(
        self,
        records: Sequence[_S],
        as_of: date,
        *,
        date_of: Callable[[_S], date] | None = None,
        context: str = "",
    ) -> _S:
        """`as_of` 시점에 active 였던 record 중 가장 최신.

        알고리즘 (oracle 자문 §3 정확 해소 + 2 차 리뷰 C1/C5 반영):

        0. **Chain integrity 사전 검증** — 모든 record 의 supersede chain 을 끝까지
           walk 하여 cycle / depth 초과 detect. corruption 있으면 즉시 raise.
        1. candidates = `date_of(r) <= as_of` 인 record (필터)
        2. 각 candidate r 의 active 판정 — successor (1-hop) 의 date 가 as_of 이후이면
           r 은 그 시점에 still active. successor 없으면 보수적으로 active.
        3. active 중 max((date_of, created_at, id)) 반환 (id 는 final tiebreaker —
           oracle 자문 C6).

        Args:
            records: 같은 도메인 key (e.g., (code, account)) 의 record list.
            as_of: PIT 기준 일자.
            date_of: PIT 기준 컬럼 추출자. None 이면 `r.effective_date` (default).
                CorporateAction 의 announce 기준 PIT 등 도메인별 의미론 지원
                (oracle 2 차 리뷰 C5).
            context: 에러 메시지의 디버깅 컨텍스트.

        Raises:
            NoActiveRecordError: as_of 시점에 active 가 없음.
            PITDataCorruptionError: chain cycle / depth 초과.
        """
        date_extractor: Callable[[_S], date] = (
            date_of if date_of is not None else (lambda r: r.effective_date)
        )

        record_by_id: dict[UUID, _S] = {r.id: r for r in records}

        # 1. Chain integrity 검증 — cycle / depth 를 전체 chain 끝까지 walk 하여 detect.
        self._validate_chain_integrity(records, record_by_id, context=context)

        # 2. Candidates 필터.
        candidates = [r for r in records if date_extractor(r) <= as_of]
        if not candidates:
            raise NoActiveRecordError(as_of, context=context)

        # 3. Active 판정 — 1-hop successor 검사 (chain integrity 가 이미 검증됨).
        active: list[_S] = [
            r for r in candidates
            if self._is_active_one_hop(r, as_of, record_by_id, date_extractor)
        ]

        if not active:
            # candidates 모두 supersede — 정상 운영에선 head 1개 이상 active.
            # corruption 이거나 호출자가 candidates 를 pre-filter 하여 head 가 빠진
            # case (예: head 의 date 가 as_of 이후라 candidates 밖). 둘은 의미적으로
            # 구별 불가하므로 NoActiveRecord 로 통합 (oracle 자문 L4 — corruption
            # 와 정상 empty 의 경계 보강).
            raise NoActiveRecordError(
                as_of,
                context=f"{context} (all candidates superseded by future records)".strip(),
            )

        # 4. Tie-break: max(date_of, created_at, id) — id 가 final tiebreaker.
        return max(active, key=lambda r: (date_extractor(r), r.created_at, str(r.id)))

    # ---------------------------------------------------------------------
    # Internal — chain integrity (cycle / depth) 별도 검증
    # ---------------------------------------------------------------------

    def _validate_chain_integrity(
        self,
        records: Sequence[_S],
        record_by_id: dict[UUID, _S],
        *,
        context: str = "",
    ) -> None:
        """모든 supersede chain 의 cycle / depth 검사. corruption 시 raise.

        본 함수는 chain 의 active 판정과 무관 — 순수히 무결성 검사. `latest_active_record`
        가 1-hop active 판정 전에 호출. 결과 cache 없으나 candidate 수가 작으므로
        실용적 부담 X. M3 backlog 의 memoize 도입 시 함께.

        Raises:
            PITDataCorruptionError: cycle 또는 depth 초과 detect.
        """
        for r in records:
            if r.superseded_by is None:
                continue
            visited: set[UUID] = {r.id}
            current = r
            depth = 0
            while current.superseded_by is not None:
                depth += 1
                if depth > self.max_chain_depth:
                    raise PITDataCorruptionError(
                        f"supersede chain exceeded depth {self.max_chain_depth} "
                        f"starting from record {r.id}{(' ' + context) if context else ''}."
                    )
                successor_id = current.superseded_by
                if successor_id in visited:
                    raise PITDataCorruptionError(
                        f"supersede cycle detected involving record {successor_id} "
                        f"starting from {r.id}{(' ' + context) if context else ''}."
                    )
                visited.add(successor_id)
                successor = record_by_id.get(successor_id)
                if successor is None:
                    # chain 의 head 가 본 records 범위 밖 — 무결성 검사상 OK.
                    break
                current = successor

    def _is_active_one_hop(
        self,
        record: _S,
        as_of: date,
        record_by_id: dict[UUID, _S],
        date_of: Callable[[_S], date],
    ) -> bool:
        """`record` 가 `as_of` 시점에 active 였는지 — 1-hop 검사.

        Chain integrity 가 사전에 검증됐다는 전제 (`_validate_chain_integrity`).
        oracle 자문 §3 의 정확 해소 — 첫 successor 의 date 만 보면 충분:

        - record.superseded_by == None → 자체 active.
        - successor 가 records 밖 → 보수적으로 active.
        - date_of(successor) > as_of → record 는 그 시점에 still active.
        - date_of(successor) <= as_of → record 는 그 시점에 already superseded.
        """
        if record.superseded_by is None:
            return True
        successor = record_by_id.get(record.superseded_by)
        if successor is None:
            return True  # 보수적 — partial fetch chain head.
        return date_of(successor) > as_of

    # ---------------------------------------------------------------------
    # Multi-chain 처리 — record 단위 active 검사 (group key 불필요)
    # ---------------------------------------------------------------------

    def filter_active_records(
        self,
        records: Sequence[_S],
        as_of: date,
        *,
        date_of: Callable[[_S], date] | None = None,
    ) -> list[_S]:
        """`as_of` 시점에 active 였던 record 만 반환 (입력 순서 보존).

        `latest_active_by_key` 와의 차이 — group key 강요 없이 모든 record 에 대해
        record 단위 active 검사. 여러 별개 chain 이 records 안에 공존 가능한
        도메인 (예: corporate_action 의 split + dividend + buyback 동시) 에 적합.

        chain 정체성 자체가 그룹 — chain head 가 하나면 1 개, 여러 chain 이 별개로
        있으면 각 chain 의 active head 가 모두 결과에 포함.

        Args:
            records: 임의 도메인 record list (한 chain 또는 여러 chain).
            as_of: PIT 기준 일자.
            date_of: PIT 기준 컬럼 추출자. None 이면 `r.effective_date`.

        Returns:
            active record list — 입력 순서 보존.

        Raises:
            PITDataCorruptionError: chain integrity 위반 (cycle / depth).
        """
        date_extractor: Callable[[_S], date] = (
            date_of if date_of is not None else (lambda r: r.effective_date)
        )
        record_by_id: dict[UUID, _S] = {r.id: r for r in records}
        self._validate_chain_integrity(records, record_by_id)

        return [
            r for r in records
            if date_extractor(r) <= as_of
            and self._is_active_one_hop(r, as_of, record_by_id, date_extractor)
        ]

    # ---------------------------------------------------------------------
    # 정정공시-aware bulk 추출 — 여러 key (account) 의 latest active 추출
    # ---------------------------------------------------------------------

    def latest_active_by_key(
        self,
        records: Sequence[_S],
        as_of: date,
        *,
        key: Callable[[_S], _K],
        date_of: Callable[[_S], date] | None = None,
    ) -> dict[_K, _S]:
        """`key` 별로 latest_active_record 반환.

        Args:
            records: domain key 와 무관한 record list.
            as_of: PIT 기준.
            key: group identity 추출 callable. Hashable 반환 강제 (TypeVar bound).
                예: `key=lambda r: r.fiscal_period`.
            date_of: PIT 기준 컬럼 추출자 (default `r.effective_date`).

        Returns:
            key → 그 그룹의 latest active record. active 가 없는 key 는 silent skip.

        Note (성능 — oracle 자문 M4):
            현재 구현은 group 별로 `latest_active_record` 호출 — group 마다
            record_by_id 재구축. cross-group successor 가 없다는 invariant 활용한
            최적화는 M0 backlog.
        """
        groups: dict[_K, list[_S]] = {}
        for r in records:
            groups.setdefault(key(r), []).append(r)

        result: dict[_K, _S] = {}
        for k, group in groups.items():
            try:
                result[k] = self.latest_active_record(
                    group, as_of, date_of=date_of, context=f"key={k}"
                )
            except NoActiveRecordError:
                continue
        return result
