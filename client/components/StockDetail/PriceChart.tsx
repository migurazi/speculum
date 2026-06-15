"use client";

/**
 * PriceChart — 종목 가격 캔들스틱 차트 (lightweight-charts v5).
 *
 * 기능:
 *   - useQuery 로 fetchStockPrices 호출, staleTime=5분.
 *   - 원본 종가(raw) / 수정 종가(adjusted) 토글.
 *     · raw: 정규 OHLC 캔들스틱.
 *     · adjusted: close_adjusted 기반 라인 시리즈 (open/high/low/close 대체 불가,
 *       수정 종가 라인으로 표시 — ADR-0001 D6 토글 의도).
 *   - 로딩: 스켈레톤 플레이스홀더.
 *   - 에러: 에러 메시지 박스.
 *   - 빈 bars: "해당 기간 가격 데이터 없음" 안내.
 *   - 반응형 폭: ResizeObserver → chart.applyOptions({width}).
 *   - 높이 고정: 320px.
 *   - chart.remove() cleanup — 언마운트·재생성 시 누수 방지.
 *   - 투자권유 어휘 0 (No Advice — 가격/거래량 사실만).
 *
 * lightweight-charts v5 API:
 *   - createChart(container, options)
 *   - chart.addSeries(CandlestickSeries, options)
 *   - chart.addSeries(LineSeries, options)
 *   - series.setMarkers([...]) — CA ▾ 마커 (raw 모드만, ADR-0001 D6)
 *   - chart.remove() — cleanup
 *
 * 관련:
 *   - M0_PLAN PriceChart.
 *   - ADR-0008 D8 — 일 단위 PIT.
 *   - ADR-0001 D6 — raw/adjusted 토글.
 */

import { useQuery } from "@tanstack/react-query";
import {
  CandlestickSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
} from "lightweight-charts";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import type { CorporateAction, StockPricesResponse } from "@/lib/api/prices";
import { fetchStockPrices } from "@/lib/api/prices";
import { cn } from "@/lib/utils";

/** 표시 모드: 원본 종가 캔들스틱 또는 수정 종가 라인. */
type PriceMode = "raw" | "adjusted";

interface PriceChartProps {
  /** KRX 종목코드. */
  readonly code: string;
  /** PIT 기준 일자 ("YYYY-MM-DD"). */
  readonly asOf: string;
  readonly className?: string;
}

/** 차트 높이(px) — 고정. */
const CHART_HEIGHT = 320;

/**
 * 캔들 색상 — 한국 증시 관행 (빨강 양봉=상승 / 파랑 음봉=하락).
 *
 * ADR-0007 D8.3 M1 결정 (T59 차트 시각요소 review gate). lightweight-charts
 * default 는 서구 관행(녹색 상승/빨강 하락)이라 override 한다. 이 색은 "그날
 * 종가 > 시가" 라는 **등락 사실**의 표시이며, No Advice(8 기둥 §2.2)가 금지하는
 * 매매 신호·추천·점수 라벨과는 구분된다 — 차트에 "매수/매도/추천" 어휘·랭킹·
 * 판단 annotation 을 일절 넣지 않는다(사실인 corporate action 일자 외 annotation
 * 금지). 네이버/KRX/키움과 동일 관행이라 사용자 인지 부담을 최소화(§2.5).
 *
 * 회귀 방지: `__tests__/chart-visual-gate.test.tsx` 가 본 상수가 한국 관행
 * (상승=빨강 계열 / 하락=파랑 계열) 임을, 그리고 서구 녹색(#16a34a 등) 상승색이
 * 재도입되지 않음을 검증한다.
 */
export const CANDLE_UP_COLOR = "#dc2626"; // 빨강 — 양봉(상승)
export const CANDLE_DOWN_COLOR = "#2563eb"; // 파랑 — 음봉(하락)

/**
 * Corporate action ▾ 마커 색 — 중립(회색). 판단색(빨강=나쁨 등) 사용 금지 (No Advice).
 * 마커는 일자(사실)와 action_type 한국어 명칭만 표시.
 */
const CA_MARKER_COLOR = "#737373"; // neutral-500

/**
 * action_type → 한국어 표시명 매핑.
 * 알 수 없는 action_type 은 그대로 표시(번역 강제 금지).
 */
const ACTION_TYPE_LABEL: Readonly<Record<string, string>> = {
  split: "액면분할",
  dividend: "배당",
  merger: "합병",
};

/**
 * CorporateAction 목록을 lightweight-charts v5 SeriesMarkerBar 형식으로 변환.
 * raw 모드 전용 (ADR-0001 D6 — adjusted 모드엔 마커 없음).
 * v5 에서는 series.setMarkers 대신 createSeriesMarkers(series, markers) 사용.
 */
function buildCaMarkers(actions: ReadonlyArray<CorporateAction>): Array<{
  time: `${number}-${number}-${number}`;
  position: "aboveBar";
  shape: "arrowDown";
  color: string;
  text: string;
}> {
  return actions.map((a) => ({
    time: a.effectiveDate as `${number}-${number}-${number}`,
    position: "aboveBar" as const,
    shape: "arrowDown" as const,
    color: CA_MARKER_COLOR,
    // action_type 한국어 매핑 — 알 수 없으면 원문 그대로.
    text: ACTION_TYPE_LABEL[a.actionType] ?? a.actionType,
  }));
}

/**
 * PriceChart — lightweight-charts 캔들스틱/라인 차트.
 *
 * lifecycle 보장:
 *   1. containerRef 가 확보되면 createChart 호출.
 *   2. bars 또는 mode 변경 시 series 재생성 (chart instance 는 유지).
 *   3. 언마운트 시 chart.remove() → canvas/이벤트 리스너 정리.
 *   4. ResizeObserver 로 컨테이너 폭 변경 감지 → chart.applyOptions({width}).
 *   5. deps 배열 정확히 관리 → 무한 재생성 루프 없음.
 */
