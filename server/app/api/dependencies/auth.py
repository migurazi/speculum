"""Auth dependency — T68 NextAuth JWT 인증 골격 (ADR-0021 D1.1, 2026-06-02 개정).

`AUTH_SECRET` 환경변수 유무로 인증 동작이 **이분 게이팅**된다 — 이것이 회귀 0
의 핵심이다:

1. **AUTH_SECRET 미설정** (개발 / CI / 테스트 **기본**):
   기존 M0 동작 유지 — `_resolve_system_user_id()`(SPECULUM_USER_ID env) →
   `UserContext(user_id, is_system=True)`. user_repo 미사용. 무토큰 user-scoped
   호출이 전부 그대로 통과(기존 watchlists/notes/screener_sets/runs 테스트 회귀 0).

2. **AUTH_SECRET 설정** (운영):
   Authorization Bearer JWT 필수. NextAuth 가 발급한 HS256 JWT 를 PyJWT 로
   decode(서명·만료 검증) → `sub` 클레임(google_sub) → user_repo.get_by_google_sub
   or provision(JIT) → `UserContext(user_id, is_system=False)`. 무토큰 / invalid /
   만료 → **HTTPException(401)**.

설계 원칙 (변경 금지):

1. **`CurrentUserDep` signature 보존** — 라우트는 본 alias 만 의존. get_current_user
   본문 교체로 user-scoped 5 파일(watchlists/notes/screener_sets/runs)이 자동
   게이팅된다(라우트 무변경).
2. **screen/fact 라우트 인증 미적용** — screen.py / market.py / stocks.py /
   meta.py + runs.py 의 reproduce endpoint 는 CurrentUserDep 미사용(익명 fact,
   ADR-0021 D4/D5 함정 D). AUTH_SECRET 설정 운영에서도 무토큰 200.
3. **재현성 불변식** — user_id 는 result_hash 입력이 아니다. computed_by 의 hash
   입력화 금지(ADR-0021 D5).
4. **`UserContext` wrapper** — endpoint 가 `user: UserContext`. is_system 으로
   fallback(True) / 실유저(False) 구별.

관련 ADR:
- ADR-0021 D1.1 (2026-06-02 — NextAuth JWT 인증 골격 설계 확정), D4/D5 (fact
  익명 + 재현성 불변식)
- ADR-0008 D7 — Screen Run 의 user_id 보존
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status

from app.api.dependencies.repositories import UserRepoDep, get_user_repository
from app.core.config import get_auth_secret
from app.repositories.user_repository import UserRepository
from app.services.screen_run import _resolve_system_user_id

# JWT 검증 알고리즘 — NextAuth(HS256 대칭키) 와 동일. AUTH_SECRET 공유 키.
_JWT_ALGORITHMS: tuple[str, ...] = ("HS256",)


@dataclass(frozen=True, slots=True)
class UserContext:
    """현재 요청의 인증 context.

    - fallback (AUTH_SECRET 미설정) = `is_system=True` + `user_id=SYSTEM_USER_ID`.
    - 실유저 (AUTH_SECRET 설정 + 유효 JWT) = `is_system=False` + JIT 해소된 user_id.

    Attributes:
        user_id: 사용자 UUID.
        is_system: SYSTEM_USER_ID fallback 여부. JWT 검증 경로면 False.
    """

    user_id: UUID
    is_system: bool


def _extract_bearer_token(request: Request) -> str:
    """Authorization: Bearer <token> 헤더에서 token 추출. 없거나 형식 위반 → 401.

    운영(AUTH_SECRET 설정) 경로 전용 — 토큰 부재 자체가 미인증이므로 401.
    """
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 필요 — Authorization Bearer 토큰이 없습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


def _decode_jwt(token: str, secret: str) -> dict[str, object]:
    """HS256 JWT decode — 서명/만료 검증. 실패 시 401 (원인 비노출).

    PyJWT 가 exp 클레임이 있으면 만료를 자동 검증(ExpiredSignatureError). 서명
    불일치는 InvalidSignatureError. 둘 다 상위 InvalidTokenError 로 통일 catch —
    어떤 실패든 401(만료/위조 구별을 외부에 노출하지 않음).
    """
    try:
        # require=["exp"] — exp 클레임 없는 서명 토큰의 영구 유효를 차단(M2.1
        # conformance 이슈 2). NextAuth 발급 토큰은 항상 exp 보유하므로 무해한
        # hardening — exp 없는 토큰은 MissingRequiredClaimError(InvalidTokenError
        # 서브) → 401. aud/iss 검증은 운영 claims 확정 시(M3 운영 승격).
        return jwt.decode(
            token, secret, algorithms=list(_JWT_ALGORITHMS),
            options={"require": ["exp"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 인증 토큰입니다.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _resolve_user_from_claims(
    claims: dict[str, object], user_repo: UserRepository,
) -> UserContext:
    """JWT claims(sub=google_sub) → JIT provision → UserContext(is_system=False).

    sub 클레임 부재/비문자열 → 401. 같은 google_sub 는 항상 같은 user_id 로 해소.
    """
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰에 사용자 식별자(sub)가 없습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    google_sub = sub.strip()
    email_claim = claims.get("email")
    email = email_claim if isinstance(email_claim, str) else None

    # JIT provision — 기존 user 조회, 없으면 신규 생성(uuid4). 같은 sub = 같은 id.
    record = user_repo.get_by_google_sub(google_sub)
    if record is None:
        record = user_repo.provision(google_sub, email)
    return UserContext(user_id=record.id, is_system=False)


def get_current_user(
    request: Request = None,  # type: ignore[assignment]
    user_repo: UserRepoDep = None,  # type: ignore[assignment]
) -> UserContext:
    """현재 요청의 인증 context — AUTH_SECRET 게이팅 (ADR-0021 D1.1).

    **AUTH_SECRET 미설정 (개발/CI/테스트 기본)**:
        `_resolve_system_user_id()`(env 재평가) → `UserContext(.., is_system=True)`.
        request / user_repo 미사용 → 무토큰 user-scoped 호출이 그대로 통과(회귀 0).
        매 호출마다 env 재평가 — import-time singleton 의 테스트 격리 문제 회피.

    **AUTH_SECRET 설정 (운영)**:
        Authorization Bearer JWT 필수 → HS256 decode(서명/만료 검증) → sub
        (google_sub) → user_repo.get_by_google_sub or provision →
        `UserContext(.., is_system=False)`. 무토큰/invalid/만료 → HTTPException(401).

    Args:
        request: FastAPI 가 주입. fallback 경로에서는 미사용(직접 호출 시 None 허용).
        user_repo: JIT provision repository (UserRepoDep). fallback 경로 미사용.

    Note (signature 보존):
        라우트는 `CurrentUserDep`(Annotated alias)만 의존하므로 본 함수의 파라미터
        추가가 라우트 무변경. request/user_repo 가 Optional default 인 것은 인자
        없는 직접 호출(`get_current_user()`)을 fallback 모드에서 허용하기 위함.
    """
    secret = get_auth_secret()
    if secret is None:
        # ── fallback (AUTH_SECRET 미설정) — 기존 M0 동작. request/user_repo 무시.
        return UserContext(
            user_id=_resolve_system_user_id(),
            is_system=True,
        )

    # ── 운영 (AUTH_SECRET 설정) — Bearer JWT 필수.
    if request is None:
        # 방어적: FastAPI 경로는 항상 request 주입. 직접 호출 + AUTH_SECRET 설정
        # 조합은 미인증으로 간주(401) — 운영에서 토큰 없는 호출과 동일 처리.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 필요 — 요청 컨텍스트가 없습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user_repo is None:
        # FastAPI DI 가 항상 채움. None 이면 wiring 오류 — 명시 실패.
        raise RuntimeError(
            "user_repo 가 주입되지 않음 — AUTH_SECRET 설정 시 UserRepoDep 필요"
        )
    token = _extract_bearer_token(request)
    claims = _decode_jwt(token, secret)
    return _resolve_user_from_claims(claims, user_repo)


def get_optional_user(
    request: Request,
    user_repo: UserRepoDep,
) -> UserContext | None:
    """선택적 인증 — 토큰 있으면 실유저, 없으면 None (정의만, 미배선).

    ADR-0021 D1.1 의 향후 mixed-mode endpoint(인증 시 개인화, 미인증 시 익명)용
    예약. 현재 어떤 라우트도 본 dependency 를 사용하지 않는다(배선 X).

    동작 (정의 의도):
        - AUTH_SECRET 미설정 → SYSTEM_USER_ID fallback (get_current_user 와 동일).
        - AUTH_SECRET 설정 + Bearer 토큰 있음 → JWT 검증 → 실유저(검증 실패는 401).
        - AUTH_SECRET 설정 + 토큰 없음 → None (401 아님 — 익명 허용).
    """
    secret = get_auth_secret()
    if secret is None:
        return UserContext(
            user_id=_resolve_system_user_id(),
            is_system=True,
        )
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        # 토큰 없음 — 익명 허용(None). get_current_user 와 달리 401 아님.
        return None
    claims = _decode_jwt(token.strip(), secret)
    return _resolve_user_from_claims(claims, user_repo)


# Annotated alias — endpoint 의 boilerplate 압축. 라우트는 본 alias 만 의존하므로
# get_current_user 본문 교체가 라우트 무변경 (signature 보존).
CurrentUserDep = Annotated[UserContext, Depends(get_current_user)]
"""Endpoint 의 type-level dependency hint. 사용 예:

    @app.get("/api/runs")
    def list_runs(user: CurrentUserDep, ...):
        repo.fetch_recent(user_id=user.user_id)
"""

# get_user_repository re-export — FastAPI Depends 가 본 dependency 의 sub-dependency
# (UserRepoDep) 를 해소할 때 import 가능하도록. (auth 가 repositories 에 의존.)
__all__ = [
    "CurrentUserDep",
    "UserContext",
    "get_current_user",
    "get_optional_user",
    "get_user_repository",
]
