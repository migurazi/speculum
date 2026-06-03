"use client";

/**
 * SaveRunButton — Screener 결과를 Screen Run snapshot 으로 immutable 저장.
 *
 * ADR-0008 D7 + ADR-0002 D4 의 implementation. Screener 결과가 있을 때만
 * 활성. 사용자가 본 버튼 누르면 같은 conditions/selected_factors/asOf 로
 * backend `/api/runs` POST → 결과 snapshot 표시 (id + computed_at).
 *
 * 본 component 의 책임 한정:
 *   - useMutation 호출 + 성공/실패 inline 알림.
 *   - canSave 결정 = 호출자 (Screener page) 가 결과 보유 여부 판단.
 *
 * 본 component 미포함 (별도 cycle / page):
 *   - Recent Runs list — `/runs` 페이지.
 *   - Run 재실행 / 비교 (data_versions diff) — Recent Runs row click.
 *
 * 관련:
 * - ADR-0008 D7 / D7-bis (Run snapshot freeze)
 * - ADR-0002 D4 (append-only)
 * - M0_PLAN T40 / §2.10
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";

import { saveRun, type ScreenRunSnapshot } from "@/lib/api/runs";
import type { ScreenCondition, SecurityType } from "@/lib/api/screen";

interface SaveRunButtonProps {
  readonly conditions: ReadonlyArray<ScreenCondition>;
  readonly selectedFactors: ReadonlyArray<string>;
  /**
   * 자산군 선택 집합. 미지정 시 saveRun 이 security_types 를 생략하고 backend 가
   * `["common"]` 으로 해소. ADR-0023 D7 — query 일부 → result_hash 자동 freeze.
   */
  readonly securityTypes?: ReadonlyArray<SecurityType>;
  readonly asOf: string;
  /**
   * 호출자가 결과 보유 여부 결정 — 결과 없으면 본 버튼 비활성. screener page
   * 의 mutation.data 가 있을 때만 true. backend 가 실 평가하므로 이론적으로는
   * 결과 없이도 호출 가능하나 UX 일관성 위해 차단.
   */
  readonly canSave: boolean;
  /** 저장 성공 후 콜백 — 호출자가 toast / 목록 갱신 등 수행. */
  readonly onSaved?: (snapshot: ScreenRunSnapshot) => void;
}

export function SaveRunButton({
  conditions,
  selectedFactors,
  securityTypes,
  asOf,
  canSave,
  onSaved,
}: SaveRunButtonProps): JSX.Element {
  const t = useTranslations("common");
  const qc = useQueryClient();
  const mutation = useMutation<ScreenRunSnapshot, Error>({
    mutationFn: () =>
      saveRun(
        {
          conditions,
          selected_factors: selectedFactors,
          security_types: securityTypes,
        },
        asOf,
      ),
    onSuccess: (snapshot) => {
      // Recent Runs 페이지 cache invalidate — 사용자가 저장 후 /runs 로
      // navigate 시 최신 목록 보장 (oracle T40 L1 follow-up).
      void qc.invalidateQueries({ queryKey: ["runs", "recent"] });
      if (onSaved !== undefined) {
        onSaved(snapshot);
      }
    },
  });

  const disabled =
    !canSave
    || conditions.length === 0
    || selectedFactors.length === 0
    || mutation.isPending;

  function handleClick(): void {
    // 직전 success/error notice 제거 — 새 시도 결과만 노출 (oracle T40 M1/M2).
    mutation.reset();
    mutation.mutate();
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled}
        className="inline-flex items-center rounded-md border border-neutral-900 bg-white px-4 py-2 text-sm font-medium text-neutral-900 disabled:cursor-not-allowed disabled:border-neutral-300 disabled:text-neutral-300 hover:bg-neutral-100"
      >
        {mutation.isPending ? t("saveRunButton.savingLabel") : t("saveRunButton.buttonLabel")}
      </button>

      {/* 성공 + error 동시 표시 차단 (oracle M2) — isSuccess 단독일 때만 notice. */}
      {mutation.isSuccess && mutation.data ? (
        <div
          role="status"
          className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs text-emerald-900"
        >
          <div>
            {t("saveRunButton.successTitle")}{" "}
            <span className="font-mono">{mutation.data.id}</span>
          </div>
          <div className="mt-0.5 text-[11px] text-emerald-800">
            {t("saveRunButton.successDetail", {
              count: mutation.data.result_codes.length,
              versionCount: Object.keys(mutation.data.data_versions).length,
            })}
          </div>
        </div>
      ) : null}

      {mutation.isError ? (
        <p
          role="alert"
          className="text-xs text-red-700"
        >
          {t("saveRunButton.errorPrefix", { message: mutation.error.message })}
        </p>
      ) : null}
    </div>
  );
}
