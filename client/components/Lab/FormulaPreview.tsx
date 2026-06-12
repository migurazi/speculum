"use client";

/**
 * FormulaPreview — AST 를 사람이 읽는 수식 텍스트로 렌더 (Fidelity, ADR-0022 D6).
 *
 * 산출식·가중치·정규화 모집단을 항상 보이게 한다. 평가 결과 숫자·등락색·랭킹은
 * 본 컴포넌트 범위 밖 (No Advice) — 정의(식 자체)만 monospace 텍스트로 표시.
 *
 * renderFormula(순수 함수, lib/factor/ast.ts)에 위임 — 렌더 로직은 단위 테스트로
 * 직접 검증한다.
 */

import { useTranslations } from "next-intl";

import { renderFormula, type Expr } from "@/lib/factor/ast";
import { cn } from "@/lib/utils";

interface FormulaPreviewProps {
  readonly ast: Expr | null | undefined;
  readonly className?: string;
}

export function FormulaPreview({ ast, className }: FormulaPreviewProps): JSX.Element {
  const t = useTranslations("lab");
  // 유니버스-상대 정규화 op 의 모집단 라벨을 i18n 으로 주입 (Fidelity 표기).
  const rendered = renderFormula(ast, t("formula.universeRelative"));

  return (
    <div className={cn("space-y-1", className)}>
      <div className="text-xs font-medium text-neutral-600">
        {t("formula.previewLabel")}
      </div>
      <pre className="overflow-x-auto rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 font-mono text-xs text-neutral-800">
        {rendered.length > 0 ? rendered : t("formula.previewEmpty")}
      </pre>
    </div>
  );
}
