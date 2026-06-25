"use client";

/**
 * PriceChart — 종목 가격 캔들스틱 차트 (lightweight-charts v5).
 *
 * 기능:
 *   - useQuery 로 fetchStockPrices 호출, staleTime=5분.
 *   - 원본 종가(raw) / 수정 종가(adjusted) / 배당재투자(total-return) 토글.
 *     · raw: 정규 OHLC 캔들스틱.
 *     · adjusted: close_adjusted 기반 라인 시리즈 (open/high/low/close 대체 불가,
 *       수정 종가 라인으로 표시 — ADR-0001 D6 토글 의도).
 *     · total-return: 세전 total return index(배당 재투자 포함)를 첫 거래일 보정
 *       종가 기준 rebase 한 라인 (M7 #6, ADR-0035). lazy fetch — 이 모드 선택 시에만
 *       fetchStockTotalReturn 호출. 세전 disclosure 를 차트 하단에 인라인 고지.
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
import type { StockTotalReturnResponse } from "@/lib/api/totalReturn";
import { fetchStockTotalReturn } from "@/lib/api/totalReturn";
import { cn } from "@/lib/utils";

import {
  PRE_TAX_DISCLOSURE_CONTAINER,
  PRE_TAX_DISCLOSURE_TEXT,
} from "./PreTaxDisclosure";

/** 표시 모드: 원본 종가 캔들스틱 / 수정 종가 라인 / 배당재투자 total-return 라인. */
type PriceMode = "raw" | "adjusted" | "total-return";

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
 * 수정 종가 라인 색 — 파랑(중립). 하락 캔들색과 동일하나 모드 배타적이라 혼동 없음.
 */
export const ADJUSTED_LINE_COLOR = "#2563eb"; // 파랑 (neutral line)

/**
 * Total Return 라인 색 — 중립 보라(violet-600). raw/adjusted 와 구분되는 별도
 * hue 이되 **판단색이 아님** — 등락 의미 빨강/파랑·성과 의미 녹색(서구 "상승=좋음")을
 * 피한다 (No Advice §2.2). total-return 모드는 배타적이라 다른 라인과 동시 표시되지
 * 않으나 시각 구분을 위해 별도 색 부여.
 *
 * 회귀 방지: `chart-visual-gate.test.tsx` 가 본 상수가 서구 녹색 상승색이 아님을 검증.
 */
