"use client";

/**
 * SecurityTypeSelector — 자산군(security_type) 다중 선택 체크박스 그룹.
 *
 * ADR-0023 D4 No Advice 경계 준수:
 *   - 4개 자산군(common / preferred / etf / reit)을 동등 체크박스로 노출.
 *   - 보통주에 색·크기·순서상 특권 없음. grayscale 중립 톤(ADR-0007 D2.2).
 *   - "추천/인기/베스트" 류 어휘 0. 등락색(빨강/파랑) 0.
 *   - 최소 1개 강제: 선택된 항목이 1개인 경우 해당 항목의 체크박스를 disable 해
 *     빈 선택으로 422 를 유발하지 않도록 UI 레벨에서 차단.
 *
 * 관련:
 *   - backend `app.schemas.screen.SecurityTypeEnum`.
 *   - lib/api/screen.ts `SecurityType` / `SECURITY_TYPE_OPTIONS`.
 *   - ADR-0023 D7 (security_types → screen query → result_hash 자동 freeze).
 */

import { useTranslations } from "next-intl";

import {
  SECURITY_TYPE_OPTIONS,
  type SecurityType,
} from "@/lib/api/screen";
import { cn } from "@/lib/utils";

interface SecurityTypeSelectorProps {
  /** 현재 선택된 자산군 집합. 최소 1개 보장이 props 계약. */
  readonly selected: ReadonlyArray<SecurityType>;
  /** 선택 변경 시 호출 — 항상 최소 1개 포함. */
  readonly onChange: (next: ReadonlyArray<SecurityType>) => void;
  readonly className?: string;
}

/** 자산군 토글 — 최소 1개 불변식 유지. */
function toggleSecurityType(
  current: ReadonlyArray<SecurityType>,
  type: SecurityType,
): ReadonlyArray<SecurityType> {
  if (current.includes(type)) {
    // 1개만 남았으면 제거 금지 — 최소 1개 강제.
    if (current.length === 1) return current;
    return current.filter((t) => t !== type);
  }
  return [...current, type];
}

/** 각 자산군이 최소-1개 규칙에 의해 비활성화되어야 하는지 판단. */
function isOnlySelected(
  current: ReadonlyArray<SecurityType>,
  type: SecurityType,
): boolean {
  return current.length === 1 && current[0] === type;
}

export function SecurityTypeSelector({
  selected,
  onChange,
  className,
}: SecurityTypeSelectorProps): JSX.Element {
  const t = useTranslations("screener");

  return (
    <div className={cn("space-y-2", className)}>
      <div className="text-sm font-medium text-neutral-700">
        {t("securityTypeSelector.sectionLabel")}
      </div>

      <fieldset>
        <legend className="sr-only">
          {t("securityTypeSelector.legend")}
        </legend>

        {/* 4개 자산군 동등 체크박스 — 순서·크기·색 동일. ADR-0023 D4. */}
        <ul className="flex flex-wrap gap-2">
          {SECURITY_TYPE_OPTIONS.map((type) => {
            const checked = selected.includes(type);
            const onlySelected = isOnlySelected(selected, type);

            return (
              <li key={type}>
                <label
                  className={cn(
                    "inline-flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-xs",
                    checked
                      ? "border-neutral-400 bg-neutral-100 text-neutral-900"
                      : "border-neutral-200 bg-white text-neutral-600 hover:border-neutral-300 hover:bg-neutral-50",
                    onlySelected && "cursor-not-allowed opacity-60",
                  )}
                >
                  <input
                    type="checkbox"
                    value={type}
                    checked={checked}
                    disabled={onlySelected}
                    onChange={() => {
                      onChange(toggleSecurityType(selected, type));
                    }}
                    aria-label={t(`securityTypeSelector.types.${type}`)}
                    className="h-3.5 w-3.5 rounded border-neutral-300"
                  />
                  <span>{t(`securityTypeSelector.types.${type}`)}</span>
                </label>
              </li>
            );
          })}
        </ul>

        {selected.length > 0 ? (
          <p className="mt-1.5 text-xs text-neutral-500">
            {t("securityTypeSelector.selectedCount", { count: selected.length })}
          </p>
        ) : null}
      </fieldset>
    </div>
  );
}
