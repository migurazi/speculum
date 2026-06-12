/**
 * Market Overview API client — GET /api/market-overview.
 *
 * 시장 전체의 집계 통계(유니버스 종목 수, 시장별 분포, factor 별 분포)를
 * 가져온다. 종목별 데이터·랭킹·추천은 포함하지 않는다.
 *
 * 설계:
 *   - backend `app.api.routes.market.get_market_overview` 의 wire schema 와 1:1 매핑.
 *   - `fetchJson` wrapper 재사용 (client.ts 와 동일 패턴).
 *   - 통계값(mean/median/p25/p75/min/max)은 Decimal→str wire 그대로 유지 —
 *     표 표시 목적이므로 number 변환 불필요.
 *   - as_of 쿼리 파라미터는 호출자가 전달 (prices.ts 와 동일 패턴).
 *
 * 관련:
 *   - backend `app.api.routes.market.get_market_overview`
 *   - M1_PLAN T61 Market Overview 뷰
 *   - 8기둥 §2.3 Active Inspection — /market 라우트 이동 시에만 fetch
 */

import { fetchJson } from "./client";

/** 시장별 종목 수 — backend `{market, count}` 와 1:1. */
export interface MarketCount {
  /** 시장 식별자 (예: "KOSPI", "KOSDAQ"). */
  readonly market: string;
  /** 해당 시장의 활성 종목 수. */
  readonly count: number;
}

/**
 * Factor 집계 통계 — backend `{canonical_id, name, unit, count, na_count,
 * mean, median, p25, p75, min, max}` 의 camelCase 매핑.
 *
 * 통계값은 Decimal→str (또는 null) wire 그대로 string | null 유지.
 * count=0 인 경우 모든 통계값이 null.
 */
export interface FactorAggregate {
  /** Factor canonical identifier. */
  readonly canonicalId: string;
  /** 사람이 읽는 라벨 (예: "PER (TTM, 연결, K-IFRS)"). */
  readonly name: string;
  /** 단위 문자열 (예: "배", "%" — 빈 문자열 가능). */
  readonly unit: string;
  /** 값이 있는 종목 수. */
  readonly count: number;
  /** N/A 종목 수 (데이터 없는 종목). */
  readonly naCount: number;
  /** 평균 (Decimal→str, 없으면 null). */
  readonly mean: string | null;
  /** 중앙값 (Decimal→str, 없으면 null). */
  readonly median: string | null;
  /** 25 백분위 (Decimal→str, 없으면 null). */
  readonly p25: string | null;
  /** 75 백분위 (Decimal→str, 없으면 null). */
  readonly p75: string | null;
  /** 최솟값 (Decimal→str, 없으면 null). */
  readonly min: string | null;
  /** 최댓값 (Decimal→str, 없으면 null). */
  readonly max: string | null;
}

/**
 * 매크로 지표 단일 항목 — backend `MacroIndicatorOut` 의 camelCase 매핑.
 *
 * value 는 Decimal→str wire 그대로 유지 (표 표시 목적 — number 변환 불필요).
 * vintageDate 는 ECOS 관측 시점(정확한 한국은행 공표일 아님) — disclaimer 필수.
 */
export interface MacroIndicator {
  /** ECOS 지표 코드 식별자. */
  readonly indicatorId: string;
  /** 사람이 읽는 지표명 (예: "한국은행 기준금리"). */
  readonly name: string;
  /** 지표값 문자열 (Decimal→str wire 그대로). */
  readonly value: string;
  /** 단위 (예: "연%"). */
  readonly unit: string;
  /** 지표 기준 시점 ("YYYY-MM-DD"). */
  readonly referenceDate: string;
  /** 관측 시점 — ECOS 에서 가져온 시점 (정확한 공표일 아님, "YYYY-MM-DD"). */
  readonly vintageDate: string;
}

/** GET /api/market-overview 응답 전체. */
export interface MarketOverview {
  /** PIT 기준 일자 ("YYYY-MM-DD"). */
  readonly asOf: string;
  /** 활성 종목 수 (전체 유니버스). */
  readonly universeCount: number;
  /** 시장별 종목 수 분포. */
  readonly marketBreakdown: ReadonlyArray<MarketCount>;
  /** Factor 별 집계 통계 목록. */
  readonly factors: ReadonlyArray<FactorAggregate>;
  /** ECOS 매크로 지표 목록 (데이터 없는 지표는 제외됨). */
  readonly macroIndicators: ReadonlyArray<MacroIndicator>;
}

/**
 * GET /api/market-overview — PIT 기준 시장 전체 집계 통계.
 *
 * @param asOf PIT 기준 일자 (ISO 8601 "YYYY-MM-DD").
 * @param signal AbortController signal (TanStack Query 가 cancel 시 주입).
 * @returns MarketOverview — 유니버스가 비어 있으면 universeCount=0.
 * @throws ApiError — fetch 실패 또는 non-2xx 응답.
 */
export async function fetchMarketOverview(
  asOf: string,
  signal?: AbortSignal,
): Promise<MarketOverview> {
  const raw = await fetchJson<{
    as_of: string;
    universe_count: number;
    market_breakdown: Array<{
      market: string;
      count: number;
    }>;
    factors: Array<{
      canonical_id: string;
      name: string;
      unit: string;
      count: number;
      na_count: number;
      mean: string | null;
      median: string | null;
      p25: string | null;
      p75: string | null;
      min: string | null;
      max: string | null;
    }>;
    macro_indicators?: Array<{
      indicator_id: string;
      name: string;
      value: string;
      unit: string;
      reference_date: string;
      vintage_date: string;
    }>;
  }>("/api/market-overview", {
    method: "GET",
    searchParams: { as_of: asOf },
    signal,
  });

  return {
    asOf: raw.as_of,
    universeCount: raw.universe_count,
    marketBreakdown: raw.market_breakdown.map((m) => ({
      market: m.market,
      count: m.count,
    })),
    factors: raw.factors.map((f) => ({
      canonicalId: f.canonical_id,
      name: f.name,
      unit: f.unit,
      count: f.count,
      naCount: f.na_count,
      mean: f.mean,
      median: f.median,
      p25: f.p25,
      p75: f.p75,
      min: f.min,
      max: f.max,
    })),
    // macro_indicators 는 Phase 2 이후 응답에 포함 — 없으면 빈 배열.
    macroIndicators: (raw.macro_indicators ?? []).map((mi) => ({
      indicatorId: mi.indicator_id,
      name: mi.name,
      value: mi.value,
      unit: mi.unit,
      referenceDate: mi.reference_date,
      vintageDate: mi.vintage_date,
    })),
  };
}
