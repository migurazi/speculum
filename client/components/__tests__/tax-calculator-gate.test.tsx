/**
 * 세금 계산기 가드레일 게이트 — ADR-0030 D2/D3/D4 (No Advice / TaxCalculatorPanel).
 *
 * `no-forbidden-words`(eslint) / `check_forbidden_words.py` 는 **텍스트** 만 검사한다.
 * 디스클레이머 게이트·개별 상황 입력 부재·양도세 UI 부재·조언 어휘 부재 같은
 * **가드레일 요소** 는 그 검사로 포착되지 않으므로 별도 게이트가 필요.
 * backtest-visual-gate.test.tsx 패턴 직계.
 *
 * 검증:
 *   1. TAX_PANEL_* 톤 상수가 모두 neutral/white 계열 — 등락/판단색 0.
 *   2. disclaimer_required=true 일 때 세무자문 디스클레이머 텍스트가 렌더됨(D3 게이트).
 *   3. 결과 패널과 디스클레이머가 항상 함께 렌더됨(게이트 — 분리 불가).
 *   4. 개별 상황 입력 필드(대주주/보유기간/손익통산 등) 부재(D2).
 *   5. 양도세 UI 부재(D1 deferred).
 *   6. "절세"/"유리한" 등 조언 어휘 부재.
 *   7. 계산 버튼 className 에 판단 의미색 없음.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import {
  TaxCalculatorPanel,
  TAX_PANEL_RESULT_TEXT,
  TAX_PANEL_RESULT_LABEL,
  TAX_PANEL_RESULT_ROW_BG,
  TAX_PANEL_RESULT_ROW_STRIPE,
  TAX_PANEL_DISCLAIMER,
  TAX_PANEL_LEGAL_SOURCE,
} from "@/components/Tax/TaxCalculatorPanel";
import { calculateSecuritiesTransactionTax } from "@/lib/api/tax";
import type { TaxResult } from "@/lib/api/tax";
import { renderWithIntl } from "@/test-utils/intl";

// calculateSecuritiesTransactionTax 만 mock — 톤 상수 등 다른 export 는 실제 모듈 유지.
vi.mock("@/lib/api/tax", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/tax")>();
  return { ...actual, calculateSecuritiesTransactionTax: vi.fn() };
});

/**
 * 등락/판단 의미 색 토큰 — TAX_PANEL 톤 상수에 등장 금지.
 * backtest-visual-gate.test.tsx 와 동일 목록.
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

/** TAX_PANEL tailwind 톤 상수 전체 목록. */
const TAX_PANEL_TAILWIND_CONSTANTS: ReadonlyArray<string> = [
  TAX_PANEL_RESULT_TEXT,
  TAX_PANEL_RESULT_LABEL,
  TAX_PANEL_RESULT_ROW_BG,
  TAX_PANEL_RESULT_ROW_STRIPE,
  TAX_PANEL_DISCLAIMER,
  TAX_PANEL_LEGAL_SOURCE,
];

/** No Advice 세무자문 조언·절세 어휘 — 결과 렌더에 등장 금지(ADR-0030). */
const FORBIDDEN_ADVISORY_WORDS: ReadonlyArray<string> = [
  "절세",
  "세금 줄이기",
  "유리한",
  "추천",
  "최적",
  "최소화",
  "우수",
  "최고",
  "Best",
  "절약",
];

// ── fixture ─────────────────────────────────────────────────────────────────

/** disclaimer_required=true 기본 결과 fixture. */
const MOCK_RESULT: TaxResult = {
  taxAmount: "1500",
  appliedRate: "0.0015",
  effectiveDate: "2023-01-01",
  legalSource: "증권거래세법 제8조 제1항",
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
      <TaxCalculatorPanel />
    </QueryClientProvider>,
  );
}

/** 폼 채우기 + 계산 헬퍼. */
async function fillFormAndCalculate(result: TaxResult): Promise<void> {
  vi.mocked(calculateSecuritiesTransactionTax).mockResolvedValue(result);
  renderPanel();

  // 거래금액 입력.
  const amountInput = screen.getByTestId("tax-trade-amount-input");
  await userEvent.clear(amountInput);
  await userEvent.type(amountInput, "1000000");

  // 거래일 입력.
  const dateInput = screen.getByTestId("tax-trade-date-input");
  await userEvent.clear(dateInput);
  await userEvent.type(dateInput, "2024-01-15");

  // 계산 실행.
  await userEvent.click(screen.getByTestId("tax-calculate-btn"));

  // 결과 대기.
  await screen.findByTestId("tax-result-panel");
}

// ── 게이트 1: 톤 상수 grayscale 검증 ─────────────────────────────────────────

describe("TaxCalculatorPanel 톤 상수 게이트 (ADR-0030 D3)", () => {
  it("TAX_PANEL tailwind 톤 상수는 모두 neutral/white 계열이다", () => {
    for (const tone of TAX_PANEL_TAILWIND_CONSTANTS) {
      expect(tone, `톤 상수 '${tone}' 가 neutral/white 이 아님`).toMatch(
        /neutral|white/,
      );
    }
  });

  it("TAX_PANEL tailwind 톤 상수에 등락/판단 의미색이 없다", () => {
    for (const tone of TAX_PANEL_TAILWIND_CONSTANTS) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(
          tone,
          `톤 상수 '${tone}' 에 금지 색 토큰 '${token}' 가 있음`,
        ).not.toMatch(new RegExp(`-${token}-|^${token}-|^${token}$`));
      }
    }
  });
});

// ── 게이트 2: disclaimer_required=true 시 세무자문 디스클레이머 렌더 ─────────

