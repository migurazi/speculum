"use client";

/**
 * RestatementHistory — 정정공시 vintage chain 표.
 *
 * §2.7 Observation: "회사가 이 계정의 수치를 정정했다"는 관측된 사실만 표시.
 * §2.2 No Advice: 등락색·증감 라벨·가치 판단 0. 모든 값 변화는 중립 표시.
 * §2.4 PIT: as_of 기준 공개된 vintage 만 포함(backend 보장).
 * §2.1 Fidelity: 원본(vintage_seq=1)·정정본 모두 추적 가능하게 노출.
 *
 * 표 구조:
 *   - account 별 그룹 — account 이름을 섹션 헤더로, vintage_seq 오름차순 행.
 *   - 각 행: 공시일(effectiveDate), 회차(vintageSeq), 값, 현행/과거 배지.
 *   - 정정 없는 account (vintage 1개) → "정정 이력 없음" 단일 행.
 *   - 정정 있는 account (vintage 2+) → 섹션 헤더에 중립 "정정 N회" 마커.
 *
 * 중립 톤 원칙:
 *   - 현행(isActive=true) 배지 → neutral solid (text-neutral-900 bg-neutral-100).
 *   - 과거(isActive=false) 배지 → muted (text-neutral-400 bg-neutral-50 선 취소선).
 *   - 값 셀 — 폰트색 중립(text-neutral-700). 등락 판단색(red/blue/green) 0.
 *   - 정정 마커 — text-neutral-500. "정정" 단어 그대로, "상향/하향/개선/악화" 0.
 *
 * 관련:
 *   - M2 T82 Phase 2 — AC-M2-F-07.
 *   - FinancialSeriesTable(T57) 스타일 선례.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";

import { fetchFinancialHistory } from "@/lib/api/financial-history";
import type { FinancialVintage } from "@/lib/api/financial-history";
import { cn } from "@/lib/utils";

interface RestatementHistoryProps {
  /** KRX 종목코드. */
  readonly code: string;
  /** PIT 기준 일자 ("YYYY-MM-DD"). undefined 시 최신 기준. */
  readonly asOf?: string;
  readonly className?: string;
}

/**
 * 숫자 문자열에 천단위 구분자를 삽입한다 — FinancialSeriesTable 과 동일 로직.
 * 소수점 있는 값은 정수부에만 적용. 변환 불가이면 원문 반환.
 * 데이터 왜곡 없음 — 포맷만 변경, 값 자체 불변.
 */
