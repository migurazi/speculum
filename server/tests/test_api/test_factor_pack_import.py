"""POST /api/factor-packs/{import-check,import} — pack 간 identity 충돌 (M2 T75).

ADR-0022 D8 2-pass import:
- import-check (Pass 1): dry-run 충돌 탐지, 자동 적용 0.
- import (Pass 2): resolution 적용. 미해결/canonical-replace/uuid-reuse = 422 fail-loud.

핵심 negative test(silent override 금지, D8.4 / canonical 불가침, D8.2)에 집중.
빌트인 충돌은 DEFAULT_PACK 의 첫 factor canonical_id 를 동적 기준으로 사용.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.factor_pack import DEFAULT_PACK

_BUILTIN_CID: str = DEFAULT_PACK.body["factors"][0]["canonical_id"]
_BUILTIN_UUID: str = DEFAULT_PACK.body["factors"][0]["uuid"]


# =============================================================================
# Fixtures / builders (test_factor_packs_validate.py 패턴 복제)
# =============================================================================

@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


def _factor(
    *, canonical_id: str, uuid_str: str, name_suffix: str = "",
) -> dict[str, Any]:
    return {
        "canonical_id": canonical_id,
        "uuid": uuid_str,
        "name": canonical_id.replace(":", " ") + name_suffix,
        "description": canonical_id + " 설명 for testing",
        "formula": {"ast": {"field": "shares_issued"}, "inputs": ["shares_issued"]},
        "unit": "ratio",
    }


def _pack(factors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "$schema": "https://speculum.dev/schemas/factor-pack-v1.json",
        "type": "factor-pack",
        "pack_slug": "user/testuser-mypack",
        "version": "1.0.0",
        "publisher": "testuser",
        "license": "MIT",
        "created_at": "2026-01-01",
        "citation": {"title": "Test Custom Pack", "publisher": "testuser"},
        "factors": factors,
        "content_hash": "sha256:" + "a" * 64,
    }


# 충돌 없는 새 factor.
def _clean_factor(n: int = 1) -> dict[str, Any]:
    return _factor(
        canonical_id=f"alice:clean-{n}",
        uuid_str=f"00000000-0000-0000-0000-0000000000{n:02d}",
    )


# 빌트인 canonical_id 를 다른 정의로 주장 → canonical 충돌.
def _conflicting_factor() -> dict[str, Any]:
    return _factor(
        canonical_id=_BUILTIN_CID,
        uuid_str="00000000-0000-0000-0000-0000000000f1",
        name_suffix=" MINE",
    )


# =============================================================================
# import-check (Pass 1)
# =============================================================================

def test_import_check_clean_pack_no_conflicts(client: TestClient) -> None:
    """충돌 없는 새 factor → valid=True, conflicts 빈, clean 에 canonical_id."""
    res = client.post(
        "/api/factor-packs/import-check", json={"pack": _pack([_clean_factor(1)])},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    assert body["conflicts"] == []
    assert body["clean"] == ["alice:clean-1"]


def test_import_check_gates_advice_in_pack_description(client: TestClient) -> None:
    """ADR-0032 D4 — 외부 pack 의 pack-level description advice 를 import 시점 차단.

    public-toggle 뿐 아니라 import-check 에서도 pack 공개 메타를 게이트해, advice
    어휘가 editor 에 렌더되기 전에 valid=False(forbidden_vocab)로 막는다.
    """
    pack = _pack([_clean_factor(1)])
    pack["description"] = "추천주 모음 — 지금 매수하세요."  # pack-level advice.
    res = client.post("/api/factor-packs/import-check", json={"pack": pack})
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is False
    assert any(issue["stage"] == "forbidden_vocab" for issue in body["issues"])


def test_import_check_hash_mismatch_flagged(client: TestClient) -> None:
    """ADR-0032 D2 — 선언 content_hash 가 본문과 불일치 → hash_mismatch=True(비차단).

    `_pack` 의 content_hash 는 placeholder("aaa...")라 실제 본문 hash 와 다르다 →
    봉인 파손 진단. valid 는 여전히 True(내용은 검증 통과).
    """
    res = client.post(
        "/api/factor-packs/import-check", json={"pack": _pack([_clean_factor(1)])},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    assert body["hash_mismatch"] is True


def test_import_check_hash_match_not_flagged(client: TestClient) -> None:
    """올바르게 봉인된 pack → hash_mismatch=False."""
    from app.services.factor_pack import compute_pack_hash

    pack = _pack([_clean_factor(1)])
    pack["content_hash"] = compute_pack_hash(pack)  # 본문에 맞게 재봉인.
    res = client.post("/api/factor-packs/import-check", json={"pack": pack})
    assert res.status_code == 200
    assert res.json()["hash_mismatch"] is False


def test_import_check_canonical_conflict_detected(client: TestClient) -> None:
    """빌트인 canonical_id 다른 정의 → conflicts(canonical tier, replace 불가)."""
    res = client.post(
        "/api/factor-packs/import-check", json={"pack": _pack([_conflicting_factor()])},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    assert len(body["conflicts"]) == 1
    conflict = body["conflicts"][0]
    assert conflict["canonical_id"] == _BUILTIN_CID
    assert conflict["existing_tier"] == "canonical"
    assert conflict["allowed_resolutions"] == ["skip", "rename"]  # replace 부재(D8.2).
    assert body["clean"] == []


def test_import_check_mixed_clean_and_conflict(client: TestClient) -> None:
    """충돌 factor + clean factor 혼재 → conflicts/clean 분리."""
    res = client.post(
        "/api/factor-packs/import-check",
        json={"pack": _pack([_conflicting_factor(), _clean_factor(2)])},
    )
    assert res.status_code == 200
    body = res.json()
    assert [c["canonical_id"] for c in body["conflicts"]] == [_BUILTIN_CID]
    assert body["clean"] == ["alice:clean-2"]


def test_import_check_invalid_pack_returns_issues(client: TestClient) -> None:
    """schema 위반 pack → valid=False, issues (충돌 검사 이전)."""
    bad = _pack([_clean_factor(1)])
    del bad["pack_slug"]
    res = client.post("/api/factor-packs/import-check", json={"pack": bad})
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is False
    assert len(body["issues"]) >= 1
    assert body["conflicts"] == []


def test_import_check_uuid_reuse_returns_422(client: TestClient) -> None:
    """빌트인 uuid 를 다른 canonical_id 로 재사용 → 422 (D8.1, resolution 불가)."""
    factor = _factor(canonical_id="alice:hijack", uuid_str=_BUILTIN_UUID)
    res = client.post(
        "/api/factor-packs/import-check", json={"pack": _pack([factor])},
    )
    assert res.status_code == 422


def test_import_check_missing_pack_returns_400(client: TestClient) -> None:
    res = client.post("/api/factor-packs/import-check", json={})
    assert res.status_code == 400


# =============================================================================
# import (Pass 2)
# =============================================================================

def test_import_clean_pack_seals_hash(client: TestClient) -> None:
    """충돌 0 clean pack → valid=True, pack 반환 + content_hash 봉인(유효 형식)."""
    res = client.post(
        "/api/factor-packs/import",
        json={"pack": _pack([_clean_factor(1)]), "resolutions": {}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    assert body["pack"] is not None
    # content_hash 재봉인 — placeholder("a"*64) 가 아닌 실제 계산값.
    assert body["pack"]["content_hash"].startswith("sha256:")
    assert body["pack"]["content_hash"] != "sha256:" + "a" * 64
    assert body["applied"] == []


def test_import_unresolved_conflict_returns_422(client: TestClient) -> None:
    """충돌에 resolution 미제공 → 422 (D8.4 silent override 금지)."""
    res = client.post(
        "/api/factor-packs/import",
        json={"pack": _pack([_conflicting_factor()]), "resolutions": {}},
    )
    assert res.status_code == 422


def test_import_canonical_replace_returns_422(client: TestClient) -> None:
    """canonical 충돌에 replace → 422 CanonicalOverrideForbidden (D8.2 불가침)."""
    res = client.post(
        "/api/factor-packs/import",
        json={
            "pack": _pack([_conflicting_factor()]),
            "resolutions": {_BUILTIN_CID: {"action": "replace"}},
        },
    )
    assert res.status_code == 422


def test_import_canonical_skip_excludes_factor(client: TestClient) -> None:
    """canonical 충돌에 skip → valid=True, 그 factor 제외."""
    res = client.post(
        "/api/factor-packs/import",
        json={
            "pack": _pack([_conflicting_factor(), _clean_factor(2)]),
            "resolutions": {_BUILTIN_CID: {"action": "skip"}},
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    cids = [f["canonical_id"] for f in body["pack"]["factors"]]
    assert _BUILTIN_CID not in cids
    assert "alice:clean-2" in cids
    assert body["applied"] == [
        {"canonical_id": _BUILTIN_CID, "action": "skip", "new_canonical_id": None},
    ]


def test_import_canonical_rename_replaces_id(client: TestClient) -> None:
    """canonical 충돌에 rename → canonical_id 치환, 충돌 해소."""
    res = client.post(
        "/api/factor-packs/import",
        json={
            "pack": _pack([_conflicting_factor()]),
            "resolutions": {
                _BUILTIN_CID: {"action": "rename", "new_canonical_id": "alice:my-per"},
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    cids = [f["canonical_id"] for f in body["pack"]["factors"]]
    assert cids == ["alice:my-per"]


def test_import_rename_without_new_id_returns_422(client: TestClient) -> None:
    """rename 인데 new_canonical_id 누락 → 422 InvalidResolution."""
    res = client.post(
        "/api/factor-packs/import",
        json={
            "pack": _pack([_conflicting_factor()]),
            "resolutions": {_BUILTIN_CID: {"action": "rename"}},
        },
    )
    assert res.status_code == 422


def test_import_rename_into_internal_collision_returns_422(client: TestClient) -> None:
    """rename 대상이 같은 pack 내 다른 factor 명과 겹치면 → 422 (적용 후 재검증).

    충돌 factor(빌트인 cid)를 rename 하되 그 새 이름을 같은 pack 의 clean factor
    (alice:clean-2)와 동일하게 지정 — apply 후 canonical_id 중복이 되므로 무결성
    재검증이 fail-loud (silent 중복 차단).
    """
    res = client.post(
        "/api/factor-packs/import",
        json={
            "pack": _pack([_conflicting_factor(), _clean_factor(2)]),
            "resolutions": {
                _BUILTIN_CID: {"action": "rename", "new_canonical_id": "alice:clean-2"},
            },
        },
    )
    assert res.status_code == 422


def test_import_invalid_pack_returns_issues(client: TestClient) -> None:
    """schema 위반 pack → valid=False, pack=None."""
    bad = _pack([_clean_factor(1)])
    del bad["pack_slug"]
    res = client.post(
        "/api/factor-packs/import", json={"pack": bad, "resolutions": {}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is False
    assert body["pack"] is None
