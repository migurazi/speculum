"""Stocks endpoint — `/api/stocks/search` + `/api/stocks/{code}`.

설계 (oracle T25 자문 P0+P1):
- AsOfPolicy dependency 통합 — `as_of` query + X-AsOf-* header
- Pydantic v2 strict schemas (`from_domain` factory)
- StockStatus enum — 200 + status (미상장/폐지 구별)
- lineage-aware fetch_by_code (code_history 시점 매칭)
- FactorEvaluator + FieldProvider 통합 — Stock Detail 지표 카드
- normalize_single_code helper — `5930` → `005930` zero-pad
- N/A factor 의 wire 표현 (is_na + na_reason)

본 사이클의 FieldProvider 는 endpoint 가 `FactorEvaluator` 의 평가에 직접 사용
하지 않음 — 본 사이클은 detail endpoint 에서 EvaluationResult 만 반환하는
**stub** 으로 시작 (T18 합류 후 실제 fact pipeline 합류). 그러나 wire schema
(`FactorValueOut`) 는 EvaluationResult 의 모든 필드 cover — 후속 사이클이
endpoint 본체만 swap.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, HTTPException, Path, Query

from app.api.dependencies import NormalizedAsOfDep
from app.api.dependencies.repositories import (
    ActivePackDep,
    FactorEvaluatorDep,
    StocksRepoDep,
)
from app.schemas.stocks import (
    DEFAULT_DISPLAY_FACTORS,
    FactorValueOut,
    StockCompareOut,
    StockDetailOut,
    StockSearchPageOut,
    StockSummaryOut,
)

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


# KRX 종목코드 형식 — 6 자리 numeric (oracle 결정 3).
# 1~6 자리 입력 허용 (zero-pad 적용). 비숫자 거부.
_CODE_PATH_REGEX: Final[str] = r"^\d{1,6}$"

# Compare 의 codes query — 2~6 개 (AC-F-05). dedup 후 limit 검증.
_COMPARE_MIN_CODES: Final[int] = 2
_COMPARE_MAX_CODES: Final[int] = 6


def _normalize_single_code(code: str) -> str:
    """KRX 종목코드 6 자리 zero-pad — `screen_run.normalize_stock_codes` 와 일관.

    Path validator (`Path(pattern=...)`) 가 비숫자 / 7+ 자리 사전 차단. 본 함수
    자체도 defense — 직접 호출 시 6 자리 초과면 `ValueError` (oracle 2 차 P1 #2).
    """
    if not code.isdigit():
        raise ValueError(f"code must be digits, got {code!r}")
    if len(code) > 6:
        raise ValueError(f"code exceeds 6 digits: {code!r}")
    return code.zfill(6)


@router.get("/search", response_model=StockSearchPageOut)
async def search_stocks(
    as_of: NormalizedAsOfDep,
    repo: StocksRepoDep,
    q: str = Query(
        ...,
        min_length=1,
        max_length=100,
        description="검색어 — 한글 종목명 또는 종목코드 (substring).",
    ),
    limit: int = Query(50, ge=1, le=200),
    include_delisted: bool = Query(
        False,
        description="True 면 as_of 시점 폐지 종목도 포함. default False (survivorship bias 회피).",
    ),
) -> StockSearchPageOut:
    """KRX 종목 검색 — AC-F-02.

    Returns:
        검색 결과 페이지. `items` 는 prefix-match 우선 + 이름 asc 정렬.
        `next_cursor` 는 M0 = None (offset 기반 구현).
    """
    try:
        records = repo.search(
            q, as_of=as_of.value, limit=limit, include_delisted=include_delisted,
        )
    except ValueError as exc:
        # 빈 query 등 — Query(min_length=1) 가 422 로 사전 차단하나 defense.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    items = tuple(
        StockSummaryOut.from_master(r, as_of=as_of.value) for r in records
    )
    return StockSearchPageOut(items=items, total=len(items), next_cursor=None)


@router.get("/compare", response_model=StockCompareOut)
async def compare_stocks(
    as_of: NormalizedAsOfDep,
    repo: StocksRepoDep,
    evaluator: FactorEvaluatorDep,
    pack: ActivePackDep,
    codes: str = Query(
        ...,
        description=(
            "비교할 종목코드 — comma-separated (예: '005930,000660'). 2~6 개. "
            "자동 zero-pad + dedup. 1~6 자리 numeric 만 허용."
        ),
        min_length=1,
        max_length=200,
    ),
) -> StockCompareOut:
    """Compare 뷰 — 2~6 종목 multi-fetch (AC-F-05).

    Route 등록 순서가 중요 — `/compare` 가 `/{code}` 보다 먼저 등록되어야
    FastAPI 의 path matching 이 본 endpoint 를 먼저 시도. `compare` 가 `{code}`
    의 regex `^\\d{1,6}$` 와 겹치지 않으나, FastAPI 는 등록 순서 우선 매칭.

    응답 의미 (T25 패턴 일관):
        - items: 정규화·dedup 후 입력 순서 의 Stock Detail. lineage 존재 종목만.
        - not_found: lineage 부재 codes — 사용자가 오타 / 미존재 코드 입력.
    """
    # 1. codes parse + 정규화.
    raw_codes = [c.strip() for c in codes.split(",") if c.strip()]
    if not raw_codes:
        raise HTTPException(
            status_code=400,
            detail="`codes` query 가 비어 있습니다.",
        )

    # 형식 검증 (1~6 자리 numeric).
    for code in raw_codes:
        if not code.isdigit() or len(code) > 6:
            raise HTTPException(
                status_code=400,
                detail=(
                    "각 code 는 1~6 자리 숫자여야 합니다."
                ),
            )

    # 정규화 — 6 자리 zero-pad. dedup 은 set 이지만 order 보존 위해 직접.
    normalized_seen: dict[str, None] = {}  # ordered set 의미
    for code in raw_codes:
        normalized_seen[code.zfill(6)] = None
    normalized = list(normalized_seen.keys())

    # 2. 개수 검증 — dedup 으로 줄어든 경우 사용자에게 정규화 사실 안내 (oracle #4).
    if len(normalized) < _COMPARE_MIN_CODES:
        was_deduped = len(raw_codes) > len(normalized)
        suffix = (
            " (입력 코드들이 6 자리 zero-pad 정규화 후 중복으로 처리됨)"
            if was_deduped else ""
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"최소 {_COMPARE_MIN_CODES} 개의 서로 다른 종목이 필요합니다 "
                f"(received: {len(normalized)}){suffix}."
            ),
        )
    if len(normalized) > _COMPARE_MAX_CODES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"최대 {_COMPARE_MAX_CODES} 개까지 비교 가능합니다 "
                f"(received: {len(normalized)})."
            ),
        )

    # 3. fetch each + status 산출. lineage 부재면 not_found.
    items: list[StockDetailOut] = []
    not_found: list[str] = []
    # M0 — pipeline 미합류라 모든 종목에 동일 stub. tuple + FactorValueOut frozen
    # 이라 공유 안전. TODO(T18): 종목별 evaluator.evaluate(pack.factor, provider,
    # as_of) 호출로 교체 — 본 loop 내부에서 per-stock 평가.
    factor_stubs = _build_factor_stubs(pack)
    for code in normalized:
        master = repo.fetch_by_code(code, as_of=as_of.value)
        if master is None:
            not_found.append(code)
            continue
        items.append(StockDetailOut.from_master_and_factors(
            master, factor_stubs, as_of=as_of.value,
        ))

    # not_found 도 입력 순서 보존 — items 와 일관 (oracle T27 #5). 사용자가
    # 입력한 순서대로 "찾지 못한 코드" 가 표시되어 UI 가 자연.
    return StockCompareOut(
        items=tuple(items),
        not_found=tuple(not_found),
    )


@router.get("/{code}", response_model=StockDetailOut)
async def get_stock_detail(
    as_of: NormalizedAsOfDep,
    repo: StocksRepoDep,
    evaluator: FactorEvaluatorDep,
    pack: ActivePackDep,
    code: str = Path(pattern=_CODE_PATH_REGEX),
) -> StockDetailOut:
    """Stock Detail — AC-F-04 의 지표 카드 + 메타데이터.

    Status 분기 (oracle 결정 6):
        - lineage 부재 → 404
        - lineage 존재 + as_of < listing_date → 200 + status="not_yet_listed"
        - lineage 존재 + delisting_date <= as_of → 200 + status="delisted"
        - 그 외 → 200 + status="active"
    """
    normalized_code = _normalize_single_code(code)
    master = repo.fetch_by_code(normalized_code, as_of=as_of.value)
    if master is None:
        raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")

    # 본 사이클의 stub: FactorEvaluator + FieldProvider 의 실제 평가는 T18 합류
    # 후 실제 fact pipeline 가 있어야. M0 에서는 default factors 의 정의만 wire
    # 에 노출 (value=None, is_na=True, na_reason="no_field_provider_in_m0").
    factor_values = _build_factor_stubs(pack)

    return StockDetailOut.from_master_and_factors(
        master, factor_values, as_of=as_of.value,
    )


def _build_factor_stubs(pack) -> tuple[FactorValueOut, ...]:
    """DEFAULT_DISPLAY_FACTORS 의 wire stub 생성 — T18 합류 전 placeholder.

    각 factor 의 정의 (name / unit) 는 pack 에서 추출. value 는 N/A — 실제
    FieldProvider 가 없어서. T18 합류 후 본 함수가 evaluator + provider 호출로
    교체. `from_evaluation` factory 우회 금지 (oracle 2 차 C3) — 정상 EvaluationResult
    생성 후 schema factory 거치도록 일관성 유지.
    """
    from types import MappingProxyType
    from uuid import UUID as _UUID

    from app.services.factor_evaluator import (
        EVALUATOR_POLICY_VERSION,
        EvaluationResult,
    )

    factors_by_id = {f["canonical_id"]: f for f in pack.body["factors"]}
    results: list[FactorValueOut] = []
    for canonical_id in DEFAULT_DISPLAY_FACTORS:
        f = factors_by_id.get(canonical_id)
        if f is None:
            continue  # 빌트인에서 누락 시 skip
        # M0 stub — pipeline 미합류 표시.
        stub_result = EvaluationResult(
            value=None,
            is_na=True,
            na_reason="missing_input:pipeline_not_yet_wired_m0",
            factor_uuid=_UUID(f["uuid"]),
            factor_canonical_id=canonical_id,
            inputs_used=MappingProxyType({}),
            evaluator_version=EVALUATOR_POLICY_VERSION,
        )
        results.append(FactorValueOut.from_evaluation(
            stub_result, factor_name=f["name"], factor_unit=f["unit"],
        ))
    return tuple(results)
