/**
 * 백테스트 결과 시각요소 게이트 — ADR-0027 D3/D4/D6 (No Advice / BacktestPanel).
 *
 * `no-forbidden-words`(eslint) / `check_forbidden_words.py` 는 **텍스트** 만 검사한다.
 * 결과 표시의 색·equity curve 색·디스클레이머 게이트·survivorship 경고 같은
 * **시각 요소** 는 그 검사로 포착되지 않으므로 별도 게이트가 필요.
 * custom-screen-visual-gate.test.tsx / chart-visual-gate.test.tsx 패턴 직계.
 *
 * 검증:
 *   1. BACKTEST_PANEL_* 톤 상수가 모두 grayscale(neutral) 계열 — 등락/판단색 0.
 *   2. equity curve 라인색(BACKTEST_PANEL_CURVE_LINE)이 grayscale hex — 수익/손실 색분기 0.
 *   3. 통계표에 "수익률 N%" headline 대형 강조·등급·평가 어휘 0.
 *   4. disclaimer_required=true 일 때 디스클레이머 텍스트가 렌더됨(D4 게이트).
 *   5. survivorship_complete=false 일 때 survivorship 경고 렌더됨(D3 디스클로저).
 *   6. pack 순위/비교/정렬 토글 UI 부재.
 *   7. 결과 패널에 추천/순위/평가 어휘 없음.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import {
  BacktestPanel,
  BACKTEST_PANEL_CURVE_LINE,
  BACKTEST_PANEL_CURVE_BG,
  BACKTEST_PANEL_STAT_TEXT,
  BACKTEST_PANEL_STAT_LABEL,
  BACKTEST_PANEL_STAT_ROW_BG,
  BACKTEST_PANEL_STAT_ROW_STRIPE,
  BACKTEST_PANEL_SURVIVORSHIP_WARNING,
  BACKTEST_PANEL_DISCLAIMER,
} from "@/components/Backtest/BacktestPanel";
import { runBacktest } from "@/lib/api/backtest";
import type { BacktestResult } from "@/lib/api/backtest";
import { renderWithIntl } from "@/test-utils/intl";

// runBacktest 만 mock — 톤 상수 등 다른 export 는 실제 모듈 유지.
vi.mock("@/lib/api/backtest", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/backtest")>();
  return { ...actual, runBacktest: vi.fn() };
});

// lightweight-charts mock — jsdom 에서 createChart 사용 불가.
// vi.mock 은 hoisting 됨 — factory 내부에서 vi.fn() 사용 가능.
vi.mock("lightweight-charts", () => {
  const mockSeries = {
    setData: () => undefined,
  };
  const mockTimeScale = {
    fitContent: () => undefined,
  };
  const mockChart = {
    addSeries: () => mockSeries,
    timeScale: () => mockTimeScale,
    applyOptions: () => undefined,
    remove: () => undefined,
  };
  return {
    createChart: () => mockChart,
    LineSeries: {},
  };
});

/**
 * 등락/판단 의미 색 토큰 — BACKTEST_PANEL 톤 상수에 등장 금지.
 * custom-screen-visual-gate.test.tsx 와 동일 목록.
 */
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

/**
 * equity curve 라인색에 금지된 색조 hex — 수익(녹색계) / 손실(적색계).
 * grayscale/neutral hex 만 허용.
 */
const FORBIDDEN_SEMANTIC_COLOR_HEXES: ReadonlyArray<string> = [
  "#16a34a", // 녹색 상승
  "#22c55e", // green-500
  "#dc2626", // 빨강 손실
  "#ef4444", // red-500
  "#2563eb", // 파랑
  "#3b82f6", // blue-500
];

/** BACKTEST_PANEL tailwind 톤 상수 전체 목록. */
const BACKTEST_PANEL_TAILWIND_CONSTANTS: ReadonlyArray<string> = [
  BACKTEST_PANEL_STAT_TEXT,
  BACKTEST_PANEL_STAT_LABEL,
  BACKTEST_PANEL_STAT_ROW_BG,
  BACKTEST_PANEL_STAT_ROW_STRIPE,
  BACKTEST_PANEL_SURVIVORSHIP_WARNING,
  BACKTEST_PANEL_DISCLAIMER,
];

/** No Advice 판단·추천·순위·평가 어휘 — 결과 렌더에 등장 금지. */
const FORBIDDEN_ADVISORY_WORDS: ReadonlyArray<string> = [
  "추천",
  "인기",
  "1위",
  "랭킹",
  "Top",
  "우수",
  "최고",
  "아웃퍼폼",
  "검증된 전략",
  "수익률 상위",
  "별점",
];

// ── fixture ─────────────────────────────────────────────────────────────────

