/**
 * Community Pack 시각요소 게이트 — ADR-0028 D3 (No Advice / §2.3 큐레이션 금지).
 *
 * `no-forbidden-words`(eslint) / `check_forbidden_words.py` 는 **텍스트** 만 검사한다.
 * 목록·배지·버튼의 색·인기순위·큐레이션 신호 같은 **시각 요소** 는 별도 게이트가 필요.
 * custom-screen-visual-gate.test.tsx / PackLibrary.test.tsx 패턴 복제.
 *
 * 검증:
 *   1. COMMUNITY_BROWSER_* 톤 상수가 모두 grayscale(neutral|white) 계열.
 *   2. 톤 상수에 등락/판단 의미색(red/green 등)이 없음.
 *   3. VISIBILITY_BADGE_CLASS(PackLibrary) 가 grayscale 계열만.
 *   4. 목록 렌더에 인기/순위/다운로드/별점/Top-N/추천 어휘 없음.
 *   5. 정렬 토글 UI 없음(backend 순서 그대로).
 *   6. visibility 배지 className 에 판단색(red/green) 없음.
 *   7. import 완료 후 편집기에 반영(onImport 콜백).
 *   8. content_hash 불일치 시 에러 표시(fail-loud).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import {
  CommunityPackBrowser,
  COMMUNITY_BROWSER_ROW_CLASS,
  COMMUNITY_BROWSER_IMPORT_BTN_CLASS,
  COMMUNITY_BROWSER_BADGE_CLASS,
} from "@/components/Lab/CommunityPackBrowser";
import { VISIBILITY_BADGE_CLASS } from "@/components/Lab/PackLibrary";
import type { CommunityPack, FactorPack } from "@/lib/api/factor-packs";
import {
  getCommunityPackBody,
  importCheckPack,
  importPack,
  listCommunityPacks,
} from "@/lib/api/factor-packs";
import { renderWithIntl } from "@/test-utils/intl";

// API 함수 mock — 실제 fetch 없이 단위 테스트.
vi.mock("@/lib/api/factor-packs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/factor-packs")>();
  return {
    ...actual,
    listCommunityPacks: vi.fn(),
    getCommunityPackBody: vi.fn(),
    importCheckPack: vi.fn(),
    importPack: vi.fn(),
  };
});

// ── 등락/판단 의미색 토큰 목록 (custom-screen-visual-gate 와 동형) ──────────────

/**
 * 등락/판단 의미 색 토큰 — COMMUNITY_BROWSER 톤 상수 및 배지 className 에 등장 금지.
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

/** No Advice 큐레이션·판단·순위 어휘 — 렌더에 등장 금지. */
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
  "다운로드",
  "별점",
];

/** COMMUNITY_BROWSER 톤 상수 전체. */
const COMMUNITY_BROWSER_TONE_CONSTANTS: ReadonlyArray<string> = [
  COMMUNITY_BROWSER_ROW_CLASS,
  COMMUNITY_BROWSER_IMPORT_BTN_CLASS,
  COMMUNITY_BROWSER_BADGE_CLASS,
];

// ── fixture ──────────────────────────────────────────────────────────────────

const MOCK_COMMUNITY_PACK_A: CommunityPack = {
  packSlug: "user/value-pack",
  version: "0.1.0",
  factorCount: 3,
  createdAt: "2026-06-01T10:00:00Z",
  contentHash: "abc123def456",
  name: "가치 지표 pack",
  description: "PER/PBR/ROE 조합",
};

const MOCK_COMMUNITY_PACK_B: CommunityPack = {
  packSlug: "user/growth-pack",
  version: "0.2.0",
  factorCount: 2,
  createdAt: "2026-06-02T09:00:00Z",
  contentHash: "def456abc789",
  name: null,
  description: null,
};

const MOCK_FACTOR_PACK: FactorPack = {
  pack_slug: "user/value-pack",
  version: "0.1.0",
  factors: [],
  citation: { title: "가치 지표 pack" },
};

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderBrowser(
  onImport: (pack: FactorPack) => void = vi.fn(),
): void {
  renderWithIntl(
    <QueryClientProvider client={makeClient()}>
      <CommunityPackBrowser onImport={onImport} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
});

