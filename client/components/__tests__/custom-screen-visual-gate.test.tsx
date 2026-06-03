/**
 * Custom Screen 결과 시각요소 게이트 — ADR-0025 D6 (No Advice / PackRegistry Phase 2d).
 *
 * `no-forbidden-words`(eslint) / `check_forbidden_words.py` 는 **텍스트** 만 검사한다.
 * 결과 표시의 색·랭킹·컬러맵 같은 **시각 요소** 는 그 검사로 포착되지 않으므로
 * 별도 게이트가 필요(lab-visual-gate.test.tsx 패턴 복제). 본 테스트는 CustomScreenPanel
 * 의 시각 정책 불변식을 회귀 방지로 박는다.
 *
 * 검증:
 *   1. 결과 표시 톤 상수가 grayscale(neutral) 계열만 — 값의 부호/크기로 색 분기 없음.
 *   2. 등락/판단 의미색(한국 빨강/파랑, 녹색, heatmap 색조)이 톤 상수 어디에도 없음.
 *   3. 렌더 출력에 랭킹/순위/Top-N 배지 부재 + 행 순서 = 결정적(자동 정렬 금지).
 *   4. 결과 패널에 값/점수/composite score 자동 강조 없음.
 *   5. 추천 어휘(추천, 인기, 순위, 상위, 우수, 최고) 0.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import {
  CustomScreenPanel,
  CUSTOM_SCREEN_RESULT_TEXT,
  CUSTOM_SCREEN_RESULT_HEADER,
  CUSTOM_SCREEN_RESULT_ROW_BG,
  CUSTOM_SCREEN_RESULT_ROW_STRIPE,
} from "@/components/Lab/CustomScreenPanel";
import { customScreen } from "@/lib/api/custom-screen";
import type { FactorDef } from "@/lib/api/factor-packs";
import type { ScreenResult } from "@/lib/api/screen";
import { renderWithIntl } from "@/test-utils/intl";

// customScreen 만 mock — 톤 상수 등 다른 export 는 실제 모듈 유지.
vi.mock("@/lib/api/custom-screen", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/api/custom-screen")>();
  return { ...actual, customScreen: vi.fn(), saveCustomRun: vi.fn() };
});

// next-auth/react — signIn 만 mock (로그인 유도 테스트용).
vi.mock("next-auth/react", () => ({
  signIn: vi.fn(),
  useSession: vi.fn(() => ({ data: null, status: "unauthenticated" })),
}));

/**
 * 등락/판단 의미 색 토큰 — Custom Screen 톤 상수에 등장 금지.
 * lab-visual-gate.test.tsx 와 동일 목록.
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

/** Custom Screen 결과 표시 톤 상수 전체. */
const CUSTOM_SCREEN_TONE_CONSTANTS: ReadonlyArray<string> = [
  CUSTOM_SCREEN_RESULT_TEXT,
  CUSTOM_SCREEN_RESULT_HEADER,
  CUSTOM_SCREEN_RESULT_ROW_BG,
  CUSTOM_SCREEN_RESULT_ROW_STRIPE,
];

/** No Advice 판단·추천·순위 어휘 — 결과 렌더에 등장 금지. */
const FORBIDDEN_ADVISORY_WORDS: ReadonlyArray<string> = [
  "추천",
  "인기",
  "순위",
  "1위",
  "랭킹",
  "Top",
  "상위",
  "우수",
  "최고",
];

// ── fixture ─────────────────────────────────────────────────────────────────

const MOCK_FACTORS: FactorDef[] = [
  {
    canonical_id: "custom:per",
    uuid: "aaaaaaaa-0000-4000-8000-000000000001",
    name: "PER",
    description: "",
    unit: "ratio",
    tags: [],
    formula: { ast: null, inputs: [] },
  },
  {
    canonical_id: "custom:roe",
    uuid: "aaaaaaaa-0000-4000-8000-000000000002",
    name: "ROE",
    description: "",
    unit: "ratio",
    tags: [],
    formula: { ast: null, inputs: [] },
  },
];

