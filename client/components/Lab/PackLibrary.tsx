"use client";

/**
 * PackLibrary — user-scoped pack 저장·목록·불러오기·삭제·visibility 토글 UI.
 *
 * 흐름:
 *   저장: 현재 editor pack → POST /api/factor-packs/saved.
 *     - 409: slug+version 충돌 에러 표시.
 *     - 422: 검증 실패 에러 표시.
 *   목록: GET /api/factor-packs/saved → 저장 날짜/slug/version/factor 수 표시.
 *   불러오기: GET /api/factor-packs/saved/{id} → onLoad(pack) 으로 editor 교체.
 *   삭제: DELETE /api/factor-packs/saved/{id} → 목록 갱신.
 *   visibility 토글: PATCH /api/factor-packs/saved/{id}/visibility (ADR-0028 D2).
 *     - private↔public 전환.
 *     - public 전환 시 422(금지어휘) → 중립 에러 표시.
 *     - visibility 배지: grayscale 계열만(판단색 0).
 *   custom screen: 각 저장 pack 행에서 CustomScreenPanel 열기 (ADR-0025 D4/D6).
 *
 * 시각 정책 (No Advice / ADR-0022 D4):
 *   - 모든 텍스트·배지: neutral grayscale 계열만 — 판단색·추천 어휘 0.
 *   - visibility 배지는 grayscale(neutral) 계열만 — 판단색(red/green) 0.
 *   - 랭킹/인기/추천 라벨 절대 금지.
 *   - pack_slug·version·factor_count·created_at 사실 그대로 표시.
 *
 * 관련: PackIO.tsx (onImport 콜백 패턴 동일), factor-packs.ts savePack/listMyPacks.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { CustomScreenPanel } from "@/components/Lab/CustomScreenPanel";
import type { FactorPack, FactorDef, SavedPackOut } from "@/lib/api/factor-packs";
import {
  deleteSavedPack,
  getSavedPack,
  listMyPacks,
  savePack,
  setPackVisibility,
} from "@/lib/api/factor-packs";
import { ApiError } from "@/lib/api/client";

/**
 * visibility 배지 톤 상수 — neutral grayscale 계열만. 판단색(red/green) 0.
 * community-pack-gate.test.tsx 가 이 상수를 검증한다(ADR-0028 D3).
 */
export const VISIBILITY_BADGE_CLASS =
  "inline-block rounded border border-neutral-200 bg-neutral-100 px-1.5 py-0.5 text-[10px] font-medium text-neutral-500";

interface PackLibraryProps {
  /** 현재 editor 의 pack — 저장 버튼 클릭 시 전송. */
  readonly pack: FactorPack;
  /**
   * ADR-0032 D3 — 현재 editor pack 의 import provenance(출처 URL). URL import 로
   * 들어온 pack 만 값을 가지며 저장 시 server 에 전달돼 row 메타로 봉인된다(body
   * 미포함 → content_hash 무관). null=출처 없음(직접 작성/편집).
   */
  readonly sourceUrl?: string | null;
  /**
   * 불러오기 완료 콜백 — PackIO 의 onImport 와 동일 시그니처.
   * 호출자(LabPage)가 setPack(pack) 으로 editor 상태를 교체한다.
   */
  readonly onLoad: (pack: FactorPack) => void;
}

/** query key — 저장 목록 캐시. */
const SAVED_PACKS_QUERY_KEY = ["factor-packs", "saved"] as const;

/**
 * 저장 에러 → i18n 키 선택 헬퍼.
 *
 * ApiError status 로 분기:
 *   409 → conflict 메시지
 *   422 → invalid 메시지
 *   그 외 → generic 메시지(message 포함)
 */
function classifySaveError(
  err: Error,
  t: ReturnType<typeof useTranslations<"lab">>,
): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return t("library.saveErrorConflict");
    if (err.status === 422) return t("library.saveErrorInvalid");
    return t("library.saveErrorGeneric", { message: err.message });
  }
  return t("library.saveErrorGeneric", { message: err.message });
}

