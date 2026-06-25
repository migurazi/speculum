/**
 * Screener page 0건 분기 투명성 테스트 — S4.
 *
 * useMutation 을 직접 mock 해 성공 응답을 주입한 뒤, total=0 일 때
 * universe_size/na_excluded_count 조합에 따라 올바른 안내 메시지가
 * 표시되는지 검증한다.
 *
 * 분기:
 *   A) universe_size === 0          → 데이터 미적재 (해당 자산군 종목 없음)
 *   B) na_excluded_count > 0        → 모집단에서 NA 제외로 인한 0건
 *   C) universe_size > 0, na === 0  → 진짜 조건 불충족
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi, beforeEach } from "vitest";

import ScreenerPage from "@/app/screener/page";
import { TEST_MESSAGES } from "@/test-utils/intl";
import type { ScreenResult } from "@/lib/api/screen";

// executeScreen API mock — 테스트마다 resolvedValue 를 교체
vi.mock("@/lib/api/screen", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/screen")>();
  return {
    ...actual,
    executeScreen: vi.fn(),
  };
});

// fetchFactors mock — factor 목록이 필요해 Run 버튼 활성화용 최소 mock
vi.mock("@/lib/api/factors", () => ({
  fetchFactors: vi.fn().mockResolvedValue([
    {
      canonicalId: "eps_trailing_annual",
      name: "주당순이익(EPS)",
      unit: "원",
      sourceKind: "financial",
    },
  ]),
}));

// as-of store — 기준일 고정
vi.mock("@/state/as-of-store", () => ({
  useAsOfStore: (selector: (s: { asOf: string }) => string) =>
    selector({ asOf: "2024-06-28" }),
}));

/** 테스트 렌더 헬퍼 — QueryClient + next-intl provider */
function renderScreenerPage(): void {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <NextIntlClientProvider locale="ko" messages={TEST_MESSAGES}>
        <ScreenerPage />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
}

/**
 * factor 체크박스를 선택하고 조건을 추가한 뒤 Run 버튼을 클릭.
 * ConditionBuilder 의 조건 추가 UI 를 통해 canRun=true 만들기 위한 헬퍼.
 *
 * 주의: ConditionBuilder 가 factor select+value 두 입력을 가지므로
 * 조건 추가 후 factor·value를 직접 설정한다.
 */
async function clickRunButton(): Promise<void> {
  // factor 목록 로드 대기 (fetchFactors mock 비동기)
  await waitFor(() => {
    expect(screen.queryByText("factor 목록 로딩 중...")).toBeNull();
  });

  // factor 체크박스 체크 (결과 표시 factor)
  const checkbox = screen.getByRole("checkbox", { name: /주당순이익/ });
  fireEvent.click(checkbox);

  // 조건 추가 버튼 클릭
  const addBtn = screen.getByRole("button", { name: /조건 추가/ });
  fireEvent.click(addBtn);

  // 조건 factor 선택 (첫 번째 select)
  const factorSelects = screen.getAllByRole("combobox");
  // 첫 번째 select = 조건 factor 선택
  if (factorSelects[0]) {
    fireEvent.change(factorSelects[0], { target: { value: "eps_trailing_annual" } });
  }

  // 조건 값 입력 (aria-label "조건 1 값" textbox)
  const valueInput = screen.getByRole("textbox", { name: /조건 1 값/ });
  fireEvent.change(valueInput, { target: { value: "0" } });

  // Run 버튼 클릭
  const runBtn = screen.getByRole("button", { name: /Screener 실행/ });
  fireEvent.click(runBtn);
}

/** executeScreen 에 지정 결과 주입 후 Run 클릭 + 메시지 waitFor 헬퍼 */
async function renderAndRunWith(result: ScreenResult): Promise<void> {
  const { executeScreen } = await import("@/lib/api/screen");
  vi.mocked(executeScreen).mockResolvedValue(result);

  renderScreenerPage();
  await clickRunButton();
}

describe("ScreenerPage 0건 분기 투명성 (S4)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("A) universe_size === 0 → 데이터 미적재 메시지", async () => {
    await renderAndRunWith({
      result_codes: [],
      total: 0,
      data_versions: {},
      universe_size: 0,
      na_excluded_count: 0,
    });

    await waitFor(() => {
      expect(
        screen.getByText(/해당 자산군에 적재된 종목이 없습니다/),
      ).toBeDefined();
    });
  });

  it("B) universe_size > 0, na_excluded_count > 0 → NA 제외 메시지 (종목 수 포함)", async () => {
    await renderAndRunWith({
      result_codes: [],
      total: 0,
      data_versions: {},
      universe_size: 150,
      na_excluded_count: 30,
    });

    await waitFor(() => {
      // 메시지에 universe_size, na_excluded_count 수치가 포함되어야 함
      expect(
        screen.getByText(/150종목 중 30종목이 데이터 미적재로 제외/),
      ).toBeDefined();
    });
  });

  it("C) universe_size > 0, na_excluded_count === 0 → 조건 불충족 메시지", async () => {
    await renderAndRunWith({
      result_codes: [],
      total: 0,
      data_versions: {},
      universe_size: 150,
      na_excluded_count: 0,
    });

    await waitFor(() => {
      expect(
        screen.getByText(/조건을 만족하는 종목이 없습니다/),
      ).toBeDefined();
    });
  });
});
