"""SqlCustomPackRepository + Fake contract 단위 테스트 — ADR-0022 D9.

테스트 매트릭스 — Fake 와 동일 contract:
    1. save + list_for_user + get (CRUD happy path)
    2. append-only — 같은 (slug,version) 다른 hash → CustomPackDataError(충돌),
       같은 hash → idempotent
    3. owner-check (IDOR) — get/delete owner mismatch → None/False
    4. list_for_user 격리(P0) — user_id 필터(전 사용자 누출 게이트)
    5. **load 시 content_hash 재검증** — DB 직접 변조 body 는 무결성 위반 탐지
       (SQL repo 전용 — converter 의 validate_hash 게이트)
    6. ORM create_all 의 custom_packs 컬럼 / 인덱스 / UNIQUE 검증
    7. **updated_at 컬럼 부재** — append-only immutable 증명

봉인 body 는 compute_pack_hash 로 content_hash 를 채워 사용(route 의 봉인 단계를
테스트가 직접 수행).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.repositories.custom_pack_repository import (
    CustomPackDataError,
    CustomPackRepository,
    FakeCustomPackRepository,
)
from app.repositories.sql_custom_pack_repository import SqlCustomPackRepository
from app.services.factor_pack import compute_pack_hash

# conftest 가 users 에 seed 한 표준 user_id (FK 충족).
_USER_A = UUID("00000000-0000-0000-0000-00000000000a")
_USER_B = UUID("00000000-0000-0000-0000-00000000000b")


def _sealed_body(
    *,
    slug: str = "user/p",
    version: str = "1.0.0",
    canonical_id: str = "alice:per",
    uuid_str: str = "00000000-0000-0000-0000-000000000001",
) -> dict[str, Any]:
    body = {
        "$schema": "https://speculum.dev/schemas/factor-pack-v1.json",
        "type": "factor-pack",
        "pack_slug": slug,
        "version": version,
        "publisher": "testuser",
        "license": "MIT",
        "created_at": "2026-01-01",
        "citation": {"title": "Test", "publisher": "testuser"},
        "factors": [
            {
                "canonical_id": canonical_id,
                "uuid": uuid_str,
                "name": canonical_id.replace(":", " "),
                "description": canonical_id + " 설명 for testing",
                "formula": {
                    "ast": {"field": "shares_issued"},
                    "inputs": ["shares_issued"],
                },
                "unit": "ratio",
            },
        ],
        "content_hash": "sha256:" + "0" * 64,
    }
    body["content_hash"] = compute_pack_hash(body)
    return body


def _sql_repo(db_session: Session) -> SqlCustomPackRepository:
    return SqlCustomPackRepository(db_session)


def _fake_repo(_db_session: Session) -> FakeCustomPackRepository:
    return FakeCustomPackRepository()


_REPO_FACTORIES = (_sql_repo, _fake_repo)


# =============================================================================
# CRUD happy path
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_save_and_list_and_get(db_session: Session, factory) -> None:
    repo: CustomPackRepository = factory(db_session)
    saved = repo.save(user_id=_USER_A, body=_sealed_body())
    assert saved.pack_slug == "user/p"
    assert saved.factor_count == 1

    packs = repo.list_for_user(user_id=_USER_A)
    assert len(packs) == 1
    assert packs[0].id == saved.id

    fetched = repo.get(saved.id, user_id=_USER_A)
    assert fetched is not None
    assert fetched.body["pack_slug"] == "user/p"


# =============================================================================
# append-only immutable
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_save_idempotent_same_definition(db_session: Session, factory) -> None:
    repo: CustomPackRepository = factory(db_session)
    first = repo.save(user_id=_USER_A, body=_sealed_body())
    second = repo.save(user_id=_USER_A, body=_sealed_body())
    assert first.id == second.id
    assert len(repo.list_for_user(user_id=_USER_A)) == 1


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_save_conflict_different_definition(db_session: Session, factory) -> None:
    """같은 (slug, version) 다른 정의 → CustomPackDataError(충돌)."""
    repo: CustomPackRepository = factory(db_session)
    repo.save(user_id=_USER_A, body=_sealed_body(canonical_id="alice:per"))
    with pytest.raises(CustomPackDataError):
        repo.save(
            user_id=_USER_A,
            body=_sealed_body(
                canonical_id="bob:roe",
                uuid_str="00000000-0000-0000-0000-000000000002",
            ),
        )


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_save_new_version_coexists(db_session: Session, factory) -> None:
    repo: CustomPackRepository = factory(db_session)
    repo.save(user_id=_USER_A, body=_sealed_body(version="1.0.0"))
    repo.save(user_id=_USER_A, body=_sealed_body(version="1.1.0"))
    assert len(repo.list_for_user(user_id=_USER_A)) == 2


# =============================================================================
# owner-check (IDOR)
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_get_owner_mismatch_returns_none(db_session: Session, factory) -> None:
    repo: CustomPackRepository = factory(db_session)
    saved = repo.save(user_id=_USER_A, body=_sealed_body())
    assert repo.get(saved.id, user_id=_USER_B) is None


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_delete_owner_mismatch_returns_false(
    db_session: Session, factory,
) -> None:
    repo: CustomPackRepository = factory(db_session)
    saved = repo.save(user_id=_USER_A, body=_sealed_body())
    assert repo.delete(saved.id, user_id=_USER_B) is False
    assert repo.get(saved.id, user_id=_USER_A) is not None


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_list_for_user_isolation_p0(db_session: Session, factory) -> None:
    """list_for_user 는 user_id 필터 (P0 누출 게이트)."""
    repo: CustomPackRepository = factory(db_session)
    repo.save(user_id=_USER_A, body=_sealed_body(slug="user/a"))
    repo.save(user_id=_USER_B, body=_sealed_body(slug="user/b"))

    a_packs = repo.list_for_user(user_id=_USER_A)
    assert [p.pack_slug for p in a_packs] == ["user/a"]
    b_packs = repo.list_for_user(user_id=_USER_B)
    assert [p.pack_slug for p in b_packs] == ["user/b"]


# =============================================================================
# ADR-0028 D1/D3/D4 — visibility / list_public / set_visibility / get_public
# =============================================================================


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_save_defaults_visibility_private(db_session: Session, factory) -> None:
    repo: CustomPackRepository = factory(db_session)
    saved = repo.save(user_id=_USER_A, body=_sealed_body())
    fetched = repo.get(saved.id, user_id=_USER_A)
    assert fetched is not None
    assert fetched.visibility == "private"


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_list_public_only_public_across_users(
    db_session: Session, factory,
) -> None:
    """list_public 은 visibility=='public' 만, user 무관 — 비공개 누출 0."""
    repo: CustomPackRepository = factory(db_session)
    a_pub = repo.save(user_id=_USER_A, body=_sealed_body(slug="user/a-pub"))
    repo.save(user_id=_USER_A, body=_sealed_body(
        slug="user/a-priv", canonical_id="alice:roe",
        uuid_str="00000000-0000-0000-0000-0000000000a2"))
    b_pub = repo.save(user_id=_USER_B, body=_sealed_body(slug="user/b-pub"))

    assert repo.set_visibility(a_pub.id, user_id=_USER_A, visibility="public")
    assert repo.set_visibility(b_pub.id, user_id=_USER_B, visibility="public")

    public = repo.list_public()
    assert {p.pack_slug for p in public} == {"user/a-pub", "user/b-pub"}


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_set_visibility_preserves_body_and_hash(
    db_session: Session, factory,
) -> None:
    """set_visibility 는 visibility 만 변경 — body/content_hash 불변(ADR-0020)."""
    repo: CustomPackRepository = factory(db_session)
    saved = repo.save(user_id=_USER_A, body=_sealed_body())
    assert repo.set_visibility(saved.id, user_id=_USER_A, visibility="public")
    after = repo.get(saved.id, user_id=_USER_A)
    assert after is not None
    assert after.visibility == "public"
    assert after.content_hash == saved.content_hash
    assert after.body == saved.body


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_set_visibility_other_user_false(db_session: Session, factory) -> None:
    """타 user pack visibility 변경 차단 — False(IDOR)."""
    repo: CustomPackRepository = factory(db_session)
    saved = repo.save(user_id=_USER_A, body=_sealed_body())
    assert repo.set_visibility(
        saved.id, user_id=_USER_B, visibility="public") is False
    after = repo.get(saved.id, user_id=_USER_A)
    assert after is not None
    assert after.visibility == "private"


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_set_visibility_invalid_value_raises(
    db_session: Session, factory,
) -> None:
    repo: CustomPackRepository = factory(db_session)
    saved = repo.save(user_id=_USER_A, body=_sealed_body())
    with pytest.raises(CustomPackDataError):
        repo.set_visibility(saved.id, user_id=_USER_A, visibility="world")


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_get_public_only_public(db_session: Session, factory) -> None:
    """get_public 은 공개 (slug, version) body 만 — 비공개 누출 0."""
    repo: CustomPackRepository = factory(db_session)
    saved = repo.save(user_id=_USER_A, body=_sealed_body(slug="user/shared"))
    assert repo.get_public("user/shared", "1.0.0") is None  # private.
    repo.set_visibility(saved.id, user_id=_USER_A, visibility="public")
    got = repo.get_public("user/shared", "1.0.0")
    assert got is not None
    assert got.pack_slug == "user/shared"
    assert repo.get_public("user/shared", "9.9.9") is None  # 미존재 version.


# =============================================================================
# load 시 content_hash 재검증 — SQL repo 전용(변조 탐지)
# =============================================================================

def test_load_detects_tampered_body(db_session: Session) -> None:
    """저장 후 body 변조 → load 시 content_hash mismatch 무결성 위반 탐지."""
    from app.db.orm.custom_packs import CustomPackORM

    repo = SqlCustomPackRepository(db_session)
    saved = repo.save(user_id=_USER_A, body=_sealed_body())
    db_session.flush()

    # ORM body 를 직접 변조(content_hash 와 불일치하도록) — converter(read-only)를
    # 우회한 DB-level 변조 시뮬레이션. body 의 factor 를 바꾸면 봉인 hash 와 불일치.
    orm = db_session.get(CustomPackORM, saved.id)
    assert orm is not None
    tampered = dict(orm.body)
    tampered["factors"] = []  # 정의 변조 — content_hash 는 그대로(불일치 유발).
    orm.body = tampered
    db_session.flush()
    db_session.expire(orm)

    # load 시 converter 의 validate_hash 가 mismatch 탐지 → CustomPackDataError.
    with pytest.raises(CustomPackDataError):
        repo.get(saved.id, user_id=_USER_A)


# =============================================================================
# ORM create_all — 컬럼 / 인덱스 / UNIQUE 검증
# =============================================================================

def test_orm_create_all_includes_custom_packs() -> None:
    """ORM create_all 의 custom_packs 컬럼/인덱스 set + updated_at 부재."""
    from app.db import orm  # noqa: F401  ← Base.metadata 등록 side-effect
    from app.db.base import Base

    engine: Engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("custom_packs")}
        assert {
            "id", "user_id", "pack_slug", "version", "content_hash",
            "body", "factor_count", "created_at",
            # ADR-0028 D1 — 공유 visibility 메타 컬럼.
            "visibility",
        } <= cols
        # append-only immutable — updated_at 컬럼 없음(visibility 는 메타 mutable).
        assert "updated_at" not in cols

        index_names = {ix["name"] for ix in insp.get_indexes("custom_packs")}
        assert "ix_custom_packs_user_id" in index_names
        assert "ix_custom_packs_user_pack" in index_names

        # UNIQUE(user_id, pack_slug, version).
        uniques = insp.get_unique_constraints("custom_packs")
        uq = next(
            u for u in uniques
            if u["name"] == "uq_custom_packs_user_slug_version"
        )
        assert uq["column_names"] == ["user_id", "pack_slug", "version"]
    finally:
        engine.dispose()
