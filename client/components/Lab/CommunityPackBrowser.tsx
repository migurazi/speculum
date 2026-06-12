"use client";

/**
 * CommunityPackBrowser — USER_SHARED 공개 pack 목록 탐색 + import UI (ADR-0028 D3/D4).
 *
 * 흐름:
 *   목록: GET /api/factor-packs/community (인증 불요) → 공개 pack 목록 표시.
 *   import: 항목의 "가져오기" 클릭 → GET full body → importCheckPack → (충돌 있으면
 *     ImportConflictResolver) → importPack → onImport(cleanPack) 으로 editor 적재.
 *   복제: import 된 pack 은 importer 의 새 custom pack(private)으로 복제 —
 *     PackIO.runPass1/Pass2 와 동일 흐름 재사용.
 *
 * 가드레일 (ADR-0028 D3, §2.3 큐레이션 금지):
 *   - 인기/순위/다운로드 수/별점/Top-N/추천 배지 0.
 *   - 정렬 토글 0 — backend created_at 역순 그대로.
 *   - 자동 강조/색 분기 0.
 *   - 모든 톤: COMMUNITY_BROWSER_* 명명 상수(grayscale/neutral 계열).
 *   - content_hash 재검증 실패 시 fail-loud(ADR-0028 D4).
 *
 * 시각 정책:
 *   pack_slug·version·factor 수·생성일·name/description(있을 경우) 사실만 표시.
 *   name/description 은 공유자 입력값(EXTERNAL/USER_SHARED) — 판단어 이미 backend
 *   게이트 통과한 사실. 추천 의미 부여 없이 그대로 표시.
 *
 * 관련: PackIO.tsx (runPass1/Pass2 동형), factor-packs.ts community API.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { ImportConflictResolver } from "@/components/Lab/ImportConflictResolver";
import { ApiError } from "@/lib/api/client";
import type {
  CommunityPack,
  ConflictResolution,
  FactorPack,
  ImportCheckResult,
} from "@/lib/api/factor-packs";
import {
  getCommunityPackBody,
  importCheckPack,
  importPack,
  listCommunityPacks,
} from "@/lib/api/factor-packs";

// ── No Advice 시각 상수 (community-pack-gate.test.tsx 가 이 상수를 검증) ──────

/**
 * 목록 행 기본 톤 — neutral. 판단색/등락색 0.
 * community-pack-gate.test.tsx 가 이 상수를 검증한다(ADR-0028 D3).
 */
export const COMMUNITY_BROWSER_ROW_CLASS =
  "border-b border-neutral-100 last:border-0 hover:bg-neutral-50";

/**
 * import 버튼 톤 — neutral. 추천 의미색 0.
 */
export const COMMUNITY_BROWSER_IMPORT_BTN_CLASS =
  "rounded border border-neutral-300 px-2 py-0.5 text-neutral-700 hover:bg-neutral-50 disabled:opacity-50";

/**
 * visibility/공유 배지 톤 — neutral grayscale. 판단색(red/green) 0.
 * community-pack-gate.test.tsx 가 이 상수를 검증한다(ADR-0028 D3).
 */
export const COMMUNITY_BROWSER_BADGE_CLASS =
  "inline-block rounded border border-neutral-200 bg-neutral-100 px-1.5 py-0.5 text-[10px] font-medium text-neutral-500";

// ── query key ─────────────────────────────────────────────────────────────────

const COMMUNITY_PACKS_QUERY_KEY = ["factor-packs", "community"] as const;

// ── import 단계 (PackIO 와 동형) ──────────────────────────────────────────────

type ImportStage =
  | "idle"
  | "fetching"
  | "checking"
  | "conflicts"
  | "applying"
  | "done"
  | "error";

