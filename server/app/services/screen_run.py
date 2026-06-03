"""Screen Run snapshot 모델 — ADR-0008 D7 implementation.

8 기둥 §2.10 Reproducibility 의 정점. 사용자가 "Save Run" 버튼으로 한 시점의 query
+ 결과 + 모든 정책 hash 를 freeze 하여, 6 개월 후 같은 결과 재현 가능하도록.

설계 원칙 (oracle 자문 P0+P1 반영):

1. **result_hash 입력 확장 (P0 / 결정 2)** — ADR-0008 D7 의 "SHA-256 of (query,
   as_of, result_codes)" 를 `data_versions` 포함으로 확장. data_versions 누락 시
   factor_pack v1.0 → v1.0.1 변경이 같은 hash 로 보임 → §2.10 위반.
2. **Canonical 정렬 (P1 / 결정 5)** — `ScreenRunQuery.conditions` 와
   `selected_factors` 모두 canonical 정렬. 사용자 입력 순서는 `presentation_order`
   필드로 분리 — hash 입력에서 제외.
3. **result_codes 정규화 (Risk-C3)** — 6 자리 zero-pad + 사전식 정렬 + dedup. KRX
   종목코드 invariant.
4. **diff_versions method (Risk-C4)** — M1 prerequisite. 현재 정책 set 과 snapshot
   의 data_versions 비교, 변경된 키만 반환.
5. **SYSTEM_USER_ID constant (결정 7)** — M0 single-user. T31 NextAuth 합류 후
   실제 user_id 사용.
6. **frozen dataclass + immutable view** — Screen Run 의 freeze 약속.

관련 ADR / 문서:
- ADR-0008 D7 (`screen_runs` schema) — 본 모듈이 implement
- ADR-0002 D5 (DB schema), D4 (Pack 버저닝)
- 8 기둥 §2.10 Reproducibility, §2.4 PIT
- M0_PLAN T30 / AC-P-10 / AC-F-08

Out-of-scope (후속):
- SQLAlchemy 구현 — T13 후
- API endpoint (`/api/runs`) — T40 (Save Run 버튼)
- KRX/DART batch_id 추가 — M1 데이터 정정 추적
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID

from app.services._jcs import compute_content_hash
from app.services.snapshot_versions import collect_active_policy_versions

__all__ = [
    "SYSTEM_USER_ID",
    "ScreenRunBuilder",
    "ScreenRunQuery",
    "ScreenRunSnapshot",
    "normalize_stock_codes",
]


# =============================================================================
# M0 single-user — T31 NextAuth 합류 후 실제 jwt sub 클레임으로 교체
# =============================================================================

# `SYSTEM_USER_ID` 의 가정: M0 = 모든 Screen Run 이 단일 시스템 사용자에 귀속.
# 환경변수 `SPECULUM_USER_ID` 로 override 가능 (테스트 / staging). T31 합류 시
# nullable migration 회피 위해 placeholder UUID 가 아닌 fixed value 사용.
_DEFAULT_SYSTEM_USER_UUID: Final[UUID] = UUID("00000000-0000-0000-0000-000000000001")


def _resolve_system_user_id() -> UUID:
    """환경변수 override 가 있으면 그 값, 없으면 default fixed UUID."""
    override = os.environ.get("SPECULUM_USER_ID", "").strip()
    if override:
        try:
            return UUID(override)
        except ValueError as exc:
            # silent fallback X — type 에러는 명시적 fail. raise ... from exc
            # 으로 원본 ValueError chain 유지 (B904).
            raise ValueError(
                f"SPECULUM_USER_ID 환경변수 값 '{override}' 가 valid UUID 아님"
            ) from exc
    return _DEFAULT_SYSTEM_USER_UUID


SYSTEM_USER_ID: Final[UUID] = _resolve_system_user_id()
"""Import-time evaluated default — 호환성 유지용. 운영 코드의 default 는
`ScreenRunBuilder.build` 가 매 호출마다 `_resolve_system_user_id()` 동적 resolve
하여 env 변경이 즉시 반영됨 (oracle 2 차 리뷰 C3)."""


# =============================================================================
# KRX 종목코드 정규화 (Risk-C3) — Screen Run 의 invariant
# =============================================================================

def normalize_stock_codes(codes: Sequence[str]) -> tuple[str, ...]:
    """KRX 종목코드 list 를 정규화: 6 자리 zero-pad + 정렬 + dedup.

    Args:
        codes: 임의 순서/형식의 KRX 종목코드 (예: "5930", "005930", "  5930  ").

    Returns:
        사전식 ascending + dedup + 6 자리 zero-pad tuple. Screen Run 의 result_hash
        입력 invariant.

    Raises:
        ValueError: 6 자리 미만 / 비숫자 입력 (KRX 코드 형식 위반).
    """
    normalized: set[str] = set()
    for raw in codes:
        cleaned = raw.strip()
        if not cleaned.isdigit():
            raise ValueError(f"KRX code must be digits: {raw!r}")
        if len(cleaned) > 6:
            raise ValueError(f"KRX code exceeds 6 digits: {raw!r}")
        padded = cleaned.zfill(6)
        normalized.add(padded)
    return tuple(sorted(normalized))


# =============================================================================
# Query — Screener 조건 + 선택 factor
# =============================================================================

@dataclass(frozen=True, slots=True)
class ScreenRunQuery:
    """Screen Run 의 query 부분 — JSONB 직렬화 + canonical hash 입력.

    `conditions` 와 `selected_factors` 모두 canonical 정렬 (사용자 입력 순서 ≠ hash
    입력 순서). 사용자 입력 순서는 `presentation_order` 로 분리 — hash 입력 제외
    (oracle 결정 5).

    Attributes:
        conditions: Screener 조건 list — 정렬됨. 각 dict 는 `{factor: str,
            op: str, value: str}` 형태. value 는 모두 str 로 정규화 (Decimal/
            numeric 직렬화 모호성 회피 — oracle Risk-C2).
        selected_factors: 결과에 표시할 factor canonical_id list — 정렬됨.
        security_types: universe 사전 필터 자산군 (ADR-0023 D7) — canonical 정렬
            + dedup. default `("common",)` (보통주만 — universe 확장이 기본 동작
            불변). 자산군 선택이 query 일부이므로 **result_hash 입력에 포함** →
            다른 자산군 선택은 다른 hash, 같은 선택은 byte 동일 (D6/D7 재현, 추가
            freeze 키 불요).
        presentation_order: 사용자 입력 순서 보존 — UI 표시용. hash 입력 제외.
            None 이면 default (정렬 순서).
    """

    conditions: tuple[Mapping[str, str], ...]
    selected_factors: tuple[str, ...]
    security_types: tuple[str, ...] = ("common",)
    presentation_order: tuple[int, ...] | None = None

    def to_canonical_dict(self) -> Mapping[str, Any]:
        """hash 입력용 canonical dict — presentation_order 미포함.

        security_types 포함 (ADR-0023 D7) — 자산군 선택이 result_hash 입력 일부.
        """
        return {
            "conditions": [dict(c) for c in self.conditions],
            "selected_factors": list(self.selected_factors),
            "security_types": list(self.security_types),
        }


def _canonicalize_query(
    *,
    conditions: Sequence[Mapping[str, str]],
    selected_factors: Sequence[str],
    security_types: Sequence[str] | None = None,
) -> ScreenRunQuery:
    """입력 query 를 canonical 형태로 정규화 — sort + dedup + str 강제.

    Conditions 의 정렬 key: `(factor, op, value)` 의 lexicographic 순서. 동일
    factor 의 두 조건 (예: PER<10 AND PER>5) 도 허용 — `(op, value)` 로 구별.

    **Dedup (oracle 2 차 C1)** — 동일 keys/values 의 conditions 는 중복 제거.
    `[{PER,<,10}, {PER,<,10}]` 의 hash 가 단일 조건과 같아야 §2.10 일관.
    Dedup 후 sort key 가 unique 해져 presentation_order 도 결정적 (M3 동시 해결).

    Selected factors 정렬: 사전식 ascending + dedup.

    security_types (ADR-0023 D7): None (미지정) 이면 default `("common",)` — 기본
    동작 보통주 (universe 확장이 기존 run 영향 0). 명시 시 사전식 ascending +
    dedup → result_hash 입력 (자산군 선택이 재현 일부). 빈 list 도 default
    ("common",) 로 해소 (모집단 0 의도 모호 방지 — schema validator 가 빈 list
    를 422 로 차단하나 도메인 builder 직접 호출 경로의 방어).

    Hash 입력의 결정성 보장.
    """
    # 1. 입력 정규화 — value 를 모두 str 로 (Risk-C2).
    normalized_with_orig: list[tuple[int, frozenset[tuple[str, str]]]] = []
    seen: set[frozenset[tuple[str, str]]] = set()
    for i, c in enumerate(conditions):
        normalized = frozenset((k, str(v)) for k, v in c.items())
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_with_orig.append((i, normalized))

    # 2. Canonical 정렬 — frozenset 의 sorted tuple 로 lexicographic.
    normalized_with_orig.sort(key=lambda x: tuple(sorted(x[1])))

    # 3. ScreenRunQuery 의 conditions = MappingProxyType tuple. presentation_order
    #    = canonical 순서 → 원본 인덱스 (중복 제거된 첫 입장 인덱스).
    sorted_conditions = tuple(
        MappingProxyType(dict(items)) for _, items in normalized_with_orig
    )
    presentation_order = tuple(orig_i for orig_i, _ in normalized_with_orig)

    sorted_factors = tuple(sorted(set(selected_factors)))

    # security_types canonical — 미지정/빈 list 면 default ("common",) (ADR-0023
    # D7: 기본 동작 보통주). 명시 시 사전식 ascending + dedup → result_hash 입력.
    if not security_types:
        sorted_security_types: tuple[str, ...] = ("common",)
    else:
        sorted_security_types = tuple(sorted(set(security_types)))

    return ScreenRunQuery(
        conditions=sorted_conditions,
        selected_factors=sorted_factors,
        security_types=sorted_security_types,
        presentation_order=presentation_order,
    )


# =============================================================================
# Snapshot — 완전 freeze 단위
# =============================================================================

@dataclass(frozen=True, slots=True)
class ScreenRunSnapshot:
    """Screen Run 의 완전한 freeze 단위 — DB row 1:1 대응.

    Attributes:
        id: Run 고유 UUID.
        user_id: M0 = SYSTEM_USER_ID. T31 합류 후 실제 사용자.
        query: canonical 정규화된 query.
        as_of: PIT 기준 일자 (AsOfPolicy 정규화 후).
        result_codes: KRX 종목코드 tuple — `normalize_stock_codes` 통과 (6 자리,
            정렬, dedup).
        result_hash: `sha256:<hex>` — JCS(query.to_canonical_dict, as_of,
            result_codes, data_versions) 의 SHA-256 (oracle 결정 2 확장).
        data_versions: `snapshot_versions.collect_active_policy_versions()` 결과
            의 freeze copy. 6 개월 후 재현의 기준.
        computed_at: 계산 완료 시각 (UTC). 결과 hash 입력에는 제외 — 같은 query
            를 두 번 실행해도 같은 hash 가 되어야 § 2.10 충족.
    """

    id: UUID
    user_id: UUID
    query: ScreenRunQuery
    as_of: date
    result_codes: tuple[str, ...]
    result_hash: str
    data_versions: Mapping[str, str]
    computed_at: datetime

    def diff_versions(self, current: Mapping[str, str]) -> Mapping[str, tuple[str, str]]:
        """본 snapshot 의 data_versions 와 `current` 비교 — 변경된 키만 (old, new).

        M1 의 "Run freshness banner" UI 가 본 method 직접 호출 (oracle Risk-C4).
        같은 Run 을 현재 정책으로 재현 시 어느 정책이 변했는지 사용자에게 표시.

        Returns:
            Mapping[key, (old, new)] — 변경 / 추가 / 제거 모두 cover.
            `(old, new)` 의 `old` 는 snapshot 시점, `new` 는 `current` 시점.
            제거된 키는 `(old, "")`, 추가된 키는 `("", new)`.
        """
        old_keys = frozenset(self.data_versions.keys())
        new_keys = frozenset(current.keys())
        diff: dict[str, tuple[str, str]] = {}
        for k in old_keys | new_keys:
            old_v = self.data_versions.get(k, "")
            new_v = current.get(k, "")
            if old_v != new_v:
                diff[k] = (old_v, new_v)
        return MappingProxyType(diff)


# =============================================================================
# Builder — canonical 정규화 + result_hash 계산
# =============================================================================

class ScreenRunBuilder:
    """Screen Run snapshot 생성의 단일 export 클래스.

    State-less — static method 만. Builder 패턴이지만 객체 누적 없이 한 번에 build.
    """

    @staticmethod
    def build(
        *,
        run_id: UUID,
        user_id: UUID | None = None,
        conditions: Sequence[Mapping[str, str]],
        selected_factors: Sequence[str],
        security_types: Sequence[str] | None = None,
        as_of: date,
        result_codes: Sequence[str],
        data_versions: Mapping[str, str] | None = None,
        computed_at: datetime | None = None,
    ) -> ScreenRunSnapshot:
        """입력을 정규화 + result_hash 계산 + 완전 freeze.

        Args:
            run_id: Run UUID.
            user_id: None 이면 `SYSTEM_USER_ID` default (M0 single-user).
            conditions / selected_factors: canonical 정렬 + dedup 적용.
            security_types: universe 자산군 사전 필터 (ADR-0023 D7). None/빈 list
                이면 default `("common",)` (보통주만 — 기본 동작 불변). canonical
                정렬 + dedup → result_hash 입력 (자산군 선택이 재현 일부).
            as_of: PIT 기준 — 호출자가 AsOfPolicy 통과 후 전달.
            result_codes: 임의 형식 — `normalize_stock_codes` 로 정규화.
            data_versions: None 이면 `collect_active_policy_versions()` 호출.
                테스트가 결정성 위해 명시 주입.
            computed_at: None 이면 현재 UTC.

        Returns:
            ScreenRunSnapshot — 완전 freeze.

        Raises:
            ValueError: result_codes 의 KRX 형식 위반 / data_versions 의 non-str.
        """
        # 1. User ID resolution — env override 를 매 호출마다 재평가 (oracle 2 차
        #    C3 — import-time singleton 의 테스트 격리 문제 회피).
        resolved_user = user_id if user_id is not None else _resolve_system_user_id()

        # 2. Query canonical 정규화 (security_types 포함 — ADR-0023 D7).
        query = _canonicalize_query(
            conditions=conditions,
            selected_factors=selected_factors,
            security_types=security_types,
        )

        # 3. Result codes 정규화 (6 자리 + 정렬 + dedup).
        normalized_codes = normalize_stock_codes(result_codes)

        # 4. data_versions — None 이면 현재 active, key/value 모두 str 검증
        #    (oracle 2 차 C2 — key 도 검증, JSON 직렬화 모호성 차단).
        if data_versions is None:
            data_versions = collect_active_policy_versions()
        for k, v in data_versions.items():
            if not isinstance(k, str):
                raise ValueError(
                    f"data_versions key must be str, got {type(k).__name__}: {k!r}"
                )
            if not isinstance(v, str):
                raise ValueError(
                    f"data_versions[{k!r}] must be str, got {type(v).__name__}: {v!r}"
                )
        frozen_versions = MappingProxyType(dict(data_versions))

        # 5. result_hash = JCS-SHA256(query, as_of, result_codes, data_versions).
        #    oracle 결정 2 의 확장 — ADR-0008 D7 의 정의를 본 사이클에서 보완.
        hash_input = {
            "query": query.to_canonical_dict(),
            "as_of": as_of.isoformat(),
            "result_codes": list(normalized_codes),
            "data_versions": dict(frozen_versions),
        }
        # exclude_key=None — result_hash 자체는 hash 입력에 없음 (자기참조 X).
        result_hash = compute_content_hash(hash_input, exclude_key=None)

        # 6. computed_at — default 현재 UTC.
        ts = computed_at if computed_at is not None else datetime.now(UTC)

        return ScreenRunSnapshot(
            id=run_id,
            user_id=resolved_user,
            query=query,
            as_of=as_of,
            result_codes=normalized_codes,
            result_hash=result_hash,
            data_versions=frozen_versions,
            computed_at=ts,
        )
