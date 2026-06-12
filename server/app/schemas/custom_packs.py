"""Custom Pack wire schemas — ADR-0022 D9 사용자 정의 pack 영속화.

설계 (notes / factor_packs 선례 일관):
- Pydantic v2 strict + extra=forbid + frozen.
- `from_domain` 단방향 factory.
- **save 입력은 pack(dict) 만** — user_id 미노출(route 의 CurrentUserDep 가
  결정, IDOR 차단). 클라이언트가 보낸 content_hash 는 route 가 무시·재봉인.
- 목록/메타 출력(CustomPackOut)은 body 제외(경량) — 전체 body 는 단건 불러오기
  (CustomPackBodyOut)에서만 반환.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.repositories.custom_pack_repository import (
    CustomPack,
    extract_public_meta,
)

# source_url(provenance) 상한 — ORM String(2048) 과 일치(초과 시 DB truncation 대신
# 422 거부). ADR-0032 D3.
_MAX_SOURCE_URL_LEN = 2048

__all__ = [
    "CommunityPackBodyOut",
    "CommunityPackListOut",
    "CommunityPackOut",
    "CustomPackBodyOut",
    "CustomPackListOut",
    "CustomPackOut",
    "CustomPackSaveIn",
    "VisibilityUpdateIn",
]

# strict — 입력 타입 엄격. extra=forbid + frozen 유지(factor_packs 선례).
_STRICT_MODEL_CONFIG = ConfigDict(strict=True, extra="forbid", frozen=True)


class CustomPackSaveIn(BaseModel):
    """POST /api/factor-packs/saved body.

    pack(전체 factor pack JSON dict)만 받는다. user_id 미노출(CurrentUserDep).
    클라이언트 content_hash 는 route 가 무시·재봉인.
    """

    model_config = _STRICT_MODEL_CONFIG

    pack: dict[str, Any]
    # ADR-0032 D3 — import provenance(출처 URL). editor 직접 작성은 None(미전송).
    # **body 가 아닌 row 메타** — content_hash 봉인에 영향 0(route 가 pack 만 봉인).
    source_url: str | None = None

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, v: str | None) -> str | None:
        """provenance URL 위생 — https 만 + 길이 상한.

        표시·링크되는 값이라 javascript:/data: 등 위험 scheme 을 막는다(client
        클릭 시 XSS 차단). client-fetch import 가 https-only 인 것과 동일 기준.
        None(editor 작성)은 통과.
        """
        if v is None:
            return None
        if len(v) > _MAX_SOURCE_URL_LEN:
            raise ValueError(
                f"source_url 이 너무 깁니다(최대 {_MAX_SOURCE_URL_LEN}자)."
            )
        if not v.startswith("https://"):
            raise ValueError("source_url 은 https:// 스킴만 허용됩니다.")
        return v


class CustomPackOut(BaseModel):
    """Custom pack 메타 출력 — body 제외(경량 목록/저장 응답).

    visibility(ADR-0028 D1)는 본인 목록에서 공유 상태 표시용 — 'private'/'public'.
    """

    model_config = _STRICT_MODEL_CONFIG

    id: UUID
    pack_slug: str
    version: str
    content_hash: str
    factor_count: int
    created_at: datetime
    visibility: str
    # ADR-0032 D3 — import provenance(출처 URL). None=editor 작성(출처 없음).
    source_url: str | None = None

    @classmethod
    def from_domain(cls, pack: CustomPack) -> CustomPackOut:
        return cls(
            id=pack.id,
            pack_slug=pack.pack_slug,
            version=pack.version,
            content_hash=pack.content_hash,
            factor_count=pack.factor_count,
            created_at=pack.created_at,
            visibility=pack.visibility,
            source_url=pack.source_url,
        )


class CustomPackBodyOut(BaseModel):
    """단건 불러오기 출력 — 메타 + 전체 body(편집/재사용용)."""

    model_config = _STRICT_MODEL_CONFIG

    id: UUID
    pack_slug: str
    version: str
    content_hash: str
    factor_count: int
    created_at: datetime
    visibility: str
    pack: dict[str, Any]
    # ADR-0032 D3/D6 — import provenance(출처 URL). 단건 불러오기에서 license/
    # citation 과 함께 use-시점 표시(#6)용. None=editor 작성.
    source_url: str | None = None

    @classmethod
    def from_domain(cls, pack: CustomPack) -> CustomPackBodyOut:
        return cls(
            id=pack.id,
            pack_slug=pack.pack_slug,
            version=pack.version,
            content_hash=pack.content_hash,
            factor_count=pack.factor_count,
            created_at=pack.created_at,
            visibility=pack.visibility,
            pack=pack.body,
            source_url=pack.source_url,
        )


class CustomPackListOut(BaseModel):
    """GET /api/factor-packs/saved 결과 — 메타 목록."""

    model_config = _STRICT_MODEL_CONFIG

    items: tuple[CustomPackOut, ...]
    total: int


# =============================================================================
# ADR-0028 — 공유 visibility 게이트 + community 공개 목록
# =============================================================================


class VisibilityUpdateIn(BaseModel):
    """PATCH /api/factor-packs/saved/{id}/visibility body — ADR-0028 D1/D2.

    visibility 는 'private'|'public' 두 값만(Literal 로 schema 강제 — 그 외는
    Pydantic 이 422). user_id 미노출(route 의 CurrentUserDep 가 결정 — IDOR).
    """

    model_config = _STRICT_MODEL_CONFIG

    visibility: Literal["private", "public"]


class CommunityPackOut(BaseModel):
    """GET /api/factor-packs/community 의 단건 — 공개 메타 (ADR-0028 D3).

    공개 목록은 body 전체를 노출하지 않는다(import 는 별도 흐름). 공개 메타만:
    pack_slug/version/factor_count/created_at + 사람이 읽는 name/description(body
    에서 추출) + content_hash(import 검증용). **큐레이션 신호 0** — 다운로드 수/
    인기/순위/별점 필드는 schema 자체에 존재하지 않는다(ADR-0028 D3).
    """

    model_config = _STRICT_MODEL_CONFIG

    pack_slug: str
    version: str
    factor_count: int
    created_at: datetime
    content_hash: str
    name: str | None
    description: str | None

    @classmethod
    def from_domain(cls, pack: CustomPack) -> CommunityPackOut:
        # 공개 메타(name/description)는 body 의 citation.title / description 에서
        # 추출 — extract_public_meta 단일 규칙(공유 게이트 검사 대상과 동일 출처).
        name, description = extract_public_meta(pack.body)
        return cls(
            pack_slug=pack.pack_slug,
            version=pack.version,
            factor_count=pack.factor_count,
            created_at=pack.created_at,
            content_hash=pack.content_hash,
            name=name,
            description=description,
        )


class CommunityPackListOut(BaseModel):
    """GET /api/factor-packs/community 결과 — 공개 pack 메타 목록 (ADR-0028 D3).

    정렬은 created_at 역순(사실)만 — Top-N/추천/순위 없음. total 은 단순 개수
    (인기 집계 아님).
    """

    model_config = _STRICT_MODEL_CONFIG

    packs: tuple[CommunityPackOut, ...]
    total: int


class CommunityPackBodyOut(BaseModel):
    """공개 pack 단건(전체 body) — ADR-0028 D4 import 재료.

    importer 가 공개 pack 의 전체 body 를 받아 기존 import-check/import 흐름
    (stateless)에 투입한다. content_hash 봉인 body 그대로 노출 — 수입측이
    `validate_hash` 로 무결성 재검증(fail-loud). 공개 pack 만 노출(404 — 비공개/
    미존재 구별 안 함).
    """

    model_config = _STRICT_MODEL_CONFIG

    pack_slug: str
    version: str
    factor_count: int
    created_at: datetime
    content_hash: str
    name: str | None
    description: str | None
    pack: dict[str, Any]

    @classmethod
    def from_domain(cls, pack: CustomPack) -> CommunityPackBodyOut:
        name, description = extract_public_meta(pack.body)
        return cls(
            pack_slug=pack.pack_slug,
            version=pack.version,
            factor_count=pack.factor_count,
            created_at=pack.created_at,
            content_hash=pack.content_hash,
            name=name,
            description=description,
            pack=pack.body,
        )