// ── 게이트 1: 톤 상수 grayscale 검증 ─────────────────────────────────────────

describe("Community Browser 톤 상수 게이트 (ADR-0028 D3)", () => {
  it("COMMUNITY_BROWSER 톤 상수는 모두 grayscale(neutral|white) 계열이다", () => {
    for (const tone of COMMUNITY_BROWSER_TONE_CONSTANTS) {
      expect(tone, `톤 상수 '${tone}' 가 neutral/white 이 아님`).toMatch(
        /neutral|white/,
      );
    }
  });

  it("COMMUNITY_BROWSER 톤 상수에 등락/판단 의미색이 없다", () => {
    for (const tone of COMMUNITY_BROWSER_TONE_CONSTANTS) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(
          tone,
          `톤 상수 '${tone}' 에 금지 색 토큰 '${token}' 가 있음`,
        ).not.toMatch(new RegExp(`-${token}-`));
      }
    }
  });

  it("VISIBILITY_BADGE_CLASS 가 grayscale(neutral) 계열이다", () => {
    expect(VISIBILITY_BADGE_CLASS, "VISIBILITY_BADGE_CLASS 가 neutral 이 아님").toMatch(
      /neutral/,
    );
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(
        VISIBILITY_BADGE_CLASS,
        `VISIBILITY_BADGE_CLASS 에 금지 색 토큰 '${token}' 가 있음`,
      ).not.toMatch(new RegExp(`-${token}-`));
    }
  });
});

// ── 게이트 2: 목록 렌더 가드레일 ─────────────────────────────────────────────

describe("Community Browser 렌더 게이트 — 큐레이션 0 (ADR-0028 D3)", () => {
  it("목록 렌더에 인기/순위/다운로드/별점/추천 어휘가 없다", async () => {
    vi.mocked(listCommunityPacks).mockResolvedValue({
      packs: [MOCK_COMMUNITY_PACK_A, MOCK_COMMUNITY_PACK_B],
      total: 2,
    });
    renderBrowser();

    const list = await screen.findByTestId("community-pack-list");
    const text = list.textContent ?? "";

    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text, `목록에 금지 어휘 '${word}' 가 있음`).not.toContain(word);
    }
  });

  it("목록에 정렬 토글 버튼/드롭다운이 없다 (backend 순서 유지)", async () => {
    vi.mocked(listCommunityPacks).mockResolvedValue({
      packs: [MOCK_COMMUNITY_PACK_A],
      total: 1,
    });
    renderBrowser();

    await screen.findByTestId("community-pack-list");

    // 정렬 관련 버튼 없음 — 사전순/인기순/최신순 토글 0.
    expect(screen.queryByRole("button", { name: /정렬|sort|순서/i })).toBeNull();
    // combobox(select) 에 정렬 어휘 없음.
    const selects = screen.queryAllByRole("combobox");
    for (const sel of selects) {
      expect(sel.textContent).not.toMatch(/정렬|sort|인기|순위/i);
    }
  });

  it("목록 행에 인기/순위 배지가 없다", async () => {
    vi.mocked(listCommunityPacks).mockResolvedValue({
      packs: [MOCK_COMMUNITY_PACK_A, MOCK_COMMUNITY_PACK_B],
      total: 2,
    });
    renderBrowser();

    await screen.findByTestId("community-pack-list");

    expect(screen.queryByText(/1위|순위|랭킹|Top|#1/i)).toBeNull();
    expect(screen.queryByText(/다운로드.*[0-9]/i)).toBeNull();
    expect(screen.queryByText(/별점|★|☆/i)).toBeNull();
  });

  it("목록 행 className 에 판단 의미색이 없다", async () => {
    vi.mocked(listCommunityPacks).mockResolvedValue({
      packs: [MOCK_COMMUNITY_PACK_A],
      total: 1,
    });
    renderBrowser();

    const row = await screen.findByTestId(
      `community-pack-row-${MOCK_COMMUNITY_PACK_A.packSlug}`,
    );

    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(row.className, `행 className 에 금지 색 '${token}' 있음`).not.toMatch(
        new RegExp(`-${token}-`),
      );
    }
  });

  it("import 버튼 className 에 판단 의미색이 없다", async () => {
    vi.mocked(listCommunityPacks).mockResolvedValue({
      packs: [MOCK_COMMUNITY_PACK_A],
      total: 1,
    });
    renderBrowser();

    const btn = await screen.findByTestId(
      `community-import-${MOCK_COMMUNITY_PACK_A.packSlug}`,
    );

    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(btn.className, `import 버튼에 금지 색 '${token}' 있음`).not.toMatch(
        new RegExp(`-${token}-`),
      );
    }
  });
});

