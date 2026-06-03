"use client";

/**
 * ResultsTable — Screener 결과 표시. TanStack Table 기반.
 *
 * M0 MVP scope: 단일 column (code) — backend 의 result_codes 만 표시.
 * 지표 column (PER, ROE 등) 은 T37 Stock Detail 의 SourceAttribution
 * pattern 합류 시 추가 — selected_factors 별로 column 동적 생성.
 *
 * TanStack Table 의 가상화 (1만 종목 60fps — AC-F-03) 는 row 수가 작을 때
 * overkill — virtualization plugin 도입은 별도 cycle backlog.
 *
 * 관련:
 * - ADR-0007 D1 (기본 정렬 — 시가총액 default. M0 는 backend 정렬 위임).
 * - AC-F-03 (Screener 결과 1만 종목 가상화).
 */

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { cn } from "@/lib/utils";

interface ResultsTableProps {
  readonly codes: ReadonlyArray<string>;
  readonly className?: string;
}

interface CodeRow {
  readonly code: string;
}

export function ResultsTable({
  codes,
  className,
}: ResultsTableProps): JSX.Element {
  const t = useTranslations("screener");

  // TanStack Table 권장 — data 는 stable reference 여야 함(매 렌더 새 배열이면
  // 불필요한 row model 재계산/리렌더). codes 변경 시에만 재생성.
  const data = useMemo<CodeRow[]>(
    () => codes.map((code) => ({ code })),
    [codes],
  );

  // columns 는 t 에 의존하므로 컴포넌트 내부에서 생성.
  const columns = useMemo<ColumnDef<CodeRow>[]>(
    () => [
      {
        id: "code",
        accessorKey: "code",
        header: () => <span>{t("resultsTable.codeHeader")}</span>,
        cell: (info) => (
          <span className="font-mono tabular-nums">
            {String(info.getValue())}
          </span>
        ),
      },
    ],
    [t],
  );

  const table = useReactTable<CodeRow>({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  if (codes.length === 0) {
    return (
      <p className={cn("text-sm text-neutral-500", className)}>
        {t("resultsTable.noResults")}
      </p>
    );
  }

  return (
    <div className={cn("overflow-x-auto rounded-md border border-neutral-200", className)}>
      <table className="w-full text-sm">
        <thead className="bg-neutral-50">
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <th
                  key={header.id}
                  className="px-3 py-2 text-left font-medium text-neutral-700"
                >
                  {flexRender(
                    header.column.columnDef.header,
                    header.getContext(),
                  )}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              className="border-t border-neutral-100 hover:bg-neutral-50"
            >
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="px-3 py-2 text-neutral-900">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
