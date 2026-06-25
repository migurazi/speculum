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

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.api.dependencies import BrowseAsOfDep
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
from app.repositories.batch_run_repository import BatchCutoff
from app.repositories.caching_repositories import (
    CachingCorporateActionRepository,
    CachingDividendRepository,
    CachingFinancialRepository,
    CachingMacroIndicatorRepository,
    CachingMarketCapRepository,
    CachingPriceRepository,
    CachingTreasurySharesRepository,
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
from app.schemas.screen import ConditionIn, OpEnum, ScreenResultOut, ScreenRunQueryIn
from app.services.db_field_provider import DbFieldProvider
from app.services.factor_evaluator import EvaluationResult, FactorEvaluator
from app.services.factor_pack import LoadedPack
from app.services.screen_run import normalize_stock_codes
from app.services.snapshot_serve import try_serve_snapshot_values
from app.services.snapshot_versions import collect_run_data_versions
from app.services.total_return_adjuster import TotalReturnAdjuster

router = APIRouter(prefix="/api", tags=["screen"])

logger = logging.getLogger(__name__)

# dividend-의존 factor input — read-bypass serve 제외 대상 (C2-a, oracle 설계검토).
# dividend 정정은 dividend_batch_id(FSC) 가 현재 항상 ""(FSC 배치 미존재)라
# data_versions equality 가 정정을 못 잡는다 → stale snapshot 을 serve 하면
# snapshot result_codes ≠ 동시점 live result_codes(§2.10 위반 · save-live 정합성
# 붕괴). 따라서 dividend-의존 input 을 가진 factor 가 조건에 **하나라도** 있으면
# screen serve 를 통째로 비활성화(전부 live)한다. CA(자본거래) 는 DART ingest 라
# dart_batch_id 가 정정을 포착 → 안전(제외 불요). builtin 에서 본 input 을 쓰는
# factor 는 dividend-yield:trailing-annual, price-return:total-annual.
_DIVIDEND_DEPENDENT_INPUTS: frozenset[str] = frozenset({
    "dividend_per_share_trailing_annual",
    "total_return_trailing_1y",
})


@router.post("/screen", response_model=ScreenResultOut)
async def execute_screen(
    as_of: BrowseAsOfDep,
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
    dividend_repo: DividendRepoDep,
    snapshot_repo: SnapshotRepoDep,
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

    # ⓓ slice 1b read-bypass — serve-vs-recompute 판정용 현재 freeze fingerprint.
    #    **pack-aware**(collect_run_data_versions 3-arg, C1) — producer
    #    (snapshot_daily.py) 가 동일 pack 인자로 freeze 하므로 builtin/custom pack 이
    #    factor_pack_content_hash 로 격리(custom pack screen → mismatch → 전부 live).
    #    session None(Fake-only) 이면 None → serve 비활성(전부 live, 회귀 0). 이 값은
    #    serve 판정 전용 — **응답 freeze(아래 2-arg)와 분리**(§2.10 result_hash 불변).
    serve_data_versions = (
        dict(collect_run_data_versions(as_of.value, session, pack))
        if session is not None
        else None
    )

    # 1. 조건 매칭 — screen 과 Save Run (runs.py) 의 단일 경로. universe 의 각
    #    종목을 자산군 필터 + conditions 로 AND 필터링한 결정적 종목코드.
    result = screen_active_codes(
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
        dividend_repo=dividend_repo,
        security_types=selected_security_types,
        snapshot_repo=snapshot_repo,
        current_data_versions=serve_data_versions,
    )

    # 2. data_versions freeze — 호출 시점. M1 T48b (M7 #5 키 set 확장 반영):
    #    정책 14 키 + (SQL session 있으면) as_of 시점 최신 성공 batch 의 batch_id
    #    3 키 (krx/dart/dividend) = 17 키. 클라이언트가 본 결과를 그대로 /api/runs
    #    저장 POST 에 전달하여 두 호출 간 정책/배치 변경 race 차단 (oracle Risk-X2).
    #    Fake-only mode 는 정책-only 14 키.
    data_versions = dict(collect_run_data_versions(as_of.value, session))

    # 결과 요약 INFO 로깅 — 운영 중 "0건" 의 원인(as_of 인지 na_excluded 인지)을
    # 파일 로그(server/logs/speculum.log)에서 바로 진단하도록. total=0 이고
    # na_excluded 가 universe 에 근접하면 데이터 부재, na_excluded=0 이면 조건
    # 자체가 0건(§2.1 Fidelity 의 두 케이스 구별).
    logger.info(
        "screen as_of=%s total=%d universe=%d na_excluded=%d",
        as_of.value,
        len(result.result_codes),
        result.universe_size,
        result.na_excluded_count,
    )

    return ScreenResultOut(
        result_codes=result.result_codes,
        total=len(result.result_codes),
        universe_size=result.universe_size,
        na_excluded_count=result.na_excluded_count,
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

# =============================================================================
# 3-state 종목 평가 결과 — 투명성 집계용 (S3, §2.1 Fidelity)
# =============================================================================

class _ConditionOutcome(Enum):
    """한 종목의 조건 평가 결과 — 3-state (투명성 집계용, S3).

    스크리너가 "0건 매칭"과 "데이터 부재로 전부 제외"를 구별하지 못하던 §2.1
    Fidelity 위반을 해소하기 위해 도입. result_codes/result_hash 는 PASS 집합만
    으로 이전과 동일하게 계산(§2.10 byte-동일 불변) — Outcome 은 additive 집계용.

    값:
        PASS         — 전 조건 통과 → result_codes 에 포함.
        EXCLUDED_NA  — 첫 실패 조건이 NA(데이터 부재) → na_excluded_count += 1.
        EXCLUDED_FAIL — 첫 실패 조건이 임계 비교 실패(데이터 있으나 미충족).
    """

    PASS = "pass"
    EXCLUDED_NA = "na"
    EXCLUDED_FAIL = "fail"


@dataclass(frozen=True)
class ScreenCodesResult:
    """screen_active_codes 반환값 — 결정적 종목코드 + 투명성 집계.

    Attributes:
        result_codes: normalize_stock_codes 적용 결정적 종목코드 tuple.
            **§2.10 byte-동일 불변** — 이 값만으로 result_hash 계산.
        universe_size: 자산군 사전 필터(ADR-0023 D7) 후 실제 평가 대상 모집단 크기.
        na_excluded_count: factor NA(데이터 부재)로 탈락한 종목 수.
            임계 비교 실패(데이터 있으나 조건 불충족) 종목은 **미포함**.
    """

    result_codes: tuple[str, ...]
    universe_size: int
    na_excluded_count: int


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
        factor_uuid: factor 의 UUID (read-bypass snapshot 키 — `_compile_conditions`
            에서 1 회 파싱, 핫패스 재파싱 회피, oracle M1).
    """

    __slots__ = ("factor", "factor_uuid", "op", "threshold")

    def __init__(
        self, factor: dict, op: OpEnum, threshold: Decimal,
        factor_uuid: UUID | None,
    ) -> None:
        self.factor = factor
        self.op = op
        self.threshold = threshold
        # factor_uuid: pack body factor 는 항상 uuid 보유. 부재(최소 factor dict
        # 단위테스트)면 None → serve-ineligible(아래 _conditions_serve_eligible)로
        # serve 비활성 → live(oracle Q5 — 비결정 uuid4 대신 결정적 None, uuid 누락
        # pack 을 silent permanent-live 로 masking 하지 않고 명시 serve 비활성).
        self.factor_uuid = factor_uuid


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
        # factor uuid 1 회 파싱 — read-bypass snapshot 키(oracle M1, 핫패스 재파싱
        # 회피). pack body factor 는 항상 uuid 보유. 부재(최소 factor dict 단위
        # 테스트)면 None → serve-ineligible(결정적, oracle Q5 — uuid4 비결정/masking 회피).
        raw_uuid = factor.get("uuid")
        factor_uuid = UUID(raw_uuid) if raw_uuid is not None else None
        compiled.append(
            _CompiledCondition(factor, cond.op, threshold, factor_uuid),
        )
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
) -> _ConditionOutcome:
    """한 종목 (provider) 이 모든 condition 을 통과하는지 — 3-state 반환.

    AND 단축 평가: 첫 실패 조건이 결과를 결정한다.
    - N/A (is_na or value None) → EXCLUDED_NA (데이터 부재, §2.1 Fidelity).
    - 임계 비교 실패 (데이터 있으나 조건 불충족) → EXCLUDED_FAIL.
    - 전 조건 통과 → PASS.

    EXCLUDED_NA 집계로 "0건 매칭" vs "데이터 부재로 전부 제외"를 클라이언트가
    구별 가능 (S3 투명성, §2.1 위반 해소). result_codes 는 PASS 집합만으로
    이전과 byte-동일하게 유지 (§2.10 불변).
    """
    for cond in compiled:
        result: EvaluationResult = evaluator.evaluate(
            cond.factor, provider, as_of=as_of,
        )
        if result.is_na or result.value is None:
            # 데이터 부재 — 투명성 집계를 위해 NA 탈락으로 구분.
            return _ConditionOutcome.EXCLUDED_NA
        comparator = _OP_COMPARATORS[cond.op]
        if not comparator(result.value, cond.threshold):
            # 데이터는 있으나 임계값 불충족.
            return _ConditionOutcome.EXCLUDED_FAIL
    return _ConditionOutcome.PASS


def _conditions_serve_eligible(compiled: list[_CompiledCondition]) -> bool:
    """조건 factor 가 모두 read-bypass serve 적격인지 (C2-a — dividend 제외).

    조건 factor 중 **하나라도** dividend-의존 input(`_DIVIDEND_DEPENDENT_INPUTS`)
    을 formula.inputs 에 가지면 False → 호출자가 serve 비활성(전부 live). dividend
    정정이 data_versions equality 로 안 잡혀(dividend_batch_id 항상 "") stale serve
    가 §2.10·save-live 정합성을 깨므로(설계검토 C2-a), dividend 조건이 섞이면 안전
    측(live)으로 전부 보낸다. CA-의존(split 보정)은 dart_batch_id 가 정정 포착 → 적격.

    factor_uuid None(pack 에 uuid 부재 — 최소 factor dict)도 serve-ineligible: snapshot
    키가 없어 serve 불가하므로 명시 live(oracle Q5 — silent 마스킹 회피).

    한계 (oracle Q3 — forward-fragility): `formula.inputs` 는 **1-level leaf field**
    list(파생 field 는 중간 field 명만 — 예: PER 의 inputs=[market_cap_ex_treasury]).
    현재 빌트인 pack 의 dividend-sourced terminal field 는 _DIVIDEND_DEPENDENT_INPUTS
    의 2 개가 전부라 완전하나, 향후 factor 가 dividend field 를 **transitive**(top-level
    inputs 에 안 드러내고 간접 참조)로 의존하면 누락 가능. 빌트인 pack 변경은 in-repo
    라 회귀 테스트(test_screen_read_bypass)가 현 invariant 를 잠금. custom pack 은
    content_hash 불일치(C1)로 어차피 전부 live → transitive 위험 무관.
    """
    for cond in compiled:
        if cond.factor_uuid is None:
            return False
        inputs = cond.factor.get("formula", {}).get("inputs", [])
        if _DIVIDEND_DEPENDENT_INPUTS.intersection(inputs):
            return False
    return True


def _passes_all_conditions_from_snapshot(
    compiled: list[_CompiledCondition],
    served: Mapping[UUID, Decimal | None],
) -> _ConditionOutcome:
    """serve 된 snapshot value 로 한 종목의 condition 평가 — 3-state 반환.

    live `_passes_all_conditions` 와 **동일 비교·동일 AND short-circuit** — 차이는
    `evaluator.evaluate` 대신 precompute snapshot value 를 쓴다는 점뿐. snapshot
    value None(미산정)은 live 의 `is_na`(invariant: is_na ⟺ value None)와 동일하게
    EXCLUDED_NA. 값 있으면 `_OP_COMPARATORS` Decimal 비교(snapshot 은 str(Decimal)
    무손실 round-trip 이라 live Decimal 과 byte-동일 비교 결과).

    3-state 의미는 live `_passes_all_conditions` 와 동일 — EXCLUDED_NA/EXCLUDED_FAIL
    구분으로 투명성 집계(S3, §2.1). result_codes 는 PASS 집합만이라 byte-동일 불변.

    전제: `served` 는 `try_serve_snapshot_values` 결과(전 조건 factor uuid hit) —
    호출자가 serve 가능(not None)을 확인한 뒤 호출하므로 각 cond.factor_uuid 가
    served 에 존재.
    """
    for cond in compiled:
        value = served.get(cond.factor_uuid)
        if value is None:
            # 미산정(live is_na 와 동일 의미) → NA 탈락으로 구분.
            return _ConditionOutcome.EXCLUDED_NA
        comparator = _OP_COMPARATORS[cond.op]
        if not comparator(value, cond.threshold):
            return _ConditionOutcome.EXCLUDED_FAIL
    return _ConditionOutcome.PASS


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
    dividend_repo: DividendRepository | None = None,
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
        # M7 #4 — dividend / total return field 실연결. dividend_repo None 시 두
        # field 정식 N/A(하위호환). fix5 both-or-neither: dividend_repo None 이면
        # adjuster 도 None (DbFieldProvider assert 와 일관).
        dividend_repo=dividend_repo,
        total_return_adjuster=(
            TotalReturnAdjuster() if dividend_repo is not None else None
        ),
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
    dividend_repo: DividendRepository | None = None,
    security_types: tuple[str, ...] | None = None,
    krx_batch_cutoff: BatchCutoff | None = None,
    dart_batch_cutoff: BatchCutoff | None = None,
    snapshot_repo: StockSnapshotRepository | None = None,
    current_data_versions: Mapping[str, str] | None = None,
) -> ScreenCodesResult:
    """active universe 를 자산군 사전 필터 + conditions 로 AND 필터링 — ScreenCodesResult 반환.

    POST /api/screen 과 POST /api/runs (Save Run) 의 **단일 조건 매칭 경로** —
    두 endpoint 가 동일 결과를 산출해야 Reproducibility (저장된 run = 스크린 결과,
    §2.10) 가 성립. 종목별 `DbFieldProvider` 로 factor 실평가, N/A 종목 제외,
    `normalize_stock_codes` 로 결정적 순서 (6 자리·정렬·dedup).

    반환 `ScreenCodesResult`:
    - `result_codes`: 조건 통과 결정적 종목코드 (§2.10 byte-동일 보존 — 호출자가
      result_hash 입력으로 사용, .result_codes 로 unwrap).
    - `universe_size`: 자산군 사전 필터 후 실평가 모집단 크기 (투명성 S3).
    - `na_excluded_count`: factor NA(데이터 부재)로 탈락한 종목 수 — 임계 비교 실패
      종목 제외, "0건 매칭" vs "데이터 부재로 전부 제외" 구별 가능 (§2.1 Fidelity).

    `security_types` (ADR-0023 D7 — universe 자산군 사전 필터): None (default) 이면
    `("common",)` — 보통주만 (universe 확장이 기본 동작 불변, D4/D7). active
    universe 를 security_type 으로 사전 필터한 뒤 conditions 평가 (필터 외 자산군은
    조건 평가 자체를 안 함 — 우선주에 발행사-귀속 factor 가 N/A 로 자동 탈락하는
    것과 별개로, 모집단에서 명시 제외). 선택된 자산군은 호출자가 ScreenRunQuery 에
    실어 result_hash 입력 (재현 freeze).

    성능: universe (운영 ~2500) × 조건 factor × 종목별 DB fetch (모듈 docstring 의
    한계). 종목 내 캐싱 (DbFieldProvider) + **macro_indicator 는 종목-무관이라
    request-scoped CachingMacroIndicatorRepository 로 N×M → M 축약** + **price/
    market_cap 은 루프 전 1회 bulk prime(CachingPriceRepository /
    CachingMarketCapRepository)으로 종목별 N round-trip → 청크 몇 회 축약** (아래
    배선, oracle 설계검토 A2). financial/treasury 도 동일 bulk prime
    (CachingFinancialRepository / CachingTreasurySharesRepository) 으로 종목 간
    N+1 제거 — raw vintage 적재 후 serve-time 해소(pit_resolution 공유 helper),
    cutoff 은 dart. stock_snapshots precompute 는 별도 cycle.

    `krx_batch_cutoff` / `dart_batch_cutoff` (M1 T48c 재현): None (default) 이면
    라이브 screen (무필터 = 기존 동작). `reproduce_run` 이 frozen batch cutoff 를
    주입하여 그 시점 데이터로 재실행 — 저장된 run 의 result_codes 와 byte-동일
    재현 (§2.10).

    `snapshot_repo` / `current_data_versions` (ⓓ slice 1b read-bypass): 둘 다 주입
    (default None = 전부 live = 회귀 0) + **live serve 경로일 때만** 종목별 평가를
    precompute snapshot lookup 으로 대체한다. serve 활성 조건(`serve_active`):
        - snapshot_repo not None and current_data_versions not None(미주입/Fake→live),
        - krx_batch_cutoff is None and dart_batch_cutoff is None(reproduce 는 cutoff
          주입 → serve 차단, byte-동일 재현 보장),
        - 조건 factor 가 전부 dividend-비의존(C2-a — `_conditions_serve_eligible`).
    serve 활성 시 종목별 `try_serve_snapshot_values`(all-or-nothing: 전 조건 factor
    uuid hit + data_versions 일치)로 serve 시도 → 성공이면 `_passes_all_conditions_
    from_snapshot` 로 판정(provider 미생성·평가 skip), miss/stale 이면 기존 live
    경로 fallback. snapshot value 와 live evaluate 결과는 byte-동일(동일 evaluator·
    str(Decimal) 무손실)이라 serve result_codes == live result_codes(§2.10 불변).
    current_data_versions 는 pack-aware(C1 — 호출부가 pack 인자로 계산)라 custom/
    다른 pack 은 자동 stale → live.

    Raises:
        HTTPException 400: condition.factor 가 active pack 에 없거나 (사용자가
            미존재 factor 참조), condition.value 가 Decimal 로 파싱 불가.
    """
    factors_by_id = {f["canonical_id"]: f for f in pack.body["factors"]}
    compiled = _compile_conditions(conditions, factors_by_id)

    # N+1 완화 (precompute layer 1차) — macro_indicator 는 종목-무관(indicator_id
    # 단위)인데 종목마다 fetch_latest 가 반복돼 N×M 동일 쿼리가 발생한다. screen
    # 1회 lifetime 의 request-scoped 캐시로 감싸 (indicator_id, as_of) 당 1회로
    # 축약(N×M → M). PIT 불변이라 결과 무변경(caching_repositories docstring).
    # per-code fact(가격·시총·재무·자사주)의 bulk prime 은 아래에 배선(price/
    # market_cap/financial/treasury). stock_snapshots precompute 는 테이블 신설이
    # 필요한 별도 cycle.
    # 이중 래핑 방어 — 호출자가 이미 캐싱 래퍼를 넘겼으면 재래핑 skip(무의미한
    # 캐시 중첩 회피, idempotent).
    if macro_repo is not None and not isinstance(
        macro_repo, CachingMacroIndicatorRepository
    ):
        macro_repo = CachingMacroIndicatorRepository(macro_repo)

    # ADR-0023 D7 — 자산군 사전 필터. None 이면 보통주만 (기본 동작 불변).
    allowed_security_types = security_types if security_types is not None else ("common",)

    # universe 를 1회 materialize 후 사전 필터(폐지 lineage current_code None 제외 +
    # ADR-0023 D7 자산군 필터). 폐지 lineage 는 active 정의상 미발생하나 방어 —
    # fact 조회 키 부재라 어차피 모든 factor N/A 로 제외됐을 것.
    universe_records = [
        r
        for r in stocks_repo.list_active(as_of=as_of)
        if r.current_code and r.security_type in allowed_security_types
    ]
    universe_codes = [r.current_code for r in universe_records]

    # per-code N+1 완화 (macro 와 동일 위치·정신, oracle 설계검토 A2) — price/
    # market_cap 은 종목마다 값이 달라 memoize 가 아닌 **루프 전 1회 bulk prime**
    # 으로 종목별 개별 쿼리 N 회를 청크 쿼리 몇 회로 축약. 단일 as_of 라 캐시
    # 수명=이 screen 1회. 이중 래핑 방어(idempotent). prime window 밖/다른
    # (as_of,cutoff) 요청은 inner 위임 → 결과 byte-불변(§2.10 재현성).
    cached_price = (
        price_repo
        if isinstance(price_repo, CachingPriceRepository)
        else CachingPriceRepository(price_repo)
    )
    cached_price.prime(universe_codes, as_of=as_of, batch_cutoff=krx_batch_cutoff)
    cached_market_cap = (
        market_cap_repo
        if isinstance(market_cap_repo, CachingMarketCapRepository)
        else CachingMarketCapRepository(market_cap_repo)
    )
    cached_market_cap.prime(
        universe_codes, as_of=as_of, batch_cutoff=krx_batch_cutoff,
    )
    # financial/treasury 도 동일 루프 전 1회 bulk prime (oracle 설계검토 — price 와
    # 동형). 단 supersede chain 해소가 serve-time 이라 raw vintage 적재(account 가
    # serve-time 가변) + 전체 vintage(date 사전필터 없음, C1). cutoff 은 **dart**
    # (DART 출처) — krx 와 혼동 시 캐시 miss 로 전부 inner 위임(perf 0). 이중 래핑
    # 방어(idempotent). prime 외 키/미prime 요청은 inner 위임 → byte-불변(§2.10).
    cached_financial = (
        financial_repo
        if isinstance(financial_repo, CachingFinancialRepository)
        else CachingFinancialRepository(financial_repo)
    )
    cached_financial.prime(
        universe_codes, as_of=as_of, batch_cutoff=dart_batch_cutoff,
    )
    cached_treasury = treasury_repo
    if treasury_repo is not None:
        cached_treasury = (
            treasury_repo
            if isinstance(treasury_repo, CachingTreasurySharesRepository)
            else CachingTreasurySharesRepository(treasury_repo)
        )
        cached_treasury.prime(
            universe_codes, as_of=as_of, batch_cutoff=dart_batch_cutoff,
        )
    # corporate_action 도 동일 bulk prime — 종목별 fetch_actions(adjusted-price
    # factor 의 split/dividend/buyback chain 보정)의 per-code N+1 제거
    # (CachingCorporateActionRepository, backtest 와 동형). **batch_cutoff 없음**
    # (CA cutoff freeze 불가, ADR-0033 D4)
    # 이라 prime/serve 모두 (as_of) 키 — reproduce 도 CA 는 원래 미freeze라 byte-불변.
    # 이중 래핑 방어. snapshot precompute 배치도 동일 prime → screen 과 대칭(2a M1 해소).
    cached_ca = corporate_action_repo
    if corporate_action_repo is not None:
        cached_ca = (
            corporate_action_repo
            if isinstance(corporate_action_repo, CachingCorporateActionRepository)
            else CachingCorporateActionRepository(corporate_action_repo)
        )
        cached_ca.prime(universe_codes, as_of=as_of)
    # dividend 도 동일 bulk prime — total_return field(배당 재투자 §2.4)의 종목별
    # fetch_dividends per-code N+1 제거(CA 와 동형). batch_cutoff 없음(dividend
    # cutoff freeze 불가, ADR-0033 D4) → (as_of) 키. **None 가드** — dividend_repo
    # None 이면 래핑 안 함(both-or-neither: dividend None → adjuster None 보존,
    # _build_provider 가 처리). 이중 래핑 방어(idempotent). raw 적재 후 serve-time
    # 해소(resolve_dividends 공유 helper)라 byte-동일(§2.10 — equity_curve 불변).
    cached_dividend = dividend_repo
    if dividend_repo is not None:
        cached_dividend = (
            dividend_repo
            if isinstance(dividend_repo, CachingDividendRepository)
            else CachingDividendRepository(dividend_repo)
        )
        cached_dividend.prime(universe_codes, as_of=as_of)

    # read-bypass serve 활성 판정 (ⓓ slice 1b) — live 경로 + repo/dv 주입 + 조건이
    # dividend-비의존(C2-a)일 때만. reproduce(cutoff 주입)는 serve 차단(byte-동일
    # 재현). 비활성이면 serve_uuids 미사용 → 전부 기존 live(회귀 0).
    serve_active = (
        snapshot_repo is not None
        and current_data_versions is not None
        and krx_batch_cutoff is None
        and dart_batch_cutoff is None
        and _conditions_serve_eligible(compiled)
    )
    serve_uuids = (
        frozenset(c.factor_uuid for c in compiled) if serve_active else frozenset()
    )

    matched: list[str] = []
    na_count = 0  # factor NA(데이터 부재)로 탈락한 종목 수 (투명성 S3, §2.1).
    for record in universe_records:
        code = record.current_code
        if serve_active:
            # 종목별 all-or-nothing serve 시도. 전 조건 factor hit + data_versions
            # 일치면 평가 skip(provider 미생성) — snapshot value 로 조건 판정.
            served = try_serve_snapshot_values(
                code,
                as_of=as_of,
                factor_uuids=serve_uuids,
                snapshot_repo=snapshot_repo,
                current_data_versions=current_data_versions,
            )
            if served is not None:
                outcome = _passes_all_conditions_from_snapshot(compiled, served)
                if outcome is _ConditionOutcome.PASS:
                    matched.append(code)
                elif outcome is _ConditionOutcome.EXCLUDED_NA:
                    na_count += 1
                continue  # serve 성공 — live 경로 skip.
            # served None(miss/stale) → 아래 live 평가 fallback.
        provider = _build_provider(
            code=code,
            as_of=as_of,
            pack=pack,
            evaluator=evaluator,
            price_repo=cached_price,
            financial_repo=cached_financial,
            corporate_action_repo=cached_ca,
            market_cap_repo=cached_market_cap,
            treasury_repo=cached_treasury,
            macro_repo=macro_repo,
            dividend_repo=cached_dividend,
            krx_batch_cutoff=krx_batch_cutoff,
            dart_batch_cutoff=dart_batch_cutoff,
        )
        outcome = _passes_all_conditions(provider, compiled, evaluator, as_of=as_of)
        if outcome is _ConditionOutcome.PASS:
            matched.append(code)
        elif outcome is _ConditionOutcome.EXCLUDED_NA:
            na_count += 1
    return ScreenCodesResult(
        result_codes=normalize_stock_codes(matched),
        universe_size=len(universe_records),
        na_excluded_count=na_count,
    )
