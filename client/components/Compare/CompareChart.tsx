"use client";

/**
 * CompareChart — 2~6 종목 수정 종가 오버레이 차트 (lightweight-charts v5).
 *
 * 설계 결정:
 *
 * 1. 로그 Y축 절대 가격:
 *    - 각 종목의 closeAdjusted 를 PriceScaleMode.Logarithmic 으로 렌더.
 *    - 기준일=100 리베이싱(정규화) 없음 — 실제 가격 절대값 표시.
 *    - 로그 스케일 이유: 종목 간 가격 단위 차이(예: 5만원 vs 100만원)를 시각적으로
 *      완화하면서도 인위적 0% 출발점을 만들지 않는다.
 *
 * 2. 중립 팔레트 6색:
 *    - 한국 증시 관행의 등락색(빨강 상승 / 파랑 하락) 절대 사용 금지.
 *    - Active Inspection / No Advice 원칙 — 색이 판단을 암시해선 안 됨.
 *    - 종목 구분만이 목적이므로 중립 색상 팔레트 고정 6색 사용.
 *
 * 3. adj_policy 표기:
 *    - ADR-0001 D6 Fidelity 의무 — "Adjusted: v1.0 rights-only" 상시 표시.
 *    - 현 정책은 단일 상수(`v1.0:rights-only`) — backend 변경 없이 프론트 상수.
 *
 * 4. useQueries 선택 이유:
 *    - 종목마다 독립적인 prices endpoint — 단일 쿼리로 묶을 수 없음.
 *    - useQueries 는 TanStack Query 의 병렬 쿼리 hook으로 각 쿼리의
 *      loading/error 상태를 배열로 관리. 코드베이스 최초 도입.
 *
 * 5. lifecycle 보장 (PriceChart 226-229행 주석 패턴 동일):
 *    - 차트 컨테이너 div 는 항상 mount — createChart effect([]) 가 마운트 시
 *      ref 를 확보해야 비동기 데이터 도착 후 series effect 가 채울 수 있음.
 *    - 로딩/에러/빈 상태는 absolute overlay 로 표시.
 *
 * 관련:
 *   - T58 / AC-F-05 Compare 차트 오버레이.
 *   - ADR-0001 D6 — adj_policy Fidelity.
 *   - ADR-0008 D8 — 일 단위 PIT.
 */

import { useQueries } from "@tanstack/react-query";
import { LineSeries, PriceScaleMode, createChart } from "lightweight-charts";
import { useTranslations } from "next-intl";
import { useEffect, useRef } from "react";

import { fetchStockPrices } from "@/lib/api/prices";
import type { StockDetail } from "@/lib/api/stocks";
import { cn } from "@/lib/utils";

// ─────────────────────────────────────────────────────────────────────────────
// 상수
// ─────────────────────────────────────────────────────────────────────────────

/** 차트 높이(px) — 고정. PriceChart 와 동일 규격. */
const CHART_HEIGHT = 320;

/**
 * ADR-0001 D6 수정 종가 정책 상수.
 * 차트 캡션에 항상 표시 — 현재 단일 정책이므로 backend 연동 불필요.
 */
const ADJ_POLICY_LABEL = "Adjusted: v1.0 rights-only";

/**
 * 종목 구분용 중립 팔레트 6색.
 *
 * 한국 증시 등락색(빨강 상승/파랑·녹색 하락) 회피.
 * 구분 목적만 — 판단 암시 없는 중립 톤.
 * index 로 고정 매핑하여 종목 순서 변경 시에도 색 안정.
 */
export const COMPARE_COLORS: ReadonlyArray<string> = [
  "#2563eb", // 파랑
  "#d97706", // 주황
  "#7c3aed", // 보라
  "#0891b2", // 청록
  "#db2777", // 분홍
  "#64748b", // 슬레이트
];

/**
 * 종목 index 로 팔레트 색 반환.
 * index 가 팔레트 범위 초과 시 마지막 색으로 fallback (최대 6 종목 제약 상 미발생).
 */
export function getCompareColor(index: number): string {
  return COMPARE_COLORS[Math.min(index, COMPARE_COLORS.length - 1)] ?? "#64748b";
}

// ─────────────────────────────────────────────────────────────────────────────
// 순수 변환 함수 (테스트 가능)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * PriceBar[] → lightweight-charts LineSeries 데이터 형식 변환.
 * closeAdjusted 만 사용 — 오버레이는 수정 종가 단일 시리즈.
 */
