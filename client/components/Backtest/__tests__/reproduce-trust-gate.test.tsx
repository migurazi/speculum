/**
 * 재현 검증 + 데이터 신선도 신뢰성 표시 게이트 — M5 #6 / ADR-0033 D2~D6.
 *
 * 검증:
 *   1. 재현 배지 톤 상수(BACKTEST_PANEL_REPRODUCE_*) 모두 neutral grayscale — 판단색 0.
 *   2. 재현 검증 버튼 클릭 → reproduceBacktest 호출.
 *   3. matches=true → "재현 일치" 배지 렌더 + neutral 톤.
 *   4. matches=false → "재현 불일치" 배지 렌더.
 *   5. packTampered=true → "pack 변조 감지" 배지 렌더.
 *   6. 데이터 신선도 패널 렌더 — KRX/DART 기준일 표시.
 *   7. isStale=true → "갱신 지연" neutral 배지 렌더.
 *   8. 배지 className 에 판단 의미색(red/green/amber 등) 없음.
 *   9. i18n 문자열에 금지 어휘(위험·부정확 등) 없음.
 *  10. 재현 문자열에 금지 어휘 없음.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import {
  BacktestPanel,
  BACKTEST_PANEL_REPRODUCE_MATCH,
  BACKTEST_PANEL_REPRODUCE_MISMATCH,
  BACKTEST_PANEL_PACK_TAMPERED,
  BACKTEST_PANEL_FRESHNESS_STALE,
  BACKTEST_PANEL_FRESHNESS_OK,
} from "@/components/Backtest/BacktestPanel";
import {
  runBacktest,
  reproduceBacktest,
  getDataFreshness,
} from "@/lib/api/backtest";
import type { BacktestResult, BacktestReproduceResult, DataFreshness } from "@/lib/api/backtest";
import { renderWithIntl } from "@/test-utils/intl";

// API mock — 톤 상수 등 다른 export 는 실제 모듈 유지.
vi.mock("@/lib/api/backtest", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/backtest")>();
  return {
    ...actual,
    runBacktest: vi.fn(),
    reproduceBacktest: vi.fn(),
    getDataFreshness: vi.fn(),
  };
});

// lightweight-charts mock — jsdom 환경.
vi.mock("lightweight-charts", () => {
  const mockSeries = { setData: () => undefined };
  const mockTimeScale = { fitContent: () => undefined };
  const mockChart = {
    addSeries: () => mockSeries,
    timeScale: () => mockTimeScale,
    applyOptions: () => undefined,
    remove: () => undefined,
  };
  return { createChart: () => mockChart, LineSeries: {} };
});

// ── 금지 색 토큰 ─────────────────────────────────────────────────────────────

const FORBIDDEN_SEMANTIC_COLOR_TOKENS: ReadonlyArray<string> = [
  "red", "rose", "blue", "sky", "indigo",
  "green", "emerald", "teal", "lime",
  "amber", "orange", "yellow",
  "purple", "violet", "fuchsia", "pink", "cyan",
];

/** 재현/신선도 배지 톤 상수 전체 목록. */
const REPRODUCE_TONE_CONSTANTS: ReadonlyArray<string> = [
  BACKTEST_PANEL_REPRODUCE_MATCH,
  BACKTEST_PANEL_REPRODUCE_MISMATCH,
  BACKTEST_PANEL_PACK_TAMPERED,
  BACKTEST_PANEL_FRESHNESS_STALE,
  BACKTEST_PANEL_FRESHNESS_OK,
];

/** 금지 어휘 — 판단·해석·위험 어휘. §2.2/§2.7 */
const FORBIDDEN_ADVISORY_WORDS: ReadonlyArray<string> = [
  "위험", "부정확", "믿을 수 없음", "신뢰 불가", "오류",
  "추천", "우수", "최고", "랭킹", "아웃퍼폼",
];

// ── Fixture ──────────────────────────────────────────────────────────────────

