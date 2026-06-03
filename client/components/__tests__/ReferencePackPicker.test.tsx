/**
 * ReferencePackPicker 컴포넌트 테스트 (ADR-0023 D8).
 *
 * 검증:
 *   1. 목록 렌더 — packSlug / version / factorCount / factor 이름 표시.
 *   2. 불러오기 — onLoad 콜백에 body(content_hash 제외 FactorPack) 전달.
 *   3. 빈 목록 — listEmpty 문구 표시.
 *   4. 시각 게이트 — 랭킹/추천 어휘 0, 모든 톤 상수 neutral 계열(No Advice / ADR-0022 D4).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import {
  ReferencePackPicker,
  REF_PACK_LOAD_BTN_CLASS,
  REF_PACK_ROW_CLASS,
  REF_PACK_FACTOR_CHIP_CLASS,
} from "@/components/Lab/ReferencePackPicker";
import type { FactorPack, ReferencePackItem } from "@/lib/api/factor-packs";
import { listReferencePacks } from "@/lib/api/factor-packs";
import { renderWithIntl } from "@/test-utils/intl";

// listReferencePacks 만 mock — 실제 fetch 없이 단위 테스트.
vi.mock("@/lib/api/factor-packs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/factor-packs")>();
  return {
    ...actual,
    listReferencePacks: vi.fn(),
  };
});

// ── fixture ─────────────────────────────────────────────────────────────────

const MOCK_PACK_BODY: FactorPack & { content_hash?: string } = {
  pack_slug: "community/speculum-reit-reference",
  version: "1.0.0",
  factors: [
    {
      canonical_id: "ffo-multiple:reit",
      uuid: "aaaa-1111",
      name: "FFO Multiple",
      description: "리츠 FFO 배수",
      unit: "ratio",
      tags: ["reit"],
      formula: { ast: { field: "ffo" }, inputs: ["ffo"] },
    },
    {
      canonical_id: "dividend-yield:reit",
      uuid: "bbbb-2222",
      name: "배당수익률",
      description: "리츠 배당수익률",
      unit: "percent",
      tags: ["reit"],
      formula: { ast: { field: "dividend" }, inputs: ["dividend"] },
    },
  ],
  citation: { title: "Speculum REIT Reference" },
  content_hash: "abc123seal",
};

const MOCK_ITEM: ReferencePackItem = {
  packSlug: "community/speculum-reit-reference",
  version: "1.0.0",
  factorCount: 2,
  body: MOCK_PACK_BODY,
};

// ── 헬퍼 ────────────────────────────────────────────────────────────────────

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderPicker(
  onLoad: (pack: FactorPack) => void = vi.fn(),
): void {
  const qc = makeClient();
  renderWithIntl(
    <QueryClientProvider client={qc}>
      <ReferencePackPicker onLoad={onLoad} />
    </QueryClientProvider>,
  );
}

// ── 개별 테스트 전 mock 초기화 ───────────────────────────────────────────────

beforeEach(() => {
  vi.resetAllMocks();
});

// ─────────────────────────────────────────────────────────────────────────────
// 목록 렌더
// ─────────────────────────────────────────────────────────────────────────────

describe("ReferencePackPicker 목록 렌더", () => {
  it("packSlug 와 version 을 표시한다", async () => {
    vi.mocked(listReferencePacks).mockResolvedValue({ packs: [MOCK_ITEM] });
    renderPicker();

    await screen.findByTestId("reference-pack-list");
    expect(screen.getByText("community/speculum-reit-reference")).toBeTruthy();
    expect(screen.getByText("1.0.0")).toBeTruthy();
  });

  it("factorCount 를 표시한다", async () => {
    vi.mocked(listReferencePacks).mockResolvedValue({ packs: [MOCK_ITEM] });
    renderPicker();

    await screen.findByTestId("reference-pack-list");
    expect(screen.getByText("2")).toBeTruthy();
  });

  it("factor 이름 chip 을 표시한다", async () => {
    vi.mocked(listReferencePacks).mockResolvedValue({ packs: [MOCK_ITEM] });
    renderPicker();

    await screen.findByTestId("reference-pack-list");
    expect(screen.getByText("FFO Multiple")).toBeTruthy();
    expect(screen.getByText("배당수익률")).toBeTruthy();
  });

  it("목록이 비어 있으면 listEmpty 문구를 표시한다", async () => {
    vi.mocked(listReferencePacks).mockResolvedValue({ packs: [] });
    renderPicker();

    await screen.findByTestId("reference-pack-empty");
    expect(
      screen.getByTestId("reference-pack-empty").textContent,
    ).toContain("참조 pack 이 없습니다");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 불러오기
// ─────────────────────────────────────────────────────────────────────────────

describe("ReferencePackPicker 불러오기", () => {
  it("불러오기 클릭 시 onLoad 콜백에 body 를 전달한다", async () => {
    vi.mocked(listReferencePacks).mockResolvedValue({ packs: [MOCK_ITEM] });

    const onLoad = vi.fn();
    renderPicker(onLoad);

    const slug = MOCK_ITEM.packSlug;
    await screen.findByTestId(`reference-pack-load-${slug}`);
    await userEvent.click(screen.getByTestId(`reference-pack-load-${slug}`));

    await waitFor(() => {
      expect(onLoad).toHaveBeenCalledOnce();
    });

    const received = onLoad.mock.calls[0]?.[0] as FactorPack & {
      content_hash?: string;
    };
    // body 에서 content_hash 를 제거하고 전달해야 한다.
    expect(received.pack_slug).toBe("community/speculum-reit-reference");
    expect(received.factors).toHaveLength(2);
    expect(received.content_hash).toBeUndefined();
  });

  it("onLoad 에 전달된 body 의 factors 가 원본 pack 과 동일하다", async () => {
    vi.mocked(listReferencePacks).mockResolvedValue({ packs: [MOCK_ITEM] });

    const onLoad = vi.fn();
    renderPicker(onLoad);

    const slug = MOCK_ITEM.packSlug;
    await screen.findByTestId(`reference-pack-load-${slug}`);
    await userEvent.click(screen.getByTestId(`reference-pack-load-${slug}`));

    await waitFor(() => expect(onLoad).toHaveBeenCalledOnce());

    const received = onLoad.mock.calls[0]?.[0] as FactorPack;
    expect(received.factors[0]?.canonical_id).toBe("ffo-multiple:reit");
    expect(received.factors[1]?.canonical_id).toBe("dividend-yield:reit");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 시각 게이트 — 랭킹/추천 어휘 0, 중립 톤 (No Advice / ADR-0022 D4)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * No Advice §2.2 / ADR-0022 D4 — 판단·추천·순위 어휘.
 * ReferencePackPicker 렌더에 이 단어가 등장하면 규정 위반.
 */
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

