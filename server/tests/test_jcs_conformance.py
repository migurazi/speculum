"""JCS content_hash cross-runtime 재현 conformance — ADR-0032 D1 (M4 #1).

open-format 의 load-bearing 불변식: 외부 구현자(임의 언어)가 RFC 8785 JCS + ADR-0032
decimalString 규약만 따르면 **byte-for-byte 동일 canonical form → 동일 content_hash**.
본 테스트가 그 canonical form 을 lock 한다 — 회귀 시 외부 재현이 깨진다.

핵심 결정성 봉쇄(ADR-0032 D1):
- 상수(const/weights/lower/upper)는 schema `decimalString`(지수표기·float 금지) →
  JSON number 가 없으므로 `json.dumps(float)` 직렬화 단계 자체 부재(IEEE 754 / 지수표기
  Python↔Node 차이 제거).
- 모든 object key 는 ASCII(schema pattern) — 비-BMP key 차이 부재.
- 값의 한글은 `ensure_ascii=False` + UTF-8 로 결정적(JCS minimal escape, \\uXXXX 미사용).

따라서 canonicalize_jcs 에는 runtime-의존 단계가 없다.
"""

from __future__ import annotations

import hashlib

import pytest

from app.services._jcs import canonicalize_jcs, compute_content_hash
from app.services.factor_pack import SchemaValidationError, validate_schema

# =============================================================================
# 1. canonical form lock — 정확한 byte 출력 (외부 구현 대조 기준)
# =============================================================================

def test_canonical_form_is_locked() -> None:
    # key 를 의도적으로 비정렬 + 한글 key/value + decimal 문자열 4종 혼합.
    crafted = {
        "z": "1000000",          # 정수 decimal string
        "b": "0.5",              # 소수
        "a": ["-1.5", "0.001"],  # 음수 + 작은 소수 배열(weights 형)
        "한": "값",               # 비-ASCII value(한글)
    }
    # JCS: key codepoint 정렬(a < b < z < 한[U+D55C]) + 공백 0 + 문자열 보존(\\uXXXX
    # 미사용) + decimal 은 문자열 그대로. 외부 구현자가 이 정확한 bytes 를 재현해야 함.
    expected = '{"a":["-1.5","0.001"],"b":"0.5","z":"1000000","한":"값"}'
    assert canonicalize_jcs(crafted).decode("utf-8") == expected


def test_canonical_no_float_no_exponent() -> None:
    # decimal 문자열은 지수표기로 변형되지 않는다 — "0.0000001" 이 "1e-07" 안 됨.
    crafted = {"x": "0.0000001", "y": "12345678901234567890"}
    out = canonicalize_jcs(crafted).decode("utf-8")
    assert "e" not in out and "E" not in out
    assert '"0.0000001"' in out
    assert '"12345678901234567890"' in out  # 큰 수도 문자열이라 정확 보존


def test_hangul_not_ascii_escaped() -> None:
    # ensure_ascii=False — 한글이 \\uXXXX 로 escape 되지 않아야(JCS minimal escape).
    out = canonicalize_jcs({"name": "삼성전자 시가총액"}).decode("utf-8")
    assert "삼성전자" in out
    assert "\\u" not in out


# =============================================================================
# 2. hash 결정성 — key 순서 무관 동일 hash (외부 직렬화 순서 무관)
# =============================================================================

def test_hash_invariant_to_key_order() -> None:
    a = {"type": "factor-pack", "pack_slug": "user/x", "v": "1.0"}
    b = {"v": "1.0", "pack_slug": "user/x", "type": "factor-pack"}
    assert compute_content_hash(a, exclude_key=None) == compute_content_hash(
        b, exclude_key=None
    )


def test_content_hash_format() -> None:
    h = compute_content_hash({"a": "1"}, exclude_key=None)
    assert h.startswith("sha256:")
    hexpart = h.removeprefix("sha256:")
    assert len(hexpart) == 64
    int(hexpart, 16)  # valid hex


