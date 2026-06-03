/**
 * FinancialSeriesTable 단위 테스트 — M1 T57 Phase 2.
 *
 * lightweight-charts 불필요 — 재무 표는 순수 HTML table.
 * fetchStockFinancials 는 globalThis.fetch mock.
 *
 * 매트릭스:
 *   1. 로딩 상태 — 스켈레톤 플레이스홀더 렌더.
 *   2. 에러 상태 — 에러 메시지 박스, 페이지 안 깨짐.
 *   3. 빈 items — "재무 데이터가 없습니다." 안내.
 *   4. 데이터 있음 — period 헤더 + 항목명 + 값 렌더.
 *   5. 결손 값(null) — "—" 표시.
 *   6. 천단위 구분자 — 큰 숫자에 comma 삽입, 값 왜곡 없음.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FinancialSeriesTable } from "../FinancialSeriesTable";
import type { FinancialSeries } from "@/lib/api/financials";
import { renderWithIntl } from "@/test-utils/intl";

/** 정상 재무 시계열 응답 wire 포맷. */
const SAMPLE_WIRE = {
  code: "005930",
  as_of: "2024-05-07",
  periods: ["2023Q1", "2023Q2", "2023Q3", "2023Q4"],
  items: [
    {
      account: "revenue",
      name: "매출액",
      unit: "krw",
      values: ["60000000000000", null, "75000000000000", "80000000000000"],
    },
    {
      account: "basic_eps",
      name: "기본 EPS",
      unit: "krw",
      values: ["1234", "567", null, "2890"],
    },
  ],
};

/** items 빈 wire 포맷. */
const EMPTY_WIRE = {
  code: "000001",
  as_of: "2024-05-07",
  periods: [],
  items: [],
};

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTable(ui: React.ReactNode) {
  return renderWithIntl(
    <QueryClientProvider client={makeClient()}>{ui}</QueryClientProvider>,
  );
}

describe("FinancialSeriesTable", () => {
  beforeEach(() => {
    process.env["NEXT_PUBLIC_API_BASE_URL"] = "http://test.local";
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("로딩 중 스켈레톤 플레이스홀더를 렌더한다", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));

    const { container } = renderTable(
      <FinancialSeriesTable code="005930" asOf="2024-05-07" />,
    );

    // animate-pulse 가 있는 요소가 존재해야 한다.
    const skeleton = container.querySelector(".animate-pulse");
    expect(skeleton).toBeInTheDocument();
  });

  it("fetch 실패 시 에러 메시지를 렌더한다 (페이지 안 깨짐)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("서버 오류", { status: 500 }),
    );

    renderTable(<FinancialSeriesTable code="005930" asOf="2024-05-07" />);

    await waitFor(() => {
      expect(screen.getByText(/재무 데이터 로드 실패/)).toBeInTheDocument();
    });
  });

  it("items 가 비어 있으면 안내 메시지를 렌더한다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(EMPTY_WIRE), { status: 200 }),
    );

    renderTable(<FinancialSeriesTable code="000001" asOf="2024-05-07" />);

    await waitFor(() => {
      expect(screen.getByText("재무 데이터가 없습니다.")).toBeInTheDocument();
    });
  });

  it("period 헤더가 열로 렌더된다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_WIRE), { status: 200 }),
    );

    renderTable(<FinancialSeriesTable code="005930" asOf="2024-05-07" />);

    await waitFor(() => {
      expect(screen.getByText("2023Q1")).toBeInTheDocument();
      expect(screen.getByText("2023Q2")).toBeInTheDocument();
      expect(screen.getByText("2023Q3")).toBeInTheDocument();
      expect(screen.getByText("2023Q4")).toBeInTheDocument();
    });
  });

  it("항목명(name)이 행 헤더로 렌더된다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_WIRE), { status: 200 }),
    );

    renderTable(<FinancialSeriesTable code="005930" asOf="2024-05-07" />);

    await waitFor(() => {
      expect(screen.getByText("매출액")).toBeInTheDocument();
      expect(screen.getByText("기본 EPS")).toBeInTheDocument();
    });
  });

  it("결손 값(null) 은 '—' 로 표시된다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_WIRE), { status: 200 }),
    );

    renderTable(<FinancialSeriesTable code="005930" asOf="2024-05-07" />);

    await waitFor(() => {
      // 결손이 2개 있으므로 "—" 셀이 2개 이상이어야 한다.
      const dashes = screen.getAllByText("—");
      expect(dashes.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("큰 숫자에 천단위 구분자가 삽입된다 (값 왜곡 없음)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_WIRE), { status: 200 }),
    );

    renderTable(<FinancialSeriesTable code="005930" asOf="2024-05-07" />);

    await waitFor(() => {
      // 60,000,000,000,000 — 천단위 쉼표 포함.
      expect(screen.getByText("60,000,000,000,000")).toBeInTheDocument();
    });
  });

  it("unit 이 ratio 가 아닌 경우 unit 텍스트가 표시된다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_WIRE), { status: 200 }),
    );

    renderTable(<FinancialSeriesTable code="005930" asOf="2024-05-07" />);

    await waitFor(() => {
      // unit = "krw" → "(krw)" 표시.
      const unitLabels = screen.getAllByText("(krw)");
      expect(unitLabels.length).toBeGreaterThan(0);
    });
  });
});
