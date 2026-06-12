/**
 * 소표본 디스클로저 시각 게이트 — ADR-0024 Phase 2 (§2.5 / §2.2 / §2.7).
 *
 * 검증:
 *   1. small_sample=true + sampleSize 있으면 디스클로저 표시(값은 보존).
 *   2. small_sample=false 이면 디스클로저 미표시.
 *   3. sampleSize=null(종목-국소) 이면 디스클로저 미표시.
 *   4. n==1 인 percentile=100 도 small_sample=true → 디스클로저 표시
 *      ("최상위" 오인 차단 — ADR-0024 D3).
 *   5. 디스클로저 배지에 등락/판단 의미색(빨강·파랑·녹색 등) 없음 — neutral 만.
 *   6. "신뢰불가/부정확/무시" 가치 어휘 부재(No Advice §2.2).
 *   7. 소표본이어도 값 자체는 항상 표시(숨기거나 N/A 처리 금지 — 값 보존).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  EvaluatePreview,
  EVAL_SMALL_SAMPLE_BG,
  EVAL_SMALL_SAMPLE_TEXT,
} from "@/components/Lab/EvaluatePreview";
import {
  evaluateFactorPack,
  type EvaluateResult,
  type FactorPack,
} from "@/lib/api/factor-packs";
import { renderWithIntl } from "@/test-utils/intl";

vi.mock("@/lib/api/factor-packs", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/api/factor-packs")>();
  return { ...actual, evaluateFactorPack: vi.fn() };
});

/**
 * 등락/판단 의미색 토큰 — 소표본 디스클로저 배지에 절대 등장 금지
 * (ADR-0024 D3 / ADR-0007 D2.2 grayscale 정신).
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
 * No Advice §2.2 / ADR-0024 D3 — 가치·지시 어휘.
 * 디스클로저 텍스트에 이 단어가 포함되면 규정 위반.
 */
const FORBIDDEN_VALUE_WORDS: ReadonlyArray<string> = [
  "신뢰불가",
  "신뢰할 수 없",
  "부정확",
  "무시",
  "틀렸",
  "사용불가",
  "비신뢰",
];

const MOCK_PACK: FactorPack = {
  pack_slug: "user/test-pack",
  version: "0.1.0",
  factors: [],
  citation: { title: "t" },
};

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

async function renderWith(result: EvaluateResult): Promise<void> {
  vi.mocked(evaluateFactorPack).mockResolvedValue(result);
  renderWithIntl(
    <QueryClientProvider client={makeClient()}>
      <EvaluatePreview pack={MOCK_PACK} asOf="2026-06-01" />
    </QueryClientProvider>,
  );
  await userEvent.type(
    screen.getByPlaceholderText("005930, 000660, 035420"),
    "005930",
  );
  await userEvent.click(screen.getByRole("button", { name: "평가" }));
  await screen.findByText("005930");
}

/** small_sample=true 결과 — 유니버스-상대 percentile, sampleSize=5. */
const SMALL_SAMPLE_RESULT: EvaluateResult = {
  valid: true,
  issues: [],
  results: [
    {
      code: "005930",
      factors: [
        {
          canonicalId: "per:pct",
          name: "PER pct",
          unit: "percent",
          value: "0.75",
          isNa: false,
          naReason: null,
          sampleSize: 5,
          smallSample: true,
        },
      ],
    },
  ],
};

/** small_sample=false 결과 — 충분한 모집단, 디스클로저 미표시여야 함. */
const LARGE_SAMPLE_RESULT: EvaluateResult = {
  valid: true,
  issues: [],
  results: [
    {
      code: "005930",
      factors: [
        {
          canonicalId: "per:pct",
          name: "PER pct",
          unit: "percent",
          value: "0.75",
          isNa: false,
          naReason: null,
          sampleSize: 500,
          smallSample: false,
        },
      ],
    },
  ],
};

/** sampleSize=null (종목-국소 factor) — 디스클로저 미표시여야 함. */
const LOCAL_FACTOR_RESULT: EvaluateResult = {
  valid: true,
  issues: [],
  results: [
    {
      code: "005930",
      factors: [
        {
          canonicalId: "close:raw",
          name: "종가",
          unit: "krw",
          value: "74200",
          isNa: false,
          naReason: null,
          sampleSize: null,
          smallSample: false,
        },
      ],
    },
  ],
};

