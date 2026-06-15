/**
 * PortfolioPanel 탭 — WAI-ARIA tabs 패턴 a11y.
 *
 * 과거: 탭을 <nav>+<button> 으로 구현해 role=tablist/tab/tabpanel, aria-selected,
 * aria-controls, 키보드 화살표 네비가 전무 → 스크린리더가 탭 위젯으로 인식 못 하고
 * 키보드 사용자가 화살표로 탭 이동 불가.
 *
 * 검증:
 *   1. tablist + 2 tab role, aria-selected / aria-controls / roving tabindex.
 *   2. tabpanel role + id + aria-labelledby (선택 탭과 연결).
 *   3. ArrowRight → 다음 탭 활성화(자동 활성화 패턴) + tabpanel 전환.
 *   4. ArrowLeft(첫 탭에서) → 마지막 탭으로 wrap.
 *   5. Home/End → 처음/마지막 탭.
 *
 * 렌더 셋업은 portfolio-gate.test.tsx 와 동일 — API/next-auth mock + QueryClient.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { PortfolioPanel } from "@/components/Portfolio/PortfolioPanel";
import { listTransactions, listPositions } from "@/lib/api/portfolio";
import { renderWithIntl } from "@/test-utils/intl";

vi.mock("@/lib/api/portfolio", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/portfolio")>();
  return {
    ...actual,
    listTransactions: vi.fn(),
    listPositions: vi.fn(),
    addTransaction: vi.fn(),
    deleteTransaction: vi.fn(),
  };
});

vi.mock("next-auth/react", () => ({
  signIn: vi.fn(),
  useSession: vi.fn(() => ({ data: null, status: "unauthenticated" })),
}));

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

async function renderPanel(): Promise<void> {
  renderWithIntl(
    <QueryClientProvider client={makeClient()}>
      <PortfolioPanel />
    </QueryClientProvider>,
  );
  // mount 시 발생하는 쿼리 resolve 를 flush (act 경고 회피) — tablist 자체는
  // 동기 렌더지만 첫 비동기 갱신을 기다린다.
  await screen.findByRole("tablist");
}

describe("PortfolioPanel WAI-ARIA tabs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listTransactions).mockResolvedValue([]);
    vi.mocked(listPositions).mockResolvedValue([]);
  });

  it("tablist + 2 tab, aria-selected / aria-controls / roving tabindex", async () => {
    await renderPanel();

    expect(screen.getByRole("tablist")).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(2);

    const txTab = screen.getByRole("tab", { name: "거래내역" });
    const posTab = screen.getByRole("tab", { name: "포지션" });

    // 초기 선택 = 거래내역.
    expect(txTab).toHaveAttribute("aria-selected", "true");
    expect(txTab).toHaveAttribute("tabindex", "0");
    expect(txTab).toHaveAttribute("aria-controls", "portfolio-panel-transactions");

    expect(posTab).toHaveAttribute("aria-selected", "false");
    expect(posTab).toHaveAttribute("tabindex", "-1");
  });

  it("tabpanel role + id + aria-labelledby (선택 탭과 연결)", async () => {
    await renderPanel();
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAttribute("id", "portfolio-panel-transactions");
    expect(panel).toHaveAttribute("aria-labelledby", "portfolio-tab-transactions");
  });

  it("ArrowRight → 다음 탭 활성화 + tabpanel 전환", async () => {
    await renderPanel();
    screen.getByRole("tab", { name: "거래내역" }).focus();

    await userEvent.keyboard("{ArrowRight}");

    expect(screen.getByRole("tab", { name: "포지션" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "id",
      "portfolio-panel-positions",
    );
  });

  it("ArrowLeft(첫 탭에서) → 마지막 탭으로 wrap", async () => {
    await renderPanel();
    screen.getByRole("tab", { name: "거래내역" }).focus();

    await userEvent.keyboard("{ArrowLeft}");

    expect(screen.getByRole("tab", { name: "포지션" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("Home/End → 처음/마지막 탭", async () => {
    await renderPanel();
    // 먼저 포지션(마지막)으로 이동.
    screen.getByRole("tab", { name: "거래내역" }).focus();
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "포지션" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // Home 으로 첫 탭 복귀.
    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "거래내역" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});
