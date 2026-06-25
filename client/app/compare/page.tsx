"use client";

/**
 * Compare page — `/compare`.
 *
 * 2~6 종목 side-by-side 비교. backend `/api/stocks/compare?codes=...&as_of=...`
 * 응답을 CompareGrid 로 렌더.
 *
 * UX:
 *   - **종목 검색으로 추가** — StockSearch(이름/코드 combobox)에서 선택하면 비교
 *     대상 칩으로 추가(코드를 외울 필요 없음). 칩의 × 로 제거. 2~6 개.
 *   - "비교 실행" 버튼 클릭 시 useQuery refetch (manual fetch — 칩이 바뀌어도
 *     자동 호출 X, 사용자 확정 후만).
 *   - not_found 종목은 grid 위쪽 warning banner.
 *
 * 관련:
 * - ADR-0008 D3 (Compare 도 as_of 영향 화면).
 * - M0_PLAN T38 / AC-F-05.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { CompareChart } from "@/components/Compare/CompareChart";
import { CompareGrid } from "@/components/Compare/CompareGrid";
import { StockSearch } from "@/components/StockSearch";
import {
  fetchStockCompare,
  type StockCompare,
  type StockSummary,
} from "@/lib/api/stocks";
import { useAsOfStore } from "@/state/as-of-store";

const MIN_CODES = 2;
const MAX_CODES = 6;

export default function ComparePage(): JSX.Element {
  const t = useTranslations("compare");
  const asOf = useAsOfStore((s) => s.asOf);
  // 선택 = 사용자가 검색으로 추가한 종목 칩. 확정 = 실 query 가 실행된 codes.
  const [selected, setSelected] = useState<ReadonlyArray<StockSummary>>([]);
  const [submittedCodes, setSubmittedCodes] = useState<ReadonlyArray<string>>(
    [],
  );

  const selectedCodes = useMemo(
    () => selected.map((s) => s.code),
    [selected],
  );
  const atMax = selected.length >= MAX_CODES;

  // 검색에서 선택 → 칩 추가. 이미 있는 코드는 무시(dedup), 최대 초과도 무시.
  function addStock(item: StockSummary): void {
    setSelected((prev) => {
      if (prev.length >= MAX_CODES) return prev;
      if (prev.some((s) => s.code === item.code)) return prev;
      return [...prev, item];
    });
  }

  function removeStock(code: string): void {
    setSelected((prev) => prev.filter((s) => s.code !== code));
  }

  // submittedCodes 가 빈 배열이면 query 비활성. 사용자가 "비교 실행" 클릭 후에만 call.
  const query = useQuery<StockCompare>({
    queryKey: ["compare", submittedCodes, asOf],
    queryFn: ({ signal }) => fetchStockCompare(submittedCodes, asOf, signal),
    enabled: submittedCodes.length >= MIN_CODES,
  });

  const canSubmit =
    selected.length >= MIN_CODES &&
    selected.length <= MAX_CODES &&
    !query.isFetching;
  // 선택이 마지막 실행 시점과 다르면 stale — 결과를 dim 처리 (oracle T38 M3).
  const isStale =
    submittedCodes.length > 0 &&
    selectedCodes.join(",") !== submittedCodes.join(",");

  function handleSubmit(): void {
    if (selected.length < MIN_CODES || selected.length > MAX_CODES) return;
    setSubmittedCodes(selectedCodes);
  }

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <h1 className="text-xl font-semibold text-neutral-900">{t("title")}</h1>
      <p className="mt-1 text-sm text-neutral-600">
        {t("subtitle", { asOf })}
      </p>

      <section className="mt-6 space-y-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
        <label className="block text-sm font-medium text-neutral-700">
          {t("searchAddLabel")}
        </label>
        {/* 이름/코드 검색 → 선택 시 addStock 콜백(이동 안 함). 최대 도달 시 비활성. */}
        {atMax ? (
          <p className="text-xs text-amber-700">
            {t("maxReached", { max: MAX_CODES })}
          </p>
        ) : (
          <StockSearch
            onSelect={addStock}
            placeholder={t("searchAddPlaceholder")}
            className="w-full"
          />
        )}

        {/* 선택된 비교 대상 칩 */}
        {selected.length > 0 ? (
          <div>
            <p className="mb-1.5 text-xs font-medium text-neutral-500">
              {t("selectedHeading", { count: selected.length, max: MAX_CODES })}
            </p>
            <ul className="flex flex-wrap gap-2">
              {selected.map((s) => (
                <li key={s.code}>
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-neutral-300 bg-white py-1 pl-3 pr-1.5 text-sm">
                    <span className="font-medium text-neutral-900">
                      {s.name}
                    </span>
                    <span className="text-xs text-neutral-400">{s.code}</span>
                    <button
                      type="button"
                      onClick={() => removeStock(s.code)}
                      aria-label={t("removeChipAria", { name: s.name })}
                      className="ml-0.5 flex h-5 w-5 items-center justify-center rounded-full text-neutral-400 transition hover:bg-neutral-100 hover:text-neutral-700"
                    >
                      ×
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="text-xs text-neutral-500">{t("emptySelected")}</p>
        )}

        <button
          type="button"
          onClick={handleSubmit}
          disabled={!canSubmit}
          className="inline-flex items-center rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-neutral-300 hover:bg-neutral-800"
        >
          {query.isFetching ? t("loadingButton") : t("runButton")}
        </button>
      </section>

      <section
        className={`mt-6 ${isStale ? "opacity-50" : ""}`}
        aria-busy={query.isFetching}
      >
        {isStale ? (
          <p className="mb-2 text-xs text-amber-700">
            {t("staleWarning")}
          </p>
        ) : null}

        {query.isError ? (
          <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900">
            {t("loadError", { message: (query.error as Error).message })}
          </div>
        ) : null}

        {query.data ? (
          <>
            {query.data.not_found.length > 0 ? (
              <div
                className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
                role="status"
              >
                {t("notFoundBanner")}{" "}
                <span className="font-mono">
                  {query.data.not_found.join(", ")}
                </span>
              </div>
            ) : null}
            {/* 모든 입력 코드가 lineage 부재인 경우 — banner 만 표시, grid 의
                "표시할 종목이 없습니다" 와 중복 회피 (oracle T38 M2). */}
            {query.data.items.length === 0 ? (
              <p className="text-sm text-neutral-500">
                {t("noItemsAfterFilter", { count: submittedCodes.length })}
              </p>
            ) : (
              <>
                {/* 오버레이 차트를 grid 위에 배치 — 전체 추이 파악 후 지표 비교 UX. */}
                <CompareChart
                  items={query.data.items}
                  asOf={asOf}
                  className="mb-4"
                />
                <CompareGrid stocks={query.data.items} asOf={asOf} />
              </>
            )}
          </>
        ) : (
          !query.isFetching && (
            <p className="text-sm text-neutral-500">
              {t("emptyPrompt")}
            </p>
          )
        )}
      </section>
    </main>
  );
}
