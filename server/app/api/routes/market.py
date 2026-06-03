"""Market Overview endpoint — GET /api/market-overview.

M1 T60 (플랜 D). as_of 시점 active 유니버스의 **집계 통계** 제공 — 홈 시장통계 +
Sector/Market Overview view 의 backend.

설계 원칙:
- **집계만, 랭킹·추천 0** (R8, Active Inspection·No Advice 기둥). 응답은
  universe_count + 시장별 종목 수 + factor 분포(평균/중앙값/사분위/최소·최대)만.
  종목 식별자·"상위 N"·매수/매도 신호를 일절 포함하지 않음.
- **유니버스 평가 경로 재사용** — `screen.py` 의 조건 매칭과 동일하게 active
  종목별 `DbFieldProvider` + `FactorEvaluator` 로 factor 를 실평가한다. 차이는
  필터링이 아니라 **집계**라는 점뿐. 따라서 Screener 결과와 동일한 factor 값
  의미론(정정공시 chain·KRX 캘린더·PIT) 을 그대로 상속 (Fidelity §2.1).
- **집계 PIT** (AC-M1-P-06): 모든 입력의 `effective_date <= as_of` — provider/
  repo 가 강제. 본 모듈은 evaluator 결과만 집계.

성능 한계 (M1, screen.py 와 동일):
- live 평가는 universe(운영 ~2500) × overview factor 수 × 종목별 DB fetch.
  정확성 우선 구현 — precompute 최적화는 별도 cycle. 종목 내 캐싱만
  (DbFieldProvider — 한 종목의 PER+PBR 이 market_cap_ex_treasury 공유).

집계 대상 factor 는 stocks.py 의 `DEFAULT_DISPLAY_FACTORS` (Stock Detail 지표
카드와 동일 집합) — 사용자가 개별 종목에서 보는 지표의 시장 전체 분포를 제시.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

from fastapi import APIRouter

from app.api.dependencies import NormalizedAsOfDep
from app.api.dependencies.repositories import (
    ActivePackDep,
    CorporateActionRepoDep,
    FactorEvaluatorDep,
    FinancialRepoDep,
    MacroIndicatorRepoDep,
    MarketCapRepoDep,
    PriceRepoDep,
    StocksRepoDep,
    TreasurySharesRepoDep,
)
from app.repositories.pit_protocols import (
    CorporateActionRepository,
    FinancialRepository,
    MacroIndicatorRepository,
    MarketCapRepository,
    PriceRepository,
    TreasurySharesRepository,
)
from app.repositories.stocks_master_repository import StocksMasterRepository
from app.schemas.market import (
    FactorAggregateOut,
    MacroIndicatorOut,
    MarketCountOut,
    MarketOverviewOut,
)
from app.schemas.stocks import DEFAULT_DISPLAY_FACTORS
from app.services.db_field_provider import DbFieldProvider
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import LoadedPack

router = APIRouter(prefix="/api", tags=["market"])

# 집계 통계의 표시용 정밀도 — 파생 통계(평균/중앙값/사분위)를 6 자리로 quantize.
# market-overview 는 저장되는 재현 run 이 아닌 **live observation** 이므로
# byte-동일 재현 대상이 아님. quantize 는 응답 문자열 길이 bound + 표시 일관성용.
_QUANTIZE: Final[Decimal] = Decimal("0.000001")

# ECOS 대표 매크로 지표 표시 목록 — (indicator_id, 표시명) 쌍.
# 표시용만 (factor 입력 금지 — ADR-0007 D5, M1 원칙).
# ECOS ITEM_NAME 에 의존하지 않고 우리 상수로 고정 — ECOS API 이름 변경에 무관.
# indicator_id 는 ECOS 통계표코드/항목코드 조합 (EcosAdapter T63 코드 체계 기준).
_MACRO_INDICATORS: Final[tuple[tuple[str, str], ...]] = (
    ("722Y001/0101000", "한국은행 기준금리"),
    ("901Y009/0", "소비자물가지수"),
)


@router.get("/market-overview", response_model=MarketOverviewOut)
async def get_market_overview(
    as_of: NormalizedAsOfDep,
    stocks_repo: StocksRepoDep,
    pack: ActivePackDep,
    evaluator: FactorEvaluatorDep,
    price_repo: PriceRepoDep,
    financial_repo: FinancialRepoDep,
    corporate_action_repo: CorporateActionRepoDep,
    market_cap_repo: MarketCapRepoDep,
    treasury_repo: TreasurySharesRepoDep,
    macro_repo: MacroIndicatorRepoDep,
) -> MarketOverviewOut:
    """as_of 시점 active 유니버스의 집계 통계 + ECOS 대표 매크로 지표.

    종목별 데이터·랭킹·추천 없이 (R8): universe_count + 시장별 종목 수 + 지표
    카드 factor 들의 분포(평균/중앙값/사분위/최소·최대) + 매크로 지표(표시용만).
    각 factor 값은 Screener 와 동일한 live 평가 경로로 산출 (PIT·정정공시 chain 일관).
    매크로 지표는 factor 입력으로 사용되지 않음 (ADR-0007 D5).
    """
    return compute_market_overview(
        as_of=as_of.value,
        stocks_repo=stocks_repo,
        pack=pack,
        evaluator=evaluator,
        price_repo=price_repo,
        financial_repo=financial_repo,
        corporate_action_repo=corporate_action_repo,
        market_cap_repo=market_cap_repo,
        treasury_repo=treasury_repo,
        macro_repo=macro_repo,
    )


# =============================================================================
# 집계 — testable pure / 준-pure helpers
# =============================================================================

def _percentile(sorted_vals: list[Decimal], q: Decimal) -> Decimal:
    """오름차순 정렬된 값에서 분위수 q (0~1) — linear interpolation (numpy type 7).

    rank = (n-1)*q. lo = floor(rank), frac = rank-lo. 두 인접 값 사이 선형보간:
        v[lo] + (v[lo+1] - v[lo]) * frac
    전부 Decimal 산술 — float 경유 없음 (evaluator/price_adjuster 와 동일 결정성).

    전제: sorted_vals 비어있지 않음 (호출부가 보장). n==1 이면 그 값.
    """
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    rank = Decimal(n - 1) * q
    # int(Decimal) 은 0 방향 절단 — rank >= 0 이므로 floor 와 동일.
    lo = int(rank)
    if lo + 1 >= n:
        # rank 가 마지막 인덱스에 정확히 도달 (q==1 또는 경계) — 보간 불필요.
        return sorted_vals[lo]
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[lo + 1] - sorted_vals[lo]) * frac


def _quantize(value: Decimal) -> str:
    """파생 통계를 표시용 6 자리로 quantize → str. ROUND_HALF_EVEN (은행가 반올림)."""
    return str(value.quantize(_QUANTIZE, rounding=ROUND_HALF_EVEN))


def aggregate_values(values: list[Decimal], na_count: int) -> dict[str, object]:
    """관측값 list → 집계 통계 dict (FactorAggregateOut 의 통계 필드).

    Args:
        values: non-N/A 로 산출된 factor 값들 (정렬 전).
        na_count: 같은 factor 의 N/A 종목 수.

    Returns:
        {count, na_count, mean, median, p25, p75, min, max}. count==0 이면 모든
        통계가 None (값 없는 factor — 전 종목 N/A).

    평균/중앙값/사분위는 6 자리 quantize (표시용). min/max 는 관측된 실제 값을
    quantize 없이 그대로 (원값 보존).
    """
    count = len(values)
    if count == 0:
        return {
            "count": 0, "na_count": na_count,
            "mean": None, "median": None, "p25": None, "p75": None,
            "min": None, "max": None,
        }
    ordered = sorted(values)
    total = sum(ordered, Decimal(0))
    mean = total / Decimal(count)
    return {
        "count": count,
        "na_count": na_count,
        "mean": _quantize(mean),
        "median": _quantize(_percentile(ordered, Decimal("0.5"))),
        "p25": _quantize(_percentile(ordered, Decimal("0.25"))),
        "p75": _quantize(_percentile(ordered, Decimal("0.75"))),
        # 관측 실제값 — quantize 없이 원값 (분포의 실제 경계).
        "min": str(ordered[0]),
        "max": str(ordered[-1]),
    }


def compute_market_overview(
    *,
    as_of: date,
    stocks_repo: StocksMasterRepository,
    pack: LoadedPack,
    evaluator: FactorEvaluator,
    price_repo: PriceRepository,
    financial_repo: FinancialRepository,
    corporate_action_repo: CorporateActionRepository,
    market_cap_repo: MarketCapRepository,
    treasury_repo: TreasurySharesRepository,
    macro_repo: MacroIndicatorRepository,
) -> MarketOverviewOut:
    """active 유니버스를 1 회 순회하며 시장별 카운트 + factor 분포를 집계.

    각 active 종목마다 `DbFieldProvider` 를 만들어 `DEFAULT_DISPLAY_FACTORS` 를
    평가 — 한 종목의 모든 overview factor 를 같은 provider 로 평가하여 종목 내
    캐싱(market_cap 공유)을 활용. N/A 는 해당 factor 의 na_count 로만 집계(값으로
    오인 금지 — Fidelity). 결과 factor 순서는 `DEFAULT_DISPLAY_FACTORS` 순서로
    결정적.

    market_breakdown 은 시장명 오름차순 정렬 (결정적). universe_count 는 평가된
    active 종목 수 (current_code 있는 종목).

    macro_indicators: _MACRO_INDICATORS 상수 순서로 각 indicator 를 PIT 조회.
        fetch_latest 결과가 None (데이터 없음) 이면 해당 indicator 를 제외 —
        빈 항목 표시 안 함. 표시용만, factor 입력 금지 (ADR-0007 D5).
    """
    factors_by_id = {f["canonical_id"]: f for f in pack.body["factors"]}
    # overview factor 중 active pack 에 실제 존재하는 것만 (방어 — pack 변경 대비).
    overview_ids = [fid for fid in DEFAULT_DISPLAY_FACTORS if fid in factors_by_id]

    # factor 별 관측값 누적 + N/A 카운트.
    values_by_factor: dict[str, list[Decimal]] = {fid: [] for fid in overview_ids}
    na_by_factor: dict[str, int] = {fid: 0 for fid in overview_ids}
    market_counts: dict[str, int] = {}
    universe_count = 0

    for record in stocks_repo.list_active(as_of=as_of):
        code = record.current_code
        if not code:
            # 폐지 lineage (current_code None) — active 정의상 미발생하나 방어.
            # fact 조회 키 부재 → 집계 제외.
            continue
        universe_count += 1
        market_counts[record.market] = market_counts.get(record.market, 0) + 1

        # 종목 1 인스턴스 — overview factor 전체를 같은 provider 로 평가 (캐싱).
        # macro_repo 주입 — ECOS field (ecos_base_rate / ecos_cpi 등) 를 factor
        # 입력으로 쓰는 경우 vintage PIT 해소. 미주입 시 정식 N/A (M2 T81).
        provider = DbFieldProvider(
            code=code,
            as_of=as_of,
            price_repo=price_repo,
            financial_repo=financial_repo,
            corporate_action_repo=corporate_action_repo,
            market_cap_repo=market_cap_repo,
            treasury_repo=treasury_repo,
            macro_repo=macro_repo,
            factor_pack=pack,
            evaluator=evaluator,
        )
        for fid in overview_ids:
            result = evaluator.evaluate(factors_by_id[fid], provider, as_of=as_of)
            if result.is_na or result.value is None:
                na_by_factor[fid] += 1
            else:
                values_by_factor[fid].append(result.value)

    factors_out = tuple(
        FactorAggregateOut(
            canonical_id=fid,
            name=factors_by_id[fid]["name"],
            unit=factors_by_id[fid]["unit"],
            **aggregate_values(values_by_factor[fid], na_by_factor[fid]),
        )
        for fid in overview_ids
    )
    market_breakdown = tuple(
        MarketCountOut(market=mkt, count=market_counts[mkt])
        for mkt in sorted(market_counts)
    )

    # ECOS 대표 매크로 지표 조회 — 표시용만 (factor 입력 금지 — ADR-0007 D5).
    # vintage 이중 시간축 PIT: reference_date + vintage_date <= as_of.
    # fetch_latest 가 None 이면 해당 지표 제외 (빈 항목 미표시).
    macro_out_list: list[MacroIndicatorOut] = []
    for indicator_id, display_name in _MACRO_INDICATORS:
        record = macro_repo.fetch_latest(indicator_id, as_of=as_of)
        if record is None:
            # 데이터 없음 — 제외 (빈 항목 표시 안 함, Fidelity).
            continue
        macro_out_list.append(MacroIndicatorOut(
            indicator_id=record.indicator_id,
            name=display_name,
            value=str(record.value),
            unit=record.unit,
            reference_date=record.reference_date,
            vintage_date=record.vintage_date,
        ))

    return MarketOverviewOut(
        as_of=as_of,
        universe_count=universe_count,
        market_breakdown=market_breakdown,
        factors=factors_out,
        macro_indicators=tuple(macro_out_list),
    )
