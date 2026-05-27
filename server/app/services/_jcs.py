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

cross-runtime (Python ↔ Node) 동일 hash 보장 — 단, 비-BMP supplementary
characters 키나 매우 큰/작은 float (지수 표기) 에서 차이 발생 가능. Speculum 의
모든 key 는 ASCII, numeric 은 int 또는 표준 decimal — 안전.

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

    Returns:
        UTF-8 encoded bytes — JSON canonical form.

    Raises:
        ValueError: NaN / Infinity float 또는 직렬화 불가 타입.
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
