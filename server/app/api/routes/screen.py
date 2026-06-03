"""Screen 실행 endpoint — POST /api/screen.

ADR-0008 D6 의 API contract — Screener 의 조건 빌더 입력 + as_of + 결과.
실행만 — Save Run 은 POST /api/runs 별도 (oracle T26 결정 1).

M1 (조건 매칭):
- M0 stub (active universe 전체 반환) 를 제거하고, 사용자 conditions
  (factor/op/value) 를 종목별로 실평가하여 AND 필터링.
- T50 FieldProvider + FactorEvaluator 를 종목별로 주입 — stocks.py 의
  `_evaluate_display_factors` provider 생성 패턴 재사용 (동일 Dep + DbFieldProvider).
- N/A (is_na) 종목은 해당 조건 불충족 → 제외 (값 없는 종목이 조건을 만족한다고
  주장하지 않음 — 8 기둥 §2.1 Fidelity).
- 결과는 정규화 (6 자리·정렬·dedup) 된 결정적 종목코드.

성능 한계 (M1):
- live 평가는 universe (운영 ~2500 종목) × 조건 factor 수 × 종목별 DB fetch 라
  비용이 큼. 본 사이클은 **정확성 우선의 live 평가**로 구현. precompute
  (stock_snapshots) 기반 최적화는 별도 cycle (provider 가 종목별로 financial /
  price fetch 를 수행하므로 universe 가 커지면 N+1 fetch 누적). 종목 내 캐싱은
  DbFieldProvider 가 담당 (한 종목의 PER+PBR 이 market_cap_ex_treasury 공유).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException

from app.api.dependencies import NormalizedAsOfDep
from app.api.dependencies.repositories import (
    ActivePackDep,
    CorporateActionRepoDep,
    FactorEvaluatorDep,
    FinancialRepoDep,
    MacroIndicatorRepoDep,
    MarketCapRepoDep,
    PriceRepoDep,
    SessionOrNoneDep,
    StocksRepoDep,
    TreasurySharesRepoDep,
)
from app.repositories.batch_run_repository import BatchCutoff
from app.repositories.pit_protocols import (
    CorporateActionRepository,
    FinancialRepository,
    MacroIndicatorRepository,
    MarketCapRepository,
    PriceRepository,
    TreasurySharesRepository,
)
from app.repositories.stocks_master_repository import StocksMasterRepository
from app.schemas.screen import ConditionIn, OpEnum, ScreenResultOut, ScreenRunQueryIn
from app.services.db_field_provider import DbFieldProvider
from app.services.factor_evaluator import EvaluationResult, FactorEvaluator
from app.services.factor_pack import LoadedPack
from app.services.screen_run import normalize_stock_codes
from app.services.snapshot_versions import collect_run_data_versions

router = APIRouter(prefix="/api", tags=["screen"])


@router.post("/screen", response_model=ScreenResultOut)
async def execute_screen(
    as_of: NormalizedAsOfDep,
    stocks_repo: StocksRepoDep,
    session: SessionOrNoneDep,
    evaluator: FactorEvaluatorDep,
    pack: ActivePackDep,
    price_repo: PriceRepoDep,
    financial_repo: FinancialRepoDep,
    corporate_action_repo: CorporateActionRepoDep,
    market_cap_repo: MarketCapRepoDep,
    treasury_repo: TreasurySharesRepoDep,
    macro_repo: MacroIndicatorRepoDep,
    body: ScreenRunQueryIn,
) -> ScreenResultOut:
    """Screener 실행 — Save 안 함, 결과만 반환.

    M1: condition match 실평가. active universe 의 각 종목에 대해 종목별
    `DbFieldProvider` 를 만들어 conditions 의 factor 를 `FactorEvaluator` 로 산출,
    op/value 로 비교. 모든 condition 통과 (AND) 한 종목코드만 반환. N/A factor
    종목은 해당 조건 불충족으로 제외.

    `data_versions` 는 호출 시점 freeze — 클라이언트가 그대로 /api/runs 저장
    POST 에 전달하여 두 호출 간 정책 변경 race 차단 (oracle Risk-X2).

    AC-F-03 (Screener 결과 — 1만 종목 가상화) 의 backend half — frontend 가
    response 의 result_codes 로 가상화 list 렌더.

    Raises:
        HTTPException 400: condition.factor 가 active pack 에 없거나
            (사용자가 미존재 factor 참조), condition.value 가 Decimal 로 파싱
            불가 (잘못된 비교값).
    """
    # 0. 자산군 사전 필터 (ADR-0023 D7) — None 이면 보통주만 (기본 동작 불변).
    #    canonical 정렬 + dedup 으로 결정성 (screen 결과 / Save Run hash 일관).
    selected_security_types = _canonical_security_types(body.security_types)

    # 1. 조건 매칭 — screen 과 Save Run (runs.py) 의 단일 경로. universe 의 각
    #    종목을 자산군 필터 + conditions 로 AND 필터링한 결정적 종목코드.
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
        macro_repo=macro_repo,
        security_types=selected_security_types,
    )

    # 2. data_versions freeze — 호출 시점. M1 T48b: 정책 11 키 + (SQL session
    #    있으면) as_of 시점 최신 성공 batch 의 batch_id 2 키. 클라이언트가 본
    #    결과를 그대로 /api/runs 저장 POST 에 전달하여 두 호출 간 정책/배치 변경
    #    race 차단 (oracle Risk-X2). Fake-only mode 는 정책-only 11 키.
    data_versions = dict(collect_run_data_versions(as_of.value, session))

    return ScreenResultOut(
        result_codes=result_codes,
        total=len(result_codes),
        data_versions=data_versions,
    )


# =============================================================================
# 자산군 사전 필터 — ADR-0023 D7
# =============================================================================

def _canonical_security_types(
    security_types: list[str] | None,
) -> tuple[str, ...]:
    """요청 security_types → canonical tuple (정렬 + dedup, default common).

    ADR-0023 D7 — None/빈 list 면 `("common",)` (보통주만 — 기본 동작 불변).
    명시 시 사전식 ascending + dedup. screen_run._canonicalize_query 의 자산군
    정규화와 동일 규칙 — screen 필터와 Save Run 의 result_hash 입력이 byte 일관
    (같은 선택 → 같은 모집단 + 같은 hash). 미지원 값 검증은 schema validator
    (ScreenRunQueryIn._validate_security_types) 가 422 로 선차단.
    """
    if not security_types:
        return ("common",)
    return tuple(sorted(set(security_types)))


# =============================================================================
# Condition 평가 — testable pure helpers
# =============================================================================

# OpEnum → Decimal 비교 연산자 매핑. float 금지 (Decimal 결정성 — evaluator /
# price_adjuster 와 동일 의미론). `=` / `!=` 도 Decimal 동치 비교.
_OP_COMPARATORS = {
    OpEnum.LT: lambda lhs, rhs: lhs < rhs,
    OpEnum.LE: lambda lhs, rhs: lhs <= rhs,
    OpEnum.EQ: lambda lhs, rhs: lhs == rhs,
    OpEnum.GE: lambda lhs, rhs: lhs >= rhs,
    OpEnum.GT: lambda lhs, rhs: lhs > rhs,
    OpEnum.NE: lambda lhs, rhs: lhs != rhs,
}


class _CompiledCondition:
    """검증·파싱 완료된 조건 — universe loop 의 핫패스 재사용.

    pack lookup 과 Decimal(value) 파싱은 모든 종목에서 동일하므로 loop 밖에서
    한 번만 수행 (factor dict + 비교값 Decimal + op). 종목마다 factor dict 재조회
    / value 재파싱 회피.

    Attributes:
        factor: pack body 의 factor dict (evaluator.evaluate 입력).
        op: OpEnum 연산자.
        threshold: 비교 기준값 (Decimal). condition.value 파싱 결과.
    """

    __slots__ = ("factor", "op", "threshold")

    def __init__(self, factor: dict, op: OpEnum, threshold: Decimal) -> None:
        self.factor = factor
        self.op = op
        self.threshold = threshold


def _compile_conditions(
    conditions: list[ConditionIn],
    factors_by_id: dict[str, dict],
) -> list[_CompiledCondition]:
    """conditions 를 검증·파싱하여 _CompiledCondition list 로 변환.

    universe loop 전에 한 번 호출 — 사용자 입력 오류 (미존재 factor / 비숫자
    value) 를 universe 평가 비용 발생 전에 fail-fast (400).

    Raises:
        HTTPException 400: factor 가 pack 에 없거나, value 가 Decimal 파싱 불가.
    """
    compiled: list[_CompiledCondition] = []
    for cond in conditions:
        factor = factors_by_id.get(cond.factor)
        if factor is None:
            # 사용자가 active pack 에 없는 factor canonical_id 참조 — 잘못된 조건.
            # factor 값을 echo 하지 않음 (ADR-0007 — request body sanitize).
            raise HTTPException(
                status_code=400,
                detail="알 수 없는 factor 입니다 (active pack 에 없음).",
            )
        threshold = _parse_decimal_value(cond.value)
        compiled.append(_CompiledCondition(factor, cond.op, threshold))
    return compiled


def _parse_decimal_value(value: str) -> Decimal:
    """condition.value (str) 를 Decimal 로 파싱. 비숫자면 400.

    float 경유 금지 — Decimal(str) 직접 파싱 (IEEE 754 drift 회피, evaluator 의
    Decimal 산술과 동일 의미론). NaN / Infinity 등 비유한값도 거부 (정상 비교
    불가). value 자체는 echo 하지 않음 (ADR-0007 sanitize).
    """
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="비교값이 올바른 숫자가 아닙니다.",
        ) from exc
    if not parsed.is_finite():
        # NaN / Infinity — Decimal 은 파싱하나 비교가 정의되지 않음 (NaN 비교는
        # 항상 False/예외). 잘못된 비교값으로 거부.
        raise HTTPException(
            status_code=400,
            detail="비교값이 올바른 숫자가 아닙니다.",
        )
    return parsed


def _passes_all_conditions(
    provider: DbFieldProvider,
    compiled: list[_CompiledCondition],
    evaluator: FactorEvaluator,
    *,
    as_of: date,
) -> bool:
    """한 종목 (provider) 이 모든 condition 을 통과하는지 (AND 결합).

    각 condition 의 factor 를 evaluator 로 산출:
    - N/A (is_na) → 해당 조건 불충족 (값 없는 종목이 조건을 만족한다고 주장하지
      않음 — Fidelity §2.1). 즉시 False (AND 단축 평가).
    - 값 있으면 op 별 Decimal 비교. 한 condition 이라도 불충족이면 False.

    모든 condition 통과 시만 True. conditions 는 schema 상 1~32 개 (비어있지
    않음) 이라 빈 list 로 인한 vacuous-true 미발생.
    """
    for cond in compiled:
        result: EvaluationResult = evaluator.evaluate(
            cond.factor, provider, as_of=as_of,
        )
        if result.is_na or result.value is None:
            # 값 없는 종목 — 해당 조건 불충족 → 제외.
            return False
        comparator = _OP_COMPARATORS[cond.op]
        if not comparator(result.value, cond.threshold):
            return False
    return True


def _build_provider(
    *,
    code: str,
    as_of: date,
    pack: LoadedPack,
    evaluator: FactorEvaluator,
    price_repo: PriceRepository,
    financial_repo: FinancialRepository,
    corporate_action_repo: CorporateActionRepository,
    market_cap_repo: MarketCapRepository,
    treasury_repo: TreasurySharesRepository,
    macro_repo: MacroIndicatorRepository | None = None,
    krx_batch_cutoff: BatchCutoff | None = None,
    dart_batch_cutoff: BatchCutoff | None = None,
) -> DbFieldProvider:
    """종목별 DbFieldProvider 생성 — stocks.py `_evaluate_display_factors` 패턴 재사용.

    종목 (code) 별 1 인스턴스 — 캐싱은 종목 내 (한 종목의 PER+PBR 이
    market_cap_ex_treasury 공유). factor_pack + evaluator 주입으로 파생 필드
    (market_cap_ex_treasury) 도 동일 evaluator 로 평가 (Option B, Fidelity 단일
    출처).

    `macro_repo` (M2 T81): ECOS field (ecos_base_rate / ecos_cpi) 해소용. None
    이면 정식 N/A (미주입 컨텍스트 — ECOS field 쓰는 조건이 없으면 무해).

    `krx_batch_cutoff` / `dart_batch_cutoff` (M1 T48c 재현): None (default) 이면
    라이브 screen (무필터). reproduce_run 이 frozen batch cutoff 를 주입하여 그
    시점 데이터로 재실행 (byte-동일 재현). thread-through 만 — DbFieldProvider 가
    price/market_cap→krx, financial/treasury→dart 로 분배.
    """
    return DbFieldProvider(
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
        krx_batch_cutoff=krx_batch_cutoff,
        dart_batch_cutoff=dart_batch_cutoff,
    )


def screen_active_codes(
    *,
    as_of: date,
    conditions: list[ConditionIn],
    stocks_repo: StocksMasterRepository,
    pack: LoadedPack,
    evaluator: FactorEvaluator,
    price_repo: PriceRepository,
    financial_repo: FinancialRepository,
    corporate_action_repo: CorporateActionRepository,
    market_cap_repo: MarketCapRepository,
    treasury_repo: TreasurySharesRepository,
    macro_repo: MacroIndicatorRepository | None = None,
    security_types: tuple[str, ...] | None = None,
    krx_batch_cutoff: BatchCutoff | None = None,
    dart_batch_cutoff: BatchCutoff | None = None,
) -> tuple[str, ...]:
    """active universe 를 자산군 사전 필터 + conditions 로 AND 필터링한 결정적 종목코드.

    POST /api/screen 과 POST /api/runs (Save Run) 의 **단일 조건 매칭 경로** —
    두 endpoint 가 동일 결과를 산출해야 Reproducibility (저장된 run = 스크린 결과,
    §2.10) 가 성립. 종목별 `DbFieldProvider` 로 factor 실평가, N/A 종목 제외,
    `normalize_stock_codes` 로 결정적 순서 (6 자리·정렬·dedup).

    `security_types` (ADR-0023 D7 — universe 자산군 사전 필터): None (default) 이면
    `("common",)` — 보통주만 (universe 확장이 기본 동작 불변, D4/D7). active
    universe 를 security_type 으로 사전 필터한 뒤 conditions 평가 (필터 외 자산군은
    조건 평가 자체를 안 함 — 우선주에 발행사-귀속 factor 가 N/A 로 자동 탈락하는
    것과 별개로, 모집단에서 명시 제외). 선택된 자산군은 호출자가 ScreenRunQuery 에
    실어 result_hash 입력 (재현 freeze).

    성능: universe (운영 ~2500) × 조건 factor × 종목별 DB fetch (모듈 docstring 의
    한계). 종목 내 캐싱만 (DbFieldProvider) — 종목 간 N+1 fetch 누적. precompute
    최적화는 별도 cycle.

    `krx_batch_cutoff` / `dart_batch_cutoff` (M1 T48c 재현): None (default) 이면
    라이브 screen (무필터 = 기존 동작). `reproduce_run` 이 frozen batch cutoff 를
    주입하여 그 시점 데이터로 재실행 — 저장된 run 의 result_codes 와 byte-동일
    재현 (§2.10).

    Raises:
        HTTPException 400: condition.factor 가 active pack 에 없거나 (사용자가
            미존재 factor 참조), condition.value 가 Decimal 로 파싱 불가.
    """
    factors_by_id = {f["canonical_id"]: f for f in pack.body["factors"]}
    compiled = _compile_conditions(conditions, factors_by_id)

    # ADR-0023 D7 — 자산군 사전 필터. None 이면 보통주만 (기본 동작 불변).
    allowed_security_types = security_types if security_types is not None else ("common",)

    matched: list[str] = []
    for record in stocks_repo.list_active(as_of=as_of):
        code = record.current_code
        if not code:
            # 폐지 lineage (current_code None) — fact 조회 키 부재. active 정의상
            # 미발생하나 방어. fact 없으면 모든 factor N/A → 어차피 제외.
            continue
        if record.security_type not in allowed_security_types:
            # ADR-0023 D7 — 선택 자산군 외 종목은 모집단에서 사전 제외.
            continue
        provider = _build_provider(
            code=code,
            as_of=as_of,
            pack=pack,
            evaluator=evaluator,
            price_repo=price_repo,
            financial_repo=financial_repo,
            corporate_action_repo=corporate_action_repo,
            market_cap_repo=market_cap_repo,
            treasury_repo=treasury_repo,
            macro_repo=macro_repo,
            krx_batch_cutoff=krx_batch_cutoff,
            dart_batch_cutoff=dart_batch_cutoff,
        )
        if _passes_all_conditions(provider, compiled, evaluator, as_of=as_of):
            matched.append(code)
    return normalize_stock_codes(matched)
