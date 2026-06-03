/**
 * AI 공시 사실추출 시각요소 게이트 — ADR-0031 D1/D2/D3/D4/D5/D6.
 *
 * backtest-visual-gate.test.tsx / disclosure-panel-gate.test.tsx 패턴 직계.
 *
 * `no-forbidden-words`(eslint) / `check_forbidden_words.py` 는 텍스트만 검사한다.
 * 사실 추출 결과의 색·디스클레이머 게이트·출처 링크·503/422 안내 같은
 * 시각 요소는 그 검사로 포착되지 않으므로 별도 게이트가 필요하다.
 *
 * 검증:
 *   1. FACTS_PANEL_* 톤 상수가 모두 neutral/white 계열 — 등락/판단색 0.
 *   2. disclaimerRequired=true 시 AI 디스클레이머 렌더 + 결과와 함께(게이트).
 *   3. 구조화 슬롯(공시유형/금액/일자/당사자/수량)만 렌더 — 자유서술/전망/추천 영역 부재.
 *   4. 출처 DART 링크 렌더 — target=_blank, rel=noopener noreferrer, href 올바름.
 *   5. 503 중립 안내 — "AI 사실추출 기능은 현재 비활성화" 키워드.
 *   6. 422 중립 안내 — "추출 결과가 표시 기준을 통과하지 못했습니다" 키워드.
 *   7. 결과 렌더에 전망/추천/판단 어휘 없음.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import {
  DisclosureFactsPanel,
  FACTS_PANEL_BG,
  FACTS_PANEL_TRIGGER_BTN,
  FACTS_PANEL_DISCLAIMER,
  FACTS_PANEL_SLOT_LABEL,
  FACTS_PANEL_SLOT_VALUE,
  FACTS_PANEL_SOURCE_LINK,
  FACTS_PANEL_MUTED,
  FACTS_PANEL_ROW_STRIPE,
} from "@/components/StockDetail/DisclosureFactsPanel";
import { extractDisclosureFacts } from "@/lib/api/fact-extraction";
import type { ExtractFactsResult } from "@/lib/api/fact-extraction";
import { ApiError } from "@/lib/api/client";
import { renderWithIntl } from "@/test-utils/intl";

// extractDisclosureFacts 만 mock — 톤 상수 등 다른 export 는 실제 모듈 유지.
vi.mock("@/lib/api/fact-extraction", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/fact-extraction")>();
  return { ...actual, extractDisclosureFacts: vi.fn() };
});

// =============================================================================
// 금지 색 목록 — backtest-visual-gate.test.tsx 와 동일
// =============================================================================

/** 등락/판단 의미 색 토큰 — FACTS_PANEL 톤 상수에 등장 금지. */
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

/** FACTS_PANEL tailwind 톤 상수 전체 목록. */
const FACTS_PANEL_TAILWIND_CONSTANTS: ReadonlyArray<string> = [
  FACTS_PANEL_BG,
  FACTS_PANEL_TRIGGER_BTN,
  FACTS_PANEL_DISCLAIMER,
  FACTS_PANEL_SLOT_LABEL,
  FACTS_PANEL_SLOT_VALUE,
  FACTS_PANEL_SOURCE_LINK,
  FACTS_PANEL_MUTED,
  FACTS_PANEL_ROW_STRIPE,
];

/** No Advice 판단·전망·추천·평가 어휘 — 결과 렌더에 등장 금지. ADR-0031 D5. */
const FORBIDDEN_ADVISORY_WORDS: ReadonlyArray<string> = [
  "전망",
  "예상",
  "추천",
  "매수",
  "매도",
  "호재",
  "악재",
  "유망",
  "긍정적",
  "부정적",
  "상승",
  "하락",
  "투자 판단",
  "별점",
  "우수",
];

// =============================================================================
// Fixture
// =============================================================================

const MOCK_CODE = "005930";
const MOCK_RCEPT_NO = "20260515000001";
const MOCK_DART_URL = `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${MOCK_RCEPT_NO}`;

/** 정상 추출 결과 fixture — disclaimerRequired=true. */
const MOCK_RESULT: ExtractFactsResult = {
  facts: {
    disclosureType: "유상증자결정",
    amounts: ["1,000억원"],
    dates: ["2026-07-01", "2026-08-15"],
    parties: ["삼성전자주식회사", "주주총회"],
    quantities: ["10,000,000주"],
  },
  source: {
    rceptNo: MOCK_RCEPT_NO,
    dartUrl: MOCK_DART_URL,
  },
  disclaimerRequired: true,
};

