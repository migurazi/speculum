/**
 * 동의 모달 E2E — AC-F-01 + AC-L-01 + Momus M0 review V2 fix 검증.
 *
 * 시나리오 (T46 V2 fix 후):
 *   A. 첫 진입 → 동의 모달 표시 (3 체크박스 모두 unchecked, button disabled).
 *   B. 모든 체크박스 체크 → button enabled → 클릭 → 모달 닫힘 + localStorage v2.
 *   C. 일부만 체크 → button 여전히 disabled (개인정보보호법 제22조 별도 동의).
 *   D. 동의 후 재진입 → 모달 미표시.
 *   E. blocking — Escape 으로 dismiss 안 됨.
 *   F. /privacy /terms /disclaimer link 노출.
 *
 * 정책:
 *   - localStorage key = "speculum-consent-v2" (v1 사용자도 재동의 강제).
 *   - 영구화 record = JSON ConsentRecord (version: 2, privacy / overseasTransfer
 *     / age14 / grantedAt).
 *
 * 관련:
 *   - components/ConsentModal.tsx (T46 V2 fix)
 *   - lib/hooks/useConsent.ts (v2 key + 3 항목 별도 동의)
 *   - ADR-0006 D6.1 / D6.4 / D6.5 / D7.1
 *   - 개인정보보호법 제22조 별도 동의
 */

import { expect, test } from "@playwright/test";

const CONSENT_KEY = "speculum-consent-v2";

test.describe("ConsentModal — AC-F-01 / AC-L-01 / Momus V2", () => {
  test.beforeEach(async ({ context }) => {
    await context.clearCookies();
  });

  test("A. 첫 진입 → 모달 + 3 unchecked + button disabled", async ({ page }) => {
    await page.goto("/");

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("Speculum 서비스 이용 안내")).toBeVisible();

    // 3 체크박스 모두 unchecked.
    await expect(
      page.getByRole("checkbox", { name: "개인정보 수집·이용 동의 (필수)" }),
    ).not.toBeChecked();
    await expect(
      page.getByRole("checkbox", { name: "개인정보 국외 이전 동의 (필수)" }),
    ).not.toBeChecked();
    await expect(
      page.getByRole("checkbox", { name: "만 14세 이상 확인 (필수)" }),
    ).not.toBeChecked();

    // 모든 체크박스 미체크 시 button disabled.
    await expect(
      page.getByRole("button", { name: "동의하고 시작" }),
    ).toBeDisabled();
  });

  test("B. 3 체크박스 모두 체크 → 동의 → 모달 닫힘 + localStorage v2", async ({
    page,
  }) => {
    await page.goto("/");

    await page
      .getByRole("checkbox", { name: "개인정보 수집·이용 동의 (필수)" })
      .check();
    await page
      .getByRole("checkbox", { name: "개인정보 국외 이전 동의 (필수)" })
      .check();
    await page
      .getByRole("checkbox", { name: "만 14세 이상 확인 (필수)" })
      .check();

    const button = page.getByRole("button", { name: "동의하고 시작" });
    await expect(button).toBeEnabled();
    await button.click();

    // 모달 해제.
    await expect(page.getByRole("dialog")).toBeHidden();

    // localStorage v2 record — JSON 형식.
    const raw = await page.evaluate(
      (key) => window.localStorage.getItem(key),
      CONSENT_KEY,
    );
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed.version).toBe(2);
    expect(parsed.privacy).toBe(true);
    expect(parsed.overseasTransfer).toBe(true);
    expect(parsed.age14).toBe(true);
    expect(typeof parsed.grantedAt).toBe("string");
  });

  test("C. 2 개만 체크 → button 여전히 disabled (제22조 별도 동의)", async ({
    page,
  }) => {
    await page.goto("/");

    await page
      .getByRole("checkbox", { name: "개인정보 수집·이용 동의 (필수)" })
      .check();
    await page
      .getByRole("checkbox", { name: "개인정보 국외 이전 동의 (필수)" })
      .check();
    // age14 미체크.

    await expect(
      page.getByRole("button", { name: "동의하고 시작" }),
    ).toBeDisabled();
  });

  test("D. 동의 후 재진입 시 모달 미표시", async ({ page }) => {
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

    await page.goto("/");
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });

  test("E. blocking — Escape 으로 dismiss 안 됨", async ({ page }) => {
    await page.goto("/");

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(dialog).toBeVisible();
  });

  test("F. /privacy /terms /disclaimer link 노출", async ({ page }) => {
    await page.goto("/");

    const dialog = page.getByRole("dialog");
    // 새 탭 open 이라 클릭 후 navigation 검증은 복잡 — link 존재 + href 만.
    const privacyLink = dialog.getByRole("link", { name: "개인정보처리방침" });
    await expect(privacyLink).toBeVisible();
    await expect(privacyLink).toHaveAttribute("href", "/privacy");

    const termsLink = dialog.getByRole("link", { name: "이용약관" });
    await expect(termsLink).toBeVisible();
    await expect(termsLink).toHaveAttribute("href", "/terms");

    const disclaimerLink = dialog.getByRole("link", { name: "면책조항" });
    await expect(disclaimerLink).toBeVisible();
    await expect(disclaimerLink).toHaveAttribute("href", "/disclaimer");
  });
});

test.describe("Privacy / Terms / Disclaimer 페이지 sanity", () => {
  test("/privacy 진입 → heading", async ({ page }) => {
    await page.goto("/privacy");
    await expect(
      page.getByRole("heading", { name: "개인정보처리방침", level: 1 }),
    ).toBeVisible();
  });

  test("/terms 진입 → heading", async ({ page }) => {
    await page.goto("/terms");
    await expect(
      page.getByRole("heading", { name: "이용약관", level: 1 }),
    ).toBeVisible();
  });

  test("/disclaimer 진입 → heading", async ({ page }) => {
    await page.goto("/disclaimer");
    await expect(
      page.getByRole("heading", { name: "면책조항", level: 1 }),
    ).toBeVisible();
  });
});
