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
    BuiltinCompositeError,
    CitationMissing,
    CyclicDependency,
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
    validate_acyclic,
    validate_builtin_no_composite,
    validate_citation,
    validate_forbidden_vocab,
    validate_hash,
    validate_identity,
    validate_schema,
    validate_shared_meta,
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


# v1.0.0 빌트인 pack 의 **byte-불변 golden hash anchor** (§2.10 load-bearing, M7 #4
# ADR-0035 D4). 이 hash 가 변하면 frozen v1.0.0 run 의 reproduce 가 깨진다 — DEFAULT_PACK
# bump (v1.0.0→v1.1.0) 가 v1.0.0.json 을 단 1 byte 도 건드리지 않았음을 회귀로 못박는다.
_V100_GOLDEN_HASH = (
    "sha256:78e2d93f46700e475c56d17edc76faa8aa742406368b1b631a45b3908d5aa36d"
)


def test_builtin_v1_0_0_golden_hash_anchor() -> None:
    """v1.0.0 content_hash 가 frozen golden 과 byte-동일 — reproduce 불변 보증."""
    pack = load_builtin_pack("1.0.0")
    assert pack.computed_hash == _V100_GOLDEN_HASH
    # body 의 declared content_hash 도 동일 (load_pack 의 validate_hash 통과 = 동치이나
    # 명시 확인 — v1.0.0.json 미수정 보증).
    assert pack.body["content_hash"] == _V100_GOLDEN_HASH


def test_load_builtin_pack_v1_1_0_succeeds() -> None:
    """v1.1.0 빌트인 pack 로드 성공 — price-return:total-annual 추가로 11 factor."""
    pack = load_builtin_pack("1.1.0")
    assert isinstance(pack, LoadedPack)
    assert pack.pack_slug == "speculum-builtin"
    assert pack.version == "1.1.0"
    assert pack.factor_count == 11
    assert pack.computed_hash.startswith("sha256:")
    # v1.1.0 은 v1.0.0 과 독립 hash (factor 추가로 content 변경).
    assert pack.computed_hash != _V100_GOLDEN_HASH
    canonical_ids = {f["canonical_id"] for f in pack.body["factors"]}
    assert "price-return:total-annual" in canonical_ids


def test_builtin_v1_1_0_total_return_factor_is_percent_unit() -> None:
    """price-return:total-annual factor — unit=percent + 단일 field 직접 참조 AST.

    field total_return_trailing_1y 는 ratio 반환, factor 는 percent 표현 (ratio_pct/
    dividend-yield 와 동일하게 ×100 은 표시 layer 책임, ADR-0002 D6). AST 는 bare
    field leaf (×100 const 곱 불필요 — schema 가장 단순·일관 방식).
    """
    pack = load_builtin_pack("1.1.0")
    factor = next(
        f for f in pack.body["factors"]
        if f["canonical_id"] == "price-return:total-annual"
    )
    assert factor["unit"] == "percent"
    assert factor["formula"]["ast"] == {"field": "total_return_trailing_1y"}
    assert factor["formula"]["inputs"] == ["total_return_trailing_1y"]


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
    assert "추천".encode() in out


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


def _nested_ast(depth: int) -> dict[str, Any]:
    """`depth` 단계 중첩된 add op AST 생성 — 깊이 DoS 회귀용."""
    node: dict[str, Any] = {"field": "shares_issued"}
    for _ in range(depth):
        node = {"op": "add", "left": node, "right": {"field": "shares_issued"}}
    return node


def test_validate_schema_rejects_deeply_nested_ast(
    builtin_body: dict[str, Any],
) -> None:
    """깊은 중첩 AST → jsonschema 재귀 전 선검사로 거부(RecursionError/500 방어).

    `/validate`·`/import-check` 는 인증 불요라 익명 DoS 표면. 한도(64) 초과 AST 는
    SchemaValidationError(깔끔한 거부)여야 하며, 미처리 RecursionError 가 새어
    나오면 안 된다(M4_PLAN §3 #1).
    """
    body = _clone(builtin_body)
    # Python 기본 recursion limit 을 넉넉히 넘는 깊이 — 선검사 없으면 jsonschema
    # 재귀에서 RecursionError 위험.
    body["factors"][0]["formula"]["ast"] = _nested_ast(2000)
    with pytest.raises(SchemaValidationError, match="중첩 깊이"):
        validate_schema(body)