export function PriceChart({
  code,
  asOf,
  className,
}: PriceChartProps): JSX.Element {
  const t = useTranslations("stock");
  const [mode, setMode] = useState<PriceMode>("raw");

  const { data, isLoading, isError, error } = useQuery<
    StockPricesResponse,
    Error
  >({
    queryKey: ["prices", code, asOf] as const,
    queryFn: ({ signal }) => fetchStockPrices(code, asOf, signal),
    staleTime: 5 * 60 * 1000, // 5분
    enabled: code.length > 0,
  });

  // 차트 컨테이너 div ref.
  const containerRef = useRef<HTMLDivElement>(null);
  // chart instance ref — cleanup 과 series 갱신에 사용.
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null);

  // chart 생성 + cleanup.
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
      rightPriceScale: {
        borderColor: "#e5e5e5",
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

  // bars 또는 mode 변경 시 series 재설정.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const bars = data?.bars;
    if (!bars || bars.length === 0) return;

    // 기존 series 모두 제거 후 재생성 — v5 removeSeries API 사용.
    // chart.getSeries() 는 v5 에서 제공되지 않으므로 직접 추적.
    // 대신 chart 를 재사용하고 series 만 교체하기 위해 ref 추적.

    if (mode === "raw") {
      // 한국 관행 — 양봉(상승)=빨강 / 음봉(하락)=파랑 (ADR-0007 D8.3, T59).
      const series = chart.addSeries(CandlestickSeries, {
        upColor: CANDLE_UP_COLOR,
        downColor: CANDLE_DOWN_COLOR,
        borderUpColor: CANDLE_UP_COLOR,
        borderDownColor: CANDLE_DOWN_COLOR,
        wickUpColor: CANDLE_UP_COLOR,
        wickDownColor: CANDLE_DOWN_COLOR,
      });
      series.setData(
        bars.map((b) => ({
          time: b.date as `${number}-${number}-${number}`,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        })),
      );

      // Corporate action ▾ 마커 — raw 모드만 표시 (ADR-0001 D6).
      // 일자(사실)와 action_type 한국어 명칭만. 해석/판단 annotation 금지.
      // v5: createSeriesMarkers(series, markers) — ISeriesApi.setMarkers 제거됨.
      const caMarkers = buildCaMarkers(data.actions ?? []);
      if (caMarkers.length > 0) {
        createSeriesMarkers(series, caMarkers);
      }

      chart.timeScale().fitContent();

      return () => {
        chart.removeSeries(series);
      };
    } else {
      const series = chart.addSeries(LineSeries, {
        color: "#2563eb",
        lineWidth: 2,
      });
      series.setData(
        bars.map((b) => ({
          time: b.date as `${number}-${number}-${number}`,
          value: b.closeAdjusted,
        })),
      );
      chart.timeScale().fitContent();

      return () => {
        chart.removeSeries(series);
      };
    }
  }, [data, mode]);

  const bars = data?.bars ?? [];
  // 빈 = 로딩·에러 아니면서 bar 0 개.
  const isEmpty = !isLoading && !isError && bars.length === 0;
  // 토글은 실제 데이터가 있을 때만.
  const hasData = !isLoading && !isError && bars.length > 0;

  return (
    <div className={cn("rounded-lg border border-neutral-200 bg-white p-4", className)}>
      {/* 헤더: 제목 + raw/adjusted 토글 */}
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-medium text-neutral-700">{t("chart.title")}</span>
        {hasData && (
          <div className="flex overflow-hidden rounded-md border border-neutral-200 text-xs">
            <button
              type="button"
              onClick={() => setMode("raw")}
              className={cn(
                "px-3 py-1 transition-colors",
                mode === "raw"
                  ? "bg-neutral-900 text-white"
                  : "bg-white text-neutral-600 hover:bg-neutral-50",
              )}
              aria-pressed={mode === "raw"}
            >
              {t("chart.rawMode")}
            </button>
            <button
              type="button"
              onClick={() => setMode("adjusted")}
              className={cn(
                "border-l border-neutral-200 px-3 py-1 transition-colors",
                mode === "adjusted"
                  ? "bg-neutral-900 text-white"
                  : "bg-white text-neutral-600 hover:bg-neutral-50",
              )}
              aria-pressed={mode === "adjusted"}
            >
              {t("chart.adjustedMode")}
            </button>
          </div>
        )}
      </div>

      {/* 차트 컨테이너는 **항상 mount** — createChart effect([]) 가 마운트 시 ref 를
          확보해야 비동기 데이터 도착 후 series effect 가 채울 수 있음. 컨테이너를
          조건부 렌더하면(로딩 중 unmount) ref 가 null 이라 차트가 영영 안 그려짐.
          로딩/에러/빈 상태는 overlay 로 표시. */}
      <div className="relative" style={{ height: CHART_HEIGHT }}>
        <div ref={containerRef} style={{ height: CHART_HEIGHT }} />
        {isLoading && (
          <div
            className="absolute inset-0 animate-pulse rounded bg-neutral-100"
            aria-label={t("chart.loadingAriaLabel")}
          />
        )}
        {isError && (
          <div className="absolute inset-0 flex items-center justify-center rounded-md border border-red-200 bg-red-50 px-4 text-center text-sm text-red-800">
            {t("chart.loadError", { message: error.message })}
          </div>
        )}
        {isEmpty && (
          <div className="absolute inset-0 flex items-center justify-center rounded bg-neutral-50 text-sm text-neutral-500">
            {t("chart.empty")}
          </div>
        )}
      </div>
    </div>
  );
}
