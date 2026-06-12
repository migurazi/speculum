/**
 * Portfolio 시각요소 게이트 — ADR-0029 D3 (회계≠평가 분리 / No Advice).
 *
 * `no-forbidden-words`(eslint) / `check_forbidden_words.py` 는 **텍스트** 만 검사한다.
 * 포지션 표의 색·손익 강조·평가 라벨 같은 **시각 요소** 는 그 검사로 포착되지 않으므로
 * 별도 게이트가 필요(custom-screen-visual-gate.test.tsx 패턴 복제).
 *
 * 검증:
 *   1. PORTFOLIO_PANEL 톤 상수가 모두 grayscale(neutral/white) 계열 — 등락색 토큰 부재.
 *   2. 손익 셀 (realized/unrealized) className 에 판단색 없음 — 양수/음수 무관 동일 톤.
 *   3. 포지션 표에 "수익/손실/양호/수익률" 평가 어휘·등급·순위·Top-N 부재.
 *   4. 손익 순위 정렬 토글 UI 부재 (default 정렬 = code_lineage_id asc 결정적).
 *   5. 세금 필드 부재 — 세전 표시만 (ADR-0029 D6).
 *   6. 행 순서 = backend 반환 순서 유지 (자동 정렬 금지).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import {
  PortfolioPanel,
  PORTFOLIO_PANEL_CELL_TEXT,
  PORTFOLIO_PANEL_HEADER_TEXT,
  PORTFOLIO_PANEL_ROW_BG,
  PORTFOLIO_PANEL_ROW_STRIPE,
  PORTFOLIO_PANEL_PNL_TEXT,
} from "@/components/Portfolio/PortfolioPanel";
import {
  listTransactions,
  listPositions,
} from "@/lib/api/portfolio";
import type { PortfolioPosition, PortfolioTransaction } from "@/lib/api/portfolio";
import { renderWithIntl } from "@/test-utils/intl";

// API mock — 톤 상수 등 다른 export 는 실제 모듈 유지.
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

// next-auth/react — signIn mock.
vi.mock("next-auth/react", () => ({
  signIn: vi.fn(),
  useSession: vi.fn(() => ({ data: null, status: "unauthenticated" })),
}));

// =============================================================================
// 등락/판단 의미 색 토큰 목록 — custom-screen-visual-gate 와 동일 목록.
// =============================================================================

const FORBIDDEN_SEMANTIC_COLOR_TOKENS: ReadonlyArray<string> = [
  "red",
  "rose",
  "blue",
  "sky",
  "indigo",
  "green",
  "emerald",
  "teal",
  "lime",
  "amber",
  "orange",
  "yellow",
  "purple",
  "violet",
  "fuchsia",
  "pink",
  "cyan",
];

// =============================================================================
// PORTFOLIO_PANEL 톤 상수 전체
// =============================================================================

const PORTFOLIO_PANEL_TONE_CONSTANTS: ReadonlyArray<string> = [
  PORTFOLIO_PANEL_CELL_TEXT,
  PORTFOLIO_PANEL_HEADER_TEXT,
  PORTFOLIO_PANEL_ROW_BG,
  PORTFOLIO_PANEL_ROW_STRIPE,
  PORTFOLIO_PANEL_PNL_TEXT,
];

// =============================================================================
// 평가 어휘 — 포지션 표에 등장 금지 (ADR-0029 D3)
// =============================================================================

const FORBIDDEN_ADVISORY_WORDS: ReadonlyArray<string> = [
  "수익 중",
  "손실 중",
  "양호",
  "수익률",
  "등급",
  "랭킹",
  "순위",
  "1위",
  "Top",
  "상위",
  "우수",
  "최고",
  "추천",
];

// =============================================================================
// fixture
// =============================================================================

const MOCK_TRANSACTIONS: PortfolioTransaction[] = [
  {
    id: "tx-001",
    codeLineageId: "aaa-lineage-id",
    side: "buy",
    quantity: 100,
    unitPrice: "70000",
    tradeDate: "2026-01-10",
    fee: "350",
    createdAt: "2026-01-10T09:00:00Z",
  },
  {
    id: "tx-002",
    codeLineageId: "bbb-lineage-id",
    side: "buy",
    quantity: 50,
    unitPrice: "50000",
    tradeDate: "2026-01-12",
    fee: "175",
    createdAt: "2026-01-12T09:00:00Z",
  },
];

/**
 * 포지션 fixture: aaa → bbb 순 (code_lineage_id asc).
 * 손익 양수/음수 혼재 — 색 분기 게이트 검증용.
 */
