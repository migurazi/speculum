/**
 * Home 페이지 sanity E2E — AC-P-03 (Active Inspection) 검증.
 *
 * 시나리오:
 *   A. 홈 진입 (동의 후) → NavBar + DisclaimerFooter + Watchlist placeholder.
 *   B. NavBar 4 link 존재 (Screener / Stocks / Compare / Watchlist).
 *   C. 빠른 시작 navigation 4 card 존재.
 *   D. 추천 위젯 / TOP N 종목 / 의제 설정 위젯 미존재 — Active Inspection.
 *
 * 사전 조건:
 *   - 모든 test 가 사전 동의 상태 — `addInitScript` 로 localStorage 주입.
 *     동의 모달 자체는 별도 spec (consent-modal.spec.ts).
 *
 * 관련:
 *   - app/page.tsx (홈 페이지)
 *   - components/NavBar.tsx
 *   - components/DisclaimerFooter.tsx
 *   - ADR-0010 (홈 화면 정체성)
 *   - 8 기둥 §2.3 (Active Inspection)
 */

import { expect, test } from "@playwright/test";

const CONSENT_KEY = "speculum-consent-v2";

// 모든 test 가 동의 완료 상태에서 시작 — consent-modal.spec.ts 의 흐름과 분리.
// useConsent v2 schema 의 JSON record 주입 (T46 V2 fix 후).
test.beforeEach(async ({ page }) => {
  await page.addInitScript((key) => {
    window.localStorage.setItem(
      key,
      JSON.stringify({
        version: 2,
        privacy: true,
        overseasTransfer: true,
        age14: true,
        grantedAt: "2026-05-28T00:00:00Z",
      }),
    );
  }, CONSENT_KEY);
});

test.describe("HomePage — AC-P-03 / ADR-0010", () => {
  test("A. 홈 진입 → NavBar + DisclaimerFooter + Watchlist placeholder", async ({
    page,
  }) => {
    await page.goto("/");

    // NavBar 의 Speculum 로고.
    await expect(page.getByRole("link", { name: "Speculum" })).toBeVisible();

    // Watchlist placeholder section (홈의 핵심 element).
    await expect(
      page.getByRole("heading", { name: "내 관심 종목" }),
    ).toBeVisible();

    // DisclaimerFooter 본문 — 첫 문장.
    await expect(
      page.getByText("본 도구는 정보 제공 목적의 정량 데이터 탐색기"),
    ).toBeVisible();
  });

  test("B. NavBar 4 link 노출", async ({ page }) => {
    await page.goto("/");

    const nav = page.getByRole("navigation", { name: "주 navigation" });
    await expect(nav.getByRole("link", { name: "Screener" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Stocks" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Compare" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Watchlist" })).toBeVisible();
  });

  test("C. 빠른 시작 navigation 4 card", async ({ page }) => {
    await page.goto("/");

    // "빠른 시작" section heading.
    await expect(
      page.getByRole("heading", { name: "빠른 시작" }),
    ).toBeVisible();

    // 4 card link (NavBar 의 4 link 와 중복되나 본 cards 는 main content 안).
    const main = page.getByRole("main");
    await expect(main.getByRole("link", { name: /Screener/ })).toBeVisible();
    await expect(main.getByRole("link", { name: /종목 조회/ })).toBeVisible();
    await expect(main.getByRole("link", { name: /Compare/ })).toBeVisible();
    await expect(main.getByRole("link", { name: /Watchlist/ })).toBeVisible();
  });

  test("D. AC-P-03 — 추천 / TOP N / 인기 위젯 미존재 (Active Inspection)", async ({
    page,
  }) => {
    await page.goto("/");

    // ADR-0010 D2 의 금지 위젯 패턴 — 일반적인 추천 시스템 라벨 미사용.
    // 사용자 인지 가능 텍스트로 검증 (정확한 위젯 제거 회귀 가드).
    const main = page.getByRole("main");
    await expect(main.getByText(/추천/i)).toHaveCount(0);
    await expect(main.getByText(/TOP \d+/i)).toHaveCount(0);
    await expect(main.getByText(/인기 종목/i)).toHaveCount(0);
    // "오늘의 종목" 같은 의제 설정 키워드. ADR-0010 D2 의 금지 예시.
    await expect(main.getByText(/오늘의 종목/i)).toHaveCount(0);
  });
});
