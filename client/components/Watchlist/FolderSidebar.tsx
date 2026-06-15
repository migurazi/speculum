"use client";

/**
 * FolderSidebar — Watchlist 의 좌측 폴더 list.
 *
 * 기능 (M0):
 *   - folder list 표시 (default 폴더 + 사용자 정의)
 *   - 폴더 생성 (이름 input + 추가 버튼)
 *   - 폴더 이름 변경 (inline edit)
 *   - 폴더 삭제 (default 제외)
 *
 * ADR-0011 D1 — default 폴더 1 개 보장, 사용자가 임의로 삭제 불가
 * (backend 가 거부). frontend 도 default 의 delete 버튼 미표시.
 *
 * M0 미구현 (별도 cycle):
 *   - 트리 구조 (parent_id) — backend 는 지원, M0 UI 는 flat list.
 *   - display_order drag-and-drop — M0 = 생성 순서.
 *
 * 관련:
 * - ADR-0011 (Watchlist scope)
 * - M0_PLAN T39 / AC-F-06.
 */

import { useTranslations } from "next-intl";
import { useState } from "react";

import type { WatchlistFolder } from "@/lib/api/watchlist";
import { cn } from "@/lib/utils";

interface FolderSidebarProps {
  readonly folders: ReadonlyArray<WatchlistFolder>;
  readonly selectedFolderId: string | null;
  readonly onSelect: (folderId: string) => void;
  readonly onCreate: (name: string) => Promise<void>;
  readonly onRename: (folderId: string, name: string) => Promise<void>;
  readonly onDelete: (folderId: string) => Promise<void>;
  readonly isMutating: boolean;
}

export function FolderSidebar({
  folders,
  selectedFolderId,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  isMutating,
}: FolderSidebarProps): JSX.Element {
  const t = useTranslations("watchlist");
  const [newName, setNewName] = useState<string>("");
  const [renameTarget, setRenameTarget] = useState<string | null>(null);
  const [renameText, setRenameText] = useState<string>("");

  async function handleCreate(): Promise<void> {
    const trimmed = newName.trim();
    if (trimmed.length === 0 || isMutating) {
      return;
    }
    await onCreate(trimmed);
    setNewName("");
  }

  function startRename(folder: WatchlistFolder): void {
    setRenameTarget(folder.id);
    setRenameText(folder.name);
  }

  async function commitRename(folderId: string): Promise<void> {
    const trimmed = renameText.trim();
    if (trimmed.length === 0) {
      // 빈 이름은 backend 가 422 — 클라이언트도 사전 거부.
      setRenameTarget(null);
      return;
    }
    await onRename(folderId, trimmed);
    setRenameTarget(null);
  }

  return (
    <aside className="w-60 shrink-0 border-r border-neutral-200 bg-neutral-50">
      <div className="p-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
          {t("sidebarHeading")}
        </h2>
        <ul className="mt-2 space-y-0.5">
          {folders.map((folder) => {
            const isSelected = folder.id === selectedFolderId;
            const isRenaming = renameTarget === folder.id;
            return (
              <li key={folder.id}>
                {isRenaming ? (
                  <div className="flex items-center gap-1">
                    <input
                      type="text"
                      value={renameText}
                      onChange={(e) => setRenameText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void commitRename(folder.id);
                        if (e.key === "Escape") setRenameTarget(null);
                      }}
                      className="flex-1 rounded border border-neutral-300 px-2 py-1 text-sm"
                      aria-label={t("renameInputAriaLabel")}
                      autoFocus
                    />
                    <button
                      type="button"
                      onClick={() => void commitRename(folder.id)}
                      className="rounded bg-neutral-800 px-2 py-1 text-xs text-white hover:bg-neutral-700"
                    >
                      {t("saveButton")}
                    </button>
                  </div>
                ) : (
                  <div
                    className={cn(
                      "group flex items-center justify-between gap-1 rounded px-2 py-1",
                      isSelected
                        ? "bg-neutral-900 text-white"
                        : "text-neutral-700 hover:bg-neutral-100",
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => onSelect(folder.id)}
                      className="flex-1 truncate text-left text-sm"
                    >
                      {folder.name}
                      {folder.is_default ? (
                        <span
                          className={cn(
                            "ml-1 text-[10px]",
                            isSelected ? "text-neutral-300" : "text-neutral-400",
                          )}
                        >
                          {t("defaultBadge")}
                        </span>
                      ) : null}
                    </button>
                    {/* 사용자 정의 폴더만 rename / delete 버튼 노출.
                        default 폴더는 backend 가 delete 시 404. */}
                    {!folder.is_default ? (
                      <div
                        className={cn(
                          "hidden gap-1 group-hover:flex",
                          isSelected ? "text-neutral-300" : "text-neutral-500",
                        )}
                      >
                        <button
                          type="button"
                          onClick={() => startRename(folder)}
                          aria-label={t("renameFolderAriaLabel", { name: folder.name })}
                          className="text-[11px] hover:underline"
                        >
                          {t("renameButton")}
                        </button>
                        <button
                          type="button"
                          onClick={() => void onDelete(folder.id)}
                          aria-label={t("deleteFolderAriaLabel", { name: folder.name })}
                          className="text-[11px] hover:underline"
                        >
                          {t("deleteButton")}
                        </button>
                      </div>
                    ) : null}
                  </div>
                )}
              </li>
            );
          })}
        </ul>

        <div className="mt-4 space-y-2 border-t border-neutral-200 pt-3">
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCreate();
            }}
            placeholder={t("newFolderPlaceholder")}
            aria-label={t("newFolderPlaceholder")}
            className="w-full rounded border border-neutral-300 px-2 py-1 text-sm"
            disabled={isMutating}
          />
          <button
            type="button"
            onClick={() => void handleCreate()}
            disabled={isMutating || newName.trim().length === 0}
            className="w-full rounded bg-neutral-800 px-2 py-1.5 text-xs text-white hover:bg-neutral-700 disabled:cursor-not-allowed disabled:bg-neutral-300"
          >
            {t("addFolderButton")}
          </button>
        </div>
      </div>
    </aside>
  );
}
