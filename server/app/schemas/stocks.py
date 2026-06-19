"""Stocks endpoint wire schemas — `/api/stocks/{code}` + `/api/stocks/search`.

설계 (oracle T25 자문 P0):
- Pydantic v2 strict + extra="forbid" + frozen=True
- Decimal → str wire (JSON number drift 회피, Risk-C4)
- `from_domain(domain) -> SchemaOut` factory (단방향)
- StockStatus enum (결정 6 — 404 vs 200 분리)
- multi-id ambiguous indicators 노출 (AC-P-08) — `_DEFAULT_DISPLAY_FACTORS`
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.repositories.pit_protocols import FinancialRecord, StockMasterRecord
from app.services.factor_evaluator import EvaluationResult

__all__ = [
    "DEFAULT_DISPLAY_FACTORS",
    "CodeHistoryItemOut",
    "CorporateActionOut",
    "FactorValueOut",
    "FinancialHistoryOut",
    "FinancialHistoryVintageOut",
    "FinancialSeriesItemOut",
    "FinancialSeriesOut",
    "StockCompareOut",
    "StockDetailOut",
    "StockPriceBarOut",
    "StockPricesOut",
    "StockSearchPageOut",
    "StockStatus",
    "StockSummaryOut",
]

# 본 사이클의 Stock Detail 지표 카드 default factor list (oracle 결정 4).
# multi-id ambiguous (PER 의 여러 정의) 의 1 차 selection — primary 정의만.
# 사용자가 `?include_alternates=true` 또는 별도 endpoint 로 전체 보기 가능 (별도 사이클).
DEFAULT_DISPLAY_FACTORS: Final[tuple[str, ...]] = (
    "market-cap:ex-treasury",
    "per:ttm-consolidated-ifrs",
    "pbr:consolidated-ifrs",
    "roe:ttm-avg-equity-consolidated-ifrs",
    "eps:basic-ttm-consolidated-ifrs",
    "dividend-yield:trailing-annual",
    "price-return:total-annual",
)

_STRICT_MODEL_CONFIG = ConfigDict(strict=True, extra="forbid", frozen=True)


class StockStatus(str, Enum):
    """as_of 시점의 종목 상태 — endpoint 가 200 + status 로 분기 (oracle 결정 6).

    경계 의미 (oracle 2 차 C1 — 반열린 구간):
        `active`: listing_date <= as_of AND (delisting_date is None OR delisting_date > as_of).
            즉 상장 첫날 = active, 폐지 당일 = delisted.
        `not_yet_listed`: as_of < listing_date. lineage 는 존재.
        `delisted`: delisting_date <= as_of. 시계열 보존 (ADR-0009 D7).

    KRX 의미: 정리매매 마지막 영업일이 delisting_date — 그 날까지 거래 가능.
    그러나 본 정책은 PIT-strict — delisting_date 당일은 거래 종료 후 freeze
    시점으로 간주 → DELISTED 분류.
    """

    ACTIVE = "active"
    NOT_YET_LISTED = "not_yet_listed"
    DELISTED = "delisted"


class CodeHistoryItemOut(BaseModel):
    """code_history JSONB 의 단일 entry — wire."""

    model_config = _STRICT_MODEL_CONFIG

    code: str
    valid_from: date
    valid_to: date | None
    reason: str


class StockSummaryOut(BaseModel):
    """검색 결과 / list view 용 요약 — Stock Detail 외 화면."""

    model_config = _STRICT_MODEL_CONFIG

    id: UUID
    code: str
    name: str
    market: str
    listing_date: date
    delisting_date: date | None
    status: StockStatus

    @classmethod
    def from_master(
        cls, master: StockMasterRecord, *, as_of: date,
    ) -> StockSummaryOut:
        """도메인 → wire 단방향 factory.

        `current_code` 가 None (폐지) 이면 lineage 의 마지막 historical code 로
        fallback — UI 가 폐지 종목도 코드로 식별 가능 (ADR-0009 D7).
        """
        return cls(
            id=master.id,
            code=_resolve_display_code(master),
            name=master.current_name,
            market=master.market,
            listing_date=master.listing_date,
            delisting_date=master.delisting_date,
            status=_compute_status(master, as_of),
        )


class FactorValueOut(BaseModel):
    """단일 factor 산출값 — Stock Detail 의 지표 카드 1 개."""

    model_config = _STRICT_MODEL_CONFIG

    canonical_id: str
    name: str
    unit: str
    # Decimal → str wire — IEEE 754 drift 회피 (oracle Risk-C4). frontend 가
    # BigNumber/decimal.js 로 처리.
    value: str | None
    is_na: bool
    na_reason: str | None
    # Provenance (Fidelity, §2.1) — 본 사이클은 factor 정의 식별만, citation 은
    # 별도 endpoint 또는 후속 사이클 (T18 합류 후 진짜 citation_id 가 의미).
    evaluator_version: str

    @classmethod
    def from_evaluation(
        cls,
        result: EvaluationResult,
        *,
        factor_name: str,
        factor_unit: str,
    ) -> FactorValueOut:
        return cls(
            canonical_id=result.factor_canonical_id,
            name=factor_name,
            unit=factor_unit,
            value=(str(result.value) if result.value is not None else None),
            is_na=result.is_na,
            na_reason=result.na_reason,
            evaluator_version=result.evaluator_version,
        )


class StockDetailOut(BaseModel):
    """Stock Detail view 의 wire schema — 지표 카드 + 메타데이터."""

    model_config = _STRICT_MODEL_CONFIG

    id: UUID
    code: str
    name: str
    market: str
    listing_date: date
    delisting_date: date | None
    fiscal_month: int
    ifrs_preference: str
    status: StockStatus
    code_history: tuple[CodeHistoryItemOut, ...]
    factors: tuple[FactorValueOut, ...]

    @classmethod
    def from_master_and_factors(
        cls,
        master: StockMasterRecord,
        factors: tuple[FactorValueOut, ...],
        *,
        as_of: date,
    ) -> StockDetailOut:
        return cls(
            id=master.id,
            code=_resolve_display_code(master),
            name=master.current_name,
            market=master.market,
            listing_date=master.listing_date,
            delisting_date=master.delisting_date,
            fiscal_month=master.fiscal_month,
            ifrs_preference=master.ifrs_preference_default,
            status=_compute_status(master, as_of),
            code_history=tuple(
                CodeHistoryItemOut(
                    code=e.code, valid_from=e.valid_from,
                    valid_to=e.valid_to, reason=e.reason,
                )
                for e in master.code_history
            ),
            factors=factors,
        )


class StockCompareOut(BaseModel):
    """Compare view 의 wire schema — 2~6 종목 multi-fetch 결과 (T27).

    Attributes:
        items: 입력 codes 순서 (정규화·dedup 후) 의 Stock Detail. lineage 부재
            종목은 본 list 에 없음 (`not_found` 로 별도 노출). UI 의 grid 가
            본 순서대로 column 배치.
        not_found: lineage 가 존재하지 않는 입력 codes. UI 가 "코드 X 는 알 수
            없음" 표시. 정렬 — 사전식 ascending.
    """

    model_config = _STRICT_MODEL_CONFIG

    items: tuple[StockDetailOut, ...]
    not_found: tuple[str, ...]


class StockSearchPageOut(BaseModel):
    """검색 결과 페이지 — cursor-aware schema (M0 구현은 offset, oracle 결정 5).

    `next_cursor` 는 M0 = None 항상. M1+ cursor 도입 시 schema 무변경 — frontend
    의 infinite scroll 패턴이 first-write 그대로 작동.
    """

    model_config = _STRICT_MODEL_CONFIG

    items: tuple[StockSummaryOut, ...]
    total: int | None
    next_cursor: str | None


class StockPriceBarOut(BaseModel):
    """가격 차트의 단일 일봉 — `/api/stocks/{code}/prices` 의 element.

    Decimal → str wire (JSON number drift 회피, 본 모듈 설계 원칙). frontend 차트
    (lightweight-charts) 가 str → number 파싱 (차트는 financial 정밀도 불요).
    raw OHLC + `close_adjusted`(ADR-0001 보정 종가) 둘 다 노출 — frontend 가
    raw/adjusted 토글 (ADR-0001 D6).
    """

    model_config = _STRICT_MODEL_CONFIG

    date: date
    open: str
    high: str
    low: str
    close: str
    close_adjusted: str
    volume: int


class CorporateActionOut(BaseModel):
    """가격 차트 ▾ 마커용 단일 corporate action — No Advice (사실만).

    No Advice (ADR-0007 T59 gate):
        effective_date / action_type / ratio 같은 시장 사실만 노출. 매수·매도
        판단·해석 0. 프론트가 ▾ 마커로 사실을 표시하는 것에만 사용.

    PIT: announced_date <= as_of 기준으로 repo 가 active chain 반환.
    effective_date 가 prices 범위 내인 것만 응답에 포함 (endpoint 가 필터).
    """

    model_config = _STRICT_MODEL_CONFIG

    effective_date: date
    action_type: str   # "split" / "dividend" / "merger" 등 — ADR-0009 D2 enum
    ratio: str | None  # Decimal → str wire. None 이면 ratio 없음 (현금배당 등).


class StockPricesOut(BaseModel):
    """가격 시계열 응답 — `[start, as_of]` 범위의 일봉 (effective_date asc).

    PIT: 모든 bar 의 date(=effective_date) <= as_of (PriceRepository 가 강제).

    actions: effective_date 가 [start, as_of] 범위 내인 corporate action 목록.
        차트 ▾ 마커 렌더링용. 데이터 없으면 빈 tuple (200).
    """

    model_config = _STRICT_MODEL_CONFIG

    code: str
    as_of: date
    bars: tuple[StockPriceBarOut, ...]
    actions: tuple[CorporateActionOut, ...]


class StockTotalReturnPointOut(BaseModel):
    """Total Return 차트의 단일 일자 — `/api/stocks/{code}/total-return` element.

    세전 total return index (TRI) 를 표시용 won 수준으로 rebase 한 line point.
    Decimal → str wire (StockPriceBarOut 동일 원칙 — JSON number drift 회피).

    value: 표시용 누적 수준 = `base_close_adjusted × TRI`. base_close_adjusted 는
        조회 window 첫 거래일의 PIT 보정 종가(PriceAdjuster, read-time as_of 보정).
        TRI[첫 거래일] == 1.0 이므로 value[0] == base_close_adjusted. 배당이 없으면
        TRI[t] == close_adj[t] / close_adj[0] 라 value[t] == close_adj[t] (배당 0 →
        total-return 라인 == 보정 종가 라인, Decimal 정밀도 내). 배당이 있으면
        배당락일 재투자분만큼 위로 발산.
    index: TRI 그대로(첫 거래일 1.0 기준 누적). 투명성·검증용 노출.
    """

    model_config = _STRICT_MODEL_CONFIG

    date: date
    value: str   # base_close_adjusted × TRI — Decimal → str
    index: str   # TRI (첫 거래일=1.0 기준) — Decimal → str


class StockTotalReturnOut(BaseModel):
    """Total Return 시계열 응답 — `[start, as_of]` 범위 세전 TRI (date asc).

    M7 #6 — 가격 차트의 "권리락+배당재투자(Total Return)" 토글 backend.

    산출 (ADR-0035 D1/D6/D7):
        PriceRepository.fetch_prices(raw) → PriceAdjuster.adjust(as_of 보정) →
        TotalReturnAdjuster.compute(배당 재투자). 보정 종가 basis 는 read-time
        PIT 보정(factor `total_return_trailing_1y` 와 동일 경로) — `/prices` 의
        repo 저장 close_adjusted(T20 미적용으로 현재 raw 동일) 와 별개 basis.

    세전 (D7): 배당소득세·양도세 미반영 — 응답 자체엔 disclosure 텍스트 없음
        (client PreTaxDisclosure 가 고지). points 비면 데이터 없음(200).

    warnings: §2.1 조용한 손실 금지 — 배당락일이 가격 시계열에 부재하거나 첫
        거래일 배당(직전 종가 부재로 재투자 불가) 등으로 누락된 배당 사유.
        보정 invariant 위반(미지원 action_type / 0 division) 시에도 사유 1줄.
        비면 모든 배당이 정상 재투자됨.
    """

    model_config = _STRICT_MODEL_CONFIG

    code: str
    as_of: date
    points: tuple[StockTotalReturnPointOut, ...]
    warnings: tuple[str, ...]


class FinancialSeriesItemOut(BaseModel):
    """재무 시계열 표의 단일 account 행.

    values 는 periods 와 같은 길이 (결손 분기 = None). Decimal → str wire.
    unit 은 FinancialRecord.unit 그대로 전달 — "krw", "ratio" 등.
    """

    model_config = _STRICT_MODEL_CONFIG

    account: str        # canonical_id (예: "revenue")
    name: str           # 표시명 (예: "매출액")
    unit: str           # "krw", "ratio", …
    values: tuple[str | None, ...]  # periods 순서와 1:1 대응


class FinancialSeriesOut(BaseModel):
    """재무 시계열 endpoint `GET /{code}/financials` 의 응답 schema.

    periods: 오름차순 정렬된 fiscal_period 합집합 (예: ["2022Q4","2023Q1",…]).
        fiscal_period 는 DART 표준 `f"{year}Q{quarter}"` 형식.
    items: 각 account 의 시계열. 적재 데이터 없는 account 는 제외 (빈 row 표시 안 함).
    데이터 전혀 없으면 periods=() / items=() 로 200 반환 (차트·표가 "데이터 없음" 표시).

    PIT: FinancialRepository.fetch_financials 가 effective_date<=as_of 강제 —
        endpoint 가 별도 필터 없이 PIT 보장.
    """

    model_config = _STRICT_MODEL_CONFIG

    code: str
    as_of: date
    periods: tuple[str, ...]          # 오름차순 정렬
    items: tuple[FinancialSeriesItemOut, ...]


class FinancialHistoryVintageOut(BaseModel):
    """정정공시 이력의 단일 vintage — 재무 account 별 한 공시 회차.

    No Advice (§2.2): effective_date / value / is_active / superseded_by 같은
    관측 사실만 노출. "정정으로 개선/악화" 판단·라벨 없음. 순수 관측.

    vintage_seq: fiscal_period 내 정정 회차 (1-based). 원본=1, 1차 정정=2, …
        프론트가 "원본 vs 정정 n회차" 표시에 사용.

    is_active: superseded_by is None → True (현재 유효한 최신 vintage).
        False = 후속 정정에 의해 supersede 된 vintage (역사적 보존).

    Decimal → str wire (JSON number drift 회피, Risk-C4). 프론트가 BigNumber
    / decimal.js 로 처리.
    """

    model_config = _STRICT_MODEL_CONFIG

    vintage_seq: int          # fiscal_period 내 정정 회차 (1-based, 원본=1)
    effective_date: date      # 공시일 (DART rcept_no 도출 또는 법적 신고기한 보수값)
    account: str              # canonical account 키 (예: "revenue")
    value: str                # Decimal → str wire
    unit: str                 # "krw", "ratio" 등
    ifrs_type: str            # "consolidated" | "separate"
    is_active: bool           # superseded_by is None — 현재 유효 여부
    superseded_by: UUID | None  # 후속 vintage id. None = 이 vintage 가 최신

    @classmethod
    def from_record(
        cls, record: FinancialRecord, *, vintage_seq: int,
    ) -> FinancialHistoryVintageOut:
        """FinancialRecord → wire 단방향 factory."""
        return cls(
            vintage_seq=vintage_seq,
            effective_date=record.effective_date,
            account=record.account,
            value=str(record.value),
            unit=record.unit,
            ifrs_type=record.ifrs_type,
            is_active=(record.superseded_by is None),
            superseded_by=record.superseded_by,
        )


class FinancialHistoryOut(BaseModel):
    """정정공시 이력 endpoint `GET /{code}/financials/history` 응답 schema.

    vintages: (fiscal_period asc, effective_date asc, id asc) 정렬된 전체 vintage.
        각 vintage 에 vintage_seq(정정 회차) + is_active(현재 유효 여부) 포함.
        프론트가 fiscal_period 별 그룹화 / 정정 chain 시각화에 사용.

    PIT look-ahead 차단 (§2.4): as_of 가 있으면 effective_date <= as_of 인
        vintage 만 포함 (그 시점까지 공시된 정정만). None 이면 전체 이력.

    No Advice (§2.2): effective_date / value / is_active 같은 사실만.
        "정정으로 개선/악화" 판단 없음.
    """

    model_config = _STRICT_MODEL_CONFIG

    code: str
    as_of: date | None          # PIT look-ahead 차단 기준일. None = 전체 이력
    fiscal_period_filter: str | None  # 요청된 fiscal_period 필터. None = 전 분기
    vintages: tuple[FinancialHistoryVintageOut, ...]


# =============================================================================
# Helpers
# =============================================================================

def _compute_status(master: StockMasterRecord, as_of: date) -> StockStatus:
    """as_of 시점의 종목 상태 산출 — oracle 결정 6."""
    if master.listing_date > as_of:
        return StockStatus.NOT_YET_LISTED
    if master.delisting_date is not None and master.delisting_date <= as_of:
        return StockStatus.DELISTED
    return StockStatus.ACTIVE


def _resolve_display_code(master: StockMasterRecord) -> str:
    """UI 표시용 종목코드 — current_code 우선, 폐지면 마지막 historical code.

    ADR-0009 D7 의 "피합병/폐지 종목 시계열 보존" 의도 — 폐지 종목도 UI 가 코드로
    식별 가능해야 함.

    `code_history` 의 invariant (oracle T27 #2):
        Repository 가 `valid_from` 오름차순으로 정렬 보장. `[-1]` = 가장 최근 코드.
        `merger_temporary` reason 의 임시 코드가 마지막일 경우 UI 가 그 임시
        코드를 노출 — 의도된 동작 (역사적 마지막 식별자). 사용자가 historical
        조회 시 임시 코드 인지가 필요한 경우 `code_history` 전체를 별도 표시.

    lineage 도 history 도 비어있는 경우 (운영상 발생 X) 빈 string fallback.
    """
    if master.current_code:
        return master.current_code
    if master.code_history:
        # 가장 최근 entry — Repository 가 valid_from asc 정렬 보장.
        return master.code_history[-1].code
    return ""
