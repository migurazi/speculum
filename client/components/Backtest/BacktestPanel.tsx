"use client";

/**
 * BacktestPanel — Factor Pack 백테스트 실행 및 결과 표시 UI (ADR-0027 D1–D6).
 *
 * 흐름:
 *   1. pack slug + version + 기간 + rebalance + 거래비용 + 스크리닝 조건 입력.
 *   2. POST /api/backtest → BacktestResult.
 *   3. 결과 표시: equity curve + 통계표 + freeze 정보 + 디스클레이머 게이트.
 *
 * No Advice 시각 경계 (ADR-0027 D4/D6 — 절대 준수):
 *   - grayscale equity curve — BACKTEST_PANEL_CURVE_LINE 단일 중립색 라인.
 *     등락색(녹수익/적손실), 0선 기준 색분기 일절 금지.
 *   - 중립 통계표 — CAGR/누적수익률/MDD/변동성/turnover 사실값만.
 *     "수익률 N%" headline 대형 강조 금지(다른 통계와 동일 비중·중립 톤).
 *     등급/별점/"우수" 라벨 0.
 *   - 거래비용 가정(cost_assumptions) 결과에 명시 표시 (D2 — 숨기지 않음).
 *   - 과거성과 디스클레이머 게이트(D4): disclaimer_required=true 면
 *     디스클레이머 없이 결과 렌더 차단 (항상 결과와 함께 표시).
 *   - survivorship 디스클로저(D3): survivorship_complete=false 면
 *     강제 경고 박스(neutral 톤, red 금지).
 *   - pack 간 비교/순위 UI 0 — 단일 pack 백테스트만. 자동 정렬/강조 0.
 *
 * 색은 명명 상수(BACKTEST_PANEL_*) 로 export — backtest-visual-gate.test.tsx
 * 가 실제 렌더 색과 1:1 대조해 등락색/컬러맵 회귀를 자동 차단한다.
 *
 * 관련:
 *   - lib/api/backtest.ts — runBacktest / BacktestResult.
 *   - components/__tests__/backtest-visual-gate.test.tsx — 시각 게이트.
 */

import { useMutation } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { LineSeries, createChart } from "lightweight-charts";

import { runBacktest } from "@/lib/api/backtest";
import type { BacktestResult, RunBacktestParams } from "@/lib/api/backtest";
import {
  SCREEN_OP_OPTIONS,
  type ScreenCondition,
  type ScreenOp,
} from "@/lib/api/screen";
import { cn } from "@/lib/utils";

// ── No Advice 시각 상수 (backtest-visual-gate 패턴 — 이 상수를 테스트가 검증) ──

/**
 * equity curve 라인 색 — grayscale 단일색. 수익/손실 색분기 금지.
 * ADR-0027 D4-b: 등락색(녹수익/적손실) 일절 금지.
 * backtest-visual-gate.test.tsx 가 이 상수를 검증한다.
 */
export const BACKTEST_PANEL_CURVE_LINE = "#525252"; // neutral-600

/**
 * equity curve 차트 배경 — 중립 흰색.
 */
export const BACKTEST_PANEL_CURVE_BG = "#ffffff";

/**
 * 통계 행 텍스트 톤 — grayscale neutral. 수치 강조·판단색 0.
 * ADR-0027 D6: 통계는 동일 비중·중립 톤.
 */
export const BACKTEST_PANEL_STAT_TEXT = "text-neutral-800";

/**
 * 통계 레이블 톤 — grayscale neutral.
 */
export const BACKTEST_PANEL_STAT_LABEL = "text-neutral-600";

/**
 * 통계 행 배경(기본) — 판단색 아님.
 */
export const BACKTEST_PANEL_STAT_ROW_BG = "bg-white";

/**
 * 통계 행 배경(줄무늬) — grayscale 가독성용.
 */
export const BACKTEST_PANEL_STAT_ROW_STRIPE = "bg-neutral-50";

