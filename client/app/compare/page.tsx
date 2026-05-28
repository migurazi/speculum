"use client";

/**
 * Compare page — `/compare`.
 *
 * 2~6 종목 side-by-side 비교. backend `/api/stocks/compare?codes=...&as_of=...`
 * 응답을 CompareGrid 로 렌더.
 *
 * UX:
 *   - 코드 입력 = comma-separated text input. backend 가 정규화 (zero-pad +
 *     dedup) 책임.
 *   - "비교 실행" 버튼 클릭 시 useQuery refetch (manual fetch — input 이
 *     바뀔 때 자동 호출 X, 사용자 확정 후만).
 *   - not_found 종목은 grid 위쪽 warning banner.
 *
 * 관련:
 * - ADR-0008 D3 (Compare 도 as_of 영향 화면).
 * - M0_PLAN T38 / AC-F-05.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { CompareGrid } from "@/components/Compare/CompareGrid";
import { fetchStockCompare, type StockCompare } from "@/lib/api/stocks";
import { useAsOfStore } from "@/state/as-of-store";

const MIN_CODES = 2;
const MAX_CODES = 6;

/** 입력 텍스트를 raw codes 배열로 — split + trim 만. */
function parseRawCodes(text: string): ReadonlyArray<string> {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/**
 * raw codes 를 6 자리 zero-pad + dedup (insertion order 보존) — backend
 * normalize 와 동치 (oracle T38 C2). 본 정규화 후 길이로 검증해야 frontend·
 * backend 가 같은 판정 기준.
 */
function normalizeCodes(
  raw: ReadonlyArray<string>,
): ReadonlyArray<string> {
  const seen = new Map<string, null>();
  for (const code of raw) {
    seen.set(code.padStart(6, "0"), null);
  }
  return Array.from(seen.keys());
}

/**
 * 클라이언트 측 사전 검증 — backend 의 400 응답 회피 (UX 신호 우선).
 * 형식 검증은 raw codes 에 적용 (zero-pad 전), 개수 검증은 정규화 후.
 */
function validateCodes(raw: ReadonlyArray<string>): string | null {
  for (const code of raw) {
    if (!/^\d{1,6}$/.test(code)) {
      return `잘못된 종목코드: "${code}" — 1~6 자리 숫자만 허용됩니다.`;
    }
  }
  const normalized = normalizeCodes(raw);
  if (normalized.length < MIN_CODES) {
    const wasDeduped = raw.length > normalized.length;
    const suffix = wasDeduped
      ? " (6 자리 zero-pad 정규화 후 중복)"
      : "";
    return `최소 ${MIN_CODES} 개의 서로 다른 종목이 필요합니다${suffix}.`;
  }
  if (normalized.length > MAX_CODES) {
    return `최대 ${MAX_CODES} 개까지 비교 가능합니다.`;
  }
  return null;
}

export default function ComparePage(): JSX.Element {
  const asOf = useAsOfStore((s) => s.asOf);
  // 입력 = 사용자가 편집 중인 텍스트. 확정 = 실 query 가 실행된 codes.
  const [codesText, setCodesText] = useState<string>("");
  const [submittedText, setSubmittedText] = useState<string>("");
  const [submittedCodes, setSubmittedCodes] = useState<ReadonlyArray<string>>(
    [],
  );

  const validationError = useMemo<string | null>(
    () => validateCodes(parseRawCodes(codesText)),
    [codesText],
  );

  // submittedCodes 가 빈 배열이면 query 비활성. 사용자가 "비교 실행" 클릭
  // 후에만 backend call.
  const query = useQuery<StockCompare>({
    queryKey: ["compare", submittedCodes, asOf],
    queryFn: ({ signal }) =>
      fetchStockCompare(submittedCodes, asOf, signal),
    enabled: submittedCodes.length >= MIN_CODES,
  });

  const canSubmit = validationError === null && !query.isFetching;
  // 입력이 마지막 실행 시점과 다르면 stale — 결과를 dim 처리 (oracle T38 M3).
  const isStale = submittedText !== "" && codesText !== submittedText;

  function handleSubmit(): void {
    if (validationError !== null) {
      return;
    }
    setSubmittedText(codesText);
    setSubmittedCodes(normalizeCodes(parseRawCodes(codesText)));
  }

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <h1 className="text-xl font-semibold text-neutral-900">Compare</h1>
      <p className="mt-1 text-sm text-neutral-600">
        2~6 종목의 지표를 기준 일자 ({asOf}) 로 나란히 비교합니다.
      </p>

      <section className="mt-6 space-y-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
        <label
          htmlFor="compare-codes"
          className="block text-sm font-medium text-neutral-700"
        >
          종목코드 (쉼표 구분, 2~6 개)
        </label>
        <input
          id="compare-codes"
          type="text"
          value={codesText}
          onChange={(e) => setCodesText(e.target.value)}
          placeholder="005930, 000660, 035420"
          className="w-full rounded border border-neutral-300 px-3 py-1.5 font-mono text-sm"
        />
        {validationError !== null && codesText.length > 0 ? (
          <p className="text-xs text-amber-700">{validationError}</p>
        ) : (
          <p className="text-xs text-neutral-500">
            예: <span className="font-mono">005930, 000660, 035420</span>
          </p>
        )}
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!canSubmit}
          className="inline-flex items-center rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-neutral-300 hover:bg-neutral-800"
        >
          {query.isFetching ? "불러오는 중..." : "비교 실행"}
        </button>
      </section>

      <section
        className={`mt-6 ${isStale ? "opacity-50" : ""}`}
        aria-busy={query.isFetching}
      >
        {isStale ? (
          <p className="mb-2 text-xs text-amber-700">
            입력이 변경되었습니다. 새 코드로 다시 "비교 실행" 을 누르세요.
          </p>
        ) : null}

        {query.isError ? (
          <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900">
            데이터 로드 실패: {(query.error as Error).message}
          </div>
        ) : null}

        {query.data ? (
          <>
            {query.data.not_found.length > 0 ? (
              <div
                className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
                role="status"
              >
                다음 종목코드는 lineage 가 없습니다 (오타 또는 미존재):{" "}
                <span className="font-mono">
                  {query.data.not_found.join(", ")}
                </span>
              </div>
            ) : null}
            {/* 모든 입력 코드가 lineage 부재인 경우 — banner 만 표시, grid 의
                "표시할 종목이 없습니다" 와 중복 회피 (oracle T38 M2). */}
            {query.data.items.length === 0 ? (
              <p className="text-sm text-neutral-500">
                입력하신 {submittedCodes.length} 개 코드 중 표시 가능한 종목이
                없습니다.
              </p>
            ) : (
              <CompareGrid stocks={query.data.items} asOf={asOf} />
            )}
          </>
        ) : (
          !query.isFetching && (
            <p className="text-sm text-neutral-500">
              종목코드를 입력하고 "비교 실행" 을 누르세요.
            </p>
          )
        )}
      </section>
    </main>
  );
}
