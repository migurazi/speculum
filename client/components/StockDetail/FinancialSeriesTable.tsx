"use client";

/**
 * FinancialSeriesTable — 재무 시계열 표.
 *
 * GET /api/stocks/{code}/financials 응답을 periods × items 행열로 표시.
 *   - 행 = account (name + unit 헤더).
 *   - 열 = fiscal_period (오름차순).
 *   - 셀 = 값(str) — 결손은 "—".
 *
 * No Advice (8기둥 §2.2):
 *   - 판단색(빨강=나쁨 등) 0 — 중립 표.
 *   - 해석/전망/추천 텍스트 0.
 *   - 값은 왜곡 없이 그대로(천단위 구분자만 허용).
 *
 * 관련:
 *   - M1 T57 Phase 2 — AC-F-04.
 *   - ADR-0008 D8 — as_of PIT.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";

import { fetchStockFinancials } from "@/lib/api/financials";
import type { FinancialSeries } from "@/lib/api/financials";
import { cn } from "@/lib/utils";

interface FinancialSeriesTableProps {
  /** KRX 종목코드. */
  readonly code: string;
  /** PIT 기준 일자 ("YYYY-MM-DD"). */
  readonly asOf: string;
  readonly className?: string;
}

/**
 * 숫자 문자열에 천단위 구분자를 삽입한다.
 * 소수점 있는 값(EPS 등)은 정수부에만 적용.
 * 변환 불가(비숫자 문자 포함)이면 원문 그대로 반환.
 * 데이터 왜곡 없음 — 포맷만 변경, 값 자체 불변.
 */
function formatFinancialValue(raw: string): string {
  // 선두 마이너스 분리.
  const sign = raw.startsWith("-") ? "-" : "";
  const body = sign ? raw.slice(1) : raw;

  // 정수부와 소수부 분리.
  const dotIdx = body.indexOf(".");
  const intPart = dotIdx >= 0 ? body.slice(0, dotIdx) : body;
  const fracPart = dotIdx >= 0 ? body.slice(dotIdx) : "";

  // 순수 숫자인지 확인.
  if (!/^\d+$/.test(intPart)) return raw;

  // 천단위 구분자.
  const formatted = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}${formatted}${fracPart}`;
}

/**
 * FinancialSeriesTable — useQuery + 재무 시계열 표.
 */
export function FinancialSeriesTable({
  code,
  asOf,
  className,
}: FinancialSeriesTableProps): JSX.Element {
  const t = useTranslations("stock");

  const { data, isLoading, isError, error } = useQuery<FinancialSeries, Error>({
    queryKey: ["financials", code, asOf] as const,
    queryFn: ({ signal }) => fetchStockFinancials(code, asOf, signal),
    staleTime: 5 * 60 * 1000, // 5분
    enabled: code.length > 0,
  });

  // 로딩 상태.
  if (isLoading) {
    return (
      <div className={cn("rounded-lg border border-neutral-200 bg-white p-4", className)}>
        <div className="h-4 w-32 animate-pulse rounded bg-neutral-100" />
        <div className="mt-3 h-40 animate-pulse rounded bg-neutral-100" />
      </div>
    );
  }

  // 에러 상태.
  if (isError) {
    return (
      <div className={cn("rounded-lg border border-neutral-200 bg-white p-4", className)}>
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {t("financials.loadError", { message: error.message })}
        </div>
      </div>
    );
  }

  // 빈 상태 — 데이터는 있으나 items 0개.
  if (!data || data.items.length === 0) {
    return (
      <div className={cn("rounded-lg border border-neutral-200 bg-white p-4", className)}>
        <p className="text-sm text-neutral-500">{t("financials.empty")}</p>
      </div>
    );
  }

  const { periods, items } = data;

  return (
    <div className={cn("rounded-lg border border-neutral-200 bg-white", className)}>
      {/* 스크롤 가능 — period 수가 많으면 가로 스크롤. */}
      <div className="overflow-x-auto">
        <table className="w-full min-w-max border-collapse text-sm">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              {/* 항목명 열 헤더 */}
              <th
                scope="col"
                className="px-4 py-2.5 text-left font-medium text-neutral-600"
              >
                {t("financials.colAccount")}
              </th>
              {periods.map((period) => (
                <th
                  key={period}
                  scope="col"
                  className="px-4 py-2.5 text-right font-mono font-medium text-neutral-600"
                >
                  {period}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((item, rowIdx) => (
              <tr
                key={item.account}
                className={cn(
                  "border-b border-neutral-100 last:border-0",
                  rowIdx % 2 === 1 ? "bg-neutral-50/50" : "bg-white",
                )}
              >
                {/* 항목명 + unit */}
                <th
                  scope="row"
                  className="px-4 py-2 text-left font-normal"
                >
                  <span className="text-neutral-800">{item.name}</span>
                  {item.unit && item.unit !== "ratio" && (
                    <span className="ml-1.5 text-xs text-neutral-400">
                      ({item.unit})
                    </span>
                  )}
                </th>
                {/* 분기별 값 */}
                {item.values.map((val, colIdx) => (
                  <td
                    // eslint-disable-next-line react/no-array-index-key
                    key={colIdx}
                    className="px-4 py-2 text-right font-mono text-neutral-700 tabular-nums"
                  >
                    {val === null ? (
                      <span className="text-neutral-300">—</span>
                    ) : (
                      formatFinancialValue(val)
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
