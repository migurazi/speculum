"use client";

/**
 * DisclosureWithFactsPanel — 공시 목록 + 공시별 AI 사실 추출 진입점 복합 패널.
 *
 * DisclosurePanel(공시 목록) 의 각 공시 행에 "AI 사실 추출" 진입점을 추가한 통합 패널.
 * DisclosurePanel 은 그대로 유지(테스트 회귀 방지) — 이 컴포넌트가 공시 데이터를
 * 별도 fetch 해 진입점 UI 를 추가하고, DisclosureFactsPanel 을 per-row 인라인으로 렌더.
 *
 * 공시 데이터 fetch:
 *   - 동일 queryKey ["disclosures", code, asOf] 사용 → TanStack Query 캐시 공유.
 *   - DisclosurePanel 먼저 렌더 시 이 컴포넌트는 캐시 히트(네트워크 재요청 없음).
 *
 * rcept_no 추출:
 *   - dart_url 의 rcpNo 쿼리 파라미터에서 추출.
 *   - DART URL 패턴: `?rcpNo=20260515000001` — 추출 불가 시 공시 제목 기반 식별.
 *
 * ADR-0031 D4: "AI 사실 추출" 버튼은 사용자 명시 클릭 시에만 동작(on-demand).
 *
 * 관련:
 *   - DisclosurePanel.tsx — 공시 목록 독립 패널(게이트 테스트 보호).
 *   - DisclosureFactsPanel.tsx — AI 사실 추출 결과 패널 + 게이트.
 *   - lib/api/disclosures.ts — 공시 목록 API client.
 *   - app/stock/[code]/page.tsx — 사용 위치.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { fetchDisclosures } from "@/lib/api/disclosures";
import type { Disclosure } from "@/lib/api/disclosures";
import { DisclosureFactsPanel } from "@/components/StockDetail/DisclosureFactsPanel";
import {
  DISCLOSURE_PANEL_BG,
  DISCLOSURE_PANEL_DATE,
  DISCLOSURE_PANEL_HEADER,
  DISCLOSURE_PANEL_LINK,
  DISCLOSURE_PANEL_MUTED,
  DISCLOSURE_PANEL_ROW_STRIPE,
  DISCLOSURE_PANEL_TEXT,
} from "@/components/StockDetail/DisclosurePanel";
import { cn } from "@/lib/utils";

// =============================================================================
// 유틸
// =============================================================================

/**
 * DART URL 에서 rcpNo 파라미터 추출.
 * 예: "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515000001" → "20260515000001"
 * 추출 실패 시 빈 문자열 반환.
 */
function extractRceptNo(dartUrl: string): string {
  try {
    const url = new URL(dartUrl);
    return url.searchParams.get("rcpNo") ?? "";
  } catch {
    return "";
  }
}

// =============================================================================
// Types
// =============================================================================

interface DisclosureWithFactsPanelProps {
  /** KRX 6자리 종목코드. */
  readonly code: string;
  /**
   * PIT 기준 일자 ("YYYY-MM-DD").
   * 미전달 시 backend default(오늘). ADR-0026 D4.
   */
  readonly asOf?: string;
  readonly className?: string;
}

// =============================================================================
// DisclosureWithFactsPanel
// =============================================================================

/**
 * DisclosureWithFactsPanel — 공시 목록 + per-row AI 사실 추출 진입점.
 *
 * ADR-0026 D1: 공시 목록 표시 — 제목+접수일+DART 원문링크 3필드.
 * ADR-0031 D4: 각 공시 행에 "AI 사실 추출" 버튼(명시 클릭 on-demand).
 * ADR-0031 D1/D3/D5: 추출 결과는 DisclosureFactsPanel 에서 게이트 적용.
 */
