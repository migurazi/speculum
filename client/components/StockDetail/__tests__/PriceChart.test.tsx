/**
 * PriceChart 단위 테스트.
 *
 * lightweight-charts 는 jsdom 에서 canvas 미지원 → vi.mock 으로 createChart
 * 전체 mock. fetchStockPrices 도 globalThis.fetch mock.
 *
 * 매트릭스:
 *   1. 로딩 상태 — 스켈레톤 플레이스홀더 렌더.
 *   2. 에러 상태 — 에러 메시지 렌더, 페이지 안 깨짐.
 *   3. 빈 bars — "해당 기간 가격 데이터 없음" 안내.
 *   4. 데이터 있음 — 차트 컨테이너 + 토글 버튼 렌더.
 *   5. raw/adjusted 토글 — 버튼 aria-pressed 상태.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PriceChart } from "../PriceChart";
import type { StockPricesResponse } from "@/lib/api/prices";
import { renderWithIntl } from "@/test-utils/intl";

// lightweight-charts 전체 mock — jsdom 에서 canvas 미지원.
// createSeriesMarkers 포함 — v5 CA 마커 API 검증용 (M1 T57 Phase 2).
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
    // v5 CA 마커 플러그인 API — series 와 markers 를 받아 플러그인 인스턴스 반환.
    createSeriesMarkers: vi.fn(),
    CandlestickSeries: {},
    LineSeries: {},
  };
});

// 샘플 가격 응답.
const SAMPLE_BARS: StockPricesResponse = {
  code: "005930",
  asOf: "2024-09-30",
  bars: [
    {
      date: "2024-09-02",
      open: 71000,
      high: 72500,
      low: 70800,
      close: 72100,
      closeAdjusted: 72100,
      volume: 12345678,
    },
    {
      date: "2024-09-03",
      open: 72200,
      high: 73000,
      low: 71500,
      close: 71800,
      closeAdjusted: 71800,
      volume: 9876543,
    },
  ],
  actions: [],
};

const EMPTY_BARS: StockPricesResponse = {
  code: "000001",
  asOf: "2024-09-30",
  bars: [],
  actions: [],
};

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

// QueryClientProvider 는 ui 내부에 위치 — renderWithIntl 이 외부 NextIntlClientProvider 를 제공.
function renderChart(ui: React.ReactNode) {
  return renderWithIntl(
    <QueryClientProvider client={makeClient()}>{ui}</QueryClientProvider>,
  );
}

describe("PriceChart", () => {
  beforeEach(() => {
    process.env["NEXT_PUBLIC_API_BASE_URL"] = "http://test.local";
  });

  afterEach(() => {
    vi.clearAllMocks();
    // fetch mock 복원.
  });

  it("로딩 중 스켈레톤 플레이스홀더를 렌더한다", () => {
    // fetch 를 절대 resolve 하지 않는 pending mock.
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));

    renderChart(<PriceChart code="005930" asOf="2024-09-30" />);

    expect(screen.getByLabelText("가격 차트 로딩 중")).toBeInTheDocument();
  });

  it("fetch 실패 시 에러 메시지를 렌더한다 (페이지 안 깨짐)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("서버 오류", { status: 500 }),
    );

    renderChart(<PriceChart code="005930" asOf="2024-09-30" />);

    await waitFor(() => {
      expect(
        screen.getByText(/가격 데이터 로드 실패/),
      ).toBeInTheDocument();
    });
  });

  it("빈 bars 시 안내 메시지를 렌더한다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          code: EMPTY_BARS.code,
          as_of: EMPTY_BARS.asOf,
          bars: [],
        }),
        { status: 200 },
      ),
    );

    renderChart(<PriceChart code="000001" asOf="2024-09-30" />);

    await waitFor(() => {
      expect(
        screen.getByText("해당 기간 가격 데이터 없음"),
      ).toBeInTheDocument();
    });
    // 토글 버튼 미표시.
    expect(screen.queryByText("원본 종가")).not.toBeInTheDocument();
  });

  it("데이터 있음 — 차트 섹션 + 토글 버튼이 렌더된다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          code: SAMPLE_BARS.code,
          as_of: SAMPLE_BARS.asOf,
          bars: SAMPLE_BARS.bars.map((b) => ({
            date: b.date,
            open: String(b.open),
            high: String(b.high),
            low: String(b.low),
            close: String(b.close),
            close_adjusted: String(b.closeAdjusted),
            volume: b.volume,
          })),
        }),
        { status: 200 },
      ),
    );

    renderChart(<PriceChart code="005930" asOf="2024-09-30" />);

    await waitFor(() => {
      expect(screen.getByText("원본 종가")).toBeInTheDocument();
      expect(screen.getByText("수정 종가")).toBeInTheDocument();
    });
  });

  it("원본 종가 버튼이 기본 선택(aria-pressed=true)이다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          code: SAMPLE_BARS.code,
          as_of: SAMPLE_BARS.asOf,
          bars: SAMPLE_BARS.bars.map((b) => ({
            date: b.date,
            open: String(b.open),
            high: String(b.high),
            low: String(b.low),
            close: String(b.close),
            close_adjusted: String(b.closeAdjusted),
            volume: b.volume,
          })),
        }),
        { status: 200 },
      ),
    );

    renderChart(<PriceChart code="005930" asOf="2024-09-30" />);

    await waitFor(() => {
      const rawBtn = screen.getByText("원본 종가");
      expect(rawBtn).toHaveAttribute("aria-pressed", "true");
      const adjBtn = screen.getByText("수정 종가");
      expect(adjBtn).toHaveAttribute("aria-pressed", "false");
    });
  });

  it("수정 종가 버튼 클릭 시 aria-pressed 상태가 전환된다", async () => {
    const user = userEvent.setup();

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          code: SAMPLE_BARS.code,
          as_of: SAMPLE_BARS.asOf,
          bars: SAMPLE_BARS.bars.map((b) => ({
            date: b.date,
            open: String(b.open),
            high: String(b.high),
            low: String(b.low),
            close: String(b.close),
            close_adjusted: String(b.closeAdjusted),
            volume: b.volume,
          })),
        }),
        { status: 200 },
      ),
    );

    renderChart(<PriceChart code="005930" asOf="2024-09-30" />);

    await waitFor(() => {
      expect(screen.getByText("수정 종가")).toBeInTheDocument();
    });

    await user.click(screen.getByText("수정 종가"));

    expect(screen.getByText("수정 종가")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("원본 종가")).toHaveAttribute("aria-pressed", "false");
  });

  it("CA actions 가 있는 raw 모드에서 createSeriesMarkers 가 호출된다", async () => {
    // actions 포함 응답 — split 1건.
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          code: SAMPLE_BARS.code,
          as_of: SAMPLE_BARS.asOf,
          bars: SAMPLE_BARS.bars.map((b) => ({
            date: b.date,
            open: String(b.open),
            high: String(b.high),
            low: String(b.low),
            close: String(b.close),
            close_adjusted: String(b.closeAdjusted),
            volume: b.volume,
          })),
          actions: [
            { effective_date: "2024-09-02", action_type: "split", ratio: "5.0" },
          ],
        }),
        { status: 200 },
      ),
    );

    renderChart(<PriceChart code="005930" asOf="2024-09-30" />);

    await waitFor(() => {
      // 토글이 보이면 데이터가 렌더된 것.
      expect(screen.getByText("원본 종가")).toBeInTheDocument();
    });

    // v5: createSeriesMarkers(series, markers) 가 호출됐는지 확인.
    const lc = await import("lightweight-charts");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const mockCreateSeriesMarkers = lc.createSeriesMarkers as any;
    expect(mockCreateSeriesMarkers).toHaveBeenCalledOnce();
    // 두 번째 인자가 markers 배열.
    const markers = mockCreateSeriesMarkers.mock.calls[0]?.[1];
    expect(markers).toHaveLength(1);
    expect(markers[0]).toMatchObject({
      time: "2024-09-02",
      position: "aboveBar",
      shape: "arrowDown",
      text: "액면분할",
    });
  });

  it("actions 가 빈 배열이면 createSeriesMarkers 를 호출하지 않는다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          code: SAMPLE_BARS.code,
          as_of: SAMPLE_BARS.asOf,
          bars: SAMPLE_BARS.bars.map((b) => ({
            date: b.date,
            open: String(b.open),
            high: String(b.high),
            low: String(b.low),
            close: String(b.close),
            close_adjusted: String(b.closeAdjusted),
            volume: b.volume,
          })),
          actions: [],
        }),
        { status: 200 },
      ),
    );

    renderChart(<PriceChart code="005930" asOf="2024-09-30" />);

    await waitFor(() => {
      expect(screen.getByText("원본 종가")).toBeInTheDocument();
    });

    const lc = await import("lightweight-charts");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const mockCreateSeriesMarkers = lc.createSeriesMarkers as any;
    // 빈 배열 → createSeriesMarkers 호출 안 됨.
    expect(mockCreateSeriesMarkers).not.toHaveBeenCalled();
  });
});
