"""Auth dependency skeleton — T31 NextAuth 합류 전 placeholder.

M0 single-user mode — `SYSTEM_USER_ID` 자동 주입. T31 합류 후 JWT decode →
실제 user_id 로 교체. endpoint signature 는 `UserContext` wrapper 가 흡수하여
T31 migration cost 최소화 (oracle 자문 결정 5).

설계 원칙:

1. **`UserContext` wrapper** — endpoint 가 `user_id: UUID` 만 받는 대신
   `user: UserContext`. T31 후 `email / roles / is_system` 등 추가가 endpoint
   변경 없이 흡수.
2. **Fresh read of `SYSTEM_USER_ID`** — dependency 가 매 호출마다
   `_resolve_system_user_id()` 호출하여 env override 가 import-time singleton
   에 막히지 않도록 (oracle Risk-R3 / screen_run.py 의 SYSTEM_USER_ID 와 일관).
3. **`Annotated` 표기** — FastAPI 0.95+ 권장 + Python 3.10+ stdlib. T25~T28
   의 5+ endpoint 의 boilerplate 압축 (`CurrentUserDep` alias).

관련 ADR:
- ADR-0008 D7 — Screen Run 의 user_id 보존 (M0 = SYSTEM_USER_ID)
- T31 NextAuth (M0 후반) — 본 모듈의 JWT decoder 합류 시점
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.services.screen_run import _resolve_system_user_id


@dataclass(frozen=True, slots=True)
class UserContext:
    """현재 요청의 인증 context.

    M0 = 항상 `is_system=True` + `user_id=SYSTEM_USER_ID`. T31 합류 후 NextAuth
    JWT 의 sub 클레임 → user_id, 실제 사용자 등 false.

    Attributes:
        user_id: 사용자 UUID.
        is_system: M0 single-user placeholder 여부. T31 합류 후 false 가능.
    """

    user_id: UUID
    is_system: bool


def get_current_user() -> UserContext:
    """M0 single-user placeholder — `SYSTEM_USER_ID` 동적 resolve.

    매 호출마다 `_resolve_system_user_id()` (env 재평가) — import-time singleton
    의 테스트 격리 문제 회피 (oracle Risk-R3 + screen_run.py 의 동일 패턴).

    T31 합류 후:
        본 함수를 JWT decoder 로 교체. signature 유지 (`Depends` 가 그대로
        endpoint 의 `Annotated[UserContext, Depends(get_current_user)]`).
    """
    return UserContext(
        user_id=_resolve_system_user_id(),
        is_system=True,
    )


# Annotated alias — endpoint 의 boilerplate 압축 (oracle 결정 5).
CurrentUserDep = Annotated[UserContext, Depends(get_current_user)]
"""Endpoint 의 type-level dependency hint. 사용 예:

    @app.get("/api/runs")
    def list_runs(user: CurrentUserDep, ...):
        repo.fetch_recent(user_id=user.user_id)
"""
