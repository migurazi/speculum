"""Backtest endpoint — POST /api/backtest (ADR-0027 D1~D6).

screen_custom.py 패턴 미러 — CurrentUserDep + pack 식별자. pack 은 registry.resolve
(빌트인/reference/custom 모두) 로 해소. 엔진(backtest_engine)을 PIT repository 와
조립하여 동기 실행, freeze artifact(BacktestSnapshot) 생성 후 wire 응답.

가드레일 (ADR-0027):
- D2: 거래비용 가정을 항상 응답에 노출 (cost_assumptions).
- D3: survivorship_complete / missing_price_ratio 노출 — UI 가 경고 게이트.
- D4: disclaimer_required=true 고정 — 과거성과 디스클레이머 게이트.
- D6: 사실 통계만 — 평가 라벨/등급 0.

성능 (ADR-0027 — 동기 처리): 작은 universe/기간 가정. universe × rebalance 횟수 ×
종목별 DB fetch 라 비용이 큼 — 무거운 입력은 기간/종목 상한으로 보호(상한 초과 시
422). precompute 최적화는 별도 cycle.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Final
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies.auth import CurrentUserDep
from app.api.dependencies.repositories import (
    CorporateActionRepoDep,
    CustomPackRepoDep,
    FactorEvaluatorDep,
    FinancialRepoDep,
    MacroIndicatorRepoDep,
    MarketCapRepoDep,
    PriceRepoDep,
    SessionOrNoneDep,
    StocksRepoDep,
    TreasurySharesRepoDep,
)
from app.schemas.backtest import (
    BacktestConditionIn,
    BacktestRequestIn,
    BacktestResultOut,
    CostAssumptionsOut,
    EquityPointOut,
    FreezeOut,
    StatsOut,
)
from app.services.backtest_engine import (
    DEFAULT_COMMISSION_BPS,
    DEFAULT_TAX_BPS,
    BacktestCondition,
    BacktestResult,
    CostAssumptions,
    run_backtest,
)
from app.services.backtest_snapshot import BacktestSnapshotBuilder
from app.services.factor_pack import LoadedPack
from app.services.pack_registry import PackRegistry
from app.services.snapshot_versions import collect_run_data_versions

router = APIRouter(prefix="/api", tags=["backtest"])

# 프로세스 수명 PackRegistry — 빌트인/reference 캐시 공유 (screen_custom 패턴).
_PACK_REGISTRY: Final[PackRegistry] = PackRegistry()

# 동기 백테스트 보호 상한 (ADR-0027 — 너무 무거우면 상한 + 보고). rebalance 그리드
# 폭증·장기 기간을 차단. 분기말 기준 최대 약 40 년, 월말 기준 약 10 년 수준의
# rebalance 점을 허용하면서 무한 universe loop 폭주는 universe 자체가 list_active
# 로 제한되므로 기간 상한만 둔다.
_MAX_PERIOD_DAYS: Final[int] = 365 * 40


def _resolve_pack(
    *,
    pack_slug: str,
    pack_version: str,
    user: CurrentUserDep,
    custom_pack_repo: CustomPackRepoDep,
) -> LoadedPack:
    """pack 식별자 → LoadedPack — 빌트인/reference/custom 통합 (ADR-0027 D5).

    빌트인(speculum-builtin)은 user_id 무관 load_builtin_pack. custom slug 는
    user 격리 조회 (타 user/미존재 → None → 404). screen_custom 의 빌트인 사칭
    차단(400)과 달리, 백테스트는 빌트인 pack 으로도 실행 가능해야 하므로 빌트인을
    허용한다 (ADR-0027 D1 — pack = registry.resolve, 빌트인 포함).
    """
    pack = _PACK_REGISTRY.resolve(
        pack_slug, pack_version,
        user_id=user.user_id,
        custom_pack_repo=custom_pack_repo,
    )
    if pack is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="pack 을 찾을 수 없습니다 (미존재 또는 타 user custom pack).",
        )
    return pack


def _parse_threshold(value: str) -> Decimal:
    """condition.value (str) → Decimal. 비숫자/비유한 → 400 (screen.py 동형)."""
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="비교값이 올바른 숫자가 아닙니다.",
        ) from exc
    if not parsed.is_finite():
        raise HTTPException(
            status_code=400, detail="비교값이 올바른 숫자가 아닙니다.",
        )
    return parsed


def _to_domain_conditions(
    conditions: list[BacktestConditionIn],
) -> list[BacktestCondition]:
    """wire conditions → 도메인 BacktestCondition (threshold Decimal 파싱)."""
    return [
        BacktestCondition(
            factor=c.factor, op=c.op.value, threshold=_parse_threshold(c.value),
        )
        for c in conditions
    ]


def _resolve_costs(
    *, commission_bps: float | None, tax_bps: float | None,
) -> CostAssumptions:
    """거래비용 가정 — override 있으면 그 값, 없으면 엔진 default (ADR-0027 D2).

    float → Decimal 은 str 경유 (IEEE 754 잔차 회피 — _jcs / price_adjuster 의미론).
    """
    commission = (
        Decimal(str(commission_bps)) if commission_bps is not None
        else DEFAULT_COMMISSION_BPS
    )
    tax = Decimal(str(tax_bps)) if tax_bps is not None else DEFAULT_TAX_BPS
    return CostAssumptions(tax_bps=tax, commission_bps=commission)


def _decimal_or_none_str(value: Decimal | None) -> str | None:
    """Decimal → str (None 보존 — 외삽 금지, 사실대로 null)."""
    return str(value) if value is not None else None


@router.post("/backtest", response_model=BacktestResultOut)
async def execute_backtest(
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
    body: BacktestRequestIn,
) -> BacktestResultOut:
    """백테스트 실행 — PIT rebalance 순회 + freeze + 사실 통계 (ADR-0027).

    pack 을 user 격리 해소(빌트인/reference/custom)하여 엔진을 PIT repository 와
    조립, 동기 실행. freeze artifact(BacktestSnapshot)로 result_hash 산출 후 wire
    응답. disclaimer_required=true 고정(D4) + survivorship 디스클로저(D3).
    """
    # 1. 기간 상한 보호 (ADR-0027 — 동기 처리, 너무 무거우면 차단 + 보고).
    span_days = (body.end - body.start).days
    if span_days > _MAX_PERIOD_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"백테스트 기간이 상한({_MAX_PERIOD_DAYS}일)을 초과합니다. "
                "기간을 줄여 다시 시도하세요."
            ),
        )

    # 2. pack 해소 (빌트인/reference/custom — ADR-0027 D5).
    pack = _resolve_pack(
        pack_slug=body.pack_slug, pack_version=body.pack_version,
        user=user, custom_pack_repo=custom_pack_repo,
    )

    # 3. 조건 + 거래비용 도메인 변환.
    conditions = _to_domain_conditions(body.conditions)
    costs = _resolve_costs(
        commission_bps=body.cost_commission_bps, tax_bps=body.cost_tax_bps,
    )

    # 4. 엔진 실행 — stateless·결정적 (외부 부수효과 0).
    result: BacktestResult = run_backtest(
        pack=pack,
        conditions=conditions,
        start=body.start,
        end=body.end,
        frequency=body.rebalance.value,  # type: ignore[arg-type]
        stocks_repo=stocks_repo,
        price_repo=price_repo,
        financial_repo=financial_repo,
        evaluator=evaluator,
        corporate_action_repo=corporate_action_repo,
        market_cap_repo=market_cap_repo,
        treasury_repo=treasury_repo,
        macro_repo=macro_repo,
        cost_assumptions=costs,
    )

    # 5. freeze artifact (ADR-0027 D5) — data_versions 는 end 시점 batch_id 기준
    #    + custom pack 명시 주입 (custom pack content_hash 반영). end 를 as_of 로
    #    사용 — 백테스트가 본 마지막 데이터 시점.
    data_versions = collect_run_data_versions(body.end, session, pack=pack)
    snapshot = BacktestSnapshotBuilder.build(
        run_id=uuid4(),
        user_id=user.user_id,
        pack_slug=pack.pack_slug,
        pack_version=pack.version,
        pack_content_hash=pack.computed_hash,
        conditions=conditions,
        start=body.start,
        end=body.end,
        rebalance=body.rebalance.value,  # type: ignore[arg-type]
        cost_assumptions=costs,
        data_versions=data_versions,
    )

    # 6. wire 응답 조립 — Decimal 은 str (정밀도 보존). 평가 라벨 0 (D6).
    return BacktestResultOut(
        freeze=FreezeOut(
            result_hash=snapshot.result_hash,
            pack_content_hash=snapshot.pack_content_hash,
            krx_batch_id=snapshot.krx_batch_id,
            dart_batch_id=snapshot.dart_batch_id,
            rebalance=snapshot.rebalance,
            start=snapshot.start,
            end=snapshot.end,
            cost_assumptions=CostAssumptionsOut(
                tax_bps=str(costs.tax_bps),
                commission_bps=str(costs.commission_bps),
            ),
        ),
        stats=StatsOut(
            cagr=_decimal_or_none_str(result.cagr),
            cumulative_return=str(result.cumulative_return),
            mdd=str(result.mdd),
            volatility=_decimal_or_none_str(result.volatility),
            turnover=str(result.turnover),
        ),
        equity_curve=tuple(
            EquityPointOut(date=p.date, value=str(p.value))
            for p in result.equity_curve
        ),
        survivorship_complete=result.survivorship_complete,
        missing_price_ratio=str(result.missing_price_ratio),
        disclaimer_required=True,
    )
