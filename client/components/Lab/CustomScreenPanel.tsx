"use client";

/**
 * CustomScreenPanel — 저장 custom pack 으로 screen 실행 UI (ADR-0025 D4/D6).
 *
 * PackLibrary 의 각 저장 pack 행에서 "이 pack 으로 screen" 클릭 시 열린다.
 * pack 의 factors 를 선택지로 삼는 conditions builder + as_of + SecurityTypeSelector
 * + 실행 → result_codes 표시 + custom run 저장.
 *
 * No Advice 시각 경계 (ADR-0025 D6 / ADR-0022 D4 / ADR-0007 D2.2):
 *   - 결과는 boolean 필터 통과 종목코드 목록만. 값/점수 0.
 *   - custom composite factor 의 값·점수에 자동 강조·랭킹·순위 라벨 0.
 *   - 모든 톤: grayscale(neutral) 계열만. 등락색·판단색 0.
 *   - 행 순서 = 결정적(backend 반환 순서, 자동 정렬 금지).
 *   - 추천 어휘 0.
 *
 * 색은 명명 상수(CUSTOM_SCREEN_RESULT_*) 로 export — custom-screen-visual-gate.test.tsx
 * 가 실제 렌더 색과 1:1 대조해 등락색/컬러맵 회귀를 자동 차단한다.
 *
 * 인증 경계:
 *   custom screen 은 인증 필요(backend Bearer). 미로그인(401) 시 로그인 유도 표시.
 *   signIn 은 AuthButton 과 동일 패턴(signIn("google")).
 *
 * 관련:
 *   - lib/api/custom-screen.ts — customScreen / saveCustomRun.
 *   - components/Screener/SecurityTypeSelector.tsx — 자산군 선택.
 *   - components/Screener/ResultsTable.tsx — result_codes 표시.
 *   - lib/api/factor-packs.ts — SavedPackOut / FactorPack.
 */

import { useMutation } from "@tanstack/react-query";
import { signIn } from "next-auth/react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { ResultsTable } from "@/components/Screener/ResultsTable";
import { SecurityTypeSelector } from "@/components/Screener/SecurityTypeSelector";
import { ApiError } from "@/lib/api/client";
import { customScreen, saveCustomRun } from "@/lib/api/custom-screen";
import type { FactorDef } from "@/lib/api/factor-packs";
import type { ScreenRunSnapshot } from "@/lib/api/runs";
import {
  DEFAULT_SECURITY_TYPES,
  SCREEN_OP_OPTIONS,
  type ScreenCondition,
  type ScreenOp,
  type ScreenResult,
  type SecurityType,
} from "@/lib/api/screen";
import { useAsOfStore } from "@/state/as-of-store";
import { cn } from "@/lib/utils";

// ── No Advice 시각 상수 (lab-visual-gate 패턴 — 이 상수를 테스트가 검증). ──────

/**
 * 결과 코드 셀 톤 — grayscale(neutral) 계열만. 판단색/등락색 0.
 * custom-screen-visual-gate.test.tsx 가 이 상수를 검증한다(ADR-0025 D6).
 */
export const CUSTOM_SCREEN_RESULT_TEXT = "text-neutral-800";
/**
 * 결과 헤더 톤 — grayscale.
 */
export const CUSTOM_SCREEN_RESULT_HEADER = "text-neutral-600";
/**
 * 결과 행 배경 기본(홀수) 톤 — 판단색 아님.
 */
export const CUSTOM_SCREEN_RESULT_ROW_BG = "bg-white";
/**
 * 결과 행 배경 줄무늬(짝수) 톤 — grayscale 가독성용.
 */
export const CUSTOM_SCREEN_RESULT_ROW_STRIPE = "bg-neutral-50";

// ── Props ──────────────────────────────────────────────────────────────────────

interface CustomScreenPanelProps {
  /** 대상 pack slug. */
  readonly packSlug: string;
  /** 대상 pack version. */
  readonly version: string;
  /** 대상 pack 의 factor 정의 목록 — conditions builder 선택지. */
  readonly factors: ReadonlyArray<FactorDef>;
  /** 패널 닫기 콜백. */
  readonly onClose: () => void;
}

// ── 상수 ──────────────────────────────────────────────────────────────────────

