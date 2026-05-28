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
# Public entry — load + validate
# =============================================================================

def load_pack(path: Path) -> LoadedPack:
    """주어진 path 의 factor pack 을 모든 단계 검증 후 LoadedPack 반환.

    검증 순서 — fail-fast:
    1. JSON parse
    2. Schema (구조)
    3. Identity (canonical_id / uuid 중복)
    4. Citation (의무)
    5. Forbidden vocabulary (factor name 등)
    6. Hash (content_hash 일치 — placeholder 면 별도 예외)
    """
    with path.open(encoding="utf-8") as f:
        body = json.load(f)

    validate_schema(body)
    validate_identity(body)
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

    Args:
        version: 'speculum-builtin-v{version}.json' 의 version 부분.
    """
    path = _BUILTIN_DIR / f"speculum-builtin-v{version}.json"
    return load_pack(path)


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
