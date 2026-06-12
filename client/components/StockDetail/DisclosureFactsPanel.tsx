"use client";

/**
 * DisclosureFactsPanel — AI 공시 사실추출 결과 패널 (ADR-0031).
 *
 * 표시 범위 하드 제약 (ADR-0031 D1/D5):
 *   - 구조화 사실 슬롯만: 공시유형·금액·일자·당사자·수량.
 *   - 자유 서술·해석·압축 문장 0.
 *   - 전망·매매시사·투자판단·평가·추천 필드 0.
 *
 * AI 디스클레이머 게이트 (ADR-0031 D3 — 절대 준수):
 *   - disclaimerRequired=true 면 "AI 가 추출한 사실이며 원문 확인이 필수입니다.
 *     hallucination 가능성이 있습니다" 를 결과와 항상 함께 렌더.
 *   - 디스클레이머 없이 결과 단독 표시 차단(게이트).
 *
 * 출처 강제 (ADR-0031 D3):
 *   - source.dartUrl DART 원문 링크(target=_blank rel=noopener noreferrer) 항상 표시.
 *   - "원문 확인" 유도 텍스트 포함.
 *
 * 중립 톤 원칙 (ADR-0007 D2 / §2.2 No Advice):
 *   - 모든 색 상수를 FACTS_PANEL_* 로 export — fact-extraction-gate.test.tsx 검증 대상.
 *   - grayscale/neutral 계열만. 등락색(red/blue/green) · 판단색 토큰 0.
 *   - 강조·색 기반 판단 유도 0.
 *
 * 503 처리 (ADR-0031 D6):
 *   - LLM 운영 미연동 시 중립 안내("AI 사실추출 기능은 현재 비활성화되어 있습니다").
 *   - 에러 취급 아님 — 정상 비활성화 상태.
 *
 * 422 처리 (ADR-0031 D2):
 *   - 출력 게이트 fail-closed 시 중립 안내("추출 결과가 표시 기준을 통과하지 못했습니다").
 *   - 사실 미표시.
 *
 * 관련:
 *   - ADR-0031 — AI 공시 사실추출 설계.
 *   - lib/api/fact-extraction.ts — API client.
 *   - components/__tests__/fact-extraction-gate.test.tsx — visual gate.
 *   - components/StockDetail/DisclosurePanel.tsx — 진입점 공시 패널.
 */

import { useMutation } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { extractDisclosureFacts } from "@/lib/api/fact-extraction";
import type { ExtractFactsResult } from "@/lib/api/fact-extraction";
import { cn } from "@/lib/utils";

// =============================================================================
// 중립 톤 상수 — visual gate 검증 대상 (ADR-0031 D3 / ADR-0007 D2)
//
// 규칙: grayscale/neutral/white 만. 등락색·판단색 토큰 금지.
// fact-extraction-gate.test.tsx 가 이 상수 전체를 neutral 검증한다.
// =============================================================================

/** 패널 전체 배경 + 테두리. */
export const FACTS_PANEL_BG = "rounded-lg border border-neutral-200 bg-white";

/** 버튼 — 중립 텍스트 버튼. 판단 의미색 금지. */
export const FACTS_PANEL_TRIGGER_BTN = "rounded-md border border-neutral-300 px-2 py-1 text-xs font-medium text-neutral-600 hover:bg-neutral-50";

/**
 * AI 디스클레이머 박스 톤 — neutral 계열.
 * ADR-0031 D3: 디스클레이머 없이 결과 표시 차단. 판단색 금지.
 */
export const FACTS_PANEL_DISCLAIMER = "bg-neutral-50 border-neutral-300 text-neutral-700";

/** 사실 슬롯 레이블 톤 — grayscale neutral. */
export const FACTS_PANEL_SLOT_LABEL = "text-neutral-600";

/** 사실 슬롯 값 톤 — grayscale neutral. 수치 강조·판단색 0. */
export const FACTS_PANEL_SLOT_VALUE = "text-neutral-800";

/** 출처 링크 톤 — neutral. hover underline 만, 색 변화 없음. */
export const FACTS_PANEL_SOURCE_LINK = "text-neutral-700 underline-offset-2 hover:underline";

/** 중립 안내 텍스트(503/422/로딩 등) — grayscale. */
export const FACTS_PANEL_MUTED = "text-neutral-500";

/** 슬롯 행 배경(짝수) — 중립. */
export const FACTS_PANEL_ROW_STRIPE = "bg-neutral-50";

// =============================================================================
// 유틸 — 빈 배열 처리
// =============================================================================

/**
 * 문자열 배열을 쉼표 구분으로 병합.
 * 빈 배열 → "—" (결손 표시 — 중립).
 */
