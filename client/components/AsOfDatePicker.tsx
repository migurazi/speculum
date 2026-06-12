"use client";

/**
 * AsOfDatePicker — ADR-0008 D1 의 일급 UI element.
 *
 * 모든 화면의 header 우측에 배치 (T35 합류 시 layout 의 nav bar). 사용자가
 * date 선택하면 zustand store 갱신 → 모든 데이터 query 가 새 asOf 로 reload.
 *
 * MVP design (M0):
 *   - native `<input type="date">` — browser-native calendar picker.
 *     shadcn/ui 의 fancy calendar 는 M1+ 합류 시 swap.
 *   - 영업일 client-side validation X — backend AsOfPolicy 가 normalize +
 *     `X-AsOf-Normalized` header 반환. 본 component 는 사용자 입력 그대로
 *     전달.
 *   - "오늘 vs 과거" 시각 차이 (ADR-0008 D4.1) — isToday=true 면 회색,
 *     false 면 강조 색상 + reset button.
 *
 * 관련 ADR:
 * - ADR-0008 D1 (전역 state), D4 (today vs past UI affordance).
 * - M0_PLAN T34 / AC-D-04 (영업일 캘린더 정확성).
 */

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { useAsOfStore, useIsToday } from "@/state/as-of-store";
import { cn } from "@/lib/utils";

interface AsOfDatePickerProps {
  readonly className?: string;
}

export function AsOfDatePicker({
  className,
}: AsOfDatePickerProps): JSX.Element {
  const t = useTranslations("common");
  const asOf = useAsOfStore((s) => s.asOf);
  const isToday = useIsToday();
  const setAsOf = useAsOfStore((s) => s.setAsOf);
  const resetToToday = useAsOfStore((s) => s.resetToToday);

  // SSR hydration 안전 — 첫 client mount 후에 store 의 persisted value 가
  // 반영. 그 사이 null 표시로 깜박임 방지.
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    // SSR + first hydration 의 default render — 빈 input 으로 layout shift X.
    return (
      <div
        className={cn(
          "inline-flex items-center gap-2 text-sm text-neutral-500",
          className,
        )}
      >
        <span>📅</span>
        <span className="font-mono">—</span>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 text-sm",
        className,
      )}
    >
      <label className="flex items-center gap-1.5">
        <span className="text-neutral-500" aria-hidden="true">
          📅
        </span>
        <input
          type="date"
          value={asOf}
          onChange={(e) => {
            const next = e.target.value;
            if (next) {
              setAsOf(next);
            }
          }}
          aria-label={t("asOfDatePicker.inputAriaLabel")}
          className={cn(
            "rounded border px-2 py-1 font-mono",
            // oracle ADR-0008 D4.1 — 오늘 vs 과거 시각 차이.
            isToday
              ? "border-neutral-300 text-neutral-600"
              : "border-amber-400 bg-amber-50 text-amber-900 font-medium",
          )}
        />
      </label>
      {!isToday ? (
        <button
          type="button"
          onClick={resetToToday}
          className="rounded border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-100 focus:outline-none focus:ring-2 focus:ring-neutral-500"
          aria-label={t("asOfDatePicker.resetAriaLabel")}
        >
          {t("asOfDatePicker.resetButtonLabel")}
        </button>
      ) : null}
    </div>
  );
}


/**
 * AsOfBanner — page 헤더 아래 표시. as-of 가 과거이면 strong notice.
 *
 * ADR-0008 D4.1: "*과거 시점 분석 — 2024-10-01 기준 데이터 표시 중. 그 시점
 * 이후의 데이터는 사용되지 않습니다.*"
 *
 * 본 component 는 store 직접 구독 — 페이지마다 mount 시점에 데이터 fetch
 * 와 함께 표시 invariant.
 */
export function AsOfBanner({
  className,
}: {
  readonly className?: string;
}): JSX.Element | null {
  const t = useTranslations("common");
  const asOf = useAsOfStore((s) => s.asOf);
  const isToday = useIsToday();

  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  // 오늘이거나 hydration 진행 중이면 미표시.
  if (!mounted || isToday) {
    return null;
  }

  return (
    <div
      role="status"
      className={cn(
        "border-b border-amber-200 bg-amber-50 px-6 py-2 text-xs text-amber-900",
        className,
      )}
    >
      {t("asOfBanner.pastAnalysis", { asOf })}
    </div>
  );
}
