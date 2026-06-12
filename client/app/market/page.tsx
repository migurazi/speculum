"use client";

/**
 * Market Overview page — `/market`.
 *
 * 8기둥 §2.3 Active Inspection — 사용자가 /market 으로 이동(의도 표시)했을 때만
 * fetch·렌더. 홈 페이지에서는 링크만 제공.
 *
 * 렌더 구성:
 *   - 헤더: "시장 통계" + as_of 기준 일자.
 *   - 유니버스 요약: 전체 종목 수 + 시장별 분포 배지.
 *   - 지표 분포 표: factor 별 집계 통계 (사실 표시만 — 해석·판단 없음).
 *
 * No Advice 원칙:
 *   - 매수/매도/추천/유망/저평가/고평가 등 해석·판단 텍스트 없음.
 *   - 판단 암시 색상(빨강=나쁨/초록=좋음) 없음 — neutral 표 스타일만.
 *   - 랭킹·"상위 N"·종목 하이라이트 없음.
 *
 * 관련:
 * - ADR-0010 D1 (홈 화면 Active Inspection).
 * - M1_PLAN T61 Market Overview.
 */

import { useTranslations } from "next-intl";
import { useQuery } from "@tanstack/react-query";

import {
  fetchMarketOverview,
  type FactorAggregate,
  type MacroIndicator,
} from "@/lib/api/market";
import { formatStatValue } from "@/lib/factor/format";
import { useAsOfStore } from "@/state/as-of-store";

/** Market Overview 쿼리 stale time — 5분. */
const MARKET_OVERVIEW_STALE_TIME = 5 * 60 * 1000;

/** 지표 분포 표의 단일 행. */
function FactorRow({ factor }: { factor: FactorAggregate }): JSX.Element {
  const hasData = factor.count > 0;
  // percent factor(dividend-yield·price-return) 는 backend ratio → ×100 표시.
  // unit 이 "percent" 이면 formatStatValue 가 ×100 변환. 이중 변환 금지.
  const isPercent = factor.unit === "percent";

  // percent factor 의 unit 표시는 "%" 가 값 안에 포함되므로 별도 suffix 미표시.
  const displayUnit = isPercent ? "" : factor.unit;

  function fmt(val: string | null): string {
    if (!hasData) return "—";
    return formatStatValue(val, isPercent);
  }

  return (
    <tr className="border-t border-neutral-100">
      <td className="py-2.5 pr-4 text-sm text-neutral-800">
        <span>{factor.name}</span>
        {displayUnit ? (
          <span className="ml-1 text-xs text-neutral-400">{displayUnit}</span>
        ) : null}
      </td>
      <td className="py-2.5 pr-4 text-sm tabular-nums text-neutral-700">
        <span>{factor.count}</span>
        {factor.naCount > 0 ? (
          <span className="ml-1.5 text-xs text-neutral-400">
            N/A {factor.naCount}
          </span>
        ) : null}
      </td>
      <td className="py-2.5 pr-4 text-sm tabular-nums text-neutral-700">
        {fmt(factor.mean)}
      </td>
      <td className="py-2.5 pr-4 text-sm tabular-nums text-neutral-700">
        {fmt(factor.median)}
      </td>
      <td className="py-2.5 pr-4 text-sm tabular-nums text-neutral-700">
        {fmt(factor.p25)}
      </td>
      <td className="py-2.5 pr-4 text-sm tabular-nums text-neutral-700">
        {fmt(factor.p75)}
      </td>
      <td className="py-2.5 pr-4 text-sm tabular-nums text-neutral-700">
        {fmt(factor.min)}
      </td>
      <td className="py-2.5 text-sm tabular-nums text-neutral-700">
        {fmt(factor.max)}
      </td>
    </tr>
  );
}

/** 로딩 중 스켈레톤 행. */
function SkeletonRow(): JSX.Element {
  return (
    <tr className="border-t border-neutral-100">
      {Array.from({ length: 8 }).map((_, i) => (
        <td key={i} className="py-2.5 pr-4">
          <div className="h-4 animate-pulse rounded bg-neutral-100" />
        </td>
      ))}
    </tr>
  );
}

/** 매크로 지표 표의 단일 행 — 중립 톤, 판단색 없음. */
function MacroRow({ indicator }: { indicator: MacroIndicator }): JSX.Element {
  return (
    <tr className="border-t border-neutral-100">
      <td className="py-2.5 pr-4 text-sm text-neutral-800">{indicator.name}</td>
      <td className="py-2.5 pr-4 text-sm tabular-nums text-neutral-700">
        {indicator.value}
        {indicator.unit ? (
          <span className="ml-1 text-xs text-neutral-400">{indicator.unit}</span>
        ) : null}
      </td>
      <td className="py-2.5 pr-4 text-sm tabular-nums text-neutral-600 font-mono">
        {indicator.referenceDate}
      </td>
      <td className="py-2.5 text-sm tabular-nums text-neutral-500 font-mono">
        {indicator.vintageDate}
      </td>
    </tr>
  );
}

/** 로딩 중 매크로 스켈레톤 행. */
function MacroSkeletonRow(): JSX.Element {
  return (
    <tr className="border-t border-neutral-100">
      {Array.from({ length: 4 }).map((_, i) => (
        <td key={i} className="py-2.5 pr-4">
          <div className="h-4 animate-pulse rounded bg-neutral-100" />
        </td>
      ))}
    </tr>
  );
}

