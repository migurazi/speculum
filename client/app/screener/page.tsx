"use client";

/**
 * Screener page — POST /api/screen end-to-end 흐름.
 *
 * ADR-0008 D3.1 + M0_PLAN T36 + AC-F-03 의 frontend 구현. 조건 빌더 +
 * Run button + 결과 테이블. useMutation 이 backend 의 /api/screen 호출.
 *
 * selected_factors: /api/factors 체크박스 다중 선택.
 *   - 선택된 canonical_id 배열이 mutation + SaveRunButton 에 그대로 흐름.
 *   - factor 목록 로딩 중에는 체크박스 disabled.
 *   - fetch 실패 시 안내 메시지 표시 (기존 text input 으로 미복구 — canRun=false).
 *
 * 관련 ADR / 문서:
 * - ADR-0008 D3.1 — Screener 의 모든 조건이 as_of 기준.
 * - M0_PLAN T36 / AC-F-03.
 */

import { useQuery, useMutation } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { SaveRunButton } from "@/components/SaveRunButton";
import { ConditionBuilder, FACTORS_QUERY_KEY } from "@/components/Screener/ConditionBuilder";
import { ResultsTable } from "@/components/Screener/ResultsTable";
import { SecurityTypeSelector } from "@/components/Screener/SecurityTypeSelector";
import { fetchFactors } from "@/lib/api/factors";
import {
  DEFAULT_SECURITY_TYPES,
  executeScreen,
  type ScreenCondition,
  type ScreenResult,
  type SecurityType,
} from "@/lib/api/screen";
import {
  toScreenConditions,
  type EditableCondition,
} from "@/lib/ui/editable-condition";
import { useAsOfStore } from "@/state/as-of-store";

const INITIAL_CONDITIONS: ReadonlyArray<EditableCondition> = [];

/** factor pack 은 정적 — 30분 stale (ConditionBuilder 와 동일 설정). */
const FACTORS_STALE_TIME = 30 * 60 * 1000;

