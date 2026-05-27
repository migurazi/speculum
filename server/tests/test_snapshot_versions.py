"""snapshot_versions aggregator 단위 테스트 + CI 게이트.

oracle 자문 Risk-C1 의 CI 게이트 — 모든 활성 정책 모듈의 `*_POLICY_VERSION` 또는
`content_hash` 상수가 `collect_active_policy_versions()` 의 결과에 포함되는지
검증. 새 정책 도입 시 본 aggregator 에 키 추가가 의무.
"""

from __future__ import annotations

import pytest

from app.services.snapshot_versions import (
    SNAPSHOT_SCHEMA_VERSION,
    collect_active_policy_versions,
)


# =============================================================================
# 1. 기본 schema + value 타입
# =============================================================================

def test_collect_returns_immutable_mapping() -> None:
    result = collect_active_policy_versions()
    with pytest.raises(TypeError):
        result["unauthorized_key"] = "x"  # type: ignore[index]


def test_collect_all_values_are_str() -> None:
    """oracle Risk-C2 — Decimal/UUID/datetime 직렬화 모호성 차단."""
    result = collect_active_policy_versions()
    for key, value in result.items():
        assert isinstance(value, str), (
            f"data_versions[{key!r}] must be str, got {type(value).__name__}: {value!r}"
        )


def test_snapshot_schema_version_constant() -> None:
    assert SNAPSHOT_SCHEMA_VERSION == "1.0"


# =============================================================================
# 2. 기대 키 set — oracle 결정 3 의 10 키 + snapshot_schema_version
# =============================================================================

EXPECTED_KEYS: frozenset[str] = frozenset({
    "factor_pack_content_hash",
    "factor_pack_slug",
    "factor_pack_version",
    "calendar_content_hash",
    "calendar_version",
    "pit_policy_version",
    "price_adjustment_policy_hash",
    "adjustment_policy_version",
    "ca_policy_version",
    "evaluator_version",
    "snapshot_schema_version",
})


def test_collect_has_expected_keys() -> None:
    result = collect_active_policy_versions()
    assert frozenset(result.keys()) == EXPECTED_KEYS, (
        f"unexpected keys: {sorted(set(result.keys()) - EXPECTED_KEYS)}; "
        f"missing keys: {sorted(EXPECTED_KEYS - set(result.keys()))}"
    )


# =============================================================================
# 3. 정책 source 모듈과의 일관성 — CI 게이트 (Risk-C1)
# =============================================================================

def test_pit_policy_version_matches_source_module() -> None:
    """`pit_policy_version` 이 as_of_policy.PIT_POLICY_VERSION 과 동일."""
    from app.services.as_of_policy import PIT_POLICY_VERSION
    result = collect_active_policy_versions()
    assert result["pit_policy_version"] == PIT_POLICY_VERSION


def test_evaluator_version_matches_source_module() -> None:
    from app.services.factor_evaluator import EVALUATOR_POLICY_VERSION
    result = collect_active_policy_versions()
    assert result["evaluator_version"] == EVALUATOR_POLICY_VERSION


def test_calendar_versions_match_source_module() -> None:
    from app.services.krx_calendar import DEFAULT_CALENDAR
    result = collect_active_policy_versions()
    assert result["calendar_version"] == DEFAULT_CALENDAR.version
    assert result["calendar_content_hash"] == DEFAULT_CALENDAR.content_hash


def test_factor_pack_versions_match_source_module() -> None:
    from app.services.factor_pack import DEFAULT_PACK
    result = collect_active_policy_versions()
    assert result["factor_pack_content_hash"] == DEFAULT_PACK.computed_hash
    assert result["factor_pack_slug"] == DEFAULT_PACK.pack_slug
    assert result["factor_pack_version"] == DEFAULT_PACK.version


def test_price_adjustment_versions_match_source_module() -> None:
    from app.services.price_adjuster import (
        ADJUSTMENT_POLICY_VERSION,
        CORPORATE_ACTION_POLICY_VERSION,
        POLICY_CONTENT_HASH,
    )
    result = collect_active_policy_versions()
    assert result["price_adjustment_policy_hash"] == POLICY_CONTENT_HASH
    assert result["adjustment_policy_version"] == ADJUSTMENT_POLICY_VERSION
    assert result["ca_policy_version"] == CORPORATE_ACTION_POLICY_VERSION


# =============================================================================
# 4. pit_policy / asof_policy 통합 — oracle 결정 3
# =============================================================================

def test_no_separate_asof_policy_version_key() -> None:
    """pit + asof 가 같은 상수 share — 단일 키만 노출 (redundancy 회피)."""
    result = collect_active_policy_versions()
    assert "asof_policy_version" not in result
    assert "pit_policy_version" in result


# =============================================================================
# 5. 결정성 — 같은 import 상태에서 같은 결과
# =============================================================================

def test_collect_is_deterministic() -> None:
    a = collect_active_policy_versions()
    b = collect_active_policy_versions()
    assert dict(a) == dict(b)