export function DisclosureWithFactsPanel({
  code,
  asOf,
  className,
}: DisclosureWithFactsPanelProps): JSX.Element {
  const t = useTranslations("stock");

  // 확장된(AI 추출 열린) 공시 행 — key: "rceptDate-dartUrl" (Disclosure 식별자).
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const { data, isLoading, isError, error } = useQuery({
    // DisclosurePanel 과 동일 queryKey — 캐시 공유, 재요청 없음.
    queryKey: ["disclosures", code, asOf ?? null] as const,
    queryFn: ({ signal }) =>
      fetchDisclosures(code, asOf ? { asOf } : {}, signal),
    staleTime: 5 * 60 * 1000,
    enabled: code.length > 0,
  });

  const makeRowKey = (item: Disclosure): string =>
    `${item.rceptDate}-${item.dartUrl}`;

  const handleToggle = (item: Disclosure): void => {
    const key = makeRowKey(item);
    setExpandedKey((prev) => (prev === key ? null : key));
  };

  // ─── 로딩 ──────────────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className={cn(DISCLOSURE_PANEL_BG, "p-4", className)}>
        <div className="h-4 w-32 animate-pulse rounded bg-neutral-100" />
        <div className="mt-3 space-y-2">
          <div className="h-8 animate-pulse rounded bg-neutral-100" />
          <div className="h-8 animate-pulse rounded bg-neutral-100" />
          <div className="h-8 animate-pulse rounded bg-neutral-100" />
        </div>
      </div>
    );
  }

  // ─── 에러 ──────────────────────────────────────────────────────────────────
  if (isError) {
    return (
      <div className={cn(DISCLOSURE_PANEL_BG, "p-4", className)}>
        <div className="rounded-md border border-neutral-300 bg-neutral-50 px-4 py-3 text-sm text-neutral-800">
          {t("disclosures.loadError", { message: (error as Error).message })}
        </div>
      </div>
    );
  }

  // ─── 빈 상태 ───────────────────────────────────────────────────────────────
  if (!data || data.disclosures.length === 0) {
    return (
      <div className={cn(DISCLOSURE_PANEL_BG, "p-4", className)}>
        <p className={cn("text-sm", DISCLOSURE_PANEL_MUTED)}>
          {t("disclosures.empty")}
        </p>
      </div>
    );
  }

  // ─── 공시 목록 + AI 사실 추출 진입점 ──────────────────────────────────────
  //
  // 공시 행: 접수일 / 공시 제목 / DART 원문 링크 / AI 사실 추출 버튼(ADR-0031 D4).
  // 버튼 클릭 시 해당 행 아래 DisclosureFactsPanel 인라인 확장.

  return (
    <div className={cn(DISCLOSURE_PANEL_BG, className)}>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              <th
                scope="col"
                className={cn(
                  "whitespace-nowrap px-4 py-2.5 text-left",
                  DISCLOSURE_PANEL_HEADER,
                )}
              >
                {t("disclosures.colRceptDate")}
              </th>
              <th
                scope="col"
                className={cn("px-4 py-2.5 text-left", DISCLOSURE_PANEL_HEADER)}
              >
                {t("disclosures.colReportName")}
              </th>
              <th
                scope="col"
                className={cn(
                  "whitespace-nowrap px-4 py-2.5 text-left",
                  DISCLOSURE_PANEL_HEADER,
                )}
              >
                {t("disclosures.colDartLink")}
              </th>
              {/*
               * AI 사실 추출 열 — ADR-0031 D4.
               * 열 헤더는 중립 라벨만. 판단·추천·평가 어휘 0.
               */}
              <th
                scope="col"
                className={cn(
                  "whitespace-nowrap px-4 py-2.5 text-left",
                  DISCLOSURE_PANEL_HEADER,
                )}
              >
                {t("factExtraction.extractButton")}
              </th>
            </tr>
          </thead>
          <tbody>
            {data.disclosures.map((item, idx) => {
              const rowKey = makeRowKey(item);
              const isExpanded = expandedKey === rowKey;
              const rceptNo = extractRceptNo(item.dartUrl);

              return (
                <>
                  <tr
                    key={rowKey}
                    className={cn(
                      "border-b border-neutral-100 last:border-0",
                      idx % 2 !== 0 && !isExpanded && DISCLOSURE_PANEL_ROW_STRIPE,
                    )}
                  >
                    {/* 접수일 — 날짜 사실, 중립 텍스트. */}
                    <td
                      className={cn(
                        "whitespace-nowrap px-4 py-2.5 align-top",
                        DISCLOSURE_PANEL_DATE,
                      )}
                    >
                      {item.rceptDate}
                    </td>

                    {/* 공시 제목 — DART 원문 그대로. EXTERNAL_QUOTE (ADR-0026 D2). */}
                    <td
                      className={cn("px-4 py-2.5 align-top", DISCLOSURE_PANEL_TEXT)}
                    >
                      {item.reportName}
                    </td>

                    {/* DART 원문 링크 — target=_blank rel=noopener noreferrer. */}
                    <td className="px-4 py-2.5 align-top">
                      <a
                        href={item.dartUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className={cn("text-sm", DISCLOSURE_PANEL_LINK)}
                        aria-label={t("disclosures.dartLinkLabel")}
                      >
                        {t("disclosures.colDartLink")}
                      </a>
                    </td>

                    {/*
                     * AI 사실 추출 버튼 — ADR-0031 D4 on-demand.
                     * 버튼은 중립 톤. 등락색·판단색 0.
                     * rceptNo 추출 불가 시 버튼 비활성화.
                     */}
                    <td className="px-4 py-2.5 align-top">
                      {rceptNo ? (
                        <button
                          type="button"
                          onClick={() => handleToggle(item)}
                          className="rounded border border-neutral-300 px-2 py-1 text-xs font-medium text-neutral-600 hover:bg-neutral-50"
                          aria-expanded={isExpanded}
                          aria-label={t("factExtraction.extractButton")}
                        >
                          {isExpanded
                            ? t("factExtraction.collapseButton")
                            : t("factExtraction.extractButton")}
                        </button>
                      ) : (
                        <span className={cn("text-xs", DISCLOSURE_PANEL_MUTED)}>—</span>
                      )}
                    </td>
                  </tr>

                  {/*
                   * AI 사실 추출 결과 행 — 확장 시 인라인 표시.
                   * DisclosureFactsPanel 이 내부에서 게이트(D3/D2/D6) 적용.
                   */}
                  {isExpanded && rceptNo ? (
                    <tr
                      key={`${rowKey}-facts`}
                      className="border-b border-neutral-100"
                    >
                      <td
                        colSpan={4}
                        className="px-4 pb-3 pt-1"
                      >
                        <DisclosureFactsPanel
                          code={code}
                          rceptNo={rceptNo}
                          disclosureTitle={item.reportName}
                        />
                      </td>
                    </tr>
                  ) : null}
                </>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
