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

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final
from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import NormalizedAsOfDep
from app.api.dependencies.repositories import (
    ActivePackDep,
    CorporateActionRepoDep,
    DividendRepoDep,
    FactorEvaluatorDep,
    FinancialRepoDep,
    MacroIndicatorRepoDep,
    MarketCapRepoDep,
    PriceRepoDep,
    SessionOrNoneDep,
    SnapshotRepoDep,
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
    StockSnapshotRepository,
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
from app.services.snapshot_serve import try_serve_snapshot_values
from app.services.snapshot_versions import collect_run_data_versions
from app.services.total_return_adjuster import TotalReturnAdjuster

router = APIRouter(prefix="/api", tags=["market"])

# 집계 통계의 표시용 정밀도 — 파생 통계(평균/중앙값/사분위)를 6 자리로 quantize.
# market-overview 는 저장되는 재현 run 이 아닌 **live observation** 이므로
# byte-동일 재현 대상이 아님. quantize 는 응답 문자열 길이 bound + 표시 일관성용.
_QUANTIZE: Final[Decimal] = Decimal("0.000001")

# 대표 매크로 지표 표시 목록 — (indicator_id, 표시명) 쌍 (ECOS + KOSIS).
# 사실 수치만 표시 (해석·전망 0 — §2.2 No Advice / §2.7 Observation).
# ECOS indicator_id: 통계표코드/항목코드 조합 (EcosAdapter T63 코드 체계 기준).
# KOSIS indicator_id: "kosis/{orgId}/{tblId}/{itmId}" 규약 (ADR-0036 §2.2).
# 표시명은 API 이름에 의존하지 않고 상수로 고정 — 외부 API 이름 변경에 무관.
# KOSIS 지표는 ECOS 미보유 통계청 고유 지표만 (CPI 제외 — ECOS 1차 SoT, ADR-0036 D5).
_MACRO_INDICATORS: Final[tuple[tuple[str, str], ...]] = (
    # ECOS
    ("722Y001/0101000", "한국은행 기준금리"),
    ("901Y009/0", "소비자물가지수"),
    # KOSIS — 통계청 고유 지표 (ECOS 미보유, M9 #2, ADR-0036 D5/D6)
    # tblId/itmId 라이브 실측 검증됨(2026-06-18). db_field_provider _RESOLUTIONS 와
    # 동일 id 집합(test_macro_field_ids_match_market_overview_display 가 강제).
    ("kosis/101/DT_1DA7001S/T80", "실업률"),
    ("kosis/101/DT_1DA7001S/T90", "고용률"),
    ("kosis/101/DT_1JH20201/T1", "전산업생산지수"),
    ("kosis/101/DT_1C8015/T1", "경기선행지수"),
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
    dividend_repo: DividendRepoDep,
    snapshot_repo: SnapshotRepoDep,
    session: SessionOrNoneDep,
) -> MarketOverviewOut:
    """as_of 시점 active 유니버스의 집계 통계 + ECOS 대표 매크로 지표.

    종목별 데이터·랭킹·추천 없이 (R8): universe_count + 시장별 종목 수 + 지표
    카드 factor 들의 분포(평균/중앙값/사분위/최소·최대) + 매크로 지표(표시용만).
    각 factor 값은 Screener 와 동일한 live 평가 경로로 산출 (PIT·정정공시 chain 일관).
    매크로 지표는 표시 + factor 입력 둘 다 허용 — ADR-0007 D5는 ECOS M2(T81)에서 해제,
    ADR-0036 D6 에서 KOSIS 동일 적용.

    precompute read-bypass (ⓓ slice 2b): snapshot 이 있고 그 종목의 전 overview
    factor 가 hit + snapshot.data_versions == 현재 live data_versions 이면 평가를
    skip 하고 precomputed value 사용(아래 compute_market_overview). market_overview
    는 reproduce 경로가 없는 **live observation** 이라 §2.10 byte-reproduce 위험 0.
    session 없으면(Fake-only) current_dv None → 전부 live(기존 동작 보존).
    """
    # 현재 freeze fingerprint — snapshot.data_versions 와 비교해 serve-vs-recompute.
    # session 있을 때만(batch_id 합류 필요). pack 은 active pack — custom pack 이면
    # factor_pack_content_hash 가 달라 builtin precompute 와 mismatch → live(안전).
    current_dv: Mapping[str, str] | None = (
        dict(collect_run_data_versions(as_of.value, session, pack))
        if session is not None
        else None
    )
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
        dividend_repo=dividend_repo,
        snapshot_repo=snapshot_repo,
        current_data_versions=current_dv,
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


def _serve_overview_from_snapshot(
    *,
    code: str,
    as_of: date,
    overview_ids: list[str],
    overview_uuids: dict[str, UUID],
    snapshot_repo: StockSnapshotRepository | None,
    current_data_versions: Mapping[str, str] | None,
) -> dict[str, Decimal | None] | None:
    """precompute snapshot 으로 한 종목의 전 overview factor 를 serve (ⓓ slice 2b).

    serve 조건 (all-or-nothing per code — partial provider 복잡도 회피, oracle 권고):
        1. snapshot_repo + current_data_versions 주입(미주입=Fake/세션없음 → None).
        2. 그 종목의 **전** overview factor uuid 가 snapshot 에 존재(하나라도 miss
           → None → 호출자가 live 평가).
        3. 각 snapshot.data_versions == 현재 live data_versions(정책 hash·batch_id·
           evaluator_version 일치 = 같은 입력·정책 세대). 하나라도 stale → None.
    data_versions 에 krx/dart/dividend batch_id 가 포함되므로 새 배치가 precompute
    후 실행되면 batch_id 변경 → mismatch → recompute(freshness guard 내장). 값은
    live 평가와 byte-동일(producer 가 동일 FactorEvaluator·동일 Decimal context 사용
    + str(Decimal) 무손실 round-trip).

    입력 source 별 freshness 보장(oracle 리뷰 M1):
        - KRX(price/market_cap)·DART(financial/treasury)·FSC(dividend): 각 batch_id
          가 data_versions 에 있어 정정/갱신이 mismatch 로 잡힘.
        - corporate_action: **DART adapter 가 ingest**(source=SourceKind.DART,
          dart_daily 배치) 하므로 CA 정정도 dart_batch_id 갱신으로 잡힘 → price-
          return/dividend-yield 의 split 보정 stale 안전. (CA 가 DART 외 source 로
          ingest 되면 data_versions 에 안 잡혀 stale 가능 — 현재 CA=DART-only 전제.)
        - macro: vintage PIT(reference_date+vintage_date<=as_of) — 고정 as_of 의 값은
          append-only vintage 라 불변, batch_id 불요(정상).
        - pack: factor_pack_content_hash 가 data_versions 에 있어 **builtin precompute
          와 다른 active/custom pack 은 자동 mismatch → live**(custom pack 안전, L1).

    잔여 한계(known-limit): out-of-order late-commit backfill(started_at<=기존 max
    라 batch_id 불변)은 미탐. 단 computed_at 가드도 동일하게 못 잡고, market_overview
    가 reproduce 없는 live display(cutoff=None 읽기의 본질적 비결정성과 동급)라 허용.

    Returns:
        {fid: value(Decimal | None=미산정)} 전 factor serve 가능 시, 아니면 None.
    """
    # 공유 순수 predicate(snapshot_serve.try_serve_snapshot_values)에 위임 —
    # predicate (1)~(4) 는 screen(slice 1b)·1a harness 와 단일 source(drift 0).
    # 본 helper 는 그 결과(uuid→value)를 overview 의 fid→value 계약으로 재매핑.
    served_by_uuid = try_serve_snapshot_values(
        code,
        as_of=as_of,
        factor_uuids=frozenset(overview_uuids.values()),
        snapshot_repo=snapshot_repo,
        current_data_versions=current_data_versions,
    )
    if served_by_uuid is None:
        return None
    return {fid: served_by_uuid[overview_uuids[fid]] for fid in overview_ids}


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
    dividend_repo: DividendRepository | None = None,
    snapshot_repo: StockSnapshotRepository | None = None,
    current_data_versions: Mapping[str, str] | None = None,
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
        빈 항목 표시 안 함. 표시(_MACRO_INDICATORS) + factor field(_RESOLUTIONS) 둘 다
        허용 — ADR-0007 D5는 ECOS M2(T81)에서 연산용 해제, ADR-0036 D6 KOSIS 동일
        (db_field_provider.py L325 참조).
    """
    factors_by_id = {f["canonical_id"]: f for f in pack.body["factors"]}
    # overview factor 중 active pack 에 실제 존재하는 것만 (방어 — pack 변경 대비).
    overview_ids = [fid for fid in DEFAULT_DISPLAY_FACTORS if fid in factors_by_id]
    # factor canonical_id → uuid (snapshot 은 factor_uuid 로 키잉, ⓓ read-bypass).
    overview_uuids = {fid: UUID(factors_by_id[fid]["uuid"]) for fid in overview_ids}

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

        # precompute read-bypass — snapshot 으로 평가 대체 시도(ⓓ slice 2b). 전
        # overview factor hit + data_versions 일치 시 평가 skip(평가 CPU + provider
        # fetch 절감). miss/stale 이면 None → 아래 live 평가(기존 경로) fallback.
        served = _serve_overview_from_snapshot(
            code=code,
            as_of=as_of,
            overview_ids=overview_ids,
            overview_uuids=overview_uuids,
            snapshot_repo=snapshot_repo,
            current_data_versions=current_data_versions,
        )
        if served is not None:
            for fid, value in served.items():
                # snapshot value=None → 미산정(N/A) — live 평가의 is_na 와 동일 집계.
                if value is None:
                    na_by_factor[fid] += 1
                else:
                    values_by_factor[fid].append(value)
            continue

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
            dividend_repo=dividend_repo,
            # M7 #4 fix5 — both-or-neither: dividend_repo None 이면 adjuster 도 None
            # (DbFieldProvider assert 와 일관). 라우트는 항상 repo 주입이나 helper
            # default(None) 직접 호출 테스트 호환.
            total_return_adjuster=(
                TotalReturnAdjuster() if dividend_repo is not None else None
            ),
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

    # 매크로 지표 조회 — 표시 + factor 입력 둘 다 허용 (ADR-0007 D5는 ECOS M2(T81) 해제,
    # ADR-0036 D6 KOSIS 동일).
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
