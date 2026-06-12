"""Custom Pack 영속화 endpoint 통합 테스트 — ADR-0022 D9.

테스트 매트릭스 (절대 원칙 게이트):
- IDOR(P0): user A 저장 → user B get/list/delete 불가(404/빈).
- immutable append-only: 같은 (slug,version) 다른 정의 → 409, 같은 정의 →
  idempotent(201). UPDATE endpoint 부재.
- hash freeze: 저장 시 클라이언트 위조 content_hash 무시·재봉인. load 시 정합.
- 검증: invalid pack(schema/identity) 422. canonical tier(speculum-builtin) 422.
- 회귀 0: AUTH_SECRET 미설정 fallback 에서 SYSTEM_USER_ID 저장(기존 무영향).

User 격리는 notes 선례(SPECULUM_USER_ID monkeypatch)를 따른다 — Fake 모드 +
AUTH_SECRET 미설정에서 get_current_user 가 env 의 SYSTEM_USER_ID 를 user 로 해소.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# IDOR 검증용 User B (notes 테스트 선례 — 임의 UUID).
_USER_B = "00000000-0000-0000-0000-0000000000bb"


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
    pack_slug: str = "user/testuser-mypack",
    version: str = "1.0.0",
    canonical_id: str = "alice:per",
    uuid_str: str = "00000000-0000-0000-0000-000000000001",
    content_hash: str = "sha256:" + "a" * 64,
) -> dict[str, Any]:
    return {
        "$schema": "https://speculum.dev/schemas/factor-pack-v1.json",
        "type": "factor-pack",
        "pack_slug": pack_slug,
        "version": version,
        "publisher": "testuser",
        "license": "MIT",
        "created_at": "2026-01-01",
        "citation": {"title": "Test Custom Pack", "publisher": "testuser"},
        "factors": [_factor(canonical_id=canonical_id, uuid_str=uuid_str)],
        # placeholder hash — save 가 무시·재봉인.
        "content_hash": content_hash,
    }


# =============================================================================
# save happy path + hash freeze
# =============================================================================

def test_save_returns_meta_and_seals_hash(client: TestClient) -> None:
    """저장 → 메타 반환 + content_hash 재봉인(클라이언트 위조값 무시)."""
    res = client.post("/api/factor-packs/saved", json={"pack": _pack()})
    assert res.status_code == 201
    body = res.json()
    assert body["pack_slug"] == "user/testuser-mypack"
    assert body["version"] == "1.0.0"
    assert body["factor_count"] == 1
    # 클라이언트가 보낸 placeholder hash 가 아니라 재계산값으로 봉인.
    assert body["content_hash"].startswith("sha256:")
    assert body["content_hash"] != "sha256:" + "a" * 64
    assert "id" in body


def test_save_then_load_returns_full_body_with_valid_hash(
    client: TestClient,
) -> None:
    """저장 후 단건 불러오기 → 전체 body + 봉인 hash 정합(변조 탐지 없음)."""
    saved = client.post(
        "/api/factor-packs/saved", json={"pack": _pack()},
    ).json()
    pack_id = saved["id"]

    res = client.get(f"/api/factor-packs/saved/{pack_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == pack_id
    # 전체 body 포함 + content_hash 가 저장 메타와 일치.
    assert body["pack"]["pack_slug"] == "user/testuser-mypack"
    assert body["pack"]["content_hash"] == saved["content_hash"]
    assert body["content_hash"] == saved["content_hash"]


def test_save_ignores_client_forged_hash(client: TestClient) -> None:
    """서로 다른 위조 hash 두 번 저장 → 같은 봉인 hash(정의 동일 → idempotent)."""
    first = client.post(
        "/api/factor-packs/saved",
        json={"pack": _pack(content_hash="sha256:" + "c" * 64)},
    ).json()
    second = client.post(
        "/api/factor-packs/saved",
        json={"pack": _pack(content_hash="sha256:" + "d" * 64)},
    ).json()
    # 위조 hash 와 무관하게 정의가 같으므로 같은 봉인 hash + idempotent(같은 id).
    assert first["content_hash"] == second["content_hash"]
    assert first["id"] == second["id"]


# =============================================================================
# immutable append-only
# =============================================================================

def test_save_idempotent_same_definition(client: TestClient) -> None:
    """같은 (slug, version) 같은 정의 재저장 → 201 + 기존 메타(신규 0)."""
    first = client.post(
        "/api/factor-packs/saved", json={"pack": _pack()},
    ).json()
    res2 = client.post("/api/factor-packs/saved", json={"pack": _pack()})
    assert res2.status_code == 201
    assert res2.json()["id"] == first["id"]
    # 목록 1 건.
    lst = client.get("/api/factor-packs/saved").json()
    assert lst["total"] == 1


def test_save_conflict_same_version_different_definition(
    client: TestClient,
) -> None:
    """같은 (slug, version) 다른 정의 → 409(append-only 위반)."""
    client.post("/api/factor-packs/saved", json={"pack": _pack()})
    # 같은 slug+version, 다른 canonical_id/uuid → 다른 정의 → 409.
    other = _pack(
        canonical_id="bob:roe",
        uuid_str="00000000-0000-0000-0000-000000000002",
    )
    res = client.post("/api/factor-packs/saved", json={"pack": other})
    assert res.status_code == 409


def test_save_new_version_coexists(client: TestClient) -> None:
    """같은 slug 다른 version → 별개 저장(append-only 새 버전)."""
    client.post("/api/factor-packs/saved", json={"pack": _pack(version="1.0.0")})
    res = client.post(
        "/api/factor-packs/saved",
        json={"pack": _pack(version="1.1.0", canonical_id="alice:per2",
                            uuid_str="00000000-0000-0000-0000-000000000009")},
    )
    assert res.status_code == 201
    lst = client.get("/api/factor-packs/saved").json()
    assert lst["total"] == 2


def test_no_update_endpoint(client: TestClient) -> None:
    """append-only — PUT/PATCH endpoint 부재(405)."""
    saved = client.post(
        "/api/factor-packs/saved", json={"pack": _pack()},
    ).json()
    pack_id = saved["id"]
    assert client.put(
        f"/api/factor-packs/saved/{pack_id}", json={"pack": _pack()},
    ).status_code == 405
    assert client.patch(
        f"/api/factor-packs/saved/{pack_id}", json={"pack": _pack()},
    ).status_code == 405


# =============================================================================
# 검증 — invalid pack / canonical tier
# =============================================================================

def test_save_invalid_pack_schema_422(client: TestClient) -> None:
    """schema 위반 pack → 422."""
    bad = _pack()
    del bad["pack_slug"]
    res = client.post("/api/factor-packs/saved", json={"pack": bad})
    assert res.status_code == 422


def test_save_canonical_tier_rejected(client: TestClient) -> None:
    """빌트인(canonical) slug → 422(사칭 차단)."""
    res = client.post(
        "/api/factor-packs/saved",
        json={"pack": _pack(pack_slug="speculum-builtin")},
    )
    assert res.status_code == 422


def test_save_missing_pack_field_422(client: TestClient) -> None:
    """`pack` 필드 누락 → 422(Pydantic CustomPackSaveIn)."""
    res = client.post("/api/factor-packs/saved", json={})
    assert res.status_code == 422


# =============================================================================
# IDOR (P0)
# =============================================================================

def test_idor_other_user_cannot_see_or_mutate(
    client: TestClient, monkeypatch,
) -> None:
    """User A 저장 → User B list 빈 + get/delete 404."""
    saved = client.post(
        "/api/factor-packs/saved", json={"pack": _pack()},
    ).json()
    pack_id = saved["id"]

    # User B 로 전환.
    monkeypatch.setenv("SPECULUM_USER_ID", _USER_B)

    # B 의 list — 빈(user_id predicate 가 A pack 차단).
    res_list = client.get("/api/factor-packs/saved")
    assert res_list.status_code == 200
    assert res_list.json()["items"] == []
    assert res_list.json()["total"] == 0

    # B 가 A 의 pack get → 404(owner mismatch).
    assert client.get(f"/api/factor-packs/saved/{pack_id}").status_code == 404
    # B 가 A 의 pack delete → 404.
    assert client.delete(f"/api/factor-packs/saved/{pack_id}").status_code == 404


def test_idor_list_does_not_leak_other_user(
    client: TestClient, monkeypatch,
) -> None:
    """A·B 각각 저장 — 각자 list 는 자기 것만(user_id predicate 누락 회귀 게이트)."""
    client.post(
        "/api/factor-packs/saved", json={"pack": _pack(pack_slug="user/a-pack")},
    )

    monkeypatch.setenv("SPECULUM_USER_ID", _USER_B)
    client.post(
        "/api/factor-packs/saved", json={"pack": _pack(pack_slug="user/b-pack")},
    )

    # B 의 list — B 것만.
    res_b = client.get("/api/factor-packs/saved").json()
    assert res_b["total"] == 1
    assert res_b["items"][0]["pack_slug"] == "user/b-pack"

    # A 로 복귀 — A 것만.
    monkeypatch.delenv("SPECULUM_USER_ID", raising=False)
    res_a = client.get("/api/factor-packs/saved").json()
    assert res_a["total"] == 1
    assert res_a["items"][0]["pack_slug"] == "user/a-pack"


# =============================================================================
# CRUD lifecycle + 404
# =============================================================================

def test_delete_then_gone(client: TestClient) -> None:
    saved = client.post(
        "/api/factor-packs/saved", json={"pack": _pack()},
    ).json()
    pack_id = saved["id"]
    assert client.delete(f"/api/factor-packs/saved/{pack_id}").status_code == 204
    assert client.get(f"/api/factor-packs/saved/{pack_id}").status_code == 404
    assert client.get("/api/factor-packs/saved").json()["total"] == 0


def test_get_nonexistent_404(client: TestClient) -> None:
    assert client.get(
        f"/api/factor-packs/saved/{uuid4()}",
    ).status_code == 404


def test_delete_nonexistent_404(client: TestClient) -> None:
    assert client.delete(
        f"/api/factor-packs/saved/{uuid4()}",
    ).status_code == 404


# =============================================================================
# ADR-0028 D1/D2 — visibility 공유 게이트
# =============================================================================


def test_save_defaults_visibility_private(client: TestClient) -> None:
    """저장 직후 visibility=='private'(공유는 명시 토글만)."""
    saved = client.post(
        "/api/factor-packs/saved", json={"pack": _pack()},
    ).json()
    assert saved["visibility"] == "private"


def test_visibility_public_then_private_toggle(client: TestClient) -> None:
    """clean pack → public 전환 200 + body/hash 불변, 다시 private 토글."""
    saved = client.post(
        "/api/factor-packs/saved", json={"pack": _pack()},
    ).json()
    pack_id = saved["id"]
    res = client.patch(
        f"/api/factor-packs/saved/{pack_id}/visibility",
        json={"visibility": "public"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["visibility"] == "public"
    # body/content_hash 불변(ADR-0020).
    assert body["content_hash"] == saved["content_hash"]
    assert body["pack"]["content_hash"] == saved["content_hash"]
    # 다시 private 으로.
    res2 = client.patch(
        f"/api/factor-packs/saved/{pack_id}/visibility",
        json={"visibility": "private"},
    )
    assert res2.status_code == 200
    assert res2.json()["visibility"] == "private"


def test_visibility_public_forbidden_words_in_name_422(
    client: TestClient,
) -> None:
    """공개 메타(citation.title)에 금지어휘 → public 전환 422(공유 거부)."""
    bad = _pack()
    # name(=citation.title)에 금지어휘 — USER_SHARED 게이트 검사 대상.
    bad["citation"] = {"title": "Strong Buy Pack", "publisher": "testuser"}
    saved = client.post(
        "/api/factor-packs/saved", json={"pack": bad},
    ).json()
    pack_id = saved["id"]
    res = client.patch(
        f"/api/factor-packs/saved/{pack_id}/visibility",
        json={"visibility": "public"},
    )
    assert res.status_code == 422
    # 어휘 echo 차단 — detail 에 검출 어휘 미노출(generic message).
    assert "Strong Buy" not in res.text
    # 거부됐으므로 여전히 private.
    after = client.get(f"/api/factor-packs/saved/{pack_id}").json()
    assert after["visibility"] == "private"


def test_visibility_public_forbidden_words_in_description_422(
    client: TestClient,
) -> None:
    """공개 메타(description)에 금지어휘 → public 전환 422(공유 거부)."""
    bad = _pack()
    bad["description"] = "이 pack 은 Top Pick 입니다."
    saved = client.post(
        "/api/factor-packs/saved", json={"pack": bad},
    ).json()
    pack_id = saved["id"]
    res = client.patch(
        f"/api/factor-packs/saved/{pack_id}/visibility",
        json={"visibility": "public"},
    )
    assert res.status_code == 422
    assert "Top Pick" not in res.text


def test_visibility_private_skips_forbidden_check(client: TestClient) -> None:
    """private 전환은 USER_SHARED 게이트 없음 — 금지어휘 pack 도 private 유지 OK."""
    bad = _pack()
    bad["citation"] = {"title": "Strong Buy Pack", "publisher": "testuser"}
    saved = client.post(
        "/api/factor-packs/saved", json={"pack": bad},
    ).json()
    pack_id = saved["id"]
    # private → private 은 검사 skip(공유 surface 진입 아님).
    res = client.patch(
        f"/api/factor-packs/saved/{pack_id}/visibility",
        json={"visibility": "private"},
    )
    assert res.status_code == 200
    assert res.json()["visibility"] == "private"


def test_visibility_invalid_value_422(client: TestClient) -> None:
    """visibility 값 도메인 — 'private'/'public' 외 → 422(Pydantic Literal)."""
    saved = client.post(
        "/api/factor-packs/saved", json={"pack": _pack()},
    ).json()
    res = client.patch(
        f"/api/factor-packs/saved/{saved['id']}/visibility",
        json={"visibility": "world"},
    )
    assert res.status_code == 422


def test_visibility_idor_other_user_404(
    client: TestClient, monkeypatch,
) -> None:
    """타 user pack visibility 변경 차단 — 404(P0 IDOR)."""
    saved = client.post(
        "/api/factor-packs/saved", json={"pack": _pack()},
    ).json()
    pack_id = saved["id"]
    # User B 로 전환 — A 의 pack 을 public 으로 바꾸려 시도.
    monkeypatch.setenv("SPECULUM_USER_ID", _USER_B)
    res_pub = client.patch(
        f"/api/factor-packs/saved/{pack_id}/visibility",
        json={"visibility": "public"},
    )
    assert res_pub.status_code == 404
    # private 전환 시도도 404(owner-check 단일 게이트).
    res_priv = client.patch(
        f"/api/factor-packs/saved/{pack_id}/visibility",
        json={"visibility": "private"},
    )
    assert res_priv.status_code == 404
    # A 로 복귀 — 여전히 private(타 user 가 못 바꿈).
    monkeypatch.delenv("SPECULUM_USER_ID", raising=False)
    after = client.get(f"/api/factor-packs/saved/{pack_id}").json()
    assert after["visibility"] == "private"


def test_visibility_nonexistent_404(client: TestClient) -> None:
    res = client.patch(
        f"/api/factor-packs/saved/{uuid4()}/visibility",
        json={"visibility": "public"},
    )
    assert res.status_code == 404


# =============================================================================
# ADR-0032 D3 — import provenance(source_url) route 라운드트립
# =============================================================================


def test_save_with_source_url_roundtrips(client: TestClient) -> None:
    """source_url 동봉 저장 → 메타/단건 불러오기 모두에 출처 URL 노출."""
    url = "https://example.com/packs/p-1.0.0.json"
    saved = client.post(
        "/api/factor-packs/saved",
        json={"pack": _pack(), "source_url": url},
    ).json()
    assert saved["source_url"] == url
    # 단건 불러오기(use-시점 #6 표시 재료)에도 동일 노출.
    full = client.get(f"/api/factor-packs/saved/{saved['id']}").json()
    assert full["source_url"] == url


def test_save_without_source_url_is_none(client: TestClient) -> None:
    """source_url 미전송(editor 작성) → null(출처 없음)."""
    saved = client.post(
        "/api/factor-packs/saved", json={"pack": _pack()},
    ).json()
    assert saved["source_url"] is None


def test_source_url_not_in_sealed_body(client: TestClient) -> None:
    """ADR-0032 D3 불변식 — source_url 은 body(봉인)에 들어가지 않는다.

    provenance 동봉 저장과 미동봉 저장의 content_hash 가 동일(봉인은 body 만의
    함수)하며, 봉인 body 안에 source_url 키가 없어야 한다.
    """
    with_url = client.post(
        "/api/factor-packs/saved",
        json={"pack": _pack(), "source_url": "https://example.com/p.json"},
    ).json()
    # 같은 정의라 idempotent — content_hash 는 provenance 와 무관해야 동일 id.
    without_url = client.post(
        "/api/factor-packs/saved", json={"pack": _pack()},
    ).json()
    assert with_url["content_hash"] == without_url["content_hash"]
    assert with_url["id"] == without_url["id"]
    # 봉인 body 안에 provenance 누출 없음.
    full = client.get(f"/api/factor-packs/saved/{with_url['id']}").json()
    assert "source_url" not in full["pack"]


def test_save_non_https_source_url_422(client: TestClient) -> None:
    """위험 scheme(javascript:/http:) source_url → 422(표시·링크 XSS 차단)."""
    res = client.post(
        "/api/factor-packs/saved",
        json={"pack": _pack(), "source_url": "javascript:alert(1)"},
    )
    assert res.status_code == 422


# =============================================================================
# M6 #4 (ADR-0034 D4/D6) — v2 namespace pack 생성·검증·저장 + 사칭 차단
# =============================================================================
#
# v2 namespace pack(`@{publisher}/{slug}`)은 (a) validator dispatch(v2 schema,
# @slug 통과) + (b) 발급 user 의 claim handle 일치 강제(verify_publisher_owns_slug
# → 사칭 403)로 저장된다. Fake 모드 + AUTH_SECRET 미설정에서 user 는 SYSTEM_USER_ID
# (SPECULUM_USER_ID monkeypatch 로 전환) — claim 과 save 가 같은 user.


def _v2_pack(
    *,
    publisher_handle: str = "alice",
    slug: str = "value-pack",
    version: str = "1.0.0",
    canonical_id: str = "alice:per",
    uuid_str: str = "00000000-0000-0000-0000-0000000000a1",
    name_override: str | None = None,
) -> dict[str, Any]:
    """v2 namespace pack — `$schema`=v2 URI, slug `@{handle}/{slug}`."""
    factor = _factor(canonical_id=canonical_id, uuid_str=uuid_str)
    if name_override is not None:
        factor["name"] = name_override
    return {
        "$schema": "https://speculum.dev/schemas/factor-pack-v2.json",
        "type": "factor-pack",
        "pack_slug": f"@{publisher_handle}/{slug}",
        "version": version,
        "publisher": publisher_handle,
        "license": "MIT",
        "created_at": "2026-01-01",
        "citation": {"title": "V2 namespace pack", "publisher": publisher_handle},
        "factors": [factor],
        "content_hash": "sha256:" + "a" * 64,
    }


def test_save_v2_pack_with_owned_handle_succeeds(client: TestClient) -> None:
    """자기 handle claim 후 `@{handle}/...` v2 pack 저장 → 201(검증·dispatch·봉인)."""
    assert client.post("/api/publishers", json={"handle": "alice"}).status_code == 200
    res = client.post(
        "/api/factor-packs/saved", json={"pack": _v2_pack(publisher_handle="alice")},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["pack_slug"] == "@alice/value-pack"
    # content_hash 재봉인(클라이언트 placeholder 무시).
    assert body["content_hash"].startswith("sha256:")
    assert body["content_hash"] != "sha256:" + "a" * 64


def test_save_v2_pack_impersonating_other_publisher_403(client: TestClient) -> None:
    """타 publisher 의 `@{other}/...` v2 pack 발급 → 403(사칭 차단)."""
    # 발급 user 는 'alice' claim 했으나 slug 는 '@bob/...' — 사칭.
    assert client.post("/api/publishers", json={"handle": "alice"}).status_code == 200
    res = client.post(
        "/api/factor-packs/saved", json={"pack": _v2_pack(publisher_handle="bob")},
    )
    assert res.status_code == 403


def test_save_v2_pack_without_claim_403(client: TestClient) -> None:
    """미claim user 가 v2 pack 발급 → 403(claim 선행 필요)."""
    res = client.post(
        "/api/factor-packs/saved", json={"pack": _v2_pack(publisher_handle="alice")},
    )
    assert res.status_code == 403


def test_save_v1_pack_unaffected_by_publisher_check(client: TestClient) -> None:
    """v1 `user/...` slug 저장은 publisher 사칭 검사와 무관 → 기존대로 201."""
    # 미claim 상태에서도 v1 slug 는 verify 통과(parse_v2_publisher→None).
    res = client.post("/api/factor-packs/saved", json={"pack": _pack()})
    assert res.status_code == 201
    assert res.json()["pack_slug"] == "user/testuser-mypack"


def test_save_v2_pack_forbidden_words_in_factor_name_422(client: TestClient) -> None:
    """v2 pack 의 factor name 금지어휘 → 422(forbidden_vocab 게이트 v2 적용).

    forbidden_vocab 게이트가 v2 namespace pack 에도 동일 적용됨을 확인. claim 까지
    선행해 verify(403)가 아니라 검증(422) 경로로 도달하게 한다(게이트 순서: 봉인
    검증이 verify 보다 먼저).
    """
    assert client.post("/api/publishers", json={"handle": "alice"}).status_code == 200
    res = client.post(
        "/api/factor-packs/saved",
        json={"pack": _v2_pack(name_override="Strong Buy Factor")},
    )
    assert res.status_code == 422