// ── 게이트 3: pack_slug·version·factor_count·created_at 사실 표시 ─────────────

describe("Community Browser 렌더 게이트 — 사실 표시 (ADR-0028 D3)", () => {
  it("pack_slug 와 version 을 표시한다", async () => {
    vi.mocked(listCommunityPacks).mockResolvedValue({
      packs: [MOCK_COMMUNITY_PACK_A, MOCK_COMMUNITY_PACK_B],
      total: 2,
    });
    renderBrowser();

    await screen.findByTestId("community-pack-list");
    expect(screen.getByText("user/value-pack")).toBeTruthy();
    expect(screen.getByText("user/growth-pack")).toBeTruthy();
    expect(screen.getByText("0.1.0")).toBeTruthy();
    expect(screen.getByText("0.2.0")).toBeTruthy();
  });

  it("factor 수(factorCount)를 표시한다", async () => {
    vi.mocked(listCommunityPacks).mockResolvedValue({
      packs: [MOCK_COMMUNITY_PACK_A],
      total: 1,
    });
    renderBrowser();

    await screen.findByTestId("community-pack-list");
    expect(screen.getByText("3")).toBeTruthy();
  });

  it("name/description 이 있으면 사실로 표시한다", async () => {
    vi.mocked(listCommunityPacks).mockResolvedValue({
      packs: [MOCK_COMMUNITY_PACK_A],
      total: 1,
    });
    renderBrowser();

    await screen.findByTestId("community-pack-list");
    expect(screen.getByText("가치 지표 pack")).toBeTruthy();
    expect(screen.getByText("PER/PBR/ROE 조합")).toBeTruthy();
  });

  it("목록이 비어 있으면 listEmpty 문구를 표시한다", async () => {
    vi.mocked(listCommunityPacks).mockResolvedValue({ packs: [], total: 0 });
    renderBrowser();

    await screen.findByTestId("community-pack-empty");
    expect(screen.getByTestId("community-pack-empty").textContent).toContain(
      "공유된 pack 이 없습니다",
    );
  });
});

// ── 게이트 4: import 흐름 (D4 명시 매핑, content_hash 검증) ──────────────────

