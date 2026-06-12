"use client";

/**
 * EvaluatePreview — Factor Lab 평가 미리보기 (M2 T74 Phase 2).
 *
 * editor 가 정의 중인 pack 을 **저장 없이** 즉시 평가해(POST evaluate) 결과를
 * 중립 표로 보여준다. 행=code, 열=factor(canonical_id/name), 셀=value+unit 또는
 * "N/A"(na_reason).
 *
 * No Advice 시각 경계 (ADR-0022 D4 / ADR-0007 D2.2 — 본 컴포넌트의 존재 이유):
 *   - **등락색 0**: 값의 부호/크기로 셀을 색칠하지 않는다. 모든 값은 동일한
 *     중립 톤(neutral grayscale)으로만 렌더 — 어떤 종목·factor 도 시각적으로
 *     "좋다/나쁘다"로 읽히지 않는다.
 *   - **랭킹/순위/Top-N 0**: 순위 배지·"주목 종목" 강조·하이라이트 없음.
 *   - **자동 정렬 0**: results 는 codes 입력 순서 그대로(default sort ≠ score,
 *     ADR-0022 D1). 정렬은 사용자 명시 클릭만 — 본 범위는 입력 순서로 충분.
 *   - **색조 컬러맵 0**: percentile 등 유니버스-상대 값도 grayscale/중립 톤만.
 *     heatmap 색조(빨강~파랑 그라데이션) 금지(ADR-0022 D4).
 *   - 해석/추천/전망 텍스트 0 — 셀은 사실값(또는 결손)만.
 *
 * 색은 명명 상수(EVAL_TEXT_COLOR 등)로 export — lab-visual-gate.test.tsx 가
 * 실제 렌더 색과 1:1 로 대조해 등락색/컬러맵 회귀를 자동 차단한다.
 */

