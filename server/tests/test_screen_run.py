"""screen_run + ScreenRunRepository 단위 테스트.

테스트 매트릭스:
1. normalize_stock_codes — zero-pad + sort + dedup
2. ScreenRunQuery canonical 정규화 (conditions / selected_factors)
3. ScreenRunBuilder.build — result_hash 결정성
4. result_hash 가 data_versions 포함 — oracle 결정 2
5. presentation_order 분리 — hash 입력 제외 검증
6. SYSTEM_USER_ID + env override
7. data_versions value str 강제 (Risk-C2)
8. diff_versions — Risk-C4
9. ScreenRunSnapshot frozen
10. FakeScreenRunRepository — save/fetch_by_id/fetch_recent
11. 동일 input 의 hash 일관성
"""

from __future__ import annotations

import dataclasses
import os
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.repositories.screen_run_repository import (
    FakeScreenRunRepository,
    ScreenRunRepository,
)
from app.services.screen_run import (
    SYSTEM_USER_ID,
    ScreenRunBuilder,
    ScreenRunQuery,
    ScreenRunSnapshot,
    normalize_stock_codes,
)


_USER_A = UUID("00000000-0000-0000-0000-0000000000aa")
_USER_B = UUID("00000000-0000-0000-0000-0000000000bb")
_NOW = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)


# =============================================================================
# 1. normalize_stock_codes — KRX 형식 invariant
# =============================================================================

def test_normalize_codes_zero_pads_to_six_digits() -> None:
    assert normalize_stock_codes(["5930", "660"]) == ("000660", "005930")


def test_normalize_codes_deduplicates() -> None:
    assert normalize_stock_codes(["5930", "005930", "5930"]) == ("005930",)


def test_normalize_codes_sorts_ascending() -> None:
    assert normalize_stock_codes(["035420", "005930", "000660"]) == (
        "000660", "005930", "035420"
    )


def test_normalize_codes_strips_whitespace() -> None:
    assert normalize_stock_codes(["  5930  ", "660"]) == ("000660", "005930")


def test_normalize_codes_rejects_non_digit() -> None:
    with pytest.raises(ValueError, match="digits"):
        normalize_stock_codes(["abc"])


def test_normalize_codes_rejects_too_long() -> None:
    with pytest.raises(ValueError, match="exceeds 6"):
        normalize_stock_codes(["1234567"])


def test_normalize_codes_empty_input() -> None:
    assert normalize_stock_codes([]) == ()


# =============================================================================
# 2. ScreenRunQuery canonical 정규화
# =============================================================================

def test_query_conditions_are_sorted() -> None:
    """conditions 가 같은 의미면 입력 순서와 무관하게 같은 query."""
    snap1 = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": "PER", "op": "<", "value": "10"},
                    {"factor": "ROE", "op": ">", "value": "15"}],
        selected_factors=["PER", "ROE"],
        as_of=date(2024, 5, 1),
        result_codes=["005930"],
        computed_at=_NOW,
    )
    snap2 = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": "ROE", "op": ">", "value": "15"},
                    {"factor": "PER", "op": "<", "value": "10"}],
        selected_factors=["ROE", "PER"],
        as_of=date(2024, 5, 1),
        result_codes=["005930"],
        computed_at=_NOW,
    )
    # 입력 순서 다르나 hash 동일 (canonical sort).
    assert snap1.result_hash == snap2.result_hash
    assert snap1.query.conditions == snap2.query.conditions
    assert snap1.query.selected_factors == snap2.query.selected_factors


def test_query_selected_factors_dedup_and_sort() -> None:
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[],
        selected_factors=["roe", "per", "per", "ebitda"],
        as_of=date(2024, 5, 1),
        result_codes=[],
        computed_at=_NOW,
    )
    assert snap.query.selected_factors == ("ebitda", "per", "roe")


def test_query_value_normalized_to_str() -> None:
    """oracle Risk-C2 — Decimal/numeric → str 강제."""
    from decimal import Decimal
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": "PER", "op": "<", "value": Decimal("10.5")}],  # type: ignore[dict-item]
        selected_factors=[],
        as_of=date(2024, 5, 1),
        result_codes=[],
        computed_at=_NOW,
    )
    # canonical 정규화 후 모든 value 가 str.
    for cond in snap.query.conditions:
        for v in cond.values():
            assert isinstance(v, str)


# =============================================================================
# 3. presentation_order — hash 입력 제외 검증
# =============================================================================

