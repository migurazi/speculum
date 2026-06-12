/**
 * CompareChart 단위 테스트 — T58.
 *
 * lightweight-charts 는 jsdom 에서 canvas 미지원 → vi.mock 으로 createChart
 * 전체 mock (PriceChart.test.tsx 패턴 동일). PriceScaleMode 도 mock 에 포함.
 *
 * 매트릭스:
 *   1. 로딩 overlay — 모든 fetch pending 시.
 *   2. 다종목 데이터 시 범례에 각 종목명/코드 + ADJ_POLICY_LABEL 렌더.
 *   3. 빈 bars — "해당 기간 가격 데이터 없음" 안내.
 *   4. 에러 처리 — 에러 overlay 렌더.
 *   5. 색 할당 로직 (getCompareColor) 단위 테스트.
 *   6. barsToSeriesData 변환 단위 테스트.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test-utils/intl";

import {
  CompareChart,
  COMPARE_COLORS,
  barsToSeriesData,
  getCompareColor,
} from "../CompareChart";

// ─────────────────────────────────────────────────────────────────────────────
// lightweight-charts 전체 mock — jsdom canvas 미지원.
// PriceChart.test.tsx 패턴 동일 + PriceScaleMode / LineSeries 추가.
// ─────────────────────────────────────────────────────────────────────────────
vi.mock("lightweight-charts", () => {
  const mockSeries = {
    setData: vi.fn(),
  };
  const mockTimeScale = {
    fitContent: vi.fn(),
  };
  const mockChart = {
    addSeries: vi.fn(() => mockSeries),
    removeSeries: vi.fn(),
    applyOptions: vi.fn(),
    timeScale: vi.fn(() => mockTimeScale),
    remove: vi.fn(),
  };
  return {
    createChart: vi.fn(() => mockChart),
    LineSeries: {},
    // PriceScaleMode: 컴포넌트가 Logarithmic(1) 을 참조 — 객체로 mock.
    PriceScaleMode: {
      Normal: 0,
      Logarithmic: 1,
      Percentage: 2,
      IndexedTo100: 3,
    },
  };
});

// ─────────────────────────────────────────────────────────────────────────────
// 헬퍼
// ─────────────────────────────────────────────────────────────────────────────

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

/** CompareChart 는 useTranslations + QueryClient 를 함께 필요로 한다. */
function renderChart(ui: React.ReactNode) {
  return renderWithIntl(
    <QueryClientProvider client={makeClient()}>{ui}</QueryClientProvider>,
  );
}

/** wire 형식으로 가격 bar 응답 생성 (backend snake_case). */
function makePricesResponse(code: string, closeAdjusted: number) {
  return {
    code,
    as_of: "2024-09-30",
    bars: [
      {
        date: "2024-09-02",
        open: "70000",
        high: "72000",
        low: "69000",
        close: "71000",
        close_adjusted: String(closeAdjusted),
        volume: 1000000,
      },
      {
        date: "2024-09-03",
        open: "71000",
        high: "73000",
        low: "70000",
        close: "72000",
        close_adjusted: String(closeAdjusted + 1000),
        volume: 900000,
      },
    ],
  };
}

// 비교 페이지에서 전달하는 items 형식.
const ITEMS_TWO = [
  { code: "005930", name: "삼성전자" },
  { code: "000660", name: "SK하이닉스" },
] as const;

const ITEMS_THREE = [
  { code: "005930", name: "삼성전자" },
  { code: "000660", name: "SK하이닉스" },
  { code: "035420", name: "NAVER" },
] as const;

// ─────────────────────────────────────────────────────────────────────────────
// 테스트
// ─────────────────────────────────────────────────────────────────────────────

