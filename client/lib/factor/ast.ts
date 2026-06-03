/**
 * Factor AST — Factor Lab editor 의 expression 모델 + 허용 op 정의.
 *
 * shared/schemas/factor-pack-v1.json 의 expression oneOf (exprConst / exprField /
 * exprOp) 와 1:1 매핑. ADR-0022 D1 의 허용 op 집합만 노출한다.
 *
 * No Advice 경계 (ADR-0022 D1):
 *   rank / top_n / bottom_n / sign / step 은 본 모듈에 절대 등장하지 않는다.
 *   서수 라벨·임계 신호 = 시스템 큐레이션 우회 (ADR-0007 D5.2). op 선택지는
 *   ALLOWED_OPS 단일 출처로만 생성되므로 금지 op 가 UI 에 새어 들어갈 수 없다.
 *
 * Fidelity (ADR-0022 D6):
 *   산출식·가중치·정규화 모집단이 항상 visible 해야 하므로, AST 를 사람이 읽는
 *   수식으로 렌더하는 순수 함수(renderFormula)를 함께 제공한다.
 */

/** 상수 leaf — { const: number }. */
export interface ExprConst {
  readonly const: number;
}

/** field leaf — { field: string }. 입력 field 이름 참조. */
export interface ExprField {
  readonly field: string;
}

/**
 * 연산 노드 — op 별로 사용하는 속성이 다르다 (schema 의 exprOp).
 * 모든 속성을 optional 로 두고, op 의 arity 정의(OP_SPECS)가 무엇이 필수인지
 * 결정한다. validate endpoint 가 최종 권위 — client 는 형태만 안내.
 */
export interface ExprOp {
  readonly op: AllowedOp;
  readonly left?: Expr;
  readonly right?: Expr;
  readonly args?: ReadonlyArray<Expr>;
  readonly field?: string;
  readonly n?: number;
  readonly weights?: ReadonlyArray<number>;
  readonly lower?: number;
  readonly upper?: number;
}

/** expression = const | field | op (재귀). */
export type Expr = ExprConst | ExprField | ExprOp;

/**
 * 허용 op — ADR-0022 D1. rank/top_n/sign 등 서수·신호 op 는 의도적으로 부재.
 * 이 배열이 op 드롭다운의 단일 출처이므로 금지 op 가 노출될 수 없다.
 */
export const ALLOWED_OPS = [
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
] as const;

export type AllowedOp = (typeof ALLOWED_OPS)[number];

/**
 * op 의 입력 형태(arity) — AST 빌더가 어떤 입력 칸을 렌더할지 결정.
 *
 *  - binary:   left + right (두 하위 식). add/sub/mul/div/ratio_pct.
 *  - variadic: args[] (가변 하위 식). avg/weighted_sum/winsorize.
 *              weighted_sum 은 weights[] 동반, winsorize 는 lower/upper 동반.
 *  - field_n:  field + n (분기별 합산). sum_last_n_quarters.
 *  - field:    field (유니버스-상대 정규화). zscore/percentile/min_max_scale.
 */
export type OpArity = "binary" | "variadic" | "field_n" | "field";

export interface OpSpec {
  readonly arity: OpArity;
  /** weighted_sum 만 true — args 와 동일 길이 가중치 배열 필요. */
  readonly hasWeights: boolean;
  /** winsorize 만 true — lower/upper 상수 경계 필요. */
  readonly hasBounds: boolean;
}

export const OP_SPECS: Readonly<Record<AllowedOp, OpSpec>> = {
  add: { arity: "binary", hasWeights: false, hasBounds: false },
  sub: { arity: "binary", hasWeights: false, hasBounds: false },
  mul: { arity: "binary", hasWeights: false, hasBounds: false },
  div: { arity: "binary", hasWeights: false, hasBounds: false },
  ratio_pct: { arity: "binary", hasWeights: false, hasBounds: false },
  sum_last_n_quarters: { arity: "field_n", hasWeights: false, hasBounds: false },
  avg: { arity: "variadic", hasWeights: false, hasBounds: false },
  weighted_sum: { arity: "variadic", hasWeights: true, hasBounds: false },
  winsorize: { arity: "variadic", hasWeights: false, hasBounds: true },
  zscore: { arity: "field", hasWeights: false, hasBounds: false },
  percentile: { arity: "field", hasWeights: false, hasBounds: false },
  min_max_scale: { arity: "field", hasWeights: false, hasBounds: false },
};

/** 이항 연산자의 중위 기호 — 사람이 읽는 수식 렌더용. */
const BINARY_INFIX: Readonly<Record<string, string>> = {
  add: "+",
  sub: "-",
  mul: "×",
  div: "÷",
};

/** 타입 가드 — const leaf. */
export function isExprConst(e: Expr): e is ExprConst {
  return typeof (e as ExprConst).const === "number"
    && (e as ExprOp).op === undefined;
}

/** 타입 가드 — field leaf. */
export function isExprField(e: Expr): e is ExprField {
  return typeof (e as ExprField).field === "string"
    && (e as ExprOp).op === undefined;
}

/** 타입 가드 — op 노드. */
export function isExprOp(e: Expr): e is ExprOp {
  return typeof (e as ExprOp).op === "string";
}

