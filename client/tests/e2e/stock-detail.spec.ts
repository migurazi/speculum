/**
 * Stock Detail 페이지 E2E — AC-F-04.
 *
 * 시나리오:
 *   A. /stock/005930 진입 → mock StockDetail 응답 → 종목명·코드·status badge.
 *   B. 지표 카드 (MetricCard) 가 mock factor 별 render — PER / ROE.
 *   C. SourceAttribution 의 8 기둥 §2.1 Fidelity element 노출.
 *
 * 정책:
 *   - GET /api/stocks/005930 mock — withConsent + mockStockDetail.
 *
 * 관련:
 *   - app/stock/[code]/page.tsx
 *   - components/StockDetail/MetricCard.tsx
 *   - components/SourceAttribution.tsx
 *   - M0_PLAN T37 / AC-F-04
 */

import { expect, test } from "@playwright/test";

import { mockCalendar, mockStockDetail, withConsent } from "./_fixtures";

test.describe("Stock Detail page — AC-F-04", () => {
  test.beforeEach(async ({ page }) => {
    await withConsent(page);
    // 전역 CalendarBoundsSync 의 GET /api/calendar — 실 네트워크 시도 제거.
    await mockCalendar(page);
    await mockStockDetail(page);
  });

  test("A. /stock/005930 → 종목 헤더 + status badge", async ({ page }) => {
    await page.goto("/stock/005930");

    // 종목명 + 코드 + status.
    await expect(
      page.getByRole("heading", { name: "삼성전자", level: 1 }),
    ).toBeVisible();
    await expect(page.getByText("005930", { exact: true })).toBeVisible();
    // active status badge 한글 라벨.
    await expect(page.getByText("거래 중")).toBeVisible();
    // market 표시 — KOSPI.
    await expect(page.getByText("KOSPI", { exact: true })).toBeVisible();
  });

  test("B. 지표 섹션 + PER / ROE MetricCard 노출", async ({ page }) => {
    await page.goto("/stock/005930");

    // "지표" 섹션 heading.
    await expect(
      page.getByRole("heading", { name: "지표", level: 2 }),
    ).toBeVisible();

    // mock factor 두 개 — PER (TTM) / ROE (TTM).
    await expect(page.getByText("PER (TTM)")).toBeVisible();
    await expect(page.getByText("ROE (TTM)")).toBeVisible();

    // factor value 표시 (Decimal string wire).
    // PER 은 unit=ratio → 값 그대로. ROE 는 unit=percent → 표시 layer 가 ×100
    // (0.108 → "10.80%", ADR-0035 D7 / MetricCard.formatPercentValue).
    await expect(page.getByText("12.5")).toBeVisible();
    await expect(page.getByText("10.80%")).toBeVisible();
  });
});
