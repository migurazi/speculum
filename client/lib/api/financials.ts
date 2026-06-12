/**
 * 재무 시계열 API 클라이언트 — GET /api/stocks/{code}/financials.
 *
 * backend `app.api.routes.stocks.get_stock_financials` 의 wire schema 와 1:1 매핑.
 * Decimal → str wire — UI 가 그대로 표시(왜곡 없이).
 *
 * PIT 약속:
 *   - as_of 기준 적재된 분기 중 오름차순 정렬.
 *   - 결손 분기 values[i] = null.
 *   - 적재 없는 account 는 items 에서 제외.
 *
 * 관련:
 *   - M1 T57 Phase 2 — AC-F-04 재무 시계열 표.
 *   - No Advice (8기둥 §2.2) — 판단색/해석/전망/추천 0.
 */

import { fetchJson } from "./client";

/** 재무 항목 단일 행 — account 별 분기 값 배열. */
export interface FinancialItem {
  /** account 식별자 (예: "revenue", "basic_eps"). */
  readonly account: string;
  /** 표시명 — backend 제공 한국어 (예: "매출액"). */
  readonly name: string;
  /** 단위 (예: "krw"). */
  readonly unit: string;
  /**
   * periods 와 동일 길이 — str(숫자) 또는 null(결손).
   * Decimal → str wire 그대로 보존 (왜곡 방지).
   */
  readonly values: ReadonlyArray<string | null>;
}

/** GET /api/stocks/{code}/financials 응답. */
export interface FinancialSeries {
  /** KRX 종목코드. */
  readonly code: string;
  /** PIT 기준 일자 ("YYYY-MM-DD"). */
  readonly asOf: string;
  /**
   * fiscal_period 오름차순 목록 (예: ["2023Q1","2023Q2","2023Q3","2023Q4"]).
   * items[].values 와 동일 길이 — 인덱스 1:1 대응.
   */
  readonly periods: ReadonlyArray<string>;
  /** 재무 항목 행 — 적재 없는 account 제외. */
  readonly items: ReadonlyArray<FinancialItem>;
}

/**
 * GET /api/stocks/{code}/financials — PIT 기준 재무 시계열 fetch.
 *
 * @param code KRX 종목코드.
 * @param asOf PIT 기준 일자 (ISO 8601 "YYYY-MM-DD").
 * @param signal AbortController signal.
 * @returns FinancialSeries — items 빈 배열이면 재무 데이터 없음.
 * @throws ApiError — fetch 실패 또는 non-2xx 응답.
 */
export async function fetchStockFinancials(
  code: string,
  asOf: string,
  signal?: AbortSignal,
): Promise<FinancialSeries> {
  const raw = await fetchJson<{
    code: string;
    as_of: string;
    periods: string[];
    items: Array<{
      account: string;
      name: string;
      unit: string;
      values: Array<string | null>;
    }>;
  }>(`/api/stocks/${encodeURIComponent(code)}/financials`, {
    method: "GET",
    searchParams: { as_of: asOf },
    signal,
  });

  return {
    code: raw.code,
    asOf: raw.as_of,
    periods: raw.periods,
    items: raw.items.map((item) => ({
      account: item.account,
      name: item.name,
      unit: item.unit,
      values: item.values,
    })),
  };
}