describe("TaxCalculatorPanel 세무자문 디스클레이머 게이트 (ADR-0030 D3)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("disclaimer_required=true 일 때 세무자문 디스클레이머 텍스트가 렌더된다", async () => {
    await fillFormAndCalculate(MOCK_RESULT);

    const disclaimer = screen.getByTestId("tax-disclaimer");
    expect(disclaimer).toBeTruthy();
    // ADR-0030 D3 핵심 키워드 포함 확인.
    expect(disclaimer.textContent).toMatch(/세무자문이 아닙니다/);
    expect(disclaimer.textContent).toMatch(/세무 전문가 상담/);
  });

  it("결과 패널과 세무자문 디스클레이머가 함께 렌더된다 (게이트 — 분리 불가)", async () => {
    await fillFormAndCalculate(MOCK_RESULT);

    // 결과 패널과 디스클레이머 모두 존재해야 함.
    expect(screen.getByTestId("tax-result-panel")).toBeTruthy();
    expect(screen.getByTestId("tax-disclaimer")).toBeTruthy();
  });

  it("디스클레이머 박스 className 에 판단 의미색(red 등)이 없다", async () => {
    await fillFormAndCalculate(MOCK_RESULT);

    const disclaimer = screen.getByTestId("tax-disclaimer");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(disclaimer.className, `디스클레이머에 금지 색 '${token}'`).not.toMatch(
        new RegExp(`-${token}-`),
      );
    }
  });
});

// ── 게이트 3: 법령출처·효력일 표시 (ADR-0030 D4) ────────────────────────────

describe("TaxCalculatorPanel 법령출처 표시 게이트 (ADR-0030 D4)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("결과에 법령출처(legalSource)가 표시된다", async () => {
    await fillFormAndCalculate(MOCK_RESULT);

    const legalSource = screen.getByTestId("tax-legal-source");
    expect(legalSource).toBeTruthy();
    expect(legalSource.textContent).toContain("증권거래세법 제8조 제1항");
  });

  it("결과에 세율 효력일(effectiveDate)이 표시된다", async () => {
    await fillFormAndCalculate(MOCK_RESULT);

    const legalSource = screen.getByTestId("tax-legal-source");
    expect(legalSource.textContent).toContain("2023-01-01");
  });
});

// ── 게이트 4: 개별 상황 입력 필드 부재 (ADR-0030 D2) ────────────────────────

describe("TaxCalculatorPanel 개별 상황 입력 0 게이트 (ADR-0030 D2)", () => {
  it("대주주 여부 입력 필드가 없다", () => {
    renderPanel();

    const panel = screen.getByTestId("tax-calculator-panel");
    const text = panel.textContent ?? "";
    expect(text).not.toMatch(/대주주/);
  });

  it("보유기간 입력 필드가 없다", () => {
    renderPanel();

    const panel = screen.getByTestId("tax-calculator-panel");
    const text = panel.textContent ?? "";
    expect(text).not.toMatch(/보유기간|보유 기간/);
  });

  it("손익통산 입력 필드가 없다", () => {
    renderPanel();

    const panel = screen.getByTestId("tax-calculator-panel");
    const text = panel.textContent ?? "";
    expect(text).not.toMatch(/손익통산|손익 통산/);
  });

  it("폼에 시장·금액·일자 입력만 존재한다 (입력 필드 3개 이하)", () => {
    renderPanel();

    // select + 2 input(amount, date) = 3개만.
    // 시장구분 select 포함 총 입력 요소: select(1) + text(1) + date(1) = 3.
    const marketSelect = screen.getByTestId("tax-market-select");
    const amountInput = screen.getByTestId("tax-trade-amount-input");
    const dateInput = screen.getByTestId("tax-trade-date-input");
    expect(marketSelect).toBeTruthy();
    expect(amountInput).toBeTruthy();
    expect(dateInput).toBeTruthy();
  });
});

// ── 게이트 5: 양도세 UI 부재 (ADR-0030 D1) ───────────────────────────────────

describe("TaxCalculatorPanel 양도세 UI 부재 게이트 (ADR-0030 D1)", () => {
  it("패널에 '양도세' 텍스트가 없다", () => {
    renderPanel();

    const panel = screen.getByTestId("tax-calculator-panel");
    const text = panel.textContent ?? "";
    expect(text).not.toMatch(/양도세|양도소득세/);
  });

  it("양도세 계산 버튼/탭이 없다", () => {
    renderPanel();

    const panel = screen.getByTestId("tax-calculator-panel");
    expect(within(panel).queryByText(/양도세 계산|양도소득세 계산/i)).toBeNull();
  });
});

// ── 게이트 6: 조언 어휘 부재 (ADR-0030 D3) ───────────────────────────────────

describe("TaxCalculatorPanel 조언 어휘 부재 게이트 (ADR-0030 D3)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("결과 패널 전체에 절세/유리한 등 조언 어휘가 없다", async () => {
    await fillFormAndCalculate(MOCK_RESULT);

    const panel = screen.getByTestId("tax-result-panel");
    const text = panel.textContent ?? "";

    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text, `패널에 금지 조언 어휘 '${word}' 가 있음`).not.toContain(word);
    }
  });

  it("폼 전체에 조언 어휘가 없다", () => {
    renderPanel();

    const panel = screen.getByTestId("tax-calculator-panel");
    const text = panel.textContent ?? "";

    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text, `폼에 금지 조언 어휘 '${word}' 가 있음`).not.toContain(word);
    }
  });
});

// ── 게이트 7: 계산 버튼 판단색 없음 ─────────────────────────────────────────

describe("TaxCalculatorPanel 계산 버튼 판단색 게이트", () => {
  it("계산 버튼 className 에 판단 의미색이 없다", () => {
    renderPanel();

    const calcBtn = screen.getByTestId("tax-calculate-btn");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(calcBtn.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });
});
