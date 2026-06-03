"""Custom Pack 영속화 endpoint — ADR-0022 D9 (Factor Lab 사용자 정의 pack 저장).

Factor Lab editor 가 만든 사용자 정의 pack JSON 을 user-scoped 로 저장/조회/삭제.
import-check / export(stateless) 와 달리 본 endpoint 들은 **DB 영속**이며 모두
인증(CurrentUserDep) user 격리 하에 동작한다.

Endpoints:
    POST   /api/factor-packs/saved        — pack 저장 (201). 저장 메타 반환.
    GET    /api/factor-packs/saved        — 본인 저장 pack 메타 목록.
    GET    /api/factor-packs/saved/{id}   — 단건 불러오기(전체 body) — owner 404.
    DELETE /api/factor-packs/saved/{id}   — 삭제 (204) — owner 404.

설계 (절대 원칙 — ADR-0022 D9):
- 모든 endpoint CurrentUserDep — user_id 는 body 에 미노출(IDOR 차단).
- **저장 전 검증·봉인 (export 패턴 복제, factor_packs.py:518-555)**:
    1. validate_custom_pack — issues 있으면 422.
    2. derive_tier — canonical(speculum-builtin)이면 422(빌트인 사칭 차단).
    3. compute_pack_hash — 클라이언트 content_hash 무시·재봉인(저장 권위 기준).
- **append-only immutable** — 같은 (user, slug, version) 다른 정의 → 409.
  같은 정의 → idempotent(기존 메타 반환, 201). UPDATE endpoint 없음.
- 단건 불러오기는 owner 의 것만(404 — mismatch/미존재 구별 안 함).

스코프 밖 (D9.4 Phase 2):
    저장된 pack 으로 screen 실행/재현 freeze 는 본 작업 범위 밖. screen.py /
    reproduce.py / snapshot_versions.py 무변경. 본 endpoint 는 저장·조회만.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies.auth import CurrentUserDep
from app.api.dependencies.repositories import CustomPackRepoDep
from app.repositories.custom_pack_repository import (
    VISIBILITY_PUBLIC,
    CustomPackDataError,
    extract_public_meta,
)
from app.schemas.custom_packs import (
    CustomPackBodyOut,
    CustomPackListOut,
    CustomPackOut,
    CustomPackSaveIn,
    VisibilityUpdateIn,
)
from app.services.factor_pack import compute_pack_hash, validate_custom_pack
from app.services.factor_pack_identity import derive_tier
from app.services.forbidden_words import (
    CheckScope,
    ForbiddenWordsAssertError,
    assert_clean,
)

router = APIRouter(prefix="/api/factor-packs/saved", tags=["custom-packs"])

# import/export 와 동일 factor 수 방어(factor_packs.py 선례 — 메모리/저장 폭주 차단).
_MAX_FACTORS = 512


def _validate_and_seal(pack_body: Any) -> dict[str, Any]:
    """저장 전 검증 + 봉인 — export 패턴 복제(factor_packs.py:531-554).

    1. dict + factor 수 방어 (형식 위반 400 / 상한 초과 422).
    2. validate_custom_pack — schema/identity/acyclic/citation/forbidden_vocab
       이슈 있으면 422.
    3. derive_tier — canonical(빌트인) 이면 422(사칭 차단).
    4. compute_pack_hash — 클라이언트 content_hash 무시·재봉인. 저장 권위 기준.

    Returns:
        봉인된 pack body(content_hash 재계산). repository.save 가 받을 형태.
    """
    if not isinstance(pack_body, dict):
        raise HTTPException(status_code=400, detail="`pack` (object) 이 필요합니다.")
    factors = pack_body.get("factors")
    if isinstance(factors, list) and len(factors) > _MAX_FACTORS:
        raise HTTPException(
            status_code=422,
            detail=f"factors 수 {len(factors)} 가 상한 {_MAX_FACTORS} 초과",
        )

    issues = validate_custom_pack(pack_body)
    if issues:
        first = issues[0]
        raise HTTPException(
            status_code=422,
            detail=(
                f"유효하지 않은 pack 은 저장 불가 — {first.stage}: {first.message}"
            ),
        )

    # canonical tier(speculum-builtin) 사칭 차단 — 빌트인은 repo 에 이미 존재하며
    # 저장 pack 으로 위장 불가(ADR-0022 D9 / D8.2 불가침).
    tier = derive_tier(pack_body["pack_slug"])
    if tier == "canonical":
        raise HTTPException(
            status_code=422,
            detail=(
                "빌트인 canonical pack(speculum-builtin)은 저장 대상이 아닙니다. "
                "user/ tier pack 만 저장합니다."
            ),
        )

    # content_hash 봉인 — 클라이언트가 보낸 값 무관하게 재계산해 정의를 잠근다.
    return {**pack_body, "content_hash": compute_pack_hash(pack_body)}


@router.post(
    "",
    response_model=CustomPackOut,
    status_code=status.HTTP_201_CREATED,
)
async def save_custom_pack(
    user: CurrentUserDep,
    repo: CustomPackRepoDep,
    body: CustomPackSaveIn,
) -> CustomPackOut:
    """사용자 정의 pack 저장 — 검증·봉인 후 append-only 영속(201).

    같은 (user, slug, version) 다른 정의 → 409. 같은 정의 → idempotent(기존 반환).
    user_id 는 CurrentUserDep 가 결정(body 미수용 — IDOR 차단).
    """
    sealed = _validate_and_seal(body.pack)
    try:
        saved = repo.save(user_id=user.user_id, body=sealed)
    except CustomPackDataError as exc:
        # 같은 (slug, version) 다른 정의 = append-only 위반 → 409(Conflict).
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CustomPackOut.from_domain(saved)


@router.get("", response_model=CustomPackListOut)
async def list_custom_packs(
    user: CurrentUserDep,
    repo: CustomPackRepoDep,
) -> CustomPackListOut:
    """본인 저장 pack 메타 목록 — user_id 필터(전 사용자 누출 차단)."""
    packs = repo.list_for_user(user_id=user.user_id)
    items = tuple(CustomPackOut.from_domain(p) for p in packs)
    return CustomPackListOut(items=items, total=len(items))


@router.get("/{pack_id}", response_model=CustomPackBodyOut)
async def get_custom_pack(
    pack_id: UUID,
    user: CurrentUserDep,
    repo: CustomPackRepoDep,
) -> CustomPackBodyOut:
    """단건 불러오기(전체 body 포함). owner mismatch/미존재 → 404.

    load 시 repository(converter)가 content_hash 를 재검증 — 저장 후 변조된 body
    는 무결성 위반으로 거부(converter 의 CustomPackDataError). 정상 경로는 404 만.
    """
    pack = repo.get(pack_id, user_id=user.user_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="저장된 pack 을 찾을 수 없습니다.")
    return CustomPackBodyOut.from_domain(pack)


# response_model=None — 204 No Content body 불가 (notes.py delete 선례).
@router.delete(
    "/{pack_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None,
)
async def delete_custom_pack(
    pack_id: UUID,
    user: CurrentUserDep,
    repo: CustomPackRepoDep,
) -> None:
    """저장 pack 삭제. owner mismatch/미존재 → 404."""
    ok = repo.delete(pack_id, user_id=user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="저장된 pack 을 찾을 수 없습니다.")


# =============================================================================
# PATCH /{pack_id}/visibility — 공유 게이트 (ADR-0028 D1/D2)
# =============================================================================


@router.patch(
    "/{pack_id}/visibility", response_model=CustomPackBodyOut,
)
async def set_pack_visibility(
    pack_id: UUID,
    user: CurrentUserDep,
    repo: CustomPackRepoDep,
    body: VisibilityUpdateIn,
) -> CustomPackBodyOut:
    """저장 pack 의 공유 visibility 토글 — ADR-0028 D1/D2.

    body: `{"visibility": "private"|"public"}`. body/content_hash 는 불변
    (ADR-0020) — visibility 메타만 UPDATE.

    **'public' 전환 게이트 (ADR-0028 D2 — USER_SHARED scope 활성화)**:
        공유 surface 진입 시점에만 pack 의 공개 메타(name=citation.title +
        description)를 `assert_clean(scope=USER_SHARED)` 검사한다. 금지어휘 포함
        시 공유 거부 → 422. private 전환/유지에는 검사 없음(USER_PRIVATE 정신 —
        본인만 보는 동안은 검사 skip). 공유가 추천 변질 통로가 되는 지점을 정확히
        차단(§2.2).

    IDOR: 타 user pack / 미존재 → 404(owner mismatch 와 미존재 구별 안 함 — user
    정보 누출 차단). repo.set_visibility 의 owner-check 가 단일 게이트.

    Note (어휘 echo 차단):
        assert_clean 의 ForbiddenWordsAssertError 를 **route 가 직접 catch** 한다.
        전역 handler 로 전파하면 500 이 되나 ADR-0028 D2 는 "공유 거부=422" 를
        요구하므로 여기서 422 로 변환하되 **detail 에 검출 어휘를 echo 하지 않는다**
        (generic message — ADR-0007 D4.4.2).
    """
    # 'public' 전환 시점에만 공유 게이트 — owner-check 전에 검사하면 타 user pack
    # 의 메타로 422/검사 비용이 새므로, 먼저 본인 pack 을 owner-check 로 확보한다.
    if body.visibility == VISIBILITY_PUBLIC:
        pack = repo.get(pack_id, user_id=user.user_id)
        if pack is None:
            # 미존재/owner mismatch — 404(IDOR, 게이트 검사 이전).
            raise HTTPException(
                status_code=404, detail="저장된 pack 을 찾을 수 없습니다.",
            )
        # 공개 메타(name=citation.title + description) USER_SHARED 검사. 본인
        # pack 의 body 에서 추출 — community 목록 노출 메타와 동일 출처.
        name, description = extract_public_meta(pack.body)
        try:
            if name is not None:
                assert_clean(
                    name, scope=CheckScope.USER_SHARED,
                    context="custom_pack.public.name",
                )
            if description is not None:
                assert_clean(
                    description, scope=CheckScope.USER_SHARED,
                    context="custom_pack.public.description",
                )
        except ForbiddenWordsAssertError as exc:
            # ADR-0028 D2 — 금지어휘 포함 pack 은 공유 거부(422). detail 은 generic
            # (검출 어휘 echo 0 — 어휘는 server-side log 로만, exc.matches 는 전역
            # handler 가 아닌 여기서 소비되므로 별도 로깅 없이 generic 응답).
            raise HTTPException(
                status_code=422,
                detail=(
                    "공유할 수 없는 pack 입니다 — 공개 메타에 허용되지 않는 표현이 "
                    "포함되어 있습니다."
                ),
            ) from exc

    ok = repo.set_visibility(
        pack_id, user_id=user.user_id, visibility=body.visibility,
    )
    if not ok:
        # 미존재/owner mismatch — 404. ('public' 경로는 위에서 이미 확보했으나
        # private 경로 및 동시성 race 도 본 분기로 일관 처리.)
        raise HTTPException(
            status_code=404, detail="저장된 pack 을 찾을 수 없습니다.",
        )
    # UPDATE 후 최신 상태(visibility 반영) 재조회 — 본인 pack 이므로 owner-check OK.
    updated = repo.get(pack_id, user_id=user.user_id)
    if updated is None:  # pragma: no cover — set 성공 직후엔 항상 존재.
        raise HTTPException(
            status_code=404, detail="저장된 pack 을 찾을 수 없습니다.",
        )
    return CustomPackBodyOut.from_domain(updated)
