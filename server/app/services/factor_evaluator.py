"""Factor Formula Evaluator — Factor pack 의 AST 를 산출값으로 evaluate.

T22 의 자연스러운 완성. `factor_pack.py` 가 정의·검증·hash 만 했고, 본 모듈이
실제 산출. PIT-aware FieldProvider 가 입력 fact 를 공급 → AST visitor 가 산출값
또는 N/A 결과 반환.

설계 원칙 (oracle 자문 P0 반영):

1. **Op-별 strict dispatcher** — schema 의 `exprOp` 가 모든 인자 필드를 optional
   허용하는 ambiguity 보완. 각 op 의 인자 모양 (binary / variadic / quarterly)
   을 evaluator 가 strict 검증. 위반 시 `MalformedFormulaError`.
2. **N/A propagation** — IEEE 754 NaN 과 유사: 한 번 N/A 면 root 까지 단조. 최초
   N/A 의 `na_reason` 보존 (downstream debug 가능). Silent 0 fill 절대 금지.
3. **Decimal 결정성** — const 의 `Decimal(str(...))` 변환 + `localcontext`
   (prec=28, ROUND_HALF_EVEN). T20 / price_adjuster 와 동일 의미론.
4. **Pack 무결성 vs 데이터 결손 이분법**:
   - 알 수 없는 op / 인자 모양 위반 / unknown field → `MalformedFormulaError` /
     `UnknownFieldError` raise (Sentry alert 대상)
   - division by zero / 입력 None / 분기 series 결손 → N/A 반환 (운영 일상)
5. **Reproducibility freeze** — `EVALUATOR_POLICY_VERSION = "1.0"` 을 결과에
   보존. Screen Run snapshot 의 `data_versions.evaluator_version` 입력.
6. **PIT invariant 약한 강제** — `FieldProvider` Protocol 에 `as_of` attribute
   요구. `evaluate(as_of=...)` 와 mismatch 시 `MalformedFormulaError` (호출자
   misuse 차단).
7. **`ratio_pct` raw ratio 반환** — `×100` 는 표시 layer 책임 (ADR-0002 D6 의
   unit 주석 일관). EvaluatorPolicy v1.0 의 명시 결정.
8. **`sum_last_n_quarters` strict** — n 분기 모두 존재 시만 합산. 결손 시 N/A.
   partial mode 는 별도 op (M2+).

관련 ADR / 문서:
- ADR-0002 D5 (stock_snapshots schema + inputs JSONB), D6 (Pack JSON formula AST)
- ADR-0008 D7 (Screen Run snapshot freeze — evaluator_version 입력)
- `factor-pack-v1.json` lines 144-223 (AST schema)
- 8 기둥 §2.1 Fidelity (inputs_used 보존), §2.10 Reproducibility

Out-of-scope:
- Schema v2 의 op-별 oneOf 분리 (C1 장기, M2 시점)
- Unit propagation 검증 (`mul(krw, ratio) → krw` 등 type rules) — community
  pack (M2) 시점
- Series 평균 별도 op (`avg_series`) — M2+
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, DivisionByZero, InvalidOperation, localcontext
from typing import Any, Final, Literal, Protocol, runtime_checkable
from uuid import UUID

__all__ = [
    "EVALUATOR_POLICY_VERSION",
    "EvaluationResult",
    "FactorEvaluator",
    "FactorEvaluatorError",
    "FieldProvider",
    "MalformedFormulaError",
    "UniverseDistributionProvider",
    "UnknownFieldError",
]

# Evaluator 자체의 정책 버전 — Screen Run snapshot freeze key.
# N/A 의미론 / op semantics / ratio_pct 정책 등이 바뀌면 새 버전.
EVALUATOR_POLICY_VERSION: Final[str] = "1.0"

# AST depth hard limit — 빌트인의 가장 복잡한 식 (ROE TTM) 도 ~5 depth 이하.
# 운영에서 32 초과는 corruption 또는 community pack 의 DoS 시도.
_DEFAULT_MAX_AST_DEPTH: Final[int] = 32

# Decimal precision / rounding — T20 / price_adjuster 와 동일 의미론.
_DECIMAL_PRECISION: Final[int] = 28


# =============================================================================
# Errors
# =============================================================================

class FactorEvaluatorError(Exception):
    """본 모듈의 모든 예외 base."""


class MalformedFormulaError(FactorEvaluatorError):
    """Factor pack 의 AST / op / 인자 모양 위반 — pack 무결성 corruption.

    - 알 수 없는 op
    - op 의 인자 모양 위반 (예: `add` 가 args 사용, `avg` 가 left/right 사용)
    - AST depth 초과
    - inputs 와 AST 실제 field 참조 불일치
    - provider.as_of 와 evaluate(as_of=...) mismatch

    fail-fast — Sentry alert 대상. 사용자 fault 아닌 pack/provider 무결성 위반.
    """


class UnknownFieldError(FactorEvaluatorError):
    """Provider 가 모르는 field — pack 의 `formula.inputs` 와 provider schema 불일치.

    데이터 결손 (값이 없음, N/A 처리) 과 구별. provider 가 raise 해야 함.
    """


# =============================================================================
# FieldProvider Protocol — PIT-aware fact source
# =============================================================================

@runtime_checkable
class FieldProvider(Protocol):
    """Factor 산출에 필요한 fact 값 제공자.

    Invariants (호출자 책임):
    - **PIT-aware** — 모든 반환값이 `self.as_of` 시점에 알 수 있었던 데이터만.
      PITEnforcer 통과 후 주입.
    - **결손 vs unknown 구별** — 알려진 field 의 값이 없으면 `None` (또는 빈
      series), 모르는 field 는 `UnknownFieldError` raise.
    - **Quarterly series 의 의미** — fiscal_period 오름차순. 정확히 `n` 개 또는
      0 개 (결손 시 부분 series 반환 금지 — silent regression 위험).

    Attributes:
        as_of: 본 provider 가 보장하는 PIT 기준 일자. Evaluator 가 invariant 검증.
    """

    as_of: date

    def get_scalar(self, field: str) -> Decimal | None:
        """단일 scalar 값. 결손 시 None.

        Raises:
            UnknownFieldError: field 가 provider schema 에 없음 (typo / pack 오류).
        """
        ...

    def get_quarterly_series(self, field: str, *, n: int) -> Sequence[Decimal]:
        """최근 n 분기 series (fiscal_period 오름차순).

        Returns:
            정확히 n 개 또는 빈 sequence. **결손이 한 분기라도 있으면 빈 sequence**
            (oracle 자문 결정 3 의 strict mode).

        Raises:
            UnknownFieldError: field unknown.
        """
        ...


@runtime_checkable
class UniverseDistributionProvider(Protocol):
    """유니버스-상대 연산 (zscore/percentile/min_max_scale) 의 분포 컨텍스트 제공자.

    ADR-0022 D2/D3 — 이 연산들은 단일 종목 값이 아니라 **as_of 시점 active
    universe 의 factor 값 분포**(모집단)에 대한 종목의 상대 위치를 산출한다.
    따라서 단일 종목 `FieldProvider` 만으로는 평가 불가하며, 유니버스 × as_of
    의 2 차 집계 (O(N)) 로 사전계산·캐싱된 분포 통계가 주입돼야 한다.

    **본 Protocol 은 T70b 에서 인터페이스 선언만 한다.** 실제 분포 계산 구현
    (모집단 = as_of active universe, N/A 종목 제외, tie-breaking 결정성,
    PIT-freeze + 통계량 hash) 은 **T72 의 분포 provider 가 담당**한다. T70b 의
    evaluator 는 이 provider 가 `None` 이면 (= 아직 미주입) 유니버스-상대 op 을
    정식 N/A (`universe_distribution_required`) 로 반환한다 — stub 아닌 정식 결과.

    각 메서드는 "현재 평가 중 종목" 의 값 `value` 와 분포가 고정된 `field`/`op`
    컨텍스트를 받아 유니버스-상대 산출값을 반환. 모집단이 비었거나 종목이 모집단
    밖이면 N/A 의미의 `None`.

    Attributes:
        as_of: 분포가 freeze 된 PIT 기준 일자. evaluate(as_of=...) 와 일치 강제.
    """

    as_of: date

    # NOTE(T72): 아래 시그니처는 분포 provider 의 contract 선언. T72 가 모집단
    # 통계 (μ/σ, 순위, min/max) 를 PIT-freeze 한 뒤 본 메서드들을 구현한다.
    # T70b 에서는 호출되지 않는다 (provider 미주입 시 정식 N/A 단축).

    def zscore(self, field: str, value: Decimal) -> Decimal | None:
        """value 의 유니버스 분포 내 z-score ((value - μ) / σ). σ=0 / 모집단
        부족 시 None (N/A)."""
        ...

    def percentile(self, field: str, value: Decimal) -> Decimal | None:
        """value 의 유니버스 분포 내 percentile [0, 100]. 모집단 부족 시 None."""
        ...

    def min_max_scale(self, field: str, value: Decimal) -> Decimal | None:
        """value 의 (value - min) / (max - min) ∈ [0, 1]. max=min / 모집단
        부족 시 None."""
        ...

    def sample_size(self, field: str) -> int:
        """`field` 의 universe-상대 분포 모집단 크기 (= n, ADR-0024 D5).

        **표시 전용 — evaluator 는 호출하지 않는다.** percentile/zscore/
        min_max_scale 의 반환 시그니처는 불변 (`Decimal | None`); 본 메서드는
        값을 만든 *뒤* 표시층 (evaluate route) 이 소표본 디스클로저 (모집단 크기
        + n < SMALL_SAMPLE_THRESHOLD 표식, ADR-0024 D2) 를 부착하기 위해 별도로
        조회하는 부수 정보. evaluator 의 산출·N/A·result_codes 경로에는 절대
        진입하지 않는다 (ADR-0024 D4 c result_codes 불변식)."""
        ...


# =============================================================================
# Result type
# =============================================================================

@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Factor evaluation 의 결과 — Screen Run snapshot 의 단위.

    Attributes:
        value: 산출값. N/A 면 None.
        is_na: `value is None` 의 alias (호출자 가독성).
        na_reason: N/A 의 근본 원인 (debug). e.g.,
            "missing_input:net_income_consolidated_ifrs" /
            "insufficient_series:net_income_consolidated_ifrs:requested=4,got=2" /
            "division_by_zero" / "empty_args".
        factor_uuid / factor_canonical_id: 산출 대상 factor 식별.
        inputs_used: Fidelity (§2.1) — 어떤 입력값으로 어떤 산출. Mapping 의 value 는
            scalar (Decimal), series (tuple[Decimal, ...]), 또는 빈 tuple
            (insufficient_series 시 시도된 field 표시). Stock Snapshot 의 `inputs`
            JSONB 직렬화 대상.
        evaluator_version: 본 결과를 만든 evaluator policy 버전. Screen Run snapshot
            의 `data_versions.evaluator_version` 입력.
    """

    value: Decimal | None
    is_na: bool
    na_reason: str | None
    factor_uuid: UUID
    factor_canonical_id: str
    inputs_used: Mapping[str, Decimal | tuple[Decimal, ...]]
    evaluator_version: str

    def __post_init__(self) -> None:
        # invariant — value None ↔ is_na True ↔ na_reason 존재 (oracle M3).
        if (self.value is None) != self.is_na:
            raise ValueError(
                f"invariant violation: value={self.value!r} is_na={self.is_na}"
            )
        if self.is_na and not self.na_reason:
            raise ValueError("is_na=True requires na_reason")
        if not self.is_na and self.na_reason is not None:
            raise ValueError(
                f"value present but na_reason={self.na_reason!r} set"
            )


