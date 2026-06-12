"use client";

/**
 * Watchlist page — `/watchlist`.
 *
 * 좌측 폴더 sidebar + 우측 items list. TanStack Query 가 backend CRUD 동기화.
 *
 * 인증:
 *   M0 single-user — backend SYSTEM_USER_ID 자동 주입. T31 NextAuth 합류 후
 *   별도 변경 X.
 *
 * 흐름:
 *   1. 마운트 시 folders fetch → 첫 폴더 자동 선택 (default 가 첫 entry).
 *   2. selectedFolderId 변경 시 items fetch.
 *   3. 모든 mutation 후 해당 query invalidate (TanStack Query 패턴).
 *
 * 관련:
 * - ADR-0011 (Watchlist scope)
 * - M0_PLAN T39 / AC-F-06
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useEffect, useMemo, useState } from "react";

import { FolderSidebar } from "@/components/Watchlist/FolderSidebar";
import { ItemList } from "@/components/Watchlist/ItemList";
import { ApiError } from "@/lib/api/client";
import { searchStocks } from "@/lib/api/stocks";
import {
  addItem,
  createFolder,
  deleteFolder,
  deleteItem,
  listFolders,
  listItems,
  updateFolder,
  updateItemNote,
  type WatchlistFolderList,
  type WatchlistItemList,
} from "@/lib/api/watchlist";
import { useAsOfStore } from "@/state/as-of-store";

export default function WatchlistPage(): JSX.Element {
  const t = useTranslations("watchlist");
  const qc = useQueryClient();
  const asOf = useAsOfStore((s) => s.asOf);
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(
    null,
  );
  const [addError, setAddError] = useState<string | null>(null);

  const foldersQuery = useQuery<WatchlistFolderList>({
    queryKey: ["watchlist", "folders"],
    queryFn: ({ signal }) => listFolders(signal),
  });

  // folders 첫 로드 시 default 폴더 자동 선택.
  useEffect(() => {
    if (
      selectedFolderId === null
      && foldersQuery.data
      && foldersQuery.data.items.length > 0
    ) {
      // default 우선, 없으면 첫 entry.
      const def =
        foldersQuery.data.items.find((f) => f.is_default)
        ?? foldersQuery.data.items[0];
      if (def !== undefined) {
        setSelectedFolderId(def.id);
      }
    }
  }, [foldersQuery.data, selectedFolderId]);

  const itemsQuery = useQuery<WatchlistItemList>({
    queryKey: ["watchlist", "items", selectedFolderId],
    queryFn: ({ signal }) => listItems(selectedFolderId!, signal),
    enabled: selectedFolderId !== null,
  });

  const selectedFolder = useMemo(
    () =>
      foldersQuery.data?.items.find((f) => f.id === selectedFolderId)
      ?? null,
    [foldersQuery.data, selectedFolderId],
  );

  // -- mutations --

  const createFolderMut = useMutation({
    mutationFn: (name: string) => createFolder(name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["watchlist", "folders"] });
    },
  });

  const renameFolderMut = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      updateFolder(id, { name }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["watchlist", "folders"] });
    },
  });

  const deleteFolderMut = useMutation({
    mutationFn: (id: string) => deleteFolder(id),
    onMutate: async (deletedId) => {
      // 삭제 전 진행 중인 items query 취소 — 삭제된 folder_id 로 404 race
      // 방지 (oracle T39 M3).
      await qc.cancelQueries({
        queryKey: ["watchlist", "items", deletedId],
      });
    },
    onSuccess: (_data, deletedId) => {
      // 삭제된 폴더 선택 중이면 default 로 fallback.
      if (selectedFolderId === deletedId) {
        setSelectedFolderId(null);
      }
      void qc.invalidateQueries({ queryKey: ["watchlist", "folders"] });
    },
  });

  const addItemMut = useMutation({
    mutationFn: async (params: {
      readonly folderId: string;
      readonly code: string;
      readonly note: string | null;
    }) => {
      // 1. 종목코드 → code_lineage_id 해석. 6 자리 zero-pad 강제 후 search.
      //    backend search 는 numeric 입력 시 정확 매치 → padded prefix →
      //    substring 순 정렬 — `005930` 같은 정확 6 자리 매치는 안전 (oracle
      //    T39 C1).
      const padded = params.code.padStart(6, "0");
      // limit=2 — total 검증으로 다중 매치 시 사용자에게 disambiguation 요구.
      const search = await searchStocks(padded, asOf, { limit: 2 });
      if (search.items.length === 0) {
        throw new Error(
          `종목코드 "${padded}" 에 해당하는 종목을 찾을 수 없습니다.`,
        );
      }
      // total 이 1 이 아니거나 first item 의 code 가 padded 와 정확히 다르면
      // 모호함 — 사용자가 lineage 를 직접 확인하기 전까지 차단 (M1+ 합류 시
      // 검색 모달 + disambiguation UI).
      const first = search.items[0]!;
      if (first.code !== padded) {
        throw new Error(
          `종목코드 "${padded}" 의 정확 매치가 없습니다 (가장 가까운 종목: `
          + `${first.name} ${first.code}). 정확한 6 자리 코드를 입력하세요.`,
        );
      }
      // 2. addItem (folder + lineage_id + note).
      return addItem(params.folderId, first.id, params.note);
    },
    onSuccess: () => {
      setAddError(null);
      void qc.invalidateQueries({
        queryKey: ["watchlist", "items", selectedFolderId],
      });
    },
    onError: (err) => {
      const msg =
        err instanceof ApiError
          ? extractApiErrorMessage(err)
          : (err as Error).message;
      setAddError(msg);
    },
  });

  const updateNoteMut = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string | null }) =>
      updateItemNote(id, note),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["watchlist", "items", selectedFolderId],
      });
    },
  });

  const removeItemMut = useMutation({
    mutationFn: (id: string) => deleteItem(id),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["watchlist", "items", selectedFolderId],
      });
    },
  });

  const isMutating =
    createFolderMut.isPending
    || renameFolderMut.isPending
    || deleteFolderMut.isPending
    || addItemMut.isPending
    || updateNoteMut.isPending
    || removeItemMut.isPending;

  if (foldersQuery.isLoading) {
    return (
      <main className="px-6 py-8">
        <p className="text-sm text-neutral-500">{t("loading")}</p>
      </main>
    );
  }
  if (foldersQuery.isError) {
    return (
      <main className="px-6 py-8">
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900">
          {t("foldersLoadError", { message: (foldersQuery.error as Error).message })}
        </div>
      </main>
    );
  }

  const folders = foldersQuery.data?.items ?? [];

  return (
    <main className="flex min-h-[calc(100vh-160px)]">
      <h1 className="sr-only">Watchlist</h1>
      <FolderSidebar
        folders={folders}
        selectedFolderId={selectedFolderId}
        onSelect={(id) => {
          setSelectedFolderId(id);
          setAddError(null);
        }}
        onCreate={async (name) => {
          await createFolderMut.mutateAsync(name);
        }}
        onRename={async (id, name) => {
          await renameFolderMut.mutateAsync({ id, name });
        }}
        onDelete={async (id) => {
          // 폴더 삭제는 backend cascade 로 items 도 함께 사라짐 — 우발적
          // click 방지 (oracle T39 C2).
          const folder = folders.find((f) => f.id === id);
          const label = folder?.name ?? t("folderFallbackLabel");
          if (
            !window.confirm(
              t("confirmDeleteFolder", { label }),
            )
          ) {
            return;
          }
          await deleteFolderMut.mutateAsync(id);
        }}
        isMutating={isMutating}
      />

      {selectedFolder ? (
        <ItemList
          folder={selectedFolder}
          items={itemsQuery.data?.items ?? []}
          onAdd={async (code, note) => {
            await addItemMut.mutateAsync({
              folderId: selectedFolder.id,
              code,
              note,
            });
          }}
          onUpdateNote={async (id, note) => {
            await updateNoteMut.mutateAsync({ id, note });
          }}
          onRemove={async (id) => {
            // 종목 삭제 confirm (oracle T39 C2). 메모도 함께 사라짐.
            if (
              !window.confirm(t("confirmRemoveItem"))
            ) {
              return;
            }
            await removeItemMut.mutateAsync(id);
          }}
          isMutating={isMutating}
          addError={addError}
        />
      ) : (
        <div className="flex-1 p-6">
          <p className="text-sm text-neutral-500">{t("noFolderSelected")}</p>
        </div>
      )}
    </main>
  );
}

/**
 * ApiError body 의 FastAPI detail 추출.
 *
 * detail 형태 (oracle T39 M4):
 *   - HTTPException: `{detail: "메시지"}` — 문자열 그대로 반환.
 *   - 422 ValidationError: `{detail: [{loc, msg, type}, ...]}` — `msg` 들 join.
 *   - 그 외: `err.message` fallback.
 */
function extractApiErrorMessage(err: ApiError): string {
  try {
    const parsed = JSON.parse(err.body) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      return parsed.detail;
    }
    if (Array.isArray(parsed.detail)) {
      const msgs = parsed.detail
        .map((entry: unknown) => {
          if (
            typeof entry === "object"
            && entry !== null
            && "msg" in entry
            && typeof (entry as { msg: unknown }).msg === "string"
          ) {
            return (entry as { msg: string }).msg;
          }
          return null;
        })
        .filter((m): m is string => m !== null);
      if (msgs.length > 0) {
        return msgs.join("; ");
      }
    }
  } catch {
    // body 가 JSON 아닌 경우 — message 그대로.
  }
  return err.message;
}
