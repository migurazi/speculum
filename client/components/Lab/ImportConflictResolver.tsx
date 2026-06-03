"use client";

/**
 * ImportConflictResolver — import-check 응답의 충돌 리스트 표시 + 해소 UI.
 *
 * 각 충돌에 대해 allowed_resolutions(backend 결정)에 따라 skip/rename/replace
 * 옵션을 제공한다. canonical tier 충돌(existingTier=="speculum-builtin")은 backend
 * 가 allowed_resolutions 에서 replace 를 제외해 반환하므로 UI 는 그대로 따른다
 * (D8.2 — CanonicalOverrideForbidden 구조적 보장).
 *
 * 미해결 충돌(action 미선택)이 하나라도 있으면 "적용" 버튼 disabled (D8.4).
 * rename 선택 시 new_canonical_id 입력창 노출.
 *
 * 시각 정책:
 *   - tier 배지는 grayscale(neutral) 계열만 — 등락색·추천 어휘 0 (ADR-0022 D4).
 *   - 충돌/해소 라벨: "충돌"/"건너뛰기"/"이름 변경"/"덮어쓰기"/"적용".
 *     "추천/인기" 류 금지.
 *
 * 관련: ADR-0022 D8 / T75.
 */

import { useTranslations } from "next-intl";

import type {
  ConflictResolution,
  ImportConflict,
  ResolutionAction,
} from "@/lib/api/factor-packs";

/**
 * tier 배지 색 — grayscale(neutral) 계열만.
 * 시각 게이트(ImportConflictResolver.test.tsx)가 이 상수를 검증한다.
 */
export const TIER_BADGE_CLASS = "bg-neutral-100 text-neutral-600 border-neutral-300";

interface ImportConflictResolverProps {
  readonly conflicts: ReadonlyArray<ImportConflict>;
  /** 현재 해소 상태 맵(canonical_id → resolution). */
  readonly resolutions: Readonly<Record<string, ConflictResolution>>;
  /** 해소 변경 콜백 — 한 충돌의 action/newCanonicalId 변경 시. */
  readonly onResolutionChange: (
    canonicalId: string,
    resolution: ConflictResolution,
  ) => void;
  /** "적용" 버튼 클릭 — 모든 충돌이 해소된 경우에만 활성. */
  readonly onApply: () => void;
  /** 적용 진행 중 */
  readonly isApplying?: boolean;
}

/**
 * 충돌이 해소됐는지 판정.
 * rename 은 new_canonical_id 가 비어 있지 않아야 해소됨.
 */
function isResolved(
  conflict: ImportConflict,
  resolution: ConflictResolution | undefined,
): boolean {
  if (resolution === undefined) return false;
  if (resolution.action === "rename") {
    return (
      typeof resolution.newCanonicalId === "string" &&
      resolution.newCanonicalId.trim().length > 0
    );
  }
  return true;
}