/** disclaimer_required=true + survivorship_complete=true 기본 결과 fixture. */
const MOCK_RESULT_DISCLAIMER: BacktestResult = {
  freeze: {
    resultHash: "sha256:abc123",
    packContentHash: "sha256:def456",
    krxBatchId: "20221231",
    dartBatchId: "20221231",
    rebalance: "quarterly",
    start: "2022-01-01",
    end: "2022-12-31",
    costAssumptions: {
      taxBps: "15",
      commissionBps: "1.5",
    },
  },
  stats: {
    cagr: "0.331",
    cumulativeReturn: "0.31",
    mdd: "-0.05",
    volatility: "0.12",
    turnover: "0.25",
  },
  equityCurve: [
    { date: "2022-03-31", value: "0.99985" },
    { date: "2022-06-30", value: "1.02" },
  ],
  survivorshipComplete: true,
  missingPriceRatio: "0",
  disclaimerRequired: true,
};

/** survivorship_complete=false fixture. */
const MOCK_RESULT_SURVIVORSHIP: BacktestResult = {
  ...MOCK_RESULT_DISCLAIMER,
  survivorshipComplete: false,
  missingPriceRatio: "0.05",
  disclaimerRequired: true,
};

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
      <BacktestPanel />
    </QueryClientProvider>,
  );
}

/** 폼 채우기 + 실행 헬퍼. */
async function fillFormAndRun(result: BacktestResult): Promise<void> {
  vi.mocked(runBacktest).mockResolvedValue(result);
  renderPanel();

  // pack slug 입력.
  const slugInput = screen.getByPlaceholderText(/user\/my-pack/i);
  await userEvent.clear(slugInput);
  await userEvent.type(slugInput, "user/test-pack");

  // 조건 factor 입력.
  const factorInputs = screen.getAllByPlaceholderText(/factor canonical_id/i);
  await userEvent.type(factorInputs[0]!, "per:ttm");

  // 조건 값 입력.
  const valueInputs = screen.getAllByPlaceholderText("값");
  await userEvent.type(valueInputs[0]!, "15");

  // 실행.
  await userEvent.click(screen.getByTestId("backtest-run-btn"));

  // 결과 대기.
  await screen.findByTestId("backtest-result-panel");
}

// ── 게이트 1: 톤 상수 grayscale 검증 ─────────────────────────────────────────

describe("BacktestPanel 톤 상수 게이트 (ADR-0027 D4)", () => {
  it("BACKTEST_PANEL tailwind 톤 상수는 모두 neutral/white 계열이다", () => {
    for (const tone of BACKTEST_PANEL_TAILWIND_CONSTANTS) {
      expect(tone, `톤 상수 '${tone}' 가 neutral/white 이 아님`).toMatch(
        /neutral|white/,
      );
    }
  });

  it("BACKTEST_PANEL tailwind 톤 상수에 등락/판단 의미색이 없다", () => {
    for (const tone of BACKTEST_PANEL_TAILWIND_CONSTANTS) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(
          tone,
          `톤 상수 '${tone}' 에 금지 색 토큰 '${token}' 가 있음`,
        ).not.toMatch(new RegExp(`-${token}-|^${token}-|^${token}$`));
      }
    }
  });

  it("equity curve 라인색(BACKTEST_PANEL_CURVE_LINE)이 grayscale hex 이다", () => {
    // grayscale hex: r==g==b (e.g., #525252).
    const hex = BACKTEST_PANEL_CURVE_LINE.replace("#", "");
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    expect(r).toBe(g);
    expect(g).toBe(b);
  });

  it("equity curve 라인색이 수익(녹색)/손실(적색) 계열이 아니다", () => {
    const lineLower = BACKTEST_PANEL_CURVE_LINE.toLowerCase();
    for (const hex of FORBIDDEN_SEMANTIC_COLOR_HEXES) {
      expect(lineLower, `equity curve 라인색 '${lineLower}' 이 금지 색 '${hex}'`).not.toBe(
        hex.toLowerCase(),
      );
    }
  });

  it("equity curve 배경색(BACKTEST_PANEL_CURVE_BG)이 neutral 계열이다", () => {
    // white 또는 grayscale hex 허용.
    expect(BACKTEST_PANEL_CURVE_BG.toLowerCase()).toMatch(/^#(f+|[e-f]{6}|ffffff)$/i);
  });
});

// ── 게이트 2: disclaimer_required=true 시 디스클레이머 렌더 ─────────────────

describe("BacktestPanel 디스클레이머 게이트 (ADR-0027 D4)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("disclaimer_required=true 일 때 과거성과 디스클레이머 텍스트가 렌더된다", async () => {
    await fillFormAndRun(MOCK_RESULT_DISCLAIMER);

    const disclaimer = screen.getByTestId("backtest-disclaimer");
    expect(disclaimer).toBeTruthy();
    // 핵심 키워드 포함 확인.
    expect(disclaimer.textContent).toMatch(/과거 성과/);
    expect(disclaimer.textContent).toMatch(/미래 수익/);
  });

  it("결과 패널과 디스클레이머가 함께 렌더된다 (게이트 — 분리 불가)", async () => {
    await fillFormAndRun(MOCK_RESULT_DISCLAIMER);

    // 결과 패널과 디스클레이머 모두 존재해야 함.
    expect(screen.getByTestId("backtest-result-panel")).toBeTruthy();
    expect(screen.getByTestId("backtest-disclaimer")).toBeTruthy();
  });
});

