/**
 * Screener 페이지 E2E — AC-F-03.
 *
 * 시나리오:
 *   A. 진입 → 조건 빌더 + factor input 노출 + Run 버튼 disabled.
 *   B. 조건 + factors 입력 → Run enable → 클릭 → mock 결과 + Save Run 노출.
 *
 * 정책:
 *   - POST /api/screen 은 page.route() 로 mock — 실 backend 의존 0.
 *   - 동의 모달 회피 위해 withConsent 사전 주입.
 *   - selector 는 ConditionBuilder 의 aria-label ("조건 N factor" / "조건 N 값")
 *     기반 — implementation 변경 시 본 spec 도 함께 update.
 *
 * 관련:
 *   - app/screener/page.tsx
 *   - components/Screener/ConditionBuilder.tsx (aria-label 정의)
 *   - M0_PLAN T36 / AC-F-03
 */

import { expect, test } from "@playwright/test";

import { mockScreenRun, withConsent } from "./_fixtures";

test.describe("Screener page — AC-F-03", () => {
  test.beforeEach(async ({ page }) => {
    await withConsent(page);
    await mockScreenRun(page);
  });

  test("A. 진입 → 조건 빌더 + factor input + Run disabled", async ({
    page,
  }) => {
    await page.goto("/screener");

    await expect(
      page.getByRole("heading", { name: "Screener", level: 1 }),
    ).toBeVisible();

    // 조건 추가 버튼 노출 + 빈 상태 안내.
    await expect(
      page.getByRole("button", { name: "조건 추가" }),
    ).toBeVisible();
    await expect(
      page.getByText("조건을 한 개 이상 추가해야 실행할 수 있습니다"),
    ).toBeVisible();

    // 결과 표시 factor input 노출.
    await expect(
      page.getByLabel("결과 표시 factor (쉼표 구분)"),
    ).toBeVisible();

    // Run 버튼 disabled.
    await expect(
      page.getByRole("button", { name: "Screener 실행" }),
    ).toBeDisabled();
  });

  test("B. 조건 + factors 입력 → Run → mock 결과 + Save Run", async ({
    page,
  }) => {
    await page.goto("/screener");

    // 조건 1 추가 — aria-label 로 정확 매칭.
    await page.getByRole("button", { name: "조건 추가" }).click();
    await page
      .getByLabel("조건 1 factor")
      .fill("per:ttm-consolidated-ifrs");
    await page.getByLabel("조건 1 값").fill("15");

    // selected_factors.
    await page
      .getByLabel("결과 표시 factor (쉼표 구분)")
      .fill("per:ttm-consolidated-ifrs");

    // Run 버튼 enable 대기 + 클릭.
    const runButton = page.getByRole("button", { name: "Screener 실행" });
    await expect(runButton).toBeEnabled({ timeout: 2_000 });
    await runButton.click();

    // mock 응답 result_codes = ["005930", "000660", "035420"].
    await expect(page.getByText("총 3 종목 매칭")).toBeVisible();
    await expect(page.getByText("005930")).toBeVisible();
    await expect(page.getByText("000660")).toBeVisible();
    await expect(page.getByText("035420")).toBeVisible();

    // Save Run 버튼 노출 (snapshot freeze 성공).
    await expect(
      page.getByRole("button", { name: /Save Run|저장/i }),
    ).toBeVisible();
  });
});
