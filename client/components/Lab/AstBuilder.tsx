"use client";

/**
 * AstBuilder — factor 산출식(AST)을 구조화 재귀 입력으로 작성하는 빌더.
 *
 * 완전 비주얼 노드 그래프는 과하므로, "적정 깊이의 중첩 폼" 으로 구현한다.
 * 각 노드는 종류 선택(상수 / field / 연산) 드롭다운 + 종류별 입력 칸으로 구성되며,
 * 연산 노드는 하위 식을 재귀로 다시 AstBuilder 로 렌더한다.
 *
 * No Advice 경계 (ADR-0022 D1):
 *   op 드롭다운은 ALLOWED_OPS 단일 출처로만 채운다. rank/top_n/sign 등 서수·신호
 *   op 는 ALLOWED_OPS 에 부재하므로 선택지에 절대 나타날 수 없다.
 *
 * Fidelity (ADR-0022 D6):
 *   weighted_sum 의 weights, winsorize 의 lower/upper, 정규화 op 의 field 가 모두
 *   별도 입력 칸으로 항상 노출된다 (숨김 없음).
 *
 * 상태 모델:
 *   본 컴포넌트는 controlled — 상위가 expr + onChange 를 준다. 노드 편집 시 새
 *   불변 객체를 만들어 onChange 로 올린다. 깊이(depth)는 시각적 들여쓰기에만 사용.
 */

import { Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  ALLOWED_OPS,
  OP_SPECS,
  isExprConst,
  isExprField,
  isExprOp,
  makeOpSkeleton,
  type AllowedOp,
  type Expr,
  type ExprOp,
} from "@/lib/factor/ast";
import { cn } from "@/lib/utils";

/** 노드 종류 — UI 선택용 (schema 의 oneOf 분기). */
type NodeKind = "const" | "field" | "op";

interface AstBuilderProps {
  readonly expr: Expr | null;
  readonly onChange: (next: Expr) => void;
  /** 재귀 깊이 — 들여쓰기/접근성 라벨 식별용. 루트는 0. */
  readonly depth?: number;
  /** 라벨 접두 — 하위 식 식별(예: "좌변", "args[1]"). */
  readonly label?: string;
}

function kindOf(expr: Expr | null): NodeKind {
  if (expr === null) return "const";
  if (isExprConst(expr)) return "const";
  if (isExprField(expr)) return "field";
  if (isExprOp(expr)) return "op";
  return "const";
}

/** 종류 전환 시 그 종류의 기본 골격 노드 생성. */
function skeletonForKind(kind: NodeKind): Expr {
  if (kind === "const") return { const: 0 };
  if (kind === "field") return { field: "" };
  return makeOpSkeleton(ALLOWED_OPS[0]);
}