# =============================================================================
# Internal — AST visit value union
# =============================================================================

# Visit 중간값 — scalar Decimal, series tuple, 또는 N/A sentinel.
# Type-level 구별을 위해 sentinel 은 별도 dataclass.

@dataclass(frozen=True, slots=True)
class _NA:
    """N/A sentinel — propagation 시 reason 보존."""

    reason: str


# Op 인자 모양 카테고리 — strict dispatcher 의 입력 검증 (oracle C1).
#
# M2 T70b 확장 (ADR-0022 D1) — composite 연산자 3 종의 새 shape 추가:
#   - "weighted_sum": args[] (variadic) + weights[] (상수 배열) — 종목-국소.
#   - "winsorize": args[] 단일 원소 (clip 대상 표현식) + lower/upper 상수 경계.
#     종목-국소 (상수 경계). 분위수 경계(유니버스-상대) 버전은 T72 deferred.
#   - "universe_unary": 단일 `field` (모집단 분포의 factor 명). zscore/
#     percentile/min_max_scale 가 공유 — 유니버스-상대. field 는 provider 로
#     종목값을 fetch 하는 동시에 분포 식별자 (= 그 field 의 유니버스 분포).
# 절대 부재 op (rank/top_n/bottom_n/sign/step) 는 _OP_SHAPES 에 등록조차 안 함 —
# ADR-0022 D1 "처음부터 부재가 안전". 등록 없으면 _visit_op 의 unknown op raise.
_OpShape = Literal[
    "binary", "variadic", "quarterly",
    "weighted_sum", "winsorize", "universe_unary",
]

