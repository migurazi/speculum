"""Publisher identity — ADR-0034 D4 사칭 차단 헬퍼 (인증 publisher 발급).

M6 #3 (M6_PLAN §2.1) — `@{handle}/...` v2 namespace pack 의 발급권을 인증된
publisher 에 못박는 사칭 차단 layer. v1 slug(speculum-builtin / `community/` /
`user/`)는 본 모듈과 무관(통과) — v2(`@` prefix) slug 만 검사한다.

두 종류의 사칭을 차단한다:
1. **예약 handle** — canonical(빌트인) / v1 tier 의 이름(community/user 등)을
   handle 로 claim 하거나 v2 slug 에 쓰는 것. `_RESERVED_HANDLES` 가 거부 대상.
2. **타 publisher 사칭** — A 가 claim 한 handle 의 `@{handle}/...` pack 을 B 가
   발급하는 것. `verify_publisher_owns_slug` 가 발급 user 의 claim handle 과
   slug 의 publisher 일치를 강제한다.

본 사이클(#3)은 헬퍼 제공 + 테스트만 — v2 저장 route 연계(발급 시 verify 호출)는
#4 scope. 본 모듈의 verify 헬퍼는 #4 가 그대로 호출할 계약이다.

관련 ADR / 문서:
- ADR-0034 D4 (publisher 인증 발급 — handle claim + 사칭 차단)
- ADR-0034 D3 (v2 namespace `@{publisher}/{slug}` — community tier 재사용)
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from app.repositories.publisher_repository import PublisherRepository

__all__ = [
    "PublisherImpersonationError",
    "ReservedHandleError",
    "assert_handle_not_reserved",
    "parse_v2_publisher",
    "verify_publisher_owns_slug",
]

# 예약 handle — canonical(빌트인) / v1 tier 이름. 이 handle 들은 claim 불가 +
# v2 slug 의 publisher 로 사용 불가(canonical/v1 tier 사칭 차단). "community"/
# "user" 는 v1 tier prefix(derive_tier), "speculum-builtin"/"speculum" 은
# canonical/브랜드 사칭. 소문자 비교(handle pattern 이 소문자만 허용).
_RESERVED_HANDLES: Final[frozenset[str]] = frozenset(
    {"community", "user", "speculum-builtin", "speculum"}
)


class ReservedHandleError(Exception):
    """예약 handle claim / v2 발급 시도 — canonical/v1 tier 사칭(ADR-0034 D4).

    route 가 422(Unprocessable)로 매핑한다(claim 거부).
    """


class PublisherImpersonationError(Exception):
    """v2 slug 의 publisher 가 발급 user 의 claim handle 과 불일치 — 사칭(D4).

    미claim(handle 없음) 또는 다른 publisher 의 slug 발급 시도. route 가 403
    (Forbidden)으로 매핑한다(발급 거부 — #4 연계).
    """


def parse_v2_publisher(pack_slug: str) -> str | None:
    """`@{publisher}/{slug}` 에서 publisher 추출 — v1 slug 면 None.

    v2 identity namespace 는 `@` prefix 로 인코딩된다(ADR-0034 D3). `@` 로 시작
    하지 않으면 v1(speculum-builtin / `community/` / `user/`) 이므로 None(검사
    무관). `@` 로 시작하나 `/` 가 없거나 publisher 가 비면 None(형식 위반은 발급
    스키마가 별도 거부 — 본 헬퍼는 publisher 추출만).

    Examples:
        parse_v2_publisher("@alice/value-pack") -> "alice"
        parse_v2_publisher("user/p") -> None
        parse_v2_publisher("community/p") -> None
        parse_v2_publisher("@/p") -> None
        parse_v2_publisher("@alice") -> None
    """
    if not pack_slug.startswith("@"):
        return None
    rest = pack_slug[1:]
    publisher, sep, _slug = rest.partition("/")
    if not sep or not publisher:
        # `@` 만 있고 `/` 없음 또는 publisher 비어있음 — 형식 위반(추출 불가).
        return None
    return publisher


def assert_handle_not_reserved(handle: str) -> None:
    """예약 handle 이면 거부 — canonical/v1 tier 사칭 차단(ADR-0034 D4).

    claim(POST /api/publishers) 과 v2 발급 양쪽에서 같은 규칙으로 쓰인다. 소문자
    비교(handle pattern 이 소문자만 허용하나 방어적으로 lower()).

    Raises:
        ReservedHandleError: handle 이 예약어(_RESERVED_HANDLES).
    """
    if handle.lower() in _RESERVED_HANDLES:
        raise ReservedHandleError(
            f"handle '{handle}' 는 예약어 — canonical/내장 tier 사칭 차단으로 "
            f"claim/발급 불가(ADR-0034 D4). 예약: {sorted(_RESERVED_HANDLES)}."
        )


def verify_publisher_owns_slug(
    *,
    user_id: UUID,
    pack_slug: str,
    publisher_repo: PublisherRepository,
) -> None:
    """v2 slug 의 publisher 가 발급 user 의 claim handle 과 일치함을 강제 — D4.

    v1 slug(`@` prefix 아님)는 publisher 사칭 개념이 없으므로 통과(무관). v2 slug
    면 발급 user 가 claim 한 publisher(get_by_user_id)의 handle 과 slug 의
    publisher 가 일치해야 한다 — 미claim 이거나 불일치면 사칭(거부).

    Args:
        user_id: 발급(저장) user — CurrentUserDep 가 결정(IDOR — body 미수신).
        pack_slug: 발급 pack 의 slug.
        publisher_repo: claim 조회 repository.

    Raises:
        PublisherImpersonationError: v2 slug 인데 발급 user 가 미claim 이거나
            slug publisher 가 claim handle 과 불일치(사칭).
    """
    publisher = parse_v2_publisher(pack_slug)
    if publisher is None:
        # v1 slug — publisher 사칭 무관(통과).
        return
    claimed = publisher_repo.get_by_user_id(user_id)
    if claimed is None:
        # publisher 미claim user 가 v2 slug 발급 시도 — 사칭(claim 선행 필요).
        raise PublisherImpersonationError(
            f"publisher handle 미claim 사용자가 v2 pack '{pack_slug}' 발급 시도 — "
            f"@{publisher} 발급권은 handle claim 이 선행돼야 합니다(ADR-0034 D4)."
        )
    if claimed.handle != publisher:
        # 타 publisher 의 slug 발급 시도 — 사칭(발급권은 claim user 에 귀속).
        raise PublisherImpersonationError(
            f"사용자의 claim handle '{claimed.handle}' 와 pack slug 의 "
            f"publisher '@{publisher}' 불일치 — 타 publisher 사칭 발급 거부"
            f"(ADR-0034 D4)."
        )