const MOCK_BACKTEST_RESULT: BacktestResult = {
  freeze: {
    resultHash: "sha256:abc123",
    packContentHash: "sha256:def456",
    packSlug: "speculum-builtin",
    packVersion: "1.0.0",
    krxBatchId: "20221231",
    dartBatchId: "20221231",
    rebalance: "quarterly",
    start: "2022-01-01",
    end: "2022-12-31",
    costAssumptions: { taxBps: "15", commissionBps: "1.5" },
    conditions: [{ factor: "per:ttm", op: ">", value: "0" }],
    dataVersions: { krx_batch_id: "20221231" },
  },
  stats: {
    cagr: "0.1",
    cumulativeReturn: "0.1",
    mdd: "-0.05",
    volatility: "0.12",
    turnover: "0.25",
  },
  equityCurve: [
    { date: "2022-03-31", value: "1.0" },
    { date: "2022-06-30", value: "1.05" },
  ],
  survivorshipComplete: true,
  missingPriceRatio: "0",
  disclaimerRequired: true,
};

const MOCK_REPRODUCE_MATCH: BacktestReproduceResult = {
  matches: true,
  reproduceNote: null,
  packTampered: false,
  equityCurve: [{ date: "2022-03-31", value: "1.0" }],
};

const MOCK_REPRODUCE_MISMATCH: BacktestReproduceResult = {
  matches: false,
  reproduceNote: "equity curve 불일치",
  packTampered: false,
  equityCurve: [{ date: "2022-03-31", value: "0.9" }],
};

const MOCK_REPRODUCE_TAMPERED: BacktestReproduceResult = {
  matches: false,
  reproduceNote: null,
  packTampered: true,
  equityCurve: [],
};

const MOCK_FRESHNESS_OK: DataFreshness = {
  krx: { source: "krx", latestBatchAt: "2026-06-01", isStale: false, elapsedDays: 3 },
  dart: { source: "dart", latestBatchAt: "2026-06-01", isStale: false, elapsedDays: 3 },
  kosis: { source: "kosis", latestBatchAt: "2026-05-15", isStale: false, elapsedDays: 20 },
  asOf: "2026-06-04T00:00:00Z",
};

const MOCK_FRESHNESS_STALE: DataFreshness = {
  krx: { source: "krx", latestBatchAt: "2026-05-20", isStale: true, elapsedDays: 15 },
  dart: { source: "dart", latestBatchAt: "2026-05-20", isStale: true, elapsedDays: 15 },
  kosis: { source: "kosis", latestBatchAt: "2026-04-01", isStale: true, elapsedDays: 64 },
  asOf: "2026-06-04T00:00:00Z",
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

/** 폼 채우기 + 실행 헬퍼 — BacktestResult 를 mock 해 결과 패널까지 진입. */
async function fillFormAndRun(result: BacktestResult): Promise<void> {
  vi.mocked(runBacktest).mockResolvedValue(result);
  renderPanel();

  const slugInput = screen.getByPlaceholderText(/user\/my-pack/i);
  await userEvent.clear(slugInput);
  await userEvent.type(slugInput, "user/test-pack");

  const factorInputs = screen.getAllByPlaceholderText(/factor canonical_id/i);
  await userEvent.type(factorInputs[0]!, "per:ttm");

  const valueInputs = screen.getAllByPlaceholderText("값");
  await userEvent.type(valueInputs[0]!, "15");

  await userEvent.click(screen.getByTestId("backtest-run-btn"));
  await screen.findByTestId("backtest-result-panel");
}

// ── 게이트 1: 배지 톤 상수 grayscale 검증 ─────────────────────────────────────

describe("재현·신선도 배지 톤 상수 게이트 (ADR-0033 D3)", () => {
  it("재현·신선도 배지 톤 상수는 모두 neutral/white 계열이다", () => {
    for (const tone of REPRODUCE_TONE_CONSTANTS) {
      expect(tone, `톤 상수 '${tone}' 가 neutral/white 이 아님`).toMatch(
        /neutral|white/,
      );
    }
  });

  it("재현·신선도 배지 톤 상수에 등락/판단 의미색이 없다", () => {
    for (const tone of REPRODUCE_TONE_CONSTANTS) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(
          tone,
          `톤 상수 '${tone}' 에 금지 색 토큰 '${token}' 가 있음`,
        ).not.toMatch(new RegExp(`-${token}-|^${token}-|^${token}$`));
      }
    }
  });
});

