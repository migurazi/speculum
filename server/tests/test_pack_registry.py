"""PackRegistry Phase 2a 단위 테스트 (ADR-0025 D1/D2/D5).

빌트인/reference resolve + 프로세스 캐시 + 미지원 custom None. content_hash 검증은
load_builtin_pack/load_pack 가 수행하므로 본 테스트는 resolve 경로의 정합성만 검증.
"""

from __future__ import annotations

from app.services.factor_pack import DEFAULT_PACK, load_reference_packs
from app.services.pack_registry import BUILTIN_PACK_SLUG, PackRegistry

# =============================================================================
# 1. 빌트인 resolve
# =============================================================================

def test_resolve_builtin_returns_default_pack_equivalent() -> None:
    """빌트인 slug/version resolve → DEFAULT_PACK 과 동일 content_hash."""
    reg = PackRegistry()
    pack = reg.resolve(BUILTIN_PACK_SLUG, "1.0.0")
    assert pack is not None
    assert pack.pack_slug == DEFAULT_PACK.pack_slug
    assert pack.version == DEFAULT_PACK.version
    assert pack.computed_hash == DEFAULT_PACK.computed_hash


def test_builtin_slug_constant_matches_default_pack() -> None:
    """BUILTIN_PACK_SLUG 상수가 실제 빌트인 pack_slug 와 일치 (회귀 가드)."""
    assert BUILTIN_PACK_SLUG == DEFAULT_PACK.pack_slug


# =============================================================================
# 2. 프로세스 캐시 — 같은 (slug, version) → 동일 인스턴스
# =============================================================================

def test_resolve_caches_builtin_instance() -> None:
    """같은 (slug, version) 반복 resolve → 동일 LoadedPack 인스턴스 (캐시)."""
    reg = PackRegistry()
    a = reg.resolve(BUILTIN_PACK_SLUG, "1.0.0")
    b = reg.resolve(BUILTIN_PACK_SLUG, "1.0.0")
    assert a is b


def test_separate_registries_do_not_share_cache() -> None:
    """레지스트리 인스턴스마다 독립 캐시 (전역 가변 상태 없음)."""
    reg1 = PackRegistry()
    reg2 = PackRegistry()
    a = reg1.resolve(BUILTIN_PACK_SLUG, "1.0.0")
    b = reg2.resolve(BUILTIN_PACK_SLUG, "1.0.0")
    # 값은 동등하나 인스턴스 캐시는 분리 (각자 1 회 로드).
    assert a is not None and b is not None
    assert a.computed_hash == b.computed_hash


# =============================================================================
# 3. reference resolve — 배포 환경에서만 매칭, 미배포면 빈 index
# =============================================================================

def test_resolve_reference_matches_loaded_reference_packs() -> None:
    """reference pack 이 배포돼 있으면 그 slug/version 으로 resolve 가능.

    미배포 환경(reference 디렉토리 부재/빈)이면 load_reference_packs 가 빈 목록 →
    resolve None — 두 경우 모두 정상(skip 없이 분기 검증).
    """
    reg = PackRegistry()
    refs = load_reference_packs()
    if not refs:
        # 미배포 환경 — 임의 reference slug 는 None.
        assert reg.resolve("any-reference-slug", "1.0.0") is None
        return
    first = refs[0]
    resolved = reg.resolve(first.pack_slug, first.version)
    assert resolved is not None
    assert resolved.computed_hash == first.computed_hash
    # 캐시 — 동일 인스턴스.
    assert reg.resolve(first.pack_slug, first.version) is resolved


# =============================================================================
# 4. 미지원 custom slug / 미존재 → None (NotImplementedError 금지)
# =============================================================================

def test_resolve_unknown_custom_slug_returns_none() -> None:
    """custom slug (Phase 2b deferred) → None (예외 아님)."""
    reg = PackRegistry()
    assert reg.resolve("user-custom-abc123", "1.0.0") is None


def test_resolve_nonexistent_version_returns_none() -> None:
    """빌트인은 아니고 reference 에도 없는 (slug, version) → None."""
    reg = PackRegistry()
    assert reg.resolve("nonexistent-pack", "9.9.9") is None


def test_resolve_accepts_phase2b_args_without_using_them() -> None:
    """user_id / custom_pack_repo 인자를 받되 Phase 2b 미구현 → None.

    시그니처 안정성 — custom resolve 인자가 있어도 빌트인은 정상 resolve, 미지원
    custom slug 는 None (예외 없음).
    """
    reg = PackRegistry()
    builtin = reg.resolve(
        BUILTIN_PACK_SLUG, "1.0.0", user_id="u1", custom_pack_repo=None,
    )
    assert builtin is not None
    custom = reg.resolve(
        "user-custom-xyz", "1.0.0", user_id="u1", custom_pack_repo=None,
    )
    assert custom is None