/**
 * 등락/판단 의미 색 토큰 — 톤 상수 및 className 에 등장 금지.
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

/** 컴포넌트가 export 하는 모든 톤 상수. */
const REF_PACK_TONE_CONSTANTS: ReadonlyArray<string> = [
  REF_PACK_LOAD_BTN_CLASS,
  REF_PACK_ROW_CLASS,
  REF_PACK_FACTOR_CHIP_CLASS,
];

describe("ReferencePackPicker 시각 게이트 (No Advice / ADR-0022 D4)", () => {
  it("톤 상수는 모두 neutral grayscale 계열을 포함한다", () => {
    for (const tone of REF_PACK_TONE_CONSTANTS) {
      expect(tone).toMatch(/neutral/);
    }
  });

  it("톤 상수에 등락/판단 의미색(빨강·파랑·녹색·색조)이 없다", () => {
    for (const tone of REF_PACK_TONE_CONSTANTS) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(tone).not.toMatch(new RegExp(`-${token}-`));
      }
    }
  });

  it("목록 렌더에 랭킹/추천/인기 어휘가 없다", async () => {
    vi.mocked(listReferencePacks).mockResolvedValue({ packs: [MOCK_ITEM] });
    renderPicker();

    const list = await screen.findByTestId("reference-pack-list");
    const text = list.textContent ?? "";

    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text).not.toContain(word);
    }
  });

  it("불러오기 버튼 className 에 판단 의미색이 없다", async () => {
    vi.mocked(listReferencePacks).mockResolvedValue({ packs: [MOCK_ITEM] });
    renderPicker();

    const slug = MOCK_ITEM.packSlug;
    const btn = await screen.findByTestId(`reference-pack-load-${slug}`);
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(btn.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });

  it("factor chip className 에 판단 의미색이 없다", async () => {
    vi.mocked(listReferencePacks).mockResolvedValue({ packs: [MOCK_ITEM] });
    renderPicker();

    const chip = await screen.findByTestId(
      "reference-factor-chip-ffo-multiple:reit",
    );
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(chip.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });

  it("행 className 에 판단 의미색이 없다", async () => {
    vi.mocked(listReferencePacks).mockResolvedValue({ packs: [MOCK_ITEM] });
    renderPicker();

    const slug = MOCK_ITEM.packSlug;
    const row = await screen.findByTestId(`reference-pack-row-${slug}`);
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(row.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });
});