/** result_codes 순서: 035420 → 005930 → 000660 (정렬되지 않은 입력 반환). */
const MOCK_RESULT: ScreenResult = {
  result_codes: ["035420", "005930", "000660"],
  total: 3,
  data_versions: {},
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
      <CustomScreenPanel
        packSlug="user/test-pack"
        version="0.1.0"
        factors={MOCK_FACTORS}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

async function renderAndRun(): Promise<void> {
  vi.mocked(customScreen).mockResolvedValue(MOCK_RESULT);
  renderPanel();

  // factor 조건 설정 — 첫 번째 factor 선택.
  const factorSelects = screen.getAllByRole("combobox");
  // 첫 factor select.
  await userEvent.selectOptions(factorSelects[0]!, "custom:per");

  // 값 입력.
  const valueInputs = screen.getAllByPlaceholderText("값");
  await userEvent.type(valueInputs[0]!, "10");

  // 실행.
  await userEvent.click(screen.getByTestId("custom-screen-run-btn"));

  // 결과 대기.
  await screen.findByTestId("custom-screen-result-summary");
}

// ── 게이트 1: 톤 상수 grayscale 검증 ─────────────────────────────────────────

describe("Custom Screen 톤 상수 게이트 (ADR-0025 D6)", () => {
  it("Custom Screen 결과 톤 상수는 모두 grayscale(neutral) 계열이다", () => {
    for (const tone of CUSTOM_SCREEN_TONE_CONSTANTS) {
      expect(tone, `톤 상수 '${tone}' 가 neutral 이 아님`).toMatch(/neutral|white/);
    }
  });

  it("Custom Screen 톤 상수에 등락/판단 의미색이 없다", () => {
    for (const tone of CUSTOM_SCREEN_TONE_CONSTANTS) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(
          tone,
          `톤 상수 '${tone}' 에 금지 색 토큰 '${token}' 가 있음`,
        ).not.toMatch(new RegExp(`-${token}-`));
      }
    }
  });
});

// ── 게이트 2: 렌더 결과 랭킹·추천 어휘 없음 ──────────────────────────────────

describe("Custom Screen 렌더 게이트 — 추천 어휘 0 (ADR-0025 D6)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("결과 패널에 추천/순위/랭킹 어휘가 없다", async () => {
    await renderAndRun();

    const panel = screen.getByTestId("custom-screen-panel");
    const text = panel.textContent ?? "";

    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text, `패널에 금지 어휘 '${word}' 가 있음`).not.toContain(word);
    }
  });

  it("결과 패널에 순위/랭킹/Top-N 배지가 없다", async () => {
    await renderAndRun();

    const panel = screen.getByTestId("custom-screen-panel");
    expect(within(panel).queryByText(/1위|순위|랭킹|Top|#1/i)).toBeNull();
  });
});

// ── 게이트 3: 행 순서 결정적(자동 정렬 금지) ─────────────────────────────────

describe("Custom Screen 렌더 게이트 — 행 순서 결정적 (ADR-0025 D6)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("result_codes 의 순서를 그대로 유지한다 (자동 정렬 금지)", async () => {
    await renderAndRun();

    // ResultsTable 은 코드만 표시(단일 column).
    const rows = screen
      .getAllByRole("row")
      .filter((row) => row.textContent?.match(/^\d{6}$/));
    const codes = rows.map((r) => r.textContent?.trim());

    // 값 크기로 정렬됐다면 005930 이 먼저여야 하지만, backend 순서 유지.
    expect(codes).toEqual(["035420", "005930", "000660"]);
  });
});

// ── 게이트 4: 값/점수 자동 강조 없음 ────────────────────────────────────────────

describe("Custom Screen 렌더 게이트 — 점수 자동강조 0 (ADR-0025 D6)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("결과 패널의 결과 요약에 점수·수치·강조 표시가 없다", async () => {
    await renderAndRun();

    // 결과 요약은 종목 수 + 기준 일자 사실만.
    const summary = screen.getByTestId("custom-screen-result-summary");
    // 점수/랭킹 관련 키워드 없음.
    expect(summary.textContent).not.toMatch(/점수|score|상위|하위|1위/i);
  });

  it("결과 패널 전체에 composite score / factor 값이 노출되지 않는다", async () => {
    await renderAndRun();

    // CustomScreenPanel 은 result_codes(boolean 필터 결과)만 표시.
    // 값/점수/composite factor 값은 일절 없어야 함(ResultsTable 은 코드만).
    const panel = screen.getByTestId("custom-screen-panel");
    // 숫자 값(factor value) 이 아닌 종목코드 형태(6자리)만 허용.
    const numericValues = panel.querySelectorAll("[data-score], [data-value]");
    expect(numericValues.length).toBe(0);
  });
});

// ── 게이트 5: 실행 버튼·저장 버튼 className 에 판단색 없음 ───────────────────

describe("Custom Screen 버튼 className 게이트 (ADR-0025 D6)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("실행 버튼의 className 에 판단 의미색이 없다", () => {
    vi.mocked(customScreen).mockResolvedValue(MOCK_RESULT);
    renderPanel();

    const runBtn = screen.getByTestId("custom-screen-run-btn");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(runBtn.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });

  it("실행 후 저장 버튼의 className 에 판단 의미색이 없다", async () => {
    await renderAndRun();

    await waitFor(() => {
      expect(screen.queryByTestId("custom-screen-save-run-btn")).not.toBeNull();
    });

    const saveBtn = screen.getByTestId("custom-screen-save-run-btn");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(saveBtn.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });
});