# =============================================================================
# Phase 2b — custom resolve (user_id + custom_pack_repo)
# =============================================================================

def _custom_pack_body(slug: str = "user/test-registry", version: str = "1.0.0") -> dict:
    """content_hash 봉인된 최소 custom pack body (registry custom resolve 테스트용)."""
    from app.services.factor_pack import compute_pack_hash

    body: dict = {
        "$schema": "https://speculum.dev/schemas/factor-pack-v1.json",
        "type": "factor-pack",
        "pack_slug": slug,
        "version": version,
        "publisher": "test",
        "license": "MIT",
        "created_at": "2026-01-01",
        "citation": {"title": "Test Registry Pack", "publisher": "test"},
        "factors": [{
            "canonical_id": "my:f1",
            "uuid": "10000000-0000-0000-0000-000000000001",
            "name": "f1",
            "description": "registry custom resolve 테스트 factor",
            "formula": {"ast": {"field": "shares_issued"}, "inputs": ["shares_issued"]},
            "unit": "ratio",
        }],
        "content_hash": "sha256:" + "0" * 64,
    }
    body["content_hash"] = compute_pack_hash(body)
    return body


def test_resolve_custom_pack_loads_saved() -> None:
    """저장 custom pack (user_id+slug+version) → LoadedPack (ADR-0025 D1 Phase 2b)."""
    from uuid import uuid4

    from app.repositories.custom_pack_repository import FakeCustomPackRepository

    user_id = uuid4()
    repo = FakeCustomPackRepository()
    repo.save(user_id=user_id, body=_custom_pack_body())
    pack = PackRegistry().resolve(
        "user/test-registry", "1.0.0", user_id=user_id, custom_pack_repo=repo,
    )
    assert pack is not None
    assert pack.pack_slug == "user/test-registry"
    assert pack.version == "1.0.0"
    assert pack.factor_count == 1


def test_resolve_custom_idor_other_user_none() -> None:
    """타 user 의 custom pack → None (IDOR — user 격리, ADR-0025 D1)."""
    from uuid import uuid4

    from app.repositories.custom_pack_repository import FakeCustomPackRepository

    owner = uuid4()
    other = uuid4()
    repo = FakeCustomPackRepository()
    repo.save(user_id=owner, body=_custom_pack_body())
    pack = PackRegistry().resolve(
        "user/test-registry", "1.0.0", user_id=other, custom_pack_repo=repo,
    )
    assert pack is None


def test_resolve_custom_not_cached() -> None:
    """custom resolve 는 프로세스 캐시 미사용 (user 격리·DB 일관성, ADR-0025 D1)."""
    from uuid import uuid4

    from app.repositories.custom_pack_repository import FakeCustomPackRepository

    user_id = uuid4()
    repo = FakeCustomPackRepository()
    repo.save(user_id=user_id, body=_custom_pack_body())
    reg = PackRegistry()
    reg.resolve("user/test-registry", "1.0.0", user_id=user_id, custom_pack_repo=repo)
    # 빌트인/reference 와 달리 custom 은 _cache 키에 들어가지 않음.
    assert ("user/test-registry", "1.0.0") not in reg._cache


def test_resolve_custom_tampered_body_raises() -> None:
    """DB body 변조(content_hash 불일치) → HashMismatch (ADR-0022 D9.2 변조 탐지)."""
    from datetime import UTC, datetime
    from uuid import uuid4

    import pytest

    from app.repositories.custom_pack_repository import (
        CustomPack,
        FakeCustomPackRepository,
    )
    from app.services.factor_pack import HashMismatch

    user_id = uuid4()
    body = _custom_pack_body()
    # content_hash 는 원본 봉인값 유지, body 의 factor name 만 변조 → hash 불일치.
    tampered = {**body, "factors": [{**body["factors"][0], "name": "TAMPERED"}]}
    repo = FakeCustomPackRepository()
    repo._packs[uuid4()] = CustomPack(
        id=uuid4(), user_id=user_id, pack_slug="user/test-registry",
        version="1.0.0", content_hash=body["content_hash"], body=tampered,
        factor_count=1, created_at=datetime.now(UTC),
    )
    with pytest.raises(HashMismatch):
        PackRegistry().resolve(
            "user/test-registry", "1.0.0", user_id=user_id, custom_pack_repo=repo,
        )