def test_validate_schema_allows_ast_at_depth_limit(
    builtin_body: dict[str, Any],
) -> None:
    """한도(64) 이내 중첩은 깊이 선검사를 통과(정상 pack 무영향).

    선검사가 한도 내 AST 를 막지 않음을 보장 — 깊이 게이트가 schema 거부로
    오작동하지 않는지(정상 evaluator max_ast_depth=32 보다 큰 64 여유). 본 AST 는
    op 구조상 schema(expression)를 통과하므로 validate_schema 가 raises 하지 않아야
    한다.
    """
    body = _clone(builtin_body)
    body["factors"][0]["formula"]["ast"] = _nested_ast(60)
    validate_schema(body)  # raises 안 함 — 깊이·schema 모두 통과.


# =============================================================================
# 4b. Schema validator dispatch — v1/v2 ($schema URI 기준, ADR-0034 D6)
# =============================================================================
#
# validate_schema 가 body 의 `$schema` URI 로 v1/v2 validator 를 고른다. v2
# namespace pack(`@{publisher}/{slug}`)이 통과하고, v1 byte-불변(미지/부재→v1
# default)이 보존됨을 검증한다(§2.10 최우선).

_V2_SCHEMA_URI = "https://speculum.dev/schemas/factor-pack-v2.json"


def _v2_pack(*, pack_slug: str = "@alice/value-pack") -> dict[str, Any]:
    """최소 v2 namespace pack — `$schema`=v2 URI, slug `@{publisher}/{slug}`."""
    return {
        "$schema": _V2_SCHEMA_URI,
        "type": "factor-pack",
        "pack_slug": pack_slug,
        "version": "1.0.0",
        "publisher": "alice",
        "license": "MIT",
        "created_at": "2026-01-01",
        "citation": {"title": "Alice value pack"},
        "factors": [
            {
                "canonical_id": "alice:per",
                "uuid": "00000000-0000-0000-0000-000000000001",
                "name": "Alice PER",
                "description": "alice per 정의 for v2 dispatch test",
                "formula": {
                    "ast": {"field": "shares_issued"},
                    "inputs": ["shares_issued"],
                },
                "unit": "ratio",
            },
        ],
        "content_hash": "sha256:" + "a" * 64,
    }


def test_validate_schema_accepts_v2_namespace_slug() -> None:
    """`$schema`=v2 URI + `@{publisher}/{slug}` slug → v2 validator 통과(거부 해소)."""
    validate_schema(_v2_pack())  # raises 안 함 — v2 dispatch.


def test_validate_schema_v2_body_rejected_by_v1_validator() -> None:
    """v2 slug(`@...`) body 를 v1 으로 검증하면 거부 — 분기 정확성.

    `$schema` 를 v1 URI 로 바꾸면 v1 validator 가 선택되고, v1 의 pack_slug
    pattern 은 `@{publisher}/{slug}` 를 허용하지 않으므로 SchemaValidationError.
    이로써 dispatch 가 실제로 다른 validator 를 고름을 증명(v2 통과가 v1 완화 아님).
    """
    body = _v2_pack()
    body["$schema"] = "https://speculum.dev/schemas/factor-pack-v1.json"
    with pytest.raises(SchemaValidationError, match="pack_slug"):
        validate_schema(body)


def test_validate_schema_v1_body_unchanged_byte_invariant(
    builtin_body: dict[str, Any],
) -> None:
    """v1 body(`$schema`=v1)는 v1 validator 로 통과 — byte-불변(§2.10)."""
    body = _clone(builtin_body)
    assert body["$schema"] == "https://speculum.dev/schemas/factor-pack-v1.json"
    validate_schema(body)  # raises 안 함 — v1 default 경로.


def test_validate_schema_missing_schema_field_defaults_v1(
    builtin_body: dict[str, Any],
) -> None:
    """`$schema` 부재 body → v1 default validator(하위호환).

    `$schema` 를 제거하면 required 위반으로 v1 validator 가 거부해야 한다(v1
    schema 가 `$schema` 를 required 로 두므로). dispatch default 가 v1 임을
    간접 증명 — 미지/부재가 v2 로 새지 않는다.
    """
    body = _clone(builtin_body)
    del body["$schema"]
    with pytest.raises(SchemaValidationError, match="schema"):
        validate_schema(body)


def test_validate_schema_unknown_schema_uri_defaults_v1() -> None:
    """미지 `$schema` URI → v1 default. v2 slug body 는 v1 으로 거부됨(default=v1).

    `$schema` 가 map 에 없는 값이면 `.get(default=_VALIDATOR)` 로 v1 이 쓰인다.
    v2 slug body 에 미지 URI 를 주면 v1 pattern 위반으로 거부 — default 가 v1 확인.
    """
    body = _v2_pack()
    body["$schema"] = "https://speculum.dev/schemas/factor-pack-v99.json"
    with pytest.raises(SchemaValidationError, match="pack_slug"):
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


