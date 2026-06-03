"""Factor Pack 로딩·검증·hash 계산 — ADR-0002 D4, D6 의 implementation.

본 모듈은:

1. `shared/schemas/factor-pack-v1.json` 으로 JSON schema 검증.
2. **RFC 8785 JCS** (JSON Canonicalization Scheme) 으로 정규화 + SHA-256 → 결정적
   content_hash 계산. cross-runtime (Python / Node) 동일 hash 보장.
3. **Multi-id 무결성** — canonical_id / uuid 중복 검사 (ADR-0002 D2 의 3-tier
   identity 시스템 무결성).
4. **금지 어휘 검사** — factor name / description 이 ADR-0007 D4 의 어휘를
   포함하지 않음 (빌트인 factor 이름의 advisory vocabulary 차단).
5. **Citation 의무** — 각 factor 또는 pack level 에 citation. ADR-0002 D3 의
   Source Citation 의 Pack 단계 implementation.

관련 ADR / 리뷰:
- ADR-0002 D4 — Pack 버저닝 (semver + immutable hash)
- ADR-0002 D6 — Pack JSON schema 형태
- ADR-0004 D6 — 빌트인 ~32 factor list (본 모듈은 그 subset 검증)
- ADR-0007 D4 — 금지 어휘 검사가 factor name / description 에도 적용
- Norma `docs/CONCEPT.md §2.14` Run snapshot freeze 의 hash chain 패턴

RFC 8785 JCS 구현 노트:
- Python 표준 라이브러리에는 JCS 가 없음. `jcs` 패키지가 있으나 외부 의존 증가.
- 본 모듈은 JCS subset 을 자체 구현 — sort_keys=True, ensure_ascii=False,
  separators=(',', ':'), number canonicalization. cross-runtime 검증은 별도
  CI step (다음 사이클).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app.services._jcs import (
    canonicalize_jcs,  # noqa: F401  re-export — tests/test_factor_pack.py 의존
    compute_content_hash,
)
from app.services.forbidden_words import CheckScope, assert_clean

# =============================================================================
# Path constants
# =============================================================================

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_SCHEMA_PATH: Final[Path] = _REPO_ROOT / "shared" / "schemas" / "factor-pack-v1.json"
_BUILTIN_DIR: Final[Path] = _REPO_ROOT / "server" / "builtin-packs" / "factors"
# speculum 제공 reference pack 디렉토리 — 빌트인(active universe pack)이 아니라
# Factor Lab 에서 불러와 쓰는 "참조 정의"(리츠 ffo-multiple 등, ADR-0023 D8).
# 빌트인과 분리돼 content_hash·재현성에 무관(빌트인 DEFAULT_PACK 무변경 보존).
_REFERENCE_DIR: Final[Path] = _REPO_ROOT / "server" / "builtin-packs" / "reference"

# content_hash 필드는 자기 자신을 hash 입력에서 제외해야 함 (chicken-and-egg).
_HASH_FIELD: Final[str] = "content_hash"
_HASH_PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")


# =============================================================================
# Errors
# =============================================================================

class FactorPackError(Exception):
    """Factor pack 처리 중 발생한 모든 오류의 base."""


class SchemaValidationError(FactorPackError):
    """JSON schema 위반."""


class IdentityViolation(FactorPackError):
    """canonical_id / uuid 중복 등 ADR-0002 D2 3-tier identity 위반."""


class HashMismatch(FactorPackError):
    """content_hash 값이 실제 계산된 값과 다름 — pack 가 변조됐거나 hash 가 stale."""


class ForbiddenVocabInPack(FactorPackError):
    """Factor name / description 에 금지 어휘 (ADR-0007 D4) 포함."""


class CitationMissing(FactorPackError):
    """Pack 또는 factor 에 citation 누락 (ADR-0002 D3)."""


class CyclicDependency(FactorPackError):
    """Factor 의존 그래프에 순환 — pack 정의만으로 탐지 (ADR-0022 R5/D2).

    factor A 의 formula.inputs 가 다른 factor B 의 출력 (derived field) 을
    참조하고, B 가 (직·간접으로) 다시 A 를 참조하면 순환. 데이터 없이 pack
    정의만으로 fail-loud — DAG 가 아닌 pack 은 로드 거부.

    **db_field_provider._resolving (M1) 과의 차이 (정적 vs 동적):**
    - `_resolving` 은 **런타임 per-evaluation** 재진입 가드 — 한 종목·한 시점
      평가 중 같은 derived field 가 다시 해소되면 raise. 데이터 평가 경로에서만
      발동하고, 평가되지 않는 (N/A 단축된) 가지의 순환은 못 잡는다.
    - 본 검증은 **정적** — pack 로드 시 1 회, 데이터·평가 없이 의존 그래프
      전체에서 순환을 탐지. 평가되지 않는 가지도 포함해 무결성을 보장.
    """


class BuiltinCompositeError(FactorPackError):
    """빌트인 pack 이 Composite(유니버스-상대 / weighted_sum) op 사용 — ADR-0022 D5.

    ADR-0007 D5.1: **빌트인 multi-factor score 0**. zscore/percentile/min_max_scale
    (유니버스-상대) 와 weighted_sum 은 사용자 정의 Composite 전용 — 빌트인 pack 에
    들어가면 "Magic Formula 점수" 같은 빌트인 합산점수(= 사실상 추천)가 된다.
    community/custom pack(사용자 정의)은 허용하나 빌트인은 금지. `load_builtin_pack`
    에서만 검증(DEFAULT_PACK import 시점 자동 게이트 — ADR-0007 D8.2 의 검색 게이트
    패턴, T73). winsorize 는 종목-국소 전처리라 제외(D5 는 유니버스-상대+weighted_sum).
    """


# =============================================================================
# Result types
# =============================================================================

@dataclass(frozen=True, slots=True)
class LoadedPack:
    """검증·로드된 factor pack.

    Attributes:
        body: JSON dict (parsed).
        computed_hash: 모듈이 계산한 hash. body['content_hash'] 와 동일해야 함.
        factor_count: pack 내 factor 수.
        pack_slug: 식별 slug.
        version: semver.
    """

    body: dict[str, Any]
    computed_hash: str
    factor_count: int
    pack_slug: str
    version: str


# =============================================================================
# Schema loading (module-level, single load)
# =============================================================================

def _load_schema() -> dict[str, Any]:
    """JSON schema 로드 — module import 시 1회."""
    with _SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


_SCHEMA: Final[dict[str, Any]] = _load_schema()
_VALIDATOR: Final[Draft202012Validator] = Draft202012Validator(_SCHEMA)


# =============================================================================
# RFC 8785 JCS — `_jcs` util 로 이관 (T30 의 공유 정리).
# 운영 backwards compat 을 위해 `canonicalize_jcs` 를 본 모듈에서도 re-export.
# =============================================================================

def compute_pack_hash(body: dict[str, Any]) -> str:
    """`content_hash` 필드 제외 후 JCS + SHA-256. `_jcs.compute_content_hash` 의 alias.

    반환 형식: "sha256:<hex>".
    """
    return compute_content_hash(body, exclude_key=_HASH_FIELD)


# =============================================================================
# Validation
# =============================================================================

def validate_schema(body: dict[str, Any]) -> None:
    """JSON schema 검증. 실패 시 SchemaValidationError + 첫 위반 경로."""
    try:
        _VALIDATOR.validate(body)
    except ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise SchemaValidationError(
            f"schema violation at {path}: {exc.message}"
        ) from exc


def validate_identity(body: dict[str, Any]) -> None:
    """canonical_id / uuid 중복 검사 — ADR-0002 D2 3-tier identity 무결성."""
    canonical_ids: list[str] = []
    uuids: list[str] = []
    for factor in body["factors"]:
        canonical_ids.append(factor["canonical_id"])
        uuids.append(factor["uuid"])

    canonical_dups = _find_duplicates(canonical_ids)
    if canonical_dups:
        raise IdentityViolation(
            f"duplicate canonical_id in pack: {sorted(canonical_dups)}"
        )

    uuid_dups = _find_duplicates(uuids)
    if uuid_dups:
        raise IdentityViolation(
            f"duplicate uuid in pack: {sorted(uuid_dups)}"
        )


def _find_duplicates(items: list[str]) -> set[str]:
    seen: set[str] = set()
    dups: set[str] = set()
    for it in items:
        if it in seen:
            dups.add(it)
        seen.add(it)
    return dups


# =============================================================================
# 정적 DAG acyclicity 검증 — ADR-0022 R5/D2
# =============================================================================
#
# Factor 의존 모델: factor 의 formula.inputs 에 등장하는 field 중, 다른 factor
# 의 **출력 (derived field)** 인 것이 factor-간 의존 엣지를 만든다. 출력 field
# 명은 canonical_id 의 명명 규칙으로 도출한다 (db_field_provider 의 derived_factor
# target_factor 매핑과 동일 규칙 — 예: factor `market-cap:ex-treasury` 의 출력
# field 는 `market_cap_ex_treasury`). 이 매핑이 pack 정의만으로 의존 그래프를
# 구성 가능케 한다 (런타임 registry 불필요 — 정적 검증의 self-contained 성).


def _derived_field_name(canonical_id: str) -> str:
    """canonical_id → 그 factor 가 출력하는 derived field 명.

    명명 규칙: canonical_id 의 `:` (tier 구분) 와 `-` (kebab) 을 `_` 로 치환.
    예: `market-cap:ex-treasury` → `market_cap_ex_treasury`. db_field_provider
    의 `_RESOLUTIONS` derived_factor entry (field `market_cap_ex_treasury` ↔
    target_factor `market-cap:ex-treasury`) 와 동일 규칙. 결과는 schema 의 field
    pattern (`^[a-z][a-z0-9_]*$`) 과 일관 (canonical_id 가 그 pattern 을 만족하면).
    """
    return canonical_id.replace(":", "_").replace("-", "_")


def _ast_input_fields(ast: Any, *, _depth: int = 0, _max_depth: int = 64) -> set[str]:
    """AST 가 참조하는 모든 field 명 정적 추출 (평가 없이 walk).

    factor_evaluator._collect_ast_fields 와 동형이나, factor_pack 의 의존 그래프
    구성용 독립 구현 (evaluator import 회피 — 정적 검증의 self-contained). op-level
    `field` (sum_last_n_quarters / universe-relative op) 와 leaf `field` 모두 수집.
    """
    if _depth > _max_depth:
        raise CyclicDependency(
            f"AST depth exceeds {_max_depth} during static dependency walk "
            f"(corruption 또는 DoS 의심)"
        )
    if not isinstance(ast, dict):
        return set()
    fields: set[str] = set()
    if isinstance(ast.get("field"), str):
        fields.add(ast["field"])
    for key in ("left", "right"):
        child = ast.get(key)
        if isinstance(child, dict):
            fields |= _ast_input_fields(child, _depth=_depth + 1, _max_depth=_max_depth)
    args = ast.get("args")
    if isinstance(args, list):
        for arg in args:
            fields |= _ast_input_fields(arg, _depth=_depth + 1, _max_depth=_max_depth)
    return fields


def _ast_ops(ast: Any, *, _depth: int = 0, _max_depth: int = 64) -> set[str]:
    """AST 의 모든 op 명 정적 추출 (평가 없이 walk) — `_ast_input_fields` 동형.

    빌트인 Composite op 검증(`validate_builtin_no_composite`)용. left/right/args
    재귀로 중첩 expression 의 op 까지 수집.
    """
    if _depth > _max_depth:
        raise CyclicDependency(
            f"AST depth exceeds {_max_depth} during op walk (corruption 의심)"
        )
    if not isinstance(ast, dict):
        return set()
    ops: set[str] = set()
    op = ast.get("op")
    if isinstance(op, str):
        ops.add(op)
    for key in ("left", "right"):
        child = ast.get(key)
        if isinstance(child, dict):
            ops |= _ast_ops(child, _depth=_depth + 1, _max_depth=_max_depth)
    args = ast.get("args")
    if isinstance(args, list):
        for arg in args:
            ops |= _ast_ops(arg, _depth=_depth + 1, _max_depth=_max_depth)
    return ops


# ADR-0022 D2 — universe-상대 (모집단 분포 의존) op 집합. zscore/percentile/
# min_max_scale 만 모집단 분포의 상대 위치를 산출 (factor_evaluator._OP_SHAPES 의
# "universe_unary" shape 와 동일 집합). winsorize/weighted_sum 은 종목-국소이므로
# 제외. ADR-0024 D5 의 universe-상대 field 추출 (`_universe_relative_fields`) 이
# 이 집합을 기준으로 분포 op node 만 골라낸다. evaluator import 회피를 위해
# factor_pack 에 독립 정의 (정적 검증의 self-contained — `_ast_ops` 와 동형 정신).
_UNIVERSE_RELATIVE_OPS: Final[frozenset[str]] = frozenset(
    {"zscore", "percentile", "min_max_scale"}
)


def _universe_relative_fields(ast: Any, *, _depth: int = 0, _max_depth: int = 64) -> set[str]:
    """AST 에서 **universe-상대 op (zscore/percentile/min_max_scale) 의 입력 field**
    만 정적 추출 (ADR-0024 D5).

    `_ast_input_fields` 는 AST 의 *모든* field (종목-국소 입력 포함) 를 모으므로
    소표본 디스클로저에 부적합 — 소표본은 **모집단 분포에 의존하는 universe-상대
    op 의 field** 에만 귀속된다 (종목-국소 field 는 모집단 개념이 없음). 본 헬퍼는
    op == universe-상대 인 node 의 `field` 만 수집한다.

    universe-상대 op (`_OP_SHAPES` 의 "universe_unary" shape) 의 AST 는 `{op, field}`
    형태 — `field` 가 모집단 분포의 factor 명이자 종목값 fetch 키. 중첩 가능
    (예: composite `weighted_sum(percentile(per), zscore(roe))`) 하므로 left/right/
    args 재귀로 모든 분포 op leaf 의 field 를 모은다. universe-상대 op 없는 AST 는
    빈 set (→ 표시층이 sample_size=None, small_sample=False 로 처리, ADR-0024 D5).

    표시 전용 추출 (ADR-0024 D4 c) — 본 헬퍼는 디스클로저 부착용이며 evaluator 의
    산출·result_codes 경로와 무관. `SMALL_SAMPLE_THRESHOLD` 를 참조하지 않는다.
    """
    if _depth > _max_depth:
        raise CyclicDependency(
            f"AST depth exceeds {_max_depth} during universe-relative field walk "
            f"(corruption 의심)"
        )
    if not isinstance(ast, dict):
        return set()
    fields: set[str] = set()
    op = ast.get("op")
    if isinstance(op, str) and op in _UNIVERSE_RELATIVE_OPS:
        field = ast.get("field")
        if isinstance(field, str):
            fields.add(field)
    for key in ("left", "right"):
        child = ast.get(key)
        if isinstance(child, dict):
            fields |= _universe_relative_fields(
                child, _depth=_depth + 1, _max_depth=_max_depth,
            )
    args = ast.get("args")
    if isinstance(args, list):
        for arg in args:
            fields |= _universe_relative_fields(
                arg, _depth=_depth + 1, _max_depth=_max_depth,
            )
    return fields


# ADR-0022 D5 — 빌트인 pack 금지 op. 유니버스-상대(zscore/percentile/min_max_scale)
# + weighted_sum 은 사용자 정의 Composite 전용. winsorize 는 종목-국소 전처리라 제외.
_BUILTIN_FORBIDDEN_OPS: Final[frozenset[str]] = frozenset(
    {"weighted_sum", "zscore", "percentile", "min_max_scale"}
)


def validate_builtin_no_composite(body: dict[str, Any]) -> None:
    """빌트인 pack 에 Composite op 부재 — ADR-0022 D5 / ADR-0007 D5.1 (T73).

    빌트인 pack 의 어떤 factor AST 도 `_BUILTIN_FORBIDDEN_OPS`(유니버스-상대 +
    weighted_sum) 를 쓰지 않음을 강제. `load_builtin_pack` 에서만 호출 —
    community/custom(사용자 정의)은 허용. 위반 시 fail-loud(import 시점 자동 게이트).
    """
    for f in body["factors"]:
        ops = _ast_ops(f["formula"]["ast"])
        forbidden = ops & _BUILTIN_FORBIDDEN_OPS
        if forbidden:
            raise BuiltinCompositeError(
                f"빌트인 factor '{f['canonical_id']}' 가 Composite op "
                f"{sorted(forbidden)} 사용 — ADR-0022 D5 / ADR-0007 D5.1 위반"
                f"(빌트인 multi-factor score 금지). Composite 는 사용자 정의 "
                f"pack(community/custom)에서만."
            )


def validate_acyclic(body: dict[str, Any]) -> None:
    """Factor 의존 그래프의 정적 acyclicity 검증 — ADR-0022 R5/D2.

    pack 내 factor 들의 의존 그래프를 **데이터 없이 정의만으로** 구성하고 순환을
    탐지. factor A 의 formula.inputs (또는 AST) 가 다른 factor B 의 출력 derived
    field (`_derived_field_name(B.canonical_id)`) 를 참조하면 엣지 A→B. 순환
    발견 시 `CyclicDependency` raise (fail-loud — DAG 가 아닌 pack 로드 거부).

    알고리즘: 3-색 DFS (white/gray/black). gray (현재 DFS 스택) 노드로의 back-edge
    가 순환. 위상정렬 대신 DFS 를 쓰는 이유 — 순환 경로 (cycle path) 를 그대로
    에러 메시지에 노출해 디버깅 용이 (어떤 factor 들이 순환인지).

    M1 `db_field_provider._resolving` (런타임 per-evaluation 재진입 가드) 과
    **다른 메커니즘**: 본 검증은 정적 (로드 시 1 회, 평가 안 함), `_resolving`
    은 동적 (평가 중 실제 해소 경로). 정적 검증은 평가되지 않는 (N/A 단축된)
    가지의 순환도 잡는다 (CyclicDependency docstring 참조).
    """
    factors = body["factors"]

    # 1. 출력 derived field 명 → 그 field 를 생산하는 factor canonical_id 의 역인덱스.
    #    같은 derived field 를 둘 이상 factor 가 생산하는 일은 canonical_id 중복
    #    (validate_identity 가 먼저 거부) 이거나 명명 충돌 — 후자는 무결성 위반.
    field_to_producer: dict[str, str] = {}
    for f in factors:
        cid = f["canonical_id"]
        out_field = _derived_field_name(cid)
        if out_field in field_to_producer and field_to_producer[out_field] != cid:
            raise CyclicDependency(
                f"derived field 명 충돌: '{out_field}' 를 factor "
                f"'{field_to_producer[out_field]}' 와 '{cid}' 가 모두 생산 "
                f"(canonical_id 명명 충돌 — 의존 그래프 모호)."
            )
        field_to_producer[out_field] = cid

    # 2. 인접 리스트 — A → B (A 가 B 의 출력 field 를 입력으로 참조).
    #    inputs 와 AST 양쪽을 union (inputs 는 contract, AST 는 실제 참조 —
    #    evaluator 가 둘의 일치를 강제하나, 검증 순서상 여기서 둘 다 본다).
    adjacency: dict[str, set[str]] = {f["canonical_id"]: set() for f in factors}
    for f in factors:
        cid = f["canonical_id"]
        referenced = set(f["formula"]["inputs"]) | _ast_input_fields(f["formula"]["ast"])
        for field_name in referenced:
            producer = field_to_producer.get(field_name)
            # producer is None → primitive DB field (factor 출력 아님) → 엣지 없음.
            # producer == cid → 자기 출력 field 를 자기가 참조 (self-loop cycle).
            if producer is not None:
                adjacency[cid].add(producer)

    # 3. 3-색 DFS 순환 탐지 — 순환 경로를 에러에 노출.
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {cid: WHITE for cid in adjacency}

    def _dfs(node: str, path: list[str]) -> None:
        color[node] = GRAY
        path.append(node)
        for dep in sorted(adjacency[node]):  # sorted — 결정적 탐지 순서.
            if color[dep] == GRAY:
                # back-edge → 순환. dep 부터 현재까지가 순환 경로.
                cycle_start = path.index(dep)
                cycle = path[cycle_start:] + [dep]
                raise CyclicDependency(
                    f"factor 의존 그래프에 순환 (ADR-0022 R5 위반): "
                    f"{' -> '.join(cycle)}. DAG 가 아닌 pack 로드 거부."
                )
            if color[dep] == WHITE:
                _dfs(dep, path)
        path.pop()
        color[node] = BLACK

    for cid in sorted(adjacency):  # sorted — 결정적 진입 순서.
        if color[cid] == WHITE:
            _dfs(cid, [])


def validate_citation(body: dict[str, Any]) -> None:
    """Citation 의무 — ADR-0002 D3.

    Pack level 에 citation 필수 (schema 의 required 로 강제됨). 각 factor 의
    `citation` 은 optional 이나, pack citation 이 없으면 모든 factor 에 의무.
    """
    pack_has_citation = "citation" in body and body["citation"].get("title")
    if pack_has_citation:
        return  # pack citation 으로 cover.

    missing: list[str] = []
    for factor in body["factors"]:
        if "citation" not in factor or not factor["citation"].get("title"):
            missing.append(factor["canonical_id"])
    if missing:
        raise CitationMissing(
            f"citation missing for factors (and no pack-level citation): {missing}"
        )


def validate_forbidden_vocab(body: dict[str, Any]) -> None:
    """Factor name / description / alt_names 가 금지 어휘를 포함하지 않음 — ADR-0007 D4.

    빌트인 pack 의 author 가 실수로 advisory vocabulary 를 넣는 것을 차단.
    factor name 은 SYSTEM scope — 가장 엄격.
    """
    for factor in body["factors"]:
        canonical_id = factor["canonical_id"]
        try:
            assert_clean(factor["name"], scope=CheckScope.SYSTEM,
                         context=f"factor[{canonical_id}].name")
            assert_clean(factor["description"], scope=CheckScope.SYSTEM,
                         context=f"factor[{canonical_id}].description")
            for alt in factor.get("alt_names", []):
                assert_clean(alt, scope=CheckScope.SYSTEM,
                             context=f"factor[{canonical_id}].alt_names")
        except ValueError as exc:
            raise ForbiddenVocabInPack(str(exc)) from exc


def validate_hash(body: dict[str, Any]) -> str:
    """body['content_hash'] 가 계산된 hash 와 일치.

    placeholder hash ('sha256:0...0') 는 build-time 에 채워지는 자리표시 —
    placeholder 면 별도 예외로 알려 dev 시 명확.
    """
    declared = body.get(_HASH_FIELD, "")
    computed = compute_pack_hash(body)
    if declared == "sha256:" + "0" * 64:
        raise HashMismatch(
            f"content_hash is placeholder; run fill_hash() before shipping. "
            f"computed={computed}"
        )
    if declared != computed:
        raise HashMismatch(
            f"declared={declared} computed={computed}"
        )
    return computed


# =============================================================================
# Custom pack 검증 — Factor Lab editor (M2 T71 Phase 1)
# =============================================================================
#
# 빌트인 검증(`load_pack`)과의 차이:
# - Composite op (weighted_sum / zscore / percentile / min_max_scale) **허용** —
#   사용자 정의 pack 은 multi-factor score 를 구성하는 것이 핵심 use-case.
#   ADR-0022 D5 / ADR-0007 D5.1 금지는 빌트인 전용.
# - content_hash **미검증** — editor 에서 실시간 검증 시 hash 가 아직 채워지지
#   않은 상태(placeholder 포함)이거나 없는 경우가 정상. 사용자 정의 pack 의 hash
#   integrity 는 저장/deploy 단계에서 강제 (T71 Phase 2).
# - schema / identity / acyclic / citation / forbidden_vocab 는 동일하게 적용.


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """단일 검증 단계의 실패 정보.

    Attributes:
        stage: 실패한 검증 단계 이름.
               값: "schema" | "identity" | "acyclic" | "citation" | "forbidden_vocab"
        message: 해당 예외의 str(). 내부 상태 기준 — API route 가 응답으로 전달.
    """

    stage: str
    message: str


def validate_custom_pack(body: dict[str, Any]) -> list[ValidationIssue]:
    """사용자 정의 custom pack 을 순차 검증하고 이슈 목록을 반환.

    Factor Lab editor 가 실시간으로 사용자 pack JSON 을 검증하기 위한 helper.
    각 단계의 예외를 `ValidationIssue` 로 수집 — 첫 에러에서 멈추지 않고 가능한
    단계까지 진행 (단, schema 실패 시 이후 단계는 무의미하므로 fail-fast 허용).

    적용 검증 순서:
    1. schema   — JSON schema 구조 검증. 실패 시 이후 단계 스킵 (fail-fast).
    2. identity — canonical_id / uuid 중복.
    3. acyclic  — factor 의존 그래프 정적 순환.
    4. citation — pack / factor-level citation 의무.
    5. forbidden_vocab — factor name / description 의 금지 어휘.

    미적용 (빌트인 전용):
    - validate_builtin_no_composite — custom pack 은 Composite op 허용.
    - validate_hash — 사용자가 editor 에서 hash 를 채우지 않은 상태가 정상.

    Args:
        body: factor pack JSON dict. FastAPI 가 JSON 파싱, validate_schema 가
              구조 검증 — 호출자는 dict 타입 보장만.

    Returns:
        수집된 ValidationIssue 목록. 빈 리스트 = 모든 단계 통과 (valid=True).
    """
    issues: list[ValidationIssue] = []

    # 1. Schema — fail-fast: schema 실패 시 이후 단계(identity/acyclic 등)는
    #    body 구조에 의존하므로 의미 없음. 즉시 반환.
    try:
        validate_schema(body)
    except SchemaValidationError as exc:
        issues.append(ValidationIssue(stage="schema", message=str(exc)))
        return issues

    # 2. Identity — canonical_id / uuid 중복.
    try:
        validate_identity(body)
    except IdentityViolation as exc:
        issues.append(ValidationIssue(stage="identity", message=str(exc)))

    # 3. Acyclic — 의존 그래프 정적 순환. identity 실패 시에도 독립 시도
    #    (중복 canonical_id 없는 경우도 있을 수 있으므로 — 두 단계 모두 수집).
    try:
        validate_acyclic(body)
    except CyclicDependency as exc:
        issues.append(ValidationIssue(stage="acyclic", message=str(exc)))

    # 4. Citation — pack / factor-level citation 의무.
    try:
        validate_citation(body)
    except CitationMissing as exc:
        issues.append(ValidationIssue(stage="citation", message=str(exc)))

    # 5. Forbidden vocabulary — factor name / description 금지 어휘.
    try:
        validate_forbidden_vocab(body)
    except ForbiddenVocabInPack as exc:
        issues.append(ValidationIssue(stage="forbidden_vocab", message=str(exc)))

    return issues


# =============================================================================
# Public entry — load + validate
# =============================================================================

def load_pack(path: Path) -> LoadedPack:
    """주어진 path 의 factor pack 을 모든 단계 검증 후 LoadedPack 반환.

    검증 순서 — fail-fast:
    1. JSON parse
    2. Schema (구조)
    3. Identity (canonical_id / uuid 중복)
    4. Acyclicity (factor 의존 그래프 정적 순환 — ADR-0022 R5/D2)
    5. Citation (의무)
    6. Forbidden vocabulary (factor name 등)
    7. Hash (content_hash 일치 — placeholder 면 별도 예외)

    Acyclicity 는 Identity 직후 — identity 가 canonical_id 중복을 먼저 거부해야
    의존 그래프의 노드 식별 (canonical_id) 이 유일함을 보장.
    """
    with path.open(encoding="utf-8") as f:
        body = json.load(f)

    validate_schema(body)
    validate_identity(body)
    validate_acyclic(body)
    validate_citation(body)
    validate_forbidden_vocab(body)
    computed_hash = validate_hash(body)

    return LoadedPack(
        body=body,
        computed_hash=computed_hash,
        factor_count=len(body["factors"]),
        pack_slug=body["pack_slug"],
        version=body["version"],
    )


def load_builtin_pack(version: str = "1.0.0") -> LoadedPack:
    """빌트인 pack 의 단축 export. 운영 코드에서 fetch.

    `load_pack`(공통 검증) 위에 **빌트인 전용 Composite 금지 게이트**(ADR-0022 D5,
    T73)를 추가 — DEFAULT_PACK import 시점에 자동 강제(빌트인에 Composite op 추가
    시 module import 실패 → 모든 pytest 가 게이트). community/custom 은 미적용.

    Args:
        version: 'speculum-builtin-v{version}.json' 의 version 부분.
    """
    path = _BUILTIN_DIR / f"speculum-builtin-v{version}.json"
    pack = load_pack(path)
    validate_builtin_no_composite(pack.body)
    return pack


# =============================================================================
# Module-level default pack — krx_calendar.DEFAULT_CALENDAR 패턴과 일관
# =============================================================================
#
# M0 single-pack 가정: 운영 시 본 builtin pack 만 active. M2 community pack
# 도입 시 `PackRegistry` 추가 + 본 singleton 의미 재정의 (oracle T30 자문 결정 8).
# snapshot_versions.py 가 본 singleton 의 content_hash / pack_slug / version 참조.

DEFAULT_PACK: Final[LoadedPack] = load_builtin_pack("1.0.0")
"""M0 빌트인 pack — module import 시 1 회 로드. snapshot_versions 의 active pack."""


# =============================================================================
# Build-time utility — placeholder hash 채우기
# =============================================================================

def fill_hash(path: Path) -> str:
    """`content_hash` 를 실제 계산값으로 갱신. 빌트인 pack 빌드 시점 사용.

    개발자가 factor 추가·수정 후 본 함수를 호출하면 hash 가 자동으로 새로 계산되어
    pack 파일에 저장됨. 운영 코드에서는 호출하면 안 됨 — immutable pack 원칙
    (ADR-0002 D4) 위반.

    반환: 새 hash.
    """
    with path.open(encoding="utf-8") as f:
        body = json.load(f)

    new_hash = compute_pack_hash(body)
    body[_HASH_FIELD] = new_hash

    # JCS 와 다른 dump format — 사람이 읽기 좋은 indent.
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(body, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")
    return new_hash


# =============================================================================
# Reference pack 로드 — speculum 제공 참조 정의 (ADR-0023 D8)
# =============================================================================

def load_reference_packs() -> list[LoadedPack]:
    """`builtin-packs/reference/` 의 모든 reference pack 로드 (검증 포함, slug 정렬).

    reference pack 은 빌트인(active universe pack, DEFAULT_PACK)이 아니라 Factor Lab
    에서 **불러와 쓰는 참조 정의**(리츠 `ffo-multiple:reit` 등, ADR-0023 D8 재현성
    경로). `load_pack` 의 전 검증(schema/identity/acyclic/citation/hash)을 거치되
    빌트인 Composite 금지 게이트(`validate_builtin_no_composite`, load_builtin_pack
    전용)는 **미적용** — reference 는 community tier 라 composite 허용(빌트인 0 게이트
    대상 아님). 빌트인 content_hash·DEFAULT_PACK 과 무관(재현성 무영향).

    디렉토리 부재 시 빈 목록(reference pack 미배포 환경 무해).
    """
    if not _REFERENCE_DIR.exists():
        return []
    return [load_pack(path) for path in sorted(_REFERENCE_DIR.glob("*.json"))]