// ── 게이트 2: 재현 검증 버튼 + 결과 배지 ─────────────────────────────────────

describe("재현 검증 버튼 + 결과 배지 (ADR-0033 D2~D5)", () => {
  beforeEach(() => {
    vi.mocked(getDataFreshness).mockResolvedValue(MOCK_FRESHNESS_OK);
    vi.resetAllMocks();
    vi.mocked(getDataFreshness).mockResolvedValue(MOCK_FRESHNESS_OK);
  });

  it("결과 패널에 '재현 검증' 버튼이 있다", async () => {
    await fillFormAndRun(MOCK_BACKTEST_RESULT);
    expect(screen.getByTestId("backtest-reproduce-btn")).toBeTruthy();
    expect(screen.getByTestId("backtest-reproduce-btn").textContent).toMatch(/재현 검증/);
  });

  it("matches=true → '재현 일치' 배지가 렌더된다", async () => {
    vi.mocked(reproduceBacktest).mockResolvedValue(MOCK_REPRODUCE_MATCH);
    await fillFormAndRun(MOCK_BACKTEST_RESULT);

    await userEvent.click(screen.getByTestId("backtest-reproduce-btn"));

    const badge = await screen.findByTestId("backtest-reproduce-matches");
    expect(badge).toBeTruthy();
    expect(badge.textContent).toMatch(/재현 일치/);
  });

  it("matches=false → '재현 불일치' 배지가 렌더된다", async () => {
    vi.mocked(reproduceBacktest).mockResolvedValue(MOCK_REPRODUCE_MISMATCH);
    await fillFormAndRun(MOCK_BACKTEST_RESULT);

    await userEvent.click(screen.getByTestId("backtest-reproduce-btn"));

    const badge = await screen.findByTestId("backtest-reproduce-mismatch");
    expect(badge).toBeTruthy();
    expect(badge.textContent).toMatch(/재현 불일치/);
  });

  it("packTampered=true → 'pack 변조 감지' 배지가 렌더된다", async () => {
    vi.mocked(reproduceBacktest).mockResolvedValue(MOCK_REPRODUCE_TAMPERED);
    await fillFormAndRun(MOCK_BACKTEST_RESULT);

    await userEvent.click(screen.getByTestId("backtest-reproduce-btn"));

    const badge = await screen.findByTestId("backtest-pack-tampered");
    expect(badge).toBeTruthy();
    expect(badge.textContent).toMatch(/pack 변조 감지/);
  });

  it("배지 className 에 판단 의미색이 없다 (matches=true)", async () => {
    vi.mocked(reproduceBacktest).mockResolvedValue(MOCK_REPRODUCE_MATCH);
    await fillFormAndRun(MOCK_BACKTEST_RESULT);
    await userEvent.click(screen.getByTestId("backtest-reproduce-btn"));

    const badge = await screen.findByTestId("backtest-reproduce-matches");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(badge.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });

  it("배지 className 에 판단 의미색이 없다 (packTampered=true)", async () => {
    vi.mocked(reproduceBacktest).mockResolvedValue(MOCK_REPRODUCE_TAMPERED);
    await fillFormAndRun(MOCK_BACKTEST_RESULT);
    await userEvent.click(screen.getByTestId("backtest-reproduce-btn"));

    const badge = await screen.findByTestId("backtest-pack-tampered");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(badge.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });
});

// ── 게이트 3: 데이터 신선도 패널 ─────────────────────────────────────────────

describe("데이터 신선도 패널 (ADR-0033 D6)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("신선도 패널이 렌더된다 (isStale=false)", async () => {
    vi.mocked(getDataFreshness).mockResolvedValue(MOCK_FRESHNESS_OK);
    renderPanel();
    const panel = await screen.findByTestId("data-freshness-panel");
    expect(panel).toBeTruthy();
  });

  it("isStale=true 시 '갱신 지연' 배지가 렌더된다", async () => {
    vi.mocked(getDataFreshness).mockResolvedValue(MOCK_FRESHNESS_STALE);
    renderPanel();
    const staleKrx = await screen.findByTestId("freshness-stale-krx");
    expect(staleKrx).toBeTruthy();
    expect(staleKrx.textContent).toMatch(/갱신 지연/);
  });

  it("isStale=false 시 '갱신 지연' 배지가 없다", async () => {
    vi.mocked(getDataFreshness).mockResolvedValue(MOCK_FRESHNESS_OK);
    renderPanel();
    await screen.findByTestId("data-freshness-panel");
    await waitFor(() => {
      expect(screen.queryByTestId("freshness-stale-krx")).toBeNull();
    });
  });

  it("신선도 갱신 지연 배지 className 에 판단 의미색이 없다", async () => {
    vi.mocked(getDataFreshness).mockResolvedValue(MOCK_FRESHNESS_STALE);
    renderPanel();
    const staleKrx = await screen.findByTestId("freshness-stale-krx");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(staleKrx.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });

  it("신선도 패널 텍스트에 판단 어휘가 없다", async () => {
    vi.mocked(getDataFreshness).mockResolvedValue(MOCK_FRESHNESS_STALE);
    renderPanel();
    const panel = await screen.findByTestId("data-freshness-panel");
    const text = panel.textContent ?? "";
    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text, `신선도 패널에 금지 어휘 '${word}'`).not.toContain(word);
    }
  });

  it("KOSIS isStale=true 시 'freshness-stale-kosis' 배지가 렌더된다", async () => {
    vi.mocked(getDataFreshness).mockResolvedValue(MOCK_FRESHNESS_STALE);
    renderPanel();
    const staleKosis = await screen.findByTestId("freshness-stale-kosis");
    expect(staleKosis).toBeTruthy();
    expect(staleKosis.textContent).toMatch(/갱신 지연/);
  });

  it("KOSIS isStale=false 시 'freshness-ok-kosis' 배지가 렌더된다", async () => {
    vi.mocked(getDataFreshness).mockResolvedValue(MOCK_FRESHNESS_OK);
    renderPanel();
    const okKosis = await screen.findByTestId("freshness-ok-kosis");
    expect(okKosis).toBeTruthy();
  });

  it("KOSIS 신선도 배지 className 에 판단 의미색이 없다", async () => {
    vi.mocked(getDataFreshness).mockResolvedValue(MOCK_FRESHNESS_STALE);
    renderPanel();
    const staleKosis = await screen.findByTestId("freshness-stale-kosis");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(staleKosis.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });
});