const MOCK_POSITIONS: PortfolioPosition[] = [
  {
    codeLineageId: "aaa-lineage-id",
    quantity: 100,
    avgCost: "70000",
    totalCost: "7000000",
    realizedPnl: "0",           // 0 — 판단 없음
    marketValue: "7500000",
    unrealizedPnl: "500000",    // 양수
  },
  {
    codeLineageId: "bbb-lineage-id",
    quantity: 50,
    avgCost: "50000",
    totalCost: "2500000",
    realizedPnl: "-50000",      // 음수 — 색 분기 금지 검증
    marketValue: "2400000",
    unrealizedPnl: "-100000",   // 음수
  },
];

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderPanel(): void {
  renderWithIntl(
    <QueryClientProvider client={makeClient()}>
      <PortfolioPanel />
    </QueryClientProvider>,
  );
}

async function renderAndSwitchToPositions(): Promise<void> {
  vi.mocked(listTransactions).mockResolvedValue(MOCK_TRANSACTIONS);
  vi.mocked(listPositions).mockResolvedValue(MOCK_POSITIONS);
  renderPanel();
  // 포지션 탭으로 전환.
  await userEvent.click(screen.getByText("포지션"));
  // 포지션 표 대기.
  await screen.findByTestId("portfolio-positions-table");
}

// =============================================================================
// 게이트 1: 톤 상수 grayscale 검증
// =============================================================================

describe("Portfolio 톤 상수 게이트 (ADR-0029 D3)", () => {
  it("PORTFOLIO_PANEL 톤 상수는 모두 grayscale(neutral/white) 계열이다", () => {
    for (const tone of PORTFOLIO_PANEL_TONE_CONSTANTS) {
      expect(tone, `톤 상수 '${tone}' 가 neutral/white 아님`).toMatch(
        /neutral|white/,
      );
    }
  });

  it("PORTFOLIO_PANEL 톤 상수에 등락/판단 의미색이 없다", () => {
    for (const tone of PORTFOLIO_PANEL_TONE_CONSTANTS) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(
          tone,
          `톤 상수 '${tone}' 에 금지 색 토큰 '${token}' 가 있음`,
        ).not.toMatch(new RegExp(`-${token}-`));
      }
    }
  });

  it("손익 전용 상수(PORTFOLIO_PANEL_PNL_TEXT)가 셀 기본 상수와 동일 톤이다", () => {
    // 손익 셀이 다른 셀과 동일 비중/톤 — 별도 강조 색 금지.
    expect(PORTFOLIO_PANEL_PNL_TEXT).toBe(PORTFOLIO_PANEL_CELL_TEXT);
  });
});

// =============================================================================
// 게이트 2: 포지션 표 평가 어휘 없음
// =============================================================================

describe("Portfolio 포지션 표 렌더 게이트 — 평가 어휘 0 (ADR-0029 D3)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("포지션 표에 평가 어휘·등급·순위가 없다", async () => {
    await renderAndSwitchToPositions();

    const table = screen.getByTestId("portfolio-positions-table");
    const text = table.textContent ?? "";

    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text, `포지션 표에 금지 어휘 '${word}' 가 있음`).not.toContain(
        word,
      );
    }
  });

  it("포지션 표에 순위/랭킹/Top-N 배지가 없다", async () => {
    await renderAndSwitchToPositions();

    const table = screen.getByTestId("portfolio-positions-table");
    expect(within(table).queryByText(/1위|순위|랭킹|Top|#1/i)).toBeNull();
  });
});

// =============================================================================
// 게이트 3: 손익 셀 className 에 판단색 없음 — 양수/음수 무관 동일 톤
// =============================================================================

