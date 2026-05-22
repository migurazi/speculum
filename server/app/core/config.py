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