def test_presentation_order_preserves_user_input_order() -> None:
    """oracle 결정 5 — 사용자 입력 순서는 별도 필드로 보존, hash 입력 제외."""
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        # ROE 가 입력 인덱스 0, PER 가 1 — canonical sort 후 PER 가 먼저.
        conditions=[{"factor": "ROE", "op": ">", "value": "15"},
                    {"factor": "PER", "op": "<", "value": "10"}],
        selected_factors=[],
        as_of=date(2024, 5, 1),
        result_codes=[],
        computed_at=_NOW,
    )
    # query.conditions 는 canonical (PER, ROE) 순서.
    assert snap.query.conditions[0]["factor"] == "PER"
    assert snap.query.conditions[1]["factor"] == "ROE"
    # presentation_order: canonical 인덱스 → 원본 인덱스.
    # PER 가 canonical[0] 이고 원본[1] 이었으므로 presentation_order[0] = 1.
    assert snap.query.presentation_order == (1, 0)


# =============================================================================
# 4. result_hash 결정성 + data_versions 포함
# =============================================================================

def test_result_hash_format() -> None:
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[], computed_at=_NOW,
    )
    assert snap.result_hash.startswith("sha256:")
    assert len(snap.result_hash) == len("sha256:") + 64


def test_result_hash_is_deterministic_for_same_input() -> None:
    args = dict(
        run_id=uuid4(),
        conditions=[{"factor": "PER", "op": "<", "value": "10"}],
        selected_factors=["PER"],
        as_of=date(2024, 5, 1),
        result_codes=["005930", "000660"],
        data_versions={"a": "1", "b": "2"},
        computed_at=_NOW,
    )
    h1 = ScreenRunBuilder.build(**args).result_hash
    h2 = ScreenRunBuilder.build(**args).result_hash
    assert h1 == h2


def test_result_hash_changes_when_data_versions_change() -> None:
    """oracle 결정 2 — data_versions 가 hash 입력에 포함."""
    common = dict(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=["005930"], computed_at=_NOW,
    )
    snap_a = ScreenRunBuilder.build(**common, data_versions={"factor_pack_version": "1.0.0"})
    snap_b = ScreenRunBuilder.build(**common, data_versions={"factor_pack_version": "1.0.1"})
    assert snap_a.result_hash != snap_b.result_hash


def test_result_hash_does_not_depend_on_computed_at() -> None:
    """같은 query/as_of/result/versions 면 실행 시각과 무관하게 같은 hash."""
    common = dict(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=["005930"],
        data_versions={"a": "1"},
    )
    snap1 = ScreenRunBuilder.build(**common, computed_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    snap2 = ScreenRunBuilder.build(**common, computed_at=datetime(2024, 12, 31, tzinfo=timezone.utc))
    assert snap1.result_hash == snap2.result_hash


def test_result_hash_changes_when_as_of_changes() -> None:
    common = dict(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        result_codes=["005930"],
        data_versions={"a": "1"}, computed_at=_NOW,
    )
    snap1 = ScreenRunBuilder.build(**common, as_of=date(2024, 5, 1))
    snap2 = ScreenRunBuilder.build(**common, as_of=date(2024, 6, 1))
    assert snap1.result_hash != snap2.result_hash


def test_result_hash_changes_when_result_codes_change() -> None:
    common = dict(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1),
        data_versions={"a": "1"}, computed_at=_NOW,
    )
    snap1 = ScreenRunBuilder.build(**common, result_codes=["005930"])
    snap2 = ScreenRunBuilder.build(**common, result_codes=["005930", "000660"])
    assert snap1.result_hash != snap2.result_hash


# =============================================================================
# 5. data_versions value str 강제 (Risk-C2)
# =============================================================================

def test_data_versions_rejects_non_str_value() -> None:
    """non-str value (예: int / Decimal / float) 는 ValueError."""
    with pytest.raises(ValueError, match="must be str"):
        ScreenRunBuilder.build(
            run_id=uuid4(),
            conditions=[], selected_factors=[],
            as_of=date(2024, 5, 1), result_codes=[],
            data_versions={"version": 1.0},  # type: ignore[dict-item]
            computed_at=_NOW,
        )


def test_data_versions_default_uses_active_collect() -> None:
    """data_versions=None 이면 collect_active_policy_versions() 결과 사용."""
    from app.services.snapshot_versions import collect_active_policy_versions
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[],
        computed_at=_NOW,
    )
    active = dict(collect_active_policy_versions())
    assert dict(snap.data_versions) == active


# =============================================================================
# 6. SYSTEM_USER_ID + env override
# =============================================================================

def test_default_user_id_is_system() -> None:
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[],
        computed_at=_NOW,
    )
    assert snap.user_id == SYSTEM_USER_ID


def test_user_id_override_via_builder() -> None:
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        user_id=_USER_A,
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[],
        computed_at=_NOW,
    )
    assert snap.user_id == _USER_A