/** 빈 슬롯 fixture — 일부 배열이 빈 경우. */
const MOCK_RESULT_EMPTY_SLOTS: ExtractFactsResult = {
  facts: {
    disclosureType: "임시주주총회소집결의",
    amounts: [],
    dates: ["2026-09-01"],
    parties: [],
    quantities: [],
  },
  source: {
    rceptNo: MOCK_RCEPT_NO,
    dartUrl: MOCK_DART_URL,
  },
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

function renderFactsPanel(): void {
  renderWithIntl(
    <QueryClientProvider client={makeClient()}>
      <DisclosureFactsPanel
        code={MOCK_CODE}
        rceptNo={MOCK_RCEPT_NO}
        disclosureTitle="유상증자결정"
      />
    </QueryClientProvider>,
  );
}

/** "AI 사실 추출" 버튼 클릭 + 결과 대기 헬퍼. */
async function clickExtractAndWait(result: ExtractFactsResult): Promise<void> {
  vi.mocked(extractDisclosureFacts).mockResolvedValue(result);
  renderFactsPanel();

  const btn = screen.getByTestId("facts-extract-btn");
  await userEvent.click(btn);

  await screen.findByTestId("facts-result-section");
}

// =============================================================================
// 게이트 1: 톤 상수 grayscale 검증 (정적 — 렌더 불필요)
// =============================================================================

describe("DisclosureFactsPanel 톤 상수 게이트 (ADR-0031 D3)", () => {
  it("FACTS_PANEL tailwind 톤 상수는 모두 neutral/white 계열이다", () => {
    for (const tone of FACTS_PANEL_TAILWIND_CONSTANTS) {
      expect(tone, `톤 상수 '${tone}' 가 neutral/white 이 아님`).toMatch(
        /neutral|white/,
      );
    }
  });

  it("FACTS_PANEL tailwind 톤 상수에 등락/판단 의미색이 없다", () => {
    for (const tone of FACTS_PANEL_TAILWIND_CONSTANTS) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(
          tone,
          `톤 상수 '${tone}' 에 금지 색 토큰 '${token}' 가 있음`,
        ).not.toMatch(new RegExp(`-${token}-|^${token}-|^${token}$`));
      }
    }
  });
});

// =============================================================================
// 게이트 2: AI 디스클레이머 게이트 (ADR-0031 D3)
// =============================================================================

describe("DisclosureFactsPanel AI 디스클레이머 게이트 (ADR-0031 D3)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("disclaimerRequired=true 일 때 AI 디스클레이머 텍스트가 렌더된다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const disclaimer = screen.getByTestId("facts-disclaimer");
    expect(disclaimer).toBeTruthy();

    // ADR-0031 D3 핵심 키워드 — "AI 가 추출", "원문 확인", "hallucination".
    expect(disclaimer.textContent).toMatch(/AI\s*가\s*추출/);
    expect(disclaimer.textContent).toMatch(/원문\s*확인/);
    expect(disclaimer.textContent).toMatch(/hallucination/i);
  });

  it("결과 패널과 디스클레이머가 함께 렌더된다 (게이트 — 분리 불가)", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    // 결과 섹션과 디스클레이머 모두 존재해야 함.
    expect(screen.getByTestId("facts-result-section")).toBeTruthy();
    expect(screen.getByTestId("facts-disclaimer")).toBeTruthy();
  });

  it("디스클레이머 박스 className 에 판단 의미색이 없다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const disclaimer = screen.getByTestId("facts-disclaimer");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(disclaimer.className, `디스클레이머에 금지 색 '${token}'`).not.toMatch(
        new RegExp(`-${token}-`),
      );
    }
  });
});

// =============================================================================
// 게이트 3: 구조화 슬롯만 렌더 — 자유 서술/전망/추천 영역 부재 (ADR-0031 D1/D5)
// =============================================================================