export default function ScreenerPage(): JSX.Element {
  const t = useTranslations("screener");
  const asOf = useAsOfStore((s) => s.asOf);
  // UI 전용 id 를 가진 row 목록(R-3). API 경계에서 toScreenConditions 로 strip.
  const [conditions, setConditions] = useState<ReadonlyArray<EditableCondition>>(
    INITIAL_CONDITIONS,
  );
  // selected_factors — 체크박스 다중 선택 상태. canonical_id string[]
  const [selectedFactors, setSelectedFactors] = useState<ReadonlyArray<string>>(
    [],
  );
  // security_types — 자산군 선택. 기본 common 만. ADR-0023 D4/D7.
  const [securityTypes, setSecurityTypes] = useState<ReadonlyArray<SecurityType>>(
    DEFAULT_SECURITY_TYPES,
  );

  /**
   * Executed snapshot — execute 시점의 입력을 freeze (oracle T40 C1/C2).
   *
   * ADR-0008 D7 "Run snapshot freeze" 의 의도:
   *   사용자가 본 결과 = 저장될 snapshot. 따라서 SaveRunButton 은 live conditions/
   *   selectedFactors/asOf 가 아닌 mutation 성공 시점의 캡처본 사용.
   *
   * conditions/asOf 가 사용자 편집으로 변경되어도 본 snapshot 은 그대로 — 사용자가
   *   "다시 실행" 누르면 새 snapshot 으로 교체.
   */
  const [executedSnapshot, setExecutedSnapshot] = useState<{
    readonly result: ScreenResult;
    readonly conditions: ReadonlyArray<ScreenCondition>;
    readonly selectedFactors: ReadonlyArray<string>;
    readonly securityTypes: ReadonlyArray<SecurityType>;
    readonly asOf: string;
  } | null>(null);

  // factor 목록 — ConditionBuilder 와 동일 query key → 공유 cache
  const {
    data: factors,
    isLoading: factorsLoading,
    isError: factorsError,
  } = useQuery({
    queryKey: FACTORS_QUERY_KEY,
    queryFn: ({ signal }) => fetchFactors(signal),
    staleTime: FACTORS_STALE_TIME,
    // retry 횟수는 QueryClient 기본값에 위임.
  });

  /** 체크박스 토글 핸들러 */
  const toggleFactor = (canonicalId: string): void => {
    setSelectedFactors((prev) =>
      prev.includes(canonicalId)
        ? prev.filter((id) => id !== canonicalId)
        : [...prev, canonicalId],
    );
  };

  const mutation = useMutation<ScreenResult, Error>({
    mutationFn: () =>
      executeScreen(
        {
          // UI 전용 id strip — wire body 에 id 미포함.
          conditions: toScreenConditions(conditions),
          selected_factors: selectedFactors,
          security_types: securityTypes,
        },
        asOf,
      ),
    onSuccess: (result) => {
      // 실행 성공 시점에 입력을 freeze — SaveRunButton 이 캡처본만 사용.
      // snapshot 도 strip 된 wire conditions 로 저장(save 경계 id 격리).
      setExecutedSnapshot({
        result,
        conditions: toScreenConditions(conditions),
        selectedFactors,
        securityTypes,
        asOf,
      });
    },
  });

  const canRun =
    conditions.length > 0
    && conditions.every((c) => c.factor && c.value)
    && selectedFactors.length > 0
    && !mutation.isPending;

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <h1 className="text-xl font-semibold text-neutral-900">{t("title")}</h1>
      <p className="mt-1 text-sm text-neutral-600">
        {t("description", { asOf })}
      </p>

      <section className="mt-6 space-y-6 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
        {/* 자산군 선택 — ADR-0023 D4/D7. 조건 입력 영역 상단에 배치. */}
        <SecurityTypeSelector
          selected={securityTypes}
          onChange={setSecurityTypes}
        />

        <ConditionBuilder
          conditions={conditions}
          onChange={setConditions}
        />

        {/* 결과 표시 factor 다중 선택 */}
        <div className="space-y-2">
          <div className="text-sm font-medium text-neutral-700">
            {t("factorSectionLabel")}
          </div>

          {factorsError ? (
            <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {t("factorsLoadError")}
            </p>
          ) : factorsLoading ? (
            <p className="text-xs text-neutral-500">{t("factorsLoading")}</p>
          ) : factors && factors.length > 0 ? (
            <fieldset>
              <legend className="sr-only">{t("factorLegend")}</legend>
              <ul className="max-h-48 overflow-y-auto rounded-md border border-neutral-200 bg-white divide-y divide-neutral-100">
                {factors.map((f) => {
                  const checked = selectedFactors.includes(f.canonicalId);
                  return (
                    <li key={f.canonicalId}>
                      <label className="flex cursor-pointer items-center gap-3 px-3 py-2 text-sm hover:bg-neutral-50">
                        <input
                          type="checkbox"
                          value={f.canonicalId}
                          checked={checked}
                          onChange={() => toggleFactor(f.canonicalId)}
                          className="h-4 w-4 rounded border-neutral-300 text-neutral-900"
                        />
                        <span className="flex-1 text-neutral-800">{f.name}</span>
                        {f.unit ? (
                          <span className="text-xs text-neutral-400">
                            {f.unit}
                          </span>
                        ) : null}
                      </label>
                    </li>
                  );
                })}
              </ul>
              {selectedFactors.length === 0 ? (
                <p className="mt-1 text-xs text-neutral-500">
                  {t("factorSelectNone")}
                </p>
              ) : (
                <p className="mt-1 text-xs text-neutral-500">
                  {t("factorSelectedCount", { count: selectedFactors.length })}
                </p>
              )}
            </fieldset>
          ) : (
            <p className="text-xs text-neutral-500">{t("factorNoneAvailable")}</p>
          )}
        </div>

        <div>
          <button
            type="button"
            onClick={() => mutation.mutate()}
            disabled={!canRun}
            className="inline-flex items-center rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-neutral-300 hover:bg-neutral-800"
          >
            {mutation.isPending ? t("runPending") : t("runButton")}
          </button>
        </div>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">{t("resultsHeading")}</h2>
        {mutation.isError ? (
          <p className="mt-2 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
            {t("runError", { message: (mutation.error as Error).message })}
          </p>
        ) : null}
        {executedSnapshot ? (
          <div className="mt-2 space-y-3">
            <p className="text-xs text-neutral-600">
              {t("resultsSummary", {
                total: executedSnapshot.result.total,
                asOf: executedSnapshot.asOf,
              })}
            </p>
            <ResultsTable codes={executedSnapshot.result.result_codes} />
            {/* Save Run — execute 시점 캡처본만 사용 (oracle T40 C1/C2). 사용자가
                conditions/asOf 변경해도 SaveRunButton 은 "방금 본 결과" 를 저장. */}
            <SaveRunButton
              conditions={executedSnapshot.conditions}
              selectedFactors={executedSnapshot.selectedFactors}
              securityTypes={executedSnapshot.securityTypes}
              asOf={executedSnapshot.asOf}
              canSave={true}
            />
          </div>
        ) : (
          <p className="mt-2 text-sm text-neutral-500">
            {t("resultsEmpty")}
          </p>
        )}
      </section>
    </main>
  );
}
