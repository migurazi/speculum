"""_jcs util 단위 테스트 + cross-module 일관성 회귀.

T30 의 JCS 추출 — factor_pack / krx_calendar / price_adjuster 의 inline 복제가
모두 동일 함수를 reference 함을 검증.
"""

from __future__ import annotations

import pytest

from app.services._jcs import HASH_PREFIX, canonicalize_jcs, compute_content_hash

# =============================================================================
# 1. JCS canonicalize basic
# =============================================================================

def test_canonicalize_sorts_keys() -> None:
    a = {"b": 1, "a": 2, "c": 3}
    b = {"c": 3, "a": 2, "b": 1}
    assert canonicalize_jcs(a) == canonicalize_jcs(b)


def test_canonicalize_strips_whitespace() -> None:
    assert canonicalize_jcs({"a": 1, "b": [2, 3]}) == b'{"a":1,"b":[2,3]}'


def test_canonicalize_preserves_unicode() -> None:
    out = canonicalize_jcs({"name": "추천"})
    assert "추천".encode() in out


def test_canonicalize_rejects_nan() -> None:
    with pytest.raises(ValueError):
        canonicalize_jcs({"x": float("nan")})


def test_canonicalize_rejects_infinity() -> None:
    with pytest.raises(ValueError):
        canonicalize_jcs({"x": float("inf")})


# =============================================================================
# 2. compute_content_hash
# =============================================================================

def test_compute_content_hash_format() -> None:
    h = compute_content_hash({"a": 1})
    assert h.startswith(HASH_PREFIX)
    assert len(h) == len(HASH_PREFIX) + 64


def test_compute_content_hash_excludes_default_key() -> None:
    a = {"a": 1, "content_hash": "sha256:" + "0" * 64}
    b = {"a": 1, "content_hash": "sha256:" + "f" * 64}
    assert compute_content_hash(a) == compute_content_hash(b)


def test_compute_content_hash_custom_exclude() -> None:
    a = {"a": 1, "ignore_me": "X"}
    b = {"a": 1, "ignore_me": "Y"}
    assert compute_content_hash(a, exclude_key="ignore_me") == compute_content_hash(b, exclude_key="ignore_me")


def test_compute_content_hash_none_exclude_includes_all() -> None:
    a = {"a": 1, "content_hash": "X"}
    b = {"a": 1, "content_hash": "Y"}
    # exclude_key=None → 모든 키 사용 → 다른 hash.
    assert compute_content_hash(a, exclude_key=None) != compute_content_hash(b, exclude_key=None)


def test_compute_content_hash_deterministic() -> None:
    body = {"a": 1, "nested": {"x": [1, 2, 3]}}
    assert compute_content_hash(body) == compute_content_hash(body)


# =============================================================================
# 3. cross-module 일관성 — 3 곳 inline 복제가 모두 동일 함수 사용
# =============================================================================

def test_factor_pack_canonicalize_uses_jcs_util() -> None:
    """factor_pack 의 re-exported canonicalize_jcs 가 _jcs 와 동일."""
    from app.services.factor_pack import canonicalize_jcs as fp_canonicalize
    sample = {"factor": "PER", "value": 12.3, "한글": "값"}
    assert fp_canonicalize(sample) == canonicalize_jcs(sample)


def test_krx_calendar_canonicalize_uses_jcs_util() -> None:
    from app.services.krx_calendar import _canonicalize_jcs
    sample = {"min_date": "2024-01-01", "version": "1.0.0"}
    assert _canonicalize_jcs(sample) == canonicalize_jcs(sample)


def test_price_adjuster_canonicalize_uses_jcs_util() -> None:
    from app.services.price_adjuster import _canonicalize_jcs
    sample = {"policy": "v1.0", "matrix": [{"action": "split"}]}
    assert _canonicalize_jcs(sample) == canonicalize_jcs(sample)


# =============================================================================
# 4. factor_pack 의 compute_pack_hash 가 _jcs 와 일관
# =============================================================================

def test_factor_pack_compute_pack_hash_matches_jcs_util() -> None:
    from app.services.factor_pack import compute_pack_hash
    body = {"$schema": "x", "content_hash": "should be excluded",
            "factors": [{"id": "a"}]}
    assert compute_pack_hash(body) == compute_content_hash(body, exclude_key="content_hash")