interface ImportState {
  readonly packSlug: string;
  readonly version: string;
  readonly stage: ImportStage;
  readonly checkResult: ImportCheckResult | null;
  readonly resolutions: Record<string, ConflictResolution>;
  readonly error: string | null;
  /** fetch 된 full pack body (Pass2 에서 재사용). */
  readonly rawBody: unknown;
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface CommunityPackBrowserProps {
  /**
   * import 완료 콜백 — PackIO.onImport / PackLibrary.onLoad 와 동일 시그니처.
   * 호출자(LabPage)가 setPack(pack) 으로 editor 상태를 교체한다.
   */
  readonly onImport: (pack: FactorPack) => void;
}

// ── 메인 컴포넌트 ──────────────────────────────────────────────────────────────

export function CommunityPackBrowser({
  onImport,
}: CommunityPackBrowserProps): JSX.Element {
  const t = useTranslations("lab");

  // 목록 쿼리
  const listQuery = useQuery({
    queryKey: COMMUNITY_PACKS_QUERY_KEY,
    queryFn: ({ signal }) => listCommunityPacks(signal),
  });

  // 현재 import 중인 pack 상태 (한 번에 하나).
  const [importState, setImportState] = useState<ImportState | null>(null);

  const packs = listQuery.data?.packs ?? [];

  // ── Pass1: full body fetch → import-check ─────────────────────────────────

  const runPass1 = async (pack: CommunityPack): Promise<void> => {
    // 이미 같은 pack 의 conflicts 단계면 닫기(토글).
    if (
      importState?.packSlug === pack.packSlug &&
      importState.version === pack.version &&
      importState.stage === "conflicts"
    ) {
      setImportState(null);
      return;
    }

    setImportState({
      packSlug: pack.packSlug,
      version: pack.version,
      stage: "fetching",
      checkResult: null,
      resolutions: {},
      error: null,
      rawBody: null,
    });

    try {
      // full body 취득 (content_hash 봉인 포함).
      const bodyResult = await getCommunityPackBody(pack.packSlug, pack.version);

      // content_hash 재검증 — 응답 hash 가 목록 hash 와 불일치 시 fail-loud(ADR-0028 D4).
      if (
        bodyResult.contentHash &&
        pack.contentHash &&
        bodyResult.contentHash !== pack.contentHash
      ) {
        setImportState((prev) =>
          prev
            ? {
                ...prev,
                stage: "error",
                error: t("community.hashMismatchError"),
              }
            : null,
        );
        return;
      }

      const rawBody = bodyResult.pack;

      // import-check dry-run.
      setImportState((prev) =>
        prev ? { ...prev, stage: "checking", rawBody } : null,
      );

      const check = await importCheckPack(rawBody);

      if (!check.valid) {
        const msg =
          check.issues.length > 0
            ? check.issues.map((i) => `[${i.stage}] ${i.message}`).join("\n")
            : t("io.importErrorParse");
        setImportState((prev) =>
          prev
            ? { ...prev, stage: "error", error: msg, checkResult: check }
            : null,
        );
        return;
      }

      if (check.conflicts.length === 0) {
        // 충돌 없음 — Pass2 자동 진행.
        await runPass2(rawBody, {}, pack.packSlug, pack.version);
      } else {
        // 충돌 있음 — 사용자 해소 대기.
        setImportState((prev) =>
          prev
            ? {
                ...prev,
                stage: "conflicts",
                checkResult: check,
                resolutions: {},
              }
            : null,
        );
      }
    } catch (err) {
      setImportState((prev) =>
        prev
          ? {
              ...prev,
              stage: "error",
              error: (err as Error).message,
            }
          : null,
      );
    }
  };

  // ── Pass2: 충돌 해소 후 실제 import ──────────────────────────────────────

  const runPass2 = async (
    rawBody: unknown,
    resolvedMap: Readonly<Record<string, ConflictResolution>>,
    packSlug: string,
    version: string,
  ): Promise<void> => {
    setImportState((prev) =>
      prev ? { ...prev, stage: "applying" } : null,
    );
    try {
      const result = await importPack(rawBody, resolvedMap);
      if (!result.valid || result.pack === null) {
        const msg =
          result.issues.length > 0
            ? result.issues.map((i) => `[${i.stage}] ${i.message}`).join("\n")
            : t("io.importErrorParse");
        setImportState((prev) =>
          prev ? { ...prev, stage: "error", error: msg } : null,
        );
        return;
      }
      // content_hash 등 backend 부가 필드 제외한 FactorPack 추출.
      const { content_hash: _unused, ...cleanPack } = result.pack as FactorPack & {
        content_hash?: string;
      };
      void _unused;
      onImport(cleanPack as FactorPack);
      setImportState({
        packSlug,
        version,
        stage: "done",
        checkResult: null,
        resolutions: {},
        error: null,
        rawBody: null,
      });
    } catch (err) {
      setImportState((prev) =>
        prev
          ? {
              ...prev,
              stage: "error",
              error: (err as Error).message,
            }
          : null,
      );
    }
  };

  // ── conflicts 해소 콜백 ───────────────────────────────────────────────────

  const onResolutionChange = (
    canonicalId: string,
    resolution: ConflictResolution,
  ): void => {
    setImportState((prev) =>
      prev
        ? {
            ...prev,
            resolutions: { ...prev.resolutions, [canonicalId]: resolution },
          }
        : null,
    );
  };

  const onApplyConflicts = (): void => {
    if (importState === null || importState.rawBody === null) return;
    void runPass2(
      importState.rawBody,
      importState.resolutions,
      importState.packSlug,
      importState.version,
    );
  };

  // ── 렌더 ─────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-3">
      <p className="text-xs text-neutral-500">
        {t("community.description")}
      </p>

      {listQuery.isPending ? (
        <p className="text-xs text-neutral-500">{t("community.loading")}</p>
      ) : listQuery.isError ? (
        <p className="text-xs text-neutral-700">
          {t("community.listError", {
            message: (listQuery.error as Error).message,
          })}
        </p>
      ) : packs.length === 0 ? (
        <p
          data-testid="community-pack-empty"
          className="text-xs text-neutral-500"
        >
          {t("community.listEmpty")}
        </p>
      ) : (
        <div
          className="overflow-x-auto rounded-md border border-neutral-200"
          data-testid="community-pack-list"
        >
          <table className="w-full table-fixed text-xs">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <th className="px-3 py-2 text-left font-medium text-neutral-600 w-2/5">
                  {t("community.colSlug")}
                </th>
                <th className="px-3 py-2 text-left font-medium text-neutral-600 w-[12%]">
                  {t("community.colVersion")}
                </th>
                <th className="px-3 py-2 text-right font-medium text-neutral-600 w-[10%]">
                  {t("community.colFactorCount")}
                </th>
                <th className="px-3 py-2 text-left font-medium text-neutral-600 w-1/4">
                  {t("community.colCreatedAt")}
                </th>
                <th className="px-3 py-2 text-right font-medium text-neutral-600 w-auto">
                  {""}
                </th>
              </tr>
            </thead>
            <tbody>
              {packs.map((item) => {
                const isThisPack =
                  importState?.packSlug === item.packSlug &&
                  importState.version === item.version;
                const thisPackStage = isThisPack ? importState!.stage : "idle";
                const isBusy =
                  thisPackStage === "fetching" ||
                  thisPackStage === "checking" ||
                  thisPackStage === "applying";

                return (
                  <CommunityPackRow
                    key={`${item.packSlug}@${item.version}`}
                    item={item}
                    isBusy={isBusy}
                    isDone={isThisPack && thisPackStage === "done"}
                    importLabel={t("community.importButton")}
                    importingLabel={t("community.importing")}
                    onImportClick={() => { void runPass1(item); }}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* import 진행 중 에러 */}
      {importState?.stage === "error" && importState.error !== null && (
        <div
          data-testid="community-import-error"
          className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-700 whitespace-pre-wrap"
        >
          {importState.error}
        </div>
      )}

      {/* import 완료 */}
      {importState?.stage === "done" && (
        <p
          data-testid="community-import-done"
          className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-700"
        >
          {t("community.importDone")}
        </p>
      )}

      {/* 충돌 해소 패널 — conflicts 단계에만 표시. */}
      {importState?.stage === "conflicts" &&
        importState.checkResult !== null && (
          <ImportConflictResolver
            conflicts={importState.checkResult.conflicts}
            resolutions={importState.resolutions}
            onResolutionChange={onResolutionChange}
            onApply={onApplyConflicts}
            isApplying={false}
          />
        )}
    </div>
  );
}

// ── Row 서브컴포넌트 ──────────────────────────────────────────────────────────

interface CommunityPackRowProps {
  readonly item: CommunityPack;
  readonly isBusy: boolean;
  readonly isDone: boolean;
  readonly importLabel: string;
  readonly importingLabel: string;
  readonly onImportClick: () => void;
}

function CommunityPackRow({
  item,
  isBusy,
  isDone,
  importLabel,
  importingLabel,
  onImportClick,
}: CommunityPackRowProps): JSX.Element {
  /** created_at ISO → 로컬 날짜(사실 표시, 해석 없음). */
  const formattedDate = (() => {
    try {
      return new Date(item.createdAt).toLocaleString("ko-KR", {
        dateStyle: "short",
        timeStyle: "short",
      });
    } catch {
      return item.createdAt;
    }
  })();

  return (
    <tr
      className={COMMUNITY_BROWSER_ROW_CLASS}
      data-testid={`community-pack-row-${item.packSlug}`}
    >
      <td className="px-3 py-2">
        <div className="space-y-0.5">
          <span className="block font-mono text-neutral-800 truncate">
            {item.packSlug}
          </span>
          {/* name/description — 사실 표시. 추천 의미 부여 없음. */}
          {item.name !== null && item.name.length > 0 && (
            <span className="block text-[11px] text-neutral-600 truncate">
              {item.name}
            </span>
          )}
          {item.description !== null && item.description.length > 0 && (
            <span className="block text-[10px] text-neutral-500 truncate">
              {item.description}
            </span>
          )}
        </div>
      </td>
      <td className="px-3 py-2 font-mono text-neutral-700">{item.version}</td>
      <td className="px-3 py-2 text-right text-neutral-700">{item.factorCount}</td>
      <td className="px-3 py-2 text-neutral-600">{formattedDate}</td>
      <td className="px-3 py-2 text-right">
        {isDone ? (
          <span
            className={COMMUNITY_BROWSER_BADGE_CLASS}
            data-testid={`community-import-done-badge-${item.packSlug}`}
          >
            가져옴
          </span>
        ) : (
          <button
            type="button"
            onClick={onImportClick}
            disabled={isBusy}
            data-testid={`community-import-${item.packSlug}`}
            className={COMMUNITY_BROWSER_IMPORT_BTN_CLASS}
          >
            {isBusy ? importingLabel : importLabel}
          </button>
        )}
      </td>
    </tr>
  );
}
