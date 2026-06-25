"""Stocks endpoint — `/api/stocks/search` + `/api/stocks/{code}`.

설계 (oracle T25 자문 P0+P1):
- AsOfPolicy dependency 통합 — `as_of` query + X-AsOf-* header
- Pydantic v2 strict schemas (`from_domain` factory)
- StockStatus enum — 200 + status (미상장/폐지 구별)
- lineage-aware fetch_by_code (code_history 시점 매칭)
- FactorEvaluator + FieldProvider 통합 — Stock Detail 지표 카드
- normalize_single_code helper — `5930` → `005930` zero-pad
- N/A factor 의 wire 표현 (is_na + na_reason)

M1-A T50 — FieldProvider 실연결. M0 의 `_build_factor_stubs` placeholder 를 제거
하고, 종목별 `DbFieldProvider` (price / financial / corporate_action repository
주입) 를 만들어 `FactorEvaluator.evaluate(factor, provider, as_of=...)` 로 실제
factor 산출값을 반환. 해소 못 하는 field (M0 미보유: shares / market_cap /
dividend / trading_value_20d_avg) 는 evaluator 가 정식 N/A (`missing_input:*`).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Final

from fastapi import APIRouter, HTTPException, Path, Query

from app.adapters.base import AdapterError
from app.api.dependencies import BrowseAsOfDep
from app.api.dependencies.repositories import (
    ActivePackDep,
    BatchRunRepoDep,
    CitationRepoDep,
    CorpCodeMappingDep,
    CorporateActionRepoDep,
    DartAdapterDep,
    DividendRepoDep,
    FactExtractorDep,
    FactorEvaluatorDep,
    FinancialRepoDep,
    MacroIndicatorRepoDep,
    MarketCapRepoDep,
    PriceRepoDep,
    PykrxAdapterOptionalDep,
    StocksRepoDep,
    TreasurySharesRepoDep,
)
from app.repositories.pit_protocols import (
    CorporateActionRepository,
    DividendRepository,
    FinancialRepository,
    MacroIndicatorRepository,
    MarketCapRepository,
    PriceRepository,
    StockMasterRecord,
    TreasurySharesRepository,
)
from app.schemas.disclosures import DisclosureItemOut, DisclosuresOut
from app.schemas.fact_extraction import (
    ExtractFactsRequest,
    FactExtractionResultOut,
)
from app.schemas.stocks import (
    DEFAULT_DISPLAY_FACTORS,
    CorporateActionOut,
    FactorValueOut,
    FinancialHistoryOut,
    FinancialHistoryVintageOut,
    FinancialSeriesItemOut,
    FinancialSeriesOut,
    StockCompareOut,
    StockDetailOut,
    StockPriceBarOut,
    StockPricesOut,
    StockSearchPageOut,
    StockSummaryOut,
    StockTotalReturnOut,
    StockTotalReturnPointOut,
)
from app.services.db_field_provider import DbFieldProvider
from app.services.disclosure_fact_extraction import (
    FactExtractionBlocked,
    extract_disclosure_facts,
)
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import LoadedPack
from app.services.krx_calendar import kst_today
from app.services.lazy_price_fetch import ensure_prices_cached
from app.services.price_adjuster import (
    _DECIMAL_PRECISION,
    AdjusterError,
)
from app.services.total_return_adjuster import TotalReturnAdjuster

logger = logging.getLogger(__name__)

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
    as_of: BrowseAsOfDep,
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
    as_of: BrowseAsOfDep,
    repo: StocksRepoDep,
    evaluator: FactorEvaluatorDep,
    pack: ActivePackDep,
    price_repo: PriceRepoDep,
    financial_repo: FinancialRepoDep,
    corporate_action_repo: CorporateActionRepoDep,
    market_cap_repo: MarketCapRepoDep,
    treasury_repo: TreasurySharesRepoDep,
    macro_repo: MacroIndicatorRepoDep,
    dividend_repo: DividendRepoDep,
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
    #    M1 T50 — 종목별 DbFieldProvider 로 per-stock factor 실평가 (stub 제거).
    items: list[StockDetailOut] = []
    not_found: list[str] = []
    for code in normalized:
        master = repo.fetch_by_code(code, as_of=as_of.value)
        if master is None:
            not_found.append(code)
            continue
        factor_values = _evaluate_display_factors(
            master, pack, evaluator,
            price_repo=price_repo,
            financial_repo=financial_repo,
            corporate_action_repo=corporate_action_repo,
            market_cap_repo=market_cap_repo,
            treasury_repo=treasury_repo,
            macro_repo=macro_repo,
            dividend_repo=dividend_repo,
            as_of=as_of.value,
        )
        items.append(StockDetailOut.from_master_and_factors(
            master, factor_values, as_of=as_of.value,
        ))

    # not_found 도 입력 순서 보존 — items 와 일관 (oracle T27 #5). 사용자가
    # 입력한 순서대로 "찾지 못한 코드" 가 표시되어 UI 가 자연.
    return StockCompareOut(
        items=tuple(items),
        not_found=tuple(not_found),
    )


@router.get("/{code}", response_model=StockDetailOut)
async def get_stock_detail(
    as_of: BrowseAsOfDep,
    repo: StocksRepoDep,
    evaluator: FactorEvaluatorDep,
    pack: ActivePackDep,
    price_repo: PriceRepoDep,
    financial_repo: FinancialRepoDep,
    corporate_action_repo: CorporateActionRepoDep,
    market_cap_repo: MarketCapRepoDep,
    treasury_repo: TreasurySharesRepoDep,
    macro_repo: MacroIndicatorRepoDep,
    dividend_repo: DividendRepoDep,
    code: str = Path(pattern=_CODE_PATH_REGEX),
) -> StockDetailOut:
    """Stock Detail — AC-F-04 의 지표 카드 + 메타데이터.

    Status 분기 (oracle 결정 6):
        - lineage 부재 → 404
        - lineage 존재 + as_of < listing_date → 200 + status="not_yet_listed"
        - lineage 존재 + delisting_date <= as_of → 200 + status="delisted"
        - 그 외 → 200 + status="active"

    M1 T50 — DbFieldProvider 로 default factor 실평가. 해소 못 하는 field 는
    evaluator 가 정식 N/A (`missing_input:*`).
    """
    normalized_code = _normalize_single_code(code)
    master = repo.fetch_by_code(normalized_code, as_of=as_of.value)
    if master is None:
        raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")

    factor_values = _evaluate_display_factors(
        master, pack, evaluator,
        price_repo=price_repo,
        financial_repo=financial_repo,
        corporate_action_repo=corporate_action_repo,
        market_cap_repo=market_cap_repo,
        treasury_repo=treasury_repo,
        macro_repo=macro_repo,
        dividend_repo=dividend_repo,
        as_of=as_of.value,
    )

    return StockDetailOut.from_master_and_factors(
        master, factor_values, as_of=as_of.value,
    )


# 가격 차트의 default lookback — as_of 기준 직전 N 일(캘린더 일). 일봉이라 ~1 년.
_PRICES_DEFAULT_DAYS: Final[int] = 365

# 재무 시계열 표시 대상 account 목록 — (canonical_id, 표시명, ifrs_type) 순서.
# 연결(consolidated) 우선: 단일 ifrs_type 지정으로 연결/별도 혼재 방지(ADR-0005).
# account 선정 근거:
#   - revenue, operating_income, net_income_attributable_to_owners:
#       손익계산서 핵심 3 행 (매출→영업→지배주주순이익 waterfall).
#   - equity_attributable_to_owners: ROE 분모·PBR 기준 자본.
#   - total_assets: 레버리지·자산규모 맥락.
#   - basic_eps: 주당 지표 (EPS 시계열 = 이익 성장 한 눈에).
_FINANCIAL_SERIES: Final[tuple[tuple[str, str, str], ...]] = (
    ("revenue",                              "매출액",         "consolidated"),
    ("operating_income",                     "영업이익",       "consolidated"),
    ("net_income_attributable_to_owners",    "지배주주순이익", "consolidated"),
    ("equity_attributable_to_owners",        "지배주주자본",   "consolidated"),
    ("total_assets",                         "자산총계",       "consolidated"),
    ("basic_eps",                            "기본 EPS",       "consolidated"),
)

# 재무 시계열 조회 기본 분기 수 — 최대 8 분기(약 2 년 분기 + 연간 혼합).
_FINANCIAL_MAX_PERIODS: Final[int] = 8

# 공시목록 조회 기본 lookback — as_of 기준 직전 N 일 (캘린더). DART list.json
# 의 bgn_de 산출. 12 개월 (365 일) — 최근 1 년 공시를 충분히 포괄하되 과도한
# rate-limit 노출 회피 (ADR-0026 D3 — on-demand 1 종목 한정).
_DISCLOSURE_DEFAULT_LOOKBACK_DAYS: Final[int] = 365

# 공시목록 응답 default / 최대 항목 수.
_DISCLOSURE_DEFAULT_LIMIT: Final[int] = 30
_DISCLOSURE_MAX_LIMIT: Final[int] = 100


@router.get("/{code}/prices", response_model=StockPricesOut)
async def get_stock_prices(
    price_repo: PriceRepoDep,
    corporate_action_repo: CorporateActionRepoDep,
    citation_repo: CitationRepoDep,
    batch_run_repo: BatchRunRepoDep,
    pykrx_adapter: PykrxAdapterOptionalDep,
    code: str = Path(pattern=_CODE_PATH_REGEX),
    days: int = Query(_PRICES_DEFAULT_DAYS, ge=1, le=3650),
    as_of: date | None = Query(  # noqa: B008
        None,
        description=(
            "조회 기준일 (KST naive date, 예: '2026-06-20'). None 이면 KST 오늘. "
            "미래 일자는 거부(400). 가격 차트 표시 전용 — 캘린더 cap·스냅 없음."
        ),
    ),
) -> StockPricesOut:
    """가격 시계열 — Stock Detail 가격 차트(lightweight-charts)의 backend.

    `[as_of - days, as_of]` 범위의 일봉(effective_date asc). PriceRepository 가
    `effective_date <= as_of` PIT 강제. raw OHLC + close_adjusted(ADR-0001) 노출 —
    frontend 가 raw/adjusted 토글. 데이터 없으면 빈 bars(200) — 차트가 "데이터
    없음" 표시. (지표 카드와 달리 lineage 404 검사 없음 — 가격 시계열 자체 조회.)

    on-demand lazy fetch (차트 전용):
        요청 구간 주가가 DB 에 없으면 KRX(pykrx, keyless)에서 즉석으로 당겨와
        prices_daily 에 캐시한 뒤 반환한다(`ensure_prices_cached`). 데이터가 과거
        시점에 멈춰 현재 주가를 못 보던 문제 해소. **스크리너/재현성과 무관** —
        본 endpoint 는 live observation 이라 frozen cutoff 경로 밖이다. lazy fetch
        는 `app.state.pykrx_adapter` 설정 운영 app 에서만 활성(테스트는 미설정).
        실패해도 차트 조회를 막지 않음(DB 있는 것만으로 degrade).

    as_of:
        raw date Query(캘린더 cap·스냅 제거 — 2025~2026 등 현재 일자 허용). None
        이면 KST 오늘. 미래 일자는 400. (financials/history 의 raw date 패턴 일관.)

    actions: [start, as_of] 범위 내 corporate action — 차트 ▾ 마커용.
        No Advice: action_type/effective_date/ratio 같은 시장 사실만 노출 (T59 gate).
        PIT: CorporateActionRepository 가 announced_date<=as_of active chain 반환.
        effective_date 가 prices 범위(start~as_of) 밖이면 마커 표시 대상 외라 제외.
    """
    normalized = _normalize_single_code(code)
    today = kst_today()
    effective_as_of = as_of if as_of is not None else today
    if effective_as_of > today:
        raise HTTPException(
            status_code=400, detail="미래 일자는 조회할 수 없습니다.",
        )
    start = effective_as_of - timedelta(days=days)

    # on-demand lazy fetch — DB 갭을 KRX 에서 즉석 충전·캐시(차트 전용). pykrx
    # adapter 미설정(테스트·키 불필요)이면 skip. 실패가 차트 조회를 막지 않도록
    # broad-except degrade(DB 에 있는 것만 반환).
    if pykrx_adapter is not None:
        try:
            ensure_prices_cached(
                normalized,
                start=start,
                end=effective_as_of,
                price_repo=price_repo,
                citation_repo=citation_repo,
                krx_adapter=pykrx_adapter,
                batch_run_repo=batch_run_repo,
            )
        except Exception:  # noqa: BLE001 — lazy fetch 실패 ≠ 차트 실패(degrade).
            logger.warning(
                "lazy price fetch 실패 code=%s start=%s end=%s",
                normalized, start, effective_as_of,
            )

    records = price_repo.fetch_prices(
        normalized, as_of=effective_as_of, start=start,
    )
    bars = tuple(
        StockPriceBarOut(
            date=r.effective_date,
            open=str(r.open_raw),
            high=str(r.high_raw),
            low=str(r.low_raw),
            close=str(r.close_raw),
            close_adjusted=str(r.close_adjusted),
            volume=r.volume,
        )
        for r in records
    )
    # corporate action — announced_date<=as_of active chain 중 effective_date 가
    # 가격 범위(start~as_of) 내인 것만 마커 대상. 범위 밖(미래·너무 과거) 제외.
    raw_actions = corporate_action_repo.fetch_actions(
        normalized, as_of=effective_as_of,
    )
    actions = tuple(
        CorporateActionOut(
            effective_date=a.effective_date,
            action_type=a.action_type,
            ratio=(str(a.ratio) if a.ratio is not None else None),
        )
        for a in raw_actions
        if start <= a.effective_date <= effective_as_of
    )
    return StockPricesOut(
        code=normalized, as_of=effective_as_of, bars=bars, actions=actions,
    )


@router.get("/{code}/total-return", response_model=StockTotalReturnOut)
async def get_stock_total_return(
    as_of: BrowseAsOfDep,
    price_repo: PriceRepoDep,
    corporate_action_repo: CorporateActionRepoDep,
    dividend_repo: DividendRepoDep,
    code: str = Path(pattern=_CODE_PATH_REGEX),
    days: int = Query(_PRICES_DEFAULT_DAYS, ge=1, le=3650),
) -> StockTotalReturnOut:
    """세전 Total Return 시계열 — 가격 차트 "배당재투자(Total Return)" 토글 backend.

    `[as_of - days, as_of]` 범위의 raw 종가를 PriceAdjuster 로 as_of 보정한 뒤
    TotalReturnAdjuster 로 배당 재투자를 누적한 total return index(TRI)를 산출,
    첫 거래일 보정 종가 기준으로 rebase 한 표시용 line point 를 반환한다.
    `db_field_provider._resolve_total_return_trailing_1y` 와 **동일 산출 경로**
    (PriceAdjuster.adjust → TotalReturnAdjuster.compute). 본 endpoint 는 live
    observation(`/prices` 동형) 으로 reproduce 진입점이 아니라 `batch_cutoff` 를
    쓰지 않는다 — live serving(둘 다 cutoff None) 에서 factor 카드
    (`price-return:total-annual`) 와 차트 라인이 같은 보정 basis 를 공유한다(frozen
    run reproduce 는 본 endpoint 경로 밖이라 비대칭 무관).

    `/prices` 와의 basis 차이(주의):
        `/prices` 의 close_adjusted 는 repo 저장값(T20 보정 일배치 미적용 → 현재
        raw 동일)이라 본 endpoint 의 read-time PIT 보정 종가와 다를 수 있다(분할
        이력 종목). 본 endpoint 는 PIT 정합 보정을 쓰므로 corporate action 이 있는
        종목에서 더 정확하다. 배당·corporate action 이 모두 없는 종목은 세 값이
        수치적으로 일치(value == close_adjusted == raw).

    PIT (§2.4):
        PriceRepository(effective_date <= as_of) + CorporateActionRepository
        (announced_date <= as_of active chain) + DividendRepository(이중 PIT:
        announced<=as_of AND effective<=as_of) 가 강제. look-ahead 0.

    데이터 없음(빈 prices) → points=() (200) — 차트가 "데이터 없음" 표시.
    보정 invariant 위반(미지원 action_type, close_adjusted/close_raw 0 division 등
        AdjusterError 계열) → points=() + warnings 1줄(fail-soft — 페이지 보존,
        factor N/A 처리와 동일 의미론). 외부 출처 장애가 아니라 데이터 결함이므로
        502 아님.

    세전 (ADR-0035 D7): 배당소득세·양도세 미반영. 응답엔 disclosure 텍스트 없음 —
        client PreTaxDisclosure 가 total-return 모드에서 중립 고지.
    """
    normalized = _normalize_single_code(code)
    start = as_of.value - timedelta(days=days)
    prices = price_repo.fetch_prices(normalized, as_of=as_of.value, start=start)
    if not prices:
        # 가격 결손 — 빈 시계열 (200). 차트가 "데이터 없음" overlay.
        return StockTotalReturnOut(
            code=normalized, as_of=as_of.value, points=(), warnings=(),
        )

    # 가격 보정용 corporate action (split/bonus/rights_issue 등) + 재투자 배당.
    # cash_dividend 는 actions 에 포함돼도 PriceAdjuster 에서 ignore(가격 미보정)
    # 이고, 재투자는 오직 dividends 리스트만 소비 — 이중 계산 없음
    # (db_field_provider._resolve_total_return_trailing_1y 동형).
    actions = tuple(
        corporate_action_repo.fetch_actions(normalized, as_of=as_of.value)
    )
    dividends = tuple(
        dividend_repo.fetch_dividends(normalized, as_of=as_of.value)
    )

    adjuster = TotalReturnAdjuster()
    try:
        series = adjuster.adjust(
            prices, actions, dividends, as_of=as_of.value,
        )
    except AdjusterError as exc:
        # 보정 / total return invariant 위반 — PIT 정합 산출 불가, fail-soft.
        # §2.1: 조용히 빈 결과로 숨기지 않고 사유를 warning 으로 노출.
        return StockTotalReturnOut(
            code=normalized,
            as_of=as_of.value,
            points=(),
            warnings=(
                f"total return 산출 실패 — 가격 보정 invariant 위반: {exc}",
            ),
        )

    if not series.series:
        return StockTotalReturnOut(
            code=normalized,
            as_of=as_of.value,
            points=(),
            warnings=series.warnings,
        )

    # rebase — 첫 거래일 보정 종가 기준 누적 수준(won). TRI[0]==1.0 이라
    # value[0] == base_close. 엔진과 동일 Decimal 상수(prec=28, ROUND_HALF_EVEN)로
    # 결정적 산출(§2.10 — 같은 입력 두 번이면 byte-동일 wire str).
    base_close = series.series[0].close_adjusted
    # base_close == 0 이면 모든 value 가 0 으로 flatten — 상장 종목 보정 종가가 0 이
    # 될 수는 없으나(compute 의 0-division guard 가 ≥2 record 면 먼저 raise) 단일
    # record edge 까지 §2.1 조용한 손실 금지: 0 으로 평탄화하지 않고 사유 고지.
    if base_close == 0:
        return StockTotalReturnOut(
            code=normalized,
            as_of=as_of.value,
            points=(),
            warnings=(
                *series.warnings,
                f"base close_adjusted 가 0 ({series.series[0].date.isoformat()}) — "
                f"total return rebase 불가",
            ),
        )
    points: list[StockTotalReturnPointOut] = []
    with localcontext() as ctx:
        ctx.prec = _DECIMAL_PRECISION
        ctx.rounding = ROUND_HALF_EVEN
        for rec in series.series:
            value: Decimal = base_close * rec.total_return_index
            points.append(
                StockTotalReturnPointOut(
                    date=rec.date,
                    value=str(value),
                    index=str(rec.total_return_index),
                )
            )

    return StockTotalReturnOut(
        code=normalized,
        as_of=as_of.value,
        points=tuple(points),
        warnings=series.warnings,
    )


@router.get("/{code}/financials", response_model=FinancialSeriesOut)
async def get_stock_financials(
    as_of: BrowseAsOfDep,
    financial_repo: FinancialRepoDep,
    code: str = Path(pattern=_CODE_PATH_REGEX),
) -> FinancialSeriesOut:
    """재무 시계열 — Stock Detail 재무 탭 표 렌더링 backend (AC-F-04).

    `_FINANCIAL_SERIES` 의 각 account 를 `fetch_financials(as_of=as_of)` 로 조회.
    PIT: FinancialRepository 가 effective_date<=as_of 기준 latest active chain 반환.
    look-ahead 0 보장 — as_of 이후 공시된 분기값 제외.

    periods: 모든 account 의 fiscal_period 합집합을 오름차순 정렬.
    values: periods 와 동일 순서 — 결손 분기는 None (표가 빈 셀 처리).
    적재 데이터 없는 account 는 items 에서 제외 (빈 행 표시 X).
    데이터 전혀 없으면 periods=() / items=() 로 200 반환.
    """
    normalized = _normalize_single_code(code)

    # account 별 조회 결과 수집 — (records, 표시명, unit) 보존.
    # 적재 없는 account 는 records=() → 이후 items 필터로 제외.
    fetched: list[tuple[str, str, str, list[tuple[str, str]]]] = []
    # fetched element: (account, name, unit_placeholder, [(fiscal_period, value_str)])

    all_periods: set[str] = set()
    for account, name, ifrs_type in _FINANCIAL_SERIES:
        records = financial_repo.fetch_financials(
            normalized,
            as_of=as_of.value,
            account=account,
            ifrs_type=ifrs_type,
            max_periods=_FINANCIAL_MAX_PERIODS,
        )
        if not records:
            # 적재 없음 — account 제외 표시자로 빈 list 보존.
            fetched.append((account, name, "", []))
            continue
        # unit 은 첫 번째 record 에서 읽음 — 같은 account 는 unit 동일 (schema 설계).
        unit = records[0].unit
        pairs = [(r.fiscal_period, str(r.value)) for r in records]
        for fp, _ in pairs:
            all_periods.add(fp)
        fetched.append((account, name, unit, pairs))

    # periods 합집합 오름차순 정렬 — DART 표준 포맷 `YYYYQn` 은 사전식 = 시간순.
    sorted_periods = tuple(sorted(all_periods))

    # items 구성 — 적재 없는 account(빈 pairs) 는 제외.
    period_index = {fp: idx for idx, fp in enumerate(sorted_periods)}
    items: list[FinancialSeriesItemOut] = []
    for account, name, unit, pairs in fetched:
        if not pairs:
            continue  # 적재 없음 → 행 제외
        values: list[str | None] = [None] * len(sorted_periods)
        for fp, val_str in pairs:
            idx = period_index.get(fp)
            if idx is not None:
                values[idx] = val_str
        items.append(FinancialSeriesItemOut(
            account=account,
            name=name,
            unit=unit,
            values=tuple(values),
        ))

    return FinancialSeriesOut(
        code=normalized,
        as_of=as_of.value,
        periods=sorted_periods,
        items=tuple(items),
    )


@router.get("/{code}/financials/history", response_model=FinancialHistoryOut)
async def get_stock_financials_history(
    financial_repo: FinancialRepoDep,
    code: str = Path(pattern=_CODE_PATH_REGEX),
    fiscal_period: str | None = Query(
        None,
        description=(
            "분기 필터 — DART 표준 형식 (예: '2024Q1'). "
            "None 이면 전 분기 이력."
        ),
    ),
    as_of: date | None = Query(  # noqa: B008
        None,
        description=(
            "PIT look-ahead 차단 기준일 (§2.4). 지정하면 effective_date <= as_of "
            "인 vintage 만 반환 — 그 시점까지 공시된 정정만. "
            "None 이면 필터 없음(현재까지 알려진 모든 정정)."
        ),
    ),
) -> FinancialHistoryOut:
    """정정공시 history view — AC-M2-F-07.

    해당 종목 재무의 effective_date supersede chain(정정 이력)을 read-only 노출.
    active(현재 유효) + superseded(정정된) vintage 를 모두 반환하여 정정 chain 을
    시간순으로 확인 가능.

    PIT look-ahead 차단 (§2.4):
        as_of 지정 시 effective_date <= as_of 인 vintage 만 포함. 그 시점 이후
        공시된 정정(미래 정보)은 제외. None 이면 필터 없음(전체 이력 조회).

    No Advice (§2.2):
        effective_date / value / is_active / superseded_by 같은 관측 사실만 노출.
        "정정으로 개선/악화" 판단·라벨 없음. 순수 관측 데이터.

    vintage_seq:
        fiscal_period 내 정정 회차 (1-based). 원본=1, 1차 정정=2, 2차 정정=3…
        프론트 Phase2 가 정정 chain 시각화에 사용.

    read-only (ADR-0020):
        이 endpoint 는 SELECT 만 수행. 어떠한 DB 상태 변경도 없음. append-only
        불변식(ADR-0020) 위반 0.

    미존재 code 또는 데이터 없음: vintages=() 로 200 반환.
    """
    normalized = _normalize_single_code(code)
    # read-only fetch — SELECT 만 (ADR-0020 불변). active + superseded 전부.
    records = financial_repo.fetch_restatement_history(
        normalized, fiscal_period=fiscal_period, as_of=as_of,
    )
    # fiscal_period 내 정정 회차(vintage_seq) 산출 — effective_date asc 정렬 후
    # 같은 fiscal_period + account 조합의 순번. 정렬은 repo 가 보장.
    # vintage_seq: 같은 (fiscal_period, account) 그룹 내 1-based 순번.
    seq_counter: dict[tuple[str, str], int] = {}
    vintages: list[FinancialHistoryVintageOut] = []
    for rec in records:
        key = (rec.fiscal_period, rec.account)
        seq_counter[key] = seq_counter.get(key, 0) + 1
        vintages.append(
            FinancialHistoryVintageOut.from_record(
                rec, vintage_seq=seq_counter[key],
            )
        )
    return FinancialHistoryOut(
        code=normalized,
        as_of=as_of,
        fiscal_period_filter=fiscal_period,
        vintages=tuple(vintages),
    )


@router.get("/{code}/disclosures", response_model=DisclosuresOut)
async def get_stock_disclosures(
    as_of: BrowseAsOfDep,
    adapter: DartAdapterDep,
    corp_mapping: CorpCodeMappingDep,
    code: str = Path(pattern=_CODE_PATH_REGEX),
    limit: int = Query(
        _DISCLOSURE_DEFAULT_LIMIT,
        ge=1,
        le=_DISCLOSURE_MAX_LIMIT,
        description="반환할 최신 공시 개수 (1~100).",
    ),
) -> DisclosuresOut:
    """공시 metadata 목록 — Stock Detail 의 공시 패널 backend (ADR-0026).

    ADR-0026 의 §2.7 예외 첫 구현 — 사용자가 진입한 1 종목의 on-demand 공시목록.
    제목·접수일자·DART 원문링크 3 필드만 (본문/요약/자체 분류라벨/"중요도" 0).

    흐름:
        1. code → corp_code 매핑 (CorpCodeMapping). 미발견 → 404.
        2. adapter.fetch_disclosure_list(기간: as_of 기준 직전 1 년 ~ as_of).
        3. PIT 필터 (ADR-0026 D4) — rcept_date <= as_of 인 공시만 (look-ahead 0).
           adapter 가 end_de=as_of 로 조회하나, DART 의 rcept_dt 경계/응답 신뢰
           대신 route 가 명시 재확인 (이중 안전).
        4. limit 적용 (adapter 가 최신순 정렬 보장 — 앞에서 N 개).

    No Advice / §2.7:
        report_name 은 DART 원문 사실 (EXTERNAL_QUOTE) — forbidden_words 검사
        제외. Speculum 이 생성하는 라벨·배지·요약 0. dart_url 클릭 시 DART 페이지
        이탈 (본문 텍스트 자체 표시 X).

    DART 장애 (AdapterError / AdapterRetryError) — 502 로 변환. 공시 부재
    (status "013") 은 adapter 가 빈 결과로 처리 (200 + disclosures=()).
    """
    normalized = _normalize_single_code(code)
    corp_code = corp_mapping.to_corp_code(normalized)
    if corp_code is None:
        # corp_code 매핑 부재 — 종목 자체는 존재할 수 있으나 DART 조회 키 없음.
        raise HTTPException(
            status_code=404,
            detail="해당 종목의 DART corp_code 매핑을 찾을 수 없습니다.",
        )

    # 조회 기간 — as_of 기준 직전 lookback ~ as_of (YYYYMMDD). end_de=as_of 로
    # DART 단에서 1 차 미래 공시 차단, route 가 PIT 재확인 (이중 안전).
    bgn = as_of.value - timedelta(days=_DISCLOSURE_DEFAULT_LOOKBACK_DAYS)
    bgn_de = bgn.strftime("%Y%m%d")
    end_de = as_of.value.strftime("%Y%m%d")

    try:
        result = adapter.fetch_disclosure_list(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
        )
    except AdapterError as exc:
        # DART 장애 / API key 미설정 / rate-limit (AdapterRetryError 도 AdapterError
        # 하위) — 502 Bad Gateway (외부 출처 장애). 본문 노출 없이 일반 메시지.
        raise HTTPException(
            status_code=502,
            detail="DART 공시 조회에 실패했습니다. 잠시 후 다시 시도하세요.",
        ) from exc

    # PIT 필터 (ADR-0026 D4) — rcept_date <= as_of. adapter 가 최신순 정렬 보장.
    pit_items = [
        item for item in result.data if item.rcept_date <= as_of.value
    ]
    disclosures = tuple(
        DisclosureItemOut.from_item(item) for item in pit_items[:limit]
    )
    return DisclosuresOut(
        code=normalized,
        as_of=as_of.value,
        disclosures=disclosures,
    )


@router.post(
    "/{code}/disclosures/extract-facts",
    response_model=FactExtractionResultOut,
)
async def extract_disclosure_facts_route(
    body: ExtractFactsRequest,
    extractor: FactExtractorDep,
    code: str = Path(pattern=_CODE_PATH_REGEX),
) -> FactExtractionResultOut:
    """AI 공시 사실추출 — 사용자 명시 1 공시 on-demand (ADR-0031).

    CONCEPT §2.7 경계를 처음 넘는 결정 — 따라서 가장 무거운 게이트로 방어한다.
    "요약" 이 아니라 고정 스키마의 **구조화 사실**(공시유형·금액·일자·당사자·수량)만
    추출하고(D1), 추출된 모든 텍스트를 forbidden_words SYSTEM scope 로 검사해
    fail-closed 차단한다(D2).

    흐름:
        1. extractor 미주입(None) → 503. 운영 LLM 미연동이 **정상 상태**(release
           blocker, D6). 테스트/개발은 FakeLlmFactExtractor 주입으로 동작.
        2. extract_disclosure_facts — extractor.extract_facts → 출력 게이트(SYSTEM
           scope) → 출처(rcept_no + DART URL) + disclaimer_required=True 첨부.
        3. 게이트 검출 시 FactExtractionBlocked → 사실 미반환 + 사유(422). 부분
           노출조차 막기 위해 결과 조립 전 차단(fail-closed).

    No Advice / §2.7:
        응답에 전망/요약/추천/평가 필드 0 (D1/D5 — 스키마에 슬롯 부재). 자동 추출·
        feed·일괄 처리 0 (D4 — body 의 단일 rcept_no 만). 추출 사실은 DART 원문
        대조 가능 필드만 + 디스클레이머 강제(D3).

    code path param 은 disclosures route 와 동형(zero-pad 정규화). 사실추출은
    rcept_no(body)로 1 공시 식별 — code 는 경로 일관성 + 향후 corp 검증 hook 용.
    """
    _normalize_single_code(code)

    # extractor 미주입 — 운영 LLM 미연동이 정상 상태(release blocker, D6). 503 으로
    # "현재 미제공" 을 명시(500 내부오류 아님). 본문 노출 없는 일반 메시지.
    if extractor is None:
        raise HTTPException(
            status_code=503,
            detail="공시 사실추출 기능은 현재 제공되지 않습니다.",
        )

    try:
        result = extract_disclosure_facts(
            extractor,
            rcept_no=body.rcept_no,
            disclosure_title=body.disclosure_title,
        )
    except FactExtractionBlocked as exc:
        # 출력 게이트 fail-closed(D2) — 사실 미반환 + 사유. 금지어휘 echo 없는
        # 일반 메시지(forbidden_words B4 — 응답 본문 어휘 노출 차단). 422 =
        # 추출 결과가 정책상 반환 불가(요청 자체는 valid).
        raise HTTPException(
            status_code=422,
            detail="추출된 사실이 표시 정책을 위반하여 차단되었습니다.",
        ) from exc

    return FactExtractionResultOut.from_result(result)


def _evaluate_display_factors(
    master: StockMasterRecord,
    pack: LoadedPack,
    evaluator: FactorEvaluator,
    *,
    price_repo: PriceRepository,
    financial_repo: FinancialRepository,
    corporate_action_repo: CorporateActionRepository,
    market_cap_repo: MarketCapRepository,
    treasury_repo: TreasurySharesRepository,
    macro_repo: MacroIndicatorRepository | None = None,
    dividend_repo: DividendRepository | None = None,
    as_of: date,
) -> tuple[FactorValueOut, ...]:
    """DEFAULT_DISPLAY_FACTORS 를 종목별 DbFieldProvider 로 실평가 (M1 T50).

    M0 의 `_build_factor_stubs` 를 대체. 종목 (lineage) 별로 하나의
    DbFieldProvider 를 만들고 (request-scoped 캐싱), 각 factor 의 formula AST 를
    `FactorEvaluator.evaluate(as_of=...)` 로 산출. 해소 가능 field 는 실값,
    M0 미보유 field 는 evaluator 가 정식 N/A (`missing_input:<field>`).

    PIT — provider 가 모든 fetch 를 `as_of` 기준 active 로 제한 (financial
    supersede chain + price `effective_date <= as_of`). evaluator 가
    `provider.as_of == as_of` invariant 강제.

    종목코드 — lineage 의 현재 활성 코드 (`current_code`) 우선. 폐지 lineage
    (current_code None) 는 fact 조회 키가 없어 모든 factor N/A (provider 가 빈
    결과 → missing_input). UI 는 status=delisted 와 함께 N/A 표시.
    """
    provider_code = master.current_code or ""
    provider = DbFieldProvider(
        code=provider_code,
        as_of=as_of,
        price_repo=price_repo,
        financial_repo=financial_repo,
        corporate_action_repo=corporate_action_repo,
        market_cap_repo=market_cap_repo,
        treasury_repo=treasury_repo,
        macro_repo=macro_repo,
        # M7 #4 — dividend_per_share_trailing_annual / total_return_trailing_1y
        # 실연결. dividend_repo 미주입(None) 시 두 field 정식 N/A(하위호환).
        # fix5 both-or-neither: dividend_repo None 이면 adjuster 도 None
        # (DbFieldProvider assert 와 일관). 라우트는 항상 repo 주입.
        dividend_repo=dividend_repo,
        total_return_adjuster=(
            TotalReturnAdjuster() if dividend_repo is not None else None
        ),
        # 파생 필드 (market_cap_ex_treasury) 해소 — 대응 factor 를 동일 evaluator
        # 로 평가 (Option B). provider 하드코딩 composite 금지 (Fidelity 단일
        # 출처). pack body 의 factors 리스트에서 target_factor 조회.
        factor_pack=pack,
        evaluator=evaluator,
    )

    factors_by_id = {f["canonical_id"]: f for f in pack.body["factors"]}
    results: list[FactorValueOut] = []
    for canonical_id in DEFAULT_DISPLAY_FACTORS:
        f = factors_by_id.get(canonical_id)
        if f is None:
            continue  # 빌트인에서 누락 시 skip
        result = evaluator.evaluate(f, provider, as_of=as_of)
        results.append(FactorValueOut.from_evaluation(
            result, factor_name=f["name"], factor_unit=f["unit"],
        ))
    return tuple(results)
