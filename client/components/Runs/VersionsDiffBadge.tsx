"use client";

/**
 * VersionsDiffBadge — Run snapshot 의 재현 가능 여부 표시.
 *
 * ADR-0008 D7-bis 의 의미:
 *   Run snapshot 은 data_versions (factor_pack / price_adjustment / dart_account
 *   _mapping 등) 를 freeze. 현재 시점의 active version 과 비교하여 정책 키가
 *   변경되었으면 재현 불가 (정정공시 / pack 버전업).
 *
 * 표시:
 *   - 일치 → 녹색 "재현 가능"
 *   - diff 있음 → 황색 "재현 변경: N 키" + tooltip 으로 키 list
 *
 * 호출자가 runId 만 전달. 본 component 가 backend 호출 책임.
 *
 * 관련:
 * - ADR-0008 D7-bis (Run versions diff)
 * - M0_PLAN T40 (Save Run) backlog
 */

import { useTranslations } from "next-intl";
import { useQuery } from "@tanstack/react-query";

import { fetchRunDiff, type VersionsDiff } from "@/lib/api/runs";

interface VersionsDiffBadgeProps {
  readonly runId: string;
}

export function VersionsDiffBadge({
  runId,
}: VersionsDiffBadgeProps): JSX.Element {
  // 클라이언트 컴포넌트 — useTranslations 사용.
  const t = useTranslations("runs");

  const query = useQuery<VersionsDiff>({
    queryKey: ["runs", "diff", runId],
    queryFn: ({ signal }) => fetchRunDiff(runId, signal),
  });

  if (query.isLoading) {
    return (
      <span className="text-[10px] text-neutral-400">{t("versionChecking")}</span>
    );
  }
  if (query.isError) {
    return (
      <span
        className="rounded-md border border-neutral-300 bg-neutral-50 px-1.5 py-0.5 text-[10px] text-neutral-600"
        title={(query.error as Error).message}
      >
        {t("versionCheckFailed")}
      </span>
    );
  }

  const data = query.data;
  if (!data) {
    return <span className="text-[10px] text-neutral-400">—</span>;
  }

  const diffKeys = Object.keys(data.diff);
  if (diffKeys.length === 0) {
    return (
      <span className="rounded-md border border-emerald-300 bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-800">
        {t("reproducible")}
      </span>
    );
  }

  // 변경된 키 = 재현 시 다른 결과 가능 (정책 supersede).
  const tooltipLines = diffKeys
    .map((k) => `${k}: ${data.diff[k]?.[0]} → ${data.diff[k]?.[1]}`)
    .join("\n");
  return (
    <span
      className="rounded-md border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-900"
      title={tooltipLines}
    >
      {t("reproducibleChanged", { count: diffKeys.length })}
    </span>
  );
}
