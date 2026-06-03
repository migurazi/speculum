/**
 * Backtest API client — POST /api/backtest.
 *
 * ADR-0027 D1/D2/D3/D4/D5 — PIT rebalance 백테스트 실행.
 *
 * 계약 (wire, snake_case):
 *   POST /api/backtest
 *   요청: { pack_slug, pack_version, conditions, start, end, rebalance, cost_commission_bps?, cost_tax_bps? }
 *   응답: { freeze, stats, equity_curve, survivorship_complete, missing_price_ratio, disclaimer_required }
 *
 * No Advice / ADR-0027 D4/D6:
 *   - 결과 타입에 평가/등급/순위 필드 없음 — 사실 통계만.
 *   - 이 모듈은 transport 전용 — 시각 정책 강제는 UI 계층(BacktestPanel).
 *
 * snake↔camel 변환:
 *   - 요청: camelCase 입력 → snake_case wire body.
 *   - 응답: snake_case wire → camelCase 반환.
 *
 * 관련:
 *   - lib/api/client.ts — fetchJson (Authorization Bearer 자동 주입).
 *   - components/Backtest/BacktestPanel.tsx — UI + 시각 게이트.
 */

import { fetchJson } from "./client";
import type { ScreenCondition } from "./screen";

// ── Wire 타입 (snake_case, backend 계약) ─────────────────────────────────────

/** POST /api/backtest 요청 body (snake_case wire). */
interface BacktestBodyWire {
  readonly pack_slug: string;
  readonly pack_version: string;
  readonly conditions: ReadonlyArray<{
    readonly factor: string;
    readonly op: string;
    readonly value: string;
  }>;
  readonly start: string;
  readonly end: string;
  readonly rebalance: "quarterly" | "monthly";
  readonly cost_commission_bps?: number;
  readonly cost_tax_bps?: number;
}

/** freeze artifact wire 구조 (ADR-0027 D5). */
interface BacktestFreezeWire {
  readonly result_hash: string;
  readonly pack_content_hash: string;
  readonly krx_batch_id: string;
  readonly dart_batch_id: string;
  readonly rebalance: "quarterly" | "monthly";
  readonly start: string;
  readonly end: string;
  readonly cost_assumptions: {
    readonly tax_bps: string;
    readonly commission_bps: string;
  };
}

/** 통계 wire 구조. Decimal 통계는 string, nullable 통계는 null (외삽 없음). */
interface BacktestStatsWire {
  readonly cagr: string | null;
  readonly cumulative_return: string;
  readonly mdd: string;
  readonly volatility: string | null;
  readonly turnover: string;
}

/** equity curve 포인트 wire. */
interface EquityCurvePointWire {
  readonly date: string;
  readonly value: string;
}

/** POST /api/backtest 응답 wire. */
interface BacktestResultWire {
  readonly freeze: BacktestFreezeWire;
  readonly stats: BacktestStatsWire;
  readonly equity_curve: ReadonlyArray<EquityCurvePointWire>;
  readonly survivorship_complete: boolean;
  readonly missing_price_ratio: string;
  readonly disclaimer_required: boolean;
}

// ── 공개 타입 (camelCase) ────────────────────────────────────────────────────

/** freeze artifact — ADR-0027 D5 (재현 불변식). */
export interface BacktestFreeze {
  readonly resultHash: string;
  readonly packContentHash: string;
  readonly krxBatchId: string;
  readonly dartBatchId: string;
  readonly rebalance: "quarterly" | "monthly";
  readonly start: string;
  readonly end: string;
  /** 거래비용 가정 — D2 강제 노출. 숨기지 않음. */
  readonly costAssumptions: {
    readonly taxBps: string;
    readonly commissionBps: string;
  };
}

/**
 * 백테스트 통계 — 사실 통계만, 평가/등급/순위 필드 없음 (ADR-0027 D6).
 *
 * - Decimal 통계: string (정밀도 보존).
 * - nullable 통계: null (외삽 없음 — CAGR, volatility 는 기간/종목 부족 시 null).
 */
export interface BacktestStats {
  readonly cagr: string | null;
  readonly cumulativeReturn: string;
  readonly mdd: string;
  readonly volatility: string | null;
  readonly turnover: string;
}