const EMPTY_CONDITION: ScreenCondition = { factor: "", op: "<", value: "" };

// ── 401 여부 판단 헬퍼 ────────────────────────────────────────────────────────

function isUnauthorized(err: Error): boolean {
  return err instanceof ApiError && err.status === 401;
}

// ── 메인 컴포넌트 ──────────────────────────────────────────────────────────────

export function CustomScreenPanel({
  packSlug,
  version,
  factors,
  onClose,
}: CustomScreenPanelProps): JSX.Element {
  const t = useTranslations("lab");
  const asOf = useAsOfStore((s) => s.asOf);

  const [conditions, setConditions] = useState<ReadonlyArray<ScreenCondition>>(
    [EMPTY_CONDITION],
  );
  const [selectedFactors, setSelectedFactors] = useState<ReadonlyArray<string>>(
    factors.map((f) => f.canonical_id),
  );
  const [securityTypes, setSecurityTypes] = useState<ReadonlyArray<SecurityType>>(
    DEFAULT_SECURITY_TYPES,
  );

  /** execute 시점 결과 freeze (ResultsTable 표시용). 자동 정렬 금지. */
  const [executedResult, setExecutedResult] = useState<ScreenResult | null>(null);

  /** custom run 저장 성공 id (인라인 표시용). */
  const [savedRunId, setSavedRunId] = useState<string | null>(null);
  const [saveRunError, setSaveRunError] = useState<string | null>(null);

  // ── screen mutation ────────────────────────────────────────────────────────

  const screenMutation = useMutation<ScreenResult, Error>({
    mutationFn: () =>
      customScreen(packSlug, version, conditions, selectedFactors, asOf, securityTypes),
    onSuccess: (result) => {
      // 결과 freeze — 행 순서 결정적(backend 반환 순서, 자동 정렬 0).
      setExecutedResult(result);
      setSavedRunId(null);
      setSaveRunError(null);
    },
  });

  // ── save run mutation ──────────────────────────────────────────────────────

  const saveRunMutation = useMutation<ScreenRunSnapshot, Error>({
    mutationFn: () =>
      saveCustomRun(packSlug, version, conditions, selectedFactors, asOf, securityTypes),
    onSuccess: (snapshot) => {
      setSavedRunId(snapshot.id);
      setSaveRunError(null);
    },
    onError: (err) => {
      setSavedRunId(null);
      setSaveRunError(err.message);
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

  // ── factor 선택 토글 ───────────────────────────────────────────────────────

  const toggleFactor = (canonicalId: string): void => {
    setSelectedFactors((prev) =>
      prev.includes(canonicalId)
        ? prev.filter((id) => id !== canonicalId)
        : [...prev, canonicalId],
    );
  };

  // ── 실행 가능 여부 ─────────────────────────────────────────────────────────

  const canRun =
    conditions.length > 0
    && conditions.every((c) => c.factor.length > 0 && c.value.length > 0)
    && selectedFactors.length > 0
    && !screenMutation.isPending;

  // ── 401 → 로그인 유도 ─────────────────────────────────────────────────────

  const screenIs401 =
    screenMutation.isError && isUnauthorized(screenMutation.error);

  const saveRunIs401 =
    saveRunMutation.isError && isUnauthorized(saveRunMutation.error);

  // ── 렌더 ───────────────────────────────────────────────────────────────────

  return (
    <div
      data-testid="custom-screen-panel"
      className="mt-3 space-y-4 rounded-lg border border-neutral-200 bg-white p-4"
    >
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-medium text-neutral-700">
            {t("customScreen.heading")}
          </p>
          <p className="mt-0.5 font-mono text-[11px] text-neutral-500">
            {packSlug} <span className="text-neutral-400">v{version}</span>
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("customScreen.closeAriaLabel")}
          className="rounded p-1 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
        >
          &times;
        </button>
      </div>

      {/* 인증 안내 — custom screen 은 로그인 필요. */}
      <p className="text-[11px] text-neutral-500">
        {t("customScreen.authRequired")}
      </p>

      {/* 자산군 선택 */}
      <SecurityTypeSelector selected={securityTypes} onChange={setSecurityTypes} />

      {/* 조건 빌더 — factor 선택지 = custom pack 의 canonical_id */}
      <div className="space-y-2">
        <p className="text-sm font-medium text-neutral-700">
          {t("customScreen.conditionsLabel")}
        </p>

        {conditions.length === 0 ? (
          <p className="text-xs text-neutral-500">
            {t("customScreen.noConditions")}
          </p>
        ) : (
          <ul className="space-y-2">
            {conditions.map((condition, index) => (
              <li
                key={index}
                className="flex flex-wrap items-center gap-2 rounded-md border border-neutral-200 bg-neutral-50 p-2"
              >
                {/* factor 선택 — custom pack factors 드롭다운 */}
                {factors.length === 0 ? (
                  <input
                    type="text"
                    value={condition.factor}
                    onChange={(e) => updateCondition(index, { factor: e.target.value })}
                    placeholder={t("customScreen.factorPlaceholder")}
                    aria-label={t("customScreen.factorAriaLabel", { index: index + 1 })}
                    className="min-w-0 flex-1 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
                  />
                ) : (
                  <select
                    value={condition.factor}
                    onChange={(e) => updateCondition(index, { factor: e.target.value })}
                    aria-label={t("customScreen.factorAriaLabel", { index: index + 1 })}
                    className="min-w-0 flex-1 rounded border border-neutral-300 bg-white px-2 py-1 text-xs"
                  >
                    <option value="" disabled>
                      {t("customScreen.factorPlaceholder")}
                    </option>
                    {factors.map((f) => (
                      <option key={f.canonical_id} value={f.canonical_id}>
                        {f.name || f.canonical_id}
                      </option>
                    ))}
                  </select>
                )}

                {/* 연산자 */}
                <select
                  value={condition.op}
                  onChange={(e) => updateCondition(index, { op: e.target.value as ScreenOp })}
                  aria-label={t("customScreen.opAriaLabel", { index: index + 1 })}
                  className="rounded border border-neutral-300 bg-white px-2 py-1 font-mono text-xs"
                >
                  {SCREEN_OP_OPTIONS.map((op) => (
                    <option key={op} value={op}>
                      {op}
                    </option>
                  ))}
                </select>

                {/* 값 */}
                <input
                  type="text"
                  value={condition.value}
                  onChange={(e) => updateCondition(index, { value: e.target.value })}
                  placeholder={t("customScreen.valuePlaceholder")}
                  aria-label={t("customScreen.valueAriaLabel", { index: index + 1 })}
                  className="w-28 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
                />

                {/* 삭제 */}
                <button
                  type="button"
                  onClick={() => removeCondition(index)}
                  aria-label={t("customScreen.removeConditionAriaLabel", { index: index + 1 })}
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
          + {t("customScreen.addCondition")}
        </button>
      </div>

      {/* selected_factors — pack factors 체크박스 */}
      {factors.length > 0 ? (
        <div className="space-y-2">
          <p className="text-sm font-medium text-neutral-700">
            {t("customScreen.selectedFactorsLabel")}
          </p>
          <fieldset>
            <legend className="sr-only">{t("customScreen.selectedFactorsLegend")}</legend>
            <ul className="max-h-40 overflow-y-auto rounded-md border border-neutral-200 bg-white divide-y divide-neutral-100">
              {factors.map((f) => {
                const checked = selectedFactors.includes(f.canonical_id);
                return (
                  <li key={f.canonical_id}>
                    <label className="flex cursor-pointer items-center gap-3 px-3 py-1.5 text-xs hover:bg-neutral-50">
                      <input
                        type="checkbox"
                        value={f.canonical_id}
                        checked={checked}
                        onChange={() => toggleFactor(f.canonical_id)}
                        className="h-3.5 w-3.5 rounded border-neutral-300"
                      />
                      <span className="flex-1 font-mono text-neutral-700">
                        {f.canonical_id}
                      </span>
                      {f.name ? (
                        <span className="text-neutral-400">{f.name}</span>
                      ) : null}
                    </label>
                  </li>
                );
              })}
            </ul>
            {selectedFactors.length === 0 ? (
              <p className="mt-1 text-xs text-neutral-500">
                {t("customScreen.factorSelectNone")}
              </p>
            ) : (
              <p className="mt-1 text-xs text-neutral-500">
                {t("customScreen.factorSelectedCount", { count: selectedFactors.length })}
              </p>
            )}
          </fieldset>
        </div>
      ) : null}

      {/* 실행 버튼 */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => screenMutation.mutate()}
          disabled={!canRun}
          data-testid="custom-screen-run-btn"
          className="inline-flex items-center rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-neutral-300 hover:bg-neutral-800"
        >
          {screenMutation.isPending
            ? t("customScreen.running")
            : t("customScreen.runButton")}
        </button>

        <span className="text-[11px] text-neutral-400">
          {t("customScreen.asOfNote", { asOf })}
        </span>
      </div>

      {/* 실행 에러 — 401 시 로그인 유도 */}
      {screenMutation.isError ? (
        screenIs401 ? (
          <div className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-700">
            <p>{t("customScreen.authError")}</p>
            <button
              type="button"
              onClick={() => void signIn("google")}
              className="mt-1.5 rounded-md border border-neutral-300 bg-white px-3 py-1 text-xs text-neutral-700 hover:border-neutral-400"
            >
              {t("customScreen.signInButton")}
            </button>
          </div>
        ) : (
          <p
            data-testid="custom-screen-error"
            className="text-xs text-neutral-700"
          >
            {t("customScreen.runError", { message: screenMutation.error.message })}
          </p>
        )
      ) : null}

      {/* 결과 — boolean 필터 통과 종목코드 목록. 값/점수/랭킹 0. 행 순서 결정적. */}
      {executedResult !== null ? (
        <div className="space-y-3">
          <p
            data-testid="custom-screen-result-summary"
            className="text-xs text-neutral-600"
          >
            {t("customScreen.resultSummary", {
              total: executedResult.total,
              asOf,
            })}
          </p>

          {/* ResultsTable — codes 배열 순서 그대로(자동 정렬 금지, ADR-0025 D6). */}
          <ResultsTable
            codes={executedResult.result_codes}
            data-testid="custom-screen-results-table"
          />

          {/* custom run 저장 */}
          <div className="space-y-2">
            <button
              type="button"
              onClick={() => {
                saveRunMutation.reset();
                setSavedRunId(null);
                setSaveRunError(null);
                saveRunMutation.mutate();
              }}
              disabled={saveRunMutation.isPending}
              data-testid="custom-screen-save-run-btn"
              className="inline-flex items-center rounded-md border border-neutral-900 bg-white px-4 py-2 text-sm font-medium text-neutral-900 disabled:cursor-not-allowed disabled:border-neutral-300 disabled:text-neutral-300 hover:bg-neutral-100"
            >
              {saveRunMutation.isPending
                ? t("customScreen.savingRun")
                : t("customScreen.saveRunButton")}
            </button>

            {savedRunId !== null ? (
              <p
                data-testid="custom-screen-save-run-success"
                className="text-xs text-neutral-700"
              >
                {t("customScreen.saveRunSuccess")}{" "}
                <span className="font-mono">{savedRunId}</span>
              </p>
            ) : null}

            {saveRunIs401 ? (
              <div className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-700">
                <p>{t("customScreen.authError")}</p>
                <button
                  type="button"
                  onClick={() => void signIn("google")}
                  className="mt-1.5 rounded-md border border-neutral-300 bg-white px-3 py-1 text-xs text-neutral-700 hover:border-neutral-400"
                >
                  {t("customScreen.signInButton")}
                </button>
              </div>
            ) : saveRunError !== null ? (
              <p
                data-testid="custom-screen-save-run-error"
                className="text-xs text-neutral-700"
              >
                {t("customScreen.saveRunError", { message: saveRunError })}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ── 결과 행 렌더 헬퍼 (ResultsTable 을 직접 사용하므로 미사용, 시각 상수 연결용) ──

/**
 * 결과 코드 행 className — grayscale 중립 톤만. 판단색·등락색 0.
 * ADR-0025 D6 / ADR-0022 D4. custom-screen-visual-gate.test.tsx 가 검증.
 */
export function customScreenResultRowClass(isStripe: boolean): string {
  return cn(
    "border-b border-neutral-100 last:border-0",
    isStripe ? CUSTOM_SCREEN_RESULT_ROW_STRIPE : CUSTOM_SCREEN_RESULT_ROW_BG,
  );
}
