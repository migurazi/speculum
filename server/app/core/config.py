"""환경별 설정 — 환경변수 기반.

본 모듈은 minimal scaffold 시점의 설정. 실제 운영용 settings (DB URL, OAuth secret
등) 는 Phase 1 (T13 이후) 에서 확장.

관련 ADR:
- ADR-0007 D4.4 — middleware 가 사용하는 ForbiddenWordsPolicy 환경 결정
"""

from __future__ import annotations

import os
from enum import Enum


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
