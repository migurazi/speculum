/**
 * Compare 페이지 E2E — AC-F-05.
 *
 * 시나리오:
 *   A. /compare 진입 → 헤더 + 종목 검색 combobox + 비교 실행 disabled + 빈 상태.
 *   B. 검색으로 2 종목 추가(칩) → 비교 실행 enable → 클릭 → mock 응답 grid 2 종목.
 *
 * 정책:
 *   - GET /api/stocks/search mock (검색 combobox) + GET /api/stocks/compare mock.
 *   - Compare 는 코드 직접 입력(text) 에서 StockSearch combobox + 칩 UI 로 전환됨
 *     (e722a5b). NavBar 에도 동일 StockSearch 가 있어 combobox 조작은 main 스코프.
 *
 * 관련:
 *   - app/compare/page.tsx
 *   - components/StockSearch.tsx
 *   - components/Compare/CompareGrid.tsx
 *   - M0_PLAN T38 / AC-F-05
 */

import { expect, test } from "@playwright/test";

import {
  mockCalendar,
  mockStockCompare,
  mockStockSearch,
  withConsent,
} from "./_fixtures";

test.describe("Compare page — AC-F-05", () => {
  test.beforeEach(async ({ page }) => {
    await withConsent(page);
    // 전역 CalendarBoundsSync 의 GET /api/calendar — 실 네트워크 시도 제거.
    await mockCalendar(page);
    await mockStockSearch(page);
    await mockStockCompare(page);
  });

  test("A. /compare 진입 → 헤더 + 검색 combobox + 빈 상태", async ({ page }) => {
    await page.goto("/compare");

    await expect(
      page.getByRole("heading", { name: "Compare", level: 1 }),
    ).toBeVisible();

    // 종목 검색 안내 라벨 + combobox — NavBar 의 동일 검색과 구분 위해 main 스코프.
    const main = page.getByRole("main");
    await expect(main.getByText("종목 검색으로 추가")).toBeVisible();
    await expect(main.getByRole("combobox")).toBeVisible();

    // 비교 실행 버튼 disabled (선택 0).
    await expect(
      page.getByRole("button", { name: "비교 실행" }),
    ).toBeDisabled();

    // 빈 선택 안내.
    await expect(
      main.getByText(/검색해서 비교할 종목을 추가하세요/),
    ).toBeVisible();
  });

  test("B. 2 종목 검색·추가 + 실행 → 2 종목 grid", async ({ page }) => {
    await page.goto("/compare");

    const main = page.getByRole("main");
    const search = main.getByRole("combobox");

    // 삼성전자: 이름으로 검색 → 드롭다운 항목 선택 → 칩 추가.
    await search.fill("삼성전자");
    await main.getByRole("option", { name: /삼성전자/ }).click();

    // SK하이닉스: 코드로 검색 → 드롭다운 항목 선택 → 칩 추가.
    await search.fill("000660");
    await main.getByRole("option", { name: /SK하이닉스/ }).click();

    const runButton = page.getByRole("button", { name: "비교 실행" });
    await expect(runButton).toBeEnabled({ timeout: 2_000 });
    await runButton.click();

    // mock 응답 — grid 의 종목 column header(th)에 두 종목 노출.
    // (칩에도 종목명이 있어 columnheader 로 grid 를 특정.)
    await expect(
      page.getByRole("columnheader", { name: /삼성전자/ }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: /SK하이닉스/ }),
    ).toBeVisible();
  });
});