describe("CompareChart", () => {
  beforeEach(() => {
    process.env["NEXT_PUBLIC_API_BASE_URL"] = "http://test.local";
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  // 1. 로딩 overlay ─────────────────────────────────────────────────────────
  it("모든 fetch 가 pending 일 때 로딩 overlay 를 렌더한다", () => {
    // 절대 resolve 하지 않는 pending mock — 종목 2개이므로 fetch 2회 예상.
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));

    renderChart(<CompareChart items={[...ITEMS_TWO]} asOf="2024-09-30" />);

    expect(screen.getByLabelText("가격 차트 로딩 중")).toBeInTheDocument();
  });

  // 2. 다종목 데이터 — 범례 + adj_policy ────────────────────────────────────
  it("데이터 로드 후 각 종목 이름/코드와 adj_policy 를 렌더한다", async () => {
    // 종목별로 fetch URL 순서 분기 — 첫 번째 call = 005930, 두 번째 = 000660.
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify(makePricesResponse("005930", 72100)),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify(makePricesResponse("000660", 150000)),
          { status: 200 },
        ),
      );

    renderChart(<CompareChart items={[...ITEMS_TWO]} asOf="2024-09-30" />);

    await waitFor(() => {
      // 범례: 종목명.
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
      expect(screen.getByText("SK하이닉스")).toBeInTheDocument();
      // 범례: 종목코드.
      expect(screen.getByText("005930")).toBeInTheDocument();
      expect(screen.getByText("000660")).toBeInTheDocument();
      // ADR-0001 D6 — adj_policy 상시 표시.
      expect(
        screen.getByText("Adjusted: v1.0 rights-only"),
      ).toBeInTheDocument();
    });
  });

  it("3 종목 범례를 올바르게 렌더한다", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(makePricesResponse("005930", 72100)), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(makePricesResponse("000660", 150000)), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(makePricesResponse("035420", 200000)), { status: 200 }),
      );

    renderChart(<CompareChart items={[...ITEMS_THREE]} asOf="2024-09-30" />);

    await waitFor(() => {
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
      expect(screen.getByText("SK하이닉스")).toBeInTheDocument();
      expect(screen.getByText("NAVER")).toBeInTheDocument();
    });
  });

  // 3. 빈 bars ───────────────────────────────────────────────────────────────
  it("모든 종목의 bars 가 빈 배열이면 '해당 기간 가격 데이터 없음' 을 렌더한다", async () => {
    const emptyResponse = { code: "005930", as_of: "2024-09-30", bars: [] };

    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(emptyResponse), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...emptyResponse, code: "000660" }),
          { status: 200 },
        ),
      );

    renderChart(<CompareChart items={[...ITEMS_TWO]} asOf="2024-09-30" />);

    await waitFor(() => {
      expect(
        screen.getByText("해당 기간 가격 데이터 없음"),
      ).toBeInTheDocument();
    });
  });

  // 4. 에러 처리 ─────────────────────────────────────────────────────────────
  it("모든 fetch 가 실패하면 에러 overlay 를 렌더한다", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("서버 오류", { status: 500 }))
      .mockResolvedValueOnce(new Response("서버 오류", { status: 500 }));

    renderChart(<CompareChart items={[...ITEMS_TWO]} asOf="2024-09-30" />);

    await waitFor(() => {
      expect(screen.getByText(/가격 데이터 로드 실패/)).toBeInTheDocument();
    });
  });

  // 5. adj_policy 는 항상 표시 ───────────────────────────────────────────────
  it("로딩 중에도 adj_policy 레이블이 표시된다", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));

    renderChart(<CompareChart items={[...ITEMS_TWO]} asOf="2024-09-30" />);

    // adj_policy 는 데이터 로드 전/후 무관하게 항상 렌더 (헤더 상단 고정).
    expect(screen.getByText("Adjusted: v1.0 rights-only")).toBeInTheDocument();
  });

  // 6. items 가 빈 배열이면 fetch 하지 않고 정상 렌더 ─────────────────────
  it("items 가 빈 배열이면 범례 없이 정상 렌더한다", () => {
    renderChart(<CompareChart items={[]} asOf="2024-09-30" />);
    // 에러/exception 없이 렌더 — 범례 없음.
    expect(screen.queryByLabelText("종목 범례")).not.toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 순수 함수 단위 테스트
// ─────────────────────────────────────────────────────────────────────────────

describe("getCompareColor", () => {
  it("index 0~5 를 팔레트 6색에 매핑한다", () => {
    for (let i = 0; i < COMPARE_COLORS.length; i++) {
      expect(getCompareColor(i)).toBe(COMPARE_COLORS[i]);
    }
  });

  it("index 범위 초과 시 마지막 색으로 fallback 한다", () => {
    expect(getCompareColor(99)).toBe(COMPARE_COLORS[COMPARE_COLORS.length - 1]);
  });

  it("팔레트에 한국 등락색(빨강 #dc2626, 녹색 #16a34a)이 없다", () => {
    // No Advice — 등락 암시 색 제외 확인.
    for (const color of COMPARE_COLORS) {
      expect(color).not.toBe("#dc2626");
      expect(color).not.toBe("#16a34a");
    }
  });
});

describe("barsToSeriesData", () => {
  it("PriceBar 배열을 {time, value} 형식으로 변환한다", () => {
    const bars = [
      { date: "2024-09-02", closeAdjusted: 72100 },
      { date: "2024-09-03", closeAdjusted: 71800 },
    ];
    const result = barsToSeriesData(bars);
    expect(result).toEqual([
      { time: "2024-09-02", value: 72100 },
      { time: "2024-09-03", value: 71800 },
    ]);
  });

  it("빈 배열을 빈 배열로 변환한다", () => {
    expect(barsToSeriesData([])).toEqual([]);
  });

  it("closeAdjusted 값을 value 로 그대로 전달한다", () => {
    const bars = [{ date: "2024-01-15", closeAdjusted: 123456.78 }];
    const result = barsToSeriesData(bars);
    expect(result[0]?.value).toBe(123456.78);
  });
});