export function ImportConflictResolver({
  conflicts,
  resolutions,
  onResolutionChange,
  onApply,
  isApplying = false,
}: ImportConflictResolverProps): JSX.Element {
  const t = useTranslations("lab");

  const allResolved = conflicts.every((c) =>
    isResolved(c, resolutions[c.canonicalId]),
  );

  return (
    <section aria-labelledby="conflict-resolver-heading" className="space-y-3">
      <h3
        id="conflict-resolver-heading"
        className="text-sm font-semibold text-neutral-900"
      >
        {t("io.conflictHeading", { count: conflicts.length })}
      </h3>
      <p className="text-xs text-neutral-500">
        {t("io.conflictDescription")}
      </p>

      <ul className="space-y-3">
        {conflicts.map((conflict) => {
          const resolution = resolutions[conflict.canonicalId];
          const resolved = isResolved(conflict, resolution);

          return (
            <li
              key={conflict.canonicalId}
              className="rounded-lg border border-neutral-200 bg-white p-4"
            >
              {/* canonical_id + tier 배지 */}
              <div className="mb-3 flex items-center gap-2 flex-wrap">
                <span className="font-mono text-xs font-medium text-neutral-800">
                  {conflict.canonicalId}
                </span>
                <span
                  className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-medium ${TIER_BADGE_CLASS}`}
                  aria-label={t("io.conflictExistingTierAriaLabel")}
                >
                  {conflict.existingTier}
                </span>
                {resolved ? (
                  <span className="ml-auto text-[10px] font-medium text-neutral-400">
                    {t("io.conflictResolved")}
                  </span>
                ) : (
                  <span className="ml-auto text-[10px] font-medium text-neutral-400">
                    {t("io.conflictUnresolved")}
                  </span>
                )}
              </div>

              {/* 해시 대조 — 간략 표시 */}
              <div className="mb-3 grid grid-cols-2 gap-2 text-[10px] text-neutral-500">
                <div>
                  <span className="block font-medium text-neutral-600">
                    {t("io.conflictIncomingHash")}
                  </span>
                  <span className="font-mono">{conflict.incomingHash.slice(0, 12)}…</span>
                </div>
                <div>
                  <span className="block font-medium text-neutral-600">
                    {t("io.conflictExistingHash")}
                  </span>
                  <span className="font-mono">{conflict.existingHash.slice(0, 12)}…</span>
                </div>
              </div>

              {/* 해소 선택 — allowed_resolutions 에 있는 것만 */}
              <fieldset>
                <legend className="mb-2 text-xs font-medium text-neutral-700">
                  {t("io.conflictChooseAction")}
                </legend>
                <div className="flex flex-wrap gap-2">
                  {conflict.allowedResolutions.map((action) => (
                    <label
                      key={action}
                      className="flex cursor-pointer items-center gap-1.5 text-xs"
                    >
                      <input
                        type="radio"
                        name={`conflict-action-${conflict.canonicalId}`}
                        value={action}
                        checked={resolution?.action === action}
                        onChange={() => {
                          onResolutionChange(conflict.canonicalId, {
                            action,
                            newCanonicalId: undefined,
                          });
                        }}
                        className="accent-neutral-700"
                        aria-label={t(`io.conflictAction${capitalize(action)}`)}
                      />
                      <span className="text-neutral-700">
                        {t(`io.conflictAction${capitalize(action)}`)}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>

              {/* rename 선택 시 new_canonical_id 입력 */}
              {resolution?.action === "rename" && (
                <div className="mt-3">
                  <label className="block text-xs font-medium text-neutral-700 mb-1">
                    {t("io.conflictNewCanonicalId")}
                  </label>
                  <input
                    type="text"
                    value={resolution.newCanonicalId ?? ""}
                    onChange={(e) => {
                      onResolutionChange(conflict.canonicalId, {
                        action: "rename",
                        newCanonicalId: e.target.value,
                      });
                    }}
                    placeholder={t("io.conflictNewCanonicalIdPlaceholder")}
                    aria-label={t("io.conflictNewCanonicalIdAriaLabel")}
                    className="w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs text-neutral-800 placeholder:text-neutral-400 focus:border-neutral-500 focus:outline-none"
                  />
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {/* 적용 버튼 — 미해결 충돌 존재 시 disabled (D8.4). */}
      <button
        type="button"
        disabled={!allResolved || isApplying}
        onClick={onApply}
        aria-disabled={!allResolved || isApplying}
        className="rounded-md bg-neutral-900 px-4 py-2 text-xs font-medium text-white hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isApplying ? t("io.conflictApplying") : t("io.conflictApply")}
      </button>
    </section>
  );
}

/** 첫 글자 대문자화 — 번역 키 조합용 내부 헬퍼. */
function capitalize(s: string): string {
  return s.length === 0 ? s : s[0]!.toUpperCase() + s.slice(1);
}