_OP_SHAPES: Final[Mapping[str, _OpShape]] = {
    "add": "binary",
    "sub": "binary",
    "mul": "binary",
    "div": "binary",
    "ratio_pct": "binary",
    "sum_last_n_quarters": "quarterly",
    "avg": "variadic",
    # ----- M2 T70b composite 연산자 (ADR-0022 D1) -----
    "weighted_sum": "weighted_sum",
    "winsorize": "winsorize",
    "zscore": "universe_unary",
    "percentile": "universe_unary",
    "min_max_scale": "universe_unary",
}

# ADR-0022 D2 — 유니버스 분포에 의존하는 (= 종목 단일 평가로 산출 불가) 연산자.
# 모집단 분포(zscore 의 μ/σ, percentile 의 순위, min_max_scale 의 min/max)는
# 유니버스 × as_of 의 2 차 집계로, 분포 provider (T72) 가 주입돼야 평가 가능.
# T70b 에서는 shape 검증만 하고, 분포 컨텍스트 미주입 시 정식 N/A 를 반환한다
# (stub 아님 — M1 derived_factor 의 "pack/evaluator 미주입 시 정식 N/A" 패턴과
# 동형의 정식 결과). winsorize 는 상수 경계(lower/upper) 버전이므로 종목-국소 —
# 본 집합에서 제외 (완전 구현). winsorize 의 분위수 경계 버전은 T72 deferred.
_UNIVERSE_RELATIVE_OPS: Final[frozenset[str]] = frozenset(
    {"zscore", "percentile", "min_max_scale"}
)

# 유니버스-상대 op 의 정식 N/A 사유 — 분포 컨텍스트 미주입.
# na_reason 으로 호출자가 "데이터 결손" 이 아닌 "분포 provider 미주입 (T72 전)"
# 임을 식별. 분포가 주입되면 T72 에서 실값 산출 (본 사유 소멸).
_NA_UNIVERSE_DISTRIBUTION_REQUIRED: Final[str] = "universe_distribution_required"


# =============================================================================
# FactorEvaluator service
# =============================================================================

