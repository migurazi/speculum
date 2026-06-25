"""Custom pack Screen 실행 + Save Run endpoint — ADR-0025 D4/D5/D6 (PackRegistry Phase 2c).

저장된 custom pack(ADR-0022 D9)으로 screen 을 실행/저장한다. ADR-0021 D4 "익명
screen 에 CurrentUserDep 혼입 금지" 함정을 회피하기 위해 **기존 익명 `/api/screen`
와 별도 endpoint**(CurrentUserDep + pack 식별자) — 기존 익명 screen 라우트 무변경
(회귀 0).

Endpoints:
    POST /api/screen/custom   — custom pack 으로 screen 실행 (저장 X, 결과만).
    POST /api/runs/custom     — custom pack screen 결과를 Save Run (freeze).

설계 원칙 (ADR-0025):
- **user 격리 (D4)**: `registry.resolve(slug, version, user_id=user.user_id,
  custom_pack_repo=repo)` → 타 user/미존재 pack 은 None → 404. user_id 는
  CurrentUserDep 만(body 미수용 — IDOR).
- **빌트인 사칭 차단**: pack_slug=speculum-builtin 은 400(빌트인은 기존 익명
  /api/screen 사용 — 본 endpoint 는 custom 전용).
- **재현 freeze (D5)**: save 경로는 `collect_run_data_versions(as_of, session,
  pack=custom_pack)` 로 custom pack 의 (content_hash/slug/version)을 freeze →
  result_hash 에 custom pack hash 반영(빌트인 run 과 다른 hash). reproduce 는
  export body 의 pack 으로 재구성(runs.export_run 이 body 동봉).
- **No Advice (D6)**: custom screen 은 conditions boolean 필터(점수 노출 0). custom
  composite universe-relative factor 의 소표본 디스클로저는 distribution 경로가
  자동 적용(본 endpoint 는 screen boolean 필터만).

기존 빌트인 screen/run/reproduce byte 불변 — 본 endpoint 는 custom 경로만 `pack=
custom_pack` 명시 주입. snapshot_versions 무인자 호출(빌트인)은 byte 불변(ADR-0025
D2/D3).
"""

from __future__ import annotations

from typing import Final
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import NormalizedAsOfDep
from app.api.dependencies.auth import CurrentUserDep
from app.api.dependencies.repositories import (
    CorporateActionRepoDep,
    CustomPackRepoDep,
    DividendRepoDep,
    FactorEvaluatorDep,
    FinancialRepoDep,
    MacroIndicatorRepoDep,
    MarketCapRepoDep,
    PriceRepoDep,
    RunsRepoDep,
    SessionOrNoneDep,
    StocksRepoDep,
    TreasurySharesRepoDep,
)
from app.api.routes.screen import _canonical_security_types, screen_active_codes
from app.schemas.screen import (
    CustomScreenRunQueryIn,
    ScreenResultOut,
    ScreenRunSnapshotOut,
)
from app.services.factor_pack import LoadedPack
from app.services.pack_registry import BUILTIN_PACK_SLUG, PackRegistry
from app.services.screen_run import ScreenRunBuilder
from app.services.snapshot_versions import collect_run_data_versions

# screen 실행은 prefix 없이 /api/screen/custom, save 는 /api/runs/custom — 두 prefix
# 를 한 모듈에서 다루기 위해 라우터를 분리(각 prefix). main 이 둘 다 등록.
screen_router = APIRouter(prefix="/api/screen", tags=["screen"])
runs_router = APIRouter(prefix="/api/runs", tags=["runs"])

# 프로세스 수명 PackRegistry — 빌트인/reference 캐시 공유(immutable, ADR-0002 D4).
# custom 은 request-scoped(user_id+repo)라 캐시 미사용 — user 격리/DB 일관.
_PACK_REGISTRY: Final[PackRegistry] = PackRegistry()