/**
 * survivorship 경고 박스 톤 — neutral(경고용 red 금지, ADR-0027 D3).
 * 경고이지만 색으로 판단 유도 금지 — 사실 공시만.
 */
export const BACKTEST_PANEL_SURVIVORSHIP_WARNING = "bg-neutral-100 border-neutral-300 text-neutral-700";

/**
 * 디스클레이머 박스 톤 — neutral 계열.
 */
export const BACKTEST_PANEL_DISCLAIMER = "bg-neutral-50 border-neutral-200 text-neutral-700";

// ── 상수 ──────────────────────────────────────────────────────────────────────

const EMPTY_CONDITION: ScreenCondition = { factor: "", op: "<", value: "" };

/** equity curve 차트 높이(px) — 고정. */
const CURVE_CHART_HEIGHT = 240;

// ── 유틸 ──────────────────────────────────────────────────────────────────────

/**
 * Decimal string → 퍼센트 포맷 (소수 2자리).
 * "0.331" → "+33.10%", "-0.05" → "-5.00%"
 */
function formatPct(value: string | null): string {
  if (value === null) return "N/A";
  const n = parseFloat(value);
  if (isNaN(n)) return "N/A";
  const sign = n >= 0 ? "+" : "";
  return `${sign}${(n * 100).toFixed(2)}%`;
}

// ── EquityCurveChart ──────────────────────────────────────────────────────────

interface EquityCurveChartProps {
  readonly data: ReadonlyArray<{ date: string; value: string }>;
}

/**
 * EquityCurveChart — lightweight-charts LineSeries 로 렌더.
 *
 * ADR-0027 D4-b:
 *   - 단일 grayscale 라인(BACKTEST_PANEL_CURVE_LINE).
 *   - 등락 색분기 금지(0선 기준 색 변경 금지).
 *   - 가격 label/marker/annotation 없음.
 */
function EquityCurveChart({ data }: EquityCurveChartProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const t = useTranslations("backtest");

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    if (data.length === 0) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: CURVE_CHART_HEIGHT,
      layout: {
        background: { color: BACKTEST_PANEL_CURVE_BG },
        textColor: "#737373", // neutral-500
      },
      grid: {
        vertLines: { color: "#f5f5f5" }, // neutral-100
        horzLines: { color: "#f5f5f5" },
      },
      rightPriceScale: {
        borderColor: "#e5e5e5", // neutral-200
      },
      timeScale: {
        borderColor: "#e5e5e5",
      },
      crosshair: {
        vertLine: { color: "#a3a3a3" }, // neutral-400
        horzLine: { color: "#a3a3a3" },
      },
    });

    // 단일 grayscale 라인 — 등락 색분기 없음 (ADR-0027 D4-b).
    const series = chart.addSeries(LineSeries, {
      color: BACKTEST_PANEL_CURVE_LINE,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    const chartData = data.map((pt) => ({
      time: pt.date as `${number}-${number}-${number}`,
      value: parseFloat(pt.value),
    }));

    series.setData(chartData);
    chart.timeScale().fitContent();

    // 반응형 폭 — ResizeObserver.
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        chart.applyOptions({ width: entry.contentRect.width });
      }
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [data]);

  if (data.length === 0) {
    return (
      <p className="text-xs text-neutral-500">
        {t("result.equityCurveNote")}
      </p>
    );
  }

  return (
    <div>
      <div
        ref={containerRef}
        data-testid="backtest-equity-curve-chart"
        style={{ height: CURVE_CHART_HEIGHT }}
        className="w-full"
      />
      <p className="mt-1 text-[11px] text-neutral-500">
        {t("result.equityCurveNote")}
      </p>
    </div>
  );
}

// ── BacktestResultPanel ───────────────────────────────────────────────────────

interface BacktestResultPanelProps {
  readonly result: BacktestResult;
}

