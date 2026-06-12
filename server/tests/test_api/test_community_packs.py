"""Community pack 공개 목록 endpoint 통합 테스트 — ADR-0028 D3.

테스트 매트릭스 (가드레일 게이트):
- 공개 필터: public 으로 토글한 pack 만 목록에 노출. private 은 누출 0.
- user 무관: 다른 user 가 공개한 pack 도 목록에 포함(공개 목록은 user 비종속).
- 큐레이션 0: 응답 schema 에 다운로드 수/인기/순위/별점 필드 부재(ADR-0028 D3).
- 인증 불요: SPECULUM_USER_ID 무관하게 동일 공개 목록.
- 공개 메타: name(=citation.title) / description / content_hash 노출(import 검증용).
- 정렬: created_at 역순(사실)만.

User 격리는 notes/custom_packs 선례(SPECULUM_USER_ID monkeypatch)를 따른다 —
Fake 모드 + AUTH_SECRET 미설정에서 get_current_user 가 env 의 SYSTEM_USER_ID 를
user 로 해소.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# IDOR/공유 검증용 User B (custom_packs 테스트 선례 — 임의 UUID).
_USER_B = "00000000-0000-0000-0000-0000000000bb"

# 큐레이션 금지 필드 — 응답 어디에도 등장하면 안 됨(ADR-0028 D3, §2.3).
_FORBIDDEN_CURATION_KEYS = frozenset({
    "download_count", "downloads", "popularity", "rank", "ranking",
    "rating", "stars", "score", "top_pick", "featured", "trending",
})


@pytest.fixture
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
        "formula": {
            "ast": {"field": "shares_issued"},
            "inputs": ["shares_issued"],
        },
        "unit": "ratio",
    }


def _pack(
    *,
    pack_slug: str = "user/community-pack",
    version: str = "1.0.0",
    canonical_id: str = "alice:per",
    uuid_str: str = "00000000-0000-0000-0000-000000000001",
    title: str = "Community Test Pack",
    description: str = "공유용 factor 묶음",
) -> dict[str, Any]:
    return {
        "$schema": "https://speculum.dev/schemas/factor-pack-v1.json",
        "type": "factor-pack",
        "pack_slug": pack_slug,
        "version": version,
        "publisher": "testuser",
        "license": "MIT",
        "created_at": "2026-01-01",
        "description": description,
        "citation": {"title": title, "publisher": "testuser"},
        "factors": [_factor(canonical_id=canonical_id, uuid_str=uuid_str)],
        "content_hash": "sha256:" + "a" * 64,
    }


def _save(client: TestClient, pack: dict[str, Any]) -> str:
    res = client.post("/api/factor-packs/saved", json={"pack": pack})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _make_public(client: TestClient, pack_id: str) -> None:
    res = client.patch(
        f"/api/factor-packs/saved/{pack_id}/visibility",
        json={"visibility": "public"},
    )
    assert res.status_code == 200, res.text


# =============================================================================
# 공개 필터 — public 만, private 누출 0
# =============================================================================


def test_community_empty_when_nothing_public(client: TestClient) -> None:
    """공개 pack 0 → 빈 목록."""
    _save(client, _pack())  # private 저장만.
    res = client.get("/api/factor-packs/community")
    assert res.status_code == 200
    body = res.json()
    assert body["packs"] == []
    assert body["total"] == 0


def test_community_lists_only_public(client: TestClient) -> None:
    """public 토글한 pack 만 노출 — private 은 누출 0."""
    pub_id = _save(client, _pack(pack_slug="user/pub", canonical_id="a:pub",
                                 uuid_str="00000000-0000-0000-0000-0000000000a1"))
    _save(client, _pack(pack_slug="user/priv", canonical_id="a:priv",
                        uuid_str="00000000-0000-0000-0000-0000000000a2"))
    _make_public(client, pub_id)

    body = client.get("/api/factor-packs/community").json()
    slugs = {p["pack_slug"] for p in body["packs"]}
    assert slugs == {"user/pub"}
    assert body["total"] == 1


def test_community_public_meta_fields(client: TestClient) -> None:
    """공개 메타 — name(=citation.title)/description/content_hash 노출(import 검증용)."""
    pid = _save(client, _pack(title="Value Pack", description="가치 factor"))
    _make_public(client, pid)
    pack = client.get("/api/factor-packs/community").json()["packs"][0]
    assert pack["name"] == "Value Pack"
    assert pack["description"] == "가치 factor"
    assert pack["content_hash"].startswith("sha256:")
    assert pack["factor_count"] == 1
    assert pack["pack_slug"] == "user/community-pack"
    assert pack["version"] == "1.0.0"


# =============================================================================
# user 무관 — 다른 user 공개 pack 포함
# =============================================================================


def test_community_includes_other_users_public(
    client: TestClient, monkeypatch,
) -> None:
    """다른 user 가 공개한 pack 도 목록에 포함 — 공개 목록은 user 비종속."""
    # User A 가 공개.
    a_id = _save(client, _pack(pack_slug="user/a-pub", canonical_id="a:p",
                               uuid_str="00000000-0000-0000-0000-0000000000a1"))
    _make_public(client, a_id)

    # User B 가 공개.
    monkeypatch.setenv("SPECULUM_USER_ID", _USER_B)
    b_id = _save(client, _pack(pack_slug="user/b-pub", canonical_id="b:p",
                               uuid_str="00000000-0000-0000-0000-0000000000b1"))
    _make_public(client, b_id)

    # B 컨텍스트에서도 A·B 둘 다 보임(인증 불요·user 무관).
    body = client.get("/api/factor-packs/community").json()
    slugs = {p["pack_slug"] for p in body["packs"]}
    assert slugs == {"user/a-pub", "user/b-pub"}
    assert body["total"] == 2

    # A 컨텍스트에서도 동일(공개 목록은 viewer user 무관).
    monkeypatch.delenv("SPECULUM_USER_ID", raising=False)
    body_a = client.get("/api/factor-packs/community").json()
    assert {p["pack_slug"] for p in body_a["packs"]} == {"user/a-pub", "user/b-pub"}


# =============================================================================
# 큐레이션 0 — 다운로드/인기/순위/별점 필드 부재
# =============================================================================


def test_community_no_curation_fields(client: TestClient) -> None:
    """응답 schema 에 큐레이션 신호 필드 0(ADR-0028 D3 — §2.3)."""
    pid = _save(client, _pack())
    _make_public(client, pid)
    body = client.get("/api/factor-packs/community").json()
    # list-level 에 인기/순위 메타 없음 — packs/total 만.
    assert set(body.keys()) == {"packs", "total"}
    pack = body["packs"][0]
    # item-level 에 다운로드 수/인기/순위/별점 필드 0.
    assert _FORBIDDEN_CURATION_KEYS.isdisjoint(pack.keys())


def test_community_no_auth_required(client: TestClient) -> None:
    """인증 불요 — 공개 목록은 로그인 무관(200)."""
    res = client.get("/api/factor-packs/community")
    assert res.status_code == 200


# =============================================================================
# ADR-0028 D4 — 공개 pack 단건 body fetch + import 재사용
# =============================================================================


def test_community_body_fetch_public_only(client: TestClient) -> None:
    """공개 pack 단건 body — 공개만 200, 비공개/미존재 404(누출 0)."""
    pid = _save(client, _pack(pack_slug="user/shared"))
    # private 상태 — 단건 body fetch 404(존재 여부 노출 차단).
    res_priv = client.get(
        "/api/factor-packs/community/user/shared/versions/1.0.0",
    )
    assert res_priv.status_code == 404
    _make_public(client, pid)
    res = client.get(
        "/api/factor-packs/community/user/shared/versions/1.0.0",
    )
    assert res.status_code == 200
    body = res.json()
    # 전체 body + 봉인 content_hash(import 검증 재료).
    assert body["pack"]["pack_slug"] == "user/shared"
    assert body["pack"]["content_hash"] == body["content_hash"]
    assert body["name"] == "Community Test Pack"


def test_community_body_fetch_nonexistent_404(client: TestClient) -> None:
    res = client.get(
        "/api/factor-packs/community/user/none/versions/1.0.0",
    )
    assert res.status_code == 404


def test_community_pack_importable_via_existing_flow(client: TestClient) -> None:
    """D4 재사용 — 공개 pack body 를 기존 import-check/import 에 그대로 투입 가능.

    community 공개 pack 의 전체 body 를 fetch → 기존 stateless import-check 흐름에
    넣으면 valid(충돌 없으면 clean) 로 통과한다. 공유 surface 는 body fetch 만
    추가하고 충돌 resolve/봉인은 재사용(silent merge 금지 유지).
    """
    pid = _save(client, _pack(pack_slug="user/importable", canonical_id="z:per",
                              uuid_str="00000000-0000-0000-0000-0000000000c1"))
    _make_public(client, pid)
    fetched = client.get(
        "/api/factor-packs/community/user/importable/versions/1.0.0",
    ).json()

    # 기존 import-check 흐름(stateless) 에 공개 body 그대로 투입 — 재사용 증명.
    check = client.post(
        "/api/factor-packs/import-check", json={"pack": fetched["pack"]},
    )
    assert check.status_code == 200
    body = check.json()
    assert body["valid"] is True
    # 빌트인과 canonical_id 충돌 없는 custom factor → clean(명시 매핑 불필요).
    assert "z:per" in body["clean"]
