"use client";

/**
 * TaxCalculatorPanel — 증권거래세 계산기 UI (ADR-0030 D1–D4).
 *
 * 흐름:
 *   1. 시장구분(코스피/코스닥/코넥스/비상장) + 거래금액 + 거래일 입력.
 *   2. POST /api/tax/securities-transaction → TaxResult.
 *   3. 결과 표시: 세액·적용세율·효력일·법령출처 + 필수 세무자문 디스클레이머.
 *
 * No Advice 가드레일 (ADR-0030 — 절대 준수):
 *   - 강제 디스클레이머 게이트(D3): disclaimerRequired=true 시 디스클레이머 없이
 *     결과 렌더 차단. 결과와 항상 함께 표시.
 *   - 개별 상황 입력 0(D2): 대주주 여부·보유기간·손익통산 입력 절대 없음.
 *     시장·금액·일자만.
 *   - 법령출처(legalSource)·효력일(effectiveDate) 명시(D4 §2.1 출처 표시).
 *   - 톤 grayscale/neutral(세액에 등락색·강조 금지).
 *   - "절세"/"세금 줄이기"/"유리한" 등 조언 어휘 0.
 *   - 양도세 UI 없음(D1 deferred).
 *
 * 색은 명명 상수(TAX_PANEL_*) 로 export — tax-calculator-gate.test.tsx 가
 * 실제 렌더 색과 1:1 대조해 판단색/조언 어휘 회귀를 자동 차단한다.
 *
 * 관련:
 *   - lib/api/tax.ts — calculateSecuritiesTransactionTax / TaxResult.
 *   - components/__tests__/tax-calculator-gate.test.tsx — 시각/가드레일 게이트.
 */

import { useMutation } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { calculateSecuritiesTransactionTax } from "@/lib/api/tax";
import type { CalculateTaxParams, TaxMarket, TaxResult } from "@/lib/api/tax";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

// ── No Advice 시각 상수 (tax-calculator-gate 패턴 — 이 상수를 테스트가 검증) ──

/**
 * 결과 행 텍스트 톤 — grayscale neutral. 세액 강조·판단색 0.
 * ADR-0030 D3: 세액에 등락색/강조 금지.
 * tax-calculator-gate.test.tsx 가 이 상수를 검증한다.
 */
export const TAX_PANEL_RESULT_TEXT = "text-neutral-800";

/**
 * 결과 레이블 톤 — grayscale neutral.
 */
export const TAX_PANEL_RESULT_LABEL = "text-neutral-600";

/**
 * 결과 행 배경(기본) — 판단색 아님.
 */
export const TAX_PANEL_RESULT_ROW_BG = "bg-white";

/**
 * 결과 행 배경(줄무늬) — grayscale 가독성용.
 */
export const TAX_PANEL_RESULT_ROW_STRIPE = "bg-neutral-50";

/**
 * 세무자문 디스클레이머 박스 톤 — neutral 계열(ADR-0030 D3).
 * 경고이지만 색으로 판단 유도 금지.
 */
export const TAX_PANEL_DISCLAIMER = "bg-neutral-50 border-neutral-200 text-neutral-700";

/**
 * 법령출처 박스 톤 — neutral 계열(ADR-0030 D4).
 */
export const TAX_PANEL_LEGAL_SOURCE = "bg-neutral-50 border-neutral-200 text-neutral-600";

// ── 유틸 ──────────────────────────────────────────────────────────────────────

/**
 * 거래금액 문자열을 쉼표 표시 형식으로.
 * "1000000" → "1,000,000"
 */
function formatAmount(value: string): string {
  const n = parseFloat(value);
  if (isNaN(n)) return value;
  return n.toLocaleString("ko-KR");
}

/**
 * 세율 문자열 → 퍼센트 형식.
 * "0.0015" → "0.15%"
 */
function formatRate(value: string): string {
  const n = parseFloat(value);
  if (isNaN(n)) return value;
  return `${(n * 100).toFixed(4).replace(/\.?0+$/, "")}%`;
}

// ── TaxResultPanel ─────────────────────────────────────────────────────────────

interface TaxResultPanelProps {
  readonly result: TaxResult;
}

