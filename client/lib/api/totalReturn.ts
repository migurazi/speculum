/**
 * Total Return API 클라이언트 — GET /api/stocks/{code}/total-return.
 *
 * backend `app.api.routes.stocks.get_stock_total_return` 의 wire schema 와 1:1 매핑.
 * 세전 total return index(TRI)를 첫 거래일 보정 종가 기준으로 rebase 한 표시용
 * line point 시계열. Decimal → str wire → number 변환 (차트 렌더용, prices.ts 동형).
 *
 * 산출 (ADR-0035 D1/D6/D7):
 *   - PriceAdjuster as_of 보정 종가 + 배당락일 재투자 → TRI.
 *   - value = base_close_adjusted × TRI (배당 0 → 보정 종가와 일치).
 *   - 세전 — 배당소득세·양도세 미반영 (PreTaxDisclosure 가 고지).
 *
 * PIT 약속:
 *   - 이중 PIT (announced<=as_of AND effective<=as_of) — 배당 누적.
 *   - points 는 date asc 정렬. 데이터 없으면 빈 points (HTTP 200).
 *
 * 관련:
 *   - M7_PLAN #6 (Total Return 차트 토글).
 *   - ADR-0035 (세전 total return 엔진).
 */

import { fetchJson } from "./client";

/** 단일 total return line point — backend `{date, value, index}` 의 camelCase 매핑. */
export interface TotalReturnPoint {
  /** ISO 8601 date ("YYYY-MM-DD"). */
  readonly date: string;
  /** 표시용 누적 수준 = base_close_adjusted × TRI (숫자 변환). */
  readonly value: number;
  /** TRI (첫 거래일=1.0 기준 누적, 숫자 변환). 투명성·디버깅용. */
  readonly index: number;
}

/** GET /api/stocks/{code}/total-return 응답. */
export interface StockTotalReturnResponse {
  /** KRX 종목코드. */
  readonly code: string;
  /** PIT 기준 일자 ("YYYY-MM-DD"). */
  readonly asOf: string;
  /** total return line points — date asc, 비어 있을 수 있음. */
  readonly points: ReadonlyArray<TotalReturnPoint>;
  /**
   * §2.1 조용한 손실 금지 — 배당락일 부재/첫 거래일 배당/보정 실패 등으로
   * 누락된 배당 사유. 없으면 빈 배열.
   */
  readonly warnings: ReadonlyArray<string>;
}

/**
 * GET /api/stocks/{code}/total-return — PIT 기준 세전 total return 시계열 fetch.
 *
 * @param code KRX 종목코드.
 * @param asOf PIT 기준 일자 (ISO 8601 "YYYY-MM-DD").
 * @param signal AbortController signal.
 * @param days 조회 기간(일). 기본 365 (prices.ts 와 동일 window).
 * @returns StockTotalReturnResponse — points 비어 있으면 데이터 없음.
 * @throws ApiError — fetch 실패 또는 non-2xx 응답.
 */
export async function fetchStockTotalReturn(
  code: string,
  asOf: string,
  signal?: AbortSignal,
  days = 365,
): Promise<StockTotalReturnResponse> {
  const raw = await fetchJson<{
    code: string;
    as_of: string;
    points: Array<{
      date: string;
      value: string;
      index: string;
    }>;
    warnings?: Array<string>;
  }>(`/api/stocks/${encodeURIComponent(code)}/total-return`, {
    method: "GET",
    searchParams: { as_of: asOf, days: String(days) },
    signal,
  });

  return {
    code: raw.code,
    asOf: raw.as_of,
    points: raw.points.map((p) => ({
      date: p.date,
      value: parseFloat(p.value),
      index: parseFloat(p.index),
    })),
    warnings: raw.warnings ?? [],
  };
}