class FactorEvaluator:
    """Factor pack 의 formula AST 를 산출값으로 evaluate.

    State-less but DI 친화 — `max_ast_depth` 같은 instance config 보존.
    """

    def __init__(self, *, max_ast_depth: int = _DEFAULT_MAX_AST_DEPTH) -> None:
        if max_ast_depth < 1:
            raise ValueError(f"max_ast_depth must be >= 1, got {max_ast_depth}")
        self.max_ast_depth: Final[int] = max_ast_depth
        self.evaluator_version: Final[str] = EVALUATOR_POLICY_VERSION

    def evaluate(
        self,
        factor: dict,
        provider: FieldProvider,
        *,
        as_of: date,
        universe_distribution: UniverseDistributionProvider | None = None,
    ) -> EvaluationResult:
        """Factor 의 formula AST 를 evaluate.

        Args:
            factor: `LoadedPack.body["factors"][i]` dict — `canonical_id`, `uuid`,
                `formula: {ast, inputs}` 필요.
            provider: PIT-aware FieldProvider. `provider.as_of == as_of` 강제.
            as_of: PIT 기준. 로깅 / freeze 용. PIT 보장은 provider 책임.
            universe_distribution: 유니버스-상대 op (zscore/percentile/
                min_max_scale) 의 분포 컨텍스트 (ADR-0022 D2/D3). **T70b 에서는
                선언만 — 실제 분포 계산은 T72.** `None` (default) 이면 해당 op 들이
                정식 N/A (`universe_distribution_required`) 를 반환한다 (stub
                아님 — M1 derived_factor 미주입 N/A 패턴과 동형). 주입 시 as_of
                일치 강제.

        Returns:
            EvaluationResult — value 또는 N/A + na_reason + inputs_used + version.

        Raises:
            MalformedFormulaError: pack 의 AST 무결성 위반 / depth 초과 /
                provider as_of mismatch / inputs ↔ AST field 불일치 /
                weighted_sum weights 길이 불일치.
            UnknownFieldError: provider 가 모르는 field — pack 의 inputs typo.
        """
        # Provider PIT invariant — oracle C3 의 약한 강제.
        if getattr(provider, "as_of", None) != as_of:
            raise MalformedFormulaError(
                f"FieldProvider.as_of ({getattr(provider, 'as_of', None)}) "
                f"!= evaluate(as_of={as_of}). PIT invariant 위반."
            )

        # 분포 provider 주입 시 PIT invariant — 분포가 같은 as_of 로 freeze 됐어야
        # 재현성 (ADR-0022 D3) 보장. mismatch 는 호출자 misuse (provider 와 동일
        # 강제 의미론).
        if (
            universe_distribution is not None
            and getattr(universe_distribution, "as_of", None) != as_of
        ):
            raise MalformedFormulaError(
                f"UniverseDistributionProvider.as_of "
                f"({getattr(universe_distribution, 'as_of', None)}) "
                f"!= evaluate(as_of={as_of}). PIT invariant 위반."
            )

        # Factor schema 의 필수 필드 sanity check.
        try:
            factor_uuid = UUID(factor["uuid"])
            canonical_id = factor["canonical_id"]
            formula = factor["formula"]
            ast = formula["ast"]
            declared_inputs = frozenset(formula["inputs"])
        except (KeyError, ValueError, TypeError) as exc:
            raise MalformedFormulaError(
                f"factor missing required fields or malformed: {exc}"
            ) from exc

        # AST 의 field 참조가 formula.inputs 에 명시되어야 함 (oracle 결정 6 / C4).
        # 정적 walk 로 evaluation 전에 검증 — N/A 단축 평가로 인한 우회 차단.
        ast_fields = _collect_ast_fields(ast, canonical_id=canonical_id,
                                          max_depth=self.max_ast_depth)
        unexpected_fields = ast_fields - declared_inputs
        if unexpected_fields:
            raise MalformedFormulaError(
                f"factor {canonical_id} AST references fields not in "
                f"formula.inputs: {sorted(unexpected_fields)}. "
                f"declared inputs: {sorted(declared_inputs)}"
            )

        # inputs_used 추적 — visit 중 채워짐. Fidelity (§2.1) 의 stock_snapshots.inputs
        # JSONB 직렬화 대상.
        inputs_used: dict[str, Decimal | tuple[Decimal, ...]] = {}

        with localcontext() as ctx:
            ctx.prec = _DECIMAL_PRECISION
            ctx.rounding = ROUND_HALF_EVEN

            value_or_na = self._visit(
                ast, provider, inputs_used,
                depth=0, canonical_id=canonical_id,
                universe_distribution=universe_distribution,
            )

        # 결과 packaging.
        if isinstance(value_or_na, _NA):
            return EvaluationResult(
                value=None, is_na=True, na_reason=value_or_na.reason,
                factor_uuid=factor_uuid,
                factor_canonical_id=canonical_id,
                inputs_used=_freeze_inputs(inputs_used),
                evaluator_version=self.evaluator_version,
            )
        if isinstance(value_or_na, tuple):
            # Root 가 series 반환은 의미 없음 — 모든 op 이 scalar 반환해야.
            raise MalformedFormulaError(
                f"factor {canonical_id} root AST returned series instead of scalar"
            )
        # value_or_na 는 Decimal.
        return EvaluationResult(
            value=value_or_na, is_na=False, na_reason=None,
            factor_uuid=factor_uuid,
            factor_canonical_id=canonical_id,
            inputs_used=_freeze_inputs(inputs_used),
            evaluator_version=self.evaluator_version,
        )

    # ---------------------------------------------------------------------
    # Visitor — recursive descent
    # ---------------------------------------------------------------------

    def _visit(
        self,
        node: Any,
        provider: FieldProvider,
        inputs_used: dict[str, Decimal | tuple[Decimal, ...]],
        *,
        depth: int,
        canonical_id: str,
        universe_distribution: UniverseDistributionProvider | None,
    ) -> Decimal | _NA:
        """AST 노드 visit. scalar Decimal 또는 _NA 반환.

        Series 는 internal only — `sum_last_n_quarters` 가 series fetch 후 즉시
        합산하여 scalar 로 반환. Public visit interface 는 scalar | _NA.

        Args:
            canonical_id: error message context — Sentry alert 시 위치 식별 (oracle L6).
        """
        if depth > self.max_ast_depth:
            raise MalformedFormulaError(
                f"factor {canonical_id} AST depth exceeds limit "
                f"{self.max_ast_depth} — possible corruption or DoS attempt."
            )
        if not isinstance(node, dict):
            raise MalformedFormulaError(
                f"factor {canonical_id} AST node must be dict, got "
                f"{type(node).__name__}: {node!r}"
            )

        # 노드 종류 판정 — op 우선 (op 가 있으면 exprOp 으로 처리, field/n 은
        # sub-attribute. const/field 만 있으면 leaf node — oracle 2 차 M2).
        if "op" in node:
            # op 노드는 const 와 동시 보유 불가 (논리적 모순).
            if "const" in node:
                raise MalformedFormulaError(
                    f"factor {canonical_id} AST node has both 'op' and 'const': "
                    f"{sorted(node.keys())}"
                )
            return self._visit_op(node, provider, inputs_used,
                                  depth=depth, canonical_id=canonical_id,
                                  universe_distribution=universe_distribution)

        # Leaf node — const 또는 field 정확히 하나 (op 없음 가정).
        leaf_markers = {"const", "field"} & node.keys()
        if len(leaf_markers) != 1:
            raise MalformedFormulaError(
                f"factor {canonical_id} AST leaf must have exactly one of "
                f"{{const, field}}; got {sorted(node.keys())}"
            )

        if "const" in node:
            return _const_to_decimal(node["const"], canonical_id=canonical_id)

        # exprField — provider.get_scalar.
        field_name = node["field"]
        value = provider.get_scalar(field_name)
        if value is None:
            return _NA(f"missing_input:{field_name}")
        inputs_used[field_name] = value
        return value

    def _visit_op(
        self,
        node: dict,
        provider: FieldProvider,
        inputs_used: dict[str, Decimal | tuple[Decimal, ...]],
        *,
        depth: int,
        canonical_id: str,
        universe_distribution: UniverseDistributionProvider | None,
    ) -> Decimal | _NA:
        """Op 노드 — 인자 모양 strict 검증 후 dispatch (oracle C1)."""
        op = node["op"]
        shape = _OP_SHAPES.get(op)
        if shape is None:
            raise MalformedFormulaError(
                f"factor {canonical_id} unknown op '{op}'"
            )

        # 인자 모양 검증 — 각 shape 가 허용하는 키 집합.
        _assert_op_shape(node, op, shape, canonical_id=canonical_id)

        if shape == "binary":
            left = self._visit(node["left"], provider, inputs_used,
                               depth=depth + 1, canonical_id=canonical_id,
                               universe_distribution=universe_distribution)
            right = self._visit(node["right"], provider, inputs_used,
                                depth=depth + 1, canonical_id=canonical_id,
                                universe_distribution=universe_distribution)
            if isinstance(left, _NA):
                return left
            if isinstance(right, _NA):
                return right
            return _apply_binary(op, left, right, canonical_id=canonical_id)

        if shape == "variadic":
            args = node["args"]
            evaluated: list[Decimal] = []
            for arg in args:
                v = self._visit(arg, provider, inputs_used,
                                depth=depth + 1, canonical_id=canonical_id,
                                universe_distribution=universe_distribution)
                if isinstance(v, _NA):
                    return v
                evaluated.append(v)
            if not evaluated:
                return _NA("empty_args")
            if op == "avg":
                return sum(evaluated, Decimal(0)) / Decimal(len(evaluated))
            raise MalformedFormulaError(
                f"factor {canonical_id} unhandled variadic op '{op}'"
            )

        if shape == "weighted_sum":
            # ADR-0022 D1 weighted_sum (종목-국소, 완전 구현) — Σ(arg_i × weight_i).
            # 정규화된 입력 전제 (이종 단위 raw 합산은 무의미, ADR-0022 Alt C).
            # weights 길이 = args 길이는 evaluator 가 강제 (pack 무결성 위반 →
            # MalformedFormulaError, 데이터 N/A 아님). 단축 평가: 한 arg 라도
            # N/A 면 합 전체 N/A (N/A propagation 단조성 유지).
            return self._visit_weighted_sum(
                node, provider, inputs_used,
                depth=depth, canonical_id=canonical_id,
                universe_distribution=universe_distribution,
            )

        if shape == "winsorize":
            # ADR-0022 D1 winsorize (종목-국소 상수경계, 완전 구현) — 단일 입력
            # 표현식 (args[0]) 을 lower/upper 상수로 clip.
            return self._visit_winsorize(
                node, provider, inputs_used,
                depth=depth, canonical_id=canonical_id,
                universe_distribution=universe_distribution,
            )

        if shape == "universe_unary":
            # ADR-0022 D2/D3 유니버스-상대 op — zscore/percentile/min_max_scale.
            # 분포 컨텍스트 미주입 시 정식 N/A (shape 검증은 통과).
            return self._visit_universe_unary(
                node, op, provider, inputs_used,
                canonical_id=canonical_id,
                universe_distribution=universe_distribution,
            )

        # shape == "quarterly" — sum_last_n_quarters
        field_name = node["field"]
        n = int(node["n"])
        if n < 1:
            raise MalformedFormulaError(
                f"factor {canonical_id} sum_last_n_quarters n must be >= 1, got {n}"
            )
        series = tuple(provider.get_quarterly_series(field_name, n=n))
        # Provider contract: len(series) ∈ {0, n}. 그 외는 무결성 위반 (oracle C2).
        if len(series) not in (0, n):
            raise MalformedFormulaError(
                f"factor {canonical_id} provider returned {len(series)} items "
                f"for field '{field_name}' (requested n={n}); contract: 0 or n."
            )
        if len(series) == 0:
            # 결손 field 는 inputs_used 에 기록하지 않음 — 같은 field 가 AST 에
            # 다른 노드에서 성공적으로 fetch 됐을 수 있고, 그 성공 기록을 덮지
            # 않도록 함 (oracle 2 차 NEW-C1 의 순서 의존성 회피). 결손 field 의
            # 식별은 na_reason 의 `insufficient_series:<field>:...` 형식으로 충분.
            return _NA(
                f"insufficient_series:{field_name}:requested={n},got=0"
            )
        inputs_used[field_name] = series
        return sum(series, Decimal(0))

    # ---------------------------------------------------------------------
    # M2 T70b composite 연산자 (ADR-0022 D1)
    # ---------------------------------------------------------------------

    def _visit_weighted_sum(
        self,
        node: dict,
        provider: FieldProvider,
        inputs_used: dict[str, Decimal | tuple[Decimal, ...]],
        *,
        depth: int,
        canonical_id: str,
        universe_distribution: UniverseDistributionProvider | None,
    ) -> Decimal | _NA:
        """ADR-0022 D1 weighted_sum (종목-국소, 완전 구현) — Σ(arg_i × weight_i).

        - **weights 길이 = args 길이** 강제: 불일치는 pack 무결성 위반 (사용자
          fault 아닌 pack corruption) → MalformedFormulaError (데이터 N/A 아님).
          ADR-0022 D6 Fidelity — 가중치가 UI 에 visible 하려면 args 와 1:1 대응.
        - **정규화 입력 전제**: 이종 단위 raw 합산은 무의미 (ADR-0022 Alt C
          기각). 입력 정규화 (zscore/percentile) 책임은 pack author. evaluator 는
          가중합만.
        - **가중치 합=1 미강제**: ADR-0022 미해결 항목. 강제하지 않음.
        - **N/A propagation**: 한 arg 라도 N/A 면 합 전체 N/A (단조성 유지).
        - Decimal 산술 (localcontext prec=28, ROUND_HALF_EVEN) — 기존 op 일관.
        """
        args = node["args"]
        weights_raw = node["weights"]
        if len(weights_raw) != len(args):
            raise MalformedFormulaError(
                f"factor {canonical_id} weighted_sum weights length "
                f"({len(weights_raw)}) != args length ({len(args)}) — "
                f"pack 무결성 위반 (ADR-0022 D1 가중치-인자 1:1 대응)."
            )
        # weights 는 상수 number 배열 — const 와 동일 의미론으로 Decimal 변환
        # (float drift 회피, inf/nan 거부). bool 등 비-number 도 차단.
        weights = [
            _const_to_decimal(w, canonical_id=canonical_id) for w in weights_raw
        ]
        total = Decimal(0)
        for arg, weight in zip(args, weights, strict=True):
            v = self._visit(arg, provider, inputs_used,
                            depth=depth + 1, canonical_id=canonical_id,
                            universe_distribution=universe_distribution)
            if isinstance(v, _NA):
                # 단조 N/A — 첫 N/A 사유 보존 (downstream debug).
                return v
            total += v * weight
        return total

    def _visit_winsorize(
        self,
        node: dict,
        provider: FieldProvider,
        inputs_used: dict[str, Decimal | tuple[Decimal, ...]],
        *,
        depth: int,
        canonical_id: str,
        universe_distribution: UniverseDistributionProvider | None,
    ) -> Decimal | _NA:
        """ADR-0022 D1 winsorize (종목-국소 상수경계, 완전 구현).

        단일 입력 표현식 (args[0]) 을 lower/upper 상수로 clip:
        value < lower → lower, value > upper → upper, 그 외 value 그대로.

        - 종목-국소 — 유니버스 분포 불필요 (상수 경계). 따라서 T70b 완전 구현.
          ADR-0022 D1 표의 winsorize "유니버스-상대/상수 경계" 중 **상수 경계만**
          구현. 분위수 경계 (유니버스-상대) 버전은 T72 deferred.
        - lower/upper 는 상수 (const 의미론 — float drift 회피, inf/nan 거부).
        - lower <= upper 무결성 강제 — 역전은 pack corruption
          (MalformedFormulaError, 데이터 N/A 아님).
        - 입력 N/A 면 N/A propagation (단조).
        """
        value = self._visit(node["args"][0], provider, inputs_used,
                            depth=depth + 1, canonical_id=canonical_id,
                            universe_distribution=universe_distribution)
        if isinstance(value, _NA):
            return value
        lower = _const_to_decimal(node["lower"], canonical_id=canonical_id)
        upper = _const_to_decimal(node["upper"], canonical_id=canonical_id)
        if lower > upper:
            raise MalformedFormulaError(
                f"factor {canonical_id} winsorize lower ({lower}) > upper "
                f"({upper}) — pack 무결성 위반."
            )
        if value < lower:
            return lower
        if value > upper:
            return upper
        return value

    def _visit_universe_unary(
        self,
        node: dict,
        op: str,
        provider: FieldProvider,
        inputs_used: dict[str, Decimal | tuple[Decimal, ...]],
        *,
        canonical_id: str,
        universe_distribution: UniverseDistributionProvider | None,
    ) -> Decimal | _NA:
        """ADR-0022 D2/D3 유니버스-상대 op — zscore/percentile/min_max_scale.

        **T70b: shape 검증은 하되, 분포 컨텍스트 미주입 시 정식 N/A.**
        (stub/TODO 아님 — M1 derived_factor 의 "pack/evaluator 미주입 시 정식
        N/A" 패턴과 동형의 정식 결과.)

        AST: `{op, field}` — `field` 는 모집단 분포의 factor 명이자 종목값을
        fetch 할 provider field. 이 op 은 (1) provider 로 종목의 field 값을
        fetch 하고 (2) 그 field 의 유니버스 분포 (T72 provider) 내 상대 위치를
        산출. 분포 = as_of active universe × field 값 (ADR-0022 D3 PIT-freeze).

        N/A 의미론:
        - 분포 provider 미주입 (`None`) → `universe_distribution_required`
          (T72 전 정식 N/A). 종목값이 결손이어도 이 사유가 우선 (분포 자체가
          없으므로 산출 불가).
        - 분포 주입 + 종목값 결손 (None) → `missing_input:<field>` (일상 N/A).
        - 분포 주입 + provider 가 N/A (모집단 부족 / σ=0 / max=min) →
          `universe_distribution_na:<op>:<field>` (자연 전파).
        """
        field_name = node["field"]
        # 분포 미주입 — T72 전 정식 N/A. 종목값 fetch 보다 먼저 단축 (분포가
        # 없으면 종목값이 있어도 상대 위치 산출 불가).
        if universe_distribution is None:
            return _NA(_NA_UNIVERSE_DISTRIBUTION_REQUIRED)

        value = provider.get_scalar(field_name)
        if value is None:
            return _NA(f"missing_input:{field_name}")
        inputs_used[field_name] = value

        # T72 의 분포 provider 가 field 의 유니버스 분포로 상대 위치 산출.
        relative = _apply_universe_relative(
            op, field_name, value, universe_distribution,
            canonical_id=canonical_id,
        )
        if relative is None:
            # provider 가 N/A (모집단 부족 / σ=0 / max=min 등) — 자연 전파.
            return _NA(f"universe_distribution_na:{op}:{field_name}")
        return relative