/**
 * TaxResultPanel — 계산 결과 표시 + 게이트 (ADR-0030 D3/D4).
 *
 * disclaimer_required=true 면 세무자문 디스클레이머가 항상 결과와 함께 표시.
 * 디스클레이머 없이 결과만 렌더되는 경로는 없다.
 */
function TaxResultPanel({ result }: TaxResultPanelProps): JSX.Element {
  const t = useTranslations("tax");

  const { taxAmount, appliedRate, effectiveDate, legalSource, disclaimerRequired } = result;

  return (
    <div
      data-testid="tax-result-panel"
      className="space-y-4"
    >
      {/* ── ADR-0030 D3 세무자문 디스클레이머 게이트 ─────────────────────────── */}
      {/* disclaimer_required=true 면 항상 결과와 함께 표시. 디스클레이머 없이 결과 미표시. */}
      {disclaimerRequired ? (
        <div
          data-testid="tax-disclaimer"
          className={cn(
            "rounded-md border px-3 py-2 text-xs",
            TAX_PANEL_DISCLAIMER,
          )}
        >
          {t("disclaimer.required")}
        </div>
      ) : null}

      {/* ── 계산 결과 — 사실값만, 동일 비중, 평가/강조 0 ──────────────────── */}
      {/* 세액·세율·효력일 사실만 표시. 세액에 등락색·강조 금지(ADR-0030 D3). */}
      <div data-testid="tax-result-table">
        <p className="text-xs font-medium text-neutral-700 mb-2">
          {t("result.heading")}
        </p>
        <table className="w-full text-xs border-collapse">
          <tbody>
            {[
              {
                label: t("result.taxAmount"),
                value: `${formatAmount(taxAmount)}원`,
                testId: "tax-result-amount",
              },
              {
                label: t("result.appliedRate"),
                value: formatRate(appliedRate),
                testId: "tax-result-rate",
              },
              {
                label: t("result.effectiveDate"),
                value: effectiveDate,
                testId: "tax-result-effective-date",
              },
            ].map((row, i) => (
              <tr
                key={row.testId}
                className={cn(
                  "border-b border-neutral-100 last:border-0",
                  i % 2 === 0 ? TAX_PANEL_RESULT_ROW_BG : TAX_PANEL_RESULT_ROW_STRIPE,
                )}
              >
                <td className={cn("py-1.5 px-2", TAX_PANEL_RESULT_LABEL)}>
                  {row.label}
                </td>
                {/* 결과 값 — 중립 톤, 강조 없음. 세액 강조/등락색 금지(ADR-0030 D3). */}
                <td
                  className={cn("py-1.5 px-2 font-mono text-right", TAX_PANEL_RESULT_TEXT)}
                  data-testid={row.testId}
                >
                  {row.value}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── ADR-0030 D4 법령출처 명시 ──────────────────────────────────────── */}
      {/* 법령출처·효력일 명시 — §2.1 출처 표시 의무. */}
      <div
        data-testid="tax-legal-source"
        className={cn(
          "rounded-md border px-3 py-2",
          TAX_PANEL_LEGAL_SOURCE,
        )}
      >
        <p className="text-[11px] font-medium text-neutral-600 mb-0.5">
          {t("result.legalSourceLabel")}
        </p>
        <p className="text-[11px] font-mono text-neutral-700">
          {legalSource}
        </p>
        <p className="mt-1 text-[11px] text-neutral-500">
          {t("result.effectiveDateLabel")}: {effectiveDate}
        </p>
      </div>
    </div>
  );
}

// ── TaxCalculatorPanel (메인) ─────────────────────────────────────────────────

/**
 * TaxCalculatorPanel — 증권거래세 계산기 입력 폼 + 결과 표시.
 *
 * 입력: 시장구분·거래금액·거래일만 (ADR-0030 D2 개별 상황 입력 0).
 * 결과: 세액·세율·효력일·법령출처 + 세무자문 디스클레이머(ADR-0030 D3).
 * 양도세 UI 없음(ADR-0030 D1 deferred).
 */
export function TaxCalculatorPanel(): JSX.Element {
  const t = useTranslations("tax");

  // ── 폼 상태 ────────────────────────────────────────────────────────────────

  // 시장구분 — 코스피 기본.
  const [market, setMarket] = useState<TaxMarket>("kospi");
  const [tradeAmount, setTradeAmount] = useState("");
  const [tradeDate, setTradeDate] = useState("");

  /** 실행 결과 freeze — 실행 시점 결과 고정. */
  const [executedResult, setExecutedResult] = useState<TaxResult | null>(null);

  // ── mutation ────────────────────────────────────────────────────────────────

  const mutation = useMutation<TaxResult, Error, CalculateTaxParams>({
    mutationFn: (params) => calculateSecuritiesTransactionTax(params),
    onSuccess: (result) => {
      setExecutedResult(result);
    },
  });

  // ── 422 에러 메시지 추출 ────────────────────────────────────────────────────

  const errorMessage = (() => {
    if (!mutation.isError) return null;
    const err = mutation.error;
    if (err instanceof ApiError && err.status === 422) {
      return t("form.error422");
    }
    return t("form.errorGeneric", { message: err.message });
  })();

  // ── 실행 가능 여부 ──────────────────────────────────────────────────────────

  const canCalculate =
    market.length > 0
    && tradeAmount.length > 0
    && tradeDate.length > 0
    && !mutation.isPending;

  // ── 실행 핸들러 ────────────────────────────────────────────────────────────

  const handleCalculate = (): void => {
    mutation.mutate({ market, tradeAmount, tradeDate });
  };

  // ── 렌더 ───────────────────────────────────────────────────────────────────

  return (
    <div
      data-testid="tax-calculator-panel"
      className="space-y-5"
    >
      {/* 헤더 */}
      <div>
        <p className="text-sm font-semibold text-neutral-900">{t("title")}</p>
        <p className="mt-0.5 text-xs text-neutral-500">{t("description")}</p>
      </div>

      {/* ── 입력 폼 — ADR-0030 D2: 시장·금액·일자만, 개별 상황 입력 0 ──────── */}
      <div className="space-y-4 rounded-lg border border-neutral-200 bg-neutral-50 p-4">

        {/* 시장구분 */}
        <label className="block text-xs font-medium text-neutral-600">
          {t("form.marketLabel")}
          <select
            value={market}
            onChange={(e) => setMarket(e.target.value as TaxMarket)}
            data-testid="tax-market-select"
            className="mt-1 w-full rounded border border-neutral-300 bg-white px-2 py-1 text-xs"
          >
            <option value="kospi">{t("form.marketKospi")}</option>
            <option value="kosdaq">{t("form.marketKosdaq")}</option>
            <option value="konex">{t("form.marketKonex")}</option>
            <option value="unlisted">{t("form.marketUnlisted")}</option>
          </select>
        </label>

        {/* 거래금액 */}
        <label className="block text-xs font-medium text-neutral-600">
          {t("form.tradeAmountLabel")}
          <input
            type="text"
            inputMode="numeric"
            value={tradeAmount}
            onChange={(e) => setTradeAmount(e.target.value)}
            placeholder={t("form.tradeAmountPlaceholder")}
            data-testid="tax-trade-amount-input"
            className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
          />
        </label>

        {/* 거래일 */}
        <label className="block text-xs font-medium text-neutral-600">
          {t("form.tradeDateLabel")}
          <input
            type="date"
            value={tradeDate}
            onChange={(e) => setTradeDate(e.target.value)}
            data-testid="tax-trade-date-input"
            className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-xs"
          />
        </label>

        {/* 계산 버튼 */}
        <button
          type="button"
          onClick={handleCalculate}
          disabled={!canCalculate}
          data-testid="tax-calculate-btn"
          className="inline-flex items-center rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-neutral-300 hover:bg-neutral-800"
        >
          {mutation.isPending ? t("form.calculating") : t("form.calculateButton")}
        </button>
      </div>

      {/* 에러 — 422(2023 이전 등) 중립 에러 표시(ADR-0030 D3). 판단색 금지. */}
      {mutation.isError ? (
        <p
          data-testid="tax-calculate-error"
          className="text-xs text-neutral-700"
        >
          {errorMessage}
        </p>
      ) : null}

      {/* ── 결과 — disclaimerRequired=true 시 항상 디스클레이머와 함께(D3 게이트) */}
      {executedResult !== null ? (
        <TaxResultPanel result={executedResult} />
      ) : null}
    </div>
  );
}