def test_validate_forbidden_vocab_ignores_pack_meta(
    builtin_body: dict[str, Any],
) -> None:
    # pack-level 공개 메타(description/citation.title)는 본 함수 범위 밖 —
    # private pack 은 USER_PRIVATE 로 보유 가능(ADR-0028 D2). 게이트는 public-toggle
    # 또는 import(route `_gate_import_pack_meta`, ADR-0032 D4) 시점. 따라서 advice 가
    # pack-메타에 있어도 본 함수는 통과한다(factor 정의는 깨끗).
    body = _clone(builtin_body)
    body["description"] = "추천주 모음 — 지금 매수하세요."
    body["citation"]["title"] = "Best Buy Stocks Pack"
    validate_forbidden_vocab(body)  # raises 안 함


# ADR-0032 D4 — validate_shared_meta: pack 공개 메타(공유/import 시점) 게이트.

def test_validate_shared_meta_passes_for_builtin(
    builtin_body: dict[str, Any],
) -> None:
    validate_shared_meta(builtin_body)  # 깨끗 → raises 안 함


def test_validate_shared_meta_rejects_advice_in_description(
    builtin_body: dict[str, Any],
) -> None:
    body = _clone(builtin_body)
    body["description"] = "추천주 모음 — 지금 매수하세요."
    with pytest.raises(ForbiddenVocabInPack):
        validate_shared_meta(body)


def test_validate_shared_meta_rejects_advice_in_citation_title(
    builtin_body: dict[str, Any],
) -> None:
    body = _clone(builtin_body)
    body["citation"]["title"] = "Best Buy Stocks Pack"
    with pytest.raises(ForbiddenVocabInPack):
        validate_shared_meta(body)


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
                ForbiddenVocabInPack, HashMismatch, CyclicDependency):
        assert issubclass(cls, FactorPackError)


# =============================================================================
# 11. 정적 DAG acyclicity 검증 (ADR-0022 R5/D2)
# =============================================================================
#
# factor 의존 그래프 (factor A 의 inputs 가 다른 factor B 의 출력 derived field
# 참조) 의 순환을 데이터 없이 정의만으로 탐지. db_field_provider._resolving
# (런타임 동적) 과 다른 정적 메커니즘.


def _mini_factor(
    *, canonical_id: str, uuid_str: str, inputs: list[str], ast: dict[str, Any],
) -> dict[str, Any]:
    """acyclicity 테스트용 minimal factor — schema 필수 필드만."""
    return {
        "canonical_id": canonical_id,
        "uuid": uuid_str,
        "name": canonical_id,
        "description": canonical_id + " desc",
        "formula": {"ast": ast, "inputs": inputs},
        "unit": "ratio",
    }


def _mini_pack(factors: list[dict[str, Any]]) -> dict[str, Any]:
    return {"factors": factors}


def test_validate_acyclic_passes_for_builtin(builtin_body: dict[str, Any]) -> None:
    """빌트인 pack (PER → market-cap:ex-treasury 단방향 의존) 은 acyclic."""
    validate_acyclic(builtin_body)  # raises 안 함


def test_validate_acyclic_passes_for_linear_chain() -> None:
    """A → B → C 단방향 체인 — acyclic 통과.

    field 명명 규칙: factor `a:base` 출력 = field `a_base` (':'/'-' → '_').
    """
    factors = [
        _mini_factor(
            canonical_id="a:base", uuid_str="00000000-0000-0000-0000-00000000000a",
            inputs=["primitive_x"], ast={"field": "primitive_x"},
        ),
        _mini_factor(
            canonical_id="b:mid", uuid_str="00000000-0000-0000-0000-00000000000b",
            inputs=["a_base"], ast={"field": "a_base"},
        ),
        _mini_factor(
            canonical_id="c:top", uuid_str="00000000-0000-0000-0000-00000000000c",
            inputs=["b_mid"], ast={"field": "b_mid"},
        ),
    ]
    validate_acyclic(_mini_pack(factors))  # raises 안 함


