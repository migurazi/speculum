"""Custom Pack Repository — ADR-0022 D9 사용자 정의 pack 영속화 (user-scoped).

Factor Lab 사용자 정의 pack JSON 의 user-scoped 저장. `notes_repository.py` 의
owner-check CRUD 패턴을 복제하되 **append-only immutable** — UPDATE 메서드 없이
save(생성) / list / get / delete 만 제공한다(screen_runs 정신, notes 의 mutable
과 다름).

설계 원칙 (절대 준수 — ADR-0022 D9):
1. 모든 method 가 `user_id: UUID` keyword-only — IDOR 차단. user_id 는 route 의
   CurrentUserDep 만 주입, request body 에서 절대 받지 않음.
2. owner-check — get/delete 는 미존재 또는 owner mismatch 시 None/False(404).
   mismatch 와 미존재를 구별하지 않아 user 정보 누출 차단.
3. **list_for_user 는 반드시 user_id 필터** — 누락 시 전 사용자 pack 누출(가장
   위험). DB 인덱스로도 보강.
4. **append-only immutable** — UPDATE 없음. 같은 (user_id, pack_slug, version):
   - 같은 content_hash → idempotent(기존 row 반환).
   - 다른 content_hash → CustomPackDataError(충돌 — 같은 버전의 다른 정의 거부).
5. **봉인된 body 입력 전제** — save 는 route 가 이미 validate_custom_pack 통과 +
   compute_pack_hash 재봉인한 body 를 받는다(클라이언트 hash 무시). repository 는
   body 에서 pack_slug / version / content_hash / factor_count 를 추출.

content_hash 는 body['content_hash'] 에 봉인돼 있어야 한다(route 가 강제). 누락/
형식 위반 body 는 CustomPackDataError.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID, uuid4

__all__ = [
    "CustomPack",
    "CustomPackDataError",
    "CustomPackRepository",
    "FakeCustomPackRepository",
    "extract_pack_fields",
    "extract_public_meta",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_PUBLIC",
    "VALID_VISIBILITIES",
]

# 공유 visibility 값 도메인 — ADR-0028 D1. route/repository 가 두 값만 허용.
VISIBILITY_PRIVATE = "private"
VISIBILITY_PUBLIC = "public"
VALID_VISIBILITIES = frozenset({VISIBILITY_PRIVATE, VISIBILITY_PUBLIC})


class CustomPackDataError(Exception):
    """Custom pack invariant 위반 — body 형식 위반 또는 (slug,version) 충돌.

    충돌(같은 user_id+pack_slug+version 의 다른 content_hash)은 본 예외로 신호.
    route 가 409(Conflict)로 매핑한다. body 형식 위반(필수 키 누락 등)도 본 예외.
    """


# =============================================================================
# Domain entity — frozen dataclass (codebase 일관)
# =============================================================================

@dataclass(frozen=True, slots=True)
class CustomPack:
    """custom_packs row 의 in-memory representation.

    Attributes:
        id: CustomPack UUID.
        user_id: 소유자 — IDOR owner-check 기준.
        pack_slug: 'user/{...}' / 'community/{...}'. canonical(빌트인)은 저장 불가.
        version: semver "x.y.z".
        content_hash: JCS+SHA-256 봉인값 "sha256:<hex>". load 시 재검증.
        body: pack 전체 정의 JSON dict.
        factor_count: body['factors'] 길이(표시용 비정규화).
        created_at: 생성 시각 (UTC). **updated_at 없음** — append-only immutable.
        visibility: ADR-0028 D1 공유 메타. 'private'(default) | 'public'. body/
            content_hash 와 달리 **mutable**(공유 토글) — append-only 불변은
            body 에만 적용. 재현성(result_hash) 입력 아님.
        source_url: ADR-0032 D3 import provenance(출처 URL). **body 가 아닌 row
            메타**라 content_hash 와 무관(봉인 불변). editor 생성 pack 은 None.
    """

    id: UUID
    user_id: UUID
    pack_slug: str
    version: str
    content_hash: str
    body: dict[str, Any]
    factor_count: int
    created_at: datetime
    # ADR-0028 D1 — 공유 visibility 메타. default 'private'. dataclass tail 에
    # default 부여로 기존 호출(positional/keyword)과 후방호환.
    visibility: str = "private"
    # ADR-0032 D3 — import provenance(출처 URL). **body 가 아닌 row 메타**라
    # content_hash 불변(봉인 무관) — provenance 를 body 에 넣으면 ADR-0020 봉인 파손.
    # editor 생성 pack 은 None(출처 없음). tail default 로 기존 호출과 후방호환.
    source_url: str | None = None


# =============================================================================
# Body field 추출 — save 입력(봉인된 body) → 컬럼 분해
# =============================================================================

def extract_pack_fields(body: dict[str, Any]) -> tuple[str, str, str, int]:
    """봉인된 pack body → (pack_slug, version, content_hash, factor_count).

    route 가 이미 validate_custom_pack 통과 + content_hash 재봉인한 body 를
    전제하나, repository 도 방어적으로 필수 키를 확인한다(누락 시
    CustomPackDataError). factor_count 는 body['factors'] 길이.

    Raises:
        CustomPackDataError: pack_slug / version / content_hash / factors 누락
            또는 형식 위반.
    """
    if not isinstance(body, dict):
        raise CustomPackDataError("pack body 는 object 여야 합니다.")
    pack_slug = body.get("pack_slug")
    version = body.get("version")
    content_hash = body.get("content_hash")
    factors = body.get("factors")
    if not isinstance(pack_slug, str) or not pack_slug:
        raise CustomPackDataError("pack body 에 pack_slug 문자열이 필요합니다.")
    if not isinstance(version, str) or not version:
        raise CustomPackDataError("pack body 에 version 문자열이 필요합니다.")
    if not isinstance(content_hash, str) or not content_hash.startswith("sha256:"):
        raise CustomPackDataError(
            "pack body 에 봉인된 content_hash('sha256:...')가 필요합니다."
        )
    if not isinstance(factors, list):
        raise CustomPackDataError("pack body 에 factors 배열이 필요합니다.")
    return pack_slug, version, content_hash, len(factors)


def extract_public_meta(body: dict[str, Any]) -> tuple[str | None, str | None]:
    """봉인된 pack body → 공개 메타 (name, description).

    factor-pack-v1 schema 에는 top-level `name` 필드가 없다(additionalProperties
    False). 사람이 읽는 pack 이름은 `citation.title`, 설명은 top-level
    `description`(optional) 이 canonical 출처다. 본 helper 가 그 두 값을 추출해
    공유 게이트(USER_SHARED 검사 대상) / community 목록(공개 메타 노출) 양쪽에서
    **단일 규칙**으로 쓰인다.

    Returns:
        (name, description). 각각 문자열이 아니거나 부재 시 None.
    """
    if not isinstance(body, dict):
        return None, None
    name: str | None = None
    citation = body.get("citation")
    if isinstance(citation, dict):
        title = citation.get("title")
        if isinstance(title, str) and title:
            name = title
    description: str | None = None
    desc = body.get("description")
    if isinstance(desc, str) and desc:
        description = desc
    return name, description


# =============================================================================
# Custom Pack Repository Protocol
# =============================================================================

@runtime_checkable
class CustomPackRepository(Protocol):
    """Custom pack 영속화 contract — 모든 method 가 `user_id` keyword-only (IDOR).

    **append-only immutable** — UPDATE 메서드 없음. save / list / get / delete 만.
    """

    def save(
        self, *, user_id: UUID, body: dict[str, Any], source_url: str | None = None,
    ) -> CustomPack:
        """봉인된 pack body 를 저장(append-only). body 에서 컬럼 분해.

        같은 (user_id, pack_slug, version):
            - 같은 content_hash → idempotent(기존 CustomPack 반환 — 기존 source_url
              유지. provenance 는 최초 저장본 기준).
            - 다른 content_hash → CustomPackDataError(충돌, route 가 409).

        Args:
            source_url: ADR-0032 D3 import provenance(출처 URL). **body 가 아닌 row
                메타** — content_hash 에 영향 0. editor 생성(직접 작성)은 None.

        Raises:
            CustomPackDataError: body 형식 위반 또는 (slug,version) 충돌.
        """
        ...

    def list_for_user(self, *, user_id: UUID) -> tuple[CustomPack, ...]:
        """본인의 저장 pack 목록 (created_at desc, id 보조 정렬).

        **반드시 user_id 필터** — 누락 시 전 사용자 pack 누출(가장 위험).
        """
        ...

    def get(self, pack_id: UUID, *, user_id: UUID) -> CustomPack | None:
        """단일 pack(전체 body 포함). 미존재 또는 owner mismatch 시 None."""
        ...

    def delete(self, pack_id: UUID, *, user_id: UUID) -> bool:
        """pack 삭제. 성공 True. 미존재/owner mismatch 시 False."""
        ...

    def get_by_slug_version(
        self, pack_slug: str, version: str, *, user_id: UUID,
    ) -> CustomPack | None:
        """(user_id, pack_slug, version) 으로 단일 pack(전체 body). 미존재/owner
        mismatch 시 None.

        PackRegistry 의 custom resolve(reproduce 재로드 / custom screen)용 — ADR-0025
        D1. UNIQUE(user_id, pack_slug, version) 라 최대 1 건. user_id keyword-only
        (IDOR — 타 user pack 비가시).
        """
        ...

    def list_public(self) -> tuple[CustomPack, ...]:
        """visibility=='public' 인 전체 pack 목록 (created_at 역순) — ADR-0028 D3.

        **user 무관** — community 공개 목록은 인증 불요(ADR-0028 D3). 정렬은
        created_at 역순(사실)만 — 다운로드 수/인기/순위 등 큐레이션 신호 0. 본
        메서드만 user_id predicate 없이 전 사용자 pack 을 가로질러 조회하며,
        반드시 visibility=='public' 필터로 비공개 pack 누출을 차단한다.
        """
        ...

    def get_public(self, pack_slug: str, version: str) -> CustomPack | None:
        """(pack_slug, version) 의 **공개** pack 단건(전체 body) — ADR-0028 D4.

        community import 의 재료 — importer 가 공개 pack 의 전체 body 를 받아
        기존 import-check/import 흐름(stateless)에 투입한다. **visibility=='public'
        만** 반환(비공개 pack body 누출 차단). user 무관(공개). 같은 (slug,
        version) 으로 여러 user 가 공개했을 수 있으므로 created_at 역순 첫 건을
        반환(결정적 — 큐레이션 아님, 사실 정렬).
        """
        ...

    def set_visibility(
        self, pack_id: UUID, *, user_id: UUID, visibility: str,
    ) -> bool:
        """본인 pack 의 visibility 토글 — ADR-0028 D1/D2. 성공 True.

        body/content_hash 는 **불변**(ADR-0020) — visibility 컬럼만 UPDATE 한다.
        owner-check — 미존재 또는 owner mismatch 시 False(route 가 404, IDOR
        — 타 user pack 의 공유 상태 변경 차단). visibility 값 도메인 검증(두 값만
        허용)은 route 가 선행하나 repository 도 방어적으로 거부한다.

        Raises:
            CustomPackDataError: visibility 가 'private'/'public' 외 값.
        """
        ...


# =============================================================================
# Fake — contract reference (in-memory)
# =============================================================================

class FakeCustomPackRepository(CustomPackRepository):
    """In-memory custom pack store — contract reference (append-only immutable)."""

    def __init__(self) -> None:
        self._packs: dict[UUID, CustomPack] = {}

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def save(
        self, *, user_id: UUID, body: dict[str, Any], source_url: str | None = None,
    ) -> CustomPack:
        pack_slug, version, content_hash, factor_count = extract_pack_fields(body)
        # idempotent / 충돌 판정 — 같은 (user_id, slug, version) 기존 row 탐색.
        for existing in self._packs.values():
            if (
                existing.user_id == user_id
                and existing.pack_slug == pack_slug
                and existing.version == version
            ):
                if existing.content_hash == content_hash:
                    # 같은 정의 재저장 — idempotent(기존 반환. source_url 도 최초
                    # 저장본 유지 — provenance 는 봉인된 정의에 한 번만 귀속).
                    return existing
                # 같은 버전 다른 정의 — append-only 위반(충돌).
                raise CustomPackDataError(
                    f"pack '{pack_slug}' v{version} 는 이미 다른 정의로 저장됨 — "
                    f"immutable(append-only) 위반. 버전을 올리세요."
                )
        record = CustomPack(
            id=uuid4(),
            user_id=user_id,
            pack_slug=pack_slug,
            version=version,
            content_hash=content_hash,
            body=body,
            factor_count=factor_count,
            created_at=self._now(),
            # 신규 저장은 항상 private — 공유는 명시 토글(set_visibility)로만.
            visibility=VISIBILITY_PRIVATE,
            # ADR-0032 D3 — import 출처. body 가 아닌 row 메타라 content_hash 무관.
            source_url=source_url,
        )
        self._packs[record.id] = record
        return record

    def list_for_user(self, *, user_id: UUID) -> tuple[CustomPack, ...]:
        owned = [p for p in self._packs.values() if p.user_id == user_id]
        # created_at desc, id 보조 정렬(결정성).
        return tuple(
            sorted(owned, key=lambda p: (p.created_at, p.id), reverse=True)
        )

    def get(self, pack_id: UUID, *, user_id: UUID) -> CustomPack | None:
        p = self._packs.get(pack_id)
        if p is None or p.user_id != user_id:
            return None
        return p

    def delete(self, pack_id: UUID, *, user_id: UUID) -> bool:
        p = self.get(pack_id, user_id=user_id)
        if p is None:
            return False
        self._packs.pop(pack_id, None)
        return True

    def get_by_slug_version(
        self, pack_slug: str, version: str, *, user_id: UUID,
    ) -> CustomPack | None:
        # user_id AND pack_slug AND version — UNIQUE 라 최대 1 건. user_id 누락
        # 시 타 user pack 노출(IDOR) 이므로 세 조건 모두 필수.
        for p in self._packs.values():
            if (
                p.user_id == user_id
                and p.pack_slug == pack_slug
                and p.version == version
            ):
                return p
        return None

    def list_public(self) -> tuple[CustomPack, ...]:
        # visibility=='public' 만 — user 무관(공개 목록). 비공개 누출 차단의
        # 단일 predicate. created_at desc, id 보조 정렬(결정성, 큐레이션 0).
        public = [
            p for p in self._packs.values() if p.visibility == VISIBILITY_PUBLIC
        ]
        return tuple(
            sorted(public, key=lambda p: (p.created_at, p.id), reverse=True)
        )

    def get_public(self, pack_slug: str, version: str) -> CustomPack | None:
        # visibility=='public' AND (slug, version) — 비공개 누출 차단. 동일
        # (slug, version) 다중 공개 시 created_at 역순 첫 건(결정적, 사실 정렬).
        matches = [
            p for p in self._packs.values()
            if p.visibility == VISIBILITY_PUBLIC
            and p.pack_slug == pack_slug
            and p.version == version
        ]
        if not matches:
            return None
        return max(matches, key=lambda p: (p.created_at, p.id))

    def set_visibility(
        self, pack_id: UUID, *, user_id: UUID, visibility: str,
    ) -> bool:
        if visibility not in VALID_VISIBILITIES:
            raise CustomPackDataError(
                f"visibility 는 'private'/'public' 만 허용 — {visibility!r}"
            )
        p = self._packs.get(pack_id)
        # owner-check — 미존재/owner mismatch 시 False(타 user pack 변경 차단).
        if p is None or p.user_id != user_id:
            return False
        # body/content_hash 불변(ADR-0020) — visibility 만 교체(frozen 이라 신규
        # 인스턴스로 대체하되 body/hash 동일 객체 유지).
        self._packs[pack_id] = replace(p, visibility=visibility)
        return True
