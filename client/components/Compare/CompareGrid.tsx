/**
 * CompareGrid — 2~6 종목 비교 그리드.
 *
 * 표 구조:
 *   row 0       header                | 종목 A    | 종목 B    | ...
 *   row 1       종목명·코드·상태       | ...       | ...
 *   row 2~N     factor 1..N            | value     | value
 *
 * 각 factor 값 cell 은 MetricCard 와 동일한 SourceAttribution wrap — 8 기둥
 * §2.1 Fidelity 의무.
 *
 * factor row 결정 — 모든 종목의 factor union (canonical_id 기준). 첫 종목의
 * 순서 우선, 이후 등장 순. M0 의 빌트인 factor pack 이 종목 무관 동일 set 이라
 * 실질적으로는 동일 순서.
 *
 * 관련:
 * - ADR-0007 D2 (Source attribution 의무) — 본 grid 의 모든 값 cell.
 * - ADR-0008 D3 (as_of 영향 화면).
 * - M0_PLAN T38 / AC-F-05.
 *
 * compare/page.tsx ("use client") 가 직접 import 하므로 클라이언트 컴포넌트로
 * 동작한다 — useTranslations 사용.
 */

"use client";

import { useTranslations } from "next-intl";

import {
  SourceAttribution,
  type SourceAttributionProps,
} from "@/components/SourceAttribution";
import type { FactorValue, StockDetail } from "@/lib/api/stocks";
import { formatPercentValue } from "@/lib/factor/format";
import { inferFactorSource } from "@/lib/factor/source";

/** status → i18n 키 매핑 — compare 네임스페이스의 statusActive 등. */
const STATUS_LABEL_KEY: Readonly<Record<string, string>> = {
  active: "statusActive",
  not_yet_listed: "statusNotYetListed",
  delisted: "statusDelisted",
};

const STATUS_BADGE_CLASS: Readonly<Record<string, string>> = {
  active: "border-emerald-300 bg-emerald-50 text-emerald-800",
  not_yet_listed: "border-neutral-300 bg-neutral-50 text-neutral-700",
  delisted: "border-amber-300 bg-amber-50 text-amber-900",
};

/**
 * 모든 종목의 factor union — canonical_id 사전식 정렬 (oracle T38 M1).
 *
 * 첫 등장 순서가 stocks[] 입력 순서에 의존하면 사용자가 "005930,000660" vs
 * "000660,005930" 두 번 호출 시 row 순서가 달라짐. backend pack 의 explicit
 * order 가 wire 에 추가될 때까지 결정적 정렬로 보장.
 */
function collectFactorOrder(
  stocks: ReadonlyArray<StockDetail>,
): ReadonlyArray<{ canonical_id: string; name: string; unit: string }> {
  const seen = new Map<string, { canonical_id: string; name: string; unit: string }>();
  for (const stock of stocks) {
    for (const factor of stock.factors) {
      if (!seen.has(factor.canonical_id)) {
        seen.set(factor.canonical_id, {
          canonical_id: factor.canonical_id,
          name: factor.name,
          unit: factor.unit,
        });
      }
    }
  }
  return Array.from(seen.values()).sort((a, b) =>
    a.canonical_id.localeCompare(b.canonical_id),
  );
}

/** 종목별 factor lookup — Compare grid cell 결정에 사용. */
function buildFactorLookup(
  stock: StockDetail,
): ReadonlyMap<string, FactorValue> {
  const map = new Map<string, FactorValue>();
  for (const factor of stock.factors) {
    map.set(factor.canonical_id, factor);
  }
  return map;
}

interface CompareGridProps {
  readonly stocks: ReadonlyArray<StockDetail>;
  readonly asOf: string;
}

