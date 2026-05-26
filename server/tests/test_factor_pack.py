"""factor_pack 단위 테스트.

테스트 매트릭스:
1. 빌트인 pack 로드 성공
2. JCS canonicalization 결정성 (key order 무관)
3. Hash 계산 결정성
4. Schema validation — 정상 / 누락 / 잘못된 type / 패턴 위반
5. Identity violation — canonical_id 중복 / uuid 중복
6. Citation 의무 — pack-level 없으면 factor-level 강제
7. Forbidden vocabulary in pack — factor name 의 "추천" 차단
8. Hash mismatch — placeholder / 잘못된 declared
9. fill_hash — placeholder 갱신
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.factor_pack import (
    CitationMissing,
    FactorPackError,
    ForbiddenVocabInPack,
    HashMismatch,
    IdentityViolation,
    LoadedPack,
    SchemaValidationError,
    canonicalize_jcs,
    compute_pack_hash,
    fill_hash,
    load_builtin_pack,
    load_pack,
    validate_citation,
    validate_forbidden_vocab,
    validate_hash,
    validate_identity,
    validate_schema,
)


_BUILTIN_PATH = (
    Path(__file__).resolve().parents[1]
    / "builtin-packs"
    / "factors"
    / "speculum-builtin-v1.0.0.json"
)


@pytest.fixture(scope="module")
def builtin_body() -> dict[str, Any]:
    """빌트인 pack 의 원본 dict — 각 테스트가 deep-copy 후 변경."""
    with _BUILTIN_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _clone(body: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(body)


# =============================================================================
# 1. 빌트인 pack 로드 성공
# =============================================================================

def test_load_builtin_pack_succeeds() -> None:
    pack = load_builtin_pack("1.0.0")
    assert isinstance(pack, LoadedPack)
    assert pack.pack_slug == "speculum-builtin"
    assert pack.version == "1.0.0"
    assert pack.factor_count == 10
    assert pack.computed_hash.startswith("sha256:")
    assert len(pack.computed_hash) == len("sha256:") + 64


def test_loaded_pack_body_has_all_required_fields(builtin_body: dict[str, Any]) -> None:
    pack = load_builtin_pack("1.0.0")
    for key in (
        "$schema", "type", "pack_slug", "version", "publisher",
        "license", "created_at", "citation", "factors", "content_hash",
    ):
        assert key in pack.body, f"{key} 누락"


def test_builtin_factors_have_unique_canonical_ids(builtin_body: dict[str, Any]) -> None:
    ids = [f["canonical_id"] for f in builtin_body["factors"]]
    assert len(ids) == len(set(ids))


def test_builtin_factors_have_unique_uuids(builtin_body: dict[str, Any]) -> None:
    uuids = [f["uuid"] for f in builtin_body["factors"]]
    assert len(uuids) == len(set(uuids))


# =============================================================================
# 2. JCS canonicalization 결정성
# =============================================================================

def test_canonicalize_jcs_is_deterministic_regardless_of_key_order() -> None:
    """같은 내용·다른 key 순서면 같은 bytes."""
    a = {"b": 1, "a": 2, "c": {"y": 3, "x": 4}}
    b = {"c": {"x": 4, "y": 3}, "a": 2, "b": 1}
    assert canonicalize_jcs(a) == canonicalize_jcs(b)


def test_canonicalize_jcs_strips_whitespace() -> None:
    """JSON 의 모든 공백 제거 (separators=(',', ':'))."""
    out = canonicalize_jcs({"a": 1, "b": [2, 3]})
    assert out == b'{"a":1,"b":[2,3]}'


def test_canonicalize_jcs_preserves_unicode() -> None:
    """ensure_ascii=False — 한글 그대로."""
    out = canonicalize_jcs({"name": "추천"})
    # 'name' 의 value 가 escape 없이 한글로 보존.
    assert "추천".encode("utf-8") in out


def test_canonicalize_jcs_rejects_nan_infinity() -> None:
    """RFC 8785 는 NaN / Infinity 금지."""
    with pytest.raises(ValueError):
        canonicalize_jcs({"x": float("nan")})
    with pytest.raises(ValueError):
        canonicalize_jcs({"x": float("inf")})


# =============================================================================
# 3. Hash 계산 결정성
# =============================================================================

def test_compute_pack_hash_excludes_content_hash_field() -> None:
    """`content_hash` 필드의 값이 hash 계산에 영향 없음 (chicken-and-egg 회피)."""
    body_a = {"a": 1, "content_hash": "sha256:" + "0" * 64}
    body_b = {"a": 1, "content_hash": "sha256:" + "f" * 64}
    assert compute_pack_hash(body_a) == compute_pack_hash(body_b)


def test_compute_pack_hash_is_deterministic_across_runs(
    builtin_body: dict[str, Any],
) -> None:
    h1 = compute_pack_hash(builtin_body)
    h2 = compute_pack_hash(builtin_body)
    assert h1 == h2


def test_hash_changes_when_factor_changes(builtin_body: dict[str, Any]) -> None:
    """factor 한 글자만 바뀌어도 hash 가 바뀜 (immutable hash 의 핵심)."""
    body = _clone(builtin_body)
    h_before = compute_pack_hash(body)
    body["factors"][0]["name"] = body["factors"][0]["name"] + " "  # whitespace 추가
    h_after = compute_pack_hash(body)
    assert h_before != h_after


# =============================================================================
# 4. Schema validation
# =============================================================================

def test_validate_schema_passes_for_builtin(builtin_body: dict[str, Any]) -> None:
    validate_schema(builtin_body)  # raises 안 함


def test_validate_schema_rejects_missing_required_field(
    builtin_body: dict[str, Any],
) -> None:
    body = _clone(builtin_body)
    del body["factors"]
    with pytest.raises(SchemaValidationError, match="factors"):
        validate_schema(body)


def test_validate_schema_rejects_wrong_type(builtin_body: dict[str, Any]) -> None:
    body = _clone(builtin_body)
    body["version"] = 100  # str pattern 위반
    with pytest.raises(SchemaValidationError):
        validate_schema(body)


def test_validate_schema_rejects_invalid_canonical_id_pattern(
    builtin_body: dict[str, Any],
) -> None:
    body = _clone(builtin_body)
    body["factors"][0]["canonical_id"] = "123:starts_with_number"
    with pytest.raises(SchemaValidationError):
        validate_schema(body)


def test_validate_schema_rejects_invalid_uuid(builtin_body: dict[str, Any]) -> None:
    body = _clone(builtin_body)
    body["factors"][0]["uuid"] = "not-a-uuid"
    with pytest.raises(SchemaValidationError):
        validate_schema(body)


def test_validate_schema_rejects_unknown_unit(builtin_body: dict[str, Any]) -> None:
    body = _clone(builtin_body)
    body["factors"][0]["unit"] = "watts"
    with pytest.raises(SchemaValidationError):
        validate_schema(body)


def test_validate_schema_rejects_additional_properties(
    builtin_body: dict[str, Any],
) -> None:
    body = _clone(builtin_body)
    body["unexpected_field"] = "x"
    with pytest.raises(SchemaValidationError):
        validate_schema(body)


# =============================================================================
# 5. Identity violation
# =============================================================================

def test_validate_identity_rejects_duplicate_canonical_id(
    builtin_body: dict[str, Any],
) -> None:
    body = _clone(builtin_body)
    body["factors"][1]["canonical_id"] = body["factors"][0]["canonical_id"]
    with pytest.raises(IdentityViolation, match="canonical_id"):
        validate_identity(body)


def test_validate_identity_rejects_duplicate_uuid(builtin_body: dict[str, Any]) -> None:
    body = _clone(builtin_body)
    body["factors"][1]["uuid"] = body["factors"][0]["uuid"]
    with pytest.raises(IdentityViolation, match="uuid"):
        validate_identity(body)


# =============================================================================
# 6. Citation 의무
# =============================================================================

def test_validate_citation_passes_with_pack_level_citation(
    builtin_body: dict[str, Any],
) -> None:
    # 빌트인 pack 은 pack-level citation 보유
    validate_citation(builtin_body)


def test_validate_citation_rejects_when_neither_pack_nor_factor(
    builtin_body: dict[str, Any],
) -> None:
    body = _clone(builtin_body)
    body["citation"] = {"title": ""}  # 빈 title — 효력 없음
    # 어느 factor 도 citation 없음 (빌트인 default).
    with pytest.raises(CitationMissing):
        validate_citation(body)


def test_validate_citation_passes_when_factor_has_own_citation(
    builtin_body: dict[str, Any],
) -> None:
    """pack-level 빈 citation 이라도 모든 factor 가 자체 citation 가지면 통과."""
    body = _clone(builtin_body)
    body["citation"] = {"title": ""}
    for f in body["factors"]:
        f["citation"] = {"title": f["canonical_id"] + " citation"}
    validate_citation(body)


# =============================================================================
# 7. Forbidden vocabulary in pack
# =============================================================================

def test_validate_forbidden_vocab_rejects_recommendation_in_factor_name(
    builtin_body: dict[str, Any],
) -> None:
    body = _clone(builtin_body)
    body["factors"][0]["name"] = "추천 종목 시가총액"
    with pytest.raises(ForbiddenVocabInPack):
        validate_forbidden_vocab(body)


def test_validate_forbidden_vocab_rejects_buy_in_description(
    builtin_body: dict[str, Any],
) -> None:
    body = _clone(builtin_body)
    body["factors"][0]["description"] = (
        "Strong Buy signal when market cap exceeds threshold."
    )
    with pytest.raises(ForbiddenVocabInPack):
        validate_forbidden_vocab(body)


def test_validate_forbidden_vocab_passes_for_builtin(
    builtin_body: dict[str, Any],
) -> None:
    validate_forbidden_vocab(builtin_body)


# =============================================================================
# 8. Hash mismatch / placeholder
# =============================================================================

def test_validate_hash_passes_when_declared_matches_computed(
    builtin_body: dict[str, Any],
) -> None:
    validate_hash(builtin_body)


def test_validate_hash_rejects_placeholder(builtin_body: dict[str, Any]) -> None:
    body = _clone(builtin_body)
    body["content_hash"] = "sha256:" + "0" * 64
    with pytest.raises(HashMismatch, match="placeholder"):
        validate_hash(body)


def test_validate_hash_rejects_mismatched_declared(builtin_body: dict[str, Any]) -> None:
    body = _clone(builtin_body)
    body["content_hash"] = "sha256:" + "1" * 64
    with pytest.raises(HashMismatch):
        validate_hash(body)


# =============================================================================
# 9. fill_hash — placeholder 갱신 (build utility)
# =============================================================================

def test_fill_hash_writes_computed_hash(
    tmp_path: Path, builtin_body: dict[str, Any],
) -> None:
    """fill_hash 가 placeholder 를 실제 hash 로 갱신."""
    body = _clone(builtin_body)
    body["content_hash"] = "sha256:" + "0" * 64

    target = tmp_path / "test-pack.json"
    target.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    new_hash = fill_hash(target)

    # 새 파일이 정상 검증 통과해야 함.
    loaded = load_pack(target)
    assert loaded.computed_hash == new_hash


def test_fill_hash_changes_when_content_changes(
    tmp_path: Path, builtin_body: dict[str, Any],
) -> None:
    """factor 변경 시 fill_hash 가 다른 결과."""
    body1 = _clone(builtin_body)
    body1["content_hash"] = "sha256:" + "0" * 64
    p1 = tmp_path / "a.json"
    p1.write_text(json.dumps(body1, ensure_ascii=False), encoding="utf-8")
    h1 = fill_hash(p1)

    body2 = _clone(builtin_body)
    body2["content_hash"] = "sha256:" + "0" * 64
    body2["factors"][0]["name"] = body2["factors"][0]["name"] + " modified"
    p2 = tmp_path / "b.json"
    p2.write_text(json.dumps(body2, ensure_ascii=False), encoding="utf-8")
    h2 = fill_hash(p2)

    assert h1 != h2


# =============================================================================
# 10. Full load_pack — end-to-end
# =============================================================================

def test_load_pack_propagates_each_validation_step(
    tmp_path: Path, builtin_body: dict[str, Any],
) -> None:
    """각 검증 단계의 오류가 적절한 예외로 전파."""
    # Schema fail
    body = _clone(builtin_body)
    del body["factors"]
    body["content_hash"] = "sha256:" + "0" * 64
    p = tmp_path / "schema-fail.json"
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SchemaValidationError):
        load_pack(p)

    # Identity fail
    body = _clone(builtin_body)
    body["factors"][1]["uuid"] = body["factors"][0]["uuid"]
    body["content_hash"] = compute_pack_hash(body)
    p = tmp_path / "identity-fail.json"
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(IdentityViolation):
        load_pack(p)

    # Forbidden vocab fail
    body = _clone(builtin_body)
    body["factors"][0]["name"] = "추천 가즈아"
    body["content_hash"] = compute_pack_hash(body)
    p = tmp_path / "vocab-fail.json"
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ForbiddenVocabInPack):
        load_pack(p)


def test_factor_pack_error_is_base_class() -> None:
    """모든 specific 예외가 FactorPackError 의 subclass."""
    for cls in (SchemaValidationError, IdentityViolation, CitationMissing,
                ForbiddenVocabInPack, HashMismatch):
        assert issubclass(cls, FactorPackError)
