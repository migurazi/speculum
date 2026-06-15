/**
 * StockDetailPage — I-1 상태 레이블 i18n 회귀 가드.
 *
 * I-1: 헤더 status 배지가 하드코딩 STATUS_LABEL_KO("거래 중"/"미상장"/"상장폐지")
 *      대신 i18n(stock.statusActive/statusNotYetListed/statusDelisted)을 렌더.
 *      미등록 status 는 raw 표시(fallback) 보존.
 *
 * 셋업: fetchStockDetail mock + useParams/as-of store stub + 무거운 자식
 * 컴포넌트(PriceChart 등) mock(차트 라이브러리 회피, 본 테스트 범위는 헤더만).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, afterEach } from "vitest";

import StockDetailPage from "../page";
import { fetchStockDetail } from "@/lib/api/stocks";
import type { StockDetail, StockStatus } from "@/lib/api/stocks";
import { renderWithIntl } from "@/test-utils/intl";

vi.mock("next/navigation", () => ({
  useParams: () => ({ code: "005930" }),
}));

vi.mock("@/state/as-of-store", () => ({
  useAsOfStore: (selector: (s: { asOf: string }) => unknown) =>
    selector({ asOf: "2026-05-15" }),
}));

vi.mock("@/lib/api/stocks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/stocks")>();
  return { ...actual, fetchStockDetail: vi.fn() };
});

// 헤더 외 자식 컴포넌트는 본 테스트 범위 밖 — 가볍게 stub.
vi.mock("@/components/StockDetail/PriceChart", () => ({
  PriceChart: () => <div data-testid="price-chart" />,
}));
vi.mock("@/components/StockDetail/FinancialSeriesTable", () => ({
  FinancialSeriesTable: () => <div />,
}));
vi.mock("@/components/StockDetail/MetricCard", () => ({
  MetricCard: () => <div />,
}));
vi.mock("@/components/StockDetail/PreTaxDisclosure", () => ({
  PreTaxDisclosure: () => <div />,
}));
vi.mock("@/components/StockDetail/RestatementHistory", () => ({
  RestatementHistory: () => <div />,
}));
vi.mock("@/components/StockDetail/DisclosureWithFactsPanel", () => ({
  DisclosureWithFactsPanel: () => <div />,
}));
vi.mock("@/components/StockDetail/NotesPanel", () => ({
  NotesPanel: () => <div />,
}));
vi.mock("@/components/StockDetail/CodeHistory", () => ({
  CodeHistory: () => <div />,
}));

function baseDetail(status: StockStatus | string): StockDetail {
  return {
    id: "lineage-1",
    code: "005930",
    name: "삼성전자",
    market: "KOSPI",
    listing_date: "1975-06-11",
    delisting_date: null,
    fiscal_month: 12,
    ifrs_preference: "consolidated",
    status: status as StockStatus,
    code_history: [],
    factors: [],
  };
}

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage() {
  return renderWithIntl(
    <QueryClientProvider client={makeClient()}>
      <StockDetailPage />
    </QueryClientProvider>,
  );
}

describe("StockDetailPage status label i18n (I-1)", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("active → '거래 중' (i18n statusActive)", async () => {
    vi.mocked(fetchStockDetail).mockResolvedValue(baseDetail("active"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("거래 중")).toBeInTheDocument();
    });
  });

  it("not_yet_listed → '미상장'", async () => {
    vi.mocked(fetchStockDetail).mockResolvedValue(
      baseDetail("not_yet_listed"),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("미상장")).toBeInTheDocument();
    });
  });

  it("delisted → '상장폐지'", async () => {
    vi.mocked(fetchStockDetail).mockResolvedValue(baseDetail("delisted"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("상장폐지")).toBeInTheDocument();
    });
  });

  it("미등록 status 는 raw 문자열 그대로 표시한다 (fallback 보존)", async () => {
    vi.mocked(fetchStockDetail).mockResolvedValue(baseDetail("suspended"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("suspended")).toBeInTheDocument();
    });
  });
});
