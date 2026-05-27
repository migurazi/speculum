"""Domain 예외 → HTTP JSON 변환.

설계 원칙 (oracle 자문 P0 / R1 반영):

1. **단일 base handler + isinstance 분기** — `AsOfPolicyError` 의 sub-class 분기.
   `code` 필드로 frontend 가 분기 가능 (ADR-0007 D4.4 의 BLOCK 응답 `{"detail",
   "code"}` 와 일관).
2. **`RequestValidationError` input sanitize** — FastAPI 의 default 422 응답이
   query/body 값을 echo. 사용자가 `as_of=추천종목` 같은 입력 시 응답 본문이
   금지 어휘 echo → middleware BLOCK → 500. 본 handler 가 입력 값을 sanitize
   후 generic message 만 반환.
3. **middleware 통과 보장** — 본 handler 의 응답이 `ForbiddenWordsGuardMiddleware`
   를 통과해야 함. 응답 메시지의 vocabulary 는 의도적으로 안전 어휘만 사용.

관련 ADR:
- ADR-0007 D4.4 — middleware policy 별 응답 처리
- ADR-0008 D6 — as_of 400/422 처리 규약
"""

from __future__ import annotations

from typing import Final

from fastapi import FastAPI, status
from fastapi.exceptions import RequestValidationError
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from app.services.as_of_policy import (
    AsOfInFutureError,
    AsOfOutOfRangeError,
    AsOfPolicyError,
)

# 응답 code 값 — 안정적 식별자. frontend 분기.
_AS_OF_FUTURE: Final[str] = "AS_OF_IN_FUTURE"
_AS_OF_OUT_OF_RANGE: Final[str] = "AS_OF_OUT_OF_RANGE"
_AS_OF_GENERIC: Final[str] = "AS_OF_INVALID"
_VALIDATION_GENERIC: Final[str] = "INVALID_REQUEST"


def register_exception_handlers(app: FastAPI) -> None:
    """Domain exception 의 JSON handler 를 app 에 등록.

    `create_app()` 가 1 회 호출.
    """

    @app.exception_handler(AsOfPolicyError)
    async def as_of_policy_handler(
        request: Request, exc: AsOfPolicyError,
    ) -> JSONResponse:
        """`AsOfPolicy` 도메인 예외 → 400 JSON. sub-class 별로 code / detail 분기."""
        if isinstance(exc, AsOfInFutureError):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "code": _AS_OF_FUTURE,
                    "detail": (
                        "as_of 가 오늘(KST)보다 미래입니다. "
                        "ADR-0008 D6 의 PIT 정책상 미래 일자는 허용되지 않습니다."
                    ),
                    "requested": exc.requested.isoformat(),
                    "today_kst": exc.today_kst.isoformat(),
                },
            )
        if isinstance(exc, AsOfOutOfRangeError):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "code": _AS_OF_OUT_OF_RANGE,
                    "detail": (
                        "as_of 가 검증된 KRX 캘린더 범위 밖입니다. "
                        "운영 캘린더 갱신 후 가능 (T17.1 work-order)."
                    ),
                    "requested": exc.requested.isoformat(),
                    "min_date": exc.min_date.isoformat(),
                    "max_date": exc.max_date.isoformat(),
                },
            )
        # AsOfPolicyError base 또는 미래 신규 sub-class — generic 400.
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": _AS_OF_GENERIC,
                "detail": "as_of 입력이 정책에 위반됩니다.",
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError,
    ) -> JSONResponse:
        """FastAPI 의 422 → input echo 제거 + generic message (oracle R1).

        Default FastAPI handler 는 입력값 `ctx.input` 을 응답 body 에 그대로
        echo. 사용자가 `as_of=추천종목` 같은 dirty input 시 응답이 금지 어휘
        포함 → ForbiddenWordsGuardMiddleware 가 BLOCK 으로 swap → 사용자가
        진짜 원인 (잘못된 형식) 못 봄. 본 handler 가 input 을 노출하지 않는
        generic message 만 반환.
        """
        # error location 만 보존 — value 는 노출 X.
        # FastAPI errors 형식: [{"loc": [...], "msg": "...", "type": "..."}]
        sanitized: list[dict] = []
        for err in exc.errors():
            sanitized.append({
                "loc": list(err.get("loc", [])),
                "msg": _sanitize_msg(err.get("msg", "")),
                "type": err.get("type", "unknown"),
            })
        # Starlette 0.46+ 의 HTTP_422_UNPROCESSABLE_CONTENT 와 동의어 — 호환성
        # 위해 정수 리터럴 사용 (FastAPI / Starlette 버전 무관).
        return JSONResponse(
            status_code=422,
            content={
                "code": _VALIDATION_GENERIC,
                "detail": "요청 형식이 올바르지 않습니다.",
                "errors": sanitized,
            },
        )


def _sanitize_msg(msg: str) -> str:
    """Validation error message 의 input echo 부분을 제거.

    Pydantic / FastAPI 의 default msg 가 `Input should be a valid date, ...,
    input_value='추천종목'` 같은 형태. 단순히 `input_value=...` 이후를 잘라냄.
    완벽한 sanitize 보다는 통상 echo 패턴 차단.
    """
    # 가장 흔한 echo 패턴 — `input_value=`, `input_type=`.
    for marker in (", input_value=", " input_value=", ", input_type=", " input_type="):
        idx = msg.find(marker)
        if idx >= 0:
            msg = msg[:idx]
    return msg
