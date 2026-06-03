"use client";

/**
 * SourceAttribution — 8 기둥 §2.1 Fidelity 의 UI backbone.
 *
 * 모든 지표 값 표시 component 는 본 wrapper 를 통과해야 함. TypeScript 의
 * required props (value + source + formula + asOf) 가 빌드 시 누락 검사 —
 * "값만 표시" 우회 회피. CI 게이트 (T41) 가 본 component 미사용 코드 차단.
 *
 * 표시 패턴:
 *   - inline: 값 + 작은 "source · YYYY-MM-DD" suffix.
 *   - hover/inspect: radix-ui Tooltip 으로 산출식 + 전체 출처 정보 표시.
 *
 * 사용 예:
 *   <SourceAttribution
 *     value="12.34"
 *     source="DART"
 *     formula="당기순이익 / 발행주식수"
 *     asOf="2024-09-30"
 *   />
 *
 * 관련 ADR / 문서:
 * - ADR-0002 D3 (Source Citation 7-tuple) — backend 측 모델
 * - ADR-0007 D2 (Source attribution 의무 UI rule)
 * - 8 기둥 §2.1 Fidelity — 모든 값에 식·출처·기준일
 * - M0_PLAN T33 (본 cycle) / AC-P-01
 */

import * as Tooltip from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";
import { useTranslations } from "next-intl";

import {
  SOURCE_LABEL_KO,
  type SourceLabel,
} from "@/lib/types/source";
import { cn } from "@/lib/utils";

export interface SourceAttributionProps {
  /**
   * 표시할 값. 단순 string (이미 format 된 — "12.34", "₩70,000" 등). 호출자
   * 가 format 책임 — component 는 wrap 만.
   *
   * ReactNode 허용 — 색상 강조 같은 inline element 가능. 단 외부 인용
   * (회사명 등) 은 별도 EXTERNAL_QUOTE component 사용 권장.
   */
  readonly value: ReactNode;

  /** 데이터 출처 — ADR-0002 D3 의 SourceKind enum 과 동일. */
  readonly source: SourceLabel;

  /**
   * 산출식 — 한글 표시. 예: "당기순이익 / 발행주식수", "시가총액 / 자기자본".
   * factor 의 canonical_id 와 함께 보여주면 더 명확 (호출자 결정).
   *
   * 단순 표시 데이터 (예: 종가) 의 경우 "KRX 종가 — 보정 없음" 같은 명시 문구.
   */
  readonly formula: string;

  /**
   * PIT 기준일 (ISO 8601 — `YYYY-MM-DD`). 사용자가 가장 자주 보는 신호 —
   * inline suffix 에 표시.
   *
   * ADR-0008 D8 (일 단위 PIT) — 시간 단위 미사용. 운영 시 backend 가 KRX
   * 영업일 기준 정규화 후 전달.
   */
  readonly asOf: string;

  /**
   * 산출식 도출의 데이터 발효일 — 정정공시 등으로 변할 수 있음. None 이면 asOf
   * 와 동일 (단순 표시 데이터).
   */
  readonly effectiveDate?: string;

  /** 추가 className — wrapper element 에 병합. */
  readonly className?: string;
}

/**
 * Source attribution wrapper — 값 + tooltip.
 *
 * 모든 props 가 required (TypeScript 강제). 누락 시 빌드 에러. 8 기둥 §2.1
 * 의 "값만 있는 UI 금지" 약속의 type-level implementation.
 */
export function SourceAttribution({
  value,
  source,
  formula,
  asOf,
  effectiveDate,
  className,
}: SourceAttributionProps): JSX.Element {
  const t = useTranslations("common");
  const sourceKo = SOURCE_LABEL_KO[source];
  return (
    <Tooltip.Provider delayDuration={200}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <span
            className={cn(
              "inline-flex items-baseline gap-1.5 cursor-help",
              className,
            )}
          >
            <span className="font-medium tabular-nums">{value}</span>
            <span className="text-[11px] text-neutral-500">
              {source} · {asOf}
            </span>
          </span>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            side="top"
            align="start"
            sideOffset={4}
            className="z-50 max-w-sm rounded-md bg-neutral-900 px-3 py-2 text-xs text-neutral-100 shadow-lg"
          >
            <div className="space-y-1">
              <div>
                <span className="text-neutral-400">{t("sourceAttribution.tooltipSource")}</span> {sourceKo}
              </div>
              <div>
                <span className="text-neutral-400">{t("sourceAttribution.tooltipFormula")}</span> {formula}
              </div>
              <div>
                <span className="text-neutral-400">{t("sourceAttribution.tooltipAsOf")}</span> {asOf}
              </div>
              {/* oracle 리뷰 M1 — 빈 문자열도 미표시. backend 가 잘못
                  전달한 "" 가 "발효일: " (값 없음) 으로 렌더되지 않도록. */}
              {effectiveDate !== undefined
              && effectiveDate.length > 0
              && effectiveDate !== asOf ? (
                <div>
                  <span className="text-neutral-400">{t("sourceAttribution.tooltipEffectiveDate")}</span>{" "}
                  {effectiveDate}
                </div>
              ) : null}
            </div>
            <Tooltip.Arrow className="fill-neutral-900" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