export function barsToSeriesData(
  bars: ReadonlyArray<{ date: string; closeAdjusted: number }>,
): Array<{ time: `${number}-${number}-${number}`; value: number }> {
  return bars.map((b) => ({
    time: b.date as `${number}-${number}-${number}`,
    value: b.closeAdjusted,
  }));
}

// ─────────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────────

interface CompareChartProps {
  /**
   * 비교할 종목 목록 — StockDetail 의 code / name 만 사용.
   * compare page 의 query.data.items 를 그대로 전달.
   */
  readonly items: ReadonlyArray<Pick<StockDetail, "code" | "name">>;
  /** PIT 기준 일자 ("YYYY-MM-DD"). */
  readonly asOf: string;
  readonly className?: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// 컴포넌트
// ─────────────────────────────────────────────────────────────────────────────

/**
 * CompareChart — 2~6 종목 수정 종가 오버레이 차트.
 *
 * lifecycle 보장:
 *   1. containerRef 가 확보되면 createChart 호출 (effect 빈 deps 배열, 마운트 1회).
 *   2. 각 종목 prices 데이터 변경 시 series 재생성 (chart instance 유지).
 *   3. 언마운트 시 chart.remove() → canvas/이벤트 리스너 정리.
 *   4. ResizeObserver 로 컨테이너 폭 변경 감지 → chart.applyOptions({width}).
 */
export function CompareChart({
  items,
  asOf,
  className,
}: CompareChartProps): JSX.Element {
  const t = useTranslations("compare");

  // ── 병렬 가격 fetch ──────────────────────────────────────────────────────
  // useQueries: 종목마다 독립 endpoint → 병렬 fetch.
  // PriceChart 의 queryKey 컨벤션 ["prices", code, asOf] 그대로 사용 — 캐시 공유.
  const results = useQueries({
    queries: items.map((item) => ({
      queryKey: ["prices", item.code, asOf] as const,
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchStockPrices(item.code, asOf, signal),
      staleTime: 5 * 60 * 1000, // 5분 — PriceChart 와 동일
    })),
  });

  // ── 집계 상태 ────────────────────────────────────────────────────────────
  const isAnyLoading = results.some((r) => r.isLoading);
  const isAllError = results.length > 0 && results.every((r) => r.isError);
  const firstError = results.find((r) => r.isError)?.error;

  // 전체 종목 중 bars 가 있는 것이 하나도 없으면 빈 상태.
  const isEmpty =
    !isAnyLoading &&
    !isAllError &&
    results.every((r) => !r.isLoading && !r.isError && (r.data?.bars.length ?? 0) === 0);

  // series 재생성 effect 의 안정적 트리거 — useQueries 는 리렌더마다 새 results
  // 배열 참조를 반환하므로 `[results]` 를 deps 로 쓰면 부모 리렌더(예: compare
  // page 입력창 타이핑)마다 series 가 전부 제거·재생성되어 깜빡임/성능 저하.
  // 데이터가 실제로 갱신된 경우(fetch 성공·상태 변화)에만 재실행하도록 각 쿼리의
  // status + dataUpdatedAt(데이터 갱신 시각) 을 직렬화한 시그니처를 deps 로 쓴다.
  const dataSignature = results
    .map((r) => `${r.status}:${r.dataUpdatedAt}`)
    .join("|");

  // ── chart refs ───────────────────────────────────────────────────────────
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null);

