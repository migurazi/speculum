"use client";

/**
 * RunList — Recent Runs 페이지의 Run row list.
 *
 * 표 형태로 표시:
 *   computed_at | as_of | conditions count | result count | versions diff
 *
 * Run row 는 backend `/api/runs` GET 응답의 `items` 에서 직접 반환받은
 * snapshot 구조. UI 가 selected_factors / data_versions / result_codes 의
 * 일부만 inline 표시.
 *
 * 관련:
 * - ADR-0008 D7 (Run snapshot freeze)
 * - M0_PLAN T40 backlog (Recent Runs 페이지)
 */

import type { ScreenRunSnapshot } from "@/lib/api/runs";

import { VersionsDiffBadge } from "./VersionsDiffBadge";

interface RunListProps {
  readonly runs: ReadonlyArray<ScreenRunSnapshot>;
}

/** ISO datetime → KST 표시. 운영은 UTC 저장, UI 는 KST. */
function formatComputedAt(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    // YYYY-MM-DD HH:mm — 초 단위 무시.
    const fmt = new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    return fmt.format(d).replace(/\./g, "-").replace(/-(\s)/g, "$1").trim();
  } catch {
    return iso;
  }
}

export function RunList({ runs }: RunListProps): JSX.Element {
  if (runs.length === 0) {
    return (
      <p className="text-sm text-neutral-500">
        저장된 Run 이 없습니다. Screener 결과를 "Save Run" 으로 저장하세요.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-neutral-50 text-xs text-neutral-600">
          <tr>
            <th scope="col" className="px-4 py-2 text-left font-medium">
              저장 시점 (KST)
            </th>
            <th scope="col" className="px-4 py-2 text-left font-medium">
              기준일
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              조건
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              결과
            </th>
            <th scope="col" className="px-4 py-2 text-left font-medium">
              재현
            </th>
            <th scope="col" className="px-4 py-2 text-left font-medium">
              Run ID
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id} className="border-t border-neutral-100">
              <td className="px-4 py-2 text-xs text-neutral-700">
                {formatComputedAt(run.computed_at)}
              </td>
              <td className="px-4 py-2 font-mono text-xs text-neutral-700">
                {run.as_of}
              </td>
              <td className="px-4 py-2 text-right text-xs text-neutral-700">
                {run.conditions.length}
              </td>
              <td className="px-4 py-2 text-right text-xs text-neutral-700">
                {run.result_codes.length} 종목
              </td>
              <td className="px-4 py-2">
                <VersionsDiffBadge runId={run.id} />
              </td>
              <td
                className="px-4 py-2 font-mono text-[10px] text-neutral-500"
                title={run.id}
              >
                {run.id.slice(0, 8)}…
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
