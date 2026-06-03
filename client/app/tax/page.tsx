"use client";

/**
 * 세금 계산 페이지 — ADR-0030 M3 #5.
 *
 * TaxCalculatorPanel 을 페이지로 노출. 신규 라우트 /tax.
 * 증권거래세 계산기만 (ADR-0030 D1 — 양도세 deferred).
 *
 * No Advice 경계:
 *   - TaxCalculatorPanel 이 D3 세무자문 디스클레이머 게이트·D2 개별입력0·
 *     D4 법령출처 명시를 모두 강제.
 *   - 이 페이지는 라우팅 + 전역 QueryClient provider 연결만.
 *
 * release blocker: ADR-0030 D6 — 세무사+변호사 자문 완료 전 운영 노출 금지.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { TaxCalculatorPanel } from "@/components/Tax/TaxCalculatorPanel";

/** QueryClient — 페이지 단위 생성 (전역 provider 미사용 시 폴백). */
function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 60_000 },
      mutations: { retry: 0 },
    },
  });
}

export default function TaxPage(): JSX.Element {
  const t = useTranslations("tax");
  // useState 로 QueryClient 안정화 — re-render 시 재생성 방지.
  const [qc] = useState(makeClient);

  return (
    <QueryClientProvider client={qc}>
      <main className="mx-auto max-w-2xl px-6 py-8">
        <h1 className="text-xl font-semibold text-neutral-900">{t("title")}</h1>
        <p className="mt-1 text-sm text-neutral-600">{t("description")}</p>

        <section className="mt-6">
          <TaxCalculatorPanel />
        </section>
      </main>
    </QueryClientProvider>
  );
}