export function AstBuilder({
  expr,
  onChange,
  depth = 0,
  label,
}: AstBuilderProps): JSX.Element {
  const t = useTranslations("lab");
  const node = expr ?? { const: 0 };
  const kind = kindOf(node);

  const onKindChange = (nextKind: NodeKind): void => {
    if (nextKind === kind) return;
    onChange(skeletonForKind(nextKind));
  };

  return (
    <div
      className={cn(
        "space-y-2 rounded-md border border-neutral-200 bg-white p-2",
        depth > 0 && "ml-3 border-l-2 border-l-neutral-200",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        {label ? (
          <span className="text-xs font-medium text-neutral-500">{label}</span>
        ) : null}
        {/* 노드 종류 선택 — const / field / 연산 */}
        <select
          value={kind}
          onChange={(e) => onKindChange(e.target.value as NodeKind)}
          aria-label={t("ast.kindAriaLabel")}
          className="rounded border border-neutral-300 bg-white px-2 py-1 text-xs"
        >
          <option value="const">{t("ast.kindConst")}</option>
          <option value="field">{t("ast.kindField")}</option>
          <option value="op">{t("ast.kindOp")}</option>
        </select>

        {/* 종류별 inline 입력 */}
        {kind === "const" && isExprConst(node) ? (
          <input
            type="number"
            value={Number.isFinite(node.const) ? node.const : 0}
            onChange={(e) => onChange({ const: Number(e.target.value) })}
            aria-label={t("ast.constAriaLabel")}
            className="w-32 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
          />
        ) : null}

        {kind === "field" && isExprField(node) ? (
          <input
            type="text"
            value={node.field}
            onChange={(e) => onChange({ field: e.target.value })}
            placeholder={t("ast.fieldPlaceholder")}
            aria-label={t("ast.fieldAriaLabel")}
            className="w-56 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
          />
        ) : null}

        {kind === "op" && isExprOp(node) ? (
          <select
            value={node.op}
            onChange={(e) =>
              onChange(makeOpSkeleton(e.target.value as AllowedOp))
            }
            aria-label={t("ast.opAriaLabel")}
            className="rounded border border-neutral-300 bg-white px-2 py-1 font-mono text-xs"
          >
            {/* 허용 op 만 — rank/top_n/sign 부재 (ADR-0022 D1) */}
            {ALLOWED_OPS.map((op) => (
              <option key={op} value={op}>
                {op}
              </option>
            ))}
          </select>
        ) : null}
      </div>

      {/* op 노드의 하위 식 — arity 에 따라 분기 */}
      {kind === "op" && isExprOp(node) ? (
        <OpChildren node={node} onChange={onChange} depth={depth} />
      ) : null}
    </div>
  );
}

interface OpChildrenProps {
  readonly node: ExprOp;
  readonly onChange: (next: Expr) => void;
  readonly depth: number;
}

/** op 노드의 arity 별 하위 입력 — binary / field_n / field / variadic. */
function OpChildren({ node, onChange, depth }: OpChildrenProps): JSX.Element {
  const t = useTranslations("lab");
  const spec = OP_SPECS[node.op];

  if (spec.arity === "binary") {
    return (
      <div className="space-y-2">
        <AstBuilder
          expr={node.left ?? { const: 0 }}
          onChange={(left) => onChange({ ...node, left })}
          depth={depth + 1}
          label={t("ast.leftLabel")}
        />
        <AstBuilder
          expr={node.right ?? { const: 0 }}
          onChange={(right) => onChange({ ...node, right })}
          depth={depth + 1}
          label={t("ast.rightLabel")}
        />
      </div>
    );
  }

  if (spec.arity === "field_n") {
    return (
      <div className="ml-3 flex flex-wrap items-center gap-2 border-l-2 border-l-neutral-200 pl-3">
        <label className="text-xs text-neutral-600">
          {t("ast.fieldLabel")}
          <input
            type="text"
            value={node.field ?? ""}
            onChange={(e) => onChange({ ...node, field: e.target.value })}
            placeholder={t("ast.fieldPlaceholder")}
            aria-label={t("ast.fieldAriaLabel")}
            className="ml-1 w-48 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
          />
        </label>
        <label className="text-xs text-neutral-600">
          {t("ast.nLabel")}
          <input
            type="number"
            min={1}
            max={20}
            value={node.n ?? 4}
            onChange={(e) => onChange({ ...node, n: Number(e.target.value) })}
            aria-label={t("ast.nAriaLabel")}
            className="ml-1 w-20 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
          />
        </label>
      </div>
    );
  }

  if (spec.arity === "field") {
    return (
      <div className="ml-3 flex flex-wrap items-center gap-2 border-l-2 border-l-neutral-200 pl-3">
        <label className="text-xs text-neutral-600">
          {t("ast.fieldLabel")}
          <input
            type="text"
            value={node.field ?? ""}
            onChange={(e) => onChange({ ...node, field: e.target.value })}
            placeholder={t("ast.fieldPlaceholder")}
            aria-label={t("ast.fieldAriaLabel")}
            className="ml-1 w-48 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
          />
        </label>
        {/* 정규화 모집단을 항상 명시 (Fidelity) — 모집단은 유니버스로 고정. */}
        <span className="text-xs text-neutral-400">
          {t("formula.universeRelative")}
        </span>
      </div>
    );
  }

  // variadic — avg / weighted_sum / winsorize
  return <VariadicChildren node={node} onChange={onChange} depth={depth} />;
}

/** variadic op 의 args[] (+ weighted_sum weights, winsorize lower/upper). */
function VariadicChildren({ node, onChange, depth }: OpChildrenProps): JSX.Element {
  const t = useTranslations("lab");
  const spec = OP_SPECS[node.op];
  const args = node.args ?? [];
  const weights = node.weights ?? [];

  const updateArg = (index: number, next: Expr): void => {
    const nextArgs = args.map((a, i) => (i === index ? next : a));
    onChange({ ...node, args: nextArgs });
  };

  const updateWeight = (index: number, value: number): void => {
    const nextWeights = args.map((_, i) =>
      i === index ? value : (weights[i] ?? 0),
    );
    onChange({ ...node, weights: nextWeights });
  };

  const addArg = (): void => {
    const nextArgs: Expr[] = [...args, { const: 0 }];
    onChange(
      spec.hasWeights
        ? { ...node, args: nextArgs, weights: [...weights, 1] }
        : { ...node, args: nextArgs },
    );
  };

  const removeArg = (index: number): void => {
    const nextArgs = args.filter((_, i) => i !== index);
    onChange(
      spec.hasWeights
        ? { ...node, args: nextArgs, weights: weights.filter((_, i) => i !== index) }
        : { ...node, args: nextArgs },
    );
  };

  return (
    <div className="space-y-2">
      <ul className="space-y-2">
        {args.map((arg, index) => (
          <li key={index} className="space-y-1">
            <div className="flex items-start gap-2">
              <div className="flex-1">
                <AstBuilder
                  expr={arg}
                  onChange={(next) => updateArg(index, next)}
                  depth={depth + 1}
                  label={t("ast.argLabel", { index: index + 1 })}
                />
              </div>
              <button
                type="button"
                onClick={() => removeArg(index)}
                aria-label={t("ast.removeArgAriaLabel", { index: index + 1 })}
                className="mt-1 rounded p-1 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900"
              >
                <Trash2 size={14} />
              </button>
            </div>
            {/* weighted_sum: 항별 가중치 — 항상 노출 (Fidelity) */}
            {spec.hasWeights ? (
              <label className="ml-3 block text-xs text-neutral-600">
                {t("ast.weightLabel")}
                <input
                  type="number"
                  step="any"
                  value={weights[index] ?? 0}
                  onChange={(e) => updateWeight(index, Number(e.target.value))}
                  aria-label={t("ast.weightAriaLabel", { index: index + 1 })}
                  className="ml-1 w-24 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
                />
              </label>
            ) : null}
          </li>
        ))}
      </ul>

      <button
        type="button"
        onClick={addArg}
        className="inline-flex items-center gap-1 rounded-md border border-neutral-300 px-2 py-1 text-xs font-medium text-neutral-700 hover:bg-neutral-50"
      >
        <Plus size={12} aria-hidden="true" />
        {t("ast.addArg")}
      </button>

      {/* winsorize: 하한/상한 절단 경계 — 항상 노출 (Fidelity) */}
      {spec.hasBounds ? (
        <div className="flex flex-wrap items-center gap-3 rounded-md bg-neutral-50 px-2 py-1">
          <label className="text-xs text-neutral-600">
            {t("ast.lowerLabel")}
            <input
              type="number"
              step="any"
              value={node.lower ?? 0}
              onChange={(e) => onChange({ ...node, lower: Number(e.target.value) })}
              aria-label={t("ast.lowerAriaLabel")}
              className="ml-1 w-24 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
            />
          </label>
          <label className="text-xs text-neutral-600">
            {t("ast.upperLabel")}
            <input
              type="number"
              step="any"
              value={node.upper ?? 0}
              onChange={(e) => onChange({ ...node, upper: Number(e.target.value) })}
              aria-label={t("ast.upperAriaLabel")}
              className="ml-1 w-24 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
            />
          </label>
        </div>
      ) : null}
    </div>
  );
}