export default function MarketPage(): JSX.Element {
  const t = useTranslations("market");
  const asOf = useAsOfStore((s) => s.asOf);

  const query = useQuery({
    queryKey: ["market-overview", asOf],
    queryFn: ({ signal }) => fetchMarketOverview(asOf, signal),
    staleTime: MARKET_OVERVIEW_STALE_TIME,
  });

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      {/* 헤더 */}
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-neutral-900">{t("pageTitle")}</h1>
        <p className="mt-1 text-sm text-neutral-500">
          {t("asOfLabel")}{" "}
          <span className="font-mono text-neutral-700">{asOf}</span>
        </p>
      </div>

      {/* 에러 상태 */}
      {query.isError ? (
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900">
          {t("errorLoad", { message: (query.error as Error).message })}
        </div>
      ) : null}

      {/* 유니버스 요약 */}
      <section className="mb-6 rounded-lg border border-neutral-200 p-4">
        <h2 className="mb-3 text-sm font-medium text-neutral-700">
          {t("universeHeading")}
        </h2>

        {query.isLoading ? (
          <div className="flex flex-wrap gap-2">
            <div className="h-8 w-24 animate-pulse rounded-md bg-neutral-100" />
            <div className="h-8 w-20 animate-pulse rounded-md bg-neutral-100" />
            <div className="h-8 w-20 animate-pulse rounded-md bg-neutral-100" />
          </div>
        ) : query.data ? (
          <>
            {query.data.universeCount === 0 ? (
              <p className="text-sm text-neutral-500">
                {t("universeEmpty")}
              </p>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md border border-neutral-300 bg-neutral-50 px-3 py-1 text-sm font-medium text-neutral-800">
                  {t("universeTotalBadge", { count: query.data.universeCount })}
                </span>
                {query.data.marketBreakdown.map((m) => (
                  <span
                    key={m.market}
                    className="rounded-md border border-neutral-200 bg-white px-3 py-1 text-sm text-neutral-700"
                  >
                    {m.market} {m.count}
                  </span>
                ))}
              </div>
            )}
          </>
        ) : null}
      </section>

      {/* 지표 분포 표 */}
      <section className="mb-6">
        <h2 className="mb-3 text-sm font-medium text-neutral-700">
          {t("factorHeading")}
        </h2>

        <div className="overflow-x-auto rounded-lg border border-neutral-200">
          <table className="w-full min-w-[700px] border-collapse">
            <thead>
              <tr className="bg-neutral-50">
                <th className="py-2.5 pr-4 text-left text-xs font-medium text-neutral-500">
                  {t("col.factorName")}
                </th>
                <th className="py-2.5 pr-4 text-left text-xs font-medium text-neutral-500">
                  {t("col.stockCount")}
                </th>
                <th className="py-2.5 pr-4 text-left text-xs font-medium text-neutral-500">
                  {t("col.mean")}
                </th>
                <th className="py-2.5 pr-4 text-left text-xs font-medium text-neutral-500">
                  {t("col.median")}
                </th>
                <th className="py-2.5 pr-4 text-left text-xs font-medium text-neutral-500">
                  {t("col.p25")}
                </th>
                <th className="py-2.5 pr-4 text-left text-xs font-medium text-neutral-500">
                  {t("col.p75")}
                </th>
                <th className="py-2.5 pr-4 text-left text-xs font-medium text-neutral-500">
                  {t("col.min")}
                </th>
                <th className="py-2.5 text-left text-xs font-medium text-neutral-500">
                  {t("col.max")}
                </th>
              </tr>
            </thead>
            <tbody>
              {query.isLoading ? (
                <>
                  <SkeletonRow />
                  <SkeletonRow />
                  <SkeletonRow />
                  <SkeletonRow />
                  <SkeletonRow />
                </>
              ) : query.data && query.data.factors.length > 0 ? (
                query.data.factors.map((f) => (
                  <FactorRow key={f.canonicalId} factor={f} />
                ))
              ) : query.data && query.data.factors.length === 0 ? (
                <tr>
                  <td
                    colSpan={8}
                    className="py-6 text-center text-sm text-neutral-500"
                  >
                    {t("factorEmpty")}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      {/* 매크로 지표 — 값·기준일·관측시점·단위만 표시. 해석·판단 없음. */}
      {query.isLoading || query.data ? (
        <section>
          <h2 className="mb-3 text-sm font-medium text-neutral-700">
            {t("macroHeading")}
          </h2>

          <div className="overflow-x-auto rounded-lg border border-neutral-200">
            <table className="w-full min-w-[500px] border-collapse">
              <thead>
                <tr className="bg-neutral-50">
                  <th className="py-2.5 pr-4 text-left text-xs font-medium text-neutral-500">
                    {t("macroCol.name")}
                  </th>
                  <th className="py-2.5 pr-4 text-left text-xs font-medium text-neutral-500">
                    {t("macroCol.value")}
                  </th>
                  <th className="py-2.5 pr-4 text-left text-xs font-medium text-neutral-500">
                    {t("macroCol.referenceDate")}
                  </th>
                  <th className="py-2.5 text-left text-xs font-medium text-neutral-500">
                    {t("macroCol.vintageDate")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {query.isLoading ? (
                  <>
                    <MacroSkeletonRow />
                    <MacroSkeletonRow />
                    <MacroSkeletonRow />
                  </>
                ) : query.data && query.data.macroIndicators.length > 0 ? (
                  query.data.macroIndicators.map((mi) => (
                    <MacroRow key={mi.indicatorId} indicator={mi} />
                  ))
                ) : (
                  <tr>
                    <td
                      colSpan={4}
                      className="py-6 text-center text-sm text-neutral-500"
                    >
                      {t("macroEmpty")}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* 관측근사 disclaimer — vintage_date 가 공표일이 아님을 명시(사실 고지). */}
          {query.data && query.data.macroIndicators.length > 0 ? (
            <p className="mt-2 text-xs text-neutral-400">
              {t("macroDisclaimer")}
            </p>
          ) : null}
        </section>
      ) : null}
    </main>
  );
}
