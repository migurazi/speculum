"""Pack 간 factor identity 충돌 탐지·해소 — ADR-0022 D8 (M2 T75 R3).

`factor_pack.validate_identity` 는 **단일 pack 내부** 의 canonical_id/uuid 중복만
검사한다. community/custom pack 을 수동 JSON 으로 import 할 때는 **pack 경계를
가로지르는** 충돌 — 같은 `canonical_id` 가 빌트인(또는 기존 import)과 다른 정의를
주장 — 이 발생한다(R3). 본 모듈은 그 충돌을 **silent override 없이**(Norma §2.3)
탐지·해소한다. No Advice 가 아니라 **identity 무결성** 원칙이다.

ADR-0022 D8 의 3축(D8.1):
- `canonical_id` = 이름(충돌 후보)
- per-factor content hash(uuid 제외) = 정의(다르면 진짜 충돌)
- `uuid` = 개체 식별자(재사용 = 위반)

핵심 불변식:
- canonical tier(빌트인) 불가침 — replace 금지, skip/rename 만(D8.2).
- 미해결 충돌 = import 거부(fail-loud, D8.4) — "미해결=진행" 이 아니라 "미해결=거부".

custom pack 미영속(stateless) — 현재 비교 대상(existing index)은 빌트인
`DEFAULT_PACK` 뿐. `build_existing_index` 는 장래 영속 import 합류를 위해 pack
목록을 받도록 일반화돼 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

from app.services._jcs import compute_content_hash
from app.services.factor_pack import DEFAULT_PACK, FactorPackError

# =============================================================================
# Tier 판정 — pack_slug 인코딩 (ADR-0002 D2 / schema pack_slug pattern)
# =============================================================================
#
# tier 는 별도 필드가 아니라 pack_slug prefix 로 인코딩된다
# (shared/schemas/factor-pack-v1.json):
#   "speculum-builtin"      → canonical
#   "community/{...}"        → community
#   "user/{...}"            → custom

TierName = Literal["canonical", "community", "custom"]

_BUILTIN_SLUG: Final[str] = "speculum-builtin"

# per-factor 정의 hash 에서 제외할 키 — uuid 는 "개체 식별자"라 정의 동일성과
# 무관(D8.1). 포함 시 동일 정의를 다른 사용자가 각자 만들면 거짓 충돌 폭증.
_FACTOR_HASH_EXCLUDE: Final[str] = "uuid"


def derive_tier(pack_slug: str) -> TierName:
    """pack_slug → identity tier (ADR-0022 D8.1 / ADR-0002 D2).

    canonical(빌트인) 은 불가침 tier(D8.2). community/custom 은 사용자 정의 tier.
    """
    if pack_slug == _BUILTIN_SLUG:
        return "canonical"
    if pack_slug.startswith("community/"):
        return "community"
    if pack_slug.startswith("user/"):
        return "custom"
    # schema pattern 이 위 셋 중 하나를 강제하나, 방어적으로 custom 취급
    # (가장 권한 낮은 tier — 불가침성 부여 안 함).
    return "custom"


def compute_factor_hash(factor: dict[str, Any]) -> str:
    """단일 factor 의 정의 hash — uuid 제외 JCS+SHA-256 (ADR-0022 D8.1).

    같은 hash = 동일 정의(idempotent re-import 통과). 다른 hash + 같은
    canonical_id = 진짜 충돌. uuid 를 제외해 "정의" 와 "개체 식별자" 를 분리.
    """
    return compute_content_hash(factor, exclude_key=_FACTOR_HASH_EXCLUDE)


# =============================================================================
# Errors (ADR-0022 D8.2/D8.4 — FactorPackError 계열)
# =============================================================================

class CrossPackIdentityConflict(FactorPackError):
    """import pack 의 canonical_id 가 기존 정의와 충돌하나 resolution 미제공 — D8.4."""


class CanonicalOverrideForbidden(FactorPackError):
    """빌트인 canonical 정의를 import 로 덮으려 시도(replace) — D8.2 불가침 위반."""


class UuidReuseViolation(FactorPackError):
    """import factor 의 uuid 가 다른 canonical_id 로 기존에 쓰임 — D8.1 개체 ID 위반."""


class InvalidResolution(FactorPackError):
    """resolution 형식/대상이 부정합(미지 action, rename 시 new_canonical_id 누락 등)."""


# =============================================================================
# Index / conflict 자료형
# =============================================================================

@dataclass(frozen=True, slots=True)
class ExistingFactorRef:
    """기존(빌트인/영속 import) factor 의 identity 참조."""

    content_hash: str
    tier: TierName
    uuid: str
    pack_slug: str


@dataclass(frozen=True, slots=True)
class ExistingIndex:
    """canonical_id / uuid 양방향 인덱스 — 충돌 탐지의 비교 기준."""

    by_canonical_id: dict[str, ExistingFactorRef] = field(default_factory=dict)
    # uuid → 그 uuid 를 점유한 canonical_id (개체 ID 재사용 탐지용).
    canonical_id_by_uuid: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FactorConflict:
    """pack 간 정의 충돌 1 건 — import-check 응답 단위."""

    canonical_id: str
    incoming_hash: str
    existing_hash: str
    existing_tier: TierName
    # 허용 resolution — canonical tier 는 ["skip","rename"](replace 불가, D8.2).
    allowed_resolutions: tuple[str, ...]


# resolution action 집합.
_VALID_ACTIONS: Final[frozenset[str]] = frozenset({"skip", "rename", "replace"})


# =============================================================================
# Index 구성
# =============================================================================

def build_existing_index(pack_bodies: list[dict[str, Any]]) -> ExistingIndex:
    """기존 pack 목록 → ExistingIndex (충돌 비교 기준).

    같은 canonical_id 가 여러 pack 에 등장하면 **canonical tier 우선**(빌트인이
    이긴다). 같은 tier 내 중복은 각 pack 의 validate_identity 가 이미 거부했으므로
    여기서는 tier 우선순위만 적용. 현재 호출은 [DEFAULT_PACK.body](빌트인만).
    """
    by_cid: dict[str, ExistingFactorRef] = {}
    cid_by_uuid: dict[str, str] = {}
    # canonical tier 가 항상 이기도록 tier 우선순위로 정렬 후 채운다.
    _tier_rank = {"canonical": 0, "community": 1, "custom": 2}
    ordered = sorted(
        pack_bodies, key=lambda b: _tier_rank.get(derive_tier(b["pack_slug"]), 2),
    )
    for body in ordered:
        tier = derive_tier(body["pack_slug"])
        for f in body["factors"]:
            cid = f["canonical_id"]
            ref = ExistingFactorRef(
                content_hash=compute_factor_hash(f),
                tier=tier,
                uuid=f["uuid"],
                pack_slug=body["pack_slug"],
            )
            # 더 높은 우선 tier 가 이미 점유했으면 유지(canonical 불가침).
            if cid not in by_cid:
                by_cid[cid] = ref
            # uuid 점유 — 먼저 등록한(높은 tier) canonical_id 우선.
            if f["uuid"] not in cid_by_uuid:
                cid_by_uuid[f["uuid"]] = cid
    return ExistingIndex(by_canonical_id=by_cid, canonical_id_by_uuid=cid_by_uuid)


def builtin_existing_index() -> ExistingIndex:
    """현재 active 빌트인(DEFAULT_PACK) 기준 인덱스 — stateless import 의 기본 비교 대상."""
    return build_existing_index([DEFAULT_PACK.body])


# =============================================================================
# 충돌 탐지 (D8.1)
# =============================================================================

def assert_no_uuid_reuse(
    incoming_body: dict[str, Any], existing: ExistingIndex,
) -> None:
    """import factor 의 uuid 가 다른 canonical_id 로 기존에 쓰였으면 거부 — D8.1.

    uuid 는 불변 개체 식별자다. 같은 uuid 가 다른 canonical_id 를 주장하면 개체
    동일성이 깨진 것 — resolution 으로 해결 불가하므로 즉시 fail-loud.
    """
    for f in incoming_body["factors"]:
        f_uuid = f["uuid"]
        existing_cid = existing.canonical_id_by_uuid.get(f_uuid)
        if existing_cid is not None and existing_cid != f["canonical_id"]:
            raise UuidReuseViolation(
                f"uuid {f_uuid} 가 기존 factor '{existing_cid}' 에 이미 점유됨 — "
                f"import factor '{f['canonical_id']}' 가 같은 uuid 를 다른 이름으로 "
                f"재사용(ADR-0022 D8.1 개체 식별자 위반). uuid 를 새로 발급하세요."
            )


def detect_cross_pack_conflicts(
    incoming_body: dict[str, Any], existing: ExistingIndex,
) -> list[FactorConflict]:
    """import pack 의 정의 충돌(같은 canonical_id, 다른 hash) 목록 — D8.1.

    같은 canonical_id + 같은 hash = 동일 정의(idempotent, 충돌 아님). 같은
    canonical_id + 다른 hash = 진짜 충돌. canonical tier 충돌은 replace 불가
    (allowed_resolutions=["skip","rename"], D8.2).

    uuid 재사용은 본 함수가 아니라 `assert_no_uuid_reuse` 소관(충돌이 아닌 거부).
    호출자는 import-check 시 assert_no_uuid_reuse 를 먼저 호출해야 한다.
    """
    conflicts: list[FactorConflict] = []
    for f in incoming_body["factors"]:
        cid = f["canonical_id"]
        ref = existing.by_canonical_id.get(cid)
        if ref is None:
            continue  # 새 이름 — 충돌 없음.
        incoming_hash = compute_factor_hash(f)
        if incoming_hash == ref.content_hash:
            continue  # 동일 정의 — idempotent re-import.
        # 진짜 충돌: 같은 이름, 다른 정의.
        allowed: tuple[str, ...] = (
            ("skip", "rename") if ref.tier == "canonical"
            else ("skip", "rename", "replace")
        )
        conflicts.append(FactorConflict(
            canonical_id=cid,
            incoming_hash=incoming_hash,
            existing_hash=ref.content_hash,
            existing_tier=ref.tier,
            allowed_resolutions=allowed,
        ))
    return conflicts


# =============================================================================
# Resolution 적용 (D8.2/D8.3/D8.4)
# =============================================================================

def _validate_resolution_shape(
    canonical_id: str, resolution: dict[str, Any],
) -> tuple[str, str | None]:
    """resolution dict → (action, new_canonical_id) 정규화 + 형식 검증."""
    action = resolution.get("action")
    if action not in _VALID_ACTIONS:
        raise InvalidResolution(
            f"factor '{canonical_id}' 의 resolution action '{action}' 부정 — "
            f"허용: {sorted(_VALID_ACTIONS)}."
        )
    new_cid = resolution.get("new_canonical_id")
    if action == "rename":
        if not isinstance(new_cid, str) or not new_cid:
            raise InvalidResolution(
                f"factor '{canonical_id}' 의 rename resolution 에 "
                f"new_canonical_id 가 필요합니다."
            )
        if new_cid == canonical_id:
            raise InvalidResolution(
                f"rename 의 new_canonical_id 가 원본 '{canonical_id}' 과 동일합니다."
            )
    return action, (new_cid if isinstance(new_cid, str) else None)


def assert_no_unresolved_conflicts(
    conflicts: list[FactorConflict],
    resolutions: dict[str, dict[str, Any]],
) -> None:
    """모든 충돌에 resolution 이 있고 canonical tier 에 replace 가 없음을 강제 — D8.4/D8.2.

    이 불변식이 silent override 를 구조적으로 차단한다: 미해결 충돌이 하나라도
    있으면 import 전체 거부(fail-loud). canonical tier 에 replace 시도는 별도 거부.
    """
    unresolved = [c.canonical_id for c in conflicts if c.canonical_id not in resolutions]
    if unresolved:
        raise CrossPackIdentityConflict(
            f"미해결 identity 충돌 {sorted(unresolved)} — 각 충돌에 resolution"
            f"(skip/rename/replace)을 명시해야 import 가능합니다(ADR-0022 D8.4 "
            f"silent override 금지)."
        )
    for c in conflicts:
        action, _ = _validate_resolution_shape(c.canonical_id, resolutions[c.canonical_id])
        if action == "replace" and c.existing_tier == "canonical":
            raise CanonicalOverrideForbidden(
                f"빌트인 canonical factor '{c.canonical_id}' 는 import 로 덮을 수 "
                f"없습니다(ADR-0022 D8.2 불가침). skip 또는 rename 만 가능합니다."
            )
        if action not in c.allowed_resolutions:
            raise InvalidResolution(
                f"factor '{c.canonical_id}' 에 '{action}' 불가 — "
                f"허용: {list(c.allowed_resolutions)}."
            )


def apply_resolutions(
    incoming_body: dict[str, Any],
    conflicts: list[FactorConflict],
    resolutions: dict[str, dict[str, Any]],
    existing: ExistingIndex,
) -> dict[str, Any]:
    """충돌 resolution 을 적용한 새 pack body 반환 — D8.3.

    - skip: 해당 factor 를 import 에서 제외.
    - rename: canonical_id 를 new_canonical_id 로 치환(새 이름이 또 충돌하면 거부).
    - replace: factor 유지(community/custom override 의도 — canonical 은 D8.4 에서 차단).
    - 충돌 없는 factor: 유지.

    호출 전 `assert_no_unresolved_conflicts` 통과를 전제(미해결/금지 조합은 거기서 거부).
    반환 body 는 새 dict(원본 불변) — content_hash 는 호출자가 재계산.
    """
    conflict_cids = {c.canonical_id for c in conflicts}
    out_factors: list[dict[str, Any]] = []
    seen_cids: set[str] = set()
    for f in incoming_body["factors"]:
        cid = f["canonical_id"]
        if cid not in conflict_cids:
            out_factors.append(f)
            seen_cids.add(cid)
            continue
        action, new_cid = _validate_resolution_shape(cid, resolutions[cid])
        if action == "skip":
            continue  # import 제외.
        if action == "rename":
            assert new_cid is not None  # _validate 가 보장.
            # 새 이름이 기존 또는 이미 채택된 import 이름과 또 충돌하면 거부.
            if new_cid in existing.by_canonical_id or new_cid in seen_cids:
                raise InvalidResolution(
                    f"rename 대상 '{new_cid}' 가 또 다른 기존/import factor 와 "
                    f"충돌합니다 — 다른 이름을 선택하세요."
                )
            renamed = {**f, "canonical_id": new_cid}
            out_factors.append(renamed)
            seen_cids.add(new_cid)
            continue
        # replace — factor 유지(override). canonical 충돌은 assert 단계에서 차단됨.
        out_factors.append(f)
        seen_cids.add(cid)

    new_body = {**incoming_body, "factors": out_factors}
    # 정의가 바뀌었을 수 있으므로(skip/rename) content_hash 는 stale — 제거하여
    # 호출자가 재봉인하게 한다(잘못된 hash 가 남아 무결성 오판되는 것 차단).
    new_body.pop("content_hash", None)
    return new_body
