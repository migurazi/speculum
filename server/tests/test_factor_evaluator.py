"""factor_evaluator 단위 테스트.

테스트 매트릭스:
1. 단순 const + field — scalar evaluation
2. binary ops (add/sub/mul/div) — Decimal 결정성
3. ratio_pct — raw ratio 반환 (×100 미적용)
4. sum_last_n_quarters — strict (정확히 n 개)
5. sum_last_n_quarters — 결손 → N/A
6. avg — variadic
7. nested AST — market-cap, PER, ROE 빌트인 식
8. N/A propagation — division by zero, missing input
9. inputs_used Fidelity — scalar + series 보존
10. Op shape 위반 → MalformedFormulaError
11. Unknown op → MalformedFormulaError
12. Unknown field → UnknownFieldError
13. AST depth limit → MalformedFormulaError
14. const 의 float → Decimal(str) 변환 정확성
15. Provider as_of mismatch → MalformedFormulaError
16. inputs ↔ AST field 참조 일치 검증
17. EVALUATOR_POLICY_VERSION + EvaluationResult.evaluator_version 보존
18. 빌트인 pack 의 실제 factor 로 end-to-end (smoke)
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest

from app.services.factor_evaluator import (
    EVALUATOR_POLICY_VERSION,
    EvaluationResult,
    FactorEvaluator,
    FactorEvaluatorError,
    MalformedFormulaError,
    UnknownFieldError,
)

_BUILTIN_PACK_PATH: Final[Path] = (
    Path(__file__).resolve().parents[1]
    / "builtin-packs" / "factors" / "speculum-builtin-v1.0.0.json"
)


# =============================================================================
# Helpers — In-memory FieldProvider implementation
# =============================================================================

class FakeFieldProvider:
    """테스트용 in-memory provider — known_fields 명시로 unknown vs missing 구별."""

    def __init__(
        self,
        *,
        as_of: date,
        scalars: dict[str, Decimal | None] | None = None,
        series: dict[str, Sequence[Decimal]] | None = None,
        known_fields: frozenset[str] | None = None,
    ) -> None:
        self.as_of = as_of
        self._scalars: dict[str, Decimal | None] = scalars or {}
        self._series: dict[str, Sequence[Decimal]] = series or {}
        # known_fields 없으면 scalars/series 의 keys 합집합.
        if known_fields is None:
            known_fields = frozenset(self._scalars.keys()) | frozenset(self._series.keys())
        self._known: frozenset[str] = known_fields

    def get_scalar(self, field: str) -> Decimal | None:
        if field not in self._known:
            raise UnknownFieldError(f"unknown field: {field}")
        return self._scalars.get(field)

    def get_quarterly_series(self, field: str, *, n: int) -> Sequence[Decimal]:
        if field not in self._known:
            raise UnknownFieldError(f"unknown field: {field}")
        series = list(self._series.get(field, []))
        # strict — 정확히 n 개 또는 빈 sequence.
        if len(series) >= n:
            return tuple(series[-n:])
        return ()


def _factor(
    *,
    canonical_id: str = "test:factor",
    uuid_str: str = "00000000-0000-0000-0000-000000000001",
    ast: dict,
    inputs: list[str],
) -> dict:
    """Minimal factor dict — evaluator 가 요구하는 필드만."""
    return {
        "canonical_id": canonical_id,
        "uuid": uuid_str,
        "formula": {"ast": ast, "inputs": inputs},
    }


# =============================================================================
# 1. const + field
# =============================================================================

def test_evaluate_const() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(ast={"const": 100}, inputs=[])
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.value == Decimal("100")
    assert not result.is_na


def test_evaluate_field_scalar() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 5, 1),
        scalars={"shares_issued": Decimal("1000000")},
    )
    factor = _factor(
        ast={"field": "shares_issued"},
        inputs=["shares_issued"],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.value == Decimal("1000000")
    assert result.inputs_used == {"shares_issued": Decimal("1000000")}


# =============================================================================
# 2. binary ops
# =============================================================================

@pytest.mark.parametrize("op, expected", [
    ("add", Decimal("30")),
    ("sub", Decimal("10")),
    ("mul", Decimal("200")),
    ("div", Decimal("2")),
])
def test_binary_ops(op: str, expected: Decimal) -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(
        ast={"op": op, "left": {"const": 20}, "right": {"const": 10}},
        inputs=[],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.value == expected


# =============================================================================
# 3. ratio_pct — raw ratio (×100 미적용)
# =============================================================================

def test_ratio_pct_returns_raw_ratio_not_multiplied() -> None:
    """oracle 자문 결정 5 — ratio_pct 가 raw ratio 반환. ×100 은 표시 layer."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(
        ast={"op": "ratio_pct", "left": {"const": 1500}, "right": {"const": 50000}},
        inputs=[],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    # 1500/50000 = 0.03 (×100 미적용)
    assert result.value == Decimal("0.03")


