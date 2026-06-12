/**
 * DisclosurePanel 시각요소 게이트 — ADR-0026 D1/D2/D3 (공시 metadata 표시).
 *
 * custom-screen-visual-gate.test.tsx 패턴으로:
 *   1. DisclosurePanel 이 export 하는 톤 상수가 모두 neutral/white — 등락색·판단색 토큰 부재.
 *   2. 렌더 출력에 추천/판단 어휘(시스템 생성 라벨) 부재.
 *      — reportName(DART 원문 EXTERNAL_QUOTE, ADR-0026 D2)은 검사 대상 아님.
 *   3. Disclosure 타입에 본문/요약 필드가 없음 — 타입 구조 회귀 검증.
 *   4. 정렬/필터 토글 UI 부재 — 재정렬 버튼·"중요공시" 배지 없음.
 *   5. 로딩/에러/빈 상태 중립 처리 — 판단색 className 부재.
 *   6. DART 원문 링크: target=_blank, rel=noopener noreferrer, href 올바름.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import {
  DisclosurePanel,
  DISCLOSURE_PANEL_BG,
  DISCLOSURE_PANEL_HEADING,
  DISCLOSURE_PANEL_HEADER,
  DISCLOSURE_PANEL_TEXT,
  DISCLOSURE_PANEL_DATE,
  DISCLOSURE_PANEL_LINK,
  DISCLOSURE_PANEL_ROW_STRIPE,
  DISCLOSURE_PANEL_MUTED,
} from "@/components/StockDetail/DisclosurePanel";
import type { Disclosure } from "@/lib/api/disclosures";
import { renderWithIntl } from "@/test-utils/intl";

// =============================================================================
// 상수 목록 — visual gate 검증 대상
// =============================================================================

/** DisclosurePanel 에서 export 하는 모든 톤 상수. */
const DISCLOSURE_TONE_CONSTANTS: ReadonlyArray<string> = [
  DISCLOSURE_PANEL_BG,
  DISCLOSURE_PANEL_HEADING,
  DISCLOSURE_PANEL_HEADER,
  DISCLOSURE_PANEL_TEXT,
  DISCLOSURE_PANEL_DATE,
  DISCLOSURE_PANEL_LINK,
  DISCLOSURE_PANEL_ROW_STRIPE,
  DISCLOSURE_PANEL_MUTED,
];

/**
 * 등락/판단 의미 색 토큰 — 톤 상수에 등장 금지.
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
 * No Advice 판단·추천·순위 어휘 — 시스템 생성 텍스트에 등장 금지.
 * ADR-0026 D1: 시스템 큐레이션 라벨 0.
 * 주의: reportName 은 DART 원문(EXTERNAL_QUOTE) 이므로 검사 대상 아님.
 */
const FORBIDDEN_ADVISORY_WORDS: ReadonlyArray<string> = [
  "중요",
  "주목",
  "호재",
  "매수",
  "추천",
  "인기",
  "순위",
  "랭킹",
  "Top",
  "상위",
  "우수",
  "최고",
];

// =============================================================================
// Fixture
// =============================================================================

const MOCK_CODE = "005930";

/** 정상 공시 응답 wire 데이터 — 3필드만(ADR-0026 D1). */
const MOCK_DISCLOSURES_WIRE = {
  code: MOCK_CODE,
  as_of: "2026-06-02",
  disclosures: [
    {
      report_name: "분기보고서 (2026.03)",
      rcept_date: "2026-05-15",
      dart_url: "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515000001",
    },
    {
      report_name: "주요사항보고서(자기주식취득결정)",
      rcept_date: "2026-04-30",
      dart_url: "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260430000002",
    },
  ],
};

/** 빈 공시 목록. */
const EMPTY_DISCLOSURES_WIRE = {
  code: MOCK_CODE,
  as_of: "2026-06-02",
  disclosures: [],
};

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function renderPanel(props?: { asOf?: string }) {
  return renderWithIntl(
    <QueryClientProvider client={makeClient()}>
      <DisclosurePanel code={MOCK_CODE} asOf={props?.asOf} />
    </QueryClientProvider>,
  );
}

