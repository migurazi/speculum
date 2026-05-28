"""SqlWatchlistRepository + SqlScreenerSetRepository 단위 테스트.

테스트 매트릭스 — Fake 와 동일 contract:
    1. create_folder + list_folders + get_folder
    2. depth ≤ 2 invariant (parent 가 root 만)
    3. update_folder / delete_folder + is_default 거부
    4. add_item 중복 → None / 미존재 folder → raise
    5. add_item ownership / remove_item / update_item_note
    6. delete_folder cascade (items + 자식 folder)
    7. ScreenerSet save / list / get / delete
    8. ScreenerSet JSON conditions round-trip
    9. Fake/SQL contract 동등성 (같은 입력 → 같은 결과)
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.repositories.sql_user_repositories import (
    SqlScreenerSetRepository,
    SqlWatchlistRepository,
)
from app.repositories.watchlist_repository import (
    FakeScreenerSetRepository,
    FakeWatchlistRepository,
    WatchlistDataError,
)

# Test user UUID — 단일 사용자 시나리오.
_USER_A = UUID("00000000-0000-0000-0000-00000000000a")
_USER_B = UUID("00000000-0000-0000-0000-00000000000b")


# =============================================================================
# Folder CRUD
# =============================================================================

def test_create_and_list_folder(db_session: Session) -> None:
    repo = SqlWatchlistRepository(db_session)
    f1 = repo.create_folder(user_id=_USER_A, name="관심1")
    repo.create_folder(user_id=_USER_A, name="관심2")
    folders = repo.list_folders(user_id=_USER_A)
    assert [f.name for f in folders] == ["관심1", "관심2"]
    # display_order 자동 증가.
    assert folders[0].display_order == 0
    assert folders[1].display_order == 1
    # get_folder.
    fetched = repo.get_folder(f1.id, user_id=_USER_A)
    assert fetched is not None and fetched.name == "관심1"


def test_get_folder_owner_mismatch_returns_none(db_session: Session) -> None:
    repo = SqlWatchlistRepository(db_session)
    f1 = repo.create_folder(user_id=_USER_A, name="A의폴더")
    # 다른 사용자가 조회 → None.
    assert repo.get_folder(f1.id, user_id=_USER_B) is None


def test_create_folder_invalid_name_raises(db_session: Session) -> None:
    repo = SqlWatchlistRepository(db_session)
    with pytest.raises(WatchlistDataError, match="non-empty"):
        repo.create_folder(user_id=_USER_A, name="")
    with pytest.raises(WatchlistDataError, match="exceeds"):
        repo.create_folder(user_id=_USER_A, name="a" * 200)


def test_depth_limit_enforced(db_session: Session) -> None:
    """parent 가 root 만 — depth ≤ 2."""
    repo = SqlWatchlistRepository(db_session)
    root = repo.create_folder(user_id=_USER_A, name="root")
    child = repo.create_folder(user_id=_USER_A, name="child", parent_id=root.id)
    # depth=2 의 grandchild 시도 → reject.
    with pytest.raises(WatchlistDataError, match="depth"):
        repo.create_folder(
            user_id=_USER_A, name="grandchild", parent_id=child.id,
        )


def test_parent_owner_mismatch_raises(db_session: Session) -> None:
    repo = SqlWatchlistRepository(db_session)
    a_root = repo.create_folder(user_id=_USER_A, name="A root")
    # 다른 user 가 A 의 root 를 parent 로 시도.
    with pytest.raises(WatchlistDataError, match="not found or not owned"):
        repo.create_folder(
            user_id=_USER_B, name="B child", parent_id=a_root.id,
        )


def test_update_folder(db_session: Session) -> None:
    repo = SqlWatchlistRepository(db_session)
    f = repo.create_folder(user_id=_USER_A, name="원본")
    updated = repo.update_folder(
        f.id, user_id=_USER_A, name="갱신됨", display_order=10,
    )
    assert updated is not None
    assert updated.name == "갱신됨"
    assert updated.display_order == 10
    # 다른 user 가 update 시도 → None.
    assert repo.update_folder(f.id, user_id=_USER_B, name="x") is None


def test_delete_folder(db_session: Session) -> None:
    repo = SqlWatchlistRepository(db_session)
    f = repo.create_folder(user_id=_USER_A, name="삭제대상")
    assert repo.delete_folder(f.id, user_id=_USER_A) is True
    # 재 fetch → None.
    assert repo.get_folder(f.id, user_id=_USER_A) is None
    # 이미 삭제된 → False.
    assert repo.delete_folder(f.id, user_id=_USER_A) is False


def test_delete_folder_cascade_items(db_session: Session) -> None:
    """폴더 삭제 시 item 도 cascade."""
    repo = SqlWatchlistRepository(db_session)
    f = repo.create_folder(user_id=_USER_A, name="폴더")
    lineage_id = uuid4()
    repo.add_item(
        user_id=_USER_A, folder_id=f.id, code_lineage_id=lineage_id,
    )
    assert len(repo.list_items(f.id, user_id=_USER_A)) == 1
    # 폴더 삭제 → item 도 사라짐.
    repo.delete_folder(f.id, user_id=_USER_A)
    # list_items 는 folder 없으면 빈 list.
    assert repo.list_items(f.id, user_id=_USER_A) == ()


def test_delete_folder_cascade_child_folder(db_session: Session) -> None:
    """root 삭제 시 child folder 도 재귀 삭제."""
    repo = SqlWatchlistRepository(db_session)
    root = repo.create_folder(user_id=_USER_A, name="root")
    child = repo.create_folder(
        user_id=_USER_A, name="child", parent_id=root.id,
    )
    repo.delete_folder(root.id, user_id=_USER_A)
    # child 도 함께 삭제됨.
    assert repo.get_folder(child.id, user_id=_USER_A) is None


# =============================================================================
# Item CRUD
# =============================================================================

def test_add_item_to_missing_folder_raises(db_session: Session) -> None:
    """oracle T28 #C1 — 미존재 folder 는 raise (404 의미)."""
    repo = SqlWatchlistRepository(db_session)
    with pytest.raises(WatchlistDataError, match="not found or not owned"):
        repo.add_item(
            user_id=_USER_A, folder_id=uuid4(), code_lineage_id=uuid4(),
        )