# =============================================================================
# Helpers
# =============================================================================

def _const_to_decimal(value: Any, *, canonical_id: str = "") -> Decimal:
    """JSON number → Decimal. float 는 str() 경유로 IEEE 754 drift 회피.

    oracle 자문 결정 2 — JSON parser 가 `0.5` 를 float 으로 반환하므로
    `Decimal(0.5)` 의 `0.5000000000000000111...` 잔차 회피 위해 `Decimal(str(0.5))`.

    inf / nan 은 거부 (oracle M1) — factor_pack.canonicalize_jcs 의 `allow_nan=False`
    와 일관. JSON 우회 입력 (테스트·migration) 의 silent 통과 차단.
    """
    ctx = f"factor {canonical_id} " if canonical_id else ""
    if isinstance(value, bool):
        # bool 은 int subclass — 의도 외 입력 차단.
        raise MalformedFormulaError(f"{ctx}const must be number, got bool: {value!r}")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MalformedFormulaError(
                f"{ctx}const must be finite, got {value!r}"
            )
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise MalformedFormulaError(
                f"{ctx}invalid const value {value!r}: {exc}"
            ) from exc
    raise MalformedFormulaError(
        f"{ctx}const must be number, got {type(value).__name__}: {value!r}"
    )


def _collect_ast_fields(
    ast: Any, *, canonical_id: str, max_depth: int, _depth: int = 0,
) -> set[str]:
    """AST 에 등장하는 모든 field 이름 추출 — evaluation 없이 정적 walk.

    inputs ↔ AST field 일치 검증 (oracle C4) 의 무결성 검증을 evaluation 의 N/A
    단축 평가 영향에서 분리. 위반 시 evaluator 가 fail-fast.
    """
    if _depth > max_depth:
        raise MalformedFormulaError(
            f"factor {canonical_id} AST depth exceeds limit {max_depth} "
            f"during static walk."
        )
    if not isinstance(ast, dict):
        raise MalformedFormulaError(
            f"factor {canonical_id} AST node must be dict, got "
            f"{type(ast).__name__}"
        )
    fields: set[str] = set()
    if "field" in ast:
        fields.add(ast["field"])
    for key in ("left", "right"):
        if key in ast and ast[key] is not None:
            fields |= _collect_ast_fields(
                ast[key], canonical_id=canonical_id,
                max_depth=max_depth, _depth=_depth + 1,
            )
    if "args" in ast and ast["args"] is not None:
        for arg in ast["args"]:
            fields |= _collect_ast_fields(
                arg, canonical_id=canonical_id,
                max_depth=max_depth, _depth=_depth + 1,
            )
    return fields


