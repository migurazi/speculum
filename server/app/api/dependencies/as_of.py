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

from fastapi import Depends, Query, Response

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


def get_browse_as_of(
    response: Response,
    as_of: Annotated[
        date | None,
        Query(
            description=(
                "PIT 기준 일자 (YYYY-MM-DD, KST) — browse 경로. 누락 시 오늘. "
                "verified 캘린더 범위 밖이면 weekday 근사로 graceful degrade "
                "(X-AsOf-Degraded). 미래는 400. (ROADMAP_v2 §2.3 — 가용성 우선)"
            ),
            examples=["2024-10-01"],
        ),
    ] = None,
) -> NormalizedAsOf:
    """browse(읽기) 경로 전용 정규화 — verified 범위 밖을 degrade(가용성 우선).

    `get_normalized_as_of`(strict) 와 달리 `mode="browse"` 로 호출 — 캘린더 범위
    밖 as_of(예: 2026 vs 2024 캘린더)를 `AsOfOutOfRangeError` 대신 weekday 근사로
    보정해 **오늘 날짜에도 200** 을 보장한다(ROADMAP_v2 §2.3 R3). 미래는 여전히
    `AsOfInFutureError`(400). **종목 상세·시장·비교 등 read-only 거울 경로 전용 —
    frozen/재현(runs·backtest)에는 절대 미사용**(정확 휴장일 필수).

    Headers: strict 와 동일 + `X-AsOf-Degraded: true`(범위 밖 근사 시).
    """
    normalized = AsOfPolicy.normalize(as_of, mode="browse")

    response.headers["X-AsOf"] = normalized.value.isoformat()
    if normalized.was_defaulted:
        response.headers["X-AsOf-Defaulted"] = "true"
    if normalized.was_snapped:
        response.headers["X-AsOf-Snapped"] = "true"
        if normalized.original_input is not None:
            response.headers["X-AsOf-Original"] = normalized.original_input.isoformat()
    if normalized.was_degraded:
        # verified 캘린더 밖 weekday 근사 — 휴장일 미반영 고지(Fidelity: 정확성이
        # 아닌 가용성 degrade 임을 헤더로 노출).
        response.headers["X-AsOf-Degraded"] = "true"
        if normalized.original_input is not None:
            response.headers["X-AsOf-Original"] = normalized.original_input.isoformat()

    return normalized


# Annotated alias — endpoint signature boilerplate 압축 (oracle 결정 5 패턴).
NormalizedAsOfDep = Annotated[NormalizedAsOf, Depends(get_normalized_as_of)]
BrowseAsOfDep = Annotated[NormalizedAsOf, Depends(get_browse_as_of)]
"""browse(읽기) 경로 전용 — verified 범위 밖 degrade. frozen 경로엔 NormalizedAsOfDep."""
"""Endpoint 의 type-level dependency hint. 사용 예:

    @app.get("/api/screen")
    def screen(as_of: NormalizedAsOfDep, ...):
        ...
"""
