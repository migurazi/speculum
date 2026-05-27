"""StocksMaster Repository — lineage-aware PIT search/lookup.

ADR-0002 D5 / ADR-0009 D6 의 stocks_master entity. T13 SQLAlchemy 합류 후
`SqlStocksMasterRepository` 가 본 Protocol 의 reference.

설계 원칙 (oracle T25 자문 결정 8):

1. **`code_history` 시점 매칭** — `current_code` 만 매칭은 historical 검색 의도
   위배. `valid_from <= as_of < (valid_to or +inf)` 인 entry 를 보유한 lineage
   반환.
2. **as_of-aware search** — `listing_date <= as_of < (delisting_date or +inf)`
   인 종목만 default. survivorship bias 반전 (zombie inclusion) 회피 위해
   `include_delisted` opt-in.
3. **PIT-aware fetch_by_code** — lineage 존재 여부만. 미상장/폐지 status 분기는
   호출자 책임 (endpoint 의 StockStatus enum).

관련 ADR:
- ADR-0002 D5 (stocks_master schema)
- ADR-0009 D6 (code_history JSONB), D7 (survivorship 보존)
- ADR-0008 D9 (as_of 와 상폐 종목 상호작용)
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from datetime import date
from typing import Protocol, Sequence, runtime_checkable

from app.repositories.pit_protocols import StockMasterRecord

__all__ = [
    "FakeStocksMasterRepository",
    "StocksMasterRepository",
]


@runtime_checkable
class StocksMasterRepository(Protocol):
    """KRX 종목 마스터 — lineage-aware PIT search/lookup.

    모든 method 가 `as_of: date` keyword-only required — `pit_protocols.py` 일관.
    """

    def fetch_by_code(
        self, code: str, *, as_of: date,
    ) -> StockMasterRecord | None:
        """`code` 가 등장하는 lineage 의 record 반환 — status 분기는 호출자.

        의미론 (oracle T25 결정 6):
            - lineage 의 `code_history` 어디든 매칭되는 `code` entry 가 있으면
              record 반환 (시점 무관, lineage 존재 의미).
            - lineage 가 없거나, lineage 가 있어도 `code` 가 그 history 에
              없으면 None.
            - status (active / not_yet_listed / delisted) 산출은 호출자 책임
              (`schemas/stocks.py` 의 `_compute_status` 가 listing/delisting_date
              와 as_of 비교).

        as_of 는 historical 한 시점 검색을 위한 hint — 미래 PIT-aware 시점
        매칭 강화 시점에 의미 확장.

        Args:
            code: KRX 종목코드 (6 자리 zero-padded 권장).
            as_of: PIT 기준 (현재 implementation 은 단순 hint).

        Returns:
            lineage record. 없으면 None.
        """
        ...

    def search(
        self,
        query: str,
        *,
        as_of: date,
        limit: int = 50,
        include_delisted: bool = False,
    ) -> Sequence[StockMasterRecord]:
        """이름·코드 substring 검색 — as_of 시점 active 만 default.

        Args:
            query: 검색어. 자동 정규화 (NFKC + trim). 숫자만이면 코드 검색, 아니면
                이름 검색 (입력 자동 감지). 빈 문자열은 ValueError.
            as_of: PIT 기준 — listing_date <= as_of 인 종목만.
            limit: 결과 개수 상한.
            include_delisted: True 면 delisting_date <= as_of 인 종목도 포함
                (survivorship 분석 등).

        Returns:
            검색 결과 — prefix match 우선, 그 다음 current_name asc.
        """
        ...

    def list_active(self, *, as_of: date) -> Sequence[StockMasterRecord]:
        """as_of 시점에 active 인 모든 종목 — Screener 의 universe.

        `listing_date <= as_of AND (delisting_date is None OR delisting_date > as_of)`
        조건의 lineage. 폐지 종목 제외 (survivorship bias 회피 — Screener universe
        의 default 정의).

        T18 합류 후 KRX universe 의 진짜 active set. 본 method 는 Screener
        (T26 의 POST /api/screen, /api/runs) 와 universe-level 분석 (M1 의 시장
        wide query) 의 backbone.
        """
        ...


class FakeStocksMasterRepository(StocksMasterRepository):
    """In-memory KRX 마스터 — T13 SQLAlchemy 구현체의 contract reference.

    fixture 5~10 종목으로 lineage / code_history / 폐지 시나리오 cover.
    """

    def __init__(self, records: Sequence[StockMasterRecord]) -> None:
        self._records: tuple[StockMasterRecord, ...] = tuple(records)
        # current_code 와 code_history 의 모든 code 를 lineage_id 로 인덱싱.
        # 동일 code 가 다른 시점에 다른 lineage 일 가능성 (드물지만 가능) →
        # multi-map.
        self._by_code: dict[str, list[StockMasterRecord]] = defaultdict(list)
        for r in records:
            for entry in r.code_history:
                self._by_code[entry.code].append(r)

    def fetch_by_code(
        self, code: str, *, as_of: date,
    ) -> StockMasterRecord | None:
        # lineage 전체 검색 — `code` 가 어느 시점에라도 등장하면 lineage 반환.
        # status (active / not_yet_listed / delisted) 산출은 호출자 책임
        # (oracle 결정 6).
        candidates = self._by_code.get(code, [])
        return candidates[0] if candidates else None

    def search(
        self,
        query: str,
        *,
        as_of: date,
        limit: int = 50,
        include_delisted: bool = False,
    ) -> Sequence[StockMasterRecord]:
        normalized = unicodedata.normalize("NFKC", query).strip()
        if not normalized:
            raise ValueError("search query must be non-empty")
        if limit < 0:
            raise ValueError(f"limit must be >= 0, got {limit}")

        # 입력 자동 감지: 모두 숫자면 코드 검색, 아니면 이름.
        is_numeric_query = normalized.isdigit()

        results: list[tuple[bool, str, StockMasterRecord]] = []
        seen: set[str] = set()  # lineage id dedup (다중 code_history entry).

        for r in self._records:
            if str(r.id) in seen:
                continue
            # 1. as_of 시점 listing 여부 검사.
            if r.listing_date > as_of:
                continue  # 미상장
            if (not include_delisted
                    and r.delisting_date is not None
                    and r.delisting_date <= as_of):
                continue  # 폐지 종목 제외

            # 2. 매칭 검사.
            matched = False
            is_prefix = False
            if is_numeric_query:
                # Numeric query — code substring + prefix 비교는 zero-pad 후 (oracle 2 차 C2).
                # 예: `"5930"` 검색 → `"005930"` 와 padded("005930") prefix 비교.
                padded_query = normalized.zfill(6) if len(normalized) <= 6 else normalized
                for entry in r.code_history:
                    if normalized in entry.code:
                        matched = True
                        # 정확 매치 우선, 그 다음 padded prefix.
                        if entry.code == padded_query:
                            is_prefix = True
                        elif entry.code.startswith(padded_query):
                            is_prefix = True
                        break
            else:
                # name substring (NFKC 정규화 후).
                name_norm = unicodedata.normalize("NFKC", r.current_name)
                if normalized in name_norm:
                    matched = True
                    if name_norm.startswith(normalized):
                        is_prefix = True

            if matched:
                # 정렬 key: (prefix 우선 = is_prefix=False 가 뒤, 이름 asc).
                results.append((not is_prefix, r.current_name, r))
                seen.add(str(r.id))

        results.sort()
        return tuple(r for _, _, r in results[:limit])

    def list_active(self, *, as_of: date) -> Sequence[StockMasterRecord]:
        """as_of 시점 active lineage — listing_date <= as_of < (delisting_date or inf).

        Sort by current_code (정렬 안정성) — 폐지 종목 (current_code=None) 은
        active 정의상 제외되므로 None 미발생.
        """
        active: list[StockMasterRecord] = []
        for r in self._records:
            if r.listing_date > as_of:
                continue
            if r.delisting_date is not None and r.delisting_date <= as_of:
                continue
            active.append(r)
        # 결정적 정렬 — current_code asc (None 은 active 정의상 미존재).
        return tuple(sorted(active, key=lambda r: r.current_code or ""))
