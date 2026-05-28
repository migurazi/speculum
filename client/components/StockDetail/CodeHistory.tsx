/**
 * CodeHistory — 종목 lineage 의 종목코드 변경 history.
 *
 * ADR-0009 D6 의 lineage 단위 entity — 종목코드 변경·재상장·합병 시 새 row
 * 가 아닌 history append. 본 component 가 표 형태로 표시.
 *
 * 단일 entry (대부분의 종목) 인 경우 contracted view — 사용자가 펼침 X.
 * lineage 가 2+ entry 인 경우만 default 펼침.
 *
 * 관련 ADR / 문서:
 * - ADR-0009 D6 (lineage code_history JSONB).
 * - M0_PLAN T37.
 */

import type { CodeHistoryItem } from "@/lib/api/stocks";
import { cn } from "@/lib/utils";

interface CodeHistoryProps {
  readonly history: ReadonlyArray<CodeHistoryItem>;
  readonly className?: string;
}

const REASON_KO: Readonly<Record<string, string>> = {
  initial_listing: "최초 상장",
  code_change: "종목코드 변경",
  merger_temporary: "합병 임시 코드",
  re_listing: "재상장",
};

export function CodeHistory({
  history,
  className,
}: CodeHistoryProps): JSX.Element | null {
  // 단일 entry 의 lineage 는 의미 적음 — 미렌더.
  if (history.length <= 1) {
    return null;
  }

  return (
    <section className={cn("rounded-lg border border-neutral-200 bg-white p-4", className)}>
      <h2 className="text-sm font-medium text-neutral-700">
        종목코드 변경 history
      </h2>
      <table className="mt-3 w-full text-xs">
        <thead className="text-neutral-500">
          <tr>
            <th className="text-left font-medium">코드</th>
            <th className="text-left font-medium">사유</th>
            <th className="text-left font-medium">시작</th>
            <th className="text-left font-medium">종료</th>
          </tr>
        </thead>
        <tbody>
          {history.map((entry, idx) => (
            <tr
              key={`${entry.code}-${entry.valid_from}-${idx}`}
              className="border-t border-neutral-100"
            >
              <td className="py-1.5 font-mono text-neutral-900">
                {entry.code}
              </td>
              <td className="py-1.5 text-neutral-700">
                {REASON_KO[entry.reason] ?? entry.reason}
              </td>
              <td className="py-1.5 font-mono text-neutral-700">
                {entry.valid_from}
              </td>
              <td className="py-1.5 font-mono text-neutral-700">
                {entry.valid_to ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
