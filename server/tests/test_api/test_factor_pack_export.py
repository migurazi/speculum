"""POST /api/factor-packs/export — self-identifying export (M2 T75 — ADR-0022 D8.5).

검증:
- export wrapper marker + tier(pack_slug 도출) + content_hash 봉인.
- 빌트인(canonical) tier 는 export 거부(repo 에 이미 존재).
- 무효 pack export 거부.
- round-trip: export 한 pack 을 import-check → 충돌 0(자기 정의).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.factor_packs import (
    FACTOR_PACK_EXPORT_MARKER,
    FACTOR_PACK_EXPORT_SCHEMA_VERSION,
)


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


def _factor(*, canonical_id: str, uuid_str: str) -> dict[str, Any]:
    return {
        "canonical_id": canonical_id,
        "uuid": uuid_str,
        "name": canonical_id.replace(":", " "),
        "description": canonical_id + " 설명 for testing",
        "formula": {"ast": {"field": "shares_issued"}, "inputs": ["shares_issued"]},
        "unit": "ratio",
    }


def _pack(pack_slug: str = "user/testuser-mypack") -> dict[str, Any]:
    return {
        "$schema": "https://speculum.dev/schemas/factor-pack-v1.json",
        "type": "factor-pack",
        "pack_slug": pack_slug,
        "version": "1.0.0",
        "publisher": "testuser",
        "license": "MIT",
        "created_at": "2026-01-01",
        "citation": {"title": "Test Custom Pack", "publisher": "testuser"},
        "factors": [
            _factor(canonical_id="alice:per",
                    uuid_str="00000000-0000-0000-0000-000000000001"),
        ],
        "content_hash": "sha256:" + "a" * 64,  # placeholder — export 가 재봉인.
    }


def test_export_user_pack_wraps_and_seals(client: TestClient) -> None:
    """user pack export → marker wrapper + tier=user + content_hash 재봉인."""
    res = client.post("/api/factor-packs/export", json={"pack": _pack()})
    assert res.status_code == 200
    body = res.json()
    assert body["export_format"] == FACTOR_PACK_EXPORT_MARKER
    assert body["pack_schema_version"] == FACTOR_PACK_EXPORT_SCHEMA_VERSION
    assert body["tier"] == "custom"
    # content_hash 봉인 — placeholder 가 아닌 실제 계산값(수입측 정의 판정 기준, D8.5).
    sealed = body["pack"]["content_hash"]
    assert sealed.startswith("sha256:")
    assert sealed != "sha256:" + "a" * 64


def test_export_community_pack_tier(client: TestClient) -> None:
    """community/* slug → tier=community."""
    res = client.post(
        "/api/factor-packs/export", json={"pack": _pack("community/acme-value")},
    )
    assert res.status_code == 200
    assert res.json()["tier"] == "community"


def test_export_builtin_slug_rejected(client: TestClient) -> None:
    """빌트인(canonical) slug → 422 (repo 에 이미 존재, export 무의미)."""
    res = client.post(
        "/api/factor-packs/export", json={"pack": _pack("speculum-builtin")},
    )
    assert res.status_code == 422


def test_export_invalid_pack_rejected(client: TestClient) -> None:
    """schema 위반 pack → 422 (유효 pack 만 export)."""
    bad = _pack()
    del bad["pack_slug"]
    res = client.post("/api/factor-packs/export", json={"pack": bad})
    assert res.status_code == 422


def test_export_missing_pack_returns_400(client: TestClient) -> None:
    res = client.post("/api/factor-packs/export", json={})
    assert res.status_code == 400


def test_export_then_import_check_roundtrip_no_conflict(client: TestClient) -> None:
    """export 한 pack 을 import-check → 충돌 0 (자기 정의는 빌트인과 무관)."""
    exported = client.post("/api/factor-packs/export", json={"pack": _pack()})
    assert exported.status_code == 200
    sealed_pack = exported.json()["pack"]
    check = client.post(
        "/api/factor-packs/import-check", json={"pack": sealed_pack},
    )
    assert check.status_code == 200
    body = check.json()
    assert body["valid"] is True
    assert body["conflicts"] == []
    assert body["clean"] == ["alice:per"]