# =============================================================================
# 7. diff_versions (Risk-C4 — M1 prerequisite)
# =============================================================================

def test_diff_versions_no_change() -> None:
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[],
        data_versions={"a": "1", "b": "2"},
        computed_at=_NOW,
    )
    diff = snap.diff_versions({"a": "1", "b": "2"})
    assert dict(diff) == {}


def test_diff_versions_value_changed() -> None:
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[],
        data_versions={"factor_pack_version": "1.0.0"},
        computed_at=_NOW,
    )
    diff = snap.diff_versions({"factor_pack_version": "1.0.1"})
    assert dict(diff) == {"factor_pack_version": ("1.0.0", "1.0.1")}


def test_diff_versions_key_added() -> None:
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[],
        data_versions={"a": "1"},
        computed_at=_NOW,
    )
    diff = snap.diff_versions({"a": "1", "new_key": "x"})
    assert dict(diff) == {"new_key": ("", "x")}


def test_diff_versions_key_removed() -> None:
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[],
        data_versions={"a": "1", "removed": "x"},
        computed_at=_NOW,
    )
    diff = snap.diff_versions({"a": "1"})
    assert dict(diff) == {"removed": ("x", "")}


# =============================================================================
# 8. ScreenRunSnapshot frozen
# =============================================================================

def test_snapshot_is_frozen() -> None:
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[],
        computed_at=_NOW,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.result_hash = "tampered"  # type: ignore[misc]


def test_snapshot_data_versions_is_immutable_view() -> None:
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[],
        data_versions={"a": "1"},
        computed_at=_NOW,
    )
    with pytest.raises(TypeError):
        snap.data_versions["a"] = "x"  # type: ignore[index]


# =============================================================================
# 9. FakeScreenRunRepository
# =============================================================================

def test_repo_save_and_fetch_by_id() -> None:
    repo = FakeScreenRunRepository()
    run_id = uuid4()
    snap = ScreenRunBuilder.build(
        run_id=run_id, user_id=_USER_A,
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[],
        computed_at=_NOW,
    )
    repo.save(snap)
    fetched = repo.fetch_by_id(run_id, user_id=_USER_A)
    assert fetched == snap


def test_repo_fetch_by_id_returns_none_for_other_user() -> None:
    """user_id 가 owner 가 아니면 None — multi-user 안전."""
    repo = FakeScreenRunRepository()
    run_id = uuid4()
    snap = ScreenRunBuilder.build(
        run_id=run_id, user_id=_USER_A,
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[],
        computed_at=_NOW,
    )
    repo.save(snap)
    # 다른 user 가 같은 run_id 조회 — None.
    assert repo.fetch_by_id(run_id, user_id=_USER_B) is None


def test_repo_fetch_recent_orders_by_computed_at_desc() -> None:
    repo = FakeScreenRunRepository()
    for i in range(3):
        snap = ScreenRunBuilder.build(
            run_id=uuid4(), user_id=_USER_A,
            conditions=[], selected_factors=[],
            as_of=date(2024, 5, 1), result_codes=[],
            computed_at=datetime(2024, 5, i + 1, tzinfo=timezone.utc),
        )
        repo.save(snap)
    runs = repo.fetch_recent(user_id=_USER_A, limit=10)
    assert len(runs) == 3
    assert runs[0].computed_at > runs[1].computed_at > runs[2].computed_at


def test_repo_fetch_recent_respects_limit() -> None:
    repo = FakeScreenRunRepository()
    for i in range(5):
        snap = ScreenRunBuilder.build(
            run_id=uuid4(), user_id=_USER_A,
            conditions=[], selected_factors=[],
            as_of=date(2024, 5, 1), result_codes=[],
            computed_at=datetime(2024, 5, i + 1, tzinfo=timezone.utc),
        )
        repo.save(snap)
    runs = repo.fetch_recent(user_id=_USER_A, limit=2)
    assert len(runs) == 2


def test_repo_fetch_recent_rejects_negative_limit() -> None:
    repo = FakeScreenRunRepository()
    with pytest.raises(ValueError):
        repo.fetch_recent(user_id=_USER_A, limit=-1)


def test_repo_fetch_recent_empty_for_unknown_user() -> None:
    repo = FakeScreenRunRepository()
    assert tuple(repo.fetch_recent(user_id=_USER_A)) == ()


