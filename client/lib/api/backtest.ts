/**
 * Backtest API client — POST /api/backtest / POST /api/backtest/reproduce / GET /api/data-freshness.
 *
 * ADR-0027 D1/D2/D3/D4/D5 — PIT rebalance 백테스트 실행.
 * ADR-0033 D2~D6 — 재현 검증 + 데이터 신선도 신뢰성 표시 (M5 #6).
 *
 * 계약 (wire, snake_case):
 *   POST /api/backtest
 *   요청: { pack_slug, pack_version, conditions, start, end, rebalance, cost_commission_bps?, cost_tax_bps? }
 *   응답: { freeze, stats, equity_curve, survivorship_complete, missing_price_ratio, disclaimer_required }
 *
 *   POST /api/backtest/reproduce
 *   요청: { freeze: BacktestFreezeWire, equity_curve: [{date, value}], custom_pack_body?: object|null }
 *   응답: { matches, reproduce_note, pack_tampered, equity_curve }
 *
 *   GET /api/data-freshness
 *   응답: { krx, dart, as_of } — 각 { source, latest_batch_at, is_stale, elapsed_days }
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

/** freeze artifact wire 구조 (ADR-0027 D5 / ADR-0033 D2 — self-contained 재현 입력). */
interface BacktestFreezeWire {
  readonly result_hash: string;
  readonly pack_content_hash: string;
  // ADR-0033 D2 — pack 정본 재로드 식별자(reproduce 입력).
  readonly pack_slug: string;
  readonly pack_version: string;
  readonly krx_batch_id: string;
  readonly dart_batch_id: string;
  readonly rebalance: "quarterly" | "monthly";
  readonly start: string;
  readonly end: string;
  readonly cost_assumptions: {
    readonly tax_bps: string;
    readonly commission_bps: string;
  };
  // ADR-0033 D2 — self-contained 재현 입력(universe conditions + 전체 data_versions).
  readonly conditions: ReadonlyArray<{
    readonly factor: string;
    readonly op: string;
    readonly value: string;
  }>;
  readonly data_versions: Readonly<Record<string, string>>;
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

/** freeze artifact — ADR-0027 D5 (재현 불변식) / ADR-0033 D2 (self-contained 재현 입력). */
export interface BacktestFreeze {
  readonly resultHash: string;
  readonly packContentHash: string;
  /** ADR-0033 D2 — pack 정본 재로드 식별자(reproduce 입력). */
  readonly packSlug: string;
  readonly packVersion: string;
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
  /** ADR-0033 D2 — self-contained 재현 입력(universe conditions, canonical 정렬). */
  readonly conditions: ReadonlyArray<{
    readonly factor: string;
    readonly op: string;
    readonly value: string;
  }>;
  /** ADR-0033 D2 — 정책+batch_id 전체 freeze(재실행 데이터 시점 재현). */
  readonly dataVersions: Readonly<Record<string, string>>;
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
    packSlug: w.pack_slug,
    packVersion: w.pack_version,
    krxBatchId: w.krx_batch_id,
    dartBatchId: w.dart_batch_id,
    rebalance: w.rebalance,
    start: w.start,
    end: w.end,
    costAssumptions: {
      taxBps: w.cost_assumptions.tax_bps,
      commissionBps: w.cost_assumptions.commission_bps,
    },
    // ADR-0033 D2 — self-contained 재현 입력 운반(읽기 전용 그대로).
    conditions: w.conditions,
    dataVersions: w.data_versions,
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

// ── Reproduce wire 타입 (ADR-0033 D2~D5) ─────────────────────────────────────

/** POST /api/backtest/reproduce 요청 body (snake_case wire). */
interface BacktestReproduceBodyWire {
  readonly freeze: BacktestFreezeWire;
  readonly equity_curve: ReadonlyArray<EquityCurvePointWire>;
  readonly custom_pack_body?: object | null;
}

/** POST /api/backtest/reproduce 응답 wire. */
interface BacktestReproduceResultWire {
  readonly matches: boolean;
  readonly reproduce_note: string | null;
  readonly pack_tampered: boolean;
  readonly equity_curve: ReadonlyArray<EquityCurvePointWire>;
}

// ── DataFreshness wire 타입 (ADR-0033 D6) ─────────────────────────────────────

/** GET /api/data-freshness 소스별 신선도 wire. */
interface SourceFreshnessWire {
  readonly source: string;
  readonly latest_batch_at: string | null;
  readonly is_stale: boolean;
  readonly elapsed_days: number | null;
}

/** GET /api/data-freshness 응답 wire. */
interface DataFreshnessWire {
  readonly krx: SourceFreshnessWire;
  readonly dart: SourceFreshnessWire;
  readonly kosis: SourceFreshnessWire;
  readonly as_of: string;
}

// ── 공개 타입 (ADR-0033) ─────────────────────────────────────────────────────

/**
 * 백테스트 재현 결과 — ADR-0033 D3/D4/D5 (사실만, 판단 없음).
 *
 * - matches: 재현 equity curve 가 원본과 일치하는지 여부(사실).
 * - reproduceNote: 불일치 원인 메모 (있으면).
 * - packTampered: pack content_hash 불일치 — 변조 또는 pack 교체 가능성(사실).
 * - equityCurve: 재현 실행의 equity curve.
 */
export interface BacktestReproduceResult {
  readonly matches: boolean;
  readonly reproduceNote: string | null;
  readonly packTampered: boolean;
  readonly equityCurve: ReadonlyArray<EquityCurvePoint>;
}

/**
 * 소스별 데이터 신선도 — ADR-0033 D6 (사실만).
 *
 * - source: 소스 식별자 (예: "krx", "dart").
 * - latestBatchAt: 최신 배치 기준일 (ISO 8601, 없으면 null).
 * - isStale: 갱신 지연 여부 (사실).
 * - elapsedDays: 마지막 배치로부터 경과 일수 (없으면 null).
 */
export interface SourceFreshness {
  readonly source: string;
  readonly latestBatchAt: string | null;
  readonly isStale: boolean;
  readonly elapsedDays: number | null;
}

/**
 * 데이터 신선도 — ADR-0033 D6 / ADR-0036 D7 (KRX/DART/KOSIS 각 소스 신선도 + 기준 시점).
 */
export interface DataFreshness {
  readonly krx: SourceFreshness;
  readonly dart: SourceFreshness;
  readonly kosis: SourceFreshness;
  readonly asOf: string;
}

// ── snake↔camel: reproduce / freshness ───────────────────────────────────────

/**
 * BacktestFreeze(camel) → BacktestFreezeWire(snake) 역변환.
 * POST /api/backtest/reproduce 요청 body 조립용.
 */
export function freezeToWire(f: BacktestFreeze): BacktestFreezeWire {
  return {
    result_hash: f.resultHash,
    pack_content_hash: f.packContentHash,
    pack_slug: f.packSlug,
    pack_version: f.packVersion,
    krx_batch_id: f.krxBatchId,
    dart_batch_id: f.dartBatchId,
    rebalance: f.rebalance,
    start: f.start,
    end: f.end,
    cost_assumptions: {
      tax_bps: f.costAssumptions.taxBps,
      commission_bps: f.costAssumptions.commissionBps,
    },
    conditions: f.conditions,
    data_versions: f.dataVersions,
  };
}

/** wire reproduce 응답 → camelCase BacktestReproduceResult. */
function mapReproduceResult(w: BacktestReproduceResultWire): BacktestReproduceResult {
  return {
    matches: w.matches,
    reproduceNote: w.reproduce_note,
    packTampered: w.pack_tampered,
    equityCurve: mapEquityCurve(w.equity_curve),
  };
}

/** wire SourceFreshness → camelCase SourceFreshness. */
function mapSourceFreshness(w: SourceFreshnessWire): SourceFreshness {
  return {
    source: w.source,
    latestBatchAt: w.latest_batch_at,
    isStale: w.is_stale,
    elapsedDays: w.elapsed_days,
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

/**
 * POST /api/backtest/reproduce — 백테스트 재현 검증 (ADR-0033 D2~D5).
 *
 * freeze + equity_curve 를 서버에 전달해 동일 결과를 재현 가능한지 검증.
 * matches=true/false, packTampered=true/false 는 사실 값 — 판단 아님.
 *
 * @param freeze BacktestFreeze (BacktestResult.freeze 그대로)
 * @param equityCurve BacktestResult.equityCurve 그대로
 * @param customPackBody custom pack body (선택, null 이면 미전달)
 * @param signal AbortController signal
 * @returns BacktestReproduceResult (camelCase)
 * @throws ApiError — 422 검증 실패 등.
 */
export async function reproduceBacktest(
  freeze: BacktestFreeze,
  equityCurve: ReadonlyArray<EquityCurvePoint>,
  customPackBody?: object | null,
  signal?: AbortSignal,
): Promise<BacktestReproduceResult> {
  const body: BacktestReproduceBodyWire = {
    freeze: freezeToWire(freeze),
    equity_curve: equityCurve.map((pt) => ({ date: pt.date, value: pt.value })),
    ...(customPackBody !== undefined ? { custom_pack_body: customPackBody } : {}),
  };

  const wire = await fetchJson<BacktestReproduceResultWire>("/api/backtest/reproduce", {
    method: "POST",
    body,
    signal,
  });

  return mapReproduceResult(wire);
}

/**
 * GET /api/data-freshness — KRX/DART 데이터 신선도 조회 (ADR-0033 D6).
 *
 * 응답은 사실 값 — latestBatchAt(날짜), isStale(경과 여부), elapsedDays(경과 일수).
 * 판단·등락 어휘 없음.
 *
 * @param signal AbortController signal
 * @returns DataFreshness (camelCase)
 */
export async function getDataFreshness(
  signal?: AbortSignal,
): Promise<DataFreshness> {
  const wire = await fetchJson<DataFreshnessWire>("/api/data-freshness", {
    method: "GET",
    signal,
  });

  return {
    krx: mapSourceFreshness(wire.krx),
    dart: mapSourceFreshness(wire.dart),
    kosis: mapSourceFreshness(wire.kosis),
    asOf: wire.as_of,
  };
}