def test_add_item_duplicate_returns_none(db_session: Session) -> None:
    """같은 folder + lineage 중복 → None (409 의미)."""
    repo = SqlWatchlistRepository(db_session)
    f = repo.create_folder(user_id=_USER_A, name="폴더")
    lineage_id = uuid4()
    first = repo.add_item(
        user_id=_USER_A, folder_id=f.id, code_lineage_id=lineage_id,
    )
    assert first is not None
    dup = repo.add_item(
        user_id=_USER_A, folder_id=f.id, code_lineage_id=lineage_id,
    )
    assert dup is None


def test_list_items_sorted_by_display_order(db_session: Session) -> None:
    repo = SqlWatchlistRepository(db_session)
    f = repo.create_folder(user_id=_USER_A, name="폴더")
    item1 = repo.add_item(
        user_id=_USER_A, folder_id=f.id, code_lineage_id=uuid4(),
    )
    item2 = repo.add_item(
        user_id=_USER_A, folder_id=f.id, code_lineage_id=uuid4(),
    )
    items = repo.list_items(f.id, user_id=_USER_A)
    assert items[0].id == item1.id
    assert items[1].id == item2.id
    assert items[0].display_order < items[1].display_order


def test_remove_item_ownership(db_session: Session) -> None:
    repo = SqlWatchlistRepository(db_session)
    f = repo.create_folder(user_id=_USER_A, name="폴더")
    item = repo.add_item(
        user_id=_USER_A, folder_id=f.id, code_lineage_id=uuid4(),
    )
    # 다른 user 가 remove 시도 → False.
    assert repo.remove_item(item.id, user_id=_USER_B) is False
    # owner 가 remove → True.
    assert repo.remove_item(item.id, user_id=_USER_A) is True
    # 재 remove → False.
    assert repo.remove_item(item.id, user_id=_USER_A) is False


def test_update_item_note(db_session: Session) -> None:
    repo = SqlWatchlistRepository(db_session)
    f = repo.create_folder(user_id=_USER_A, name="폴더")
    item = repo.add_item(
        user_id=_USER_A, folder_id=f.id, code_lineage_id=uuid4(), note="원본",
    )
    updated = repo.update_item_note(
        item.id, user_id=_USER_A, note="새 메모",
    )
    assert updated is not None and updated.note == "새 메모"
    # 길이 초과 → raise.
    with pytest.raises(WatchlistDataError, match="exceeds"):
        repo.update_item_note(item.id, user_id=_USER_A, note="a" * 500)


def test_add_item_long_note_raises(db_session: Session) -> None:
    repo = SqlWatchlistRepository(db_session)
    f = repo.create_folder(user_id=_USER_A, name="폴더")
    with pytest.raises(WatchlistDataError, match="exceeds"):
        repo.add_item(
            user_id=_USER_A, folder_id=f.id,
            code_lineage_id=uuid4(), note="x" * 500,
        )