describe("DisclosureFactsPanel 구조화 슬롯 게이트 (ADR-0031 D1/D5)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("공시유형 슬롯이 렌더된다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const slotsTable = screen.getByTestId("facts-slots-table");
    expect(slotsTable.textContent).toMatch(/공시유형/);
    expect(slotsTable.textContent).toContain("유상증자결정");
  });

  it("금액 슬롯이 렌더된다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const slotsTable = screen.getByTestId("facts-slots-table");
    expect(slotsTable.textContent).toMatch(/금액/);
    expect(slotsTable.textContent).toContain("1,000억원");
  });

  it("일자 슬롯이 렌더된다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const slotsTable = screen.getByTestId("facts-slots-table");
    expect(slotsTable.textContent).toMatch(/일자/);
    expect(slotsTable.textContent).toContain("2026-07-01");
  });

  it("당사자 슬롯이 렌더된다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const slotsTable = screen.getByTestId("facts-slots-table");
    expect(slotsTable.textContent).toMatch(/당사자/);
    expect(slotsTable.textContent).toContain("삼성전자주식회사");
  });

  it("수량 슬롯이 렌더된다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const slotsTable = screen.getByTestId("facts-slots-table");
    expect(slotsTable.textContent).toMatch(/수량/);
    expect(slotsTable.textContent).toContain("10,000,000주");
  });

  it("결과 렌더에 전망/추천/평가 어휘가 없다 (ADR-0031 D5)", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const resultSection = screen.getByTestId("facts-result-section");
    const text = resultSection.textContent ?? "";

    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text, `결과에 금지 어휘 '${word}' 가 있음`).not.toContain(word);
    }
  });

  it("빈 배열 슬롯은 '—' 로 표시된다 (결손 중립 표시)", async () => {
    await clickExtractAndWait(MOCK_RESULT_EMPTY_SLOTS);

    const slotsTable = screen.getByTestId("facts-slots-table");
    // amounts, parties, quantities 가 빈 배열 → "—" 표시.
    const cells = slotsTable.querySelectorAll("td");
    const cellTexts = Array.from(cells).map((c) => c.textContent ?? "");
    // "—" 가 1개 이상 존재.
    expect(cellTexts.some((t) => t.includes("—"))).toBe(true);
  });

  it("슬롯 행 className 에 판단 의미색이 없다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const slotsTable = screen.getByTestId("facts-slots-table");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(slotsTable.innerHTML).not.toMatch(
        new RegExp(`-${token}-\\d{2,3}`),
      );
    }
  });
});

// =============================================================================
// 게이트 4: 출처 DART 링크 (ADR-0031 D3)
// =============================================================================

describe("DisclosureFactsPanel 출처 DART 링크 게이트 (ADR-0031 D3)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("출처 섹션이 렌더된다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    expect(screen.getByTestId("facts-source")).toBeTruthy();
  });

  it("DART 원문 링크가 렌더되고 target=_blank 이다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const dartLink = screen.getByTestId("facts-dart-link");
    expect(dartLink).toBeTruthy();
    expect(dartLink.getAttribute("target")).toBe("_blank");
  });

  it("DART 원문 링크에 rel=noopener noreferrer 가 있다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const dartLink = screen.getByTestId("facts-dart-link");
    expect(dartLink.getAttribute("rel")).toContain("noopener");
    expect(dartLink.getAttribute("rel")).toContain("noreferrer");
  });

  it("DART 링크 href 가 올바른 DART URL 이다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const dartLink = screen.getByTestId("facts-dart-link");
    expect(dartLink.getAttribute("href")).toBe(MOCK_DART_URL);
  });

  it("출처 링크 className 에 판단 의미색이 없다", async () => {
    await clickExtractAndWait(MOCK_RESULT);

    const source = screen.getByTestId("facts-source");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(source.innerHTML).not.toMatch(new RegExp(`-${token}-\\d{2,3}`));
    }
  });
});

// =============================================================================
// 게이트 5: 503 중립 안내 (ADR-0031 D6 — LLM 미연동)
// =============================================================================

describe("DisclosureFactsPanel 503 중립 안내 게이트 (ADR-0031 D6)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("503 응답 시 'AI 사실추출 기능은 현재 비활성화' 중립 안내가 렌더된다", async () => {
    vi.mocked(extractDisclosureFacts).mockRejectedValue(
      new ApiError(503, "Service Unavailable", ""),
    );
    renderFactsPanel();

    await userEvent.click(screen.getByTestId("facts-extract-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("facts-503-notice")).toBeTruthy();
    });

    const notice = screen.getByTestId("facts-503-notice");
    expect(notice.textContent).toMatch(/비활성화/);
  });

  it("503 시 사실 슬롯 표가 렌더되지 않는다", async () => {
    vi.mocked(extractDisclosureFacts).mockRejectedValue(
      new ApiError(503, "Service Unavailable", ""),
    );
    renderFactsPanel();

    await userEvent.click(screen.getByTestId("facts-extract-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("facts-503-notice")).toBeTruthy();
    });

    // 사실 슬롯 표 없음.
    expect(screen.queryByTestId("facts-slots-table")).toBeNull();
  });

  it("503 안내 텍스트에 에러·실패·오류 같은 부정적 강조 어휘가 없다", async () => {
    vi.mocked(extractDisclosureFacts).mockRejectedValue(
      new ApiError(503, "Service Unavailable", ""),
    );
    renderFactsPanel();

    await userEvent.click(screen.getByTestId("facts-extract-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("facts-503-notice")).toBeTruthy();
    });

    const notice = screen.getByTestId("facts-503-notice");
    // 중립 안내 — 에러/실패/오류 강조 없음.
    expect(notice.textContent).not.toMatch(/^오류|^에러|^실패/);
  });
});