describe("Portfolio 손익 셀 className 게이트 (ADR-0029 D3)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("realizedPnl 셀 className 에 판단 의미색이 없다", async () => {
    await renderAndSwitchToPositions();

    const pnlCells = screen.getAllByTestId("portfolio-realized-pnl-cell");
    expect(pnlCells.length).toBeGreaterThan(0);

    for (const cell of pnlCells) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(
          cell.className,
          `realizedPnl 셀 className '${cell.className}' 에 금지 색 '${token}' 가 있음`,
        ).not.toMatch(new RegExp(`-${token}-`));
      }
    }
  });

  it("unrealizedPnl 셀 className 에 판단 의미색이 없다", async () => {
    await renderAndSwitchToPositions();

    const pnlCells = screen.getAllByTestId("portfolio-unrealized-pnl-cell");
    expect(pnlCells.length).toBeGreaterThan(0);

    for (const cell of pnlCells) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(
          cell.className,
          `unrealizedPnl 셀 className '${cell.className}' 에 금지 색 '${token}' 가 있음`,
        ).not.toMatch(new RegExp(`-${token}-`));
      }
    }
  });

  it("양수 손익 행과 음수 손익 행의 셀 className 이 동일하다 (색 분기 0)", async () => {
    await renderAndSwitchToPositions();

    // MOCK_POSITIONS: 첫 번째 unrealizedPnl = 양수, 두 번째 = 음수.
    const unrealizedCells = screen.getAllByTestId("portfolio-unrealized-pnl-cell");
    expect(unrealizedCells.length).toBe(2);

    const positiveCell = unrealizedCells[0]!;
    const negativeCell = unrealizedCells[1]!;

    // 양수/음수 셀의 className 이 동일해야 함 — 색 분기 없음.
    expect(positiveCell.className).toBe(negativeCell.className);
  });
});

// =============================================================================
// 게이트 4: 손익 순위 정렬 토글 UI 부재
// =============================================================================

describe("Portfolio 포지션 표 렌더 게이트 — 손익 정렬 토글 0 (ADR-0029 D3)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("포지션 표에 손익 정렬 토글 버튼이 없다", async () => {
    await renderAndSwitchToPositions();

    const table = screen.getByTestId("portfolio-positions-table");
    // 정렬 토글이 있다면 data-sort 속성이나 "정렬"/"오름차순"/"내림차순" 텍스트가 있을 것.
    expect(within(table).queryByRole("button", { name: /정렬|오름차순|내림차순|sort/i })).toBeNull();
    expect(within(table).queryByText(/오름차순|내림차순/i)).toBeNull();
  });

  it("포지션 표의 행 순서가 backend 반환 순서 그대로다 (자동 정렬 금지)", async () => {
    await renderAndSwitchToPositions();

    const table = screen.getByTestId("portfolio-positions-table");
    const rows = within(table).getAllByRole("row").slice(1); // 헤더 제외.

    // MOCK_POSITIONS: aaa → bbb 순.
    expect(rows[0]?.textContent).toContain("aaa-lineage-id");
    expect(rows[1]?.textContent).toContain("bbb-lineage-id");
  });
});

// =============================================================================
// 게이트 5: 세금 필드 부재 (ADR-0029 D6)
// =============================================================================

describe("Portfolio 포지션 표 렌더 게이트 — 세금 필드 부재 (ADR-0029 D6)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("포지션 표 헤더에 세금 관련 컬럼이 없다", async () => {
    await renderAndSwitchToPositions();

    const table = screen.getByTestId("portfolio-positions-table");
    const headerRow = within(table).getAllByRole("row")[0];

    expect(headerRow?.textContent).not.toMatch(/세금|tax|세후|after.?tax/i);
  });

  it("포지션 표 전체에 세금 계산 수치 컬럼·필드가 없다", async () => {
    await renderAndSwitchToPositions();

    const table = screen.getByTestId("portfolio-positions-table");
    // "세금 계산 미포함" 안내 텍스트는 허용. 세후/양도세/금융소득 수치 필드는 금지.
    expect(within(table).queryByText(/세후|양도세|금융소득|after.?tax/i)).toBeNull();
    // 컬럼 헤더에 세금 관련 항목 없음.
    const headers = within(table).getAllByRole("columnheader");
    for (const header of headers) {
      expect(header.textContent ?? "").not.toMatch(/세금|세후|양도세|금융소득|tax/i);
    }
  });
});

// =============================================================================
// 게이트 6: 거래내역 탭 — 거래 입력 버튼 className 에 판단색 없음
// =============================================================================

describe("Portfolio 거래 입력 버튼 className 게이트 (ADR-0029 D3)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("거래 입력 버튼의 className 에 판단 의미색이 없다", async () => {
    vi.mocked(listTransactions).mockResolvedValue([]);
    renderPanel();

    const addBtn = screen.getByTestId("portfolio-add-tx-btn");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(addBtn.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });
});
