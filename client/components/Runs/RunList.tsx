"use client";

/**
 * RunList — Recent Runs 페이지의 Run row list.
 *
 * 표 형태로 표시:
 *   computed_at | as_of | conditions count | result count | versions diff | export
 *
 * Run row 는 backend `/api/runs` GET 응답의 `items` 에서 직접 반환받은
 * snapshot 구조. UI 가 selected_factors / data_versions / result_codes 의
 * 일부만 inline 표시.
 *
 * export 버튼 (M2 T80 Phase 2):
 *   - 클릭 → exportRun() → Blob 다운로드 (파일명: screen-run-{id}.json).
 *   - 자기완결 export: export_format / snapshot_schema_version 포함.
 *   - 에러 시 버튼 옆 인라인 오류 표시 (toast 없음 — 단순 텍스트).
 *
 * 관련:
 * - ADR-0008 D7 (Run snapshot freeze)
 * - M0_PLAN T40 backlog (Recent Runs 페이지)
 * - M2_PLAN T80 (export/reproduce)
 */

import { useState } from "react";
import { useTranslations } from "next-intl";

import type { ScreenRunSnapshot } from "@/lib/api/runs";
import { exportRun } from "@/lib/api/runs";

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

/**
 * ExportButton — 단일 Run 의 export 다운로드 트리거.
 *
 * exportRun() 호출 → ScreenRunExport JSON → Blob → <a download> 트리거.
 * 로딩 중 버튼 비활성, 에러 시 인라인 텍스트 표시 (중립 톤).
 */
function ExportButton({ runId }: { readonly runId: string }): JSX.Element {
  const t = useTranslations("runs");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleExport(): Promise<void> {
    setLoading(true);
    setError(null);
    try {
      const data = await exportRun(runId);
      // Blob → <a download> 방식 — window.URL.createObjectURL 사용.
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `screen-run-${runId}.json`;
      document.body.appendChild(a);
      a.click();
      // 메모리 해제 — revokeObjectURL 은 즉시 호출해도 다운로드에 영향 없음.
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-1.5">
      <button
        type="button"
        disabled={loading}
        onClick={() => { void handleExport(); }}
        className="rounded border border-neutral-300 bg-white px-2 py-0.5 text-[10px] text-neutral-700 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {loading ? t("exportLoading") : t("exportButton")}
      </button>
      {error !== null && (
        <span className="text-[10px] text-neutral-500" title={error}>
          {t("exportError")}
        </span>
      )}
    </span>
  );
}

export function RunList({ runs }: RunListProps): JSX.Element {
  // 클라이언트 컴포넌트 — useTranslations 사용.
  const t = useTranslations("runs");

  if (runs.length === 0) {
    return (
      <p className="text-sm text-neutral-500">
        {t("emptyMessage")}
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-neutral-50 text-xs text-neutral-600">
          <tr>
            <th scope="col" className="px-4 py-2 text-left font-medium">
              {t("colComputedAt")}
            </th>
            <th scope="col" className="px-4 py-2 text-left font-medium">
              {t("colAsOf")}
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              {t("colConditions")}
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              {t("colResults")}
            </th>
            <th scope="col" className="px-4 py-2 text-left font-medium">
              {t("colReproducible")}
            </th>
            <th scope="col" className="px-4 py-2 text-left font-medium">
              {t("colRunId")}
            </th>
            <th scope="col" className="px-4 py-2 text-left font-medium">
              {t("colExport")}
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
                {t("resultCount", { count: run.result_codes.length })}
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
              <td className="px-4 py-2">
                <ExportButton runId={run.id} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