// ── 게이트 4: 재현 결과 패널 금지 어휘 검증 ──────────────────────────────────

describe("재현 결과 패널 금지 어휘 검증 (§2.2/§2.7)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getDataFreshness).mockResolvedValue(MOCK_FRESHNESS_OK);
  });

  it("재현 결과 패널 텍스트에 판단·해석 어휘가 없다", async () => {
    vi.mocked(reproduceBacktest).mockResolvedValue(MOCK_REPRODUCE_MATCH);
    await fillFormAndRun(MOCK_BACKTEST_RESULT);
    await userEvent.click(screen.getByTestId("backtest-reproduce-btn"));

    const panel = await screen.findByTestId("backtest-reproduce-result");
    const text = panel.textContent ?? "";
    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text, `재현 결과에 금지 어휘 '${word}'`).not.toContain(word);
    }
  });

  it("재현 불일치 패널 텍스트에 판단·해석 어휘가 없다", async () => {
    vi.mocked(reproduceBacktest).mockResolvedValue(MOCK_REPRODUCE_MISMATCH);
    await fillFormAndRun(MOCK_BACKTEST_RESULT);
    await userEvent.click(screen.getByTestId("backtest-reproduce-btn"));

    const panel = await screen.findByTestId("backtest-reproduce-result");
    const text = panel.textContent ?? "";
    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text, `재현 불일치에 금지 어휘 '${word}'`).not.toContain(word);
    }
  });
});
