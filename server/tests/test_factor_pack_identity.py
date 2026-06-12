"""factor_pack_identity 단위 테스트 — ADR-0022 D8 pack 간 identity 충돌 (M2 T75 R3).

3축(D8.1) 검증:
- canonical_id = 이름(충돌 후보)
- per-factor content hash(uuid 제외) = 정의(다르면 진짜 충돌)
- uuid = 개체 식별자(재사용 위반)

핵심 불변식: canonical 불가침(D8.2), 미해결=거부(D8.4).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.factor_pack import DEFAULT_PACK
from app.services.factor_pack_identity import (
    CanonicalOverrideForbidden,
    CrossPackIdentityConflict,
    InvalidResolution,
    UuidReuseViolation,
    apply_resolutions,
    assert_no_unresolved_conflicts,
    assert_no_uuid_reuse,
    build_existing_index,
    builtin_existing_index,
    compute_factor_hash,
    derive_tier,
    detect_cross_pack_conflicts,
)

# 빌트인 factor 를 동적 기준으로 — 빌트인 pack 변경에 강건(특정 canonical_id 하드코딩 X).
_BUILTIN_FACTORS: list[dict[str, Any]] = DEFAULT_PACK.body["factors"]
_BUILTIN_CID: str = _BUILTIN_FACTORS[0]["canonical_id"]
_BUILTIN_UUID: str = _BUILTIN_FACTORS[0]["uuid"]


# =============================================================================
# Builders
# =============================================================================

def _factor(
    *, canonical_id: str, uuid_str: str, name_suffix: str = "",
) -> dict[str, Any]:
    """schema 무관 단위 테스트용 factor dict (서비스는 schema 검증 안 함)."""
    return {
        "canonical_id": canonical_id,
        "uuid": uuid_str,
        "name": f"factor {canonical_id}{name_suffix}",
        "description": f"{canonical_id} 설명",
        "formula": {"ast": {"field": "shares_issued"}, "inputs": ["shares_issued"]},
        "unit": "ratio",
    }


def _pack(pack_slug: str, factors: list[dict[str, Any]]) -> dict[str, Any]:
    return {"pack_slug": pack_slug, "version": "1.0.0", "factors": factors}


# =============================================================================
# D8.1 — derive_tier
# =============================================================================

def test_derive_tier_canonical() -> None:
    assert derive_tier("speculum-builtin") == "canonical"


def test_derive_tier_community() -> None:
    assert derive_tier("community/acme-value") == "community"


def test_derive_tier_custom() -> None:
    assert derive_tier("user/alice-mypack") == "custom"


def test_derive_tier_unknown_defaults_custom() -> None:
    """미지 slug 는 가장 권한 낮은 custom(불가침성 미부여)."""
    assert derive_tier("weird-slug") == "custom"


def test_derive_tier_v2_namespace_is_community() -> None:
    """ADR-0034 D3 — v2 namespace(`@{publisher}/{slug}`)는 community tier 재사용."""
    assert derive_tier("@joel-greenblatt/magic-formula") == "community"
    assert derive_tier("@acme/value-pack") == "community"


def test_derive_tier_v1_unchanged_by_v2_branch() -> None:
    """ADR-0034 D3 회귀 — `@` 분기 추가가 v1 derive_tier 동작을 바꾸지 않음.

    `@` prefix 는 v1 pattern(speculum-builtin/`community/`/`user/`)과 disjoint 라
    기존 분기 byte-불변(M6 #5 v1 불변 보존). v1 slug 들이 그대로 판정돼야 한다.
    """
    assert derive_tier("speculum-builtin") == "canonical"
    assert derive_tier("community/acme-value") == "community"
    assert derive_tier("user/alice-mypack") == "custom"
    assert derive_tier("weird-slug") == "custom"


def test_factor_pack_v2_schema_valid_and_namespace_pattern() -> None:
    """ADR-0034 D1 — factor-pack-v2.json 유효 + pack_slug 가 @{publisher}/{slug} 강제.

    v2 schema 가 Draft 2020-12 메타스키마를 통과하고, pack_slug pattern 이 v2
    namespace(`@{publisher}/{slug}`)만 허용하며 v1 형식(speculum-builtin/community//
    user/)을 거부함을 확인(ADR-0034 D2 — `@` disjoint).
    """
    import json
    import re
    from pathlib import Path

    from jsonschema import Draft202012Validator

    schema_path = (
        Path(__file__).resolve().parents[2]
        / "shared" / "schemas" / "factor-pack-v2.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    # 메타스키마 유효(schema 자체가 올바른 JSON Schema).
    Draft202012Validator.check_schema(schema)

    pat = schema["properties"]["pack_slug"]["pattern"]
    # v2 namespace 통과.
    assert re.match(pat, "@acme/value-pack")
    assert re.match(pat, "@joel-greenblatt/magic-formula")
    # v1 형식 거부(@ disjoint — ADR-0034 D2).
    assert not re.match(pat, "speculum-builtin")
    assert not re.match(pat, "community/x")
    assert not re.match(pat, "user/x")


def test_v2_namespace_community_tier_first_wins() -> None:
    """ADR-0034 D3/D5 (M6 #5 T-M6-05c) — v2(@) pack 은 community tier 라 기존
    community 와 동률, cross-pack 동작 회귀 0.

    `build_existing_index` 의 `_tier_rank` 에서 v2(`@`→community)·기존 `community/`
    가 같은 rank(1)다. 같은 canonical_id 가 두 pack 에 다른 정의(다른 hash)로 있으면
    입력 순서 first-wins(안정 정렬)가 적용되며, v2 도입이 이 community-community
    동작(by_cid first-wins)을 바꾸지 않는다(v1 cross-pack identity 보존 — ADR-0034 D5).
    """
    cid = "alice:per"
    u = "00000000-0000-0000-0000-000000000001"
    community = _pack(
        "community/x-pack",
        [_factor(canonical_id=cid, uuid_str=u, name_suffix=" v1")],
    )
    v2 = _pack(
        "@joel-greenblatt/y-pack",
        [_factor(canonical_id=cid, uuid_str=u, name_suffix=" v2")],
    )
    # 같은 rank(community) → 입력 first-wins: community 먼저면 community 점유.
    idx = build_existing_index([community, v2])
    assert idx.by_canonical_id[cid].pack_slug == "community/x-pack"
    assert idx.by_canonical_id[cid].tier == "community"
    # 순서 반전 → v2 점유(동률 first-wins — rank 동일이라 입력 순서가 결정).
    #   v2(@)도 community tier 로 분류됨(derive_tier @→community).
    idx2 = build_existing_index([v2, community])
    assert idx2.by_canonical_id[cid].pack_slug == "@joel-greenblatt/y-pack"
    assert idx2.by_canonical_id[cid].tier == "community"


# =============================================================================
# D8.1 — compute_factor_hash (uuid 제외 = 정의 동일성)
# =============================================================================

def test_factor_hash_excludes_uuid() -> None:
    """같은 정의 + 다른 uuid → 같은 hash (uuid 는 개체 식별자, 정의 무관)."""
    f1 = _factor(canonical_id="my:per", uuid_str="00000000-0000-0000-0000-000000000001")
    f2 = _factor(canonical_id="my:per", uuid_str="00000000-0000-0000-0000-0000000000ff")
    assert compute_factor_hash(f1) == compute_factor_hash(f2)


def test_factor_hash_differs_on_definition() -> None:
    """정의(name 등 uuid 외 필드) 다르면 다른 hash."""
    f1 = _factor(canonical_id="my:per", uuid_str="00000000-0000-0000-0000-000000000001")
    f2 = _factor(
        canonical_id="my:per", uuid_str="00000000-0000-0000-0000-000000000001",
        name_suffix=" CHANGED",
    )
    assert compute_factor_hash(f1) != compute_factor_hash(f2)


def test_factor_hash_deterministic() -> None:
    """같은 입력 → 같은 hash (JCS 결정성). sha256: prefix."""
    f = _factor(canonical_id="my:per", uuid_str="00000000-0000-0000-0000-000000000001")
    h = compute_factor_hash(f)
    assert h == compute_factor_hash(dict(f))
    assert h.startswith("sha256:")


# =============================================================================
# D8.1 — detect_cross_pack_conflicts
# =============================================================================

def test_detect_no_conflict_for_new_canonical_id() -> None:
    """기존에 없는 canonical_id → 충돌 0."""
    existing = builtin_existing_index()
    incoming = _pack("user/alice", [
        _factor(canonical_id="alice:brand-new",
                uuid_str="00000000-0000-0000-0000-000000000aa1"),
    ])
    assert detect_cross_pack_conflicts(incoming, existing) == []


def test_detect_conflict_for_builtin_canonical_id_different_def() -> None:
    """빌트인 canonical_id + 다른 정의 → 충돌(canonical tier, replace 불가)."""
    existing = builtin_existing_index()
    incoming = _pack("user/alice", [
        _factor(canonical_id=_BUILTIN_CID,
                uuid_str="00000000-0000-0000-0000-000000000aa2"),
    ])
    conflicts = detect_cross_pack_conflicts(incoming, existing)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.canonical_id == _BUILTIN_CID
    assert c.existing_tier == "canonical"
    # canonical 은 replace 불가 — skip/rename 만 (D8.2).
    assert c.allowed_resolutions == ("skip", "rename")


def test_detect_idempotent_same_definition_no_conflict() -> None:
    """빌트인 factor 를 정의 그대로(uuid 만 달라도) import → 충돌 0 (idempotent)."""
    existing = builtin_existing_index()
    # 빌트인 factor 를 그대로 복사하되 uuid 만 교체 — 정의(hash) 동일.
    clone = {**_BUILTIN_FACTORS[0], "uuid": "00000000-0000-0000-0000-000000000aa3"}
    incoming = _pack("user/alice", [clone])
    assert detect_cross_pack_conflicts(incoming, existing) == []


def test_detect_community_conflict_allows_replace() -> None:
    """community tier 충돌은 replace 허용 (D8.3) — canonical 과 대비."""
    # 가상의 community pack 을 existing 에 합류시켜 community tier 충돌 구성.
    community = _pack("community/acme-pack", [
        _factor(canonical_id="acme:score",
                uuid_str="00000000-0000-0000-0000-0000000000c1"),
    ])
    existing = build_existing_index([DEFAULT_PACK.body, community])
    incoming = _pack("user/alice", [
        _factor(canonical_id="acme:score",
                uuid_str="00000000-0000-0000-0000-0000000000c2",
                name_suffix=" DIFFERENT"),
    ])
    conflicts = detect_cross_pack_conflicts(incoming, existing)
    assert len(conflicts) == 1
    assert conflicts[0].existing_tier == "community"
    assert conflicts[0].allowed_resolutions == ("skip", "rename", "replace")


# =============================================================================
# D8.1 — assert_no_uuid_reuse
# =============================================================================

def test_uuid_reuse_for_different_canonical_id_rejected() -> None:
    """빌트인 uuid 를 다른 canonical_id 로 재사용 → UuidReuseViolation."""
    existing = builtin_existing_index()
    incoming = _pack("user/alice", [
        _factor(canonical_id="alice:hijack", uuid_str=_BUILTIN_UUID),
    ])
    with pytest.raises(UuidReuseViolation):
        assert_no_uuid_reuse(incoming, existing)


def test_uuid_same_canonical_id_not_reuse() -> None:
    """같은 canonical_id + 같은 uuid → 재사용 아님(idempotent, 통과)."""
    existing = builtin_existing_index()
    incoming = _pack("user/alice", [
        _factor(canonical_id=_BUILTIN_CID, uuid_str=_BUILTIN_UUID),
    ])
    assert_no_uuid_reuse(incoming, existing)  # raise 없음.


# =============================================================================
# D8.4/D8.2 — assert_no_unresolved_conflicts
# =============================================================================

def _builtin_conflict() -> Any:
    """빌트인 canonical 충돌 1 건 생성 helper."""
    existing = builtin_existing_index()
    incoming = _pack("user/alice", [
        _factor(canonical_id=_BUILTIN_CID,
                uuid_str="00000000-0000-0000-0000-000000000bb1"),
    ])
    return detect_cross_pack_conflicts(incoming, existing)


def test_unresolved_conflict_rejected() -> None:
    """resolution 미제공 충돌 → CrossPackIdentityConflict (D8.4 silent override 금지)."""
    conflicts = _builtin_conflict()
    with pytest.raises(CrossPackIdentityConflict):
        assert_no_unresolved_conflicts(conflicts, {})


def test_canonical_replace_forbidden() -> None:
    """canonical 충돌에 replace → CanonicalOverrideForbidden (D8.2 불가침)."""
    conflicts = _builtin_conflict()
    resolutions = {_BUILTIN_CID: {"action": "replace"}}
    with pytest.raises(CanonicalOverrideForbidden):
        assert_no_unresolved_conflicts(conflicts, resolutions)


def test_canonical_skip_allowed() -> None:
    """canonical 충돌에 skip → 통과(허용 resolution)."""
    conflicts = _builtin_conflict()
    assert_no_unresolved_conflicts(conflicts, {_BUILTIN_CID: {"action": "skip"}})


def test_rename_requires_new_canonical_id() -> None:
    """rename 인데 new_canonical_id 누락 → InvalidResolution."""
    conflicts = _builtin_conflict()
    with pytest.raises(InvalidResolution):
        assert_no_unresolved_conflicts(conflicts, {_BUILTIN_CID: {"action": "rename"}})


# =============================================================================
# D8.3 — apply_resolutions
# =============================================================================

def test_apply_skip_excludes_factor() -> None:
    """skip → 해당 factor 가 결과 body 에서 제외."""
    existing = builtin_existing_index()
    incoming = _pack("user/alice", [
        _factor(canonical_id=_BUILTIN_CID,
                uuid_str="00000000-0000-0000-0000-000000000bb2"),
        _factor(canonical_id="alice:keep",
                uuid_str="00000000-0000-0000-0000-000000000bb3"),
    ])
    conflicts = detect_cross_pack_conflicts(incoming, existing)
    new_body = apply_resolutions(
        incoming, conflicts, {_BUILTIN_CID: {"action": "skip"}}, existing,
    )
    cids = [f["canonical_id"] for f in new_body["factors"]]
    assert _BUILTIN_CID not in cids
    assert "alice:keep" in cids
    # content_hash 는 제거됨(재봉인은 route 책임).
    assert "content_hash" not in new_body


def test_apply_rename_replaces_canonical_id() -> None:
    """rename → canonical_id 가 new_canonical_id 로 치환."""
    existing = builtin_existing_index()
    incoming = _pack("user/alice", [
        _factor(canonical_id=_BUILTIN_CID,
                uuid_str="00000000-0000-0000-0000-000000000bb4"),
    ])
    conflicts = detect_cross_pack_conflicts(incoming, existing)
    new_body = apply_resolutions(
        incoming, conflicts,
        {_BUILTIN_CID: {"action": "rename", "new_canonical_id": "alice:my-per"}},
        existing,
    )
    cids = [f["canonical_id"] for f in new_body["factors"]]
    assert cids == ["alice:my-per"]
    assert _BUILTIN_CID not in cids


def test_apply_rename_to_existing_name_rejected() -> None:
    """rename 대상이 또 다른 기존 factor 와 충돌 → InvalidResolution."""
    existing = builtin_existing_index()
    # 두 빌트인 cid 가 있으면 한쪽을 다른 빌트인 이름으로 rename 시도.
    other_builtin = _BUILTIN_FACTORS[1]["canonical_id"] if len(_BUILTIN_FACTORS) > 1 else _BUILTIN_CID
    incoming = _pack("user/alice", [
        _factor(canonical_id=_BUILTIN_CID,
                uuid_str="00000000-0000-0000-0000-000000000bb5"),
    ])
    conflicts = detect_cross_pack_conflicts(incoming, existing)
    with pytest.raises(InvalidResolution):
        apply_resolutions(
            incoming, conflicts,
            {_BUILTIN_CID: {"action": "rename", "new_canonical_id": other_builtin}},
            existing,
        )


def test_apply_no_conflict_keeps_all_factors() -> None:
    """충돌 0 → 모든 factor 유지(clean import)."""
    existing = builtin_existing_index()
    incoming = _pack("user/alice", [
        _factor(canonical_id="alice:f1",
                uuid_str="00000000-0000-0000-0000-000000000cc1"),
        _factor(canonical_id="alice:f2",
                uuid_str="00000000-0000-0000-0000-000000000cc2"),
    ])
    conflicts = detect_cross_pack_conflicts(incoming, existing)
    assert conflicts == []
    new_body = apply_resolutions(incoming, conflicts, {}, existing)
    cids = sorted(f["canonical_id"] for f in new_body["factors"])
    assert cids == ["alice:f1", "alice:f2"]