# =============================================================================
# 3. golden hash — 고정 fixture → 고정 hash (회귀 lock + 외부 대조 anchor)
# =============================================================================

# ADR-0032 D1 conformance anchor — 외부 구현자가 동일 fixture 로 검증할 골든 값.
# 4개 numeric 필드(const/weights/lower/upper)를 decimal 문자열로, 한글 포함.
_GOLDEN_FIXTURE = {
    "type": "factor-pack",
    "pack_slug": "user/conformance-anchor",
    "publisher": "speculum",
    "factors": [
        {
            "canonical_id": "conformance:가중합",
            "name": "가중합 테스트",
            "formula": {
                "ast": {
                    "op": "weighted_sum",
                    "args": [{"const": "0.001"}, {"const": "-1.5"}],
                    "weights": ["0.6", "0.4"],
                },
                "inputs": ["x"],
            },
        },
        {
            "canonical_id": "conformance:클립",
            "name": "winsorize 테스트",
            "formula": {
                "ast": {
                    "op": "winsorize",
                    "args": [{"field": "x"}],
                    "lower": "0",
                    "upper": "1000000",
                },
                "inputs": ["x"],
            },
        },
    ],
}


def test_golden_canonical_and_hash() -> None:
    canonical = canonicalize_jcs(_GOLDEN_FIXTURE)
    # 직접 SHA-256 = compute_content_hash(exclude_key=None) 와 동치 확인.
    direct = "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert compute_content_hash(_GOLDEN_FIXTURE, exclude_key=None) == direct
    # 골든 anchor — 회귀 시 외부 재현이 깨짐을 즉시 감지. canonical form 고정.
    assert direct == _GOLDEN_HASH
    # decimal 문자열·한글이 canonical 에 그대로 보존(지수표기 부재는 위 별도 테스트가
    # 검증 — 여기선 op 이름의 'e' 와 구분 위해 escape 미사용 + 값 보존만 확인).
    text = canonical.decode("utf-8")
    assert "\\u" not in text  # 한글 미escape(ensure_ascii=False)
    assert '"0.001"' in text and '"-1.5"' in text and '"1000000"' in text
    assert "가중합" in text


# 골든 값 — `_GOLDEN_FIXTURE` 의 content_hash(exclude_key=None). 회귀 lock + 외부
# 구현자 대조 anchor. 변경 시 외부 재현이 깨지므로 의도적 변경만(ADR-0032 D1).
_GOLDEN_HASH = "sha256:39c17d9e834dfbac128ce3887dddc35451a709a8ea044ff7b59ed177eb668152"


# =============================================================================
# 4. schema 강제 — number const/weights 는 decimalString 위반 → 거부
# =============================================================================

def _builtin_with_ast(ast: dict, inputs: list[str]) -> dict:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "builtin-packs" / "factors" / "speculum-builtin-v1.0.0.json"
    )
    with path.open(encoding="utf-8") as f:
        body = json.load(f)
    body["factors"][0]["formula"]["ast"] = ast
    body["factors"][0]["formula"]["inputs"] = inputs
    return body


@pytest.mark.parametrize("bad_ast", [
    {"op": "mul", "left": {"field": "x"}, "right": {"const": 0.5}},   # number const
    {"op": "weighted_sum", "args": [{"field": "x"}], "weights": [1.0]},  # number weight
    {"op": "winsorize", "args": [{"field": "x"}], "lower": 0, "upper": 1},  # number bound
    {"op": "mul", "left": {"field": "x"}, "right": {"const": "1e-7"}},  # 지수표기 문자열
    {"op": "mul", "left": {"field": "x"}, "right": {"const": "01"}},    # 선행 0
])
def test_schema_rejects_non_decimal_string_const(bad_ast: dict) -> None:
    # ADR-0032 D1 — 상수는 decimalString 만. number(float drift) · 지수표기 · 선행0 거부.
    body = _builtin_with_ast(bad_ast, inputs=["x"])
    with pytest.raises(SchemaValidationError):
        validate_schema(body)