def _resolve_custom_pack(
    *,
    pack_slug: str,
    version: str,
    user: CurrentUserDep,
    custom_pack_repo: CustomPackRepoDep,
) -> LoadedPack:
    """custom pack 식별자 → LoadedPack — user 격리 조회 (ADR-0025 D4).

    빌트인 사칭(speculum-builtin) → 400(custom endpoint 전용). 그 외 slug 는
    `registry.resolve(slug, version, user_id, custom_pack_repo)` 로 조회. 미존재/
    타 user pack → None → 404(IDOR — mismatch/미존재 구별 안 함). DB body 가
    손상됐으면 resolve 내부 validate_hash 가 HashMismatch(500이 아닌 — 변조 탐지는
    저장 시 봉인으로 정상 경로엔 미발생).

    Raises:
        HTTPException 400: 빌트인 slug(custom endpoint 비대상).
        HTTPException 404: 미존재/타 user custom pack.
    """
    if pack_slug == BUILTIN_PACK_SLUG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "빌트인 pack(speculum-builtin)은 custom screen 대상이 아닙니다. "
                "기본 /api/screen 을 사용하세요."
            ),
        )
    pack = _PACK_REGISTRY.resolve(
        pack_slug, version,
        user_id=user.user_id,
        custom_pack_repo=custom_pack_repo,
    )
    if pack is None:
        # 미존재 또는 타 user 소유 — owner mismatch/미존재 구별 안 함(IDOR).
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="저장된 custom pack 을 찾을 수 없습니다.",
        )
    return pack


@screen_router.post("/custom", response_model=ScreenResultOut)
async def execute_custom_screen(
    as_of: NormalizedAsOfDep,
    user: CurrentUserDep,
    stocks_repo: StocksRepoDep,
    custom_pack_repo: CustomPackRepoDep,
    session: SessionOrNoneDep,
    evaluator: FactorEvaluatorDep,
    price_repo: PriceRepoDep,
    financial_repo: FinancialRepoDep,
    corporate_action_repo: CorporateActionRepoDep,
    market_cap_repo: MarketCapRepoDep,
    treasury_repo: TreasurySharesRepoDep,
    macro_repo: MacroIndicatorRepoDep,
    dividend_repo: DividendRepoDep,
    body: CustomScreenRunQueryIn,
) -> ScreenResultOut:
    """Custom pack 으로 Screener 실행 — Save 안 함, 결과만 (ADR-0025 D4).

    user 의 저장 custom pack(pack_slug/version)을 조회(타 user 404)하여 그 pack 으로
    conditions 를 AND 필터링한 결정적 종목코드를 반환. `data_versions` 는 custom
    pack 으로 freeze(custom pack content_hash 반영) — 클라이언트가 그대로
    /api/runs/custom 저장 POST 에 전달(정책 race 차단).

    기존 익명 /api/screen 무변경 — 본 endpoint 만 CurrentUserDep + custom pack.
    """
    pack = _resolve_custom_pack(
        pack_slug=body.pack_slug, version=body.version,
        user=user, custom_pack_repo=custom_pack_repo,
    )
    selected_security_types = _canonical_security_types(body.security_types)

    # custom pack 으로 조건 매칭 — screen_active_codes 의 pack 인자만 custom 명시.
    # ScreenCodesResult 반환 — .result_codes 로 unwrap (custom screen 도 result_codes 만 필요).
    result_codes = screen_active_codes(
        as_of=as_of.value,
        conditions=body.conditions,
        stocks_repo=stocks_repo,
        pack=pack,
        evaluator=evaluator,
        price_repo=price_repo,
        financial_repo=financial_repo,
        corporate_action_repo=corporate_action_repo,
        market_cap_repo=market_cap_repo,
        treasury_repo=treasury_repo,
        macro_repo=macro_repo,
        # M7 #4 — dividend / total return field 실연결(§2.10 대칭, save 와 동일).
        dividend_repo=dividend_repo,
        security_types=selected_security_types,
    ).result_codes

    # data_versions freeze — custom pack 명시 주입 → factor_pack_* 3 키가 custom
    # pack 의 hash/slug/version 으로 채워짐(빌트인 run 과 다른 hash). 빌트인 무인자
    # 경로는 byte 불변(ADR-0025 D2/D3).
    data_versions = dict(collect_run_data_versions(as_of.value, session, pack=pack))

    return ScreenResultOut(
        result_codes=result_codes,
        total=len(result_codes),
        data_versions=data_versions,
    )


