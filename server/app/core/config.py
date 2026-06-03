"""환경별 설정 — 환경변수 기반.

본 모듈은 minimal scaffold 시점의 설정. 실제 운영용 settings (DB URL, OAuth secret
등) 는 Phase 1 (T13 이후) 에서 확장.

관련 ADR:
- ADR-0007 D4.4 — middleware 가 사용하는 ForbiddenWordsPolicy 환경 결정
"""

from __future__ import annotations

import os
import sys
from enum import Enum
from pathlib import Path


def _load_dotenv_files() -> None:
    """프로세스 시작 시 `.env` 로드 — 서버(uvicorn) / 배치 / 스크립트가 본 모듈을
    import 하는 시점에 1회 실행 (config 는 가장 이른 chokepoint).

    - **pytest 중에는 미로드** — 개발자 `.env` 의 DB URL 등이 단위 테스트 환경을
      오염시키지 않도록(테스트는 자체 fixture/in-memory DB 사용).
    - `override=False`: 이미 set 된 OS 환경변수(set/setx)가 .env 보다 우선.
    - python-dotenv 미설치 시 graceful skip (`pip install -e .` 후 활성).
    - 탐색: repo 루트 `.env` (.env.example 과 같은 위치) + server/ `.env` 둘 다.
    """
    if "pytest" in sys.modules:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # config.py = server/app/core/config.py → parents[2]=server, [3]=repo 루트
    here = Path(__file__).resolve()
    for env_path in (here.parents[3] / ".env", here.parents[2] / ".env"):
        if env_path.is_file():
            load_dotenv(env_path, override=False)


# import 시점 즉시 로드 — 이후 os.environ.get(...) 들이 .env 값을 봄.
_load_dotenv_files()


class Environment(str, Enum):
    """Speculum 실행 환경 — 환경변수 `SPECULUM_ENV` 로 결정.

    DEV: 개발자 로컬. middleware = WARN_ONLY. 응답 그대로 통과 + 로그.
    STAGING: 사용자 한 두 명의 staging. middleware = REDACT. 검출 어휘만 별표 처리.
    PROD: 운영. middleware = BLOCK. 응답 500 으로 swap + Sentry alert.

    설정되지 않으면 DEV 로 기본 (개발 안전).
    """

    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


def get_environment() -> Environment:
    """환경변수에서 현재 환경 결정. 알 수 없으면 DEV."""
    raw = os.environ.get("SPECULUM_ENV", "").lower().strip()
    try:
        return Environment(raw) if raw else Environment.DEV
    except ValueError:
        # 알 수 없는 값 — 안전한 DEV 로 fallback.
        return Environment.DEV


def get_auth_secret() -> str | None:
    """`AUTH_SECRET` 환경변수 — T68 NextAuth JWT 검증의 게이팅 키 (ADR-0021 D1.1).

    본 getter 가 인증 동작 전체를 게이팅한다 (`auth.get_current_user`):

        - **None / 빈 문자열** — 개발 / CI / 테스트 **기본**. JWT 검증 비활성 →
          `auth.get_current_user` 가 `SYSTEM_USER_ID` fallback (기존 M0 동작
          그대로). 무토큰 user-scoped 호출이 전부 통과 (회귀 0).
        - **설정 (운영)** — Authorization Bearer JWT 필수. NextAuth 가 발급한
          HS256 JWT 를 본 secret 으로 검증 (`jwt.decode(..., algorithms=["HS256"])`).

    NextAuth 의 `AUTH_SECRET`(NEXTAUTH_SECRET) 과 동일 값을 backend 에 주입해야
    JWT 서명 검증이 성립한다 (HS256 = 대칭키 공유).

    Returns:
        secret 문자열 (truthy) 또는 None (fallback 모드).

    Note:
        단순 환경변수 read + trim. secret 의 강도(길이/엔트로피) 검증은 하지
        않음 — 운영 배포 hardening 의 책임 (env 주입 단계).
    """
    raw = os.environ.get("AUTH_SECRET", "").strip()
    return raw or None


def assert_auth_config_for_environment() -> None:
    """운영(PROD)에서 AUTH_SECRET 필수 — 부재 시 startup fail (conformance 이슈 3).

    AUTH_SECRET 미설정 = 인증 single-user fallback(`get_auth_secret` None →
    `auth.get_current_user` 가 SYSTEM_USER_ID 반환). dev/test 는 이 fallback 이
    의도(회귀 0)이나, **운영(PROD)에서는 전 user-scoped 요청이 익명으로 SYSTEM_USER
    를 받는 silent degrade**(인증 우회)다. PROD 환경에서 AUTH_SECRET 누락을 명시
    fail 로 차단해 사고 경로를 닫는다(M2.1 momus 이슈 3 — single-user silent
    degrade 방지). STAGING 은 운영 데이터 노출이 제한적이라 강제 대상 아님(DEV 와
    동일 허용 — 인증 도입 전 staging 테스트 편의).

    `create_app` 부팅 시 호출 — 운영 배포에서 AUTH_SECRET 미주입이면 앱이 뜨지 않음.

    Raises:
        RuntimeError: PROD 환경 + AUTH_SECRET 부재.
    """
    if get_environment() is Environment.PROD and get_auth_secret() is None:
        raise RuntimeError(
            "운영(SPECULUM_ENV=prod) 환경에서 AUTH_SECRET 이 설정되지 않았습니다 — "
            "인증이 single-user fallback(SYSTEM_USER_ID)으로 silent degrade 되어 "
            "전 user-scoped 요청이 익명 통과합니다. AUTH_SECRET 을 설정하거나 "
            "비운영(SPECULUM_ENV=dev/staging)으로 실행하세요."
        )


def get_database_url() -> str | None:
    """`SPECULUM_DATABASE_URL` 환경변수 — T13 wiring 의 SQL/Fake 자동 선택 키.

    설정값:
        - `postgresql+psycopg://user:pass@host:port/dbname` — 운영 PostgreSQL.
        - `sqlite:///path/to.sqlite` — 로컬 dev / single-machine.
        - `sqlite:///:memory:` — integration test (in-memory).
        - **None / 빈 문자열** — Fake repository 모드 (M0 default, T13 wiring 전
          과 같은 동작).

    Returns:
        URL 문자열 (truthy) 또는 None (Fake 모드).

    Note:
        URL scheme 검증은 본 함수가 하지 않음 — SQLAlchemy `create_engine` 이
        invalid scheme 에서 raise. 본 함수는 단순 환경변수 read + trim.
    """
    raw = os.environ.get("SPECULUM_DATABASE_URL", "").strip()
    return raw or None
