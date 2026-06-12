/**
 * Factor Lab 평가 결과 시각요소 review gate — T74 Phase 2 (No Advice / ADR-0022).
 *
 * `check_forbidden_words.py` / eslint `no-forbidden-words` 는 **텍스트** 만
 * 검사한다. 평가 표의 색·랭킹·컬러맵 같은 **시각 요소** 는 그 검사로 포착되지
 * 않으므로 별도 gate 가 필요(M1 chart-visual-gate.test.tsx 패턴 복제). 본
 * 테스트는 EvaluatePreview 의 시각 정책 불변식을 회귀 방지로 박는다 — 색을
 * 명명 상수로 export 했기에 실제 렌더 톤과 1:1 대응한다.
 *
 * 검증:
 *   1. 평가 셀/헤더 색이 grayscale(neutral) 계열만 — 값의 부호/크기로 색 분기
 *      없음(등락색 0). ADR-0022 D4 / ADR-0007 D2.2.
 *   2. 등락 의미색(한국 관행 빨강/파랑, 서구 녹색, heatmap 색조)이 평가 톤 상수
 *      어디에도 없음 — 가격 차트와 달리 평가 표는 "등락 사실" 이 아니므로 어떤
 *      판단색도 금지.
 *   3. 렌더 출력에 랭킹/순위/Top-N 배지 부재 + 행 순서 = codes 입력 순서 유지
 *      (자동 정렬 금지, ADR-0022 D1).
 *   4. percentile 류 유니버스-상대 값도 동일 중립 톤으로 렌더 — 값 크기에 따른
 *      색조 컬러맵(heatmap) 부재.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  EvaluatePreview,
  EVAL_HEADER_COLOR,
  EVAL_NA_COLOR,
  EVAL_SMALL_SAMPLE_BG,
  EVAL_SMALL_SAMPLE_TEXT,
  EVAL_TEXT_COLOR,
} from "@/components/Lab/EvaluatePreview";
import {
  evaluateFactorPack,
  type EvaluateResult,
  type FactorPack,
} from "@/lib/api/factor-packs";
import { renderWithIntl } from "@/test-utils/intl";

// evaluateFactorPack 만 mock — 톤 상수 등 다른 export 는 실제 모듈 유지.
vi.mock("@/lib/api/factor-packs", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/api/factor-packs")>();
  return { ...actual, evaluateFactorPack: vi.fn() };
});

/**
 * 등락/판단 의미를 갖는 색의 Tailwind 토큰 — 평가 톤 상수에 등장 금지.
 * 한국 관행 빨강/파랑(차트에선 등락 사실이나 평가 표에선 판단색),
 * 서구 녹색, heatmap 류 색조(amber/orange/yellow/purple 등) 전부 배제.
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

/** 평가 표가 노출하는 모든 톤 상수 — grayscale(neutral) 만 허용. */
const EVAL_TONE_CONSTANTS: ReadonlyArray<string> = [
  EVAL_TEXT_COLOR,
  EVAL_NA_COLOR,
  EVAL_HEADER_COLOR,
  // 소표본 디스클로저 톤 상수(ADR-0024 Phase 2) — 중립 grayscale 검증 포함.
  EVAL_SMALL_SAMPLE_BG,
  EVAL_SMALL_SAMPLE_TEXT,
];

const MOCK_PACK: FactorPack = {
  pack_slug: "user/my-pack",
  version: "0.1.0",
  factors: [],
  citation: { title: "t" },
};

