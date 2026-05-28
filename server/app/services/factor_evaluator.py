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
_OpShape = Literal["binary", "variadic", "quarterly"]

_OP_SHAPES: Final[Mapping[str, _OpShape]] = {
    "add": "binary",
    "sub": "binary",
    "mul": "binary",
    "div": "binary",
    "ratio_pct": "binary",
    "sum_last_n_quarters": "quarterly",
    "avg": "variadic",
}


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
    ) -> EvaluationResult:
        """Factor 의 formula AST 를 evaluate.

        Args:
            factor: `LoadedPack.body["factors"][i]` dict — `canonical_id`, `uuid`,
                `formula: {ast, inputs}` 필요.
            provider: PIT-aware FieldProvider. `provider.as_of == as_of` 강제.
            as_of: PIT 기준. 로깅 / freeze 용. PIT 보장은 provider 책임.

        Returns:
            EvaluationResult — value 또는 N/A + na_reason + inputs_used + version.

        Raises:
            MalformedFormulaError: pack 의 AST 무결성 위반 / depth 초과 /
                provider as_of mismatch / inputs ↔ AST field 불일치.
            UnknownFieldError: provider 가 모르는 field — pack 의 inputs typo.
        """
        # Provider PIT invariant — oracle C3 의 약한 강제.
        if getattr(provider, "as_of", None) != as_of:
            raise MalformedFormulaError(
                f"FieldProvider.as_of ({getattr(provider, 'as_of', None)}) "
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
                                  depth=depth, canonical_id=canonical_id)

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
                               depth=depth + 1, canonical_id=canonical_id)
            right = self._visit(node["right"], provider, inputs_used,
                                depth=depth + 1, canonical_id=canonical_id)
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
                                depth=depth + 1, canonical_id=canonical_id)
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


def _assert_op_shape(node: dict, op: str, shape: _OpShape, *, canonical_id: str = "") -> None:
    """Op 의 인자 모양 strict 검증 — oracle C1.

    schema 의 `exprOp` 가 left/right/args/field/n 모두 optional 로 두므로
    `{op:'add', args:[a,b,c]}` 같은 의미-모순 형태가 schema-valid. evaluator 가
    strict reject.
    """
    keys_present = {k for k in ("left", "right", "args", "field", "n")
                    if k in node and node[k] is not None}
    if shape == "binary":
        required = {"left", "right"}
        disallowed = {"args", "field", "n"}
    elif shape == "variadic":
        required = {"args"}
        disallowed = {"left", "right", "field", "n"}
    elif shape == "quarterly":
        required = {"field", "n"}
        disallowed = {"left", "right", "args"}
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


def _freeze_inputs(
    inputs_used: dict[str, Decimal | tuple[Decimal, ...]],
) -> Mapping[str, Decimal | tuple[Decimal, ...]]:
    """immutable view — dict mutation 차단."""
    from types import MappingProxyType
    return MappingProxyType(dict(inputs_used))
