/**
 * /backtest 페이지 QueryClient 배선 회귀 가드 — 이중 QueryClient island 방지.
 *
 * 과거 버그: BacktestPage 가 자체 QueryClient + QueryClientProvider 를 생성해
 * (app/providers.tsx 의 전역 provider 가 layout 에서 이미 트리를 감싸는데도)
 * BacktestPanel 의 쿼리가 전역 캐시와 분리된 섬(island) 에 적재됐다 → cross-page
 * invalidate (예: data-freshness 공유) 단절 + 캐시 이중화. 다른 모든 페이지
 * (screener / watchlist / portfolio) 는 전역 provider 만 사용한다.
 *
 * 본 테스트는 페이지를 **앰비언트(전역) QueryClient** 아래 렌더한 뒤, BacktestPanel
 * 의 data-freshness 쿼리가 그 앰비언트 client 캐시에 적재되는지 확인한다. 페이지가
 * island 를 만들면 쿼리가 globalClient 캐시에 나타나지 않아 테스트가 실패한다 —
 * 즉 island 재도입에 대한 회귀 가드.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import BacktestPage from "@/app/backtest/page";
import { getDataFreshness } from "@/lib/api/backtest";
import type { DataFreshness } from "@/lib/api/backtest";
import { renderWithIntl } from "@/test-utils/intl";

// API mock — getDataFreshness 만 mount 시 호출. run/reproduce 는 사용자 액션용.
vi.mock("@/lib/api/backtest", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/backtest")>();
  return {
    ...actual,
    runBacktest: vi.fn(),
    reproduceBacktest: vi.fn(),
    getDataFreshness: vi.fn(),
  };
});

// lightweight-charts mock — jsdom 환경 (BacktestPanel 의 차트 컴포넌트).
vi.mock("lightweight-charts", () => {
  const mockSeries = { setData: () => undefined };
  const mockChart = {
    addSeries: () => mockSeries,
    timeScale: () => ({ fitContent: () => undefined }),
    applyOptions: () => undefined,
    remove: () => undefined,
  };
  return { createChart: () => mockChart, LineSeries: {} };
});

const MOCK_FRESHNESS: DataFreshness = {
  krx: { source: "krx", latestBatchAt: "2026-06-01", isStale: false, elapsedDays: 3 },
  dart: { source: "dart", latestBatchAt: "2026-06-01", isStale: false, elapsedDays: 3 },
  kosis: { source: "kosis", latestBatchAt: "2026-05-15", isStale: false, elapsedDays: 20 },
  asOf: "2026-06-04",
};

describe("BacktestPage QueryClient 배선", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getDataFreshness).mockResolvedValue(MOCK_FRESHNESS);
  });

  it("자체 island 가 아닌 앰비언트(전역) QueryClient 를 사용한다", async () => {
    // layout 의 전역 QueryClientProvider 를 모사 — retry:false 로 테스트 안정화.
    const globalClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    renderWithIntl(
      <QueryClientProvider client={globalClient}>
        <BacktestPage />
      </QueryClientProvider>,
    );

    // BacktestPanel 의 data-freshness 쿼리(queryKey ["data-freshness"]) 가 전역
    // client 캐시에 적재되면 페이지가 전역 client 를 쓰는 것 — island 였다면
    // 이 캐시에 나타나지 않는다.
    await waitFor(() => {
      expect(globalClient.getQueryData(["data-freshness"])).toBeDefined();
    });
  });
});
