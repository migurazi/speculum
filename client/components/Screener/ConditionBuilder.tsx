"use client";

/**
 * ConditionBuilder — Screener 조건 입력 component.
 *
 * 한 row 당 (factor / op / value) 3 input. 사용자 클릭으로 row 추가·제거.
 *
 * factor 선택: /api/factors 드롭다운 (opton label = name, value = canonical_id).
 * factor 목록 로딩 중에는 select 를 disabled 처리. fetch 실패 시 텍스트 input
 * fallback + 안내 메시지.
 *
 * 관련:
 * - backend `app.api.routes.meta.get_factors` wire schema.
 * - lib/api/factors.ts — fetchFactors().
 * - ADR-0007 D1 (Screener 결과의 기본 정렬 — 시가총액 default).
 * - M0_PLAN T36 / AC-F-03.
 */

import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { fetchFactors } from "@/lib/api/factors";
import {
  SCREEN_OP_OPTIONS,
  type ScreenCondition,
  type ScreenOp,
} from "@/lib/api/screen";
import { cn } from "@/lib/utils";

interface ConditionBuilderProps {
  readonly conditions: ReadonlyArray<ScreenCondition>;
  readonly onChange: (next: ReadonlyArray<ScreenCondition>) => void;
  readonly className?: string;
}

const EMPTY_CONDITION: ScreenCondition = {
  factor: "",
  op: "<",
  value: "",
};

/** factor 목록 쿼리 키 — 앱 전체 공유 (동일 cache). */
export const FACTORS_QUERY_KEY = ["factors"] as const;

/** factor pack 은 정적 — 30분 stale 로 불필요한 refetch 차단. */
const FACTORS_STALE_TIME = 30 * 60 * 1000;

export function ConditionBuilder({
  conditions,
  onChange,
  className,
}: ConditionBuilderProps): JSX.Element {
  const t = useTranslations("screener");

  const {
    data: factors,
    isLoading: factorsLoading,
    isError: factorsError,
  } = useQuery({
    queryKey: FACTORS_QUERY_KEY,
    queryFn: ({ signal }) => fetchFactors(signal),
    staleTime: FACTORS_STALE_TIME,
    // retry 횟수는 QueryClient 기본값에 위임 (운영: 3회, 테스트: 0회).
    // 컴포넌트 레벨에서 override 하지 않아야 테스트가 fake timer 없이 동작.
  });

  const updateRow = (
    index: number,
    patch: Partial<ScreenCondition>,
  ): void => {
    const next = conditions.map((c, i) =>
      i === index ? { ...c, ...patch } : c,
    );
    onChange(next);
  };

  const addRow = (): void => {
    onChange([...conditions, EMPTY_CONDITION]);
  };

  const removeRow = (index: number): void => {
    onChange(conditions.filter((_, i) => i !== index));
  };

  /** factor 선택 영역 렌더 — 로딩/에러/정상 상태 분기. */
  const renderFactorInput = (condition: ScreenCondition, index: number): JSX.Element => {
    // fetch 실패 시 기존 text input fallback
    if (factorsError) {
      return (
        <input
          type="text"
          value={condition.factor}
          onChange={(e) => updateRow(index, { factor: e.target.value })}
          placeholder="factor (예: per:ttm-consolidated-ifrs)"
          aria-label={t("conditionBuilder.factorAriaLabel", { index: index + 1 })}
          className="min-w-0 flex-1 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
        />
      );
    }

    return (
      <select
        value={condition.factor}
        onChange={(e) => updateRow(index, { factor: e.target.value })}
        aria-label={t("conditionBuilder.factorAriaLabel", { index: index + 1 })}
        disabled={factorsLoading}
        className={cn(
          "min-w-0 flex-1 rounded border border-neutral-300 px-2 py-1 text-xs",
          factorsLoading && "cursor-wait bg-neutral-50 text-neutral-400",
          !factorsLoading && "bg-white",
        )}
      >
        {/* 미선택 placeholder option */}
        <option value="" disabled>
          {factorsLoading
            ? t("conditionBuilder.factorLoading")
            : t("conditionBuilder.factorPlaceholder")}
        </option>
        {factors?.map((f) => (
          <option key={f.canonicalId} value={f.canonicalId}>
            {f.name}
          </option>
        ))}
      </select>
    );
  };

  return (
    <div className={cn("space-y-3", className)}>
      <div className="text-sm font-medium text-neutral-700">
        {t("conditionBuilder.sectionLabel")}
      </div>

      {/* factor fetch 실패 안내 */}
      {factorsError ? (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {t("conditionBuilder.loadError")}
        </p>
      ) : null}

      {conditions.length === 0 ? (
        <p className="text-xs text-neutral-500">
          {t("conditionBuilder.noConditions")}
        </p>
      ) : (
        <ul className="space-y-2">
          {conditions.map((condition, index) => (
            <li
              key={index}
              className="flex flex-wrap items-center gap-2 rounded-md border border-neutral-200 bg-white p-2"
            >
              {renderFactorInput(condition, index)}
              <select
                value={condition.op}
                onChange={(e) =>
                  updateRow(index, { op: e.target.value as ScreenOp })
                }
                aria-label={t("conditionBuilder.opAriaLabel", { index: index + 1 })}
                className="rounded border border-neutral-300 bg-white px-2 py-1 font-mono text-xs"
              >
                {SCREEN_OP_OPTIONS.map((op) => (
                  <option key={op} value={op}>
                    {op}
                  </option>
                ))}
              </select>
              <input
                type="text"
                value={condition.value}
                onChange={(e) =>
                  updateRow(index, { value: e.target.value })
                }
                placeholder={t("conditionBuilder.valuePlaceholder")}
                aria-label={t("conditionBuilder.valueAriaLabel", { index: index + 1 })}
                className="w-28 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
              />
              <button
                type="button"
                onClick={() => removeRow(index)}
                aria-label={t("conditionBuilder.removeAriaLabel", { index: index + 1 })}
                className="rounded p-1 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900"
              >
                <Trash2 size={16} />
              </button>
            </li>
          ))}
        </ul>
      )}

      <button
        type="button"
        onClick={addRow}
        className="inline-flex items-center gap-1 rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50"
      >
        <Plus size={14} aria-hidden="true" />
        {t("conditionBuilder.addButton")}
      </button>
    </div>
  );
}