# =============================================================================
# ScreenerSet
# =============================================================================

def test_screener_set_save_and_get(db_session: Session) -> None:
    repo = SqlScreenerSetRepository(db_session)
    conditions = [{"factor": "per:ttm", "op": "<", "value": "10"}]
    saved = repo.save(
        user_id=_USER_A, name="저PER",
        conditions=conditions,
        selected_factors=["per:ttm", "pbr:ttm"],
    )
    fetched = repo.get(saved.id, user_id=_USER_A)
    assert fetched is not None
    assert fetched.name == "저PER"
    assert fetched.conditions == ({"factor": "per:ttm", "op": "<", "value": "10"},)
    assert fetched.selected_factors == ("per:ttm", "pbr:ttm")


def test_screener_set_get_owner_mismatch(db_session: Session) -> None:
    repo = SqlScreenerSetRepository(db_session)
    saved = repo.save(
        user_id=_USER_A, name="A의셋", conditions=[], selected_factors=[],
    )
    assert repo.get(saved.id, user_id=_USER_B) is None


def test_screener_set_list_sorted_by_updated_desc(
    db_session: Session,
) -> None:
    import time
    repo = SqlScreenerSetRepository(db_session)
    s1 = repo.save(
        user_id=_USER_A, name="first",
        conditions=[], selected_factors=[],
    )
    time.sleep(0.01)  # updated_at 분리 보장.
    s2 = repo.save(
        user_id=_USER_A, name="second",
        conditions=[], selected_factors=[],
    )
    result = repo.list_all(user_id=_USER_A)
    # updated_at desc — second 가 먼저.
    assert [s.id for s in result] == [s2.id, s1.id]


def test_screener_set_delete(db_session: Session) -> None:
    repo = SqlScreenerSetRepository(db_session)
    saved = repo.save(
        user_id=_USER_A, name="삭제대상",
        conditions=[], selected_factors=[],
    )
    assert repo.delete(saved.id, user_id=_USER_A) is True
    assert repo.get(saved.id, user_id=_USER_A) is None
    # 재 삭제 → False.
    assert repo.delete(saved.id, user_id=_USER_A) is False
    # 다른 user 가 삭제 시도 → False.
    s2 = repo.save(
        user_id=_USER_A, name="B시도",
        conditions=[], selected_factors=[],
    )
    assert repo.delete(s2.id, user_id=_USER_B) is False


def test_screener_set_invalid_name_raises(db_session: Session) -> None:
    repo = SqlScreenerSetRepository(db_session)
    with pytest.raises(WatchlistDataError, match="non-empty"):
        repo.save(
            user_id=_USER_A, name="",
            conditions=[], selected_factors=[],
        )


# =============================================================================
# Fake/SQL contract 동등성
# =============================================================================

def test_watchlist_fake_sql_contract_equivalence(db_session: Session) -> None:
    """같은 시나리오에서 Fake/SQL 결과 동등성 — list_folders 정렬."""
    sql_repo = SqlWatchlistRepository(db_session)
    fake_repo = FakeWatchlistRepository()

    # 동일 fixture.
    for repo in (sql_repo, fake_repo):
        root1 = repo.create_folder(user_id=_USER_A, name="A")
        repo.create_folder(user_id=_USER_A, name="A-child", parent_id=root1.id)
        repo.create_folder(user_id=_USER_A, name="B")

    sql_folders = sql_repo.list_folders(user_id=_USER_A)
    fake_folders = fake_repo.list_folders(user_id=_USER_A)
    # 이름 sequence 동일.
    assert [f.name for f in sql_folders] == [f.name for f in fake_folders]


def test_screener_set_fake_sql_contract_equivalence(
    db_session: Session,
) -> None:
    sql_repo = SqlScreenerSetRepository(db_session)
    fake_repo = FakeScreenerSetRepository()

    for repo in (sql_repo, fake_repo):
        repo.save(
            user_id=_USER_A, name="t1",
            conditions=[{"factor": "per:ttm", "op": "<", "value": "10"}],
            selected_factors=["per:ttm"],
        )

    sql_list = sql_repo.list_all(user_id=_USER_A)
    fake_list = fake_repo.list_all(user_id=_USER_A)
    assert len(sql_list) == len(fake_list) == 1
    assert sql_list[0].name == fake_list[0].name == "t1"
    assert sql_list[0].conditions == fake_list[0].conditions
