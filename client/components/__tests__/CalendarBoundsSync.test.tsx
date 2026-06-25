/**
 * CalendarBoundsSync 단위 테스트.
 *
 * 검증 항목:
 *   1. fetchCalendarCoverage 응답 도착 후 CalendarBoundsStore 가 갱신됨.
 *   2. 범위 밖 asOf(미래 날짜)가 upperBound 로 클램프됨.
 *   3. 이미 범위 내 asOf 는 변경되지 않음.
 *
 * fetchCalendarCoverage 를 vi.mock 으로 대체. QueryClientProvider 로 감싸
 * useQuery 를 활성화.
 */

import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { CalendarBoundsSync } from "@/components/CalendarBoundsSync";
import { useAsOfStore } from "@/state/as-of-store";
import { useCalendarBoundsStore } from "@/state/calendar-bounds-store";

// fetchCalendarCoverage 모킹 — 순수 함수(clampUpperBound, clampAsOf)는 실 구현 사용.
const mockFetchCalendarCoverage = vi.fn();
vi.mock("@/lib/api/calendar", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/calendar")>();
  return {
    ...actual,
    fetchCalendarCoverage: (...args: unknown[]) =>
      mockFetchCalendarCoverage(...args),
  };
});

/** 테스트용 QueryClient 래퍼 — retry 0 으로 실패 빠르게. */
function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: 0 },
    },
  });
}

function wrapper({ children }: { children: ReactNode }): JSX.Element {
  const qc = makeQueryClient();
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const MOCK_COVERAGE = {
  minDate: "2024-01-01",
  maxDate: "2024-12-31",
  earliestBusinessDay: "2024-01-02",
  latestBusinessDay: "2024-12-30",
  latestDataDate: "2024-06-28",
  version: "1.0.0",
};

describe("CalendarBoundsSync", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchCalendarCoverage.mockResolvedValue(MOCK_COVERAGE);
    // 스토어 초기화.
    useCalendarBoundsStore.setState({ bounds: null });
    useAsOfStore.getState().resetToToday();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("data 도착 후 CalendarBoundsStore 가 갱신됨", async () => {
    render(<CalendarBoundsSync />, { wrapper });

    await waitFor(() => {
      const bounds = useCalendarBoundsStore.getState().bounds;
      expect(bounds).not.toBeNull();
      expect(bounds?.lowerBound).toBe("2024-01-02");
      expect(bounds?.upperBound).toBe("2024-06-28");
      expect(bounds?.minDate).toBe("2024-01-01");
      expect(bounds?.maxDate).toBe("2024-12-31");
    });
  });

  it("범위 밖 asOf(미래 날짜) → upperBound 로 클램프", async () => {
    // 미래 날짜로 설정.
    useAsOfStore.getState().setAsOf("2026-06-19");

    render(<CalendarBoundsSync />, { wrapper });

    await waitFor(() => {
      expect(useAsOfStore.getState().asOf).toBe("2024-06-28");
    });
  });

  it("범위 내 asOf → 변경 없음", async () => {
    useAsOfStore.getState().setAsOf("2024-03-15");

    render(<CalendarBoundsSync />, { wrapper });

    await waitFor(() => {
      // bounds 가 채워졌음을 먼저 확인.
      expect(useCalendarBoundsStore.getState().bounds).not.toBeNull();
    });

    // asOf 는 그대로여야 함.
    expect(useAsOfStore.getState().asOf).toBe("2024-03-15");
  });
});