def test_repo_save_overwrites_same_id() -> None:
    repo = FakeScreenRunRepository()
    run_id = uuid4()
    snap1 = ScreenRunBuilder.build(
        run_id=run_id, user_id=_USER_A,
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=["005930"],
        computed_at=_NOW,
    )
    snap2 = ScreenRunBuilder.build(
        run_id=run_id, user_id=_USER_A,
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=["005930", "000660"],
        computed_at=_NOW,
    )
    repo.save(snap1)
    repo.save(snap2)
    fetched = repo.fetch_by_id(run_id, user_id=_USER_A)
    assert fetched == snap2
    # recent 에 1 개만.
    runs = repo.fetch_recent(user_id=_USER_A)
    assert len(runs) == 1


def test_repo_is_runtime_checkable_protocol() -> None:
    repo = FakeScreenRunRepository()
    assert isinstance(repo, ScreenRunRepository)


# =============================================================================
# 10. SYSTEM_USER_ID env override (module reload)
# =============================================================================

def test_system_user_id_default() -> None:
    """env 가 비어있으면 fixed default UUID."""
    # 본 테스트는 SYSTEM_USER_ID 가 import 시 결정된 상태 — 환경변수가 없다는
    # default case 검증.
    assert SYSTEM_USER_ID == UUID("00000000-0000-0000-0000-000000000001")


def test_resolve_user_id_rejects_invalid_uuid(monkeypatch) -> None:
    """env 값이 invalid UUID 면 ValueError."""
    from app.services.screen_run import _resolve_system_user_id
    monkeypatch.setenv("SPECULUM_USER_ID", "not-a-uuid")
    with pytest.raises(ValueError):
        _resolve_system_user_id()


def test_resolve_user_id_accepts_env_override(monkeypatch) -> None:
    from app.services.screen_run import _resolve_system_user_id
    monkeypatch.setenv("SPECULUM_USER_ID", "11111111-2222-3333-4444-555555555555")
    assert _resolve_system_user_id() == UUID("11111111-2222-3333-4444-555555555555")


# =============================================================================
# 11. oracle 2 차 리뷰 회귀
# =============================================================================

def test_duplicate_conditions_deduped_in_canonical(_=None) -> None:
    """oracle 2 차 C1 — 동일 조건 중복 입력은 hash 입력에서 제거."""
    snap_with_dup = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[
            {"factor": "PER", "op": "<", "value": "10"},
            {"factor": "PER", "op": "<", "value": "10"},  # 동일 중복
            {"factor": "PER", "op": "<", "value": "10"},  # 또 중복
        ],
        selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[], computed_at=_NOW,
    )
    snap_single = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": "PER", "op": "<", "value": "10"}],
        selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[], computed_at=_NOW,
    )
    assert snap_with_dup.result_hash == snap_single.result_hash
    assert len(snap_with_dup.query.conditions) == 1


def test_presentation_order_does_not_affect_result_hash() -> None:
    """oracle 2 차 M1 — 같은 canonical conditions, 다른 입력 순서면 같은 hash."""
    snap1 = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": "PER", "op": "<", "value": "10"},
                    {"factor": "ROE", "op": ">", "value": "15"}],
        selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[], computed_at=_NOW,
    )
    snap2 = ScreenRunBuilder.build(
        run_id=uuid4(),
        # 입력 순서만 뒤집음
        conditions=[{"factor": "ROE", "op": ">", "value": "15"},
                    {"factor": "PER", "op": "<", "value": "10"}],
        selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[], computed_at=_NOW,
    )
    # presentation_order 는 다름 (사용자 입력 보존).
    assert snap1.query.presentation_order != snap2.query.presentation_order
    # 그러나 result_hash 는 동일 — canonical hash 입력에서 presentation_order 제외.
    assert snap1.result_hash == snap2.result_hash


def test_data_versions_rejects_non_str_key() -> None:
    """oracle 2 차 C2 — value 뿐 아니라 key 도 str 강제."""
    with pytest.raises(ValueError, match="key must be str"):
        ScreenRunBuilder.build(
            run_id=uuid4(),
            conditions=[], selected_factors=[],
            as_of=date(2024, 5, 1), result_codes=[],
            data_versions={1: "value"},  # type: ignore[dict-item]
            computed_at=_NOW,
        )


def test_builder_uses_lazy_user_id_resolution(monkeypatch) -> None:
    """oracle 2 차 C3 — Builder 가 매 호출마다 env 재평가."""
    monkeypatch.setenv("SPECULUM_USER_ID", "99999999-9999-9999-9999-999999999999")
    snap = ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[], selected_factors=[],
        as_of=date(2024, 5, 1), result_codes=[], computed_at=_NOW,
    )
    # import-time SYSTEM_USER_ID 는 default 였으나 호출 시점 env 가 적용.
    assert snap.user_id == UUID("99999999-9999-9999-9999-999999999999")
