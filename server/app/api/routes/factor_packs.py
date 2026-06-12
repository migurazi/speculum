"""Factor Pack endpoint — POST /api/factor-packs/{validate,evaluate}.

M2 T71/T74 Phase 1 (backend). Factor Lab editor 가 사용자 정의 pack JSON 을 실시간
검증(validate) + 저장 없이 즉시 평가(evaluate, 미리보기)하기 위한 API.
Phase 2 (frontend) 가 본 endpoint 들을 호출한다.

validate — 검증 단계 (custom pack 기준 — 빌트인과의 차이는 아래 참조):
1. schema         — JSON schema 구조 (필수 필드, 허용 op enum 등). 실패 시 fail-fast.
2. identity       — canonical_id / uuid 중복.
3. acyclic        — factor 의존 그래프 정적 순환 탐지.
4. citation       — pack / factor-level citation 의무.
5. forbidden_vocab— factor name / description / alt_names 금지 어휘.

custom vs builtin 차이:
- validate_builtin_no_composite **미적용** — custom pack 은 Composite op
  (weighted_sum / zscore / percentile / min_max_scale) 허용. 사용자 정의
  multi-factor score 가 핵심 use-case. ADR-0022 D5 금지는 빌트인 전용.
- validate_hash **미적용** — editor 에서 실시간 검증 시 hash 미채워진 상태
  (placeholder / 없음) 가 정상. hash 무결성은 저장/deploy 단계에서 강제 (T71 Phase 2).

evaluate (T74 Phase 1) — 검증 통과한 custom pack 을 지정 종목·as_of 에 즉시 평가:
- custom pack body 를 `LoadedPack` 으로 감싸 `DbFieldProvider`(종목-국소 factor +
  파생 필드) 와 `DbUniverseDistributionProvider`(유니버스-상대 op) 에 주입.
- 분포 provider 는 **request 단위 1 인스턴스** 공유 — T72 의 field별 분포 캐싱이
  O(N) 1 회를 보장 (codes 루프 밖에서 생성).
- PIT — as_of 고정(provider/분포 모두). 결과는 값/N-A 사실만 (No Advice).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies.repositories import (
    CorporateActionRepoDep,
    CustomPackRepoDep,
    DividendRepoDep,
    FactorEvaluatorDep,
    FinancialRepoDep,
    MacroIndicatorRepoDep,
    MarketCapRepoDep,
    PriceRepoDep,
    StocksRepoDep,
    TreasurySharesRepoDep,
)
from app.schemas.custom_packs import (
    CommunityPackBodyOut,
    CommunityPackListOut,
    CommunityPackOut,
)
from app.schemas.factor_packs import (
    CustomPackEvaluateOut,
    CustomPackValidateOut,
    EvaluatedFactorOut,
    EvaluatedStockOut,
    FactorConflictOut,
    FactorPackExportOut,
    ImportApplyOut,
    ImportCheckOut,
    ReferencePackListOut,
    ReferencePackOut,
    ResolutionAppliedOut,
    ValidationIssueOut,
)
from app.services.db_field_provider import DbFieldProvider
from app.services.db_universe_distribution import (
    SMALL_SAMPLE_THRESHOLD,
    DbUniverseDistributionProvider,
)
from app.services.factor_pack import (
    ForbiddenVocabInPack,
    LoadedPack,
    _universe_relative_fields,
    compute_pack_hash,
    load_reference_packs,
    validate_custom_pack,
    validate_shared_meta,
)
from app.services.factor_pack_identity import (
    CanonicalOverrideForbidden,
    CrossPackIdentityConflict,
    InvalidResolution,
    UuidReuseViolation,
    apply_resolutions,
    assert_no_unresolved_conflicts,
    assert_no_uuid_reuse,
    builtin_existing_index,
    derive_tier,
    detect_cross_pack_conflicts,
)
from app.services.total_return_adjuster import TotalReturnAdjuster

router = APIRouter(prefix="/api", tags=["factor-packs"])

# factor 수 방어 상한 — schema maxItems 가 없는 경우를 대비한 route-level 방어.
# validate_schema 가 이미 schema 레벨에서 구조를 강제하나, 극단적 payload 에 대해
# 서비스 전에 빠르게 거부할 수 있도록 route 에서도 체크.
_MAX_FACTORS: int = 256

# evaluate 의 codes 상한 — ad-hoc 미리보기는 소수 종목 대상. 유니버스-상대 op
# 분포는 codes 와 무관(전 universe 1 회)하나, 종목별 provider 평가가 codes 에
# 비례하므로 방어 상한. 초과 시 422 (저장된 screen 과 달리 즉시 미리보기 범위).
_MAX_CODES: int = 20


@router.post(
    "/factor-packs/validate",
    response_model=CustomPackValidateOut,
    status_code=status.HTTP_200_OK,
)
async def validate_custom_pack_endpoint(body: dict[str, Any]) -> CustomPackValidateOut:
    """사용자 정의 factor pack JSON 검증.

    Factor Lab editor 가 pack JSON 을 실시간으로 검증할 때 호출한다. body 가
    dict 가 아닌 경우(배열 등)는 FastAPI 가 422 처리. dict 이면 validate_schema
    가 구조를 검증 — route 는 JSON 파싱·기본 타입만 보장.

    factor 수 방어 상한: `_MAX_FACTORS`(256) 초과 시 issues 에 schema 이슈로 수집
    (validate_schema 가 schema 레벨에서 거부하나 schema 에 maxItems 없을 때 대비).
    실제로는 schema 의 type/structure 검증이 먼저 동작하므로 이 분기는 거의 발동 않음.

    Response:
        {valid: bool, issues: [{stage: str, message: str}, ...]}
        valid=True 이면 issues 는 빈 배열. valid=False 이면 1 개 이상 이슈.
    """
    # 극단적 factor 수 방어 — schema maxItems 가 강제하나 route 에서도 방어.
    factors = body.get("factors")
    if isinstance(factors, list) and len(factors) > _MAX_FACTORS:
        issue = ValidationIssueOut(
            stage="schema",
            message=f"factors 수 {len(factors)} 가 상한 {_MAX_FACTORS} 초과",
        )
        return CustomPackValidateOut(valid=False, issues=(issue,))

    raw_issues = validate_custom_pack(body)
    issue_outs = tuple(
        ValidationIssueOut(stage=i.stage, message=i.message) for i in raw_issues
    )
    return CustomPackValidateOut(valid=len(issue_outs) == 0, issues=issue_outs)


# =============================================================================
# POST /api/factor-packs/evaluate (M2 T74 Phase 1) — custom pack ad-hoc 평가
# =============================================================================


def _wrap_custom_pack(body: dict[str, Any]) -> LoadedPack:
    """검증 통과한 custom pack body 를 평가용 `LoadedPack` 으로 감싼다.

    `DbFieldProvider` / `DbUniverseDistributionProvider` 는 pack 을 `LoadedPack`
    으로 받아 `.body["factors"]` 를 참조(파생 필드 target_factor 조회). `load_pack`
    은 path 기반이라 ad-hoc body 에 부적합하므로, body 를 직접 `LoadedPack` 으로
    구성한다. computed_hash 는 `compute_pack_hash` 로 계산 — 미리보기 평가에는
    hash 가 쓰이지 않으나(저장 단계 freeze 용), LoadedPack contract 충족을 위해
    실제 값을 채운다(placeholder 가 아닌 결정적 hash — 일관성).
    """
    return LoadedPack(
        body=body,
        computed_hash=compute_pack_hash(body),
        factor_count=len(body["factors"]),
        pack_slug=body["pack_slug"],
        version=body["version"],
    )


def _normalize_codes(raw: list[Any]) -> list[str]:
    """요청 codes 를 6 자리 zero-pad 정규화 + 순서 보존 dedup.

    각 code 는 1~6 자리 numeric 만 허용(stocks._normalize_single_code 와 일관).
    형식 위반은 400, 정규화 후 빈 목록도 400 (HTTPException 으로 호출자 전파).
    """
    if not raw:
        raise HTTPException(status_code=400, detail="`codes` 가 비어 있습니다.")
    seen: dict[str, None] = {}  # ordered set — 입력 순서 보존 dedup.
    for code in raw:
        if not isinstance(code, str) or not code.isdigit() or len(code) > 6:
            raise HTTPException(
                status_code=400,
                detail="각 code 는 1~6 자리 숫자 문자열이어야 합니다.",
            )
        seen[code.zfill(6)] = None
    return list(seen.keys())


@router.post(
    "/factor-packs/evaluate",
    response_model=CustomPackEvaluateOut,
    status_code=status.HTTP_200_OK,
)
async def evaluate_custom_pack_endpoint(
    body: dict[str, Any],
    stocks_repo: StocksRepoDep,
    evaluator: FactorEvaluatorDep,
    price_repo: PriceRepoDep,
    financial_repo: FinancialRepoDep,
    corporate_action_repo: CorporateActionRepoDep,
    market_cap_repo: MarketCapRepoDep,
    treasury_repo: TreasurySharesRepoDep,
    macro_repo: MacroIndicatorRepoDep,
    dividend_repo: DividendRepoDep,
) -> CustomPackEvaluateOut:
    """custom factor pack 을 지정 종목·as_of 에 저장 없이 즉시 평가 (미리보기).

    Request body:
        {
          "pack": <factor pack JSON>,
          "as_of": "YYYY-MM-DD",
          "codes": ["005930", ...]   # 1~6 자리 numeric, 최대 _MAX_CODES 개
        }

    동작:
    1. **먼저 validate_custom_pack(pack)** — issues 있으면 평가하지 않고
       `{valid: false, issues: [...], results: []}` 반환(잘못된 pack 에 평가 비용
       지출 회피). valid 면 평가 진행.
    2. custom pack body 를 `LoadedPack` 으로 감싸(`_wrap_custom_pack`) provider
       들에 주입 — `DbFieldProvider.factor_pack` 이 `.body["factors"]` 참조.
    3. **유니버스-상대 op 분포 provider 는 request 단위 1 인스턴스 공유** — codes
       루프 **밖**에서 생성. T72 의 field별 분포 캐싱이 O(N) 1 회를 보장(종목마다
       새 인스턴스면 O(N²)). 종목-국소 factor 만 있어도 무해(분포 미요청 시 미계산).
    4. 각 code 마다 `DbFieldProvider`(as_of 고정) 구성 후 pack 의 각 factor 를
       `evaluator.evaluate(..., universe_distribution=<분포 provider>)` 로 평가.
       유니버스-상대 op 는 분포 주입으로 실값(universe_distribution_required N/A 가
       아닌 실 percentile/zscore/min_max).

    PIT: as_of 고정(provider + 분포 모두 effective_date<=as_of). 유니버스-상대 분포
    모집단은 as_of active universe(T72/ADR-0022 D3 — survivorship bias 방지).

    No Advice (backend): 결과는 **값/N-A 사실만**. 랭킹·정렬·점수 라벨 0(프론트
    시각 게이트는 T74 Phase2). code 는 사용자 명시 종목이라 노출 OK.
    """
    # ----- 1. 요청 파싱 + 방어 -----
    pack_body = body.get("pack")
    if not isinstance(pack_body, dict):
        raise HTTPException(
            status_code=400, detail="`pack` (object) 이 필요합니다.",
        )
    as_of_raw = body.get("as_of")
    if not isinstance(as_of_raw, str):
        raise HTTPException(
            status_code=400, detail="`as_of` (YYYY-MM-DD) 가 필요합니다.",
        )
    try:
        as_of = date.fromisoformat(as_of_raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"`as_of` 형식 오류 (YYYY-MM-DD): {as_of_raw!r}",
        ) from exc

    codes_raw = body.get("codes")
    if not isinstance(codes_raw, list):
        raise HTTPException(
            status_code=400, detail="`codes` (list) 가 필요합니다.",
        )
    codes = _normalize_codes(codes_raw)
    if len(codes) > _MAX_CODES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"codes 수 {len(codes)} 가 상한 {_MAX_CODES} 초과 "
                f"(ad-hoc 미리보기 범위)."
            ),
        )

    # ----- 2. 검증 먼저 — issues 있으면 평가 안 함 -----
    raw_issues = validate_custom_pack(pack_body)
    if raw_issues:
        issue_outs = tuple(
            ValidationIssueOut(stage=i.stage, message=i.message)
            for i in raw_issues
        )
        return CustomPackEvaluateOut(valid=False, issues=issue_outs, results=())

    # ----- 3. custom body → LoadedPack wrapper (provider 주입용) -----
    pack = _wrap_custom_pack(pack_body)

    # ----- 4. 유니버스-상대 op 분포 provider — request 단위 1 인스턴스 (codes
    #          루프 밖). T72 의 field별 분포 캐싱이 O(N) 1 회 보장. as_of 고정. -----
    distribution = DbUniverseDistributionProvider(
        as_of=as_of,
        stocks_repo=stocks_repo,
        pack=pack,
        evaluator=evaluator,
        price_repo=price_repo,
        financial_repo=financial_repo,
        corporate_action_repo=corporate_action_repo,
        market_cap_repo=market_cap_repo,
        treasury_repo=treasury_repo,
        macro_repo=macro_repo,
        # M7 #4 (§2.10 break) — dividend / total return field 배선. universe-상대
        # op 이 이 field 를 쓰면 분포 모집단이 종목별 평가 provider 와 동일 배선이라
        # 야 일관 (per-stock provider 는 이미 dividend_repo 주입됨).
        dividend_repo=dividend_repo,
    )

    # ----- 5. 종목별 평가 -----
    factors = pack_body["factors"]
    results: list[EvaluatedStockOut] = []
    for code in codes:
        # ADR-0023 D5 — 분포 partition 컨텍스트를 평가 대상 자산군으로 바인딩.
        # 유니버스-상대 op (zscore/percentile/min_max) 가 이 code 와 같은
        # security_type 모집단으로 산출되도록 분포 provider 에 설정 (보통주↔우선주
        # 이종 혼합 차단). active 가 아닌 code 는 default common.
        distribution.bind_target_security_type(code)
        # 종목-국소 factor + 파생 필드 해소용 provider (as_of 고정 — PIT).
        provider = DbFieldProvider(
            code=code,
            as_of=as_of,
            price_repo=price_repo,
            financial_repo=financial_repo,
            corporate_action_repo=corporate_action_repo,
            market_cap_repo=market_cap_repo,
            treasury_repo=treasury_repo,
            macro_repo=macro_repo,
            dividend_repo=dividend_repo,
            total_return_adjuster=TotalReturnAdjuster(),
            factor_pack=pack,
            evaluator=evaluator,
        )
        factor_outs: list[EvaluatedFactorOut] = []
        for factor in factors:
            result = evaluator.evaluate(
                factor, provider, as_of=as_of,
                universe_distribution=distribution,
            )
            # ADR-0024 D2/D5 — 소표본 디스클로저 (표시 전용, 값 생성 *후* 부착).
            # 그 factor 가 universe-상대 op (zscore/percentile/min_max_scale) 을
            # 포함하면, 쓰인 분포 field 들의 모집단 크기를 조회해 **min(n)** (가장
            # 빈약한 모집단이 전체 신뢰도 상한, ADR-0024 D5) 으로 귀속한다. bind
            # 상태 (이 code 의 자산군 partition 모집단) 에서 조회 — 위에서
            # bind_target_security_type(code) 후이므로 분포 op 가 본 평가에서 쓴
            # 것과 같은 모집단의 n. universe-상대 op 없는 factor 는 모집단 개념이
            # 없어 sample_size=None, small_sample=False.
            #
            # **result_codes 무관 (ADR-0024 D4 c)**: sample_size/small_sample 은
            # 표시 전용 — evaluator 산출값 (result.value) 과 무관하며, n==1
            # percentile=100 케이스도 값 100 을 보존하고 small_sample=True 만 덧붙인다
            # (N/A 강등 아님). screener/result_codes 경로에는 진입하지 않는다.
            ur_fields = _universe_relative_fields(factor["formula"]["ast"])
            if ur_fields:
                # min(n) — 가장 빈약한 모집단 (ADR-0024 D5 귀속 규칙). 분포 캐시
                # hit (평가가 같은 (security_type, field) 분포를 이미 채움).
                min_n = min(distribution.sample_size(f) for f in ur_fields)
                sample_size: int | None = min_n
                # 임계 비교는 표시층 책임 (ADR-0024 D4 c) — 분포 op 가 아닌 여기서만.
                small_sample = min_n < SMALL_SAMPLE_THRESHOLD
            else:
                sample_size = None
                small_sample = False
            factor_outs.append(EvaluatedFactorOut(
                canonical_id=result.factor_canonical_id,
                name=factor["name"],
                unit=factor["unit"],
                value=(str(result.value) if result.value is not None else None),
                is_na=result.is_na,
                na_reason=result.na_reason,
                sample_size=sample_size,
                small_sample=small_sample,
            ))
        results.append(EvaluatedStockOut(code=code, factors=tuple(factor_outs)))

    return CustomPackEvaluateOut(valid=True, issues=(), results=tuple(results))


# =============================================================================
# Pack 간 identity 충돌 import + self-identifying export (M2 T75 — ADR-0022 D8)
# =============================================================================
#
# custom pack 은 영속화되지 않으므로(in-memory loader) import = "검증·충돌해소된
# JSON 을 editor 상태로 수용" 하는 stateless 경로다. 비교 기준(existing index)은
# 현재 빌트인 DEFAULT_PACK 뿐(장래 영속 import 합류 시 build_existing_index 확장).
# user 컨텍스트 불필요 — T68(OAuth) 무관, 공유 JSON 은 user 비종속(ADR-0021 D5 정신).


def _validate_import_pack(pack_body: Any) -> dict[str, Any]:
    """import/export body 의 `pack` 필드 파싱 + factor 수 방어. 실패 시 HTTPException."""
    if not isinstance(pack_body, dict):
        raise HTTPException(status_code=400, detail="`pack` (object) 이 필요합니다.")
    factors = pack_body.get("factors")
    if isinstance(factors, list) and len(factors) > _MAX_FACTORS:
        raise HTTPException(
            status_code=422,
            detail=f"factors 수 {len(factors)} 가 상한 {_MAX_FACTORS} 초과",
        )
    return pack_body


def _conflict_to_out(conflict: Any) -> FactorConflictOut:
    return FactorConflictOut(
        canonical_id=conflict.canonical_id,
        incoming_hash=conflict.incoming_hash,
        existing_hash=conflict.existing_hash,
        existing_tier=conflict.existing_tier,
        allowed_resolutions=conflict.allowed_resolutions,
    )


def _gate_import_pack_meta(pack_body: dict[str, Any]) -> ValidationIssueOut | None:
    """외부 import pack 의 공개 메타(citation.title + description) 게이트 — ADR-0032 D4.

    `validate_custom_pack`(factor name/desc, save+import 공유)과 별도로, **import
    시점에만** pack-level 공개 메타를 `assert_clean(USER_SHARED)` 검사한다. public-toggle
    (ADR-0028 D2)과 동일 필드·scope — 외부 저자의 "추천주 Buy 종목" 류 pack 설명/제목이
    게이트 통과 전 editor 에 렌더되는 §2.2 hole 을 차단(client 는 valid=False 면 미렌더).

    private pack 의 자기 메타는 save 시점에 검사하지 않으므로(USER_PRIVATE, ADR-0028 D2)
    본 게이트는 import 경로 전용이다.

    Returns:
        위반 시 forbidden_vocab `ValidationIssueOut`(어휘 echo 0 — forbidden_words B4),
        깨끗하면 None.
    """
    try:
        validate_shared_meta(pack_body)
    except ForbiddenVocabInPack:
        # 어휘 echo 0(forbidden_words B4) — 일반 메시지만.
        return ValidationIssueOut(
            stage="forbidden_vocab",
            message="공유 메타(제목/설명)에 금지 어휘가 있어 import 할 수 없습니다.",
        )
    return None


@router.post(
    "/factor-packs/import-check",
    response_model=ImportCheckOut,
    status_code=status.HTTP_200_OK,
)
async def import_check_endpoint(body: dict[str, Any]) -> ImportCheckOut:
    """import 전 dry-run 충돌 탐지 (ADR-0022 D8.4 Pass 1) — 자동 적용 0.

    Request body: `{"pack": <factor pack JSON>}`.

    동작:
    1. validate_custom_pack — 무효 pack 이면 issues 반환(충돌 검사 이전).
    2. assert_no_uuid_reuse — uuid 가 다른 이름으로 기존에 쓰였으면 **422**
       (resolution 으로 해결 불가, D8.1 개체 ID 위반).
    3. detect_cross_pack_conflicts — 같은 canonical_id·다른 정의 충돌 목록.
       canonical tier 충돌은 allowed_resolutions=["skip","rename"](replace 불가, D8.2).

    Response: `{valid, issues, conflicts, clean}` — 사용자가 conflicts 의 각
    resolution 을 골라 /import 로 Pass 2 호출.
    """
    pack_body = _validate_import_pack(body.get("pack"))

    issue_outs = [
        ValidationIssueOut(stage=i.stage, message=i.message)
        for i in validate_custom_pack(pack_body)
    ]
    # ADR-0032 D4 — 외부 pack 공개 메타 게이트(import 전용).
    meta_issue = _gate_import_pack_meta(pack_body)
    if meta_issue is not None:
        issue_outs.append(meta_issue)
    if issue_outs:
        return ImportCheckOut(
            valid=False, issues=tuple(issue_outs), conflicts=(), clean=(),
        )

    existing = builtin_existing_index()
    # uuid 재사용은 resolution 으로 풀 수 없는 위반 → 422 fail-loud (D8.1).
    try:
        assert_no_uuid_reuse(pack_body, existing)
    except UuidReuseViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    conflicts = detect_cross_pack_conflicts(pack_body, existing)
    conflict_cids = {c.canonical_id for c in conflicts}
    clean = tuple(
        f["canonical_id"] for f in pack_body["factors"]
        if f["canonical_id"] not in conflict_cids
    )
    conflict_outs = tuple(_conflict_to_out(c) for c in conflicts)
    # ADR-0032 D2 — 봉인 무결성 진단(비차단 경고). 선언 content_hash 가 본문과
    # 다르면 저자 봉인 파손(변조/손상). 선언 부재(editor pack)면 mismatch 아님.
    declared_hash = pack_body.get("content_hash")
    hash_mismatch = (
        isinstance(declared_hash, str)
        and declared_hash != compute_pack_hash(pack_body)
    )
    return ImportCheckOut(
        valid=True, issues=(), conflicts=conflict_outs, clean=clean,
        hash_mismatch=hash_mismatch,
    )


@router.post(
    "/factor-packs/import",
    response_model=ImportApplyOut,
    status_code=status.HTTP_200_OK,
)
async def import_endpoint(body: dict[str, Any]) -> ImportApplyOut:
    """충돌 resolution 적용 import (ADR-0022 D8.4 Pass 2).

    Request body:
        {
          "pack": <factor pack JSON>,
          "resolutions": {
            "<canonical_id>": {"action": "skip"|"rename"|"replace",
                               "new_canonical_id": "<rename 시>"},
            ...
          }
        }

    동작:
    1. validate_custom_pack — 무효면 issues 반환(pack=None).
    2. assert_no_uuid_reuse — uuid 위반 시 422.
    3. detect + assert_no_unresolved_conflicts — **미해결 충돌·canonical replace·
       부정 resolution 이면 422**(silent override 금지, D8.4/D8.2).
    4. apply_resolutions(skip/rename/replace) → content_hash 재봉인.

    Response: `{valid, issues, pack, applied}` — pack 은 충돌 해소 + hash 봉인된
    최종 body. editor 가 이를 작업 상태로 수용.
    """
    pack_body = _validate_import_pack(body.get("pack"))
    resolutions = body.get("resolutions", {})
    if not isinstance(resolutions, dict):
        raise HTTPException(
            status_code=400, detail="`resolutions` (object) 형식이 필요합니다.",
        )

    issue_outs = [
        ValidationIssueOut(stage=i.stage, message=i.message)
        for i in validate_custom_pack(pack_body)
    ]
    # ADR-0032 D4 — 외부 pack 공개 메타 게이트(import 전용, Pass 2 도 방어).
    meta_issue = _gate_import_pack_meta(pack_body)
    if meta_issue is not None:
        issue_outs.append(meta_issue)
    if issue_outs:
        return ImportApplyOut(
            valid=False, issues=tuple(issue_outs), pack=None, applied=(),
        )

    existing = builtin_existing_index()
    # 무결성 위반·미해결 충돌은 모두 422 fail-loud (D8.4 — "미해결=거부").
    try:
        assert_no_uuid_reuse(pack_body, existing)
        conflicts = detect_cross_pack_conflicts(pack_body, existing)
        assert_no_unresolved_conflicts(conflicts, resolutions)
        new_body = apply_resolutions(pack_body, conflicts, resolutions, existing)
    except (
        UuidReuseViolation,
        CrossPackIdentityConflict,
        CanonicalOverrideForbidden,
        InvalidResolution,
    ) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # apply_resolutions 가 stale content_hash 를 제거 — 새 정의 집합으로 재봉인(D8.5).
    new_body["content_hash"] = compute_pack_hash(new_body)

    # resolution 적용(특히 rename)이 새 identity 충돌을 만들지 않았는지 재검증 —
    # rename 대상이 같은 pack 내 다른 factor 명과 겹치면 silent 중복이 될 수 있다
    # (apply 의 seen_cids 가드는 처리 순서에 의존하므로 완전하지 않음). 결과
    # 무결성을 fail-loud 로 보장(D8.4 정신 — 무결성 위반은 조용히 통과 금지).
    post_issues = validate_custom_pack(new_body)
    if post_issues:
        first = post_issues[0]
        raise HTTPException(
            status_code=422,
            detail=(
                f"resolution 적용 후 pack 무결성 위반 — {first.stage}: {first.message}"
            ),
        )

    applied = tuple(
        ResolutionAppliedOut(
            canonical_id=c.canonical_id,
            action=resolutions[c.canonical_id]["action"],
            new_canonical_id=resolutions[c.canonical_id].get("new_canonical_id"),
        )
        for c in conflicts
    )
    return ImportApplyOut(valid=True, issues=(), pack=new_body, applied=applied)


@router.post(
    "/factor-packs/export",
    response_model=FactorPackExportOut,
    status_code=status.HTTP_200_OK,
)
async def export_endpoint(body: dict[str, Any]) -> FactorPackExportOut:
    """custom/community pack 을 self-identifying JSON 으로 export (ADR-0022 D8.5).

    Request body: `{"pack": <factor pack JSON>}`.

    동작:
    1. validate_custom_pack — 무효 pack 은 export 불가(422).
    2. tier 판정 — canonical(빌트인)은 export 무의미(repo 에 이미 존재) → 422.
       community/user tier 만.
    3. content_hash 봉인 — 수입측이 정의 동일성을 판정할 권위 기준(D8.5 필수).

    Response: marker wrapper `{export_format, pack_schema_version, tier, pack}`.
    """
    pack_body = _validate_import_pack(body.get("pack"))

    raw_issues = validate_custom_pack(pack_body)
    if raw_issues:
        first = raw_issues[0]
        raise HTTPException(
            status_code=422,
            detail=(
                f"유효하지 않은 pack 은 export 불가 — {first.stage}: {first.message}"
            ),
        )

    tier = derive_tier(pack_body["pack_slug"])
    if tier == "canonical":
        raise HTTPException(
            status_code=422,
            detail=(
                "빌트인 canonical pack 은 export 대상이 아닙니다(repo 에 이미 존재). "
                "community/user tier pack 만 export 합니다."
            ),
        )

    # content_hash 봉인 — 기존 값 무관하게 재계산해 정의를 잠근다(D8.5).
    sealed = {**pack_body, "content_hash": compute_pack_hash(pack_body)}
    return FactorPackExportOut(tier=tier, pack=sealed)


@router.get(
    "/factor-packs/reference",
    response_model=ReferencePackListOut,
    status_code=status.HTTP_200_OK,
)
async def list_reference_packs_endpoint() -> ReferencePackListOut:
    """speculum 제공 reference pack(리츠 ffo-multiple 등) 목록 — 익명 공개 (ADR-0023 D8).

    Factor Lab 이 불러와 editor pack 으로 쓰거나 evaluate 에 전달하는 **참조 정의**.
    빌트인(active universe pack)이 아니라 `builtin-packs/reference/` 의 pack
    (load_reference_packs 가 전 검증). 참조 정의는 fact tier — `CurrentUserDep`
    미사용(로그인 무관, ADR-0021 D4 미인증 fact 익명 허용 정합). 빌트인 content_hash
    무관(재현성 무영향).
    """
    packs = load_reference_packs()
    return ReferencePackListOut(packs=tuple(
        ReferencePackOut(
            pack_slug=p.pack_slug,
            version=p.version,
            factor_count=p.factor_count,
            body=p.body,
        )
        for p in packs
    ))


# =============================================================================
# GET /api/factor-packs/community — 공개 pack 목록 (ADR-0028 D3)
# =============================================================================


@router.get(
    "/factor-packs/community",
    response_model=CommunityPackListOut,
    status_code=status.HTTP_200_OK,
)
async def list_community_packs_endpoint(
    repo: CustomPackRepoDep,
) -> CommunityPackListOut:
    """사용자가 공개(visibility=='public')로 공유한 custom pack 목록 — ADR-0028 D3.

    인증 불요(공개 목록 — CurrentUserDep 미사용). `repo.list_public()` 가
    visibility=='public' 만 user 무관 반환(비공개 누출 차단의 단일 predicate).

    정렬: **created_at 역순(사실)만**. 큐레이션 신호 0 — 다운로드 수/인기/별점/
    순위/Top-N/추천 배지 없음(ADR-0028 D3, §2.3). 다운로드 카운트 자체를
    집계·노출하지 않는다. body 전체는 노출하지 않고 공개 메타(name=citation.title
    + description + content_hash)만 — import 는 별도 흐름(import-check/import 재사용).
    """
    public = repo.list_public()
    items = tuple(CommunityPackOut.from_domain(p) for p in public)
    return CommunityPackListOut(packs=items, total=len(items))


@router.get(
    "/factor-packs/community/{pack_slug:path}/versions/{version}",
    response_model=CommunityPackBodyOut,
    status_code=status.HTTP_200_OK,
)
async def get_community_pack_endpoint(
    pack_slug: str,
    version: str,
    repo: CustomPackRepoDep,
) -> CommunityPackBodyOut:
    """공개 pack 단건(전체 body) — ADR-0028 D4 import 재료. 인증 불요.

    importer 가 본 endpoint 로 공개 pack 의 전체 body 를 받아 **기존**
    import-check/import 흐름(stateless)에 그대로 투입한다 — 공유 surface 는
    body fetch 만 추가하고 충돌 resolve/봉인 로직은 재사용(silent merge 금지·
    content_hash fail-loud 유지, ADR-0028 D4). 복제된 pack 은 importer 가 별도로
    POST /saved 하면 importer 의 새 private pack 이 된다(원본과 독립 — user 격리).

    `repo.get_public` 은 visibility=='public' 만 반환(비공개 body 누출 차단).
    미존재/비공개 → 404(구별 안 함 — private pack 의 존재 여부 노출 차단).

    pack_slug 은 'user/...' 처럼 `/` 를 포함하므로 path converter(`:path`)로 받고,
    버전 구분을 위해 `/versions/{version}` segment 를 둔다(slug 와 version 경계
    모호성 제거).
    """
    pack = repo.get_public(pack_slug, version)
    if pack is None:
        raise HTTPException(
            status_code=404, detail="공개된 pack 을 찾을 수 없습니다.",
        )
    return CommunityPackBodyOut.from_domain(pack)