# =============================================================================
# 4. sum_last_n_quarters — strict 정확히 n 개
# =============================================================================

def test_sum_last_n_quarters_exact_count() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 12, 31),
        series={"net_income": tuple(Decimal(x) for x in ("100", "120", "110", "130"))},
    )
    factor = _factor(
        ast={"op": "sum_last_n_quarters", "field": "net_income", "n": 4},
        inputs=["net_income"],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 12, 31))
    assert result.value == Decimal("460")
    # inputs_used 에 series 가 tuple 로 보존.
    assert result.inputs_used["net_income"] == tuple(
        Decimal(x) for x in ("100", "120", "110", "130")
    )


def test_sum_last_n_quarters_uses_last_n() -> None:
    """series 가 n 보다 많으면 최근 n 개."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 12, 31),
        series={"x": tuple(Decimal(v) for v in ("10", "20", "30", "40", "50", "60"))},
    )
    factor = _factor(
        ast={"op": "sum_last_n_quarters", "field": "x", "n": 4},
        inputs=["x"],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 12, 31))
    # 마지막 4 개 = 30+40+50+60 = 180
    assert result.value == Decimal("180")


# =============================================================================
# 5. 결손 → N/A
# =============================================================================

def test_sum_last_n_quarters_with_insufficient_series_returns_na() -> None:
    """oracle 자문 결정 3 — n 미달 시 N/A (strict mode)."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 12, 31),
        series={"net_income": tuple(Decimal(x) for x in ("100", "120"))},  # 2 개만
    )
    factor = _factor(
        ast={"op": "sum_last_n_quarters", "field": "net_income", "n": 4},
        inputs=["net_income"],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 12, 31))
    assert result.is_na
    assert "insufficient_series" in result.na_reason
    assert "requested=4" in result.na_reason


def test_field_with_none_value_returns_na() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 5, 1),
        scalars={"close": None},
    )
    factor = _factor(ast={"field": "close"}, inputs=["close"])
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.is_na
    assert "missing_input:close" in result.na_reason


# =============================================================================
# 6. avg — variadic
# =============================================================================