_OP_SHAPE_KEYS: Final[tuple[str, ...]] = (
    "left", "right", "args", "field", "n", "weights", "lower", "upper",
)


def _assert_op_shape(node: dict, op: str, shape: _OpShape, *, canonical_id: str = "") -> None:
    """Op 의 인자 모양 strict 검증 — oracle C1.

    schema 의 `exprOp` 가 left/right/args/field/n/weights/lower/upper 모두
    optional 로 두므로 `{op:'add', args:[a,b,c]}` 같은 의미-모순 형태가
    schema-valid. evaluator 가 strict reject — 각 op 의 의미상 필요한 키만 허용.

    M2 T70b composite shape (ADR-0022 D1):
    - weighted_sum: args + weights 필수 (길이 일치는 _visit_weighted_sum 강제),
      나머지 금지.
    - winsorize: args + lower + upper 필수 (단일 입력 표현식 + 상수 경계).
    - universe_unary: field 필수 (분포 식별 + 종목값 fetch), 나머지 금지.
    """
    keys_present = {k for k in _OP_SHAPE_KEYS
                    if k in node and node[k] is not None}
    if shape == "binary":
        required = {"left", "right"}
        disallowed = keys_present - required
    elif shape == "variadic":
        required = {"args"}
        disallowed = keys_present - required
    elif shape == "quarterly":
        required = {"field", "n"}
        disallowed = keys_present - required
    elif shape == "weighted_sum":
        required = {"args", "weights"}
        disallowed = keys_present - required
    elif shape == "winsorize":
        required = {"args", "lower", "upper"}
        disallowed = keys_present - required
    elif shape == "universe_unary":
        required = {"field"}
        disallowed = keys_present - required
    else:
        raise MalformedFormulaError(f"unhandled shape '{shape}'")

    missing = required - keys_present
    extra = keys_present & disallowed
    if missing or extra:
        ctx = f"factor {canonical_id} " if canonical_id else ""
        raise MalformedFormulaError(
            f"{ctx}op '{op}' (shape {shape}) malformed: missing={sorted(missing)} "
            f"extra={sorted(extra)} present={sorted(keys_present)}"
        )


