/**
 * 정정공시 history API 클라이언트 — GET /api/stocks/{code}/financials/history.
 *
 * §2.4 PIT: as_of 기준 vintage chain 전체 노출.
 * §2.7 Observation: vintage 존재 자체가 "회사가 이 수치를 정정했다"는 관측된 사실.
 * §2.2 No Advice: 값 변화에 판단색·증감·등락 라벨 0 — 값 그대로 노출만.
 *
 * vintage_seq: 1 = 원본 공시, 2+ = 정정 회차.
 * is_active: true = 현행(superseded_by null), false = 과거(다음 vintage 에 의해 대체됨).
 * superseded_by: 다음 vintage 의 UUID. null 이면 현행.
 *
 * 관련:
 *   - M2 T82 Phase 2 — AC-M2-F-07 정정공시 history view.
 *   - ADR-0008 D8 — as_of PIT.
 */

import { fetchJson } from "./client";

/** 단일 vintage — 한 account 의 한 정정 회차. */
export interface FinancialVintage {
  /** 정정 회차 (1=원본 공시). */
  readonly vintageSeq: number;
  /** 공시 효력 발생일 ("YYYY-MM-DD"). */
  readonly effectiveDate: string;
  /** 재무 계정 식별자 (예: "revenue"). */
  readonly account: string;
  /** 값 — Decimal → str wire. 왜곡 방지. */
  readonly value: string;
  /** 단위 (예: "krw"). */
  readonly unit: string;
  /** IFRS 구분 (예: "consolidated"). */
  readonly ifrsType: string;
  /** 현행 여부 — true=현재 유효한 값, false=후속 정정에 의해 대체됨. */
  readonly isActive: boolean;
  /** 이 vintage 를 대체한 다음 vintage 의 UUID. null 이면 현행. */
  readonly supersededBy: string | null;
}

/** GET /api/stocks/{code}/financials/history 응답. */
export interface FinancialHistory {
  /** KRX 종목코드. */
  readonly code: string;
  /**
   * PIT 기준 일자 ("YYYY-MM-DD") 또는 null(최신 기준).
   * §2.4: 요청한 as_of 시점에 공개된 vintage 만 포함.
   */
  readonly asOf: string | null;
  /** fiscal_period 필터. null 이면 전 기간. */
  readonly fiscalPeriodFilter: string | null;
  /**
   * vintage 배열 — 모든 account·회차 플랫.
   * account + vintage_seq 기준으로 그룹핑은 UI 레이어 책임.
   */
  readonly vintages: ReadonlyArray<FinancialVintage>;
}

/**
 * GET /api/stocks/{code}/financials/history — 정정공시 vintage chain fetch.
 *
 * @param code KRX 종목코드.
 * @param params 선택적 쿼리 파라미터 — fiscalPeriod, asOf.
 * @param signal AbortController signal.
 * @returns FinancialHistory — vintages 빈 배열이면 이력 없음.
 * @throws ApiError — fetch 실패 또는 non-2xx 응답.
 */
export async function fetchFinancialHistory(
  code: string,
  params: {
    readonly fiscalPeriod?: string;
    readonly asOf?: string;
  } = {},
  signal?: AbortSignal,
): Promise<FinancialHistory> {
  // 쿼리 파라미터 — undefined 는 URLSearchParams 에서 제외.
  const searchParams: Record<string, string> = {};
  if (params.fiscalPeriod) searchParams["fiscal_period"] = params.fiscalPeriod;
  if (params.asOf) searchParams["as_of"] = params.asOf;

  const raw = await fetchJson<{
    code: string;
    as_of: string | null;
    fiscal_period_filter: string | null;
    vintages: Array<{
      vintage_seq: number;
      effective_date: string;
      account: string;
      value: string;
      unit: string;
      ifrs_type: string;
      is_active: boolean;
      superseded_by: string | null;
    }>;
  }>(`/api/stocks/${encodeURIComponent(code)}/financials/history`, {
    method: "GET",
    searchParams,
    signal,
  });

  return {
    code: raw.code,
    asOf: raw.as_of,
    fiscalPeriodFilter: raw.fiscal_period_filter,
    // snake_case → camelCase 매핑. 값 자체 불변.
    vintages: raw.vintages.map((v) => ({
      vintageSeq: v.vintage_seq,
      effectiveDate: v.effective_date,
      account: v.account,
      value: v.value,
      unit: v.unit,
      ifrsType: v.ifrs_type,
      isActive: v.is_active,
      supersededBy: v.superseded_by,
    })),
  };
}
