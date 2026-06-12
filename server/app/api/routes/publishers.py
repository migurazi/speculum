"""Publisher claim endpoint — M6 #3 인증 publisher handle 발급 (ADR-0034 D4).

인증 user 가 자기 publisher handle 을 claim 한다. 그 user 만 `@{handle}/...` v2
pack 을 발급하도록 사칭을 차단한다(§2.1). user↔handle 1:1, handle 인스턴스 유일.

Endpoints:
    POST /api/publishers   — publisher handle claim (200)

설계 (notes / portfolio 선례 일관):
- CurrentUserDep — body 에 user_id X(IDOR 차단). user_id 는 CurrentUserDep 로만 주입.
- Pydantic v2 strict input(handle pattern) + from_domain output.
- 예약 handle(canonical/v1 tier 사칭) → 422(ReservedHandleError).
- 중복(handle 인스턴스 유일 / user 1:1) → 409(PublisherClaimError).
- handle pattern 위반 → 422(Pydantic Field).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.dependencies.auth import CurrentUserDep
from app.api.dependencies.repositories import PublisherRepoDep
from app.repositories.publisher_repository import PublisherClaimError
from app.schemas.publishers import PublisherClaimIn, PublisherOut
from app.services.publisher_identity import (
    ReservedHandleError,
    assert_handle_not_reserved,
)

router = APIRouter(prefix="/api/publishers", tags=["publishers"])


@router.post("", response_model=PublisherOut)
async def claim_publisher(
    user: CurrentUserDep,
    repo: PublisherRepoDep,
    body: PublisherClaimIn,
) -> PublisherOut:
    """publisher handle claim — 인증 user 1:1, handle 인스턴스 유일(사칭 차단).

    user_id 는 CurrentUserDep 가 결정(body 에서 안 받음 — IDOR 차단). 예약 handle
    (canonical/v1 tier 사칭)은 422, 중복(handle/user)은 409.
    """
    # 예약 handle 거부 — canonical/v1 tier 사칭 차단(claim 단계 선검사). 422.
    try:
        assert_handle_not_reserved(body.handle)
    except ReservedHandleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # claim — handle 인스턴스 유일 + user 1:1 강제(중복 시 409). 같은 user+handle
    # 재요청은 idempotent(기존 반환).
    try:
        publisher = repo.claim(user_id=user.user_id, handle=body.handle)
    except PublisherClaimError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PublisherOut.from_domain(publisher)