/**
 * n==1 의 percentile=100 — "최상위" 오인 차단 핵심 케이스(ADR-0024 D3).
 * 값 보존(100 표시) + small_sample=true → 디스클로저 표시.
 */
const SINGLE_ITEM_UNIVERSE_RESULT: EvaluateResult = {
  valid: true,
  issues: [],
  results: [
    {
      code: "005930",
      factors: [
        {
          canonicalId: "per:pct",
          name: "PER pct",
          unit: "percent",
          value: "100",
          isNa: false,
          naReason: null,
          sampleSize: 1,
          smallSample: true,
        },
      ],
    },
  ],
};

describe("소표본 디스클로저 표시 게이트 (ADR-0024 Phase 2)", () => {
  it("small_sample=true + sampleSize 있으면 디스클로저 배지가 표시된다", async () => {
    await renderWith(SMALL_SAMPLE_RESULT);
    const badge = screen.getByTestId("small-sample-disclosure");
    expect(badge).toBeTruthy();
    // "모집단 5개" 포함 — 사실 텍스트 검증.
    expect(badge.textContent).toContain("5");
    expect(badge.textContent).toContain("소표본");
  });

  it("small_sample=true 이어도 값(0.75)은 항상 표시된다 — 값 보존(§2.5)", async () => {
    await renderWith(SMALL_SAMPLE_RESULT);
    // 값 셀 내부에서 "0.75" 가 보임 — 소표본이라도 숨기거나 N/A 처리 금지.
    expect(screen.getByText("0.75")).toBeTruthy();
  });

  it("small_sample=false 이면 디스클로저 배지가 없다", async () => {
    await renderWith(LARGE_SAMPLE_RESULT);
    expect(screen.queryByTestId("small-sample-disclosure")).toBeNull();
  });

  it("sampleSize=null(종목-국소 factor) 이면 디스클로저 배지가 없다", async () => {
    await renderWith(LOCAL_FACTOR_RESULT);
    expect(screen.queryByTestId("small-sample-disclosure")).toBeNull();
  });

  it("n==1 의 percentile=100 도 small_sample=true → 디스클로저 표시 + 값(100) 보존", async () => {
    await renderWith(SINGLE_ITEM_UNIVERSE_RESULT);
    // "최상위" 오인 차단 핵심: 값 100 보존.
    expect(screen.getByText("100")).toBeTruthy();
    // 소표본 디스클로저도 표시.
    const badge = screen.getByTestId("small-sample-disclosure");
    expect(badge.textContent).toContain("1");
    expect(badge.textContent).toContain("소표본");
  });
});

describe("소표본 디스클로저 중립 톤 게이트 (No Advice §2.2 / ADR-0024 D3)", () => {
  it("소표본 디스클로저 톤 상수는 grayscale(neutral) 계열이다", () => {
    expect(EVAL_SMALL_SAMPLE_BG).toMatch(/neutral/);
    expect(EVAL_SMALL_SAMPLE_TEXT).toMatch(/neutral/);
  });

  it("소표본 디스클로저 톤 상수에 등락/판단 의미색이 없다", () => {
    const constants = [EVAL_SMALL_SAMPLE_BG, EVAL_SMALL_SAMPLE_TEXT];
    for (const c of constants) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(c).not.toMatch(new RegExp(`-${token}-`));
      }
    }
  });

  it("소표본 디스클로저 배지 className 에 등락/판단 의미색이 없다", async () => {
    await renderWith(SMALL_SAMPLE_RESULT);
    const badge = screen.getByTestId("small-sample-disclosure");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(badge.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });

  it("소표본 디스클로저 텍스트에 가치·지시 어휘가 없다 (No Advice §2.2)", async () => {
    await renderWith(SMALL_SAMPLE_RESULT);
    const badge = screen.getByTestId("small-sample-disclosure");
    for (const word of FORBIDDEN_VALUE_WORDS) {
      expect(badge.textContent ?? "").not.toContain(word);
    }
  });

  it("디스클로저 배지와 값 셀의 테이블 구조가 동일 <td> 안에 있다 (인라인 고지)", async () => {
    await renderWith(SMALL_SAMPLE_RESULT);
    const badge = screen.getByTestId("small-sample-disclosure");
    const cell = badge.closest("td");
    expect(cell).toBeTruthy();
    // 같은 <td> 안에 값(0.75)도 존재.
    expect(within(cell!).getByText("0.75")).toBeTruthy();
  });
});
