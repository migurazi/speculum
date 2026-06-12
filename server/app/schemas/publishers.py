"""Publisher wire schemas — M6 #3 인증 publisher handle claim (ADR-0034 D4).

설계 (notes / portfolio 선례 일관):
- Pydantic v2 strict input + extra=forbid + frozen.
- `from_domain` 단방향 factory.
- **user_id 미노출**(IDOR — route 의 CurrentUserDep 가 결정).
- handle pattern `^[a-z0-9][a-z0-9-]*$` ≤64 — DB String(64) + v2 namespace 규약.
  예약 handle 거부(canonical/v1 사칭)는 route 의 assert_handle_not_reserved.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.repositories.publisher_repository import Publisher

__all__ = [
    "PublisherClaimIn",
    "PublisherOut",
]

# strict input + extra=forbid + frozen. handle 은 pattern 으로 형식 강제.
_STRICT_MODEL_CONFIG = ConfigDict(strict=True, extra="forbid", frozen=True)


class PublisherClaimIn(BaseModel):
    """POST /api/publishers body — publisher handle claim.

    user_id 미노출(CurrentUserDep). handle 은 소문자 영숫자/하이픈, 영숫자로 시작
    (pattern), ≤64. 예약 handle(canonical/v1 사칭) 거부는 route 가 422 로 별도 수행.
    """

    model_config = _STRICT_MODEL_CONFIG

    # ^[a-z0-9][a-z0-9-]*$ — 소문자 영숫자 시작, 영숫자/하이픈만. v2 namespace
    # `@{handle}/...` 규약. 위반 시 Pydantic 422.
    handle: str = Field(
        min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$",
    )


class PublisherOut(BaseModel):
    """Publisher wire output — handle + created_at(user_id 미노출, IDOR)."""

    model_config = _STRICT_MODEL_CONFIG

    handle: str
    created_at: datetime

    @classmethod
    def from_domain(cls, publisher: Publisher) -> PublisherOut:
        return cls(
            handle=publisher.handle,
            created_at=publisher.created_at,
        )
