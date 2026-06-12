/**
 * 가격 API 클라이언트 — GET /api/stocks/{code}/prices.
 *
 * backend `app.api.routes.stocks.get_stock_prices` 의 wire schema 와 1:1 매핑.
 * OHLC / close_adjusted 는 Decimal → str wire → number 변환 (차트 렌더용).
 * volume 은 int wire → number 유지.
 *
 * PIT 약속:
 *   - as_of 기준 effective_date <= as_of 의 bars 만 반환.
 *   - bars 는 date asc 정렬.
 *   - 데이터 없으면 빈 bars (HTTP 200).
 *
 * 관련:
 *   - M0_PLAN PriceChart 기능.
 *   - ADR-0008 D8 — 일 단위 PIT.
 *   - ADR-0001 D6 — corporate action 마커 (raw 모드만).
 */

import { fetchJson } from "./client";

/**
 * Corporate action — backend `{effective_date, action_type, ratio}` 의 camelCase 매핑.
 *
 * ADR-0001 D6: raw 모드 차트에 ▾ 마커로 표시. 일자(사실)와 종류만 — 해석/판단 annotation 금지.
 */
export interface CorporateAction {
  /** 기준일 (ISO 8601 "YYYY-MM-DD"). */
  readonly effectiveDate: string;
  /** corporate action 종류 — "split" | "dividend" | "merger" | 기타. */
  readonly actionType: string;
  /** 비율 (str or null). */
  readonly ratio: string | null;
}

/** 단일 가격 bar — backend `{date, open, high, low, close, close_adjusted, volume}` 의 camelCase 매핑. */
export interface PriceBar {
  /** ISO 8601 date ("YYYY-MM-DD"). */
  readonly date: string;
  /** 시가 (숫자 변환). */
  readonly open: number;
  /** 고가 (숫자 변환). */
  readonly high: number;
  /** 저가 (숫자 변환). */
  readonly low: number;
  /** 종가 (숫자 변환). */
  readonly close: number;
  /** 수정 종가 (숫자 변환). */
  readonly closeAdjusted: number;
  /** 거래량. */
  readonly volume: number;
}

/** GET /api/stocks/{code}/prices 응답. */
export interface StockPricesResponse {
  /** KRX 종목코드. */
  readonly code: string;
  /** PIT 기준 일자 ("YYYY-MM-DD"). */
  readonly asOf: string;
  /** 가격 bars — date asc, 비어 있을 수 있음. */
  readonly bars: ReadonlyArray<PriceBar>;
  /**
   * corporate actions — effective_date 가 차트 범위 내인 항목만.
   * raw 모드 차트에서 ▾ 마커로 표시 (ADR-0001 D6).
   * 없으면 빈 배열.
   */
  readonly actions: ReadonlyArray<CorporateAction>;
}

/**
 * GET /api/stocks/{code}/prices — PIT 기준 가격 bars fetch.
 *
 * @param code KRX 종목코드.
 * @param asOf PIT 기준 일자 (ISO 8601 "YYYY-MM-DD").
 * @param signal AbortController signal.
 * @param days 조회 기간(일). 기본 365.
 * @returns StockPricesResponse — bars 비어 있으면 데이터 없음.
 * @throws ApiError — fetch 실패 또는 non-2xx 응답.
 */
export async function fetchStockPrices(
  code: string,
  asOf: string,
  signal?: AbortSignal,
  days = 365,
): Promise<StockPricesResponse> {
  const raw = await fetchJson<{
    code: string;
    as_of: string;
    bars: Array<{
      date: string;
      open: string;
      high: string;
      low: string;
      close: string;
      close_adjusted: string;
      volume: number;
    }>;
    actions?: Array<{
      effective_date: string;
      action_type: string;
      ratio: string | null;
    }>;
  }>(`/api/stocks/${encodeURIComponent(code)}/prices`, {
    method: "GET",
    searchParams: { as_of: asOf, days: String(days) },
    signal,
  });

  return {
    code: raw.code,
    asOf: raw.as_of,
    bars: raw.bars.map((b) => ({
      date: b.date,
      open: parseFloat(b.open),
      high: parseFloat(b.high),
      low: parseFloat(b.low),
      close: parseFloat(b.close),
      closeAdjusted: parseFloat(b.close_adjusted),
      volume: b.volume,
    })),
    actions: (raw.actions ?? []).map((a) => ({
      effectiveDate: a.effective_date,
      actionType: a.action_type,
      ratio: a.ratio,
    })),
  };
}
