"""Market Overview endpoint wire schemas — `GET /api/market-overview`.

설계 (M1 T60, 플랜 R8):
- **집계 통계만** — 평균/중앙값/분위수(분포). 종목별 리스트·랭킹·추천 0
  (Active Inspection·No Advice 기둥). 응답에 개별 종목코드를 노출하지 않음.
- Decimal → str wire (JSON number drift 회피, stocks.py 와 동일 원칙).
- 집계 PIT: 모든 입력의 `effective_date <= as_of` (provider/repo 가 강제,
  AC-M1-P-06). 본 스키마는 산출 결과만 운반.
- strict + extra="forbid" + frozen=True.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

__all__ = [
    "FactorAggregateOut",
    "MacroIndicatorOut",
    "MarketCountOut",
    "MarketOverviewOut",
]

_STRICT_MODEL_CONFIG = ConfigDict(strict=True, extra="forbid", frozen=True)


class MacroIndicatorOut(BaseModel):
    """ECOS 거시지표 단일 관측값 — 표시용만 (factor 입력 금지 — ADR-0007 D5).

    값 + 기준일(reference_date) + 관측시점(vintage_date) + 단위를 사실로만 제시.
    해석·판단·추천 텍스트 0, 랭킹 0, 종목과 무관한 시장 wide fact.

    vintage_date 는 우리가 ECOS 에서 관측한 시점의 근사(정확한 한국은행 공표일
    아님 — T63). 응답에 포함해 투명성 유지(disclaimer 는 Phase 3 프론트 소관).

    Attributes:
        indicator_id: ECOS 통계 식별자 (예: "722Y001/0101000").
        name: 지표 표시명 (ECOS ITEM_NAME 의존 금지 — routes 의 상수 매핑).
        value: 지표 값 (Decimal → str, JSON number drift 회피).
        unit: 값의 단위 ("percent" / "index" 등).
        reference_date: 지표가 가리키는 기준 시점 (월/분기 → date 정규화).
        vintage_date: 한국은행 공표/개정 시점 근사 — PIT 두 번째 축 (투명성).
    """

    model_config = _STRICT_MODEL_CONFIG

    indicator_id: str
    name: str
    value: str  # Decimal → str (JSON drift 회피)
    unit: str
    reference_date: date
    vintage_date: date


class MarketCountOut(BaseModel):
    """시장별 active 종목 수 — 집계(분포)의 일부. 랭킹 아님."""

    model_config = _STRICT_MODEL_CONFIG

    market: str  # "KOSPI" | "KOSDAQ" | "KONEX"
    count: int


class FactorAggregateOut(BaseModel):
    """단일 factor 의 유니버스 집계 통계 — 종목별 값이 아닌 **분포 요약**.

    No Advice/Active Inspection: 어떤 종목이 싸다/비싸다 판단하지 않고, 유니버스
    전체의 분포(평균·중앙값·사분위·최소/최대)만 사실로 제시. 종목 식별자 미포함.

    Attributes:
        count: 값이 산출된(non-N/A) 종목 수.
        na_count: 값이 N/A 인 종목 수 (분모 0·입력 부재 등). count+na_count =
            평가된 유니버스 종목 수.
        mean / median / p25 / p75: 산출 통계 (Decimal → str). 표시용 정밀도로
            6 자리 quantize. count==0 이면 모두 None.
        min / max: 관측된 실제 최소/최대값 (quantize 없이 원값). count==0 → None.
    """

    model_config = _STRICT_MODEL_CONFIG

    canonical_id: str
    name: str
    unit: str
    count: int
    na_count: int
    mean: str | None
    median: str | None
    p25: str | None
    p75: str | None
    min: str | None
    max: str | None


class MarketOverviewOut(BaseModel):
    """시장 통계 응답 — as_of 시점 active 유니버스의 집계.

    종목별 데이터·랭킹·추천을 일절 포함하지 않음 (R8). universe_count +
    시장별 종목 수 + factor 분포 통계 + ECOS 대표 매크로 지표(표시용만).

    macro_indicators: ECOS 거시지표 — 값이 있는(None 아닌) 지표만 포함.
        데이터 없는 지표는 제외(빈 항목 표시 안 함). 표시용만 — factor 입력 금지
        (ADR-0007 D5, M1 원칙). 순서는 routes._MACRO_INDICATORS 상수 정의 순.
    """

    model_config = _STRICT_MODEL_CONFIG

    as_of: date
    universe_count: int
    market_breakdown: tuple[MarketCountOut, ...]
    factors: tuple[FactorAggregateOut, ...]
    macro_indicators: tuple[MacroIndicatorOut, ...] = ()
