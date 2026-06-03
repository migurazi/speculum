"use client";

/**
 * 백테스트 페이지 — ADR-0027 M3 #1.
 *
 * BacktestPanel 을 페이지로 노출. 신규 라우트 /backtest.
 * Lab 페이지와 독립 — 별도 관심사 분리.
 *
 * No Advice 경계:
 *   - BacktestPanel 이 D4 출력 게이트·D3 survivorship·D6 평가 0 를 모두 강제.
 *   - 이 페이지는 라우팅 + 전역 QueryClient provider 연결만.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { BacktestPanel } from "@/components/Backtest/BacktestPanel";

/** QueryClient — 페이지 단위 생성 (전역 provider 미사용 시 폴백). */
function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 60_000 },
      mutations: { retry: 0 },
    },
  });
}

export default function BacktestPage(): JSX.Element {
  const t = useTranslations("backtest");
  // useState 로 QueryClient 안정화 — re-render 시 재생성 방지.
  const [qc] = useState(makeClient);

  return (
    <QueryClientProvider client={qc}>
      <main className="mx-auto max-w-3xl px-6 py-8">
        <h1 className="text-xl font-semibold text-neutral-900">{t("title")}</h1>
        <p className="mt-1 text-sm text-neutral-600">{t("description")}</p>

        <section className="mt-6">
          <BacktestPanel />
        </section>
      </main>
    </QueryClientProvider>
  );
}