def test_validate_acyclic_rejects_direct_cycle() -> None:
    """A → B → A 순환 — fail-loud (CyclicDependency)."""
    factors = [
        _mini_factor(
            canonical_id="a:x", uuid_str="00000000-0000-0000-0000-00000000000a",
            inputs=["b_y"], ast={"field": "b_y"},  # A 가 B 출력 참조
        ),
        _mini_factor(
            canonical_id="b:y", uuid_str="00000000-0000-0000-0000-00000000000b",
            inputs=["a_x"], ast={"field": "a_x"},  # B 가 A 출력 참조 → 순환
        ),
    ]
    with pytest.raises(CyclicDependency, match="순환"):
        validate_acyclic(_mini_pack(factors))


def test_validate_acyclic_rejects_self_loop() -> None:
    """A → A self-loop (자기 출력 field 를 자기가 참조) — 순환."""
    factors = [
        _mini_factor(
            canonical_id="a:self", uuid_str="00000000-0000-0000-0000-00000000000a",
            inputs=["a_self"], ast={"field": "a_self"},
        ),
    ]
    with pytest.raises(CyclicDependency, match="순환"):
        validate_acyclic(_mini_pack(factors))


def test_validate_acyclic_rejects_indirect_cycle() -> None:
    """A → B → C → A 간접 순환 — 탐지."""
    factors = [
        _mini_factor(
            canonical_id="a:x", uuid_str="00000000-0000-0000-0000-00000000000a",
            inputs=["c_z"], ast={"field": "c_z"},
        ),
        _mini_factor(
            canonical_id="b:y", uuid_str="00000000-0000-0000-0000-00000000000b",
            inputs=["a_x"], ast={"field": "a_x"},
        ),
        _mini_factor(
            canonical_id="c:z", uuid_str="00000000-0000-0000-0000-00000000000c",
            inputs=["b_y"], ast={"field": "b_y"},
        ),
    ]
    with pytest.raises(CyclicDependency, match="순환"):
        validate_acyclic(_mini_pack(factors))


def test_validate_acyclic_cycle_message_includes_path() -> None:
    """순환 에러가 순환 경로 (factor canonical_id) 를 노출 — 디버깅."""
    factors = [
        _mini_factor(
            canonical_id="a:x", uuid_str="00000000-0000-0000-0000-00000000000a",
            inputs=["b_y"], ast={"field": "b_y"},
        ),
        _mini_factor(
            canonical_id="b:y", uuid_str="00000000-0000-0000-0000-00000000000b",
            inputs=["a_x"], ast={"field": "a_x"},
        ),
    ]
    with pytest.raises(CyclicDependency) as exc:
        validate_acyclic(_mini_pack(factors))
    msg = str(exc.value)
    assert "a:x" in msg and "b:y" in msg


def test_validate_acyclic_ignores_primitive_fields() -> None:
    """primitive DB field (어떤 factor 출력도 아님) 참조는 엣지 생성 안 함."""
    factors = [
        _mini_factor(
            canonical_id="a:base", uuid_str="00000000-0000-0000-0000-00000000000a",
            inputs=["shares_issued", "close_price_adjusted"],
            ast={"op": "mul",
                 "left": {"field": "shares_issued"},
                 "right": {"field": "close_price_adjusted"}},
        ),
    ]
    validate_acyclic(_mini_pack(factors))  # primitive 만 — acyclic


def test_load_pack_rejects_cyclic_pack(
    tmp_path: Path, builtin_body: dict[str, Any],
) -> None:
    """load_pack 이 순환 pack 을 거부 (검증 파이프라인 통합)."""
    body = _clone(builtin_body)
    # 빌트인의 처음 두 factor 를 상호 참조 순환으로 변조.
    body["factors"] = [
        _mini_factor(
            canonical_id="a:x", uuid_str="00000000-0000-0000-0000-00000000000a",
            inputs=["b_y"], ast={"field": "b_y"},
        ),
        _mini_factor(
            canonical_id="b:y", uuid_str="00000000-0000-0000-0000-00000000000b",
            inputs=["a_x"], ast={"field": "a_x"},
        ),
    ]
    body["content_hash"] = compute_pack_hash(body)
    p = tmp_path / "cyclic.json"
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CyclicDependency):
        load_pack(p)


# =============================================================================
# 12. 금지 op schema 거부 (ADR-0022 D1) — rank/top_n/bottom_n/sign/step
# =============================================================================

@pytest.mark.parametrize("forbidden_op", ["rank", "top_n", "bottom_n", "sign", "step"])
def test_schema_rejects_forbidden_ops(
    builtin_body: dict[str, Any], forbidden_op: str,
) -> None:
    """ADR-0022 D1 — rank/top_n/bottom_n/sign/step 은 exprOp enum 에 부재 (거부).

    "처음부터 부재가 안전" (ADR-0007 Alternative C) — schema 가 1 차 방어선.
    """
    body = _clone(builtin_body)
    body["factors"][0]["formula"]["ast"] = {"op": forbidden_op, "field": "shares_issued"}
    body["factors"][0]["formula"]["inputs"] = ["shares_issued"]
    with pytest.raises(SchemaValidationError):
        validate_schema(body)


