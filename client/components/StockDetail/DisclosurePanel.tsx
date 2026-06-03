"use client";

/**
 * DisclosurePanel — 종목 공시 목록 패널 (ADR-0026).
 *
 * 표시 범위 하드 제약 (ADR-0026 D1):
 *   - 공시 제목(reportName) + 접수일(rceptDate) + DART 원문 링크(dartUrl) 만.
 *   - 본문·요약·자체 분류라벨·"중요공시" 배지·필터 UI·정렬 토글 0.
 *   - 클릭 시 DART 페이지 이탈(target=_blank rel=noopener noreferrer).
 *
 * scope = EXTERNAL_QUOTE (ADR-0026 D2):
 *   - 공시 제목은 DART 원문 그대로 — forbidden_words 검사 대상 아님.
 *   - Speculum 이 생성하는 라벨·배지·요약은 0.
 *
 * trigger = 사용자 명시 요청 1종목 (ADR-0026 D3):
 *   - Stock Detail 진입 종목만 on-demand fetch.
 *   - 홈/Watchlist/Screener 에 공시 feed push 금지.
 *
 * PIT 정합 (ADR-0026 D4):
 *   - asOf → backend 가 rcept_date <= asOf 필터 → look-ahead 0.
 *
 * 중립 톤 원칙 (ADR-0007 D2 / §2.2 No Advice):
 *   - 모든 색 상수를 DISCLOSURE_PANEL_* 로 export — visual gate 검증 대상.
 *   - grayscale/neutral 계열만. 등락색(red/blue/green) · 판단색 토큰 0.
 *   - 링크는 hover underline 만(색 변화 0).
 *
 * 관련:
 *   - ADR-0026 공시 metadata 표시 설계.
 *   - lib/api/disclosures.ts — API client.
 *   - components/__tests__/disclosure-panel-gate.test.tsx — visual gate.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";

import { fetchDisclosures } from "@/lib/api/disclosures";
import { cn } from "@/lib/utils";

// =============================================================================
// 중립 톤 상수 — visual gate 검증 대상 (ADR-0026 D1 / ADR-0007 D2)
//
// 규칙: grayscale/neutral/white 만. 등락색·판단색 토큰 금지.
// disclosure-panel-gate.test.tsx 가 이 상수 전체를 neutral 검증한다.
// =============================================================================

/** 패널 전체 배경 + 테두리. */
export const DISCLOSURE_PANEL_BG = "rounded-lg border border-neutral-200 bg-white";

/** 헤더 영역 라벨 색. */
export const DISCLOSURE_PANEL_HEADING = "text-sm font-medium text-neutral-700";

/** 테이블 헤더 셀 텍스트 색. */
export const DISCLOSURE_PANEL_HEADER = "font-medium text-neutral-600";

/** 공시 제목 텍스트 색 — EXTERNAL_QUOTE 원문 그대로. */
export const DISCLOSURE_PANEL_TEXT = "text-neutral-800";

/** 접수일 텍스트 색 — 날짜 중립. */
export const DISCLOSURE_PANEL_DATE = "font-mono text-neutral-500";

/** DART 원문 링크 텍스트 색 — hover 시 underline 만, 색 변화 없음. */
export const DISCLOSURE_PANEL_LINK = "text-neutral-700 underline-offset-2 hover:underline";

/** 짝수 행 줄무늬 — 중립 배경. */
export const DISCLOSURE_PANEL_ROW_STRIPE = "bg-neutral-50/60";

/** 로딩·에러·빈 상태 텍스트 색. */
export const DISCLOSURE_PANEL_MUTED = "text-neutral-500";

// =============================================================================
// Types
// =============================================================================

interface DisclosurePanelProps {
  /** KRX 6자리 종목코드. */
  readonly code: string;
  /**
   * PIT 기준 일자 ("YYYY-MM-DD").
   * 미전달 시 backend default(오늘).
   * ADR-0026 D4: as_of 이후 공시 backend 에서 제외.
   */
  readonly asOf?: string;
  readonly className?: string;
}

// =============================================================================
// DisclosurePanel
// =============================================================================

/**
 * 종목 공시 목록 패널.
 *
 * ADR-0026 D1: 제목 + 접수일 + DART 원문링크만 렌더.
 * ADR-0026 D3: on-demand fetch — enabled = code.length > 0.
 * ADR-0026 D4: asOf → searchParams 로 backend 전달.
 *
 * 정렬: backend 반환 순서(최신순) 그대로 — 클라이언트 재정렬 UI 0.
 * 필터: 클라이언트 필터 UI 0 — "중요공시" 배지/토글 0.
 */
export function DisclosurePanel({
  code,
  asOf,
  className,
}: DisclosurePanelProps): JSX.Element {
  const t = useTranslations("stock");

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["disclosures", code, asOf ?? null] as const,
    queryFn: ({ signal }) =>
      fetchDisclosures(code, asOf ? { asOf } : {}, signal),
    // staleTime 5분 — DART 실데이터, on-demand 캐시.
    staleTime: 5 * 60 * 1000,
    enabled: code.length > 0,
  });

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
        {/* 에러 알림 — neutral 테두리/배경. 판단색(red) 0. */}
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

  // ─── 공시 목록 표 ──────────────────────────────────────────────────────────
  //
  // ADR-0026 D1: 제목/접수일/링크 3열만. 본문·중요도·분류 열 0.
  // 정렬: backend 반환 순서(최신순 rcept_date 역순) 그대로.
  // 클라이언트 재정렬 버튼·필터 토글 0.

  return (
    <div className={cn(DISCLOSURE_PANEL_BG, className)}>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              {/* 접수일 열 — 날짜 사실. */}
              <th
                scope="col"
                className={cn(
                  "whitespace-nowrap px-4 py-2.5 text-left",
                  DISCLOSURE_PANEL_HEADER,
                )}
              >
                {t("disclosures.colRceptDate")}
              </th>
              {/* 공시 제목 열 — EXTERNAL_QUOTE (ADR-0026 D2). */}
              <th
                scope="col"
                className={cn("px-4 py-2.5 text-left", DISCLOSURE_PANEL_HEADER)}
              >
                {t("disclosures.colReportName")}
              </th>
              {/* DART 원문 링크 열. */}
              <th
                scope="col"
                className={cn(
                  "whitespace-nowrap px-4 py-2.5 text-left",
                  DISCLOSURE_PANEL_HEADER,
                )}
              >
                {t("disclosures.colDartLink")}
              </th>
            </tr>
          </thead>
          <tbody>
            {data.disclosures.map((item, idx) => (
              <tr
                key={`${item.rceptDate}-${item.dartUrl}`}
                className={cn(
                  "border-b border-neutral-100 last:border-0",
                  // 짝수 행 줄무늬 — 중립(ADR-0026 D1).
                  idx % 2 !== 0 && DISCLOSURE_PANEL_ROW_STRIPE,
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

                {/*
                 * 공시 제목 — DART 원문 report_nm 그대로.
                 * ADR-0026 D2 EXTERNAL_QUOTE: 금지어 검사 skip.
                 * Speculum 이 추가하는 라벨·배지·요약 0.
                 */}
                <td
                  className={cn("px-4 py-2.5 align-top", DISCLOSURE_PANEL_TEXT)}
                >
                  {item.reportName}
                </td>

                {/*
                 * DART 원문 링크 — target=_blank rel=noopener noreferrer.
                 * ADR-0026 D1: 클릭 시 DART 페이지 이탈.
                 * 색 변화 없이 hover underline 만.
                 */}
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
