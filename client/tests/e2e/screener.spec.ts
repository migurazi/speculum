/**
 * Screener 페이지 E2E — AC-F-03.
 *
 * 시나리오:
 *   A. 진입 → 조건 빌더 + 결과표시 factor 체크박스 노출 + Run 버튼 disabled.
 *   B. 조건(factor select + 값) + 표시 factor 체크박스 선택 → Run enable → 클릭 →
 *      mock 결과 + Save Run 노출.
 *
 * 정책:
 *   - POST /api/screen 은 page.route() 로 mock — 실 backend 의존 0.
 *   - GET /api/factors mock — ConditionBuilder select + 결과표시 체크박스 채움.
 *     (factor 입력이 text → /api/factors 드롭다운/체크박스로 전환됨.)
 *   - 동의 모달 회피 위해 withConsent 사전 주입.
 *   - selector 는 ConditionBuilder 의 aria-label ("조건 N factor" / "조건 N 값")
 *     기반 — implementation 변경 시 본 spec 도 함께 update.
 *
 * 관련:
 *   - app/screener/page.tsx
 *   - components/Screener/ConditionBuilder.tsx (factor select aria-label 정의)
 *   - M0_PLAN T36 / AC-F-03
 */

import { expect, test } from "@playwright/test";

import { mockCalendar, mockFactors, mockScreenRun, withConsent } from "./_fixtures";

test.describe("Screener page — AC-F-03", () => {
  test.beforeEach(async ({ page }) => {
    await withConsent(page);
    // 전역 CalendarBoundsSync 의 GET /api/calendar — 실 네트워크 시도 제거.
    await mockCalendar(page);
    await mockFactors(page);
    await mockScreenRun(page);
  });

  test("A. 진입 → 조건 빌더 + 표시 factor 체크박스 + Run disabled", async ({
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

    // 결과 표시 factor — mock /api/factors 로 체크박스 목록 렌더.
    // exact — sr-only <legend>"결과 표시 factor 선택"</legend> 과의 중복 매칭 회피.
    await expect(
      page.getByText("결과 표시 factor", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("checkbox", { name: "PER (TTM)" }),
    ).toBeVisible();

    // Run 버튼 disabled (조건 0 + factor 미선택).
    await expect(
      page.getByRole("button", { name: "Screener 실행" }),
    ).toBeDisabled();
  });

  test("B. 조건 + 표시 factor 선택 → Run → mock 결과 + Save Run", async ({
    page,
  }) => {
    await page.goto("/screener");

    // 조건 1 추가 — aria-label 로 정확 매칭.
    await page.getByRole("button", { name: "조건 추가" }).click();
    // factor 는 /api/factors 기반 select — value=canonical_id 로 선택.
    await page
      .getByLabel("조건 1 factor")
      .selectOption("per:ttm-consolidated-ifrs");
    await page.getByLabel("조건 1 값").fill("15");

    // 결과 표시 factor — 체크박스 선택 (selected_factors ≥ 1 이어야 실행 가능).
    await page.getByRole("checkbox", { name: "PER (TTM)" }).check();

    // Run 버튼 enable 대기 + 클릭.
    const runButton = page.getByRole("button", { name: "Screener 실행" });
    await expect(runButton).toBeEnabled({ timeout: 2_000 });
    await runButton.click();

    // mock 응답 result_codes = ["005930", "000660", "035420"], total = 3.
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
