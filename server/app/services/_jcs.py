"""RFC 8785 JCS (JSON Canonicalization Scheme) + SHA-256 content hash 유틸.

본 모듈은 factor_pack / krx_calendar / price_adjuster / screen_run 의 inline 복제
를 단일화. 정책 변경 시 한 곳만 수정 — drift risk 영구 제거 (oracle 자문 결정 1
+ P1).

JCS 의 핵심 규칙 (subset 구현):
- object 의 키는 codepoint 순서로 정렬 (`sort_keys=True`)
- 모든 공백 제거 (separators=(",", ":"))
- 문자열은 JSON minimal escape
- 숫자는 IEEE 754 double 의 "shortest" 형식 (Python `json.dumps` default — 대부분
  ASCII numeric 에서 RFC 8785 와 일치)
- non-ASCII 보존 (`ensure_ascii=False`)
- NaN / Infinity 금지 (`allow_nan=False`)

cross-runtime (Python ↔ Node ↔ 임의 언어) 동일 hash 보장 — open-format 재현의
load-bearing 불변식(ADR-0032 D1). 잠재 위험 2가지를 **포맷 레벨에서 봉쇄**한다:

- **float 지수표기·IEEE 754 shortest-repr 차이**: factor pack 의 상수(const/weights/
  lower/upper)는 schema 가 `decimalString`(지수표기·float 금지) 으로 강제 → JSON 에
  number 가 들어오지 않으므로 `json.dumps(float)` 직렬화 단계 자체가 없다. 모든
  numeric 은 decimal **문자열**(언어 무관 동일 직렬화) 또는 int(동일 직렬화).
- **비-BMP supplementary 문자 키**: 모든 object key 는 ASCII(field/slug/op enum 등
  schema pattern 강제). 값의 한글 등 BMP 문자는 `ensure_ascii=False` + UTF-8 로
  결정적(JCS minimal escape).

따라서 본 canonicalizer 에는 runtime-의존 단계가 없다 — 외부 구현자가 RFC 8785 JCS
+ decimalString 규약만 따르면 byte-for-byte 동일 canonical form → 동일 hash.

`_` prefix — 운영 코드가 `from app.services import *` 로 import 하지 않도록.
명시적 import (`from app.services._jcs import canonicalize_jcs`) 만 허용.

관련 ADR:
- ADR-0002 D4 (Pack content_hash) — factor_pack 의 첫 사용처
- ADR-0008 D7 (Screen Run snapshot result_hash) — T30 의 결합점
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final

__all__ = [
    "canonicalize_jcs",
    "compute_content_hash",
    "HASH_PREFIX",
]

# Hash 표기 prefix — 모든 모듈이 동일 형식 사용.
HASH_PREFIX: Final[str] = "sha256:"


def canonicalize_jcs(value: Any) -> bytes:
    """RFC 8785 JCS subset 정규화 후 UTF-8 bytes 반환.

    factor_pack / krx_calendar / price_adjuster 의 inline 구현과 byte-for-byte
    동일. cross-module drift 영구 차단의 single source.

    Args:
        value: dict / list / 원시 (int/str/bool/None). NaN/Infinity float 거부.
            **Decimal 입력 차단** — `json.dumps` 가 Decimal 직접 직렬화 미지원
            (TypeError). 호출자가 `str(decimal_value)` 로 명시 변환 의무 (Momus
            M0 review W7). float 변환은 IEEE 754 잔차로 cross-runtime drift
            위험 → 항상 string 경유.

    Returns:
        UTF-8 encoded bytes — JSON canonical form.

    Raises:
        ValueError: NaN / Infinity float.
        TypeError: 직렬화 불가 타입 (Decimal / datetime / set 등 — 호출자가
            str 또는 isoformat 으로 변환 책임).
    """
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def compute_content_hash(
    body: dict[str, Any], *, exclude_key: str | None = "content_hash",
) -> str:
    """`body` 의 JCS canonical form 을 SHA-256 → `sha256:<hex>` 형식 반환.

    Args:
        body: dict — 일반적으로 schema 의 root document.
        exclude_key: hash 계산에서 제외할 top-level 키. None 이면 전체 사용.
            default "content_hash" — 자기 참조 chicken-and-egg 회피 (factor_pack /
            krx_calendar 와 일관). Screen Run 의 result_hash 는 `exclude_key=None`
            (자기 자신 미포함 의 별도 의미).

    Returns:
        "sha256:<64-hex>" 형식 문자열.
    """
    if exclude_key is not None:
        body = {k: v for k, v in body.items() if k != exclude_key}
    digest = hashlib.sha256(canonicalize_jcs(body)).hexdigest()
    return f"{HASH_PREFIX}{digest}"