  // ── chart 생성 + cleanup (마운트 1회) ────────────────────────────────────
  // 컨테이너 div 는 항상 mount — 로딩/에러/빈 상태를 absolute overlay 로 처리.
  // 조건부 unmount 시 createChart effect 가 ref 를 못 잡아 데이터 도착 후 차트가
  // 영영 안 그려지는 lifecycle 버그 회피 (PriceChart 226-229행 패턴 동일).
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: CHART_HEIGHT,
      layout: {
        background: { color: "#ffffff" },
        textColor: "#404040",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#f0f0f0" },
        horzLines: { color: "#f0f0f0" },
      },
      // 로그 스케일: 종목 간 가격 단위 차이를 시각적으로 완화.
      // PriceScaleMode.Logarithmic = 1 (enum 값).
      rightPriceScale: {
        borderColor: "#e5e5e5",
        mode: PriceScaleMode.Logarithmic,
      },
      timeScale: {
        borderColor: "#e5e5e5",
        timeVisible: false,
      },
    });
    chartRef.current = chart;

    // ResizeObserver — 컨테이너 폭 변경 감지.
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const newWidth = entry.contentRect.width;
        if (newWidth > 0) {
          chart.applyOptions({ width: newWidth });
        }
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
    };
    // containerRef.current 는 ref — 의존성 배열에 포함 불필요 (마운트 시 1회).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── series 재생성 (데이터 변경 시) ──────────────────────────────────────
  // items 또는 results 변경 시 기존 series 를 removeSeries 로 제거 후 재생성.
  // chart instance 는 유지 — 불필요한 전체 재초기화 없음.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    // 데이터가 있는 결과만 series 로 추가.
    // 종목 index 로 색 매핑 — 종목 순서 변경 시 색도 함께 이동.
    const seriesList: ReturnType<typeof chart.addSeries>[] = [];

    let hasAnyBars = false;

    for (let i = 0; i < results.length; i++) {
      const result = results[i];
      const bars = result?.data?.bars;
      if (!bars || bars.length === 0) continue;

      hasAnyBars = true;
      const color = getCompareColor(i);

      const series = chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
      });
      series.setData(barsToSeriesData(bars));
      seriesList.push(series);
    }

    if (hasAnyBars) {
      chart.timeScale().fitContent();
    }

    // cleanup: 다음 effect 실행 전 series 제거.
    return () => {
      for (const s of seriesList) {
        chart.removeSeries(s);
      }
    };
  // deps = dataSignature — 데이터 갱신 시각/상태가 바뀔 때만 재실행. effect 내부는
  // 최신 results 클로저를 읽으므로 exhaustive-deps 를 의도적으로 비활성화한다
  // (dataSignature 가 results 의 데이터 변화를 대표하는 안정 키).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataSignature]);

  // ── 렌더 ─────────────────────────────────────────────────────────────────
  return (
    <div
      className={cn("rounded-lg border border-neutral-200 bg-white p-4", className)}
    >
      {/* 헤더: 제목 + adj_policy 표기 (ADR-0001 D6 Fidelity 의무) */}
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-medium text-neutral-700">
          {t("chartTitle")}
        </span>
        {/* adj_policy: 항상 표시 — 현 정책 단일 상수, backend 연동 불필요. */}
        <span
          className="text-xs text-neutral-400"
          aria-label={t("adjPolicyAriaLabel")}
        >
          {ADJ_POLICY_LABEL}
        </span>
      </div>

      {/* 종목 범례 — 색 ↔ 종목명/코드 매핑. 해석/순위/등락 텍스트 없음 (Observation). */}
      {items.length > 0 && (
        <div
          className="mb-3 flex flex-wrap gap-x-4 gap-y-1"
          aria-label={t("legendAriaLabel")}
        >
          {items.map((item, i) => (
            <div
              key={item.code}
              className="flex items-center gap-1.5 text-xs text-neutral-700"
            >
              {/* 색 스와치 — 중립 팔레트, 등락 의미 없음. */}
              <span
                className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: getCompareColor(i) }}
                aria-hidden="true"
              />
              <span className="font-medium">{item.name}</span>
              <span className="font-mono text-neutral-400">{item.code}</span>
            </div>
          ))}
        </div>
      )}

      {/* 차트 컨테이너 — 항상 mount (lifecycle 보장, PriceChart 패턴 동일).
          로딩/에러/빈 상태는 absolute overlay 로 표시. */}
      <div className="relative" style={{ height: CHART_HEIGHT }}>
        <div ref={containerRef} style={{ height: CHART_HEIGHT }} />

        {isAnyLoading && (
          <div
            className="absolute inset-0 animate-pulse rounded bg-neutral-100"
            aria-label={t("chartLoadingAriaLabel")}
          />
        )}

        {isAllError && !isAnyLoading && (
          <div className="absolute inset-0 flex items-center justify-center rounded-md border border-red-200 bg-red-50 px-4 text-center text-sm text-red-800">
            {t("chartLoadError", {
              message: (firstError as Error | undefined)?.message ?? t("chartLoadErrorUnknown"),
            })}
          </div>
        )}

        {isEmpty && (
          <div className="absolute inset-0 flex items-center justify-center rounded bg-neutral-50 text-sm text-neutral-500">
            {t("chartEmpty")}
          </div>
        )}
      </div>
    </div>
  );
}
