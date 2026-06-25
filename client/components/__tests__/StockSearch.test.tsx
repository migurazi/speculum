/**
 * StockSearch 단위 테스트 — T36/T37.
 *
 * 매트릭스:
 *   1. 타이핑(디바운스 후) → 드롭다운에 mock 결과 렌더
 *   2. 항목 클릭 → router.push('/stock/005930') 호출
 *   3. 빈 결과 → "결과 없음" 표시
 *   4. 키보드 ArrowDown+Enter → 첫 항목 선택·이동
 *   5. query 비면 드롭다운 닫힘(표시 안 됨)
 *   6. Escape → 드롭다운 닫힘
 *   7. 에러 상태 → "검색 실패" 표시
 *
 * 디바운스 전략:
 *   debounceMs={0} prop 을 전달하여 실 디바운스를 제거.
 *   → fake timers 불필요 → waitFor 로 Promise 해소 대기만 하면 됨.
 *   실 250ms 디바운스 동작은 prop default 값으로 보장되며 통합 수준에서 확인.
 */

import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { StockSearch } from "../StockSearch";
import { renderWithIntl } from "@/test-utils/intl";
import type { StockSearchPage } from "@/lib/api/stocks";
import { useAsOfStore } from "@/state/as-of-store";

// ─── mock 선언 ──────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const mockSearchStocks = vi.fn<(...args: any[]) => Promise<StockSearchPage>>();

vi.mock("@/lib/api/stocks", () => ({
  searchStocks: (
    q: string,
    asOf: string,
    opts: { limit?: number; signal?: AbortSignal },
  ) => mockSearchStocks(q, asOf, opts),
}));

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

// ─── 헬퍼 ───────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
        staleTime: 0,
      },
    },
  });
}

/**
 * QueryClientProvider + NextIntlClientProvider 래핑 렌더.
 * debounceMs=0 으로 디바운스를 비활성화하여 타이핑 즉시 query 발화.
 */
function renderStockSearch() {
  const qc = makeQueryClient();
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderWithIntl(
    <Wrapper>
      <StockSearch debounceMs={0} />
    </Wrapper>,
  );
}

const MOCK_RESULT: StockSearchPage = {
  items: [
    {
      id: "uuid-1",
      code: "005930",
      name: "삼성전자",
      market: "KOSPI",
      listing_date: "1975-06-11",
      delisting_date: null,
      status: "active",
    },
    {
      id: "uuid-2",
      code: "005935",
      name: "삼성전자우",
      market: "KOSPI",
      listing_date: "1976-01-01",
      delisting_date: null,
      status: "active",
    },
  ],
  total: 2,
  next_cursor: null,
};

// ─── 테스트 ──────────────────────────────────────────────────────────────────

describe("StockSearch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    act(() => {
      useAsOfStore.getState().setAsOf("2024-06-28");
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  // ─── 1. 타이핑 후 드롭다운 ──────────────────────────────────────────────
  it("타이핑(디바운스 후) → 드롭다운에 검색 결과 표시", async () => {
    mockSearchStocks.mockResolvedValue(MOCK_RESULT);
    const user = userEvent.setup();

    renderStockSearch();

    const input = screen.getByRole("combobox");
    await user.type(input, "삼성");

    await waitFor(() => {
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
    });

    expect(screen.getByText("005930")).toBeInTheDocument();
    expect(screen.getByText("삼성전자우")).toBeInTheDocument();
    expect(screen.getAllByText("KOSPI")).toHaveLength(2);
  });

  // ─── 2. 항목 클릭 → router.push ─────────────────────────────────────────
  it("항목 클릭 → router.push('/stock/005930') 호출", async () => {
    mockSearchStocks.mockResolvedValue(MOCK_RESULT);
    const user = userEvent.setup();

    renderStockSearch();

    const input = screen.getByRole("combobox");
    await user.type(input, "삼성");

    await waitFor(() => {
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
    });

    // mouseDown 이벤트로 클릭 — StockSearch 가 onMouseDown 으로 항목 선택 처리
    const item = screen.getByText("삼성전자").closest("li");
    expect(item).not.toBeNull();
    await user.pointer({ target: item!, keys: "[MouseLeft>]" });

    expect(mockPush).toHaveBeenCalledWith("/stock/005930");
  });

  // ─── 3. 빈 결과 → "결과 없음" ───────────────────────────────────────────
  it("빈 결과 → '결과 없음' 표시", async () => {
    mockSearchStocks.mockResolvedValue({ items: [], total: 0, next_cursor: null });
    const user = userEvent.setup();

    renderStockSearch();

    const input = screen.getByRole("combobox");
    await user.type(input, "존재하지않는종목");

    await waitFor(() => {
      expect(screen.getByText("결과 없음")).toBeInTheDocument();
    });
  });

  // ─── 4. ArrowDown + Enter → 첫 항목 선택 ────────────────────────────────
  it("ArrowDown + Enter → 첫 항목 선택·이동", async () => {
    mockSearchStocks.mockResolvedValue(MOCK_RESULT);
    const user = userEvent.setup();

    renderStockSearch();

    const input = screen.getByRole("combobox");
    await user.type(input, "삼성");

    await waitFor(() => {
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
    });

    // ArrowDown → index=0 하이라이트, Enter → 첫 항목(005930) 선택
    await user.keyboard("{ArrowDown}");
    await user.keyboard("{Enter}");

    expect(mockPush).toHaveBeenCalledWith("/stock/005930");
  });

  // ─── 5. 빈 입력이면 드롭다운 없음 ──────────────────────────────────────
  it("입력이 빈 문자열이면 드롭다운(listbox)이 없다", () => {
    renderStockSearch();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  // ─── 6. Escape → 드롭다운 닫힘 ─────────────────────────────────────────
  it("Escape → 드롭다운 닫힘", async () => {
    mockSearchStocks.mockResolvedValue(MOCK_RESULT);
    const user = userEvent.setup();

    renderStockSearch();

    const input = screen.getByRole("combobox");
    await user.type(input, "삼성");

    await waitFor(() => {
      expect(screen.getByRole("listbox")).toBeInTheDocument();
    });

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  // ─── 7. 에러 → "검색 실패" ──────────────────────────────────────────────
  it("API 에러 → '검색 실패' 표시", async () => {
    const consoleSpy = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);
    mockSearchStocks.mockRejectedValue(new Error("network error"));
    const user = userEvent.setup();

    renderStockSearch();

    const input = screen.getByRole("combobox");
    await user.type(input, "삼성");

    await waitFor(() => {
      expect(screen.getByText("검색 실패")).toBeInTheDocument();
    });

    consoleSpy.mockRestore();
  });
});
