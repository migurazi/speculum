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
    collect_run_data_versions,
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
    # M1 T48b — "1.0" → "1.1" (krx_batch_id / dart_batch_id 2 키가 run 생성
    # 경로의 data_versions 에 합류 → schema 키 set 확장).
    # M2 T72 — "1.1" → "1.2" (distribution_policy_version 1 키가 정책-only set 에
    # 합류 → schema 키 set 확장).
    # M7 #5 — "1.2" → "1.3" (total_return_policy_hash / total_return_policy_version
    # 2 키 + dividend_batch_id 1 키가 합류 → schema 키 set 확장, §2.10 격리).
    assert SNAPSHOT_SCHEMA_VERSION == "1.3"


# =============================================================================
# 2. 기대 키 set — oracle 결정 3 의 10 키 + distribution_policy_version (T72)
#    + total_return_policy_hash / total_return_policy_version (M7 #5)
#    + snapshot_schema_version
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
    "distribution_policy_version",
    "total_return_policy_hash",
    "total_return_policy_version",
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


def test_distribution_policy_version_matches_source_module() -> None:
    """M2 T72 — `distribution_policy_version` 이 db_universe_distribution 의
    DISTRIBUTION_POLICY_VERSION 과 동일 (ADR-0022 D3 freeze)."""
    from app.services.db_universe_distribution import DISTRIBUTION_POLICY_VERSION
    result = collect_active_policy_versions()
    assert result["distribution_policy_version"] == DISTRIBUTION_POLICY_VERSION


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


def test_total_return_policy_matches_source_module() -> None:
    """M7 #5 (§2.10 격리) — `total_return_policy_hash` /
    `total_return_policy_version` 이 total_return_adjuster 의 상수와 동일
    (ADR-0035 freeze, price_adjustment 일관성 테스트 동형)."""
    from app.services.total_return_adjuster import (
        POLICY_CONTENT_HASH,
        TOTAL_RETURN_POLICY_VERSION,
    )
    result = collect_active_policy_versions()
    assert result["total_return_policy_hash"] == POLICY_CONTENT_HASH
    assert result["total_return_policy_version"] == TOTAL_RETURN_POLICY_VERSION


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


# =============================================================================
# 6. ADR-0025 D3 — pack default 인자 byte 불변
# =============================================================================

def test_collect_no_arg_equals_default_pack_arg() -> None:
    """무인자 호출 == 명시 DEFAULT_PACK 인자 (byte 동일, 키 14개 불변).

    ADR-0025 D3 — collect_active_policy_versions 에 pack 파라미터를 추가했으나
    default=DEFAULT_PACK 라 기존 무인자 호출 결과가 byte 불변이어야 한다. 14 키 set
    + 모든 value 가 동일 (M7 #5 total_return 2 키 합류 후).
    """
    from app.services.factor_pack import DEFAULT_PACK

    no_arg = dict(collect_active_policy_versions())
    explicit = dict(collect_active_policy_versions(DEFAULT_PACK))
    assert no_arg == explicit
    assert frozenset(no_arg.keys()) == EXPECTED_KEYS
    assert len(no_arg) == 14


def test_collect_explicit_pack_freezes_that_pack_hash() -> None:
    """명시 pack 주입 시 그 pack 의 content_hash/slug/version 을 freeze (custom 경로).

    Phase 2c custom pack run 생성 경로의 계약 — 무인자(default)는 DEFAULT_PACK,
    명시 주입은 그 pack. 본 테스트는 빌트인 pack 명시 주입이 default 와 동일함을
    확인 (custom pack 자체는 Phase 2c).
    """
    from app.services.factor_pack import DEFAULT_PACK

    result = collect_active_policy_versions(DEFAULT_PACK)
    assert result["factor_pack_content_hash"] == DEFAULT_PACK.computed_hash
    assert result["factor_pack_slug"] == DEFAULT_PACK.pack_slug
    assert result["factor_pack_version"] == DEFAULT_PACK.version


def test_default_pack_is_v1_1_0_after_m7_bump() -> None:
    """DEFAULT_PACK 이 M7 #4 후 v1.1.0 (신규 run 의 active pack content_hash bump)."""
    from app.services.factor_pack import DEFAULT_PACK

    assert DEFAULT_PACK.version == "1.1.0"
    assert DEFAULT_PACK.factor_count == 11


def test_explicit_v1_0_0_pack_freezes_frozen_hash_despite_default_bump() -> None:
    """명시 v1.0.0 주입 시 frozen v1.0.0 hash/version 을 그대로 freeze (§2.10 load-bearing).

    DEFAULT_PACK 이 v1.1.0 으로 bump 됐어도, frozen v1.0.0 run 의 재현 경로는 명시
    v1.0.0 pack 을 주입하므로 factor_pack_version=="1.0.0" + v1.0.0 golden hash 가
    불변이어야 한다 — DEFAULT_PACK bump 가 기존 frozen run 의 data_versions 에 무영향임을
    못박는다 (M7 #4 ADR-0035 D4).
    """
    from app.services.factor_pack import DEFAULT_PACK, load_builtin_pack

    v100 = load_builtin_pack("1.0.0")
    # 전제 — default 와 v1.0.0 은 서로 다른 버전/hash (bump 가 실제로 일어남).
    assert DEFAULT_PACK.version == "1.1.0"
    assert v100.version == "1.0.0"
    assert DEFAULT_PACK.computed_hash != v100.computed_hash

    result = collect_active_policy_versions(v100)
    assert result["factor_pack_version"] == "1.0.0"
    assert result["factor_pack_content_hash"] == (
        "sha256:78e2d93f46700e475c56d17edc76faa8aa742406368b1b631a45b3908d5aa36d"
    )
    assert result["factor_pack_content_hash"] == v100.computed_hash


def test_collect_run_data_versions_no_pack_arg_byte_invariant() -> None:
    """collect_run_data_versions(as_of, None) 무-pack 호출 byte 불변 (정책 14 키)."""
    from datetime import date

    from app.services.factor_pack import DEFAULT_PACK

    as_of = date(2024, 6, 30)
    two_arg = dict(collect_run_data_versions(as_of, None))
    explicit = dict(collect_run_data_versions(as_of, None, DEFAULT_PACK))
    assert two_arg == explicit
    assert two_arg == dict(collect_active_policy_versions())