import { useMutation } from "@tanstack/react-query";
import { AlertCircle, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import {
  evaluateFactorPack,
  EVALUATE_MAX_CODES,
  type EvaluateResult,
  type EvaluatedRow,
  type FactorPack,
} from "@/lib/api/factor-packs";
import { cn } from "@/lib/utils";

/**
 * 평가 표 셀/텍스트의 단일 중립 톤 — 모든 값에 동일 적용(판단색 분기 없음).
 * grayscale 계열만 사용한다는 사실을 명명 상수로 고정해 시각 게이트가 검증한다.
 */
export const EVAL_TEXT_COLOR = "text-neutral-700";
/** 결손("N/A") 셀 톤 — 값과 구분되는 흐린 grayscale(판단색 아님). */
export const EVAL_NA_COLOR = "text-neutral-400";
/** 헤더 텍스트 톤 — grayscale. */
export const EVAL_HEADER_COLOR = "text-neutral-600";

/**
 * 소표본 디스클로저 배지 배경 톤 — neutral grayscale(ADR-0024 D3 / §2.2).
 * 경고색(빨강·주황·노랑)·등락색 절대 금지. 사실 고지용 중립 배지.
 * lab-visual-gate 가 이 상수를 검증해 컬러 회귀를 차단한다.
 */
export const EVAL_SMALL_SAMPLE_BG = "bg-neutral-100";
/** 소표본 디스클로저 텍스트 톤 — neutral grayscale. */
export const EVAL_SMALL_SAMPLE_TEXT = "text-neutral-500";

interface EvaluatePreviewProps {
  readonly pack: FactorPack;
  /** PIT 기준 일자("YYYY-MM-DD", 전역 as-of store). */
  readonly asOf: string;
  readonly className?: string;
}

/** 쉼표 입력 → 정규화 코드 배열(공백 제거, 빈 토큰 제거, 상한 적용). */
function parseCodes(raw: string): string[] {
  return raw
    .split(",")
    .map((c) => c.trim())
    .filter((c) => c.length > 0)
    .slice(0, EVALUATE_MAX_CODES);
}

/**
 * 평가 표 헤더(열) — pack 정의 순서의 factor 목록. results 의 첫 행에서 factor
 * 메타(canonical_id/name)를 취해 열 순서를 정한다(pack 정의 순서 보존).
 */
function deriveColumns(
  rows: ReadonlyArray<EvaluatedRow>,
): ReadonlyArray<{ canonicalId: string; name: string }> {
  const first = rows[0];
  if (!first) return [];
  return first.factors.map((f) => ({
    canonicalId: f.canonicalId,
    name: f.name,
  }));
}

export function EvaluatePreview({
  pack,
  asOf,
  className,
}: EvaluatePreviewProps): JSX.Element {
  const t = useTranslations("lab");
  const [codesInput, setCodesInput] = useState("");

  const mutation = useMutation<EvaluateResult, Error, string[]>({
    mutationFn: (codes) => evaluateFactorPack(pack, asOf, codes),
  });

  const codes = useMemo(() => parseCodes(codesInput), [codesInput]);

  const onEvaluate = (): void => {
    if (codes.length === 0) return;
    mutation.mutate(codes);
  };

  const result = mutation.data ?? null;
  const columns = result ? deriveColumns(result.results) : [];

  return (
    <section className={cn("space-y-3", className)} aria-live="polite">
      <h2 className="text-sm font-semibold text-neutral-900">
        {t("evaluate.heading")}
      </h2>
      <p className="text-xs text-neutral-500">{t("evaluate.description")}</p>

      {/* code 입력 + 평가 버튼 */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="block flex-1 text-xs font-medium text-neutral-600">
          {t("evaluate.codesLabel", { max: EVALUATE_MAX_CODES })}
          <input
            type="text"
            value={codesInput}
            onChange={(e) => setCodesInput(e.target.value)}
            placeholder={t("evaluate.codesPlaceholder")}
            className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
          />
        </label>
        <button
          type="button"
          onClick={onEvaluate}
          disabled={mutation.isPending || codes.length === 0}
          className="inline-flex items-center justify-center rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-neutral-300 hover:bg-neutral-800"
        >
          {mutation.isPending ? (
            <Loader2 size={14} className="mr-1.5 animate-spin" aria-hidden="true" />
          ) : null}
          {t("evaluate.run")}
        </button>
      </div>

      {/* 평가 대상 as_of 안내 — 사실 표시(중립). */}
      <p className="text-[11px] text-neutral-400">
        {t("evaluate.asOfNote", { asOf })}
      </p>

      {/* 요청 자체 실패(네트워크/서버) */}
      {mutation.isError ? (
        <p className="flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <AlertCircle size={14} aria-hidden="true" />
          {t("evaluate.requestError", { message: mutation.error.message })}
        </p>
      ) : null}

      {/* invalid pack — issues 안내(검증 stage/message). 중립 톤. */}
      {result && !result.valid ? (
        <div className="space-y-2">
          <p className="flex items-center gap-2 text-sm text-red-800">
            <AlertCircle size={16} aria-hidden="true" />
            {t("evaluate.invalidCount", { count: result.issues.length })}
          </p>
          <ul className="space-y-1">
            {result.issues.map((issue, index) => (
              <li
                key={index}
                className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-900"
              >
                <span className="mr-2 inline-block rounded bg-red-200 px-1.5 py-0.5 font-mono text-[10px] uppercase text-red-900">
                  {issue.stage}
                </span>
                {issue.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* 평가 결과 표 — valid 이고 결과가 있을 때. */}
      {result && result.valid && result.results.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
          <table className="w-full min-w-max border-collapse text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <th
                  scope="col"
                  className={cn(
                    "px-4 py-2.5 text-left font-medium",
                    EVAL_HEADER_COLOR,
                  )}
                >
                  {t("evaluate.colCode")}
                </th>
                {columns.map((col) => (
                  <th
                    key={col.canonicalId}
                    scope="col"
                    className={cn(
                      "px-4 py-2.5 text-right font-medium",
                      EVAL_HEADER_COLOR,
                    )}
                  >
                    {/* canonical_id(식별자) + 사람이 읽는 이름 — 둘 다 사실. */}
                    <span className="font-mono text-xs">{col.canonicalId}</span>
                    {col.name ? (
                      <span className="ml-1.5 text-[11px] font-normal text-neutral-400">
                        {col.name}
                      </span>
                    ) : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {/* 행 순서 = codes 입력 순서 그대로(자동 정렬 금지). */}
              {result.results.map((row, rowIdx) => (
                <tr
                  key={row.code}
                  className={cn(
                    "border-b border-neutral-100 last:border-0",
                    // 줄무늬는 가독성용 grayscale 만 — 값과 무관(판단색 아님).
                    rowIdx % 2 === 1 ? "bg-neutral-50/50" : "bg-white",
                  )}
                >
                  <th
                    scope="row"
                    className="px-4 py-2 text-left font-mono font-normal text-neutral-800"
                  >
                    {row.code}
                  </th>
                  {row.factors.map((f) => (
                    <td
                      key={f.canonicalId}
                      className={cn(
                        "px-4 py-2 text-right font-mono tabular-nums",
                        // 모든 값에 동일 중립 톤 — 부호/크기로 색 분기 없음.
                        f.isNa ? EVAL_NA_COLOR : EVAL_TEXT_COLOR,
                      )}
                    >
                      {f.isNa ? (
                        <span title={f.naReason ?? undefined}>
                          {t("evaluate.na")}
                        </span>
                      ) : (
                        <>
                          {f.value}
                          {f.unit ? (
                            <span className="ml-1 text-[11px] text-neutral-400">
                              {f.unit}
                            </span>
                          ) : null}
                          {/*
                           * 소표본 디스클로저 (ADR-0024 Phase 2 / §2.5 / §2.2 / §2.7):
                           *   - small_sample=true 이고 sampleSize 가 있을 때만 표시.
                           *   - 값 자체는 항상 보존 — 소표본이어도 숨기거나 N/A 처리 금지.
                           *   - n==1 의 percentile=100 도 small_sample=true → "최상위"
                           *     오인 차단이 핵심(ADR-0024 D3). 값은 표시, 모집단 사실만 고지.
                           *   - neutral 톤만(빨강·파랑·녹색 등 판단색 절대 금지).
                           *   - "신뢰불가/부정확/무시" 가치·지시어 금지 — 사실 텍스트만.
                           */}
                          {f.smallSample && f.sampleSize !== null ? (
                            <span
                              className={cn(
                                "mt-1 block rounded px-1.5 py-0.5 font-sans text-[10px] font-normal leading-tight",
                                EVAL_SMALL_SAMPLE_BG,
                                EVAL_SMALL_SAMPLE_TEXT,
                              )}
                              title={t("evaluate.smallSampleHint")}
                              data-testid="small-sample-disclosure"
                            >
                              {t("evaluate.smallSampleLabel", {
                                n: f.sampleSize,
                              })}
                            </span>
                          ) : null}
                        </>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {/* valid 이나 결과 0행 — 빈 안내. */}
      {result && result.valid && result.results.length === 0 ? (
        <p className="text-xs text-neutral-500">{t("evaluate.empty")}</p>
      ) : null}
    </section>
  );
}
