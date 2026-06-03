"""ADR-0022 D9 — CustomPackRepository contract 단위 테스트 (Fake + 추출 helper).

검증:
1. extract_pack_fields — 봉인 body → (slug, version, hash, factor_count).
   필수 키 누락 / content_hash 형식 위반 → CustomPackDataError.
2. save append-only — 같은 (user, slug, version) 같은 hash → idempotent.
   다른 hash → CustomPackDataError(충돌).
3. IDOR — get/delete owner mismatch → None/False. list_for_user user_id 필터.
4. UPDATE 메서드 부재 — Protocol 에 update attribute 없음(append-only immutable).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.repositories.custom_pack_repository import (
    CustomPackDataError,
    CustomPackRepository,
    FakeCustomPackRepository,
    extract_pack_fields,
    extract_public_meta,
)

_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64


def _body(*, slug: str = "user/p", version: str = "1.0.0",
          content_hash: str = _HASH_A, n_factors: int = 2) -> dict[str, Any]:
    return {
        "pack_slug": slug,
        "version": version,
        "content_hash": content_hash,
        "factors": [{"canonical_id": f"f{i}"} for i in range(n_factors)],
    }


# =============================================================================
# extract_pack_fields
# =============================================================================

def test_extract_pack_fields_happy() -> None:
    slug, version, h, fc = extract_pack_fields(_body())
    assert slug == "user/p"
    assert version == "1.0.0"
    assert h == _HASH_A
    assert fc == 2


@pytest.mark.parametrize("missing", ["pack_slug", "version", "factors"])
def test_extract_pack_fields_missing_key(missing: str) -> None:
    body = _body()
    del body[missing]
    with pytest.raises(CustomPackDataError):
        extract_pack_fields(body)


def test_extract_pack_fields_bad_content_hash() -> None:
    with pytest.raises(CustomPackDataError):
        extract_pack_fields(_body(content_hash="not-a-hash"))


# =============================================================================
# save — append-only immutable
# =============================================================================

def test_save_returns_pack_with_extracted_meta() -> None:
    repo = FakeCustomPackRepository()
    uid = uuid4()
    pack = repo.save(user_id=uid, body=_body(n_factors=3))
    assert pack.user_id == uid
    assert pack.pack_slug == "user/p"
    assert pack.version == "1.0.0"
    assert pack.content_hash == _HASH_A
    assert pack.factor_count == 3


def test_save_idempotent_same_definition() -> None:
    """같은 (user, slug, version) 같은 hash → 기존 row 반환(신규 0)."""
    repo = FakeCustomPackRepository()
    uid = uuid4()
    first = repo.save(user_id=uid, body=_body())
    second = repo.save(user_id=uid, body=_body())
    assert first.id == second.id
    assert len(repo.list_for_user(user_id=uid)) == 1


def test_save_conflict_same_version_different_definition() -> None:
    """같은 (user, slug, version) 다른 hash → CustomPackDataError(충돌)."""
    repo = FakeCustomPackRepository()
    uid = uuid4()
    repo.save(user_id=uid, body=_body(content_hash=_HASH_A))
    with pytest.raises(CustomPackDataError):
        repo.save(user_id=uid, body=_body(content_hash=_HASH_B))


def test_save_different_version_coexists() -> None:
    """같은 slug 다른 version → 별개 row(append-only 새 버전)."""
    repo = FakeCustomPackRepository()
    uid = uuid4()
    repo.save(user_id=uid, body=_body(version="1.0.0"))
    repo.save(user_id=uid, body=_body(version="1.1.0", content_hash=_HASH_B))
    assert len(repo.list_for_user(user_id=uid)) == 2


def test_save_same_slug_version_different_user_coexists() -> None:
    """다른 user 가 같은 (slug, version) 저장 → 충돌 아님(user 격리)."""
    repo = FakeCustomPackRepository()
    a, b = uuid4(), uuid4()
    repo.save(user_id=a, body=_body())
    # 다른 user 는 같은 키여도 충돌하지 않는다.
    repo.save(user_id=b, body=_body(content_hash=_HASH_B))
    assert len(repo.list_for_user(user_id=a)) == 1
    assert len(repo.list_for_user(user_id=b)) == 1


# =============================================================================
# IDOR — owner-check + user_id 필터
# =============================================================================

def test_get_owner_mismatch_returns_none() -> None:
    repo = FakeCustomPackRepository()
    a, b = uuid4(), uuid4()
    pack = repo.save(user_id=a, body=_body())
    assert repo.get(pack.id, user_id=b) is None
    assert repo.get(pack.id, user_id=a) is not None


def test_delete_owner_mismatch_returns_false() -> None:
    repo = FakeCustomPackRepository()
    a, b = uuid4(), uuid4()
    pack = repo.save(user_id=a, body=_body())
    assert repo.delete(pack.id, user_id=b) is False
    # A 의 pack 은 그대로.
    assert repo.get(pack.id, user_id=a) is not None
    assert repo.delete(pack.id, user_id=a) is True
    assert repo.get(pack.id, user_id=a) is None


def test_list_for_user_filters_by_user() -> None:
    """list_for_user 는 자기 것만 — 전 사용자 누출 차단."""
    repo = FakeCustomPackRepository()
    a, b = uuid4(), uuid4()
    repo.save(user_id=a, body=_body(slug="user/a"))
    repo.save(user_id=b, body=_body(slug="user/b"))
    a_packs = repo.list_for_user(user_id=a)
    assert len(a_packs) == 1
    assert a_packs[0].pack_slug == "user/a"


def test_get_nonexistent_returns_none() -> None:
    repo = FakeCustomPackRepository()
    assert repo.get(uuid4(), user_id=uuid4()) is None


# =============================================================================
# append-only immutable — UPDATE 메서드 부재
# =============================================================================

def test_repository_has_no_update_method() -> None:
    """append-only immutable — Protocol / Fake 에 update 메서드 없음."""
    assert not hasattr(FakeCustomPackRepository, "update")
    assert not hasattr(CustomPackRepository, "update")


# =============================================================================
# ADR-0028 D1 — visibility (list_public / set_visibility)
# =============================================================================


def test_save_defaults_to_private() -> None:
    """신규 저장 pack 은 항상 visibility=='private'(공유는 명시 토글만)."""
    repo = FakeCustomPackRepository()
    pack = repo.save(user_id=uuid4(), body=_body())
    assert pack.visibility == "private"


def test_list_public_empty_when_all_private() -> None:
    """private pack 만 있으면 list_public 빈 — 비공개 누출 차단."""
    repo = FakeCustomPackRepository()
    repo.save(user_id=uuid4(), body=_body())
    assert repo.list_public() == ()


def test_list_public_returns_only_public_across_users() -> None:
    """list_public 는 visibility=='public' 만, user 무관(공개 목록)."""
    repo = FakeCustomPackRepository()
    a, b = uuid4(), uuid4()
    pub = repo.save(user_id=a, body=_body(slug="user/a-pub"))
    repo.save(user_id=a, body=_body(slug="user/a-priv"))
    other_pub = repo.save(user_id=b, body=_body(slug="user/b-pub"))
    # A 의 한 pack + B 의 한 pack 을 public 으로.
    assert repo.set_visibility(pub.id, user_id=a, visibility="public") is True
    assert repo.set_visibility(other_pub.id, user_id=b, visibility="public") is True
    public = repo.list_public()
    slugs = {p.pack_slug for p in public}
    # public 둘만 — A 의 private 은 제외(user 무관 + visibility 필터).
    assert slugs == {"user/a-pub", "user/b-pub"}


def test_list_public_sorted_created_at_desc() -> None:
    """list_public 정렬은 created_at 역순(사실)만 — 큐레이션 신호 0."""
    repo = FakeCustomPackRepository()
    uid = uuid4()
    p1 = repo.save(user_id=uid, body=_body(slug="user/p1"))
    p2 = repo.save(user_id=uid, body=_body(slug="user/p2"))
    repo.set_visibility(p1.id, user_id=uid, visibility="public")
    repo.set_visibility(p2.id, user_id=uid, visibility="public")
    public = repo.list_public()
    # 역순 — 나중 created_at 이 앞. (Fake 의 _now 단조 증가, id 보조 정렬.)
    assert public[0].created_at >= public[1].created_at


def test_set_visibility_toggles_and_preserves_body_hash() -> None:
    """set_visibility 는 visibility 만 변경 — body/content_hash 불변(ADR-0020)."""
    repo = FakeCustomPackRepository()
    uid = uuid4()
    pack = repo.save(user_id=uid, body=_body())
    before = repo.get(pack.id, user_id=uid)
    assert before is not None
    assert repo.set_visibility(pack.id, user_id=uid, visibility="public") is True
    after = repo.get(pack.id, user_id=uid)
    assert after is not None
    assert after.visibility == "public"
    # body/content_hash 불변.
    assert after.content_hash == before.content_hash
    assert after.body == before.body
    # 되돌리기도 가능(mutable 메타).
    assert repo.set_visibility(pack.id, user_id=uid, visibility="private") is True
    reverted = repo.get(pack.id, user_id=uid)
    assert reverted is not None
    assert reverted.visibility == "private"


def test_set_visibility_other_user_returns_false() -> None:
    """타 user pack 의 visibility 변경 불가 — False(IDOR 차단)."""
    repo = FakeCustomPackRepository()
    a, b = uuid4(), uuid4()
    pack = repo.save(user_id=a, body=_body())
    assert repo.set_visibility(pack.id, user_id=b, visibility="public") is False
    # A 의 pack 은 그대로 private — 타 user 가 공개로 못 바꿈.
    after = repo.get(pack.id, user_id=a)
    assert after is not None
    assert after.visibility == "private"


def test_set_visibility_nonexistent_returns_false() -> None:
    repo = FakeCustomPackRepository()
    assert repo.set_visibility(uuid4(), user_id=uuid4(), visibility="public") is False


def test_get_public_returns_only_public_body() -> None:
    """get_public 은 visibility=='public' 인 (slug, version) body 만 반환."""
    repo = FakeCustomPackRepository()
    a = uuid4()
    pub = repo.save(user_id=a, body=_body(slug="user/shared", version="1.0.0"))
    # private 상태에서는 None.
    assert repo.get_public("user/shared", "1.0.0") is None
    repo.set_visibility(pub.id, user_id=a, visibility="public")
    got = repo.get_public("user/shared", "1.0.0")
    assert got is not None
    assert got.pack_slug == "user/shared"
    # user 무관(다른 user 도 같은 공개 pack 조회 가능 — 공개 목록).
    assert got.user_id == a
    # 미존재 (slug, version) → None.
    assert repo.get_public("user/shared", "9.9.9") is None
    # b 가 같은 slug/version private 저장해도 공개본만 반환(누출 0).
    assert repo.get_public("user/other", "1.0.0") is None


def test_set_visibility_invalid_value_raises() -> None:
    """visibility 값 도메인 방어 — 'private'/'public' 외 값 거부."""
    repo = FakeCustomPackRepository()
    uid = uuid4()
    pack = repo.save(user_id=uid, body=_body())
    with pytest.raises(CustomPackDataError):
        repo.set_visibility(pack.id, user_id=uid, visibility="world")


# =============================================================================
# extract_public_meta — 공개 메타(name=citation.title, description) 추출
# =============================================================================


def test_extract_public_meta_from_citation_title_and_description() -> None:
    body = {
        "citation": {"title": "My Pack"},
        "description": "factor 묶음 설명",
    }
    name, description = extract_public_meta(body)
    assert name == "My Pack"
    assert description == "factor 묶음 설명"


def test_extract_public_meta_missing_returns_none() -> None:
    name, description = extract_public_meta({})
    assert name is None
    assert description is None
    # citation 이 dict 아니거나 title 비어도 None.
    name2, _ = extract_public_meta({"citation": {"title": ""}})
    assert name2 is None