def _apply_binary(
    op: str, left: Decimal, right: Decimal, *, canonical_id: str = "",
) -> Decimal | _NA:
    """Binary op 적용. division-by-zero 등은 N/A 반환 (운영 일상).

    `div` vs `ratio_pct` 의미 (oracle 결정 5 / C3):
        본 evaluator 는 양쪽 모두 raw ratio (left / right) 반환. `ratio_pct` 의
        unit hint (×100 표시) 는 표시 layer 가 factor.unit == "percent" 보고 적용.
        EvaluationResult 자체에 unit_hint 노출은 후속 사이클 (M2+ community pack
        합류 전 결정). 본 사이클에서는 양 op 의 동작 동일이 명시적 정책.
    """
    if op == "add":
        return left + right
    if op == "sub":
        return left - right
    if op == "mul":
        return left * right
    if op in ("div", "ratio_pct"):
        if right == 0:
            return _NA("division_by_zero")
        try:
            return left / right
        except (DivisionByZero, InvalidOperation):
            return _NA("division_by_zero")
    ctx = f"factor {canonical_id} " if canonical_id else ""
    raise MalformedFormulaError(f"{ctx}unhandled binary op '{op}'")


def _apply_universe_relative(
    op: str,
    field: str,
    value: Decimal,
    distribution: UniverseDistributionProvider,
    *,
    canonical_id: str = "",
) -> Decimal | None:
    """유니버스-상대 op 을 분포 provider 메서드로 dispatch (ADR-0022 D2/D3).

    **NOTE(T72):** 본 dispatch 는 분포 provider 가 주입된 (`is not None`) 경우만
    호출된다 — T70b 에서는 분포 provider 가 없어 `_visit_universe_unary` 가
    여기 도달 전에 정식 N/A 로 단축한다. provider 구현 (모집단 통계 PIT-freeze,
    N/A 종목 제외, tie-breaking 결정성) 은 T72. 본 함수는 그 contract dispatch.

    Returns:
        Decimal 산출값. 분포 provider 가 N/A (모집단 부족 / σ=0 / max=min 등)
        반환 시 None (호출자가 자연 전파).
    """
    if op == "zscore":
        return distribution.zscore(field, value)
    if op == "percentile":
        return distribution.percentile(field, value)
    if op == "min_max_scale":
        return distribution.min_max_scale(field, value)
    ctx = f"factor {canonical_id} " if canonical_id else ""
    raise MalformedFormulaError(f"{ctx}unhandled universe-relative op '{op}'")


def _freeze_inputs(
    inputs_used: dict[str, Decimal | tuple[Decimal, ...]],
) -> Mapping[str, Decimal | tuple[Decimal, ...]]:
    """immutable view — dict mutation 차단."""
    from types import MappingProxyType
    return MappingProxyType(dict(inputs_used))