export function PackLibrary({
  pack,
  sourceUrl = null,
  onLoad,
}: PackLibraryProps): JSX.Element {
  const t = useTranslations("lab");
  const qc = useQueryClient();

  /** 저장 완료 일시 표시(성공 flash). */
  const [saveSuccessId, setSaveSuccessId] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  /** 불러오기 중인 ID (row 별 로딩 상태). */
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  /** 삭제 에러. */
  const [deleteError, setDeleteError] = useState<string | null>(null);

  /**
   * visibility 로컬 상태 맵 (pack_id → visibility).
   * PATCH 성공 시 로컬 캐시 업데이트 — GET 목록은 visibility 를 포함하지 않으므로
   * PATCH 성공 응답에서 최신 상태를 유지한다.
   */
  const [visibilityMap, setVisibilityMap] = useState<
    Record<string, "private" | "public">
  >({});

  /** visibility 토글 에러 (pack_id → 에러 메시지). */
  const [visibilityErrors, setVisibilityErrors] = useState<
    Record<string, string>
  >({});

  /**
   * custom screen 패널 열린 pack id + factors.
   * 하나의 패널만 — 다른 행 클릭 시 기존 패널 교체(단일 확장).
   */
  const [screenPanel, setScreenPanel] = useState<{
    readonly id: string;
    readonly packSlug: string;
    readonly version: string;
    readonly factors: ReadonlyArray<FactorDef>;
  } | null>(null);

  // ── 목록 쿼리 ──────────────────────────────────────────────────────────────

  const listQuery = useQuery({
    queryKey: SAVED_PACKS_QUERY_KEY,
    queryFn: ({ signal }) => listMyPacks(signal),
  });

  // ── 저장 mutation ──────────────────────────────────────────────────────────

  const saveMutation = useMutation<SavedPackOut, Error, FactorPack>({
    // ADR-0032 D3 — 저장 시 provenance(sourceUrl) 동반. body 와 분리 전송 →
    // content_hash 봉인 무관(server 가 row 메타로 저장).
    mutationFn: (p) => savePack(p, sourceUrl),
    onSuccess: (result) => {
      setSaveError(null);
      setSaveSuccessId(result.id);
      // 목록 캐시 무효화 → refetch.
      void qc.invalidateQueries({ queryKey: SAVED_PACKS_QUERY_KEY });
    },
    onError: (err) => {
      setSaveSuccessId(null);
      setSaveError(classifySaveError(err, t));
    },
  });

  // ── 삭제 mutation ──────────────────────────────────────────────────────────

  const deleteMutation = useMutation<void, Error, string>({
    mutationFn: (id) => deleteSavedPack(id),
    onSuccess: () => {
      setDeleteError(null);
      void qc.invalidateQueries({ queryKey: SAVED_PACKS_QUERY_KEY });
    },
    onError: (err) => {
      setDeleteError(t("library.deleteError", { message: err.message }));
    },
  });

  // ── visibility 토글 mutation ───────────────────────────────────────────────

  const visibilityMutation = useMutation<
    Awaited<ReturnType<typeof setPackVisibility>>,
    Error,
    { id: string; next: "private" | "public" }
  >({
    mutationFn: ({ id, next }) => setPackVisibility(id, next),
    onSuccess: (result) => {
      // 로컬 visibility 맵 업데이트.
      setVisibilityMap((prev) => ({ ...prev, [result.id]: result.visibility }));
      setVisibilityErrors((prev) => {
        const next = { ...prev };
        delete next[result.id];
        return next;
      });
    },
    onError: (err, variables) => {
      // 422 → 금지어휘 메시지, 그 외 → generic.
      const msg =
        err instanceof ApiError && err.status === 422
          ? t("library.visibilityErrorForbidden")
          : t("library.visibilityErrorGeneric", { message: err.message });
      setVisibilityErrors((prev) => ({ ...prev, [variables.id]: msg }));
    },
  });

  const onToggleVisibility = (id: string, current: "private" | "public"): void => {
    setVisibilityErrors((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    const next = current === "private" ? "public" : "private";
    visibilityMutation.mutate({ id, next });
  };

  // ── 불러오기 ───────────────────────────────────────────────────────────────

  const onLoadPack = (id: string): void => {
    if (loadingId !== null) return;
    setLoadingId(id);
    setLoadError(null);
    void getSavedPack(id)
      .then((loaded) => {
        onLoad(loaded);
        setLoadingId(null);
      })
      .catch((err: Error) => {
        setLoadError(t("library.loadError", { message: err.message }));
        setLoadingId(null);
      });
  };

  // ── 핸들러 ─────────────────────────────────────────────────────────────────

  const onSave = (): void => {
    setSaveSuccessId(null);
    setSaveError(null);
    saveMutation.mutate(pack);
  };

  const onDelete = (id: string): void => {
    setDeleteError(null);
    deleteMutation.mutate(id);
  };

  /**
   * "이 pack 으로 screen" 클릭 → 해당 pack body 를 불러와 패널 열기.
   * 이미 열려 있는 동일 id 면 닫기(토글). 다른 id 면 새 fetch 후 교체.
   */
  const onOpenScreen = (item: SavedPackOut): void => {
    // 동일 pack 재클릭 → 패널 닫기.
    if (screenPanel?.id === item.id) {
      setScreenPanel(null);
      return;
    }
    // pack body fetch 후 패널 열기.
    void getSavedPack(item.id).then((loaded) => {
      setScreenPanel({
        id: item.id,
        packSlug: loaded.pack_slug,
        version: loaded.version,
        factors: loaded.factors,
      });
    });
  };

  // ── 렌더 ───────────────────────────────────────────────────────────────────

  const items = listQuery.data?.items ?? [];
  const isSaving = saveMutation.isPending;
  const isDeleting = deleteMutation.isPending;
  const isTogglingVisibility = visibilityMutation.isPending;

  return (
    <div className="space-y-4">
      <p className="text-xs text-neutral-500">{t("library.description")}</p>

      {/* 저장 버튼 영역 */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onSave}
          disabled={isSaving}
          className="rounded-md bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-neutral-800 disabled:opacity-50"
        >
          {isSaving ? t("library.saving") : t("library.saveButton")}
        </button>

        {saveSuccessId !== null && !isSaving && (
          <span className="text-xs text-neutral-600">{t("library.saveSuccess")}</span>
        )}
        {saveError !== null && !isSaving && (
          <span
            data-testid="pack-library-save-error"
            className="text-xs text-neutral-700"
          >
            {t("library.saveError")}: {saveError}
          </span>
        )}
      </div>

      {/* 목록 */}
      <div className="overflow-x-auto rounded-md border border-neutral-200">
        {listQuery.isPending ? (
          <p className="px-4 py-3 text-xs text-neutral-500">{t("library.saving")}</p>
        ) : listQuery.isError ? (
          <p className="px-4 py-3 text-xs text-neutral-700">
            {t("library.listError", { message: (listQuery.error as Error).message })}
          </p>
        ) : items.length === 0 ? (
          <p
            data-testid="pack-library-empty"
            className="px-4 py-3 text-xs text-neutral-500"
          >
            {t("library.listEmpty")}
          </p>
        ) : (
          <table className="w-full table-fixed text-xs" data-testid="pack-library-table">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <th className="px-3 py-2 text-left font-medium text-neutral-600 w-2/5">
                  {t("library.colSlug")}
                </th>
                <th className="px-3 py-2 text-left font-medium text-neutral-600 w-1/6">
                  {t("library.colVersion")}
                </th>
                <th className="px-3 py-2 text-right font-medium text-neutral-600 w-1/6">
                  {t("library.colFactorCount")}
                </th>
                <th className="px-3 py-2 text-left font-medium text-neutral-600 w-1/4">
                  {t("library.colCreatedAt")}
                </th>
                <th className="px-3 py-2 text-left font-medium text-neutral-600 w-[10%]">
                  {t("library.colVisibility")}
                </th>
                <th className="px-3 py-2 text-right font-medium text-neutral-600 w-auto">
                  {""}
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                // PATCH 성공 후 로컬 상태 우선, 없으면 "private" (기본).
                const currentVisibility = visibilityMap[item.id] ?? "private";
                return (
                  <PackLibraryRow
                    key={item.id}
                    item={item}
                    isLoadingThis={loadingId === item.id}
                    isDeletingThis={deleteMutation.isPending && deleteMutation.variables === item.id}
                    isAnyBusy={loadingId !== null || isDeleting || isSaving}
                    isScreenOpen={screenPanel?.id === item.id}
                    visibility={currentVisibility}
                    isTogglingVisibility={
                      isTogglingVisibility &&
                      visibilityMutation.variables?.id === item.id
                    }
                    visibilityError={visibilityErrors[item.id] ?? null}
                    onLoad={() => onLoadPack(item.id)}
                    onDelete={() => onDelete(item.id)}
                    onScreen={() => onOpenScreen(item)}
                    onToggleVisibility={() =>
                      onToggleVisibility(item.id, currentVisibility)
                    }
                    loadLabel={t("library.loadButton")}
                    loadingLabel={t("library.loading")}
                    deleteLabel={t("library.deleteButton")}
                    deletingLabel={t("library.deleting")}
                    screenLabel={t("library.screenButton")}
                    visibilityPrivateLabel={t("library.visibilityPrivate")}
                    visibilityPublicLabel={t("library.visibilityPublic")}
                    visibilityToggleLabel={t("library.visibilityToggle")}
                    visibilityTogglingLabel={t("library.visibilityToggling")}
                    provenanceLabel={t("library.provenanceLabel")}
                  />
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* custom screen 패널 — 열린 pack 에 대해 단일 표시. */}
      {screenPanel !== null && (
        <CustomScreenPanel
          packSlug={screenPanel.packSlug}
          version={screenPanel.version}
          factors={screenPanel.factors}
          onClose={() => setScreenPanel(null)}
        />
      )}

      {/* 불러오기 / 삭제 에러 */}
      {loadError !== null && (
        <p
          data-testid="pack-library-load-error"
          className="text-xs text-neutral-700"
        >
          {loadError}
        </p>
      )}
      {deleteError !== null && (
        <p
          data-testid="pack-library-delete-error"
          className="text-xs text-neutral-700"
        >
          {deleteError}
        </p>
      )}
    </div>
  );
}

// ── Row 서브컴포넌트 (순수 렌더, 핸들러는 부모에서 전달) ──────────────────────

interface PackLibraryRowProps {
  readonly item: SavedPackOut;
  readonly isLoadingThis: boolean;
  readonly isDeletingThis: boolean;
  readonly isAnyBusy: boolean;
  /** custom screen 패널이 이 row 에 대해 열려 있는지. */
  readonly isScreenOpen: boolean;
  /** 현재 visibility 상태 (PATCH 응답 기준, 없으면 "private"). */
  readonly visibility: "private" | "public";
  /** visibility 토글 중인지(PATCH 진행 중). */
  readonly isTogglingVisibility: boolean;
  /** visibility 토글 에러 메시지(없으면 null). */
  readonly visibilityError: string | null;
  readonly onLoad: () => void;
  readonly onDelete: () => void;
  /** "이 pack 으로 screen" 버튼 핸들러. */
  readonly onScreen: () => void;
  /** visibility 토글 버튼 핸들러. */
  readonly onToggleVisibility: () => void;
  readonly loadLabel: string;
  readonly loadingLabel: string;
  readonly deleteLabel: string;
  readonly deletingLabel: string;
  readonly screenLabel: string;
  readonly visibilityPrivateLabel: string;
  readonly visibilityPublicLabel: string;
  readonly visibilityToggleLabel: string;
  readonly visibilityTogglingLabel: string;
  /** ADR-0032 D3 — provenance(출처) 라벨. */
  readonly provenanceLabel: string;
}

function PackLibraryRow({
  item,
  isLoadingThis,
  isDeletingThis,
  isAnyBusy,
  isScreenOpen,
  visibility,
  isTogglingVisibility,
  visibilityError,
  onLoad,
  onDelete,
  onScreen,
  onToggleVisibility,
  loadLabel,
  loadingLabel,
  deleteLabel,
  deletingLabel,
  screenLabel,
  visibilityPrivateLabel,
  visibilityPublicLabel,
  visibilityToggleLabel,
  visibilityTogglingLabel,
  provenanceLabel,
}: PackLibraryRowProps): JSX.Element {
  // ADR-0032 D3 — provenance 링크는 https 출처만 렌더(저장 시 server 가 이미
  // https-only 강제하나, 표시단도 방어적으로 위험 scheme 클릭 차단).
  const provenanceUrl =
    item.sourceUrl !== null && /^https:\/\//i.test(item.sourceUrl)
      ? item.sourceUrl
      : null;
  /** created_at ISO → 로컬 날짜+시간(사실 표시, 해석 없음). */
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
    <>
      <tr
        className="border-b border-neutral-100 last:border-0 hover:bg-neutral-50"
        data-testid={`pack-row-${item.id}`}
      >
        <td className="px-3 py-2 font-mono text-neutral-800 truncate">{item.packSlug}</td>
        <td className="px-3 py-2 font-mono text-neutral-700">{item.version}</td>
        <td className="px-3 py-2 text-right text-neutral-700">{item.factorCount}</td>
        <td className="px-3 py-2 text-neutral-600">{formattedDate}</td>
        {/* visibility 배지 — grayscale 계열만, 판단색(red/green) 0. */}
        <td className="px-3 py-2">
          <span
            className={VISIBILITY_BADGE_CLASS}
            data-testid={`pack-visibility-badge-${item.id}`}
          >
            {visibility === "public" ? visibilityPublicLabel : visibilityPrivateLabel}
          </span>
        </td>
        <td className="px-3 py-2 text-right">
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onToggleVisibility}
              disabled={isAnyBusy || isTogglingVisibility}
              data-testid={`pack-visibility-toggle-${item.id}`}
              className="rounded border border-neutral-200 px-2 py-0.5 text-neutral-500 hover:bg-neutral-50 disabled:opacity-50"
            >
              {isTogglingVisibility ? visibilityTogglingLabel : visibilityToggleLabel}
            </button>
            <button
              type="button"
              onClick={onScreen}
              data-testid={`pack-screen-${item.id}`}
              aria-expanded={isScreenOpen}
              className="rounded border border-neutral-300 px-2 py-0.5 text-neutral-700 hover:bg-neutral-50"
            >
              {screenLabel}
            </button>
            <button
              type="button"
              onClick={onLoad}
              disabled={isAnyBusy}
              data-testid={`pack-load-${item.id}`}
              className="rounded border border-neutral-300 px-2 py-0.5 text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
            >
              {isLoadingThis ? loadingLabel : loadLabel}
            </button>
            <button
              type="button"
              onClick={onDelete}
              disabled={isAnyBusy}
              data-testid={`pack-delete-${item.id}`}
              className="rounded border border-neutral-200 px-2 py-0.5 text-neutral-500 hover:bg-neutral-50 disabled:opacity-50"
            >
              {isDeletingThis ? deletingLabel : deleteLabel}
            </button>
          </div>
        </td>
      </tr>
      {/* ADR-0032 D3 — import provenance(출처 URL). content_hash 봉인과 무관한 row
          메타로, 이 pack 이 어디서 들어왔는지 use-시점에 표시한다. https 출처만
          링크(rel=noopener/noreferrer — referrer 누출·tabnabbing 차단). */}
      {provenanceUrl !== null && (
        <tr>
          <td
            colSpan={6}
            className="px-3 pb-2 text-[10px] text-neutral-500"
            data-testid={`pack-provenance-${item.id}`}
          >
            {provenanceLabel}:{" "}
            <a
              href={provenanceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono text-neutral-600 underline decoration-neutral-300 hover:text-neutral-800"
            >
              {provenanceUrl}
            </a>
          </td>
        </tr>
      )}
      {/* visibility 토글 에러 — 422(금지어휘) 또는 generic. 판단어 없는 중립 문구. */}
      {visibilityError !== null && (
        <tr>
          <td
            colSpan={6}
            className="px-3 pb-2 text-[10px] text-neutral-600"
            data-testid={`pack-visibility-error-${item.id}`}
          >
            {visibilityError}
          </td>
        </tr>
      )}
    </>
  );
}
