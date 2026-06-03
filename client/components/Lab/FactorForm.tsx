"use client";

/**
 * FactorForm — 단일 factor 정의 편집 폼.
 *
 * 메타데이터(canonical_id, name, unit, tags, description) + 산출식(AstBuilder) +
 * 산출식 미리보기(FormulaPreview)를 한 카드에 묶는다. formula.inputs 는 AST 의
 * field leaf 에서 자동 수집(collectFields)되어 사용자가 손으로 맞출 부담을 던다.
 *
 * No Advice: 어떤 입력 칸도 판단/추천/등급 어휘를 요구하지 않는다. unit 은 schema
 * enum(FACTOR_UNITS)으로만 선택.
 */

import { Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  FACTOR_UNITS,
  type FactorDef,
  type FactorUnit,
} from "@/lib/api/factor-packs";
import { collectFields, type Expr } from "@/lib/factor/ast";

import { AstBuilder } from "./AstBuilder";
import { FormulaPreview } from "./FormulaPreview";

interface FactorFormProps {
  readonly factor: FactorDef;
  readonly index: number;
  readonly onChange: (next: FactorDef) => void;
  readonly onRemove: () => void;
}

export function FactorForm({
  factor,
  index,
  onChange,
  onRemove,
}: FactorFormProps): JSX.Element {
  const t = useTranslations("lab");

  const patch = (p: Partial<FactorDef>): void => {
    onChange({ ...factor, ...p });
  };

  /**
   * AST 변경 시 inputs 를 field leaf 에서 자동 재수집.
   * formula.inputs 는 adapter contract 이므로 AST 와 동기화되어야 한다.
   */
  const onAstChange = (ast: Expr): void => {
    onChange({
      ...factor,
      formula: { ast, inputs: collectFields(ast) },
    });
  };

  // tags 는 쉼표 구분 문자열 ↔ 배열로 변환 (UX 단순화).
  const tagsText = factor.tags.join(", ");
  const onTagsChange = (text: string): void => {
    const tags = text
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    patch({ tags });
  };

  return (
    <fieldset className="space-y-4 rounded-lg border border-neutral-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <legend className="text-sm font-semibold text-neutral-900">
          {t("factor.heading", { index: index + 1 })}
        </legend>
        <button
          type="button"
          onClick={onRemove}
          aria-label={t("factor.removeAriaLabel", { index: index + 1 })}
          className="inline-flex items-center gap-1 rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
        >
          <Trash2 size={14} aria-hidden="true" />
          {t("factor.remove")}
        </button>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block text-xs font-medium text-neutral-600">
          {t("factor.canonicalId")}
          <input
            type="text"
            value={factor.canonical_id}
            onChange={(e) => patch({ canonical_id: e.target.value })}
            placeholder={t("factor.canonicalIdPlaceholder")}
            className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
          />
        </label>

        <label className="block text-xs font-medium text-neutral-600">
          {t("factor.name")}
          <input
            type="text"
            value={factor.name}
            onChange={(e) => patch({ name: e.target.value })}
            placeholder={t("factor.namePlaceholder")}
            className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-xs"
          />
        </label>

        <label className="block text-xs font-medium text-neutral-600">
          {t("factor.unit")}
          <select
            value={factor.unit}
            onChange={(e) => patch({ unit: e.target.value as FactorUnit })}
            className="mt-1 w-full rounded border border-neutral-300 bg-white px-2 py-1 text-xs"
          >
            {FACTOR_UNITS.map((u) => (
              <option key={u} value={u}>
                {t(`factor.units.${u}`)}
              </option>
            ))}
          </select>
        </label>

        <label className="block text-xs font-medium text-neutral-600">
          {t("factor.tags")}
          <input
            type="text"
            value={tagsText}
            onChange={(e) => onTagsChange(e.target.value)}
            placeholder={t("factor.tagsPlaceholder")}
            className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
          />
        </label>
      </div>

      <label className="block text-xs font-medium text-neutral-600">
        {t("factor.description")}
        <textarea
          value={factor.description}
          onChange={(e) => patch({ description: e.target.value })}
          placeholder={t("factor.descriptionPlaceholder")}
          rows={2}
          className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-xs"
        />
      </label>

      <div className="space-y-2">
        <div className="text-xs font-medium text-neutral-600">
          {t("factor.formula")}
        </div>
        <AstBuilder expr={factor.formula.ast} onChange={onAstChange} />
      </div>

      <FormulaPreview ast={factor.formula.ast} />

      {/* inputs 는 AST 에서 자동 수집 — 읽기 전용 표시 (adapter contract 가시화) */}
      <div className="text-xs text-neutral-500">
        {t("factor.inputs")}:{" "}
        <span className="font-mono text-neutral-700">
          {factor.formula.inputs.length > 0
            ? factor.formula.inputs.join(", ")
            : t("factor.inputsEmpty")}
        </span>
      </div>
    </fieldset>
  );
}
