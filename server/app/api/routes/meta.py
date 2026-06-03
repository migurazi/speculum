"""Meta endpoint — `/api/as_of` (정규화 결과) + `/api/policy-versions` (정책 freeze).

본 사이클 (T24) 의 deliverable. Frontend picker / 디버깅 / Save Run freeze
검증의 source.

설계 원칙 (oracle 자문 결정 6):
- 두 endpoint 분리 — 책임·호출 빈도·cache 주기 다름.
- `/api/as_of` = picker click 마다 호출 (저 빈도).
- `/api/policy-versions` = 디버깅 / Save Run 시 freeze 검증 (매우 저 빈도).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import NormalizedAsOfDep
from app.api.dependencies.repositories import ActivePackDep
from app.services.snapshot_versions import collect_active_policy_versions

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/as_of")
async def get_as_of(as_of: NormalizedAsOfDep) -> dict:
    """현재 입력의 정규화 결과 반환. picker UI 가 snap 결과 미리 보기 + Response
    header (`X-AsOf-*`) 와 함께 사용.

    Response body:
        value: 정규화된 영업일 (ISO 8601).
        was_defaulted: 입력 None → kst_today 채움.
        was_snapped: 휴장일 입력 → 직전 영업일 snap.
        original_input: snap 전 입력 (was_snapped=True 시).
        pit_policy_version: 본 결과를 만든 정책 버전.

    Response headers (`as_of` dependency 가 자동 채움):
        X-AsOf, X-AsOf-Defaulted, X-AsOf-Snapped, X-AsOf-Original.
    """
    return {
        "value": as_of.value.isoformat(),
        "was_defaulted": as_of.was_defaulted,
        "was_snapped": as_of.was_snapped,
        "original_input": (
            as_of.original_input.isoformat()
            if as_of.original_input is not None else None
        ),
        "pit_policy_version": as_of.pit_policy_version,
    }


@router.get("/policy-versions")
async def get_policy_versions() -> dict:
    """현재 활성 정책의 hash + version 집계 — Screen Run snapshot freeze 입력의 source.

    8 기둥 §2.10 Reproducibility 의 인스턴스. 6 개월 후 같은 Run 재현 시 본
    endpoint 결과와 snapshot 의 `data_versions` 비교로 변경된 정책 식별.

    Response 는 `snapshot_versions.collect_active_policy_versions()` 의 결과
    (11 키 Mapping[str, str]).
    """
    return dict(collect_active_policy_versions())


@router.get("/factors")
async def get_factors(pack: ActivePackDep) -> dict:
    """활성 factor pack 의 factor 목록 — Screener 의 factor 선택 UI source.

    frontend 가 canonical_id 를 직접 타이핑하지 않고 드롭다운으로 고를 수 있도록
    `(canonical_id, name, unit, tags)` 를 노출. canonical_id 는 조건/표시 입력의
    실제 값, name 은 사람이 읽는 라벨, tags 는 카테고리 그룹핑용(valuation 등).

    Response body:
        pack_slug / pack_version: 활성 pack 식별 (UI 캐시 무효화 키).
        factors: `[{canonical_id, name, unit, tags}]` — pack 정의 순서.
    """
    factors = [
        {
            "canonical_id": f["canonical_id"],
            "name": f["name"],
            "unit": f["unit"],
            "tags": list(f.get("tags", ())),
        }
        for f in pack.body["factors"]
    ]
    return {
        "pack_slug": pack.pack_slug,
        "pack_version": pack.version,
        "factors": factors,
    }
