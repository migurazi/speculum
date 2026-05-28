/**
 * Watchlist 페이지 E2E — AC-F-06.
 *
 * 시나리오:
 *   A. /watchlist 진입 → mock folder list 응답 → "기본 관심 종목" 폴더 노출.
 *   B. default 폴더 auto-select → items list fetch → mock item ("장기 보유 후보"
 *      note) 노출.
 *
 * 정책:
 *   - GET /api/watchlists + GET /api/watchlists/{id}/items mock.
 *   - mutation (folder/item CRUD) 은 본 cycle scope 외 — read-only 검증만.
 *
 * 관련:
 *   - app/watchlist/page.tsx
 *   - components/Watchlist/FolderSidebar.tsx + ItemList.tsx
 *   - M0_PLAN T39 / AC-F-06
 */

import { expect, test } from "@playwright/test";

import { mockWatchlist, withConsent } from "./_fixtures";

test.describe("Watchlist page — AC-F-06", () => {
  test.beforeEach(async ({ page }) => {
    await withConsent(page);
    await mockWatchlist(page);
  });

  test("A. /watchlist → default 폴더 노출", async ({ page }) => {
    await page.goto("/watchlist");

    // mock folder name 노출 — FolderSidebar 의 button + ItemList header 양쪽에
    // 같은 이름이 나옴. 가장 명확한 element 인 H2 heading 으로 검증.
    await expect(
      page.getByRole("heading", { name: "기본 관심 종목", level: 2 }),
    ).toBeVisible();
    // sidebar 의 button (기본 폴더 표기 "(기본)") 도 함께 검증.
    await expect(
      page.getByRole("button", { name: /기본 관심 종목.*기본/ }),
    ).toBeVisible();
  });

  test("B. default 폴더 auto-select → item note 노출", async ({ page }) => {
    await page.goto("/watchlist");

    // default 자동 선택 → ItemList 가 items fetch → "장기 보유 후보" note 표시.
    await expect(page.getByText("장기 보유 후보")).toBeVisible({
      timeout: 5_000,
    });
  });
});