// ── 게이트 3: survivorship_complete=false 시 경고 렌더 ──────────────────────

describe("BacktestPanel survivorship 디스클로저 게이트 (ADR-0027 D3)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("survivorship_complete=false 일 때 survivorship 경고 박스가 렌더된다", async () => {
    await fillFormAndRun(MOCK_RESULT_SURVIVORSHIP);

    const warning = screen.getByTestId("backtest-survivorship-warning");
    expect(warning).toBeTruthy();
    // survivorship bias 경고 텍스트 포함.
    expect(warning.textContent).toMatch(/survivorship bias/i);
    expect(warning.textContent).toMatch(/missing_price_ratio/);
  });

  it("survivorship 경고 박스 className 에 판단 의미색(red 등)이 없다", async () => {
    await fillFormAndRun(MOCK_RESULT_SURVIVORSHIP);

    const warning = screen.getByTestId("backtest-survivorship-warning");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      // red 는 특히 금지 (ADR-0027 D3: red 색 금지, neutral 톤).
      expect(warning.className, `survivorship 경고에 금지 색 '${token}'`).not.toMatch(
        new RegExp(`-${token}-`),
      );
    }
  });

  it("survivorship_complete=true 일 때 경고 박스가 없다", async () => {
    await fillFormAndRun(MOCK_RESULT_DISCLAIMER);

    expect(screen.queryByTestId("backtest-survivorship-warning")).toBeNull();
  });
});

// ── 게이트 4: 통계표 평가·등급·순위 어휘 없음 ───────────────────────────────

describe("BacktestPanel 통계표 게이트 — 평가 0 (ADR-0027 D6)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("결과 패널 전체에 추천/순위/평가 어휘가 없다", async () => {
    await fillFormAndRun(MOCK_RESULT_DISCLAIMER);

    const panel = screen.getByTestId("backtest-result-panel");
    const text = panel.textContent ?? "";

    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text, `패널에 금지 어휘 '${word}' 가 있음`).not.toContain(word);
    }
  });

  it("통계표에 순위/랭킹/Top-N 배지가 없다", async () => {
    await fillFormAndRun(MOCK_RESULT_DISCLAIMER);

    const panel = screen.getByTestId("backtest-result-panel");
    expect(within(panel).queryByText(/1위|순위|랭킹|Top|#1/i)).toBeNull();
  });

  it("통계표에 별점/등급/'우수'/'양호' 라벨이 없다", async () => {
    await fillFormAndRun(MOCK_RESULT_DISCLAIMER);

    const panel = screen.getByTestId("backtest-result-panel");
    const text = panel.textContent ?? "";
    expect(text).not.toMatch(/별점|★|☆|등급|우수|양호|최고|최저/);
  });

  it("통계표는 CAGR·누적수익률·MDD·변동성·회전율 5개 통계를 표시한다", async () => {
    await fillFormAndRun(MOCK_RESULT_DISCLAIMER);

    const statsTable = screen.getByTestId("backtest-stats-table");
    expect(statsTable.textContent).toMatch(/CAGR|누적수익률/);
    expect(statsTable.textContent).toMatch(/MDD/);
    expect(statsTable.textContent).toMatch(/변동성/);
    expect(statsTable.textContent).toMatch(/회전율/);
  });
});

// ── 게이트 5: pack 순위/비교 UI 없음 ─────────────────────────────────────────

describe("BacktestPanel 순위/비교 UI 부재 게이트 (ADR-0027 D4-c)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("BacktestPanel 에 pack 비교/순위/정렬 토글 UI 가 없다", () => {
    renderPanel();

    const panel = screen.getByTestId("backtest-panel");
    const text = panel.textContent ?? "";

    // 순위/비교/정렬 관련 어휘.
    expect(text).not.toMatch(/비교|순위 보기|정렬|랭킹|순위표/);
    // 비교용 select/toggle 없음.
    expect(within(panel).queryByText(/비교 pack|pack 비교/i)).toBeNull();
  });

  it("실행 버튼 className 에 판단 의미색이 없다", () => {
    renderPanel();

    const runBtn = screen.getByTestId("backtest-run-btn");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(runBtn.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });
});

// ── 게이트 6: 거래비용 가정 명시 표시 (D2) ───────────────────────────────────

describe("BacktestPanel 거래비용 가정 명시 게이트 (ADR-0027 D2)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("결과에 적용된 거래비용(위탁수수료·증권거래세) 가정이 표시된다", async () => {
    await fillFormAndRun(MOCK_RESULT_DISCLAIMER);

    const costBox = screen.getByTestId("backtest-cost-assumptions");
    expect(costBox).toBeTruthy();
    // 수치 표시 확인.
    expect(costBox.textContent).toContain("1.5");
    expect(costBox.textContent).toContain("15");
    expect(costBox.textContent).toMatch(/bps/);
  });
});