function formatValue(raw: string): string {
  const sign = raw.startsWith("-") ? "-" : "";
  const body = sign ? raw.slice(1) : raw;
  const dotIdx = body.indexOf(".");
  const intPart = dotIdx >= 0 ? body.slice(0, dotIdx) : body;
  const fracPart = dotIdx >= 0 ? body.slice(dotIdx) : "";
  if (!/^\d+$/.test(intPart)) return raw;
  const formatted = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}${formatted}${fracPart}`;
}

/**
 * account 별로 vintage 그룹핑 — account 키 순서 유지, vintage_seq 오름차순.
 * Map(account → vintages[]) 반환. 삽입 순서 = 첫 등장 순서.
 */
function groupByAccount(
  vintages: ReadonlyArray<FinancialVintage>,
): Map<string, FinancialVintage[]> {
  const map = new Map<string, FinancialVintage[]>();
  for (const v of vintages) {
    const group = map.get(v.account);
    if (group) {
      group.push(v);
    } else {
      map.set(v.account, [v]);
    }
  }
  // vintage_seq 오름차순 정렬 — backend 순서에 의존하지 않음.
  for (const group of map.values()) {
    group.sort((a, b) => a.vintageSeq - b.vintageSeq);
  }
  return map;
}

/**
 * 현행/과거 배지 — 중립 톤. 등락색(red/blue/green) 0.
 * 현행: neutral solid. 과거: muted + 취소선 힌트.
 */
function StatusBadge({
  isActive,
  t,
}: {
  isActive: boolean;
  t: ReturnType<typeof useTranslations<"stock">>;
}): JSX.Element {
  if (isActive) {
    return (
      <span className="inline-flex items-center rounded border border-neutral-300 bg-neutral-100 px-1.5 py-0.5 text-xs font-medium text-neutral-900">
        {t("restatementHistory.statusActive")}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded border border-neutral-200 bg-neutral-50 px-1.5 py-0.5 text-xs font-medium text-neutral-400">
      {t("restatementHistory.statusSuperseded")}
    </span>
  );
}

/**
 * RestatementHistory — useQuery + account × vintage 정정 이력 표.
 */
export function RestatementHistory({
  code,
  asOf,
  className,
}: RestatementHistoryProps): JSX.Element {
  const t = useTranslations("stock");

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["financialHistory", code, asOf ?? null] as const,
    queryFn: ({ signal }) =>
      fetchFinancialHistory(code, asOf ? { asOf } : {}, signal),
    staleTime: 5 * 60 * 1000, // 5분
    enabled: code.length > 0,
  });

  // 로딩 상태.
  if (isLoading) {
    return (
      <div
        className={cn(
          "rounded-lg border border-neutral-200 bg-white p-4",
          className,
        )}
      >
        <div className="h-4 w-40 animate-pulse rounded bg-neutral-100" />
        <div className="mt-3 h-32 animate-pulse rounded bg-neutral-100" />
      </div>
    );
  }

  // 에러 상태.
  if (isError) {
    return (
      <div
        className={cn(
          "rounded-lg border border-neutral-200 bg-white p-4",
          className,
        )}
      >
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {t("restatementHistory.loadError", {
            message: (error as Error).message,
          })}
        </div>
      </div>
    );
  }

  // 빈 상태 — vintages 없음.
  if (!data || data.vintages.length === 0) {
    return (
      <div
        className={cn(
          "rounded-lg border border-neutral-200 bg-white p-4",
          className,
        )}
      >
        <p className="text-sm text-neutral-500">
          {t("restatementHistory.empty")}
        </p>
      </div>
    );
  }

  const groups = groupByAccount(data.vintages);

  return (
    <div
      className={cn("rounded-lg border border-neutral-200 bg-white", className)}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-max border-collapse text-sm">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              {/* account 열 — 항목명 */}
              <th
                scope="col"
                className="px-4 py-2.5 text-left font-medium text-neutral-600"
              >
                {t("financials.colAccount")}
              </th>
              {/* 공시일 */}
              <th
                scope="col"
                className="px-4 py-2.5 text-left font-medium text-neutral-600"
              >
                {t("restatementHistory.colEffectiveDate")}
              </th>
              {/* 회차 */}
              <th
                scope="col"
                className="px-4 py-2.5 text-right font-medium text-neutral-600"
              >
                {t("restatementHistory.colVintageSeq")}
              </th>
              {/* 값 */}
              <th
                scope="col"
                className="px-4 py-2.5 text-right font-medium text-neutral-600"
              >
                {t("restatementHistory.colValue")}
              </th>
              {/* 상태 */}
              <th
                scope="col"
                className="px-4 py-2.5 text-left font-medium text-neutral-600"
              >
                {t("restatementHistory.colStatus")}
              </th>
            </tr>
          </thead>
          <tbody>
            {[...groups.entries()].map(([account, vintages]) => {
              // 정정 여부: vintage 가 2개 이상이면 정정 이력 존재.
              const hasRestatement = vintages.length >= 2;
              // 정정 횟수 = vintages - 1 (원본 제외 정정 회차 수).
              const restatementCount = vintages.length - 1;

              return vintages.map((vintage, vinIdx) => (
                <tr
                  key={`${account}-${vintage.vintageSeq}`}
                  className={cn(
                    "border-b border-neutral-100 last:border-0",
                    // 현행 행 — 살짝 밝은 배경으로 구별(중립).
                    vintage.isActive ? "bg-white" : "bg-neutral-50/40",
                  )}
                >
                  {/* account 셀 — 그룹 내 첫 행에만 rowspan 으로 표시 */}
                  {vinIdx === 0 && (
                    <th
                      scope="rowgroup"
                      rowSpan={vintages.length}
                      className="px-4 py-2 text-left align-top font-normal"
                    >
                      <span className="text-neutral-800">{account}</span>
                      {/* 단위 표시 */}
                      {vintage.unit && vintage.unit !== "ratio" && (
                        <span className="ml-1.5 text-xs text-neutral-400">
                          ({vintage.unit})
                        </span>
                      )}
                      {/*
                       * 정정 마커 — §2.7 관측 사실. "정정 N회" 중립 텍스트.
                       * "상향/하향/개선/악화" 금지. 색 판단 0(neutral-500).
                       */}
                      {hasRestatement && (
                        <span className="ml-2 inline-flex items-center rounded bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-500">
                          {t("restatementHistory.restatementCount", {
                            count: restatementCount,
                          })}
                        </span>
                      )}
                    </th>
                  )}

                  {/* 공시일 */}
                  <td className="px-4 py-2 font-mono text-neutral-700">
                    {vintage.effectiveDate}
                  </td>

                  {/* 회차 */}
                  <td className="px-4 py-2 text-right font-mono tabular-nums text-neutral-700">
                    {vintage.vintageSeq}
                  </td>

                  {/*
                   * 값 — §2.2 No Advice: 값 변화에 색 판단 0.
                   * 과거 vintage 는 취소선으로 "대체됨" 사실만 표시(중립).
                   * 색은 중립 text-neutral-700 고정 — 등락 판단색 0.
                   */}
                  <td
                    className={cn(
                      "px-4 py-2 text-right font-mono tabular-nums text-neutral-700",
                      !vintage.isActive && "line-through decoration-neutral-400",
                    )}
                  >
                    {formatValue(vintage.value)}
                  </td>

                  {/* 현행/과거 배지 — 중립 톤. */}
                  <td className="px-4 py-2">
                    <StatusBadge isActive={vintage.isActive} t={t} />
                  </td>
                </tr>
              ));
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