export function CompareGrid({
  stocks,
  asOf,
}: CompareGridProps): JSX.Element {
  const t = useTranslations("compare");

  if (stocks.length === 0) {
    return (
      <p className="text-sm text-neutral-500">{t("gridEmpty")}</p>
    );
  }

  const factorOrder = collectFactorOrder(stocks);
  const lookups = stocks.map((s) => buildFactorLookup(s));

  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-neutral-50 text-xs text-neutral-600">
          <tr>
            <th
              scope="col"
              className="sticky left-0 z-10 border-b border-r border-neutral-200 bg-neutral-50 px-4 py-2 text-left font-medium"
            >
              {t("gridMetricHeader")}
            </th>
            {stocks.map((stock) => (
              <th
                key={stock.id}
                scope="col"
                className="border-b border-neutral-200 px-4 py-2 text-left font-medium"
              >
                <div className="flex flex-col gap-1">
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm font-semibold text-neutral-900">
                      {stock.name}
                    </span>
                    <span className="font-mono text-xs text-neutral-600">
                      {stock.code}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-1">
                    <span
                      className={`rounded-md border px-1.5 py-0.5 text-[10px] ${
                        STATUS_BADGE_CLASS[stock.status]
                        ?? "border-neutral-200 bg-neutral-50"
                      }`}
                    >
                      {STATUS_LABEL_KEY[stock.status] !== undefined
                        ? t(STATUS_LABEL_KEY[stock.status] as Parameters<typeof t>[0])
                        : stock.status}
                    </span>
                    <span className="text-[10px] text-neutral-500">
                      {stock.market}
                    </span>
                  </div>
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {factorOrder.map((factor) => (
            <tr key={factor.canonical_id} className="border-b border-neutral-100">
              <th
                scope="row"
                className="sticky left-0 z-10 border-r border-neutral-200 bg-white px-4 py-2 text-left text-xs font-medium text-neutral-700"
              >
                <div>{factor.name}</div>
                <div className="font-mono text-[10px] text-neutral-400">
                  {factor.canonical_id}
                </div>
              </th>
              {stocks.map((stock, idx) => {
                const cell = lookups[idx]!.get(factor.canonical_id);
                return (
                  <td
                    key={stock.id}
                    className="px-4 py-2 align-top"
                  >
                    <CompareCell
                      factor={cell ?? null}
                      asOf={asOf}
                      canonicalIdFallback={factor.canonical_id}
                    />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface CompareCellProps {
  readonly factor: FactorValue | null;
  readonly asOf: string;
  /** factor 가 null 일 때도 row 의 canonical_id 를 출력 (디버깅 신호). */
  readonly canonicalIdFallback: string;
}

/**
 * Compare grid 의 단일 cell — MetricCard 의 inline 버전.
 *
 * MetricCard 는 카드 형태 (큰 값 + label), CompareCell 은 표 cell (값만 + 작은
 * source/asOf). 두 component 의 source/N-A 분기 로직은 동일하나 UI 형태가
 * 달라 별도 component 유지.
 */
function CompareCell({
  factor,
  asOf,
  canonicalIdFallback,
}: CompareCellProps): JSX.Element {
  if (factor === null) {
    // factor union 에 포함되었으나 본 종목에는 값 없음 — backend invariant 위반
    // 이지만 안전하게 표시. (M0 에서 factor pack 이 종목 무관 동일 set 이라
    // 실제로는 발생 X.)
    return (
      <span
        className="text-xs text-neutral-400"
        title={canonicalIdFallback}
      >
        —
      </span>
    );
  }

  if (factor.is_na || factor.value === null) {
    return (
      <div>
        <span className="text-sm text-neutral-400">N/A</span>
        {factor.na_reason !== null ? (
          <div className="mt-0.5 text-[10px] text-neutral-500">
            {factor.na_reason}
          </div>
        ) : null}
      </div>
    );
  }

  // 상위 (is_na || value === null) 가드 이후에도 계약 위반 데이터에서
  // value 가 null 로 올 수 있으므로 null 가드 후 빈 문자열 fallback 제공.
  const rawValue = factor.value ?? "";
  const displayValue =
    factor.unit === "percent"
      ? formatPercentValue(rawValue)
      : rawValue;

  return (
    <SourceAttribution
      value={
        <span>
          {displayValue}
          {factor.unit !== "ratio" &&
          factor.unit !== "percent" &&
          factor.unit !== "" ? (
            <span className="ml-1 text-[10px] text-neutral-500">
              {factor.unit}
            </span>
          ) : null}
        </span>
      }
      source={inferFactorSource(factor.canonical_id)}
      formula={factor.canonical_id}
      asOf={asOf}
    />
  );
}

export type { SourceAttributionProps };
