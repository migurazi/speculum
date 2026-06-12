"""Factor Pack 해소 레지스트리 — slug/version → LoadedPack 단일 진입점 (ADR-0025).

PackRegistry Phase 2a (재현성 기반 단계, ADR-0025 D1/D2/D3/D5):

저장된 Screen Run 을 재현할 때, 그 시점 frozen `data_versions` 의
`factor_pack_slug` / `factor_pack_version` / `factor_pack_content_hash` 로만 pack
을 재로드해야 한다 (ADR-0025 D5). 운영 시점의 활성 pack(`DEFAULT_PACK`)을 무조건
사용하면 정책이 바뀐 뒤 재현 결과가 frozen 과 달라져 §2.10 Reproducibility 가
깨진다. 본 레지스트리가 그 "frozen slug/version → pack" 해소를 담당.

해소 대상 (Phase 2a 범위):
1. 빌트인(`slug == "speculum-builtin"`) — `load_builtin_pack(version)`.
2. reference(`load_reference_packs` 의 slug 매칭) — speculum 제공 참조 정의.
3. custom slug — **Phase 2b deferred**. user_id + custom_pack_repo 인자는 받되
   이번 cycle 은 None 반환(reproduce 가 None → matches=False 로 보수 처리).

설계 원칙:
- **단방향 의존**: 본 모듈은 `snapshot_versions` 를 import 하지 않는다(snapshot →
  factor_pack 단방향 토폴로지 보존). pack 파라미터만 다룬다.
- **content_hash 검증 재사용**: 빌트인/reference 모두 `load_pack` /
  `load_builtin_pack` 가 `validate_hash` 를 수행하므로, 변조된 pack 은 로드 단계
  에서 fail-loud. 본 레지스트리는 추가 hash 검증을 하지 않고 frozen hash 와의
  일치 판정은 호출자(reproduce_run)에 맡긴다.
- **프로세스 수명 캐시**: (slug, version) 키로 LoadedPack 캐시. 빌트인/reference
  pack 은 immutable(ADR-0002 D4) 이라 프로세스 내 재로드 불필요.

관련 ADR / 문서:
- ADR-0025 (2026-06-02) D1/D2/D3/D5 — PackRegistry Phase 2a
- ADR-0002 D4 — immutable pack (캐시 안전성 근거)
- factor_pack.py:640-712 (load_builtin_pack / load_reference_packs)
- reproduce.py — frozen data_versions 기반 재로드 호출자
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.services.factor_pack import (
    LoadedPack,
    load_builtin_pack,
    load_reference_packs,
    validate_hash,
)

if TYPE_CHECKING:
    from app.repositories.custom_pack_repository import CustomPackRepository

__all__ = [
    "BUILTIN_PACK_SLUG",
    "PackRegistry",
    "load_pack_from_body",
]

# 빌트인 pack 의 slug — speculum-builtin-v{version}.json 의 pack_slug 값과 일치.
# data_versions["factor_pack_slug"] 가 본 값이면 load_builtin_pack 경로.
BUILTIN_PACK_SLUG: Final[str] = "speculum-builtin"


def load_pack_from_body(body: dict[str, Any]) -> LoadedPack:
    """봉인된 custom pack body → LoadedPack — user 무관 자기완결 로드 (ADR-0025 D5).

    reproduce 의 custom run 재현 경로용 (ADR-0021 D5 — export JSON 자기완결).
    export JSON 에 포함된 custom pack body 만으로 그 시점 pack 을 재구성하므로 DB /
    user_id 가 필요 없다 — 삭제된 custom pack 도 body 자체로 재현 가능.

    `validate_hash` 가 body['content_hash'] 와 재계산 hash 의 일치를 강제 — 변조된
    export body 는 `HashMismatch` 로 fail-loud (재현 호출자가 matches=False 로 처리).
    `validate_hash` 가 반환한 재계산 hash 로 LoadedPack.computed_hash 를 채워, 호출자
    가 frozen data_versions["factor_pack_content_hash"] 와 대조할 수 있게 한다.

    Args:
        body: 봉인된 pack body (content_hash 필드 포함). custom screen save 시
            freeze 된 그 body 가 export JSON 에 자기완결로 실려 들어온다.

    Returns:
        LoadedPack — computed_hash = content_hash, factor/slug/version 채움.

    Raises:
        HashMismatch: body['content_hash'] 가 재계산 hash 와 불일치 (변조) 또는
            placeholder hash. KeyError: factors/pack_slug/version 키 부재 (형식
            위반 — 정상 export 면 발생 안 함).
    """
    computed = validate_hash(body)
    return LoadedPack(
        body=body,
        computed_hash=computed,
        factor_count=len(body["factors"]),
        pack_slug=body["pack_slug"],
        version=body["version"],
    )


class PackRegistry:
    """slug/version → LoadedPack 해소 — 빌트인/reference (Phase 2a), custom deferred.

    프로세스 수명 캐시를 인스턴스 단위로 보유(`dict[(slug, version)]`). 빌트인/
    reference pack 은 immutable 이라 (slug, version) 가 같으면 같은 LoadedPack.

    reference slug index 는 첫 reference 조회 시 lazy 로 1 회 구축
    (`load_reference_packs` 의 디스크 I/O 를 최초 1 회로 한정).
    """

    def __init__(self) -> None:
        # (slug, version) → LoadedPack 캐시 (빌트인 + reference 통합).
        self._cache: dict[tuple[str, str], LoadedPack] = {}
        # reference pack 의 (slug, version) → LoadedPack lazy index. None 이면
        # 아직 미구축 — 첫 reference 조회 시 load_reference_packs 로 1 회 채움.
        self._reference_index: dict[tuple[str, str], LoadedPack] | None = None

    def resolve(
        self,
        slug: str,
        version: str,
        *,
        user_id: object | None = None,
        custom_pack_repo: CustomPackRepository | None = None,
    ) -> LoadedPack | None:
        """frozen slug/version 으로 pack 을 재로드 — 미지원/미존재 시 None.

        Args:
            slug: data_versions["factor_pack_slug"] (예: "speculum-builtin").
            version: data_versions["factor_pack_version"] (예: "1.0.0").
            user_id: custom pack 소유자 식별 — **Phase 2b custom resolve** 용
                인자(이번 cycle 미사용). 시그니처 안정성을 위해 선반영.
            custom_pack_repo: custom pack 조회 repository — **Phase 2b custom
                resolve** 용(이번 cycle 미사용).

        Returns:
            LoadedPack — 빌트인/reference 해소 성공.
            None — custom slug(Phase 2b deferred) 또는 미존재 reference. reproduce
                가 None → matches=False("frozen pack 재로드 불가") 로 보수 처리.
                NotImplementedError 를 던지지 않음(재현 endpoint 가 500 이 아닌
                "재현 불가" 정보를 반환해야 함).

        Note:
            content_hash 검증은 load_builtin_pack / load_pack 의 validate_hash 가
            수행. frozen hash 와 재로드 hash 의 일치 판정은 호출자(reproduce_run)가
            담당 — 본 메서드는 "그 slug/version 의 정본 pack" 만 반환.
        """
        cache_key = (slug, version)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if slug == BUILTIN_PACK_SLUG:
            # 빌트인 — load_builtin_pack 가 validate_hash + Composite 게이트 수행.
            pack = load_builtin_pack(version)
            self._cache[cache_key] = pack
            return pack

        reference = self._resolve_reference(slug, version)
        if reference is not None:
            self._cache[cache_key] = reference
            return reference

        # custom slug — user_id + custom_pack_repo 로 사용자 저장 pack 조회(ADR-0025
        # D1, Phase 2b). request-scoped — user 격리·DB 일관성 위해 _cache 미사용.
        #
        # ADR-0034 D2 (identity v2 dual-load): v2 namespace pack(`@{publisher}/{slug}`)
        # 도 빌트인(BUILTIN_PACK_SLUG)·reference 가 아니므로 **본 custom 경로로
        # 자동 해소**된다(별도 v2 분기 불필요 — slug 형식만으로 v1/v2 구별,
        # data_versions 신규 키 0). v1 custom(`user/...`)·v2(`@.../...`) 모두
        # get_by_slug_version 으로 같은 경로. content_hash 무결성은 load_pack_from_body
        # 의 validate_hash 가 강제(schema 무관 — D6: 재로드는 hash-only).
        if user_id is not None and custom_pack_repo is not None:
            return self._resolve_custom(slug, version, user_id, custom_pack_repo)
        # custom slug 인데 user_id/repo 미제공(예: reproduce 의 익명 재현 경로) → None.
        # reproduce 가 matches=False("frozen pack 재로드 불가")로 보수 처리.
        return None

    def resolve_from_body(self, body: dict[str, Any]) -> LoadedPack:
        """export JSON 의 custom pack body → LoadedPack — user 무관 (ADR-0025 D5).

        reproduce 의 custom run 재현 경로 진입점. `load_pack_from_body` 의 thin
        wrapper — reproduce 가 PackRegistry 인스턴스만 들고 다니므로 메서드로도
        노출(빌트인/reference 의 `resolve` 와 대칭). DB / user_id 무관 — export
        body 자체로 pack 재구성하므로 삭제된 custom pack 도 재현 가능. 변조 body 는
        `HashMismatch` 로 fail-loud.

        custom pack body 는 immutable 이 아니라 (request 마다 다른 export body)
        프로세스 캐시(`_cache`)에 넣지 않는다 — 빌트인/reference 경로만 캐시.
        """
        return load_pack_from_body(body)

    def _resolve_custom(
        self,
        slug: str,
        version: str,
        user_id: object,
        custom_pack_repo: CustomPackRepository,
    ) -> LoadedPack | None:
        """저장 custom pack (user_id, slug, version) → LoadedPack — ADR-0025 D1.

        `get_by_slug_version` owner-check(IDOR — 타 user None). body `validate_hash`
        재검증으로 content_hash 봉인 무결성 확인(ADR-0022 D9.2 변조 탐지 — DB body 가
        손상됐으면 HashMismatch). custom 은 request-scoped 라 `_cache` 에 넣지 않는다
        (user 격리 + DB 일관성 — 같은 (slug,version)이라도 user 마다 다른 pack).
        """
        record = custom_pack_repo.get_by_slug_version(slug, version, user_id=user_id)
        if record is None:
            return None
        # content_hash 봉인 무결성 + LoadedPack 구성 — load_pack_from_body 재사용
        # (validate_hash → HashMismatch 변조 탐지, computed_hash 채움).
        return load_pack_from_body(record.body)

    def _resolve_reference(self, slug: str, version: str) -> LoadedPack | None:
        """reference pack 디렉토리에서 (slug, version) 매칭 — lazy index."""
        if self._reference_index is None:
            self._reference_index = {
                (pack.pack_slug, pack.version): pack
                for pack in load_reference_packs()
            }
        return self._reference_index.get((slug, version))