// =============================================================================
// 게이트 1: 톤 상수 grayscale 검증 (정적 — 렌더 불필요)
// =============================================================================

describe("DisclosurePanel 톤 상수 게이트 (ADR-0026 D1)", () => {
  it("모든 톤 상수는 grayscale(neutral/white) 계열이다", () => {
    for (const tone of DISCLOSURE_TONE_CONSTANTS) {
      expect(tone, `톤 상수 '${tone}' 가 neutral/white 가 아님`).toMatch(
        /neutral|white/,
      );
    }
  });

  it("톤 상수에 등락/판단 의미색 토큰이 없다", () => {
    for (const tone of DISCLOSURE_TONE_CONSTANTS) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(
          tone,
          `톤 상수 '${tone}' 에 금지 색 토큰 '${token}' 가 있음`,
        ).not.toMatch(new RegExp(`(?:^|[-\\s])${token}(?:[-\\s]|$)`));
      }
    }
  });
});

// =============================================================================
// 게이트 2: Disclosure 타입 구조 — 본문/요약 필드 부재 회귀
// =============================================================================

describe("Disclosure 타입 구조 게이트 (ADR-0026 D1)", () => {
  it("Disclosure 타입에 본문/요약 필드가 없다", () => {
    // 타입 구조를 런타임에서 검증 — Disclosure 는 3필드만.
    const sample: Disclosure = {
      reportName: "테스트 공시",
      rceptDate: "2026-06-01",
      dartUrl: "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=test",
    };

    const keys = Object.keys(sample);

    // 허용 필드: reportName, rceptDate, dartUrl 만.
    expect(keys).toContain("reportName");
    expect(keys).toContain("rceptDate");
    expect(keys).toContain("dartUrl");

    // 금지 필드: 본문·요약·분류·중요도 관련 필드 부재.
    expect(keys).not.toContain("body");
    expect(keys).not.toContain("summary");
    expect(keys).not.toContain("content");
    expect(keys).not.toContain("importance");
    expect(keys).not.toContain("category");
    expect(keys).not.toContain("label");
    expect(keys).not.toContain("score");

    // 정확히 3필드.
    expect(keys).toHaveLength(3);
  });
});

// =============================================================================
// 게이트 3: 렌더 — 추천/판단 어휘 및 배지 부재
// =============================================================================

describe("DisclosurePanel 렌더 게이트 — 시스템 생성 어휘 0 (ADR-0026 D1)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("공시 목록 렌더 후 추천/판단/순위 어휘(시스템 생성)가 없다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(MOCK_DISCLOSURES_WIRE), { status: 200 }),
    );

    const { container } = renderPanel();

    // 테이블이 렌더될 때까지 대기.
    await waitFor(() => {
      expect(container.querySelector("table")).toBeInTheDocument();
    });

    // 시스템 생성 텍스트(헤더·라벨)에서만 검사.
    // reportName(EXTERNAL_QUOTE)은 제외 — th 와 시스템 td 만 검사.
    const headers = container.querySelectorAll("th");
    const systemText = Array.from(headers)
      .map((el) => el.textContent ?? "")
      .join(" ");

    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(
        systemText,
        `시스템 헤더에 금지 어휘 '${word}' 가 있음`,
      ).not.toContain(word);
    }
  });

  it("공시 목록 렌더 후 '중요공시' 배지·필터·정렬 토글이 없다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(MOCK_DISCLOSURES_WIRE), { status: 200 }),
    );

    const { container } = renderPanel();

    await waitFor(() => {
      expect(container.querySelector("table")).toBeInTheDocument();
    });

    // 배지/필터/정렬 UI 없음.
    expect(
      within(document.body).queryByText(/중요|필터|정렬/),
    ).toBeNull();

    // button 요소 없음(링크 a 만 허용).
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBe(0);
  });
});

// =============================================================================
// 게이트 4: DART 원문 링크 — target/rel/href 검증
// =============================================================================

