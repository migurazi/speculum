/**
 * E2E 공통 fixture / mock helper — 4 뷰 spec 의 backend route mocking.
 *
 * 설계:
 *   - `page.route()` 로 backend HTTP intercept → fixture JSON 반환. E2E 가
 *     실 backend 의존 0 (CI 비용 + flakiness 회피). 의미는 "frontend 가
 *     backend wire schema 를 정확히 소비하는가" 만 검증.
 *   - 본 모듈은 helper 만 — 각 spec 이 호출 (fixture 종속 회피).
 *   - consent 사전 주입은 `withConsent(page)` 한 줄로 — 4 뷰 모두 동의
 *     완료 가정.
 *
 * 정책:
 *   - mock 응답의 schema 는 backend Pydantic 과 1:1. drift 시 본 fixture 도
 *     update — CONTRIBUTING 의 4.2 wire schema 변경 절차 따름.
 *   - route 우선 순위: 명시 endpoint 만 mock. 그 외 backend call 은 fail
 *     (운영 의도 외 호출 신호).
 */

import type { Page } from "@playwright/test";

/** useConsent 의 localStorage key — components/ConsentModal 과 동기화 (v2). */
const CONSENT_KEY = "speculum-consent-v2";

/**
 * 사전 동의 상태 주입 — consent modal 표시 회피. addInitScript 는 `page.goto`
 * 직전에 호출되어야 효과 발생 (page lifecycle).
 *
 * useConsent v2 (T46 V2 fix) 의 JSON ConsentRecord 형식. version=2 + 3 필수
 * 항목 (privacy / overseasTransfer / age14) 모두 true.
 */
export async function withConsent(page: Page): Promise<void> {
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
}

// =============================================================================
// Mock data — 4 뷰 fixture
// =============================================================================

/** 삼성전자 (005930) StockDetail mock — backend StockDetailOut schema. */
export const SAMSUNG_DETAIL = {
  id: "11111111-1111-1111-1111-111111111111",
  code: "005930",
  name: "삼성전자",
  market: "KOSPI",
  listing_date: "1975-06-11",
  delisting_date: null,
  fiscal_month: 12,
  ifrs_preference: "consolidated",
  status: "active",
  code_history: [
    {
      code: "005930",
      valid_from: "1975-06-11",
      valid_to: null,
      reason: "최초 상장",
    },
  ],
  factors: [
    {
      canonical_id: "per:ttm-consolidated-ifrs",
      name: "PER (TTM)",
      unit: "ratio",
      value: "12.5",
      is_na: false,
      na_reason: null,
      evaluator_version: "1.0.0",
    },
    {
      canonical_id: "roe:ttm-consolidated-ifrs",
      name: "ROE (TTM)",
      unit: "percent",
      value: "10.8",
      is_na: false,
      na_reason: null,
      evaluator_version: "1.0.0",
    },
  ],
} as const;

/** SK하이닉스 (000660) StockDetail mock. */
export const SK_HYNIX_DETAIL = {
  id: "22222222-2222-2222-2222-222222222222",
  code: "000660",
  name: "SK하이닉스",
  market: "KOSPI",
  listing_date: "1996-12-26",
  delisting_date: null,
  fiscal_month: 12,
  ifrs_preference: "consolidated",
  status: "active",
  code_history: [],
  factors: [
    {
      canonical_id: "per:ttm-consolidated-ifrs",
      name: "PER (TTM)",
      unit: "ratio",
      value: "8.2",
      is_na: false,
      na_reason: null,
      evaluator_version: "1.0.0",
    },
  ],
} as const;

// =============================================================================
// Route mock helpers — page.route() 등록
// =============================================================================

/**
 * /api/stocks/{code} GET mock — Stock Detail.
 *
 * `code` 미지정 시 모든 종목코드 = 삼성전자 응답 (단일 fixture 시나리오용).
 * 명시 시 해당 code 일치만 응답, 다른 code 는 404.
 */
export async function mockStockDetail(
  page: Page,
  code?: string,
): Promise<void> {
  // backend 의 path: /api/stocks/{code}?as_of=YYYY-MM-DD
  await page.route(/\/api\/stocks\/(\d{1,6})(\?|$)/, async (route) => {
    const url = new URL(route.request().url());
    const matched = url.pathname.match(/\/api\/stocks\/(\d{1,6})/);
    const requested = matched?.[1] ?? "";

    if (code !== undefined && requested !== code) {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "not found" }),
      });
      return;
    }

    // 005930 / 000660 명시, 그 외 default 삼성전자.
    const payload = requested === "000660" ? SK_HYNIX_DETAIL : SAMSUNG_DETAIL;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
}

/** /api/stocks/compare GET mock — 2~6 종목 multi-fetch. */
export async function mockStockCompare(page: Page): Promise<void> {
  await page.route(/\/api\/stocks\/compare\?/, async (route) => {
    const url = new URL(route.request().url());
    const codes = (url.searchParams.get("codes") ?? "").split(",").filter(Boolean);
    const items = codes.map((rawCode) => {
      // zero-pad to 6 digits (backend normalize 동치).
      const code = rawCode.padStart(6, "0");
      if (code === "005930") return SAMSUNG_DETAIL;
      if (code === "000660") return SK_HYNIX_DETAIL;
      // 그 외 = not_found 시뮬레이션 (현재 fixture 미정의).
      return null;
    });
    const found = items.filter((x): x is typeof SAMSUNG_DETAIL => x !== null);
    const not_found = codes
      .map((c) => c.padStart(6, "0"))
      .filter((c) => c !== "005930" && c !== "000660");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: found, not_found }),
    });
  });
}

/** POST /api/screen mock — Screener 실행 결과. */
export async function mockScreenRun(page: Page): Promise<void> {
  await page.route(/\/api\/screen(\?|$)/, async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        result_codes: ["005930", "000660", "035420"],
        total: 3,
        data_versions: { calendar: "v1.0.0", factor_pack: "v1.0.0" },
      }),
    });
  });
}

/**
 * /api/watchlists CRUD mock — folder list + items list.
 *
 * 단순 fixture: 1 default folder ("기본 관심 종목") + 1 item (005930 lineage).
 * mutation (POST/PUT/DELETE) 은 본 spec scope 외 (M0 cycle = read-only 시나리오).
 */
export async function mockWatchlist(page: Page): Promise<void> {
  const defaultFolder = {
    id: "33333333-3333-3333-3333-333333333333",
    parent_id: null,
    name: "기본 관심 종목",
    display_order: 0,
    is_default: true,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  };

  // GET /api/watchlists — folder list.
  await page.route(/\/api\/watchlists$/, async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [defaultFolder], total: 1 }),
    });
  });

  // GET /api/watchlists/{folderId}/items — items list.
  await page.route(/\/api\/watchlists\/[^/]+\/items$/, async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            id: "44444444-4444-4444-4444-444444444444",
            watchlist_id: defaultFolder.id,
            code_lineage_id: "55555555-5555-5555-5555-555555555555",
            note: "장기 보유 후보",
            display_order: 0,
            added_at: "2026-05-10T09:00:00Z",
          },
        ],
        total: 1,
      }),
    });
  });
}
