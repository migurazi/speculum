/**
 * 세금 계산 API client — POST /api/tax/securities-transaction.
 *
 * ADR-0030 D1/D3 — 증권거래세 단순 산식 계산기.
 *
 * 계약:
 *   - POST /api/tax/securities-transaction (인증 불요)
 *       body { market, trade_amount, trade_date }
 *       → 200 { tax_amount, applied_rate, effective_date, legal_source, disclaimer_required: true }
 *       잘못된 입력 / 2023-01-01 이전 거래일 → 422.
 *
 * snake↔camel: wire 는 snake_case, 호출자에게는 camelCase 반환.
 *
 * No Advice 경계 (ADR-0030 D1/D2):
 *   - 양도세 함수 없음(D1 deferred).
 *   - 개별 상황 파라미터(대주주 여부·보유기간·손익통산) 없음(D2).
 *   - 이 모듈은 transport 전용 — 디스클레이머 게이트는 UI 계층(TaxCalculatorPanel).
 *
 * 관련:
 *   - components/Tax/TaxCalculatorPanel.tsx — 디스클레이머 게이트 UI.
 *   - lib/api/client.ts — fetchJson (인증 없이도 동작 — Authorization 헤더 미전송).
 */

import { fetchJson } from "./client";

/** 시장구분 — 증권거래세법 시장구분. */
export type TaxMarket = "kospi" | "kosdaq" | "konex" | "unlisted";

/** POST /api/tax/securities-transaction 요청 파라미터 (camelCase). */
export interface CalculateTaxParams {
  /** 시장구분 (코스피/코스닥/코넥스/비상장). */
  readonly market: TaxMarket;
  /** 거래금액 문자열 (예: "1000000"). */
  readonly tradeAmount: string;
  /** 거래일 (ISO 8601, "YYYY-MM-DD"). */
  readonly tradeDate: string;
}

/** POST /api/tax/securities-transaction wire 요청 body (snake_case). */
interface TaxRequestWire {
  readonly market: TaxMarket;
  readonly trade_amount: string;
  readonly trade_date: string;
}

/** POST /api/tax/securities-transaction wire 응답 (snake_case). */
interface TaxResponseWire {
  readonly tax_amount: string;
  readonly applied_rate: string;
  readonly effective_date: string;
  readonly legal_source: string;
  readonly disclaimer_required: true;
}

/** calculateSecuritiesTransactionTax 반환 타입 (camelCase). */
export interface TaxResult {
  /** 계산된 세액 문자열 (예: "1500"). */
  readonly taxAmount: string;
  /** 적용 세율 문자열 (예: "0.0015"). */
  readonly appliedRate: string;
  /** 세율 효력일 (ISO 8601, "YYYY-MM-DD"). ADR-0030 D4 효력일 freeze. */
  readonly effectiveDate: string;
  /** 법령 출처 문자열 (예: "증권거래세법 제8조 제1항"). ADR-0030 D4. */
  readonly legalSource: string;
  /**
   * 세무자문 디스클레이머 필수 여부 — backend 가 항상 true 반환.
   * TaxCalculatorPanel 이 이 플래그로 게이트를 강제한다(ADR-0030 D3).
   */
  readonly disclaimerRequired: true;
}

/**
 * POST /api/tax/securities-transaction — 증권거래세 단순 계산.
 *
 * ADR-0030 D1/D2:
 *   - 시장구분 + 거래금액 + 거래일만. 개별 상황 파라미터 없음.
 *   - 양도세 계산 없음(D1 deferred).
 *
 * @param params 시장구분·거래금액·거래일.
 * @returns TaxResult (camelCase 변환 완료).
 * @throws ApiError — 422 (2023-01-01 이전 거래일·잘못된 입력 등).
 */
export async function calculateSecuritiesTransactionTax(
  params: CalculateTaxParams,
): Promise<TaxResult> {
  const body: TaxRequestWire = {
    market: params.market,
    trade_amount: params.tradeAmount,
    trade_date: params.tradeDate,
  };

  // snake_case 응답 → camelCase 변환.
  const wire = await fetchJson<TaxResponseWire>("/api/tax/securities-transaction", {
    method: "POST",
    body,
  });

  return {
    taxAmount: wire.tax_amount,
    appliedRate: wire.applied_rate,
    effectiveDate: wire.effective_date,
    legalSource: wire.legal_source,
    disclaimerRequired: wire.disclaimer_required,
  };
}
