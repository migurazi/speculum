"use client";

/**
 * Screener page — POST /api/screen end-to-end 흐름.
 *
 * ADR-0008 D3.1 + M0_PLAN T36 + AC-F-03 의 frontend 구현. 조건 빌더 +
 * Run button + 결과 테이블. useMutation 이 backend 의 /api/screen 호출.
 *
 * M0 MVP:
 *   - 조건 free text input (factor canonical_id 직접 입력).
 *   - selected_factors 도 free text (M1+ shadcn Combobox).
 *   - 결과 = 종목코드 list 만 (T37 Stock Detail 합류 시 지표 표시).
 *
 * 관련 ADR / 문서:
 * - ADR-0008 D3.1 — Screener 의 모든 조건이 as_of 기준.
 * - M0_PLAN T36 / AC-F-03.
 */

import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { SaveRunButton } from "@/components/SaveRunButton";
import { ConditionBuilder } from "@/components/Screener/ConditionBuilder";
import { ResultsTable } from "@/components/Screener/ResultsTable";
import {
  executeScreen,
  type ScreenCondition,
  type ScreenResult,
} from "@/lib/api/screen";
import { useAsOfStore } from "@/state/as-of-store";

const INITIAL_CONDITIONS: ReadonlyArray<ScreenCondition> = [];

export default function ScreenerPage(): JSX.Element {
  const asOf = useAsOfStore((s) => s.asOf);
  const [conditions, setConditions] = useState<ReadonlyArray<ScreenCondition>>(
    INITIAL_CONDITIONS,
  );
  // selected_factors — 결과 표시할 factor canonical_id list. M0 = comma-
  // separated text input. M1+ Combobox.
  const [selectedFactorsText, setSelectedFactorsText] = useState<string>("");

  // selectedFactors 는 mutation 과 SaveRunButton 양쪽이 동일 입력을 사용해야
  // backend 가 같은 result_hash 생성. useMemo 로 derived state 화.
  const selectedFactors = useMemo<ReadonlyArray<string>>(
    () =>
      selectedFactorsText
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.length > 0),
    [selectedFactorsText],
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
    readonly asOf: string;
  } | null>(null);

  const mutation = useMutation<ScreenResult, Error>({
    mutationFn: () =>
      executeScreen(
        {
          conditions,
          selected_factors: selectedFactors,
        },
        asOf,
      ),
    onSuccess: (result) => {
      // 실행 성공 시점에 입력을 freeze — SaveRunButton 이 캡처본만 사용.
      setExecutedSnapshot({
        result,
        conditions,
        selectedFactors,
        asOf,
      });
    },
  });

  const canRun =
    conditions.length > 0
    && conditions.every((c) => c.factor && c.value)
    && selectedFactorsText.trim().length > 0
    && !mutation.isPending;

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <h1 className="text-xl font-semibold text-neutral-900">Screener</h1>
      <p className="mt-1 text-sm text-neutral-600">
        조건을 추가하고 실행하면 기준 일자 ({asOf}) 의 데이터로 필터한 종목코드
        list 가 표시됩니다.
      </p>

      <section className="mt-6 space-y-6 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
        <ConditionBuilder
          conditions={conditions}
          onChange={setConditions}
        />

        <div className="space-y-2">
          <label
            htmlFor="selected-factors"
            className="block text-sm font-medium text-neutral-700"
          >
            결과 표시 factor (쉼표 구분)
          </label>
          <input
            id="selected-factors"
            type="text"
            value={selectedFactorsText}
            onChange={(e) => setSelectedFactorsText(e.target.value)}
            placeholder="per:ttm-consolidated-ifrs, roe:ttm-consolidated-ifrs"
            className="w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
          />
          <p className="text-xs text-neutral-500">
            factor canonical_id 를 쉼표로 구분해 입력합니다.
          </p>
        </div>

        <div>
          <button
            type="button"
            onClick={() => mutation.mutate()}
            disabled={!canRun}
            className="inline-flex items-center rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-neutral-300 hover:bg-neutral-800"
          >
            {mutation.isPending ? "실행 중..." : "Screener 실행"}
          </button>
        </div>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">결과</h2>
        {mutation.isError ? (
          <p className="mt-2 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
            실행 실패: {(mutation.error as Error).message}
          </p>
        ) : null}
        {executedSnapshot ? (
          <div className="mt-2 space-y-3">
            <p className="text-xs text-neutral-600">
              총 {executedSnapshot.result.total} 종목 매칭 · 기준{" "}
              <span className="font-mono">{executedSnapshot.asOf}</span>
            </p>
            <ResultsTable codes={executedSnapshot.result.result_codes} />
            {/* Save Run — execute 시점 캡처본만 사용 (oracle T40 C1/C2). 사용자가
                conditions/asOf 변경해도 SaveRunButton 은 "방금 본 결과" 를 저장. */}
            <SaveRunButton
              conditions={executedSnapshot.conditions}
              selectedFactors={executedSnapshot.selectedFactors}
              asOf={executedSnapshot.asOf}
              canSave={true}
            />
          </div>
        ) : (
          <p className="mt-2 text-sm text-neutral-500">
            아직 실행 결과가 없습니다.
          </p>
        )}
      </section>
    </main>
  );
}