describe("Community Browser import 흐름 (ADR-0028 D4)", () => {
  it("충돌 없는 import — onImport 콜백이 clean pack 으로 호출된다", async () => {
    vi.mocked(listCommunityPacks).mockResolvedValue({
      packs: [MOCK_COMMUNITY_PACK_A],
      total: 1,
    });
    vi.mocked(getCommunityPackBody).mockResolvedValue({
      packSlug: MOCK_COMMUNITY_PACK_A.packSlug,
      version: MOCK_COMMUNITY_PACK_A.version,
      factorCount: MOCK_COMMUNITY_PACK_A.factorCount,
      createdAt: MOCK_COMMUNITY_PACK_A.createdAt,
      contentHash: MOCK_COMMUNITY_PACK_A.contentHash,
      name: MOCK_COMMUNITY_PACK_A.name,
      description: MOCK_COMMUNITY_PACK_A.description,
      pack: { ...MOCK_FACTOR_PACK, content_hash: MOCK_COMMUNITY_PACK_A.contentHash },
    });
    vi.mocked(importCheckPack).mockResolvedValue({
      valid: true,
      issues: [],
      conflicts: [],
      clean: ["custom:per"],
    });
    vi.mocked(importPack).mockResolvedValue({
      valid: true,
      issues: [],
      pack: { ...MOCK_FACTOR_PACK, content_hash: MOCK_COMMUNITY_PACK_A.contentHash },
      applied: [],
    });

    const onImport = vi.fn();
    renderBrowser(onImport);

    await screen.findByTestId(
      `community-import-${MOCK_COMMUNITY_PACK_A.packSlug}`,
    );
    await userEvent.click(
      screen.getByTestId(`community-import-${MOCK_COMMUNITY_PACK_A.packSlug}`),
    );

    await waitFor(() => {
      expect(onImport).toHaveBeenCalledOnce();
      // content_hash 는 제거된 clean pack.
      const called = onImport.mock.calls[0]?.[0] as FactorPack;
      expect(called).not.toHaveProperty("content_hash");
      expect(called.pack_slug).toBe(MOCK_FACTOR_PACK.pack_slug);
    });

    // 완료 메시지 표시.
    await screen.findByTestId("community-import-done");
  });

  it("content_hash 불일치 시 fail-loud 에러 표시 (ADR-0028 D4)", async () => {
    vi.mocked(listCommunityPacks).mockResolvedValue({
      packs: [MOCK_COMMUNITY_PACK_A],
      total: 1,
    });
    // body 의 hash 가 목록 hash 와 다름.
    vi.mocked(getCommunityPackBody).mockResolvedValue({
      packSlug: MOCK_COMMUNITY_PACK_A.packSlug,
      version: MOCK_COMMUNITY_PACK_A.version,
      factorCount: MOCK_COMMUNITY_PACK_A.factorCount,
      createdAt: MOCK_COMMUNITY_PACK_A.createdAt,
      contentHash: "tampered_hash_00000",
      name: null,
      description: null,
      pack: { ...MOCK_FACTOR_PACK, content_hash: "tampered_hash_00000" },
    });

    renderBrowser();

    await screen.findByTestId(
      `community-import-${MOCK_COMMUNITY_PACK_A.packSlug}`,
    );
    await userEvent.click(
      screen.getByTestId(`community-import-${MOCK_COMMUNITY_PACK_A.packSlug}`),
    );

    await waitFor(() => {
      const err = screen.getByTestId("community-import-error");
      expect(err.textContent).toContain("무결성 검증에 실패");
    });

    // import 함수는 호출되지 않아야 함.
    expect(importCheckPack).not.toHaveBeenCalled();
  });

  it("importCheckPack valid=false 시 에러 표시", async () => {
    vi.mocked(listCommunityPacks).mockResolvedValue({
      packs: [MOCK_COMMUNITY_PACK_A],
      total: 1,
    });
    vi.mocked(getCommunityPackBody).mockResolvedValue({
      packSlug: MOCK_COMMUNITY_PACK_A.packSlug,
      version: MOCK_COMMUNITY_PACK_A.version,
      factorCount: MOCK_COMMUNITY_PACK_A.factorCount,
      createdAt: MOCK_COMMUNITY_PACK_A.createdAt,
      contentHash: MOCK_COMMUNITY_PACK_A.contentHash,
      name: null,
      description: null,
      pack: { ...MOCK_FACTOR_PACK, content_hash: MOCK_COMMUNITY_PACK_A.contentHash },
    });
    vi.mocked(importCheckPack).mockResolvedValue({
      valid: false,
      issues: [{ stage: "schema", message: "invalid pack" }],
      conflicts: [],
      clean: [],
    });

    renderBrowser();

    await screen.findByTestId(
      `community-import-${MOCK_COMMUNITY_PACK_A.packSlug}`,
    );
    await userEvent.click(
      screen.getByTestId(`community-import-${MOCK_COMMUNITY_PACK_A.packSlug}`),
    );

    await waitFor(() => {
      expect(screen.getByTestId("community-import-error").textContent).toContain(
        "invalid pack",
      );
    });
  });
});
