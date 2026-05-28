"use client";

/**
 * ConditionBuilder — Screener 조건 입력 component.
 *
 * 한 row 당 (factor / op / value) 3 input. 사용자 클릭으로 row 추가·제거.
 * M0 MVP: factor 는 free text input (validation 은 backend Pydantic 의 regex
 * 가 강제). M1+ shadcn Combobox 로 빌트인 factor list autocomplete.
 *
 * 관련:
 * - backend `app.schemas.screen.ConditionIn` 의 wire schema.
 * - ADR-0007 D1 (Screener 결과의 기본 정렬 — 시가총액 default).
 * - M0_PLAN T36 / AC-F-03.
 */

import { Plus, Trash2 } from "lucide-react";

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

export function ConditionBuilder({
  conditions,
  onChange,
  className,
}: ConditionBuilderProps): JSX.Element {
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

  return (
    <div className={cn("space-y-3", className)}>
      <div className="text-sm font-medium text-neutral-700">조건</div>

      {conditions.length === 0 ? (
        <p className="text-xs text-neutral-500">
          조건을 한 개 이상 추가해야 실행할 수 있습니다.
        </p>
      ) : (
        <ul className="space-y-2">
          {conditions.map((condition, index) => (
            <li
              key={index}
              className="flex flex-wrap items-center gap-2 rounded-md border border-neutral-200 bg-white p-2"
            >
              <input
                type="text"
                value={condition.factor}
                onChange={(e) =>
                  updateRow(index, { factor: e.target.value })
                }
                placeholder="factor (예: per:ttm-consolidated-ifrs)"
                aria-label={`조건 ${index + 1} factor`}
                className="min-w-0 flex-1 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
              />
              <select
                value={condition.op}
                onChange={(e) =>
                  updateRow(index, { op: e.target.value as ScreenOp })
                }
                aria-label={`조건 ${index + 1} 연산자`}
                className="rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
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
                placeholder="값"
                aria-label={`조건 ${index + 1} 값`}
                className="w-28 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
              />
              <button
                type="button"
                onClick={() => removeRow(index)}
                aria-label={`조건 ${index + 1} 삭제`}
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
        조건 추가
      </button>
    </div>
  );
}