/** code 순서: 035420 → 005930 → 000660 (정렬되지 않은 입력 순서). */
const MOCK_RESULT: EvaluateResult = {
  valid: true,
  issues: [],
  results: [
    {
      code: "035420",
      factors: [
        { canonicalId: "per:custom", name: "PER", unit: "ratio", value: "12.34", isNa: false, naReason: null, sampleSize: null, smallSample: false },
        { canonicalId: "per:pct", name: "PER pct", unit: "percent", value: "0.91", isNa: false, naReason: null, sampleSize: null, smallSample: false },
      ],
    },
    {
      code: "005930",
      factors: [
        { canonicalId: "per:custom", name: "PER", unit: "ratio", value: "8.10", isNa: false, naReason: null, sampleSize: null, smallSample: false },
        { canonicalId: "per:pct", name: "PER pct", unit: "percent", value: "0.12", isNa: false, naReason: null, sampleSize: null, smallSample: false },
      ],
    },
    {
      code: "000660",
      factors: [
        { canonicalId: "per:custom", name: "PER", unit: "ratio", value: null, isNa: true, naReason: "데이터 없음", sampleSize: null, smallSample: false },
        { canonicalId: "per:pct", name: "PER pct", unit: "percent", value: "0.55", isNa: false, naReason: null, sampleSize: null, smallSample: false },
      ],
    },
  ],
};

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

async function renderAndEvaluate(): Promise<void> {
  vi.mocked(evaluateFactorPack).mockResolvedValue(MOCK_RESULT);
  renderWithIntl(
    <QueryClientProvider client={makeClient()}>
      <EvaluatePreview pack={MOCK_PACK} asOf="2026-06-01" />
    </QueryClientProvider>,
  );
  await userEvent.type(
    screen.getByPlaceholderText("005930, 000660, 035420"),
    "035420, 005930, 000660",
  );
  await userEvent.click(screen.getByRole("button", { name: "평가" }));
  await screen.findByText("035420");
}

describe("Factor Lab 평가 톤 상수 gate (T74)", () => {
  it("평가 톤 상수는 모두 grayscale(neutral) 계열이다", () => {
    for (const tone of EVAL_TONE_CONSTANTS) {
      expect(tone).toMatch(/neutral/);
    }
  });

  it("평가 톤 상수에 등락/판단 의미색(빨강·파랑·녹색·색조)이 없다", () => {
    for (const tone of EVAL_TONE_CONSTANTS) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(tone).not.toMatch(new RegExp(`-${token}-`));
      }
    }
  });

  it("값 톤과 결손(N/A) 톤은 둘 다 neutral — 판단색으로 분기하지 않는다", () => {
    expect(EVAL_TEXT_COLOR).toMatch(/neutral/);
    expect(EVAL_NA_COLOR).toMatch(/neutral/);
    expect(EVAL_TEXT_COLOR).not.toBe(EVAL_NA_COLOR);
  });
});

describe("EvaluatePreview 렌더 gate (T74)", () => {
  it("행 순서가 codes 입력 순서를 그대로 유지한다 (자동 정렬 금지)", async () => {
    await renderAndEvaluate();
    const rowHeaders = screen
      .getAllByRole("rowheader")
      .map((el) => el.textContent);
    // 값 크기로 정렬했다면 005930(8.10) 이 먼저 와야 하지만, 입력 순서를 유지.
    expect(rowHeaders).toEqual(["035420", "005930", "000660"]);
  });

  it("순위/랭킹/Top-N 배지가 없다", async () => {
    await renderAndEvaluate();
    const table = screen.getByRole("table");
    expect(within(table).queryByText(/1위|순위|랭킹|Top|#1/i)).toBeNull();
  });

  it("percentile 류 값을 포함한 모든 값 셀이 동일 중립 톤(neutral)으로 렌더된다", async () => {
    await renderAndEvaluate();
    // per:pct 열(유니버스-상대 percentile)의 값 셀 톤이 중립 — 색조 컬러맵 없음.
    const pctCell = screen.getByText("0.91").closest("td");
    expect(pctCell?.className).toContain("neutral");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(pctCell?.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });

  it("값 크기가 큰 셀과 작은 셀의 톤이 동일하다 (등락색 0)", async () => {
    await renderAndEvaluate();
    const big = screen.getByText("12.34").closest("td")?.className ?? "";
    const small = screen.getByText("8.10").closest("td")?.className ?? "";
    expect(big).toBe(small);
  });

  it("결손 셀은 N/A 로 표시되며 판단색이 아닌 neutral 톤이다", async () => {
    await renderAndEvaluate();
    const naCell = screen.getByText("N/A").closest("td");
    expect(naCell?.className).toContain("neutral");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(naCell?.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });
});