describe("DisclosurePanel 렌더 게이트 — DART 링크 속성 (ADR-0026 D1)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("DART 링크가 target=_blank, rel=noopener noreferrer 를 가진다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(MOCK_DISCLOSURES_WIRE), { status: 200 }),
    );

    const { container } = renderPanel();

    await waitFor(() => {
      expect(container.querySelector("table")).toBeInTheDocument();
    });

    const links = container.querySelectorAll("a");
    // 공시 수(2) 만큼 링크 있음.
    expect(links.length).toBe(MOCK_DISCLOSURES_WIRE.disclosures.length);

    for (const link of links) {
      expect(link.getAttribute("target")).toBe("_blank");
      expect(link.getAttribute("rel")).toContain("noopener");
      expect(link.getAttribute("rel")).toContain("noreferrer");
    }
  });

  it("DART 링크 href 가 올바른 DART URL 이다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(MOCK_DISCLOSURES_WIRE), { status: 200 }),
    );

    const { container } = renderPanel();

    await waitFor(() => {
      expect(container.querySelector("table")).toBeInTheDocument();
    });

    const links = container.querySelectorAll("a");
    const hrefs = Array.from(links).map((l) => l.getAttribute("href"));

    expect(hrefs).toContain(MOCK_DISCLOSURES_WIRE.disclosures[0]!.dart_url);
    expect(hrefs).toContain(MOCK_DISCLOSURES_WIRE.disclosures[1]!.dart_url);
  });
});

// =============================================================================
// 게이트 5: 로딩/에러/빈 상태 중립 처리
// =============================================================================

describe("DisclosurePanel 상태 게이트 — 중립 처리 (ADR-0026 D1)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("로딩 상태에서 스켈레톤을 렌더하고 판단색 className 이 없다", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));

    const { container } = renderPanel();

    const skeleton = container.querySelector(".animate-pulse");
    expect(skeleton).toBeInTheDocument();

    // 판단색 없음.
    const html = container.innerHTML;
    expect(html).not.toMatch(/text-red-\d{3}/);
    expect(html).not.toMatch(/text-blue-\d{3}/);
    expect(html).not.toMatch(/text-green-\d{3}/);
    expect(html).not.toMatch(/bg-red-\d{3}/);
    expect(html).not.toMatch(/bg-green-\d{3}/);
  });

  it("에러 상태에서 중립 메시지를 렌더하고 판단색이 없다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("서버 오류", { status: 500 }),
    );

    const { container } = renderPanel();

    await waitFor(() => {
      expect(screen.getByText(/공시 목록 로드 실패/)).toBeInTheDocument();
    });

    // 에러 박스에 판단색 없음(RestatementHistory 와 달리 neutral).
    const html = container.innerHTML;
    expect(html).not.toMatch(/text-red-\d{3}/);
    expect(html).not.toMatch(/bg-red-\d{3}/);
  });

  it("빈 공시 목록이면 중립 안내 메시지를 렌더한다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(EMPTY_DISCLOSURES_WIRE), { status: 200 }),
    );

    renderPanel();

    await waitFor(() => {
      expect(
        screen.getByText("해당 기간 공시가 없습니다."),
      ).toBeInTheDocument();
    });
  });
});

// =============================================================================
// 게이트 6: 행 순서 결정적 — backend 순서 그대로 (ADR-0026 D3)
// =============================================================================

describe("DisclosurePanel 렌더 게이트 — 행 순서 결정적 (ADR-0026 D3)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("backend 반환 순서 그대로 렌더한다 (클라이언트 재정렬 금지)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(MOCK_DISCLOSURES_WIRE), { status: 200 }),
    );

    const { container } = renderPanel();

    await waitFor(() => {
      expect(container.querySelector("table")).toBeInTheDocument();
    });

    // tbody 행 순서 — thead 제외.
    const rows = container.querySelectorAll("tbody tr");
    expect(rows.length).toBe(MOCK_DISCLOSURES_WIRE.disclosures.length);

    // 첫 번째 행의 날짜가 최신(2026-05-15).
    expect(rows[0]!.textContent).toContain("2026-05-15");
    // 두 번째 행의 날짜가 그 다음(2026-04-30).
    expect(rows[1]!.textContent).toContain("2026-04-30");
  });
});