def test_avg_variadic_args() -> None:
    """ROE 의 (equity_begin + equity_end) / 2 패턴."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 5, 1),
        scalars={"equity_begin": Decimal("100"), "equity_end": Decimal("140")},
    )
    factor = _factor(
        ast={"op": "avg", "args": [
            {"field": "equity_begin"},
            {"field": "equity_end"},
        ]},
        inputs=["equity_begin", "equity_end"],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.value == Decimal("120")


def test_avg_single_arg() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(
        ast={"op": "avg", "args": [{"const": 42}]},
        inputs=[],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.value == Decimal("42")


# =============================================================================
# 7. Nested AST — market-cap (sub + mul)
# =============================================================================

def test_market_cap_ex_treasury_formula() -> None:
    """ADR-0004 D1 default — (shares_issued - shares_treasury) × close_adjusted."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 5, 1),
        scalars={
            "shares_issued": Decimal("1000000"),
            "shares_treasury": Decimal("50000"),
            "close_price_adjusted": Decimal("70000"),
        },
    )
    factor = _factor(
        ast={"op": "mul",
             "left": {"op": "sub",
                      "left": {"field": "shares_issued"},
                      "right": {"field": "shares_treasury"}},
             "right": {"field": "close_price_adjusted"}},
        inputs=["shares_issued", "shares_treasury", "close_price_adjusted"],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    # (1000000 - 50000) × 70000 = 950000 × 70000 = 66,500,000,000
    assert result.value == Decimal("66500000000")
    assert set(result.inputs_used.keys()) == {
        "shares_issued", "shares_treasury", "close_price_adjusted"
    }


# =============================================================================
# 8. N/A propagation — root 까지
# =============================================================================

def test_na_propagates_through_nested_ops() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 5, 1),
        scalars={"a": Decimal("100"), "b": None},  # b 결손
    )
    factor = _factor(
        ast={"op": "add",
             "left": {"field": "a"},
             "right": {"field": "b"}},
        inputs=["a", "b"],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.is_na
    assert result.na_reason == "missing_input:b"


def test_division_by_zero_returns_na() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(
        ast={"op": "div", "left": {"const": 100}, "right": {"const": 0}},
        inputs=[],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.is_na
    assert result.na_reason == "division_by_zero"


def test_ratio_pct_division_by_zero_returns_na() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(
        ast={"op": "ratio_pct", "left": {"const": 100}, "right": {"const": 0}},
        inputs=[],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.is_na


# =============================================================================
# 9. Op shape 위반 → MalformedFormulaError
# =============================================================================

def test_binary_op_with_args_raises() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(
        ast={"op": "add", "args": [{"const": 1}, {"const": 2}]},
        inputs=[],
    )
    with pytest.raises(MalformedFormulaError, match="add"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


def test_avg_with_left_right_raises() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(
        ast={"op": "avg", "left": {"const": 1}, "right": {"const": 2}},
        inputs=[],
    )
    with pytest.raises(MalformedFormulaError, match="avg"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


def test_quarterly_op_missing_n_raises() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(
        ast={"op": "sum_last_n_quarters", "field": "x"},  # n 누락
        inputs=["x"],
    )
    with pytest.raises(MalformedFormulaError, match="sum_last_n_quarters"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


# =============================================================================
# 10. Unknown op → MalformedFormulaError
# =============================================================================

def test_unknown_op_raises() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(
        ast={"op": "exp", "left": {"const": 1}, "right": {"const": 2}},
        inputs=[],
    )
    with pytest.raises(MalformedFormulaError, match="unknown op"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


# =============================================================================
# 11. Unknown field → UnknownFieldError
# =============================================================================

def test_unknown_field_raises_unknown_field_error() -> None:
    """oracle 자문 결정 7 — provider 가 모르는 field 는 raise (typo / contract 위반)."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 5, 1),
        scalars={"a": Decimal("100")},
        known_fields=frozenset(["a"]),  # b 모름
    )
    factor = _factor(
        ast={"field": "b"},
        inputs=["b"],
    )
    with pytest.raises(UnknownFieldError):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


# =============================================================================
# 12. AST depth limit
# =============================================================================

def test_ast_depth_limit_raises() -> None:
    evaluator = FactorEvaluator(max_ast_depth=4)
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    # 깊이 6 의 nested add — 4 초과 시 raise.
    ast: dict = {"const": 1}
    for _ in range(6):
        ast = {"op": "add", "left": ast, "right": {"const": 1}}
    factor = _factor(ast=ast, inputs=[])
    with pytest.raises(MalformedFormulaError, match="depth"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


def test_init_rejects_zero_max_depth() -> None:
    with pytest.raises(ValueError):
        FactorEvaluator(max_ast_depth=0)


# =============================================================================
# 13. const 의 float → Decimal(str) 변환 정확성
# =============================================================================

def test_float_const_decimal_drift_avoided() -> None:
    """oracle 자문 결정 2 — Decimal(str(0.5)) = Decimal("0.5") 정확.

    Decimal(0.5) (float 직접) 는 0.5 정확하나, 0.1 + 0.2 같은 미세 drift case 검증.
    """
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(
        ast={"op": "add", "left": {"const": 0.1}, "right": {"const": 0.2}},
        inputs=[],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    # 0.1 + 0.2 = 0.3 (Decimal "0.1" + "0.2"), not 0.30000000000000004 (float)
    assert result.value == Decimal("0.3")


def test_int_const_preserved() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(ast={"const": 42}, inputs=[])
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.value == Decimal("42")


def test_bool_const_rejected() -> None:
    """bool 은 int subclass — silent 통과 차단."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(ast={"const": True}, inputs=[])
    with pytest.raises(MalformedFormulaError, match="bool"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


# =============================================================================
# 14. Provider as_of mismatch → MalformedFormulaError
# =============================================================================

def test_provider_as_of_mismatch_raises() -> None:
    """oracle C3 — PIT invariant 약한 강제."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(ast={"const": 1}, inputs=[])
    with pytest.raises(MalformedFormulaError, match="PIT invariant"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 6, 1))


# =============================================================================
# 15. inputs ↔ AST field 참조 일치 검증
# =============================================================================

def test_ast_field_not_in_formula_inputs_raises() -> None:
    """oracle 결정 6 — AST 가 formula.inputs 외 field 참조 시 무결성 위반."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 5, 1),
        scalars={"a": Decimal("1"), "b": Decimal("2")},
    )
    # AST 가 a, b 참조하지만 inputs 에 a 만 명시 → b 가 unexpected.
    factor = _factor(
        ast={"op": "add", "left": {"field": "a"}, "right": {"field": "b"}},
        inputs=["a"],  # b 누락
    )
    with pytest.raises(MalformedFormulaError, match="not in formula.inputs"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


def test_inputs_with_unused_field_does_not_raise() -> None:
    """inputs 에 명시됐지만 AST 에서 안 쓰는 field 는 OK (분기 결과로 일부만 사용)."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 5, 1),
        scalars={"a": Decimal("100")},
    )
    factor = _factor(
        ast={"field": "a"},
        inputs=["a", "b"],  # b 는 미사용 (allowed)
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.value == Decimal("100")


# =============================================================================
# 16. EvaluatorPolicy version freeze
# =============================================================================

def test_evaluator_version_constant() -> None:
    assert EVALUATOR_POLICY_VERSION == "1.0"


def test_result_carries_evaluator_version() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(ast={"const": 1}, inputs=[])
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    assert result.evaluator_version == "1.0"


# =============================================================================
# 17. Result immutability + 예외 계층
# =============================================================================

def test_evaluation_result_is_frozen() -> None:
    import dataclasses
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(ast={"const": 1}, inputs=[])
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.value = Decimal("999")  # type: ignore[misc]


def test_inputs_used_is_immutable_mapping() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 5, 1),
        scalars={"a": Decimal("1")},
    )
    factor = _factor(ast={"field": "a"}, inputs=["a"])
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))
    with pytest.raises(TypeError):
        result.inputs_used["a"] = Decimal("999")  # type: ignore[index]


def test_error_hierarchy() -> None:
    assert issubclass(MalformedFormulaError, FactorEvaluatorError)
    assert issubclass(UnknownFieldError, FactorEvaluatorError)


# =============================================================================
# 18. 빌트인 pack end-to-end — market_cap_ex_treasury
# =============================================================================

def test_evaluate_builtin_market_cap_ex_treasury() -> None:
    """실제 빌트인 pack 의 market-cap:ex-treasury factor 를 evaluate."""
    with _BUILTIN_PACK_PATH.open(encoding="utf-8") as f:
        pack = json.load(f)
    market_cap = next(
        f for f in pack["factors"] if f["canonical_id"] == "market-cap:ex-treasury"
    )

    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 5, 1),
        scalars={
            "shares_issued": Decimal("5969782550"),  # 삼성전자 발행주식수 근사
            "shares_treasury": Decimal("0"),
            "close_price_adjusted": Decimal("70000"),
        },
    )
    result = evaluator.evaluate(market_cap, provider, as_of=date(2024, 5, 1))
    expected = Decimal("5969782550") * Decimal("70000")
    assert result.value == expected
    assert not result.is_na


def test_evaluate_builtin_roe_ttm_nested() -> None:
    """oracle 2 차 M5 — ROE TTM nested AST (div + sum_last_n_quarters + avg) end-to-end.

    ROE = sum_last_4q(net_income) / avg(equity_begin, equity_end).
    """
    with _BUILTIN_PACK_PATH.open(encoding="utf-8") as f:
        pack = json.load(f)
    roe = next(
        f for f in pack["factors"]
        if f["canonical_id"] == "roe:ttm-avg-equity-consolidated-ifrs"
    )

    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 12, 31),
        scalars={
            "equity_attributable_to_common_consolidated_ifrs_period_begin":
                Decimal("100000000000"),
            "equity_attributable_to_common_consolidated_ifrs":
                Decimal("140000000000"),
        },
        series={
            "net_income_attributable_consolidated_ifrs": tuple(
                Decimal(x) for x in (
                    "5000000000", "6000000000", "5500000000", "6500000000"
                )
            ),
        },
    )
    result = evaluator.evaluate(roe, provider, as_of=date(2024, 12, 31))
    # sum_4q = 23000000000, avg = (100 + 140)/2 = 120000000000
    # ROE = 23000000000 / 120000000000 = 0.19166...
    expected = Decimal("23000000000") / Decimal("120000000000")
    assert result.value == expected
    # inputs_used 에 series + 두 scalar 모두 포함.
    assert "net_income_attributable_consolidated_ifrs" in result.inputs_used
    assert "equity_attributable_to_common_consolidated_ifrs" in result.inputs_used


def test_evaluate_builtin_dividend_yield_ratio_pct() -> None:
    """oracle M5 — dividend-yield (ratio_pct) 빌트인 end-to-end."""
    with _BUILTIN_PACK_PATH.open(encoding="utf-8") as f:
        pack = json.load(f)
    dy = next(
        f for f in pack["factors"]
        if f["canonical_id"] == "dividend-yield:trailing-annual"
    )

    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 12, 31),
        scalars={
            "dividend_per_share_trailing_annual": Decimal("1500"),
            "close_price_adjusted": Decimal("50000"),
        },
    )
    result = evaluator.evaluate(dy, provider, as_of=date(2024, 12, 31))
    # ratio_pct = raw ratio (×100 미적용) = 0.03
    assert result.value == Decimal("0.03")


# =============================================================================
# 19. oracle 2 차 리뷰 회귀 — C/M/L 적용 항목별
# =============================================================================

def test_insufficient_series_field_in_na_reason_only() -> None:
    """oracle 2 차 NEW-C1 — 결손 시 inputs_used 미기록, na_reason 에 field 포함.

    이전 setdefault 패턴은 동일 field 가 AST 의 다른 노드에서 성공 fetch 된 경우
    순서 의존성으로 빈 tuple 이 성공 값을 overwrite 하거나 그 반대 silent 발생.
    결손 식별은 na_reason 으로 충분.
    """
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 12, 31),
        series={"net_income": ()},
    )
    factor = _factor(
        ast={"op": "sum_last_n_quarters", "field": "net_income", "n": 4},
        inputs=["net_income"],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 12, 31))
    assert result.is_na
    # inputs_used 에는 들어가지 않음 — 순서 의존성 회피.
    assert "net_income" not in result.inputs_used
    # 식별은 na_reason 으로 충분.
    assert "insufficient_series:net_income" in result.na_reason
    assert "requested=4" in result.na_reason


def test_same_field_in_two_nodes_inputs_used_records_success_only() -> None:
    """동일 field 가 AST 두 노드에서 참조 시 — 성공 호출이 inputs_used 에 기록.

    AST: `add(sum_4q(net_income), sum_2q(net_income))` — 같은 field 두 번 fetch.
    첫 호출 4 분기 → 성공. 두 번째 2 분기 → 성공 (둘 다 series).
    `inputs_used["net_income"]` 은 마지막 기록 (overwrite — 의도된 행동).
    """
    evaluator = FactorEvaluator()
    series_data = tuple(Decimal(x) for x in ("10", "20", "30", "40", "50", "60"))
    provider = FakeFieldProvider(
        as_of=date(2024, 12, 31),
        series={"net_income": series_data},
    )
    factor = _factor(
        ast={"op": "add",
             "left": {"op": "sum_last_n_quarters", "field": "net_income", "n": 4},
             "right": {"op": "sum_last_n_quarters", "field": "net_income", "n": 2}},
        inputs=["net_income"],
    )
    result = evaluator.evaluate(factor, provider, as_of=date(2024, 12, 31))
    # 4q = 30+40+50+60 = 180, 2q = 50+60 = 110, sum = 290
    assert result.value == Decimal("290")
    # inputs_used 가 최종 성공 호출 (2q) 의 series 보유 — 순서 의존성은 있으나
    # 둘 다 성공이므로 silent 손실 없음. 결손과 성공 혼재 시는 위 test 의
    # 보호 정책으로 처리.
    assert "net_income" in result.inputs_used


def test_provider_over_fetch_raises_malformed_formula() -> None:
    """oracle 2 차 C2 — provider 가 n 보다 많이 반환 시 contract 위반 raise."""
    evaluator = FactorEvaluator()

    class OverFetchProvider:
        as_of = date(2024, 12, 31)
        def get_scalar(self, field: str) -> Decimal | None:
            return None
        def get_quarterly_series(self, field: str, *, n: int) -> Sequence[Decimal]:
            # n=4 요청에 5 개 반환 — contract 위반
            return tuple(Decimal(i) for i in range(n + 1))

    factor = _factor(
        ast={"op": "sum_last_n_quarters", "field": "x", "n": 4},
        inputs=["x"],
    )
    with pytest.raises(MalformedFormulaError, match="contract"):
        evaluator.evaluate(factor, OverFetchProvider(), as_of=date(2024, 12, 31))


def test_static_ast_field_validation_runs_before_evaluation() -> None:
    """oracle 2 차 C4 — N/A short-circuit 도 inputs 무결성 검증 우회 X.

    `add(field:a, field:b)` 에서 a 결손 → N/A. b 가 inputs 에 미선언 — 정적 walk 가
    먼저 검출.
    """
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 5, 1),
        scalars={"a": None, "b": Decimal("10")},  # a 결손 (short-circuit 가능)
    )
    factor = _factor(
        ast={"op": "add", "left": {"field": "a"}, "right": {"field": "b"}},
        inputs=["a"],  # b 누락
    )
    # short-circuit 이 발생해도 정적 walk 가 먼저 raise.
    with pytest.raises(MalformedFormulaError, match="not in formula.inputs"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


def test_const_inf_rejected() -> None:
    """oracle 2 차 M1 — inf float 거부."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(ast={"const": float("inf")}, inputs=[])
    with pytest.raises(MalformedFormulaError, match="finite"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


def test_const_nan_rejected() -> None:
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(ast={"const": float("nan")}, inputs=[])
    with pytest.raises(MalformedFormulaError, match="finite"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


def test_node_with_both_const_and_field_raises() -> None:
    """oracle 2 차 M2 — const/field/op 중 정확히 하나만 허용."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1),
                                  scalars={"a": Decimal("1")})
    factor = _factor(
        ast={"const": 1, "field": "a"},  # 두 marker 동시
        inputs=["a"],
    )
    with pytest.raises(MalformedFormulaError, match="exactly one"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


def test_evaluation_result_invariant_enforced() -> None:
    """oracle 2 차 M3 — value None ↔ is_na True ↔ na_reason 존재."""
    from uuid import uuid4
    # value None + is_na False — 모순
    with pytest.raises(ValueError, match="invariant"):
        EvaluationResult(
            value=None, is_na=False, na_reason=None,
            factor_uuid=uuid4(), factor_canonical_id="x",
            inputs_used={}, evaluator_version="1.0",
        )
    # is_na True 인데 na_reason 없음
    with pytest.raises(ValueError, match="na_reason"):
        EvaluationResult(
            value=None, is_na=True, na_reason=None,
            factor_uuid=uuid4(), factor_canonical_id="x",
            inputs_used={}, evaluator_version="1.0",
        )
    # value 있는데 na_reason 도 set
    with pytest.raises(ValueError, match="na_reason"):
        EvaluationResult(
            value=Decimal("1"), is_na=False, na_reason="surprise",
            factor_uuid=uuid4(), factor_canonical_id="x",
            inputs_used={}, evaluator_version="1.0",
        )


def test_error_message_includes_canonical_id() -> None:
    """oracle 2 차 L6 — error 위치 식별을 위한 factor_canonical_id 포함."""
    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(as_of=date(2024, 5, 1))
    factor = _factor(
        canonical_id="test:problem-factor",
        ast={"op": "add", "args": [{"const": 1}]},  # binary 인데 args
        inputs=[],
    )
    with pytest.raises(MalformedFormulaError, match="test:problem-factor"):
        evaluator.evaluate(factor, provider, as_of=date(2024, 5, 1))


def test_evaluate_builtin_per_ttm_with_quarterly_series() -> None:
    """PER (TTM) — market_cap / sum_last_n_quarters(net_income, 4)."""
    with _BUILTIN_PACK_PATH.open(encoding="utf-8") as f:
        pack = json.load(f)
    per_ttm = next(
        f for f in pack["factors"] if f["canonical_id"] == "per:ttm-consolidated-ifrs"
    )

    evaluator = FactorEvaluator()
    provider = FakeFieldProvider(
        as_of=date(2024, 12, 31),
        scalars={"market_cap_ex_treasury": Decimal("400000000000000")},
        series={"net_income_attributable_consolidated_ifrs": tuple(
            Decimal(x) for x in (
                "10000000000000",
                "12000000000000",
                "11000000000000",
                "13000000000000",
            )
        )},
    )
    result = evaluator.evaluate(per_ttm, provider, as_of=date(2024, 12, 31))
    # market_cap / sum = 4e14 / 4.6e13 ≈ 8.695...
    expected_sum = Decimal("46000000000000")
    expected = Decimal("400000000000000") / expected_sum
    assert result.value == expected
