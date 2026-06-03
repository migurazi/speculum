/**
 * Factor AST 순수 함수 테스트 — renderFormula / 허용 op / collectFields /
 * makeOpSkeleton.
 *
 * 핵심 검증:
 *   1. AST → 사람이 읽는 수식 렌더 (Fidelity — 가중치/경계/모집단 visible).
 *   2. 허용 op 목록에 rank/top_n/bottom_n/sign 부재 (ADR-0022 D1, No Advice).
 *   3. field leaf 수집(inputs 자동).
 */

import { describe, expect, it } from "vitest";

import {
  ALLOWED_OPS,
  OP_SPECS,
  collectFields,
  makeOpSkeleton,
  renderFormula,
  type Expr,
} from "../ast";

describe("renderFormula — AST → 수식 텍스트 (Fidelity)", () => {
  it("const / field leaf", () => {
    expect(renderFormula({ const: 12.3 })).toBe("12.3");
    expect(renderFormula({ field: "close_price_adjusted" })).toBe(
      "close_price_adjusted",
    );
  });

  it("이항 사칙연산 — 중위 기호 + 괄호", () => {
    const ast: Expr = {
      op: "sub",
      left: { field: "shares_issued" },
      right: { field: "shares_treasury" },
    };
    expect(renderFormula(ast)).toBe("(shares_issued - shares_treasury)");
  });

  it("mul — 중첩 식 합성", () => {
    const ast: Expr = {
      op: "mul",
      left: {
        op: "sub",
        left: { field: "shares_issued" },
        right: { field: "shares_treasury" },
      },
      right: { field: "close_price_adjusted" },
    };
    expect(renderFormula(ast)).toBe(
      "((shares_issued - shares_treasury) × close_price_adjusted)",
    );
  });

  it("ratio_pct — 함수 표기", () => {
    const ast: Expr = {
      op: "ratio_pct",
      left: { field: "net_income" },
      right: { field: "equity" },
    };
    expect(renderFormula(ast)).toBe("ratio_pct(net_income, equity)");
  });

  it("sum_last_n_quarters — field + n 항상 표기", () => {
    const ast: Expr = { op: "sum_last_n_quarters", field: "revenue", n: 4 };
    expect(renderFormula(ast)).toBe("sum_last_n_quarters(revenue, n=4)");
  });

  it("weighted_sum — 가중치를 각 항에 표기 (Fidelity)", () => {
    const ast: Expr = {
      op: "weighted_sum",
      args: [{ field: "a" }, { field: "b" }],
      weights: [0.6, 0.4],
    };
    expect(renderFormula(ast)).toBe("weighted_sum(a × 0.6, b × 0.4)");
  });

  it("winsorize — lower/upper 경계 표기 (Fidelity)", () => {
    const ast: Expr = {
      op: "winsorize",
      args: [{ field: "x" }],
      lower: 0,
      upper: 100,
    };
    expect(renderFormula(ast)).toBe("winsorize(x, [0, 100])");
  });

  it("정규화 op — 유니버스-상대 모집단 라벨 표기 (Fidelity)", () => {
    const ast: Expr = { op: "zscore", field: "roe" };
    expect(renderFormula(ast, "유니버스-상대")).toBe("zscore(roe, 유니버스-상대)");
  });

  it("avg — 가변 항", () => {
    const ast: Expr = {
      op: "avg",
      args: [{ field: "a" }, { field: "b" }, { const: 3 }],
    };
    expect(renderFormula(ast)).toBe("avg(a, b, 3)");
  });

  it("null/undefined 는 빈 문자열", () => {
    expect(renderFormula(null)).toBe("");
    expect(renderFormula(undefined)).toBe("");
  });

  it("미완성 노드 — 빈 field/n 은 ? placeholder", () => {
    expect(renderFormula({ op: "sum_last_n_quarters" } as Expr)).toBe(
      "sum_last_n_quarters(?, n=?)",
    );
  });
});

describe("ALLOWED_OPS — No Advice 경계 (ADR-0022 D1)", () => {
  it("허용 op 12종을 정확히 포함", () => {
    expect(ALLOWED_OPS).toEqual([
      "add",
      "sub",
      "mul",
      "div",
      "sum_last_n_quarters",
      "avg",
      "ratio_pct",
      "weighted_sum",
      "zscore",
      "percentile",
      "min_max_scale",
      "winsorize",
    ]);
  });

  it("금지 서수·신호 op 가 부재 — rank/top_n/bottom_n/sign/step", () => {
    const forbidden = ["rank", "top_n", "bottom_n", "sign", "step"];
    for (const op of forbidden) {
      expect(ALLOWED_OPS as readonly string[]).not.toContain(op);
    }
  });

  it("OP_SPECS 가 모든 허용 op 를 커버", () => {
    for (const op of ALLOWED_OPS) {
      expect(OP_SPECS[op]).toBeDefined();
    }
  });
});

describe("collectFields — inputs 자동 수집", () => {
  it("중첩 식의 field leaf 를 중복 제거 + 순서 유지", () => {
    const ast: Expr = {
      op: "div",
      left: {
        op: "sub",
        left: { field: "shares_issued" },
        right: { field: "shares_treasury" },
      },
      right: { field: "shares_issued" },
    };
    expect(collectFields(ast)).toEqual(["shares_issued", "shares_treasury"]);
  });

  it("op 의 field 속성도 수집 (sum_last_n_quarters / zscore)", () => {
    const ast: Expr = {
      op: "add",
      left: { op: "sum_last_n_quarters", field: "revenue", n: 4 },
      right: { op: "zscore", field: "roe" },
    };
    expect(collectFields(ast)).toEqual(["revenue", "roe"]);
  });
});

describe("makeOpSkeleton — op 별 골격", () => {
  it("binary op 는 left/right 골격", () => {
    const sk = makeOpSkeleton("add");
    expect(sk.op).toBe("add");
    expect(sk.left).toBeDefined();
    expect(sk.right).toBeDefined();
  });

  it("weighted_sum 은 args + weights 동반", () => {
    const sk = makeOpSkeleton("weighted_sum");
    expect(sk.args).toHaveLength(1);
    expect(sk.weights).toHaveLength(1);
  });

  it("winsorize 는 args + lower/upper 동반", () => {
    const sk = makeOpSkeleton("winsorize");
    expect(sk.args).toBeDefined();
    expect(sk.lower).toBe(0);
    expect(sk.upper).toBe(0);
  });

  it("field op 는 field 골격", () => {
    const sk = makeOpSkeleton("zscore");
    expect(sk.field).toBe("");
  });
});
