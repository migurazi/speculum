/**
 * PackLibrary 컴포넌트 테스트 (ADR-0022 D9 / T69).
 *
 * 검증:
 *   1. 저장 목록 렌더 — packSlug / version / factorCount / createdAt 표시.
 *   2. 불러오기 — getSavedPack 호출 → onLoad 콜백 전달.
 *   3. 시각 게이트 — 랭킹/추천 어휘 0, 모든 톤 상수 neutral 계열.
 *   4. 409 충돌 에러 표시 — "slug·버전의 다른 정의" 문구 포함.
 *   5. 빈 목록 — listEmpty 문구 표시.
 *   6. 삭제 — deleteSavedPack 호출 후 목록 갱신.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { PackLibrary } from "@/components/Lab/PackLibrary";
import { ApiError } from "@/lib/api/client";
import type { FactorPack, SavedPackOut } from "@/lib/api/factor-packs";
import {
  deleteSavedPack,
  getSavedPack,
  listMyPacks,
  savePack,
} from "@/lib/api/factor-packs";
import { renderWithIntl } from "@/test-utils/intl";

// API 함수 전체 mock — 실제 fetch 없이 단위 테스트.
vi.mock("@/lib/api/factor-packs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/factor-packs")>();
  return {
    ...actual,
    savePack: vi.fn(),
    listMyPacks: vi.fn(),
    getSavedPack: vi.fn(),
    deleteSavedPack: vi.fn(),
  };
});

// ── fixture ────────────────────────────────────────────────────────────────────

const MOCK_PACK: FactorPack = {
  pack_slug: "user/my-pack",
  version: "0.1.0",
  factors: [],
  citation: { title: "test" },
};

const SAVED_ITEM_1: SavedPackOut = {
  id: "aaa-111",
  packSlug: "user/my-pack",
  version: "0.1.0",
  contentHash: "abc123",
  factorCount: 3,
  createdAt: "2026-06-01T12:00:00Z",
};

const SAVED_ITEM_2: SavedPackOut = {
  id: "bbb-222",
  packSlug: "user/other-pack",
  version: "0.2.0",
  contentHash: "def456",
  factorCount: 5,
  createdAt: "2026-06-02T09:30:00Z",
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

function renderLibrary(
  onLoad: (pack: FactorPack) => void = vi.fn(),
  client?: QueryClient,
): QueryClient {
  const qc = client ?? makeClient();
  renderWithIntl(
    <QueryClientProvider client={qc}>
      <PackLibrary pack={MOCK_PACK} onLoad={onLoad} />
    </QueryClientProvider>,
  );
  return qc;
}

// ── 개별 테스트 전 mock 초기화 ─────────────────────────────────────────────

beforeEach(() => {
  vi.resetAllMocks();
});

// ──────────────────────────────────────────────────────────────────────────────
// 저장 목록 렌더
// ──────────────────────────────────────────────────────────────────────────────

describe("PackLibrary 저장 목록 렌더", () => {
  it("목록이 있으면 packSlug 와 version 을 표시한다", async () => {
    vi.mocked(listMyPacks).mockResolvedValue({ items: [SAVED_ITEM_1, SAVED_ITEM_2] });
    renderLibrary();

    await screen.findByTestId("pack-library-table");
    expect(screen.getByText("user/my-pack")).toBeTruthy();
    expect(screen.getByText("user/other-pack")).toBeTruthy();
    expect(screen.getByText("0.1.0")).toBeTruthy();
    expect(screen.getByText("0.2.0")).toBeTruthy();
  });

  it("factorCount 를 표시한다", async () => {
    vi.mocked(listMyPacks).mockResolvedValue({ items: [SAVED_ITEM_1] });
    renderLibrary();

    await screen.findByTestId("pack-library-table");
    // factorCount=3 이 셀에 표시됨.
    expect(screen.getByText("3")).toBeTruthy();
  });

  it("목록이 비어 있으면 listEmpty 문구를 표시한다", async () => {
    vi.mocked(listMyPacks).mockResolvedValue({ items: [] });
    renderLibrary();

    await screen.findByTestId("pack-library-empty");
    expect(screen.getByTestId("pack-library-empty").textContent).toContain(
      "저장된 pack 이 없습니다",
    );
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// 불러오기
// ──────────────────────────────────────────────────────────────────────────────

describe("PackLibrary 불러오기", () => {
  it("불러오기 클릭 시 getSavedPack 을 호출하고 onLoad 콜백을 실행한다", async () => {
    vi.mocked(listMyPacks).mockResolvedValue({ items: [SAVED_ITEM_1] });
    vi.mocked(getSavedPack).mockResolvedValue(MOCK_PACK);

    const onLoad = vi.fn();
    renderLibrary(onLoad);

    await screen.findByTestId(`pack-load-${SAVED_ITEM_1.id}`);
    await userEvent.click(screen.getByTestId(`pack-load-${SAVED_ITEM_1.id}`));

    await waitFor(() => {
      expect(getSavedPack).toHaveBeenCalledWith(SAVED_ITEM_1.id);
      expect(onLoad).toHaveBeenCalledWith(MOCK_PACK);
    });
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// 삭제
// ──────────────────────────────────────────────────────────────────────────────

describe("PackLibrary 삭제", () => {
  it("삭제 클릭 시 deleteSavedPack 을 호출한다", async () => {
    vi.mocked(listMyPacks).mockResolvedValue({ items: [SAVED_ITEM_1] });
    vi.mocked(deleteSavedPack).mockResolvedValue(undefined);

    renderLibrary();

    await screen.findByTestId(`pack-delete-${SAVED_ITEM_1.id}`);
    await userEvent.click(screen.getByTestId(`pack-delete-${SAVED_ITEM_1.id}`));

    await waitFor(() => {
      expect(deleteSavedPack).toHaveBeenCalledWith(SAVED_ITEM_1.id);
    });
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// 저장 에러 — 409 충돌
// ──────────────────────────────────────────────────────────────────────────────

describe("PackLibrary 저장 409 충돌 에러", () => {
  it("savePack 이 409 를 던지면 충돌 에러 문구를 표시한다", async () => {
    vi.mocked(listMyPacks).mockResolvedValue({ items: [] });
    vi.mocked(savePack).mockRejectedValue(
      new ApiError(409, "API 409 Conflict", ""),
    );

    renderLibrary();

    // 빈 목록 표시 대기.
    await screen.findByTestId("pack-library-empty");

    const saveBtn = screen.getByRole("button", { name: "현재 Pack 저장" });
    await userEvent.click(saveBtn);

    await waitFor(() => {
      const errEl = screen.getByTestId("pack-library-save-error");
      expect(errEl.textContent).toContain("slug·버전의 다른 정의");
    });
  });

  it("savePack 이 422 를 던지면 유효하지 않다는 에러 문구를 표시한다", async () => {
    vi.mocked(listMyPacks).mockResolvedValue({ items: [] });
    vi.mocked(savePack).mockRejectedValue(
      new ApiError(422, "API 422 Unprocessable", ""),
    );

    renderLibrary();
    await screen.findByTestId("pack-library-empty");

    await userEvent.click(screen.getByRole("button", { name: "현재 Pack 저장" }));

    await waitFor(() => {
      const errEl = screen.getByTestId("pack-library-save-error");
      expect(errEl.textContent).toContain("유효하지 않습니다");
    });
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// 시각 게이트 — 랭킹/추천 어휘 0, 중립 톤
// ──────────────────────────────────────────────────────────────────────────────

/**
 * No Advice §2.2 / ADR-0022 D4 — 판단·추천·순위 어휘.
 * PackLibrary 렌더에 이 단어가 등장하면 규정 위반.
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
 * 등락/판단 의미 색 토큰 — PackLibrary 컴포넌트 className 에 등장 금지.
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

describe("PackLibrary 시각 게이트 (No Advice / ADR-0022 D4)", () => {
  it("목록 렌더에 랭킹/추천/인기 어휘가 없다", async () => {
    vi.mocked(listMyPacks).mockResolvedValue({ items: [SAVED_ITEM_1, SAVED_ITEM_2] });
    renderLibrary();

    const table = await screen.findByTestId("pack-library-table");
    const text = table.textContent ?? "";

    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text).not.toContain(word);
    }
  });

  it("목록 테이블 행의 className 에 판단 의미색이 없다", async () => {
    vi.mocked(listMyPacks).mockResolvedValue({ items: [SAVED_ITEM_1] });
    renderLibrary();

    await screen.findByTestId(`pack-row-${SAVED_ITEM_1.id}`);
    const row = screen.getByTestId(`pack-row-${SAVED_ITEM_1.id}`);
    const className = row.className;

    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(className).not.toMatch(new RegExp(`-${token}-`));
    }
  });

  it("불러오기 버튼 className 에 판단 의미색이 없다", async () => {
    vi.mocked(listMyPacks).mockResolvedValue({ items: [SAVED_ITEM_1] });
    renderLibrary();

    const btn = await screen.findByTestId(`pack-load-${SAVED_ITEM_1.id}`);
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(btn.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });

  it("삭제 버튼 className 에 판단 의미색이 없다", async () => {
    vi.mocked(listMyPacks).mockResolvedValue({ items: [SAVED_ITEM_1] });
    renderLibrary();

    const btn = await screen.findByTestId(`pack-delete-${SAVED_ITEM_1.id}`);
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(btn.className).not.toMatch(new RegExp(`-${token}-`));
    }
  });
});