/**
 * BacktestResultPanel — 결과 표시 + 게이트 (ADR-0027 D3/D4/D6).
 *
 * disclaimer_required=true 면 디스클레이머가 항상 결과와 함께 표시(게이트).
 * survivorship_complete=false 면 survivorship 경고 박스 강제 표시.
 */
function BacktestResultPanel({ result }: BacktestResultPanelProps): JSX.Element {
  const t = useTranslations("backtest");

  const { freeze, stats, equityCurve, survivorshipComplete, missingPriceRatio, disclaimerRequired } = result;

  // 누락 비율 퍼센트 변환 (0.05 → "5.00").
  const missingPct = (parseFloat(missingPriceRatio) * 100).toFixed(2);

  return (
    <div
      data-testid="backtest-result-panel"
      className="space-y-4"
    >
      {/* ── D4 과거성과 디스클레이머 게이트 ─────────────────────────────────── */}
      {/* disclaimer_required=true 면 항상 결과와 함께 표시. 차단 게이트(없으면 렌더 안 됨). */}
      {disclaimerRequired ? (
        <div
          data-testid="backtest-disclaimer"
          className={cn(
            "rounded-md border px-3 py-2 text-xs",
            BACKTEST_PANEL_DISCLAIMER,
          )}
        >
          {t("disclaimer.required")}
        </div>
      ) : null}

      {/* ── D3 survivorship 디스클로저 ────────────────────────────────────── */}
      {/* survivorship_complete=false 면 결과 표면에 강제 경고. neutral 톤(red 금지). */}
      {!survivorshipComplete ? (
        <div
          data-testid="backtest-survivorship-warning"
          className={cn(
            "rounded-md border px-3 py-2 text-xs space-y-1",
            BACKTEST_PANEL_SURVIVORSHIP_WARNING,
          )}
        >
          <p className="font-medium">{t("survivorship.warning")}</p>
          <p>{t("survivorship.missingRatio", { ratio: missingPct })}</p>
          <p className="text-neutral-600">{t("survivorship.detail")}</p>
        </div>
      ) : null}

      {/* ── D2 거래비용 가정 명시 ─────────────────────────────────────────── */}
      {/* 거래비용 가정 숨기지 않음 — 결과와 함께 항상 표시. */}
      <div
        data-testid="backtest-cost-assumptions"
        className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2"
      >
        <p className="text-xs font-medium text-neutral-700 mb-1">
          {t("result.costAssumptionsHeading")}
        </p>
        <div className="flex flex-wrap gap-4 text-xs text-neutral-600">
          <span>
            {t("result.commissionBps")}{" "}
            <span className="font-mono text-neutral-800">
              {freeze.costAssumptions.commissionBps} {t("result.bpsSuffix")}
            </span>
          </span>
          <span>
            {t("result.taxBps")}{" "}
            <span className="font-mono text-neutral-800">
              {freeze.costAssumptions.taxBps} {t("result.bpsSuffix")}
            </span>
          </span>
        </div>
      </div>

      {/* ── D6 통계표 — 사실 통계만, 동일 비중, 평가/등급 0 ─────────────── */}
      {/* "수익률 N%" headline 대형 강조 금지. 모든 통계 동일 비중·중립 톤. */}
      <div data-testid="backtest-stats-table">
        <p className="text-xs font-medium text-neutral-700 mb-2">
          {t("result.statsHeading")}
        </p>
        <table className="w-full text-xs border-collapse">
          <tbody>
            {[
              { label: t("result.statCumulativeReturn"), value: formatPct(stats.cumulativeReturn) },
              { label: t("result.statCagr"), value: formatPct(stats.cagr) },
              { label: t("result.statMdd"), value: formatPct(stats.mdd) },
              { label: t("result.statVolatility"), value: formatPct(stats.volatility) },
              { label: t("result.statTurnover"), value: formatPct(stats.turnover) },
            ].map((row, i) => (
              <tr
                key={row.label}
                className={cn(
                  "border-b border-neutral-100 last:border-0",
                  i % 2 === 0 ? BACKTEST_PANEL_STAT_ROW_BG : BACKTEST_PANEL_STAT_ROW_STRIPE,
                )}
              >
                <td className={cn("py-1.5 px-2", BACKTEST_PANEL_STAT_LABEL)}>
                  {row.label}
                </td>
                {/* 통계 값 — 중립 톤, 강조 없음. 수익/손실 색분기 금지. */}
                <td
                  className={cn("py-1.5 px-2 font-mono text-right", BACKTEST_PANEL_STAT_TEXT)}
                  data-testid={`backtest-stat-${row.label}`}
                >
                  {row.value}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── equity curve 차트 ────────────────────────────────────────────── */}
      <div data-testid="backtest-curve-section">
        <p className="text-xs font-medium text-neutral-700 mb-2">
          {t("result.equityCurveHeading")}
        </p>
        <EquityCurveChart data={equityCurve} />
      </div>

      {/* ── freeze 정보 ───────────────────────────────────────────────────── */}
      <details className="rounded-md border border-neutral-200 bg-neutral-50">
        <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-neutral-700">
          {t("result.freezeHeading")}
        </summary>
        <div className="px-3 pb-3 pt-1 space-y-1 text-[11px] font-mono text-neutral-600">
          <p>
            <span className="text-neutral-400">{t("result.resultHash")}:</span>{" "}
            {freeze.resultHash}
          </p>
          <p>
            <span className="text-neutral-400">{t("result.packContentHash")}:</span>{" "}
            {freeze.packContentHash}
          </p>
          <p>
            <span className="text-neutral-400">{t("result.rebalance")}:</span>{" "}
            {freeze.rebalance}
          </p>
          <p>
            <span className="text-neutral-400">{t("result.period")}:</span>{" "}
            {freeze.start} ~ {freeze.end}
          </p>
          {freeze.krxBatchId ? (
            <p>
              <span className="text-neutral-400">{t("result.krxBatchId")}:</span>{" "}
              {freeze.krxBatchId}
            </p>
          ) : null}
          {freeze.dartBatchId ? (
            <p>
              <span className="text-neutral-400">{t("result.dartBatchId")}:</span>{" "}
              {freeze.dartBatchId}
            </p>
          ) : null}
        </div>
      </details>
    </div>
  );
}

// ── BacktestPanel (메인) ──────────────────────────────────────────────────────

/**
 * BacktestPanel props — 필요 시 외부 pack 정보를 주입 가능.
 * 주입 없으면 폼 내 직접 입력.
 */
export interface BacktestPanelProps {
  /** 초기 pack slug (선택 — 외부 주입 시 폼에 미리 채워짐). */
  readonly initialPackSlug?: string;
  /** 초기 pack version (선택). */
  readonly initialPackVersion?: string;
}

/**
 * BacktestPanel — 백테스트 입력 폼 + 결과 표시.
 *
 * 단일 pack 백테스트만 지원. pack 간 비교/순위 UI 없음 (ADR-0027 D4-c/D4-d).
 */
export function BacktestPanel({
  initialPackSlug = "",
  initialPackVersion = "0.1.0",
}: BacktestPanelProps): JSX.Element {
  const t = useTranslations("backtest");

  // ── 폼 상태 ───────────────────────────────────────────────────────────────

  const [packSlug, setPackSlug] = useState(initialPackSlug);
  const [packVersion, setPackVersion] = useState(initialPackVersion);
  const [start, setStart] = useState("2022-01-01");
  const [end, setEnd] = useState("2022-12-31");
  const [rebalance, setRebalance] = useState<"quarterly" | "monthly">("quarterly");
  const [commissionBpsStr, setCommissionBpsStr] = useState("1.5");
  const [taxBpsStr, setTaxBpsStr] = useState("15");
  const [conditions, setConditions] = useState<ReadonlyArray<ScreenCondition>>([
    EMPTY_CONDITION,
  ]);

  /** 실행 결과 freeze — 실행 시점 결과 고정(자동 정렬 금지). */
  const [executedResult, setExecutedResult] = useState<BacktestResult | null>(null);

  // ── mutation ──────────────────────────────────────────────────────────────

  const mutation = useMutation<BacktestResult, Error, RunBacktestParams>({
    mutationFn: (params) => runBacktest(params),
    onSuccess: (result) => {
      setExecutedResult(result);
    },
  });

  // ── 조건 편집 핸들러 ───────────────────────────────────────────────────────

  const updateCondition = (index: number, patch: Partial<ScreenCondition>): void => {
    setConditions((prev) =>
      prev.map((c, i) => (i === index ? { ...c, ...patch } : c)),
    );
  };

  const addCondition = (): void => {
    setConditions((prev) => [...prev, EMPTY_CONDITION]);
  };

  const removeCondition = (index: number): void => {
    setConditions((prev) => prev.filter((_, i) => i !== index));
  };

  // ── 실행 가능 여부 ─────────────────────────────────────────────────────────

  const canRun =
    packSlug.length > 0
    && packVersion.length > 0
    && start.length > 0
    && end.length > 0
    && conditions.length > 0
    && conditions.every((c) => c.factor.length > 0 && c.value.length > 0)
    && !mutation.isPending;

  // ── 실행 핸들러 ───────────────────────────────────────────────────────────

  const handleRun = (): void => {
    const commissionBps = parseFloat(commissionBpsStr);
    const taxBps = parseFloat(taxBpsStr);
    mutation.mutate({
      packSlug,
      packVersion,
      conditions,
      start,
      end,
      rebalance,
      ...(isNaN(commissionBps) ? {} : { costCommissionBps: commissionBps }),
      ...(isNaN(taxBps) ? {} : { costTaxBps: taxBps }),
    });
  };

  // ── 렌더 ───────────────────────────────────────────────────────────────────

  return (
    <div
      data-testid="backtest-panel"
      className="space-y-5"
    >
      {/* 헤더 */}
      <div>
        <p className="text-sm font-semibold text-neutral-900">{t("title")}</p>
        <p className="mt-0.5 text-xs text-neutral-500">{t("description")}</p>
      </div>

      {/* ── 입력 폼 ────────────────────────────────────────────────────────── */}
      <div className="space-y-4 rounded-lg border border-neutral-200 bg-neutral-50 p-4">

        {/* pack slug + version */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block text-xs font-medium text-neutral-600">
            {t("form.packLabel")}
            <input
              type="text"
              value={packSlug}
              onChange={(e) => setPackSlug(e.target.value)}
              placeholder={t("form.packPlaceholder")}
              className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
            />
          </label>
          <label className="block text-xs font-medium text-neutral-600">
            {t("form.packVersionLabel")}
            <input
              type="text"
              value={packVersion}
              onChange={(e) => setPackVersion(e.target.value)}
              placeholder={t("form.packVersionPlaceholder")}
              className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
            />
          </label>
        </div>

        {/* 기간 */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="block text-xs font-medium text-neutral-600">
            {t("form.startLabel")}
            <input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-xs"
            />
          </label>
          <label className="block text-xs font-medium text-neutral-600">
            {t("form.endLabel")}
            <input
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-xs"
            />
          </label>
          <label className="block text-xs font-medium text-neutral-600">
            {t("form.rebalanceLabel")}
            <select
              value={rebalance}
              onChange={(e) => setRebalance(e.target.value as "quarterly" | "monthly")}
              className="mt-1 w-full rounded border border-neutral-300 bg-white px-2 py-1 text-xs"
            >
              <option value="quarterly">{t("form.rebalanceQuarterly")}</option>
              <option value="monthly">{t("form.rebalanceMonthly")}</option>
            </select>
          </label>
        </div>

        {/* 거래비용 가정 — D2 강제 노출. 0 으로 숨길 수 없음. */}
        <div>
          <p className="text-xs font-medium text-neutral-700">
            {t("form.costHeading")}
          </p>
          <p className="mt-0.5 text-[11px] text-neutral-500">
            {t("form.costHint")}
          </p>
          <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block text-xs font-medium text-neutral-600">
              {t("form.commissionBpsLabel")}
              <input
                type="number"
                value={commissionBpsStr}
                onChange={(e) => setCommissionBpsStr(e.target.value)}
                placeholder={t("form.commissionBpsPlaceholder")}
                min="0.001"
                step="0.1"
                className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
              />
            </label>
            <label className="block text-xs font-medium text-neutral-600">
              {t("form.taxBpsLabel")}
              <input
                type="number"
                value={taxBpsStr}
                onChange={(e) => setTaxBpsStr(e.target.value)}
                placeholder={t("form.taxBpsPlaceholder")}
                min="0.001"
                step="1"
                className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
              />
            </label>
          </div>
        </div>

        {/* 스크리닝 조건 */}
        <div className="space-y-2">
          <p className="text-xs font-medium text-neutral-700">
            {t("form.conditionsLabel")}
          </p>
          {conditions.length === 0 ? (
            <p className="text-xs text-neutral-500">{t("form.noConditions")}</p>
          ) : (
            <ul className="space-y-2">
              {conditions.map((condition, index) => (
                <li
                  key={index}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-neutral-200 bg-white p-2"
                >
                  {/* factor canonical_id 직접 입력 */}
                  <input
                    type="text"
                    value={condition.factor}
                    onChange={(e) => updateCondition(index, { factor: e.target.value })}
                    placeholder={t("form.factorPlaceholder")}
                    aria-label={t("form.factorAriaLabel", { index: index + 1 })}
                    className="min-w-0 flex-1 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
                  />
                  {/* 연산자 */}
                  <select
                    value={condition.op}
                    onChange={(e) => updateCondition(index, { op: e.target.value as ScreenOp })}
                    aria-label={t("form.opAriaLabel", { index: index + 1 })}
                    className="rounded border border-neutral-300 bg-white px-2 py-1 font-mono text-xs"
                  >
                    {SCREEN_OP_OPTIONS.map((op) => (
                      <option key={op} value={op}>{op}</option>
                    ))}
                  </select>
                  {/* 값 */}
                  <input
                    type="text"
                    value={condition.value}
                    onChange={(e) => updateCondition(index, { value: e.target.value })}
                    placeholder={t("form.valuePlaceholder")}
                    aria-label={t("form.valueAriaLabel", { index: index + 1 })}
                    className="w-28 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
                  />
                  {/* 삭제 */}
                  <button
                    type="button"
                    onClick={() => removeCondition(index)}
                    aria-label={t("form.removeConditionAriaLabel", { index: index + 1 })}
                    className="rounded p-1 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
                  >
                    &times;
                  </button>
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            onClick={addCondition}
            className="inline-flex items-center gap-1 rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50"
          >
            + {t("form.addCondition")}
          </button>
        </div>

        {/* 실행 버튼 */}
        <button
          type="button"
          onClick={handleRun}
          disabled={!canRun}
          data-testid="backtest-run-btn"
          className="inline-flex items-center rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-neutral-300 hover:bg-neutral-800"
        >
          {mutation.isPending ? t("form.running") : t("form.runButton")}
        </button>
      </div>

      {/* 실행 에러 */}
      {mutation.isError ? (
        <p
          data-testid="backtest-run-error"
          className="text-xs text-neutral-700"
        >
          {t("form.runError", { message: mutation.error.message })}
        </p>
      ) : null}

      {/* ── 결과 — disclaimer_required=true 시 항상 결과와 함께 표시(D4 게이트) */}
      {executedResult !== null ? (
        <BacktestResultPanel result={executedResult} />
      ) : null}
    </div>
  );
}