# =============================================================================
# 13. 허용 composite op schema 통과 (ADR-0022 D1)
# =============================================================================

def test_schema_accepts_weighted_sum(builtin_body: dict[str, Any]) -> None:
    body = _clone(builtin_body)
    body["factors"][0]["formula"]["ast"] = {
        "op": "weighted_sum",
        "args": [{"field": "shares_issued"}, {"field": "shares_treasury"}],
        # ADR-0032 D1 — weights 는 decimal 문자열(float 직렬화 봉쇄, cross-runtime hash).
        "weights": ["0.5", "0.5"],
    }
    body["factors"][0]["formula"]["inputs"] = ["shares_issued", "shares_treasury"]
    validate_schema(body)  # raises 안 함


def test_schema_accepts_winsorize(builtin_body: dict[str, Any]) -> None:
    body = _clone(builtin_body)
    body["factors"][0]["formula"]["ast"] = {
        "op": "winsorize", "args": [{"field": "shares_issued"}],
        # ADR-0032 D1 — lower/upper 는 decimal 문자열.
        "lower": "0", "upper": "1000000",
    }
    body["factors"][0]["formula"]["inputs"] = ["shares_issued"]
    validate_schema(body)


@pytest.mark.parametrize("op", ["zscore", "percentile", "min_max_scale"])
def test_schema_accepts_universe_relative_ops(
    builtin_body: dict[str, Any], op: str,
) -> None:
    body = _clone(builtin_body)
    body["factors"][0]["formula"]["ast"] = {"op": op, "field": "shares_issued"}
    body["factors"][0]["formula"]["inputs"] = ["shares_issued"]
    validate_schema(body)


# =============================================================================
# 12. 빌트인 Composite op 금지 게이트 (ADR-0022 D5 / ADR-0007 D5.1 / T73)
# =============================================================================
#
# 빌트인 pack 에 유니버스-상대(zscore/percentile/min_max_scale) + weighted_sum 이
# 들어가면 "빌트인 multi-factor score"(= 사실상 추천) 가 되므로 load_builtin_pack
# 에서 자동 거부. community/custom(사용자 정의)은 허용. winsorize 는 제외.


def test_validate_builtin_no_composite_passes_for_builtin(
    builtin_body: dict[str, Any],
) -> None:
    """빌트인 pack 은 Composite op 미사용 — 통과 (DEFAULT_PACK import 도 통과)."""
    validate_builtin_no_composite(builtin_body)  # raises 안 함


@pytest.mark.parametrize(
    "op", ["weighted_sum", "zscore", "percentile", "min_max_scale"]
)
def test_validate_builtin_no_composite_rejects(
    builtin_body: dict[str, Any], op: str,
) -> None:
    """빌트인에 Composite op 포함 → BuiltinCompositeError (ADR-0022 D5)."""
    body = _clone(builtin_body)
    body["factors"][0]["formula"]["ast"] = {"op": op, "field": "shares_issued"}
    body["factors"][0]["formula"]["inputs"] = ["shares_issued"]
    with pytest.raises(BuiltinCompositeError, match="Composite op"):
        validate_builtin_no_composite(body)


def test_validate_builtin_no_composite_allows_winsorize(
    builtin_body: dict[str, Any],
) -> None:
    """winsorize(종목-국소 전처리)는 빌트인 허용 — D5 제외."""
    body = _clone(builtin_body)
    body["factors"][0]["formula"]["ast"] = {
        "op": "winsorize", "args": [{"field": "shares_issued"}],
        "lower": 0, "upper": 1000000,
    }
    body["factors"][0]["formula"]["inputs"] = ["shares_issued"]
    validate_builtin_no_composite(body)  # raises 안 함


def test_validate_builtin_no_composite_detects_nested(
    builtin_body: dict[str, Any],
) -> None:
    """중첩 expression(left/right)의 Composite op 도 탐지 — AST walk."""
    body = _clone(builtin_body)
    body["factors"][0]["formula"]["ast"] = {
        "op": "add",
        "left": {"field": "shares_issued"},
        "right": {"op": "percentile", "field": "shares_issued"},
    }
    body["factors"][0]["formula"]["inputs"] = ["shares_issued"]
    with pytest.raises(BuiltinCompositeError):
        validate_builtin_no_composite(body)