/** equity curve 포인트 (camelCase). */
export interface EquityCurvePoint {
  readonly date: string;
  readonly value: string;
}

/**
 * 백테스트 결과 — ADR-0027 D4/D6 출력 게이트.
 *
 * 포함: freeze, stats, equityCurve, survivorshipComplete, missingPriceRatio, disclaimerRequired.
 * 제외: 평가/등급/순위/우수/별점 필드 일절 없음 (D6 결과 = 사실 통계만).
 */
export interface BacktestResult {
  readonly freeze: BacktestFreeze;
  readonly stats: BacktestStats;
  readonly equityCurve: ReadonlyArray<EquityCurvePoint>;
  /** survivorship bias 가능 여부 (ADR-0027 D3). */
  readonly survivorshipComplete: boolean;
  /** 가격 누락 비율 string (0 이상 1 미만). */
  readonly missingPriceRatio: string;
  /** 과거성과 디스클레이머 게이트 필요 여부 (ADR-0027 D4). */
  readonly disclaimerRequired: boolean;
}

/** runBacktest 입력 파라미터 (camelCase). */
export interface RunBacktestParams {
  readonly packSlug: string;
  readonly packVersion: string;
  readonly conditions: ReadonlyArray<ScreenCondition>;
  readonly start: string;
  readonly end: string;
  readonly rebalance: "quarterly" | "monthly";
  readonly costCommissionBps?: number;
  readonly costTaxBps?: number;
}

// ── snake↔camel 변환 ─────────────────────────────────────────────────────────

/** wire freeze → camelCase BacktestFreeze. */
function mapFreeze(w: BacktestFreezeWire): BacktestFreeze {
  return {
    resultHash: w.result_hash,
    packContentHash: w.pack_content_hash,
    krxBatchId: w.krx_batch_id,
    dartBatchId: w.dart_batch_id,
    rebalance: w.rebalance,
    start: w.start,
    end: w.end,
    costAssumptions: {
      taxBps: w.cost_assumptions.tax_bps,
      commissionBps: w.cost_assumptions.commission_bps,
    },
  };
}

/** wire stats → camelCase BacktestStats. */
function mapStats(w: BacktestStatsWire): BacktestStats {
  return {
    cagr: w.cagr,
    cumulativeReturn: w.cumulative_return,
    mdd: w.mdd,
    volatility: w.volatility,
    turnover: w.turnover,
  };
}

/** wire equity_curve → camelCase EquityCurvePoint[]. */
function mapEquityCurve(
  w: ReadonlyArray<EquityCurvePointWire>,
): ReadonlyArray<EquityCurvePoint> {
  return w.map((pt) => ({ date: pt.date, value: pt.value }));
}

/** wire 응답 → BacktestResult (camelCase). */
function mapResult(w: BacktestResultWire): BacktestResult {
  return {
    freeze: mapFreeze(w.freeze),
    stats: mapStats(w.stats),
    equityCurve: mapEquityCurve(w.equity_curve),
    survivorshipComplete: w.survivorship_complete,
    missingPriceRatio: w.missing_price_ratio,
    disclaimerRequired: w.disclaimer_required,
  };
}

// ── 공개 함수 ─────────────────────────────────────────────────────────────────

/**
 * POST /api/backtest — PIT rebalance 백테스트 실행.
 *
 * @param params  RunBacktestParams (camelCase)
 * @param signal  AbortController signal (선택)
 * @returns BacktestResult (camelCase)
 * @throws ApiError — 401 미인증, 404 팩 없음, 422 검증 실패.
 */
export async function runBacktest(
  params: RunBacktestParams,
  signal?: AbortSignal,
): Promise<BacktestResult> {
  const body: BacktestBodyWire = {
    pack_slug: params.packSlug,
    pack_version: params.packVersion,
    conditions: params.conditions,
    start: params.start,
    end: params.end,
    rebalance: params.rebalance,
    ...(params.costCommissionBps !== undefined
      ? { cost_commission_bps: params.costCommissionBps }
      : {}),
    ...(params.costTaxBps !== undefined
      ? { cost_tax_bps: params.costTaxBps }
      : {}),
  };

  const wire = await fetchJson<BacktestResultWire>("/api/backtest", {
    method: "POST",
    body,
    signal,
  });

  return mapResult(wire);
}
