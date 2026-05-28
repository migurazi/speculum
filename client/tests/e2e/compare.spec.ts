/**
 * Compare 페이지 E2E — AC-F-05.
 *
 * 시나리오:
 *   A. /compare 진입 → 헤더 + 입력 안내 + 비교 실행 disabled.
 *   B. 2 종목 입력 → 비교 실행 enable → 클릭 → mock 응답 grid 2 종목 표시.
 *
 * 정책:
 *   - GET /api/stocks/compare mock — withConsent + mockStockCompare.
 *
 * 관련:
 *   - app/compare/page.tsx
 *   - components/Compare/CompareGrid.tsx
 *   - M0_PLAN T38 / AC-F-05
 */

import { expect, test } from "@playwright/test";

import { mockStockCompare, withConsent } from "./_fixtures";

test.describe("Compare page — AC-F-05", () => {
  test.beforeEach(async ({ page }) => {
    await withConsent(page);
    await mockStockCompare(page);
  });

  test("A. /compare 진입 → 헤더 + 입력 + 빈 상태", async ({ page }) => {
    await page.goto("/compare");

    await expect(
      page.getByRole("heading", { name: "Compare", level: 1 }),
    ).toBeVisible();

    // 종목코드 input label 노출.
    await expect(
      page.getByLabel("종목코드 (쉼표 구분, 2~6 개)"),
    ).toBeVisible();

    // 비교 실행 버튼 disabled (빈 입력).
    await expect(
      page.getByRole("button", { name: "비교 실행" }),
    ).toBeDisabled();

    // 빈 상태 안내.
    await expect(
      page.getByText('종목코드를 입력하고 "비교 실행" 을 누르세요'),
    ).toBeVisible();
  });

  test("B. 2 종목 입력 + 실행 → 2 종목 grid", async ({ page }) => {
    await page.goto("/compare");

    await page
      .getByLabel("종목코드 (쉼표 구분, 2~6 개)")
      .fill("005930,000660");

    const runButton = page.getByRole("button", { name: "비교 실행" });
    await expect(runButton).toBeEnabled({ timeout: 2_000 });
    await runButton.click();

    // mock 응답 — 삼성전자 + SK하이닉스 grid 에 노출.
    await expect(page.getByText("삼성전자")).toBeVisible();
    await expect(page.getByText("SK하이닉스")).toBeVisible();
  });
});
