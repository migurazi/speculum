"use client";

/**
 * Stock Detail page — `/stock/[code]`.
 *
 * ADR-0007 D2 + M0_PLAN T37 / AC-F-04. 종목 메타정보 (이름·시장·상장일·
 * status) + 지표 카드 grid + 종목코드 변경 history.
 *
 * 가격 차트 + 재무 시계열 표 는 별도 cycle backlog (lightweight-charts 도입
 * 필요).
 *
 * 관련:
 * - ADR-0008 D3 — Stock Detail 이 as_of 기준 데이터 표시.
 * - ADR-0009 D6 — code_history lineage.
 * - M0_PLAN T37 / AC-F-04.
 */

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";

import { CodeHistory } from "@/components/StockDetail/CodeHistory";
import { MetricCard } from "@/components/StockDetail/MetricCard";
import { fetchStockDetail } from "@/lib/api/stocks";
import { useAsOfStore } from "@/state/as-of-store";

const STATUS_LABEL_KO: Readonly<Record<string, string>> = {
  active: "거래 중",
  not_yet_listed: "미상장",
  delisted: "상장폐지",
};

const STATUS_BADGE_CLASS: Readonly<Record<string, string>> = {
  active: "border-emerald-300 bg-emerald-50 text-emerald-800",
  not_yet_listed: "border-neutral-300 bg-neutral-50 text-neutral-700",
  delisted: "border-amber-300 bg-amber-50 text-amber-900",
};

export default function StockDetailPage(): JSX.Element {
  const params = useParams<{ code: string }>();
  const code = params?.code ?? "";
  const asOf = useAsOfStore((s) => s.asOf);

  const query = useQuery({
    queryKey: ["stock", code, asOf],
    queryFn: ({ signal }) => fetchStockDetail(code, asOf, signal),
    enabled: code.length > 0 && code.length <= 6 && /^\d+$/.test(code),
  });

  if (!code) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-8">
        <p className="text-sm text-neutral-500">종목코드가 필요합니다.</p>
      </main>
    );
  }

  if (query.isLoading) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-8">
        <p className="text-sm text-neutral-500">불러오는 중...</p>
      </main>
    );
  }

  if (query.isError) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-8">
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900">
          데이터 로드 실패: {(query.error as Error).message}
        </div>
      </main>
    );
  }

  const detail = query.data;
  if (!detail) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-8">
        <p className="text-sm text-neutral-500">데이터 없음.</p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <header className="space-y-2">
        <div className="flex flex-wrap items-baseline gap-3">
          <h1 className="text-2xl font-semibold text-neutral-900">
            {detail.name}
          </h1>
          <span className="font-mono text-base text-neutral-600">
            {detail.code}
          </span>
          <span
            className={`rounded-md border px-2 py-0.5 text-xs ${
              STATUS_BADGE_CLASS[detail.status] ?? "border-neutral-200 bg-neutral-50"
            }`}
          >
            {STATUS_LABEL_KO[detail.status] ?? detail.status}
          </span>
          <span className="text-xs text-neutral-500">{detail.market}</span>
        </div>
        <div className="text-xs text-neutral-600">
          상장일: <span className="font-mono">{detail.listing_date}</span>
          {detail.delisting_date !== null ? (
            <>
              {" · "}
              폐지일:{" "}
              <span className="font-mono">{detail.delisting_date}</span>
            </>
          ) : null}
          {" · "}
          결산월: {detail.fiscal_month}월
          {" · "}
          IFRS: {detail.ifrs_preference}
        </div>
      </header>

      {/* 지표 카드 grid — ADR-0007 D2 의 source attribution 의무 강제.
          각 MetricCard 가 SourceAttribution wrap. */}
      <section className="mt-6">
        <h2 className="text-sm font-medium text-neutral-700">지표</h2>
        {detail.factors.length === 0 ? (
          <p className="mt-2 text-sm text-neutral-500">
            표시할 지표가 없습니다.
          </p>
        ) : (
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {detail.factors.map((factor) => (
              <MetricCard
                key={factor.canonical_id}
                factor={factor}
                asOf={asOf}
              />
            ))}
          </div>
        )}
      </section>

      {/* lineage 의 종목코드 변경 history (2+ entry 인 경우만 렌더). */}
      <section className="mt-6">
        <CodeHistory history={detail.code_history} />
      </section>
    </main>
  );
}
