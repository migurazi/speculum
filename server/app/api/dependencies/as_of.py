"""AsOfPolicy FastAPI dependency — ADR-0008 D6 API contract.

`as_of` query param → `AsOfPolicy.normalize()` → `NormalizedAsOf` 주입 + Response
header (`X-AsOf`, `X-AsOf-Defaulted`, `X-AsOf-Snapped`, `X-AsOf-Original`) 자동
채움.

설계 원칙 (oracle 자문 P0 반영):

1. **Service layer 분리** — `as_of_policy.py` 는 framework-agnostic (일배치 +
   API + Frontend picker 모두 사용). FastAPI `Query`/`Response` import 는 본
   dependency 모듈에서만.
2. **Native `date` coercion** — `as_of: date | None = Query(None)` 으로 FastAPI
   의 422 (RFC-compliant) 자동 처리. 자체 parse 회피.
3. **Response 직접 inject** — background task X (header set 시점이 이미 늦음).
4. **모든 정책 path 에서 header 통과 확인** — BLOCK 모드는 header 손실 가능 (R2)
   — middleware 통합 test 에서 명시.

관련 ADR / 문서:
- ADR-0008 D6 — API contract (as_of query + default fill + header)
- ADR-0008 D1.1 — picker UI 의 snap 표시 (`X-AsOf-Original` 입력)
- M0_PLAN T24
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Response, status

from app.services.as_of_policy import (
    AsOfInFutureError,
    AsOfOutOfRangeError,
    AsOfPolicy,
    NormalizedAsOf,
)


def get_normalized_as_of(
    response: Response,
    as_of: Annotated[
        date | None,
        Query(
            description=(
                "PIT 기준 일자 (YYYY-MM-DD, KST). 누락 시 오늘(영업일) 자동 채움. "
                "휴장일이면 가장 가까운 직전 영업일로 snap. 미래는 400."
            ),
            examples=["2024-10-01"],
        ),
    ] = None,
) -> NormalizedAsOf:
    """`as_of` query param 을 정규화 + Response header 주입.

    Headers:
        X-AsOf: 정규화 결과 일자 (ISO 8601). 항상 포함.
        X-AsOf-Defaulted: "true" — 입력 None 으로 today fill (ADR-0008 D6).
        X-AsOf-Snapped: "true" — 휴장일 입력으로 직전 영업일 snap (ADR-0008 D1.1).
        X-AsOf-Original: snap 전 입력 일자 (ISO 8601). Frontend picker UI 의
            toast 메시지 ("2024-10-05 는 휴장이라 2024-10-04 로 조정") 입력.

    Returns:
        `NormalizedAsOf` — value / was_defaulted / was_snapped / original_input /
        pit_policy_version.

    Raises:
        HTTPException(400):
            - `AsOfInFutureError` → code="AS_OF_IN_FUTURE"
            - `AsOfOutOfRangeError` → code="AS_OF_OUT_OF_RANGE"
        (도메인 예외는 `exception_handlers.py` 의 handler 가 JSON 변환 — 본
        함수는 그대로 raise.)
    """
    try:
        normalized = AsOfPolicy.normalize(as_of)
    except AsOfInFutureError:
        raise  # exception_handler 가 처리
    except AsOfOutOfRangeError:
        raise  # exception_handler 가 처리

    # Header injection — 항상 X-AsOf set + 조건부 메타데이터.
    response.headers["X-AsOf"] = normalized.value.isoformat()
    if normalized.was_defaulted:
        response.headers["X-AsOf-Defaulted"] = "true"
    if normalized.was_snapped:
        response.headers["X-AsOf-Snapped"] = "true"
        if normalized.original_input is not None:
            response.headers["X-AsOf-Original"] = normalized.original_input.isoformat()

    return normalized


# Annotated alias — endpoint signature boilerplate 압축 (oracle 결정 5 패턴).
NormalizedAsOfDep = Annotated[NormalizedAsOf, Depends(get_normalized_as_of)]
"""Endpoint 의 type-level dependency hint. 사용 예:

    @app.get("/api/screen")
    def screen(as_of: NormalizedAsOfDep, ...):
        ...
"""