export const TOTAL_RETURN_LINE_COLOR = "#7c3aed"; // violet-600 (중립 구분색)

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

  // Total Return — total-return 모드 선택 시에만 lazy fetch (배당 재투자 계산은
  // 서버에서 PriceAdjuster + TotalReturnAdjuster 를 돌리므로 불필요 호출 회피).
  const {
    data: trData,
    isLoading: trLoading,
    isError: trIsError,
    error: trError,
  } = useQuery<StockTotalReturnResponse, Error>({
    queryKey: ["total-return", code, asOf] as const,
    queryFn: ({ signal }) => fetchStockTotalReturn(code, asOf, signal),
    staleTime: 5 * 60 * 1000, // 5분
    enabled: code.length > 0 && mode === "total-return",
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

  // bars / trData / mode 변경 시 series 재설정.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    // total-return 모드 — rebase 한 TRI 라인 (배당 재투자 포함). 별도 시계열이라
    // bars(가격)와 독립. points 미도착/빈 경우 그리지 않음 (overlay 가 상태 표시).
    if (mode === "total-return") {
      const points = trData?.points;
      if (!points || points.length === 0) return;
      const series = chart.addSeries(LineSeries, {
        color: TOTAL_RETURN_LINE_COLOR,
        lineWidth: 2,
      });
      series.setData(
        points.map((p) => ({
          time: p.date as `${number}-${number}-${number}`,
          value: p.value,
        })),
      );
      chart.timeScale().fitContent();
      return () => {
        // 언마운트(페이지 이탈) 시 Effect 1 의 cleanup 이 `chart.remove()` 로 차트를
        // 먼저 폐기하면(chartRef.current=null), 폐기된 차트에 removeSeries 호출 →
        // lightweight-charts 내부 "Value is undefined" 크래시. 차트가 살아있을 때만
        // (= series 교체용 re-render) 제거하고, 언마운트면 chart.remove() 가 series
        // 까지 처리하므로 skip. (re-render: chartRef.current 유효 → 옛 series 제거.)
        if (chartRef.current) {
          chart.removeSeries(series);
        }
      };
    }

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
        // 언마운트(페이지 이탈) 시 Effect 1 의 cleanup 이 `chart.remove()` 로 차트를
        // 먼저 폐기하면(chartRef.current=null), 폐기된 차트에 removeSeries 호출 →
        // lightweight-charts 내부 "Value is undefined" 크래시. 차트가 살아있을 때만
        // (= series 교체용 re-render) 제거하고, 언마운트면 chart.remove() 가 series
        // 까지 처리하므로 skip. (re-render: chartRef.current 유효 → 옛 series 제거.)
        if (chartRef.current) {
          chart.removeSeries(series);
        }
      };
    } else {
      // adjusted 모드 — 수정 종가 라인.
      const series = chart.addSeries(LineSeries, {
        color: ADJUSTED_LINE_COLOR,
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
        // 언마운트(페이지 이탈) 시 Effect 1 의 cleanup 이 `chart.remove()` 로 차트를
        // 먼저 폐기하면(chartRef.current=null), 폐기된 차트에 removeSeries 호출 →
        // lightweight-charts 내부 "Value is undefined" 크래시. 차트가 살아있을 때만
        // (= series 교체용 re-render) 제거하고, 언마운트면 chart.remove() 가 series
        // 까지 처리하므로 skip. (re-render: chartRef.current 유효 → 옛 series 제거.)
        if (chartRef.current) {
          chart.removeSeries(series);
        }
      };
    }
  }, [data, trData, mode]);

  const bars = data?.bars ?? [];
  const inTotalReturn = mode === "total-return";
  // 토글은 가격 데이터가 있을 때만 노출 (total-return 진입점도 가격 차트 존재 전제).
  const hasData = !isLoading && !isError && bars.length > 0;
  // 모드별 effective 로딩/에러/빈 상태 — total-return 모드면 TR 쿼리 상태가 우선.
  const showLoading = isLoading || (inTotalReturn && trLoading);
  const showError = isError || (inTotalReturn && trIsError);
  // 에러 메시지 — 가격 에러 우선, 아니면 total-return 에러.
  const errorMessage = isError
    ? (error?.message ?? "")
    : (trError?.message ?? "");
  const isEmpty =
    !showLoading &&
    !showError &&
    (inTotalReturn
      ? (trData?.points.length ?? 0) === 0
      : bars.length === 0);

  return (
    <div className={cn("rounded-lg border border-neutral-200 bg-white p-4", className)}>
      {/* 헤더: 제목 + raw/adjusted/total-return 토글 */}
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
            <button
              type="button"
              onClick={() => setMode("total-return")}
              className={cn(
                "border-l border-neutral-200 px-3 py-1 transition-colors",
                mode === "total-return"
                  ? "bg-neutral-900 text-white"
                  : "bg-white text-neutral-600 hover:bg-neutral-50",
              )}
              aria-pressed={mode === "total-return"}
            >
              {t("chart.totalReturnMode")}
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
        {showLoading && (
          <div
            className="absolute inset-0 animate-pulse rounded bg-neutral-100"
            aria-label={t("chart.loadingAriaLabel")}
          />
        )}
        {showError && (
          <div className="absolute inset-0 flex items-center justify-center rounded-md border border-red-200 bg-red-50 px-4 text-center text-sm text-red-800">
            {t("chart.loadError", { message: errorMessage })}
          </div>
        )}
        {isEmpty && (
          <div className="absolute inset-0 flex items-center justify-center rounded bg-neutral-50 text-sm text-neutral-500">
            {t("chart.empty")}
          </div>
        )}
      </div>

      {/* 세전 disclosure — total-return 라인이 실제 표시될 때만 인라인 고지
          (ADR-0035 D7 / §2.1·§2.7). 로딩/에러/빈 상태에서는 표시할 total-return
          데이터가 없어 disclosure 가 UI 노이즈이므로 제외. 배당 재투자 수익이
          세전(배당소득세·양도세 미반영)임을 중립 사실로 고지. PreTaxDisclosure 의
          gate-검증 중립 스타일 상수·메시지 키 재사용. */}
      {inTotalReturn && !showLoading && !showError && !isEmpty && (
        <p className={PRE_TAX_DISCLOSURE_CONTAINER}>
          <span className={PRE_TAX_DISCLOSURE_TEXT}>
            {t("metricsPreTaxDisclosure")}
          </span>
        </p>
      )}
    </div>
  );
}
