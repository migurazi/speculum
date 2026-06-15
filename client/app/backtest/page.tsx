"use client";

/**
 * 백테스트 페이지 — ADR-0027 M3 #1.
 *
 * BacktestPanel 을 페이지로 노출. 신규 라우트 /backtest.
 * Lab 페이지와 독립 — 별도 관심사 분리.
 *
 * QueryClient: **전역 provider 사용**. app/providers.tsx 의 QueryClientProvider
 * 가 app/layout.tsx 에서 트리 전체를 감싸므로 (다른 모든 페이지 — screener /
 * watchlist / portfolio — 와 동일), 본 페이지는 자체 client 를 만들지 않는다.
 * 페이지 전용 QueryClient 를 생성하면 전역 캐시와 분리된 섬(island) 이 되어
 * cross-page invalidate (예: data-freshness 공유) 가 끊기고 캐시가 이중화된다.
 *
 * No Advice 경계:
 *   - BacktestPanel 이 D4 출력 게이트·D3 survivorship·D6 평가 0 를 모두 강제.
 *   - 이 페이지는 라우팅 + 제목/설명 표시만.
 */

import { useTranslations } from "next-intl";

import { BacktestPanel } from "@/components/Backtest/BacktestPanel";

export default function BacktestPage(): JSX.Element {
  const t = useTranslations("backtest");

  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="text-xl font-semibold text-neutral-900">{t("title")}</h1>
      <p className="mt-1 text-sm text-neutral-600">{t("description")}</p>

      <section className="mt-6">
        <BacktestPanel />
      </section>
    </main>
  );
}