function joinOrDash(arr: ReadonlyArray<string>): string {
  if (arr.length === 0) return "—";
  return arr.join(", ");
}

// =============================================================================
// 내부 컴포넌트 — 사실 결과 렌더 + 게이트
// =============================================================================

interface FactsResultSectionProps {
  readonly result: ExtractFactsResult;
}

/**
 * FactsResultSection — 사실 결과 + 디스클레이머 게이트 + 출처 (ADR-0031 D3).
 *
 * disclaimerRequired=true 이면 디스클레이머 없이 사실 단독 렌더 금지.
 * 디스클레이머와 출처는 항상 사실과 함께 표시.
 */
function FactsResultSection({ result }: FactsResultSectionProps): JSX.Element {
  const t = useTranslations("stock");
  const { facts, source, disclaimerRequired } = result;

  // 구조화 사실 슬롯 목록 — 자유 서술 없음 (ADR-0031 D1/D5).
  const slots = [
    { label: t("factExtraction.slotDisclosureType"), value: facts.disclosureType || "—" },
    { label: t("factExtraction.slotAmounts"), value: joinOrDash(facts.amounts) },
    { label: t("factExtraction.slotDates"), value: joinOrDash(facts.dates) },
    { label: t("factExtraction.slotParties"), value: joinOrDash(facts.parties) },
    { label: t("factExtraction.slotQuantities"), value: joinOrDash(facts.quantities) },
  ];

  return (
    <div
      data-testid="facts-result-section"
      className="mt-3 space-y-3"
    >
      {/*
       * AI 디스클레이머 게이트 (ADR-0031 D3 — 절대 준수).
       * disclaimerRequired=true 이면 항상 결과와 함께 표시.
       * 디스클레이머 없이 사실 단독 렌더 차단.
       */}
      {disclaimerRequired ? (
        <div
          data-testid="facts-disclaimer"
          className={cn(
            "rounded-md border px-3 py-2 text-xs leading-relaxed",
            FACTS_PANEL_DISCLAIMER,
          )}
        >
          {t("factExtraction.disclaimer")}
        </div>
      ) : null}

      {/*
       * 구조화 사실 슬롯 표 — ADR-0031 D1/D5.
       * 공시유형·금액·일자·당사자·수량 5슬롯만.
       * 자유 서술·전망·추천·평가 행 0.
       */}
      <div
        data-testid="facts-slots-table"
        className={cn("rounded-md border border-neutral-200 overflow-hidden")}
      >
        <table className="w-full border-collapse text-xs">
          <tbody>
            {slots.map((slot, i) => (
              <tr
                key={slot.label}
                className={cn(
                  "border-b border-neutral-100 last:border-0",
                  i % 2 !== 0 && FACTS_PANEL_ROW_STRIPE,
                )}
              >
                <td
                  className={cn(
                    "whitespace-nowrap px-3 py-2 font-medium align-top w-24",
                    FACTS_PANEL_SLOT_LABEL,
                  )}
                >
                  {slot.label}
                </td>
                <td
                  className={cn("px-3 py-2 align-top font-mono", FACTS_PANEL_SLOT_VALUE)}
                  data-testid={`facts-slot-${slot.label}`}
                >
                  {slot.value}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/*
       * 출처 강제 — ADR-0031 D3.
       * DART 원문 링크 항상 표시. target=_blank rel=noopener noreferrer.
       * "원문 확인" 유도.
       */}
      <div
        data-testid="facts-source"
        className="flex items-center gap-2 text-xs"
      >
        <span className={cn(FACTS_PANEL_MUTED)}>
          {t("factExtraction.source")}
        </span>
        <a
          href={source.dartUrl}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(FACTS_PANEL_SOURCE_LINK)}
          data-testid="facts-dart-link"
        >
          {t("factExtraction.dartLinkLabel")}
        </a>
        <span className={cn("font-mono", FACTS_PANEL_MUTED)}>
          ({source.rceptNo})
        </span>
      </div>
    </div>
  );
}

// =============================================================================
// DisclosureFactsPanel
// =============================================================================

interface DisclosureFactsPanelProps {
  /** KRX 6자리 종목코드 — ADR-0031 D4: 1종목 on-demand. */
  readonly code: string;
  /** DART 공시 접수번호. */
  readonly rceptNo: string;
  /** 공시 제목 — LLM context 보조. */
  readonly disclosureTitle: string;
  readonly className?: string;
}

/**
 * DisclosureFactsPanel — 공시 항목별 AI 사실추출 결과 패널.
 *
 * ADR-0031 D4: 사용자가 "AI 사실 추출" 버튼을 명시 클릭 시에만 실행.
 * ADR-0031 D1: 구조화 사실 슬롯만 렌더(공시유형·금액·일자·당사자·수량).
 * ADR-0031 D3: disclaimerRequired=true 이면 디스클레이머와 항상 함께 표시(게이트).
 * ADR-0031 D3: 출처(DART 원문 링크) 항상 표시.
 * ADR-0031 D5: 전망·추천·평가 필드 없음.
 * ADR-0031 D6: 503 → 비활성화 중립 안내. 422 → 게이트 실패 중립 안내.
 */
export function DisclosureFactsPanel({
  code,
  rceptNo,
  disclosureTitle,
  className,
}: DisclosureFactsPanelProps): JSX.Element {
  const t = useTranslations("stock");

  // 실행 결과 — 실행 시점 freeze(자동 재실행 금지).
  const [executedResult, setExecutedResult] = useState<ExtractFactsResult | null>(null);

  // 503/422 구분 처리를 위한 상태.
  const [errorStatus, setErrorStatus] = useState<503 | 422 | "other" | null>(null);

  // useMutation — react-query. BacktestPanel 패턴과 동일.
  const mutation = useMutation<ExtractFactsResult, Error, void>({
    mutationFn: () =>
      extractDisclosureFacts(code, { rceptNo, disclosureTitle }),
    onSuccess: (result) => {
      setExecutedResult(result);
      setErrorStatus(null);
    },
    onError: (err) => {
      setExecutedResult(null);
      if (err instanceof ApiError) {
        if (err.status === 503) {
          setErrorStatus(503);
        } else if (err.status === 422) {
          setErrorStatus(422);
        } else {
          setErrorStatus("other");
        }
      } else {
        setErrorStatus("other");
      }
    },
  });

  const handleExtract = (): void => {
    setExecutedResult(null);
    setErrorStatus(null);
    mutation.mutate();
  };

  return (
    <div
      data-testid="facts-panel"
      className={cn(FACTS_PANEL_BG, "p-3", className)}
    >
      {/*
       * "AI 사실 추출" 버튼 — 사용자 명시 클릭(ADR-0031 D4 on-demand).
       * 버튼은 중립 톤(판단 의미색 0).
       * 로딩 중에는 비활성화.
       */}
      <button
        type="button"
        onClick={handleExtract}
        disabled={mutation.isPending}
        data-testid="facts-extract-btn"
        className={cn(FACTS_PANEL_TRIGGER_BTN, "disabled:cursor-not-allowed disabled:opacity-50")}
      >
        {mutation.isPending
          ? t("factExtraction.extracting")
          : t("factExtraction.extractButton")}
      </button>

      {/* ── 로딩 상태 ──────────────────────────────────────────────────────── */}
      {mutation.isPending ? (
        <div className="mt-3 space-y-2">
          <div className="h-4 w-48 animate-pulse rounded bg-neutral-100" />
          <div className="h-4 w-32 animate-pulse rounded bg-neutral-100" />
        </div>
      ) : null}

      {/* ── 503: LLM 운영 미연동 중립 안내 (ADR-0031 D6) ─────────────────── */}
      {/* 에러 취급 아님 — 정상 비활성화 상태. 중립 안내만. */}
      {errorStatus === 503 ? (
        <p
          data-testid="facts-503-notice"
          className={cn("mt-3 text-xs", FACTS_PANEL_MUTED)}
        >
          {t("factExtraction.serviceUnavailable")}
        </p>
      ) : null}

      {/* ── 422: 출력 게이트 fail-closed 중립 안내 (ADR-0031 D2) ─────────── */}
      {/* 사실 미표시 — 금지어 추출 차단. 중립 안내만. */}
      {errorStatus === 422 ? (
        <p
          data-testid="facts-422-notice"
          className={cn("mt-3 text-xs", FACTS_PANEL_MUTED)}
        >
          {t("factExtraction.gateRejected")}
        </p>
      ) : null}

      {/* ── 기타 오류 ──────────────────────────────────────────────────────── */}
      {errorStatus === "other" ? (
        <p
          data-testid="facts-error-notice"
          className={cn("mt-3 text-xs", FACTS_PANEL_MUTED)}
        >
          {t("factExtraction.extractError", { message: mutation.error?.message ?? "" })}
        </p>
      ) : null}

      {/*
       * 결과 표시 — disclaimerRequired=true 이면 항상 디스클레이머와 함께(게이트).
       * FactsResultSection 내부에서 디스클레이머 + 슬롯 표 + 출처 함께 렌더.
       * 결과는 실행 freeze — 자동 재정렬/재실행 없음(ADR-0031 D4).
       */}
      {executedResult !== null ? (
        <FactsResultSection result={executedResult} />
      ) : null}
    </div>
  );
}