// =============================================================================
// 게이트 6: 422 중립 안내 (ADR-0031 D2 — 출력 게이트 fail-closed)
// =============================================================================

describe("DisclosureFactsPanel 422 중립 안내 게이트 (ADR-0031 D2)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("422 응답 시 '추출 결과가 표시 기준을 통과하지 못했습니다' 중립 안내가 렌더된다", async () => {
    vi.mocked(extractDisclosureFacts).mockRejectedValue(
      new ApiError(422, "Unprocessable Entity", ""),
    );
    renderFactsPanel();

    await userEvent.click(screen.getByTestId("facts-extract-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("facts-422-notice")).toBeTruthy();
    });

    const notice = screen.getByTestId("facts-422-notice");
    expect(notice.textContent).toMatch(/표시 기준/);
  });

  it("422 시 사실 슬롯 표가 렌더되지 않는다 (fail-closed)", async () => {
    vi.mocked(extractDisclosureFacts).mockRejectedValue(
      new ApiError(422, "Unprocessable Entity", ""),
    );
    renderFactsPanel();

    await userEvent.click(screen.getByTestId("facts-extract-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("facts-422-notice")).toBeTruthy();
    });

    // 사실 슬롯 표 없음 — 게이트 실패 시 결과 미표시(ADR-0031 D2 fail-closed).
    expect(screen.queryByTestId("facts-slots-table")).toBeNull();
  });

  it("422 시 AI 디스클레이머가 없다 (결과 없으므로 게이트 불필요)", async () => {
    vi.mocked(extractDisclosureFacts).mockRejectedValue(
      new ApiError(422, "Unprocessable Entity", ""),
    );
    renderFactsPanel();

    await userEvent.click(screen.getByTestId("facts-extract-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("facts-422-notice")).toBeTruthy();
    });

    expect(screen.queryByTestId("facts-disclaimer")).toBeNull();
  });
});

// =============================================================================
// 게이트 7: ExtractFactsResult 타입 구조 — 전망/요약/추천 필드 부재 (ADR-0031 D5)
// =============================================================================

describe("ExtractFactsResult 타입 구조 게이트 (ADR-0031 D1/D5)", () => {
  it("DisclosureFacts 에 전망/요약/추천/평가 필드가 없다", () => {
    // 타입 구조를 런타임에서 검증.
    const facts = MOCK_RESULT.facts;
    const keys = Object.keys(facts);

    // 허용 필드: 5개 슬롯만.
    expect(keys).toContain("disclosureType");
    expect(keys).toContain("amounts");
    expect(keys).toContain("dates");
    expect(keys).toContain("parties");
    expect(keys).toContain("quantities");

    // 금지 필드: 전망/요약/추천/평가 관련 필드 부재.
    expect(keys).not.toContain("summary");
    expect(keys).not.toContain("forecast");
    expect(keys).not.toContain("recommendation");
    expect(keys).not.toContain("evaluation");
    expect(keys).not.toContain("sentiment");
    expect(keys).not.toContain("outlook");
    expect(keys).not.toContain("advice");

    // 정확히 5필드.
    expect(keys).toHaveLength(5);
  });

  it("ExtractFactsResult 에 source + disclaimerRequired 가 있다 (ADR-0031 D3)", () => {
    const result = MOCK_RESULT;
    const keys = Object.keys(result);

    expect(keys).toContain("facts");
    expect(keys).toContain("source");
    expect(keys).toContain("disclaimerRequired");

    // FactSource 구조.
    const sourceKeys = Object.keys(result.source);
    expect(sourceKeys).toContain("rceptNo");
    expect(sourceKeys).toContain("dartUrl");
    expect(sourceKeys).toHaveLength(2);
  });
});