/**
 * AST 를 사람이 읽는 수식 텍스트로 재귀 렌더 (Fidelity, ADR-0022 D6).
 *
 * 산출식·가중치·정규화 모집단을 항상 보이게 한다. 평가·미리보기 값(숫자 결과)·
 * 등락색·랭킹은 본 함수 범위 밖 — 정의(식 자체)만 텍스트로 표현한다.
 *
 * 예:
 *   sub(field shares_issued, field shares_treasury)
 *     → "(shares_issued - shares_treasury)"
 *   weighted_sum(args=[a,b], weights=[0.6,0.4])
 *     → "weighted_sum(a × 0.6, b × 0.4)"
 *   winsorize(args=[x], lower=0, upper=100)
 *     → "winsorize(x, [0, 100])"
 *   zscore(field roe)
 *     → "zscore(roe, 유니버스-상대)"
 *
 * @param e         렌더할 expression
 * @param relativeLabel 유니버스-상대 정규화 op 의 모집단 라벨(i18n 주입).
 *                      미지정 시 정규화 op 는 모집단 표기 생략.
 */
export function renderFormula(e: Expr | null | undefined, relativeLabel?: string): string {
  if (e === null || e === undefined) return "";

  if (isExprConst(e)) {
    return formatNumber(e.const);
  }
  if (isExprField(e)) {
    return e.field;
  }
  if (!isExprOp(e)) {
    // 알 수 없는 형태 — 빈 노드(작성 중). 명시적 placeholder.
    return "?";
  }

  const spec = OP_SPECS[e.op];

  // 이항: 사칙연산은 중위 기호, ratio_pct 는 함수 표기(% 산출 명시).
  if (spec.arity === "binary") {
    const l = renderFormula(e.left, relativeLabel);
    const r = renderFormula(e.right, relativeLabel);
    if (e.op === "ratio_pct") {
      return `ratio_pct(${l}, ${r})`;
    }
    const infix = BINARY_INFIX[e.op] ?? e.op;
    return `(${l} ${infix} ${r})`;
  }

  // field + n: 분기별 합산. n 을 항상 표기(Fidelity — 윈도 길이 visible).
  if (spec.arity === "field_n") {
    const field = e.field ?? "?";
    const n = e.n ?? "?";
    return `sum_last_n_quarters(${field}, n=${n})`;
  }

  // field: 유니버스-상대 정규화. 모집단 라벨을 항상 표기(Fidelity).
  if (spec.arity === "field") {
    const field = e.field ?? "?";
    const pop = relativeLabel ? `, ${relativeLabel}` : "";
    return `${e.op}(${field}${pop})`;
  }

  // variadic: avg / weighted_sum / winsorize.
  const args = e.args ?? [];
  if (e.op === "weighted_sum") {
    // 가중치를 각 항에 붙여 항상 visible (Fidelity — 가중치 노출).
    const weights = e.weights ?? [];
    const terms = args.map((a, i) => {
      const w = weights[i];
      const rendered = renderFormula(a, relativeLabel);
      return w === undefined ? rendered : `${rendered} × ${formatNumber(w)}`;
    });
    return `weighted_sum(${terms.join(", ")})`;
  }
  if (e.op === "winsorize") {
    // lower/upper 경계를 항상 표기 (Fidelity — 절단 경계 노출).
    const terms = args.map((a) => renderFormula(a, relativeLabel));
    const lower = e.lower === undefined ? "?" : formatNumber(e.lower);
    const upper = e.upper === undefined ? "?" : formatNumber(e.upper);
    return `winsorize(${terms.join(", ")}, [${lower}, ${upper}])`;
  }
  // avg
  const terms = args.map((a) => renderFormula(a, relativeLabel));
  return `${e.op}(${terms.join(", ")})`;
}

/** 숫자 포맷 — 지수 표기 회피, 불필요한 trailing zero 제거. */
function formatNumber(n: number): string {
  if (!Number.isFinite(n)) return String(n);
  // toString 이 정수/소수 모두 합리적으로 처리. (지수 표기는 매우 큰 수만 — 허용)
  return String(n);
}

/**
 * AST 가 참조하는 field leaf 이름을 모두 수집 (중복 제거, 출현 순서 유지).
 * formula.inputs 자동 채우기 보조 — 사용자가 inputs 를 손으로 맞추는 부담 경감.
 */
export function collectFields(e: Expr | null | undefined): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const visit = (node: Expr | null | undefined): void => {
    if (node === null || node === undefined) return;
    if (isExprField(node)) {
      if (!seen.has(node.field)) {
        seen.add(node.field);
        out.push(node.field);
      }
      return;
    }
    if (isExprOp(node)) {
      // op 자체의 field 속성(sum_last_n_quarters / 정규화 op).
      if (typeof node.field === "string" && node.field.length > 0) {
        if (!seen.has(node.field)) {
          seen.add(node.field);
          out.push(node.field);
        }
      }
      visit(node.left);
      visit(node.right);
      for (const a of node.args ?? []) visit(a);
    }
  };
  visit(e);
  return out;
}

/**
 * op 선택 시 그 arity 에 맞는 골격 노드를 생성 (빈 하위 식 포함).
 * 빌더가 op 를 바꿀 때 기존 입력을 버리고 새 형태로 초기화하는 데 사용.
 */
export function makeOpSkeleton(op: AllowedOp): ExprOp {
  const spec = OP_SPECS[op];
  const node: {
    op: AllowedOp;
    left?: Expr;
    right?: Expr;
    args?: Expr[];
    field?: string;
    n?: number;
    weights?: number[];
    lower?: number;
    upper?: number;
  } = { op };

  if (spec.arity === "binary") {
    node.left = { const: 0 };
    node.right = { const: 0 };
  } else if (spec.arity === "field_n") {
    node.field = "";
    node.n = 4;
  } else if (spec.arity === "field") {
    node.field = "";
  } else {
    // variadic
    node.args = [{ const: 0 }];
    if (spec.hasWeights) node.weights = [1];
    if (spec.hasBounds) {
      node.lower = 0;
      node.upper = 0;
    }
  }
  return node as ExprOp;
}
