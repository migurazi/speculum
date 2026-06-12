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

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies.auth import CurrentUserDep
from app.api.dependencies.repositories import (
    BatchRunRepoDep,
    CorporateActionRepoDep,
    CustomPackRepoDep,
    DividendRepoDep,
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
    BacktestReproduceIn,
    BacktestReproduceOut,
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
from app.services.factor_pack import DEFAULT_PACK, LoadedPack
from app.services.pack_registry import PackRegistry
from app.services.reproduce import reproduce_backtest
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
    dividend_repo: DividendRepoDep,
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
        # macro N+1 완화: raw macro_repo 를 넘기면 run_backtest 진입부가
        # CachingMacroIndicatorRepository 로 자동 래핑(시점 내 N종목 중복 제거).
        macro_repo=macro_repo,
        # M7 #4 (§2.10 break) — dividend / total return field 실연결. reproduce_
        # backtest 와 동일 배선해야 dividend-yield/total-return universe 필터가
        # silent N/A 아닌 같은 값 → equity_curve byte-동일 재현.
        dividend_repo=dividend_repo,
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
            # ADR-0033 D2 — self-contained 재현 입력(reproduce 시 pack 재로드 +
            # 같은 universe·데이터 시점 재현). snapshot 이 canonical 정렬·freeze 한 값.
            pack_slug=snapshot.pack_slug,
            pack_version=snapshot.pack_version,
            krx_batch_id=snapshot.krx_batch_id,
            dart_batch_id=snapshot.dart_batch_id,
            rebalance=snapshot.rebalance,
            start=snapshot.start,
            end=snapshot.end,
            cost_assumptions=CostAssumptionsOut(
                tax_bps=str(costs.tax_bps),
                commission_bps=str(costs.commission_bps),
            ),
            conditions=tuple(dict(c) for c in snapshot.conditions),
            data_versions=dict(snapshot.data_versions),
            # M1 — 산식 세대 식별자를 self-contained freeze 에 운반. client 가 보관해
            # reproduce 시 현재 엔진과 비교(engine_version_superseded 진단).
            engine_version=snapshot.engine_version,
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


@router.post("/backtest/reproduce", response_model=BacktestReproduceOut)
async def reproduce_backtest_endpoint(
    user: CurrentUserDep,
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
    body: BacktestReproduceIn,
) -> BacktestReproduceOut:
    """frozen backtest 재현 — frozen batch 기준 재실행 + equity_curve 동일 검증.

    screen `reproduce_run_endpoint` 의 backtest 등가물 (ADR-0033 D2~D4). self-
    contained freeze + baseline equity_curve 를 받아, frozen data_versions 의 pack
    키로 pack 을 재로드하고(ADR-0025 D5 — 운영 활성 pack 무조건 사용 금지) frozen
    krx/dart batch cutoff 를 주입하여 `run_backtest` 를 frozen 입력으로 재실행한다.
    재실행 equity_curve 와 baseline 의 byte-동일(`matches`)을 반환.

    user 비종속 (ADR-0021 D5):
        재현은 user fact 조회가 아니라 frozen batch_cutoff 만 사용한다. 인증 일관성
        (screen endpoint 동형)을 위해 CurrentUserDep 은 받되 재현 경로에서 user_id
        는 사용하지 않는다 — 공유 freeze 를 누구든 재현 가능.

    custom run (ADR-0025 D5):
        frozen slug 가 빌트인/reference 가 아니면 body.custom_pack_body(export 동봉
        봉인 pack body)로 재구성 — user/DB 무관 자기완결. 빌트인 run 은 None.

    오류 처리:
        - 필수 필드 누락/형식 불일치 → 422 (Pydantic validation).
        - frozen pack 재로드 불가/변조 → matches=False + reproduce_note (별도 404 X
          — 재현 실패 자체가 정보).
        - batch_id 가 batch_runs 에 없으면 reproduce_backtest 가 EXCLUDE_ALL_CUTOFF
          로 처리 → 빈/불일치 equity_curve (matches=False).
    """
    freeze = body.freeze

    # freeze.cost_assumptions(wire str) → 도메인 CostAssumptions 재구성. freeze 가
    # 운반한 정확한 거래비용 가정으로 재실행 (비용도 freeze 입력 — ADR-0027 D5).
    costs = CostAssumptions(
        tax_bps=Decimal(freeze.cost_assumptions.tax_bps),
        commission_bps=Decimal(freeze.cost_assumptions.commission_bps),
    )

    # baseline equity_curve → wire 형태((date isoformat, value str)) 튜플 — 재실행
    # 결과와 동일 표현으로 비교. EquityPointOut.date 는 date 객체라 isoformat() 화.
    baseline = tuple(
        (p.date.isoformat(), p.value) for p in body.equity_curve
    )

    # reproduce_backtest 재사용 — freeze.conditions 는 이미 {factor,op,value} dict
    # 라 _to_domain_conditions 불가, 서비스 함수가 역변환(threshold Decimal)한다.
    result = reproduce_backtest(
        pack_slug=freeze.pack_slug,
        pack_version=freeze.pack_version,
        conditions=freeze.conditions,
        start=freeze.start,
        end=freeze.end,
        rebalance=freeze.rebalance,
        cost_assumptions=costs,
        data_versions=freeze.data_versions,
        baseline_equity_curve=baseline,
        batch_run_repo=batch_run_repo,
        stocks_repo=stocks_repo,
        # frozen data_versions 에 pack 키(slug/version/content_hash)가 있으면 본
        # fallback 은 무시되고 _PACK_REGISTRY 가 frozen 값으로 재로드 (ADR-0025 D5).
        # pack 키 없는 구 freeze / 직접 호출만 DEFAULT_PACK 으로 fallback (하위호환).
        pack=DEFAULT_PACK,
        evaluator=evaluator,
        price_repo=price_repo,
        financial_repo=financial_repo,
        corporate_action_repo=corporate_action_repo,
        market_cap_repo=market_cap_repo,
        treasury_repo=treasury_repo,
        macro_repo=macro_repo,
        # M7 #4 (§2.10 break) — dividend / total return field 실연결. live
        # backtest 와 동일 배선해야 universe 필터가 byte-동일 재현.
        dividend_repo=dividend_repo,
        pack_registry=_PACK_REGISTRY,
        custom_pack_body=body.custom_pack_body,
        # M1 — freeze 의 산식 세대(구 freeze 는 None). 현재 엔진과 다르면 benign
        # 세대 bump 진단(engine_version_superseded).
        frozen_engine_version=freeze.engine_version,
    )

    # 재현 결과 안내(note 텍스트) — matches 의미 + 재로드 실패/세대 bump 사유 (screen
    # reproduce 동형 + M1 세대 진단). 분기 우선순위: 성공 > pack 변조/부재 > 산식 세대
    # bump > 데이터 드리프트. **note 텍스트만** 이 우선순위로 분기하며, 구조화 필드
    # engine_version_superseded 는 matches 와 독립으로 항상 truthful 하게 전달된다
    # (matches=True 라도 세대가 다르면 True — 분할 없는 backtest 는 세대 bump 에도
    # byte-동일 재현되므로 "성공 note + superseded=True" 가 정상·비모순).
    if result.matches:
        note = (
            "재현 성공 — frozen batch 기준 equity_curve byte-동일"
            " (§2.10 Reproducibility)."
        )
    elif result.reproduce_note is not None:
        # frozen pack 재로드 불가/변조 — 구체 사유 (ADR-0025 D5).
        note = result.reproduce_note
    elif result.engine_version_superseded:
        # 산식 세대 bump(예: 보유수익률 보정 버그 수정 1.0→1.1)로 인한 benign
        # 불일치 — 데이터 변조/드리프트가 **아님**을 명시(M5 신선도 진단 false-alarm
        # 방지). frozen 산식으로 산출된 baseline 은 현재 엔진이 byte-동일 재생산 불가.
        note = (
            "재현 불일치 — 저장 당시 backtest 산식 세대(engine_version)가 현재와"
            " 달라(예: 산식 버그 수정) equity_curve 값이 바뀐 것이 **주된** 원인일"
            " 수 있습니다. 현재 엔진으로 재실행한 결과가 새 기준입니다. (세대 bump 와"
            " frozen batch 이후 데이터 정정이 공존할 수도 있음 — ADR-0035 D8 상속 한계.)"
        )
    else:
        note = (
            "재현 불일치 — data_versions 의 batch_id 에 해당 batch row 가 없거나"
            " (삭제/이전), frozen batch 이후 데이터·정책 변화가 원인일 수 있습니다."
        )

    return BacktestReproduceOut(
        matches=result.matches,
        reproduce_note=note,
        equity_curve=tuple(
            EquityPointOut(date=date.fromisoformat(d), value=v)
            for d, v in result.equity_curve
        ),
        # pack_tampered: 변조(content_hash 불일치)만 True. 부재는 False(M5 #3b).
        pack_tampered=result.pack_tampered,
        # M1 — 산식 세대 bump 진단(matches 와 독립 구조화 신호).
        engine_version_superseded=result.engine_version_superseded,
    )
