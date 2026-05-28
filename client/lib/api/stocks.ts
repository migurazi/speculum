/**
 * Stocks API client — GET /api/stocks/{code} + /api/stocks/search.
 *
 * Backend `app/api/routes/stocks.py` 의 wire schema (StockDetailOut /
 * StockSummaryOut / FactorValueOut) 와 1:1 매핑. Decimal → string wire
 * (backend Risk-C4) — frontend 가 BigNumber / decimal.js 로 처리 시 안전.
 */

import { fetchJson } from "./client";

/** StockStatus — backend `app.schemas.stocks.StockStatus`. */
export type StockStatus = "active" | "not_yet_listed" | "delisted";

/** CodeHistoryItem — lineage 의 단일 entry. */
export interface CodeHistoryItem {
  readonly code: string;
  readonly valid_from: string;  // ISO 8601 date
  readonly valid_to: string | null;
  readonly reason: string;
}

/** FactorValue — Stock Detail 의 지표 카드 1 개. */
export interface FactorValue {
  readonly canonical_id: string;
  readonly name: string;
  readonly unit: string;
  /** Decimal → string wire. UI 가 BigNumber 처리. null = N/A. */
  readonly value: string | null;
  readonly is_na: boolean;
  readonly na_reason: string | null;
  readonly evaluator_version: string;
}

/** StockDetail — `/api/stocks/{code}` 응답. */
export interface StockDetail {
  readonly id: string;
  readonly code: string;
  readonly name: string;
  readonly market: string;
  readonly listing_date: string;
  readonly delisting_date: string | null;
  readonly fiscal_month: number;
  readonly ifrs_preference: string;
  readonly status: StockStatus;
  readonly code_history: ReadonlyArray<CodeHistoryItem>;
  readonly factors: ReadonlyArray<FactorValue>;
}

/** StockSummary — 검색 결과 row. */
export interface StockSummary {
  readonly id: string;
  readonly code: string;
  readonly name: string;
  readonly market: string;
  readonly listing_date: string;
  readonly delisting_date: string | null;
  readonly status: StockStatus;
}

/** StockSearchPage — `/api/stocks/search` 응답. */
export interface StockSearchPage {
  readonly items: ReadonlyArray<StockSummary>;
  readonly total: number | null;
  readonly next_cursor: string | null;
}

/**
 * GET /api/stocks/{code} — Stock Detail fetch.
 *
 * @param code KRX 종목코드 (6자리). 1~6 자리 입력은 backend 가 zero-pad.
 * @param asOf PIT 기준 일자 (ISO 8601).
 * @param signal AbortController.
 */
export async function fetchStockDetail(
  code: string,
  asOf: string,
  signal?: AbortSignal,
): Promise<StockDetail> {
  return fetchJson<StockDetail>(`/api/stocks/${encodeURIComponent(code)}`, {
    method: "GET",
    searchParams: { as_of: asOf },
    signal,
  });
}

/** StockCompare — `/api/stocks/compare` 응답. */
export interface StockCompare {
  /** 정규화·dedup 후 입력 순서. lineage 존재 종목만. */
  readonly items: ReadonlyArray<StockDetail>;
  /**
   * lineage 부재 codes — 정규화 후 입력 순서 보존 (backend
   * `compare_stocks` `not_found.append(code)` in normalized loop).
   * 클라이언트 측 정렬·dedup 가정 금지 (oracle T38 C1).
   */
  readonly not_found: ReadonlyArray<string>;
}

/**
 * GET /api/stocks/compare — 2~6 종목 multi-fetch (AC-F-05).
 *
 * @param codes 비교할 종목코드 list — 2~6 개. backend 가 6 자리 zero-pad + dedup.
 * @param asOf PIT 기준 일자.
 * @param signal AbortController.
 *
 * @throws ApiError (400) 시 codes 가 2 개 미만 또는 6 개 초과, 형식 위반.
 */
export async function fetchStockCompare(
  codes: ReadonlyArray<string>,
  asOf: string,
  signal?: AbortSignal,
): Promise<StockCompare> {
  // backend 가 comma-separated string 으로 받음 — Query(...) 의 limit 200 제약
  // 이 있으나 6 종목 × 6 자리 + 5 separator = 41 char 라 안전.
  return fetchJson<StockCompare>("/api/stocks/compare", {
    method: "GET",
    searchParams: { codes: codes.join(","), as_of: asOf },
    signal,
  });
}

/**
 * GET /api/stocks/search — 종목 검색.
 *
 * @param q 검색어 — 한글 이름 또는 숫자 코드 (자동 감지).
 * @param asOf PIT 기준 일자.
 * @param limit default 50, max 200.
 * @param includeDelisted default false.
 */
export async function searchStocks(
  q: string,
  asOf: string,
  options: {
    readonly limit?: number;
    readonly includeDelisted?: boolean;
    readonly signal?: AbortSignal;
  } = {},
): Promise<StockSearchPage> {
  const searchParams: Record<string, string> = {
    q,
    as_of: asOf,
  };
  if (options.limit !== undefined) {
    searchParams["limit"] = String(options.limit);
  }
  if (options.includeDelisted !== undefined) {
    searchParams["include_delisted"] = String(options.includeDelisted);
  }
  return fetchJson<StockSearchPage>("/api/stocks/search", {
    method: "GET",
    searchParams,
    signal: options.signal,
  });
}
