"use client";

/**
 * Stock Detail page — `/stock/[code]`.
 *
 * ADR-0007 D2 + M0_PLAN T37 / AC-F-04. 종목 메타정보 (이름·시장·상장일·
 * status) + 가격 차트(raw/adjusted 토글 + CA 마커) + 재무 시계열 표 +
 * 지표 카드 grid + 종목코드 변경 history.
 *
 * 관련:
 * - ADR-0008 D3 — Stock Detail 이 as_of 기준 데이터 표시.
 * - ADR-0009 D6 — code_history lineage.
 * - M0_PLAN T37 / AC-F-04.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useParams } from "next/navigation";

import { CodeHistory } from "@/components/StockDetail/CodeHistory";
import { DisclosureWithFactsPanel } from "@/components/StockDetail/DisclosureWithFactsPanel";
import { FinancialSeriesTable } from "@/components/StockDetail/FinancialSeriesTable";
import { MetricCard } from "@/components/StockDetail/MetricCard";
import { NotesPanel } from "@/components/StockDetail/NotesPanel";
import { PreTaxDisclosure } from "@/components/StockDetail/PreTaxDisclosure";
import { PriceChart } from "@/components/StockDetail/PriceChart";
import { RestatementHistory } from "@/components/StockDetail/RestatementHistory";
import { fetchStockDetail } from "@/lib/api/stocks";
import type { StockDetail } from "@/lib/api/stocks";
import { useAsOfStore } from "@/state/as-of-store";

// status → i18n 키 매핑. 미등록 status 는 키 없음 → fallback 으로 raw status
// 문자열을 그대로 표시(아래 렌더부에서 처리). 하드코딩 레이블 제거(i18n 우회 차단).
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

export default function StockDetailPage(): JSX.Element {
  const t = useTranslations("stock");
  const params = useParams<{ code: string }>();
  const code = params?.code ?? "";
  const asOf = useAsOfStore((s) => s.asOf);

  const query = useQuery<StockDetail, Error>({
    queryKey: ["stock", code, asOf],
    queryFn: ({ signal }) => fetchStockDetail(code, asOf, signal),
    enabled: code.length > 0 && code.length <= 6 && /^\d+$/.test(code),
  });

  if (!code) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-8">
        <p className="text-sm text-neutral-500">{t("noCode")}</p>
      </main>
    );
  }

  if (query.isLoading) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-8">
        <p className="text-sm text-neutral-500">{t("loading")}</p>
      </main>
    );
  }

  if (query.isError) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-8">
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900">
          {t("loadError", { message: query.error.message })}
        </div>
      </main>
    );
  }

  const detail = query.data;
  if (!detail) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-8">
        <p className="text-sm text-neutral-500">{t("noData")}</p>
      </main>
    );
  }

  // status → i18n 키. 미등록 status 는 undefined → 아래 렌더에서 raw status 표시.
  const statusLabelKey = STATUS_LABEL_KEY[detail.status];

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
            {statusLabelKey !== undefined ? t(statusLabelKey) : detail.status}
          </span>
          <span className="text-xs text-neutral-500">{detail.market}</span>
        </div>
        <div className="text-xs text-neutral-600">
          {t("listingDate")}: <span className="font-mono">{detail.listing_date}</span>
          {detail.delisting_date !== null ? (
            <>
              {" · "}
              {t("delistingDate")}:{" "}
              <span className="font-mono">{detail.delisting_date}</span>
            </>
          ) : null}
          {" · "}
          {t("fiscalMonth")}: {detail.fiscal_month}{t("fiscalMonthUnit")}
          {" · "}
          {t("ifrs")}: {detail.ifrs_preference}
        </div>
      </header>

      {/* 가격 차트 — lightweight-charts 캔들스틱/라인. PIT = asOf.
          raw 모드: CA ▾ 마커 표시 (ADR-0001 D6). adjusted 모드: 마커 없음. */}
      <section className="mt-6">
        <PriceChart code={code} asOf={asOf} />
      </section>

      {/* 재무 시계열 표 — periods × items. 결손 "—". 중립 표(판단색 0). */}
      <section className="mt-6">
        <h2 className="mb-3 text-sm font-medium text-neutral-700">
          {t("financials.heading")}
        </h2>
        <FinancialSeriesTable code={code} asOf={asOf} />
      </section>

      {/* 지표 카드 grid — ADR-0007 D2 의 source attribution 의무 강제.
          각 MetricCard 가 SourceAttribution wrap.
          세전 factor(price-return·dividend-yield) 표시 시 ADR-0035 D7 고지. */}
      <section className="mt-6">
        <h2 className="text-sm font-medium text-neutral-700">{t("metricsHeading")}</h2>
        {detail.factors.length === 0 ? (
          <p className="mt-2 text-sm text-neutral-500">
            {t("noMetrics")}
          </p>
        ) : (
          <>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {detail.factors.map((factor) => (
                <MetricCard
                  key={factor.canonical_id}
                  factor={factor}
                  asOf={asOf}
                />
              ))}
            </div>
            <PreTaxDisclosure
              factorIds={detail.factors
                .filter((f) => !f.is_na && f.value !== null)
                .map((f) => f.canonical_id)}
            />
          </>
        )}
      </section>

      {/*
       * 정정공시 이력 — §2.7 Observation. 회사가 수치를 정정했다는 사실 표.
       * §2.2 No Advice: 등락색·증감·가치 판단 0. vintage chain 중립 표시.
       * §2.4 PIT: asOf 기준 공개된 vintage 만 포함(backend 보장).
       */}
      <section className="mt-6">
        <h2 className="mb-3 text-sm font-medium text-neutral-700">
          {t("restatementHistory.heading")}
        </h2>
        <RestatementHistory code={code} asOf={asOf} />
      </section>

      {/*
       * 공시 목록 패널 + AI 사실 추출 진입점 — ADR-0026 D1/D3 + ADR-0031 D4.
       * 제목 + 접수일 + DART 원문링크 + AI 사실 추출 버튼(on-demand).
       * on-demand fetch — 사용자가 명시 진입한 이 1종목만.
       * asOf: PIT 기준 이후 공시 backend 가 제외(ADR-0026 D4).
       * AI 사실 추출: 사용자 명시 클릭 시에만 per-row 실행(ADR-0031 D4).
       */}
      <section className="mt-6">
        <h2 className="mb-3 text-sm font-medium text-neutral-700">
          {t("disclosures.heading")}
        </h2>
        <DisclosureWithFactsPanel code={code} asOf={asOf} />
      </section>

      {/*
       * 종목별 메모 패널 — T76. 사용자 scoped CRUD.
       * detail.id = code_lineage_id (UUID). Notes 는 사용자 자유 콘텐츠.
       * 시스템 UI 라벨은 §2.2 No Advice 중립 원칙 준수.
       */}
      <section className="mt-6">
        <h2 className="mb-3 text-sm font-medium text-neutral-700">
          {t("notes.heading")}
        </h2>
        <NotesPanel codeLineageId={detail.id} />
      </section>

      {/* lineage 의 종목코드 변경 history (2+ entry 인 경우만 렌더). */}
      <section className="mt-6">
        <CodeHistory history={detail.code_history} />
      </section>
    </main>
  );
}