@runs_router.post(
    "/custom",
    response_model=ScreenRunSnapshotOut,
    status_code=status.HTTP_201_CREATED,
)
async def save_custom_run(
    as_of: NormalizedAsOfDep,
    user: CurrentUserDep,
    stocks_repo: StocksRepoDep,
    custom_pack_repo: CustomPackRepoDep,
    runs_repo: RunsRepoDep,
    session: SessionOrNoneDep,
    evaluator: FactorEvaluatorDep,
    price_repo: PriceRepoDep,
    financial_repo: FinancialRepoDep,
    corporate_action_repo: CorporateActionRepoDep,
    market_cap_repo: MarketCapRepoDep,
    treasury_repo: TreasurySharesRepoDep,
    macro_repo: MacroIndicatorRepoDep,
    dividend_repo: DividendRepoDep,
    body: CustomScreenRunQueryIn,
) -> ScreenRunSnapshotOut:
    """Custom pack Save Run — custom pack 으로 재실행 + freeze 저장 (ADR-0025 D5).

    custom pack 의 (content_hash/slug/version)을 `collect_run_data_versions(...,
    pack=custom_pack)` 로 freeze — result_hash 에 custom pack hash 반영(빌트인 run
    과 다른 hash). 저장된 run 은 export(pack body 동봉) → reproduce 로 user/DB 무관
    byte-동일 재현(ADR-0021 D5).

    저장 result_codes 는 /api/screen/custom 과 **동일 조건 매칭 경로**
    (screen_active_codes) — 저장 run = 사용자가 본 custom screen 결과(§2.10).
    """
    pack = _resolve_custom_pack(
        pack_slug=body.pack_slug, version=body.version,
        user=user, custom_pack_repo=custom_pack_repo,
    )
    selected_security_types = _canonical_security_types(body.security_types)

    # ScreenCodesResult 반환 — .result_codes 로 unwrap (custom Save Run 도 result_codes 만 필요).
    result_codes = screen_active_codes(
        as_of=as_of.value,
        conditions=body.conditions,
        stocks_repo=stocks_repo,
        pack=pack,
        evaluator=evaluator,
        price_repo=price_repo,
        financial_repo=financial_repo,
        corporate_action_repo=corporate_action_repo,
        market_cap_repo=market_cap_repo,
        treasury_repo=treasury_repo,
        # M2 T81 — macro field 실연결(§2.10 대칭, custom screen 과 동일). 저장
        # custom run 이 사용자가 본 custom screen 과 macro 조건에서 일치하도록.
        macro_repo=macro_repo,
        # M7 #4 — dividend / total return field 실연결(§2.10 대칭, screen 과 동일).
        dividend_repo=dividend_repo,
        security_types=selected_security_types,
    ).result_codes

    # custom pack 명시 주입 freeze — factor_pack_* 3 키가 custom pack hash/slug/
    # version. 빌트인 save_run 경로(runs.py)는 무변경(pack=DEFAULT_PACK 무인자).
    data_versions = collect_run_data_versions(as_of.value, session, pack=pack)

    snapshot = ScreenRunBuilder.build(
        run_id=uuid4(),
        user_id=user.user_id,
        conditions=[
            {"factor": c.factor, "op": c.op.value, "value": c.value}
            for c in body.conditions
        ],
        selected_factors=body.selected_factors,
        security_types=selected_security_types,
        as_of=as_of.value,
        result_codes=result_codes,
        data_versions=data_versions,
    )
    runs_repo.save(snapshot)
    return ScreenRunSnapshotOut.from_domain(snapshot)
