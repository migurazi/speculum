"use client";

/**
 * Recent Runs page — `/runs`.
 *
 * 사용자가 Save Run 으로 저장한 snapshot 목록. ADR-0008 D7 의 freeze 의
 * 사용자 가시성 — "내가 저장한 결과를 다시 볼 수 있다".
 *
 * M0 scope:
 *   - 최근 20 개 표시 (backend default).
 *   - 각 Run 의 versions diff badge — 재현 가능 여부.
 *
 * M1+ (별도 cycle):
 *   - Run 재실행 + 결과 비교 (현재 active vs snapshot).
 *   - 페이지네이션 (limit 200 까지).
 *   - Run 의 conditions / selected_factors / result_codes 상세 보기.
 *
 * 관련:
 * - ADR-0008 D7 (Run snapshot freeze)
 * - M0_PLAN T40 backlog
 */

import { useTranslations } from "next-intl";
import { useQuery } from "@tanstack/react-query";

import { RunList } from "@/components/Runs/RunList";
import { ReproduceImport } from "@/components/Runs/ReproduceImport";
import {
  listRecentRuns,
  type ScreenRunList,
} from "@/lib/api/runs";

export default function RecentRunsPage(): JSX.Element {
  // 클라이언트 컴포넌트 — useTranslations 사용.
  const t = useTranslations("runs");

  const query = useQuery<ScreenRunList>({
    queryKey: ["runs", "recent"],
    queryFn: ({ signal }) => listRecentRuns(undefined, signal),
  });

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <header>
        <h1 className="text-xl font-semibold text-neutral-900">{t("title")}</h1>
        <p className="mt-1 text-sm text-neutral-600">
          {t("description")}
        </p>
      </header>

      <section className="mt-6">
        {query.isLoading ? (
          <p className="text-sm text-neutral-500">{t("loading")}</p>
        ) : null}

        {query.isError ? (
          <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900">
            {t("loadError", { message: (query.error as Error).message })}
          </div>
        ) : null}

        {query.data ? (
          <>
            <p className="mb-2 text-xs text-neutral-500">
              {t("totalCount", { total: query.data.total })}
            </p>
            <RunList runs={query.data.items} />
          </>
        ) : null}
      </section>

      {/* 재현 검증 섹션 — user 비종속: 공유 JSON 누구든 업로드 가능. */}
      <ReproduceImport />
    </main>
  );
}
