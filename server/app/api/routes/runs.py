"""Screen Run snapshot endpoint — POST /api/runs (Save Run) + GET.

ADR-0008 D7 / D7-bis 의 Screen Run snapshot freeze implementation. T40 (Save Run
버튼) 의 backend. ADR-0002 D4 의 immutable + append-only 일관.

설계 (oracle T26 자문 P0):
- POST /api/runs — 재실행 + 저장 (M0 단순화)
- GET /api/runs — CurrentUserDep 의 runs only (IDOR 차단)
- GET /api/runs/{id} — fetch by id (user_id 검증)
- GET /api/runs/{id}/diff — VersionsDiffOut (현재 active 와 비교)
- GET /api/runs/{id}/export — self-identifying export JSON (M2 T80)
- POST /api/runs/reproduce — export JSON import → 재현 (M2 T80)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import NormalizedAsOfDep
from app.api.dependencies.auth import CurrentUserDep
from app.api.dependencies.repositories import (
    ActivePackDep,
    BatchRunRepoDep,
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
    ReproduceIn,
    ReproduceOut,
    RestatementLagOut,
    ScreenRunExportOut,
    ScreenRunListOut,
    ScreenRunQueryIn,
    ScreenRunSnapshotOut,
    VersionsDiffOut,
    _SnapshotIn,
)
from app.services.factor_pack import DEFAULT_PACK
from app.services.pack_registry import BUILTIN_PACK_SLUG, PackRegistry
from app.services.reproduce import reproduce_run
from app.services.restatement_lag import assess_restatement_lag
from app.services.screen_run import ScreenRunBuilder, ScreenRunSnapshot
from app.services.snapshot_versions import collect_run_data_versions

router = APIRouter(prefix="/api/runs", tags=["runs"])

_DEFAULT_LIST_LIMIT: Final[int] = 20
_MAX_LIST_LIMIT: Final[int] = 100

# 프로세스 수명 PackRegistry — frozen slug/version → pack 재로드 (ADR-0025 D5).
# 빌트인/reference pack 은 immutable 이라 프로세스 캐시 안전 (ADR-0002 D4).
_PACK_REGISTRY: Final[PackRegistry] = PackRegistry()

# data_versions 의 factor pack 식별 키 — snapshot_versions 가 freeze.
_PACK_SLUG_KEY: Final[str] = "factor_pack_slug"
_PACK_VERSION_KEY: Final[str] = "factor_pack_version"


@router.post("", response_model=ScreenRunSnapshotOut, status_code=status.HTTP_201_CREATED)
async def save_run(
    as_of: NormalizedAsOfDep,
    user: CurrentUserDep,
    stocks_repo: StocksRepoDep,
    runs_repo: RunsRepoDep,
    session: SessionOrNoneDep,
    evaluator: FactorEvaluatorDep,
    pack: ActivePackDep,
    price_repo: PriceRepoDep,
    financial_repo: FinancialRepoDep,
    corporate_action_repo: CorporateActionRepoDep,
    market_cap_repo: MarketCapRepoDep,
    treasury_repo: TreasurySharesRepoDep,
    macro_repo: MacroIndicatorRepoDep,
    dividend_repo: DividendRepoDep,
    body: ScreenRunQueryIn,
) -> ScreenRunSnapshotOut:
    """Save Run — 재실행 + 저장. T40 (Save Run 버튼) 의 backend.

    M0 단순화 (oracle 자문 결정 1 대안 B):
        실행과 저장이 같은 호출. 두 호출 분리 + token 패턴은 over-engineering.

    M1: result_codes 는 POST /api/screen 과 **동일한 조건 매칭 경로**
    (`screen_active_codes`) 로 산출 — 저장된 run 이 사용자가 본 스크린 결과와
    일치해야 재현성 (§2.10) 이 성립 (이전 stub 은 active universe 전체를 저장하여
    screen 결과와 불일치했음).
    """
    # 0. 자산군 사전 필터 (ADR-0023 D7) — screen 과 동일 canonical 규칙.
    selected_security_types = _canonical_security_types(body.security_types)

    # 1. 조건 매칭 — screen 과 단일 경로. 저장 result_codes = screen 결과.
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
        # M2 T81 — macro field 실연결(§2.10 대칭). execute_screen 이 macro_repo 를
        # 배선하므로 save_run 도 동일 배선해야 macro 조건의 저장 결과가 사용자가 본
        # 스크린과 일치(누락 시 macro field 가 silent N/A → result_codes 불일치).
        macro_repo=macro_repo,
        # M7 #4 — dividend / total return field 실연결(§2.10 대칭). live save 가
        # execute_screen 과 동일하게 dividend_repo 를 배선해야 reproduce 와 일치.
        dividend_repo=dividend_repo,
        security_types=selected_security_types,
    )

    # 2. data_versions freeze — M1 T48b (M7 #5 키 set 확장 반영). 정책 14 키 +
    #    (SQL session 있으면) as_of 시점 최신 성공 batch 의 batch_id 3 키
    #    (krx/dart/dividend) 명시 merge = 17 키. Fake-only mode (session=None) 는
    #    정책-only 14 키 (하위호환).
    data_versions = collect_run_data_versions(as_of.value, session)

    # 3. ScreenRunBuilder — canonical 정규화 + result_hash + data_versions freeze.
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

    # 3. 저장 — Fake/Sql 무관 (append-only).
    runs_repo.save(snapshot)

    return ScreenRunSnapshotOut.from_domain(snapshot)


@router.get("", response_model=ScreenRunListOut)
async def list_runs(
    user: CurrentUserDep,
    runs_repo: RunsRepoDep,
    limit: int = Query(_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
) -> ScreenRunListOut:
    """Current user 의 recent runs — IDOR 차단 (oracle 결정 5).

    User_id query param 없음 — CurrentUserDep 가 SYSTEM_USER_ID (M0) 또는
    NextAuth jwt sub (T31 후).
    """
    snapshots = runs_repo.fetch_recent(user_id=user.user_id, limit=limit)
    items = tuple(ScreenRunSnapshotOut.from_domain(s) for s in snapshots)
    return ScreenRunListOut(items=items, total=len(items))


@router.get("/{run_id}", response_model=ScreenRunSnapshotOut)
async def get_run(
    run_id: UUID,
    user: CurrentUserDep,
    runs_repo: RunsRepoDep,
) -> ScreenRunSnapshotOut:
    """단일 Run snapshot — user_id 검증 (IDOR 차단)."""
    snapshot = runs_repo.fetch_by_id(run_id, user_id=user.user_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run 을 찾을 수 없습니다.",
        )
    return ScreenRunSnapshotOut.from_domain(snapshot)


@router.get("/{run_id}/diff", response_model=VersionsDiffOut)
async def get_run_diff(
    run_id: UUID,
    user: CurrentUserDep,
    runs_repo: RunsRepoDep,
    session: SessionOrNoneDep,
) -> VersionsDiffOut:
    """Snapshot 의 data_versions 와 현재 active 비교 — UI freshness banner.

    재현성 (§2.10) 의 운영 의미 — 6 개월 후 같은 Run 재현 시 변경된 정책 키 노출.
    빈 diff = 일치 (재현 OK).

    M1 T48b (M7 #5 키 set 확장 반영) — 비교 기준 `current` 는 snapshot 과 동일한
    산출 경로 (`collect_run_data_versions(snapshot.as_of, session)`) 를 사용 →
    정책 14 키 + batch_id 3 키 (krx/dart/dividend) = 17 키. 같은 as_of 에서 새
    배치가 성공하면 batch_id 변경이 diff 에 노출되어 freshness banner 가 통지
    (새 run = 다른 연산, 재현 hash 와는 무관). Fake-only mode (session=None) 면
    정책-only 14 키 — 기존 14 키 run 과 일치하여 빈 diff (하위호환).

    oracle 자문 결정 4 — list endpoint 의 N+1 회피 위해 별도 endpoint.
    """
    snapshot = runs_repo.fetch_by_id(run_id, user_id=user.user_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run 을 찾을 수 없습니다.",
        )
    current = collect_run_data_versions(snapshot.as_of, session)
    diff = snapshot.diff_versions(current)
    return VersionsDiffOut(
        snapshot_id=snapshot.id,
        diff={k: (v[0], v[1]) for k, v in diff.items()},
    )


@router.get(
    "/{run_id}/export",
    response_model=ScreenRunExportOut,
    # custom run 만 pack body 를 싣고, 빌트인/reference run 은 pack=None 키를
    # 직렬화에서 제외 — **빌트인 run export 의 byte 불변** 보존 (ADR-0025 D5).
    response_model_exclude_none=True,
)
async def export_run(
    run_id: UUID,
    user: CurrentUserDep,
    runs_repo: RunsRepoDep,
    custom_pack_repo: CustomPackRepoDep,
) -> ScreenRunExportOut:
    """Self-identifying Screen Run export — M2 T80 Phase 1 + 2c custom 자기완결.

    저장된 Screen Run snapshot 을 자기완결 JSON 으로 감싸 반환. export JSON 에는
    재현에 필요한 모든 정보(conditions, as_of, result_codes, result_hash,
    data_versions)가 자기완결로 포함되어, 파일 하나만으로 재현 가능 (ROADMAP §4.1).

    export_format 마커 (`"speculum-screen-run-export-v1"`) 가 JSON 을 Speculum
    Screen Run export 로 자기 식별 — POST /api/runs/reproduce 가 이 마커를 검증.

    **custom run 자기완결 (ADR-0025 D5 / ADR-0021 D5)**: frozen factor_pack_slug 가
    빌트인(speculum-builtin)이 아니면 custom run — 그 시점 custom pack 정의를
    `pack` 필드에 동봉한다(custom_pack_repo 에서 frozen slug/version + user_id 로
    조회). 이로써 export JSON 만으로 user/DB 무관 재현이 가능(custom pack 삭제
    후에도 body 로 재구성). 빌트인/reference run 은 `pack=None` →
    response_model_exclude_none 으로 키 자체가 직렬화되지 않아 **빌트인 export byte
    불변**.

    user_id 는 export 에 포함(ScreenRunSnapshotOut 구조)되나 재현 경로에서 무시.
    export JSON = 공유 가능, user 비종속 (ADR-0021 D5).

    IDOR 차단 — user_id 검증 포함 (다른 user 의 run export 는 404).
    """
    snapshot = runs_repo.fetch_by_id(run_id, user_id=user.user_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run 을 찾을 수 없습니다.",
        )

    # custom run 이면 frozen slug/version 으로 custom pack body 를 동봉 (자기완결).
    pack_body = _resolve_custom_pack_body_for_export(
        snapshot.data_versions, user_id=user.user_id, custom_pack_repo=custom_pack_repo,
    )
    return ScreenRunExportOut(
        run=ScreenRunSnapshotOut.from_domain(snapshot),
        pack=pack_body,
    )


def _resolve_custom_pack_body_for_export(
    data_versions: Mapping[str, str],
    *,
    user_id: UUID,
    custom_pack_repo: CustomPackRepoDep,
) -> dict | None:
    """frozen data_versions 가 custom run 이면 그 봉인 pack body 를 반환 (자기완결).

    빌트인(slug=speculum-builtin)/pack 키 부재(구 run)면 None — registry 가 frozen
    slug/version 으로 정본 재로드하므로 body 불요(빌트인 export byte 불변). custom
    slug 면 `get_by_slug_version(slug, version, user_id)` 로 봉인 body 조회. 소유
    custom pack 이 없으면(삭제 등) None — 이 경우 export 는 body 없이 나가고
    reproduce 는 "body 필요" 로 matches=False (정직한 재현 불가).

    user_id 는 CurrentUserDep — export 호출자의 소유 pack 만 동봉(IDOR 일관).
    """
    slug = data_versions.get(_PACK_SLUG_KEY)
    version = data_versions.get(_PACK_VERSION_KEY)
    if not slug or not version or slug == BUILTIN_PACK_SLUG:
        # 빌트인 / pack 키 부재 — body 불요.
        return None
    record = custom_pack_repo.get_by_slug_version(slug, version, user_id=user_id)
    if record is None:
        return None
    return record.body


def _reconstruct_snapshot(snap_in: _SnapshotIn) -> ScreenRunSnapshot:
    """_SnapshotIn → ScreenRunSnapshot 도메인 재구성 (reproduce·정정진단 공용).

    ScreenRunBuilder.build 가 canonical 정규화 + result_hash 재계산. 재현·진단은
    user_id 무관 — user_id 는 snap_in 에서 pass-through 하되 호출부는 그 값을
    사용하지 않음 (ADR-0021 D5). None 필드는 이미 resolve_snapshot 의 필수 검증을
    통과했으므로 안전.
    """
    return ScreenRunBuilder.build(
        run_id=snap_in.id or uuid4(),
        user_id=snap_in.user_id,
        conditions=list(snap_in.conditions),  # type: ignore[arg-type]
        selected_factors=list(snap_in.selected_factors or ()),
        # ADR-0023 D7 — 구 export (security_types 부재) 는 None → builder 가
        # default ("common",) 해소 (하위호환, 보통주 run 재현 무영향).
        security_types=(
            list(snap_in.security_types) if snap_in.security_types is not None else None
        ),
        as_of=snap_in.as_of,  # type: ignore[arg-type]
        result_codes=list(snap_in.result_codes),  # type: ignore[arg-type]
        data_versions=dict(snap_in.data_versions),  # type: ignore[arg-type]
        computed_at=snap_in.computed_at,
    )


@router.post("/reproduce", response_model=ReproduceOut, status_code=status.HTTP_200_OK)
async def reproduce_run_endpoint(
    body: ReproduceIn,
    batch_run_repo: BatchRunRepoDep,
    stocks_repo: StocksRepoDep,
    evaluator: FactorEvaluatorDep,
    price_repo: PriceRepoDep,
    financial_repo: FinancialRepoDep,
    corporate_action_repo: CorporateActionRepoDep,
    market_cap_repo: MarketCapRepoDep,
    treasury_repo: TreasurySharesRepoDep,
    macro_repo: MacroIndicatorRepoDep,
    dividend_repo: DividendRepoDep,
) -> ReproduceOut:
    """Export JSON import → M1 reproduce_run 재현 — M2 T80 Phase 1.

    export JSON (POST /api/runs/{id}/export 결과) 또는 그 `run` 부분
    (ScreenRunSnapshotOut)을 body 로 받아, M1 `reproduce_run` 을 호출하여
    frozen batch_id cutoff 기준으로 재실행한다.

    재현 경로:
        1. body.resolve_snapshot() → _SnapshotIn (export wrapper/run 직접 입력 통합).
        2. _SnapshotIn → ScreenRunSnapshot 도메인 객체 재구성
           (ScreenRunBuilder.build 의 결정론적 정규화 재사용).
        3. reproduce_run(snapshot, ...) → ReproduceResult(matches, result_codes).
        4. matches=True = byte-동일 재현 성공. False = data_versions batch_id 의
           frozen batch 가 없거나(삭제/이전) 데이터 변화로 불일치.

    factor pack 재로드 (ADR-0025 D5):
        운영 활성 pack(ActivePackDep/DEFAULT_PACK)을 무조건 주입하지 않는다. frozen
        data_versions 의 factor_pack_slug/version/content_hash 로 PackRegistry 가
        그 시점 pack 을 재로드 (정책이 바뀐 뒤에도 frozen 정책으로 재현 — §2.10).
        pack 키가 없는 구 run 만 reproduce_run 이 DEFAULT_PACK 으로 fallback.

    user 비종속 (ADR-0021 D5):
        - reproduce_run 이 user_id 를 입력받지 않음 (fact 조회는 batch_cutoff 만).
        - export JSON 에 user_id 가 있어도 재현 경로에서 무시.
        - 공유된 export JSON 을 누구든 재현 가능 — CurrentUserDep 미사용.

    오류 처리:
        - 필수 필드 누락/형식 불일치 → 422 (Pydantic validation).
        - export_format 불일치 → 400.
        - batch_id 가 batch_runs 에 없으면 reproduce_run 이 EXCLUDE_ALL_CUTOFF
          로 처리 → 결과가 빈 tuple 또는 불일치 (matches=False). 별도 404 X —
          frozen batch 의 부재가 재현 실패를 의미하며 그 결과 자체가 정보.
    """
    # 1. body → _SnapshotIn 추출 (export wrapper / run 직접 입력 통합).
    try:
        snap_in = body.resolve_snapshot()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # 2. _SnapshotIn → ScreenRunSnapshot 도메인 재구성 (단일 헬퍼).
    snapshot = _reconstruct_snapshot(snap_in)

    # 3. M1 reproduce_run 재사용 — frozen batch_id cutoff 기준 재실행. pack 은
    #    ActivePackDep 주입이 아니라 frozen data_versions 기반 PackRegistry 재로드
    #    (ADR-0025 D5). DEFAULT_PACK 은 pack 키가 없는 구 run 의 fallback 일 뿐.
    #    custom run 은 export JSON 에 동봉된 봉인 custom pack body(body.pack)로
    #    재구성 — user/DB 무관 자기완결(ADR-0021 D5). 빌트인 run 은 pack=None.
    result = reproduce_run(
        snapshot,
        batch_run_repo=batch_run_repo,
        stocks_repo=stocks_repo,
        pack=DEFAULT_PACK,
        evaluator=evaluator,
        price_repo=price_repo,
        financial_repo=financial_repo,
        corporate_action_repo=corporate_action_repo,
        market_cap_repo=market_cap_repo,
        treasury_repo=treasury_repo,
        # M2 T81 — macro field 실연결(§2.10 break 수정). save_run 이 macro_repo 를
        # 배선하므로 reproduce 도 동일 배선해야 macro 조건이 byte-동일 재현(silent
        # N/A 로 매칭 깨지지 않게). macro 는 vintage PIT 가 재현 축이라 cutoff 불요.
        macro_repo=macro_repo,
        # M7 #4 — dividend / total return field 실연결(§2.10 break 수정). live
        # save 가 dividend_repo 를 배선하므로 reproduce 도 동일 배선해야 byte-동일
        # 재현(dividend-yield/total-return 조건이 silent N/A 로 매칭 깨지지 않게).
        dividend_repo=dividend_repo,
        pack_registry=_PACK_REGISTRY,
        custom_pack_body=body.pack,
    )

    # 4. 응답 구성 — matches 의미 안내 + 프론트 Phase 2 용 result 포함.
    if result.matches:
        note = "재현 성공 — frozen batch 기준 byte-동일 (§2.10 Reproducibility)."
    elif result.reproduce_note is not None:
        # frozen pack 재로드 불가/변조 — 구체 사유 (ADR-0025 D5).
        note = result.reproduce_note
    else:
        note = (
            "재현 불일치 — data_versions 의 batch_id 에 해당 batch row 가 없거나"
            " (삭제/이전), frozen batch 이후 데이터·정책 변화가 원인일 수 있습니다."
        )

    return ReproduceOut(
        matches=result.matches,
        reproduced_result_codes=result.result_codes,
        original_result_codes=snapshot.result_codes,
        result_hash=snapshot.result_hash,
        note=note,
        # pack_tampered: 변조(content_hash 불일치)만 True. 부재는 False(M5 #3b).
        pack_tampered=result.pack_tampered,
    )


@router.post(
    "/restatement-lag",
    response_model=RestatementLagOut,
    status_code=status.HTTP_200_OK,
)
async def restatement_lag_endpoint(
    body: ReproduceIn,
    financial_repo: FinancialRepoDep,
) -> RestatementLagOut:
    """정정 미반영 진단 — M5 #4b (T-M5-04b, ADR-0033 D6).

    frozen Screen Run 의 result_codes 종목 중, frozen 시점(`as_of`) **이후
    정정공시된** financial 이 있는 종목을 **사실로** 식별한다(§2.1 — "이 결과는 N건
    정정 후행"). 판정·해석 문구 없이 종목코드·건수 사실만 반환.

    입력 경로(reproduce 와 동일):
        1. body.resolve_snapshot() → _SnapshotIn (export wrapper/run 직접 입력 통합).
        2. _SnapshotIn → ScreenRunSnapshot 재구성 (ScreenRunBuilder.build 재사용 —
           result_codes·as_of 만 진단에 사용).
        3. assess_restatement_lag(snapshot, financial_repo) → RestatementLag.

    user 비종속 (ADR-0021 D5, 기존 reproduce 와 동일):
        - CurrentUserDep 미사용 — 진단은 fetch_restatement_history(read-only)만.
        - export JSON 에 user_id 가 있어도 진단 경로에서 무시. 공유 export 누구든 진단.

    read-only:
        fetch_restatement_history 는 SELECT 만 (ADR-0020 append-only 무변경).

    오류 처리(reproduce 와 동일):
        - 필수 필드 누락/형식 불일치 → 422 (Pydantic validation).
        - export_format 불일치 → 400.
    """
    # 1. body → _SnapshotIn 추출 (export wrapper / run 직접 입력 통합).
    try:
        snap_in = body.resolve_snapshot()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # 2. _SnapshotIn → ScreenRunSnapshot 재구성 (reproduce 와 동일 헬퍼 재사용).
    #    진단은 result_codes·as_of 만 쓰나, 재구성 경로는 reproduce 와 단일화한다.
    snapshot = _reconstruct_snapshot(snap_in)

    # 3. 정정 미반영 진단 — as_of 전후 record 공존 group 이 있는 종목을 사실로 식별.
    lag = assess_restatement_lag(snapshot, financial_repo)

    return RestatementLagOut(
        restated_codes=lag.restated_codes,
        count=lag.count,
    )
