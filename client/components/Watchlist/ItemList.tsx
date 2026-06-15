"use client";

/**
 * ItemList — Watchlist 의 선택된 폴더 안 종목 list.
 *
 * 기능 (M0):
 *   - items 표시 (종목코드 + 메모)
 *   - 새 항목 추가 — 종목코드 입력 → backend `/api/stocks/search` 로
 *     lineage_id 해석 → POST /api/watchlists/{folder_id}/items
 *   - 메모 inline edit (PATCH)
 *   - 항목 삭제 (DELETE)
 *
 * code_lineage_id 해석:
 *   사용자는 종목코드 (예: "005930") 만 입력 — backend addItem 은 lineage_id
 *   요구. 본 component 가 searchStocks(code) → first match 의 id 로 add.
 *   미발견 종목 = error 표시.
 *
 * M0 미구현 (별도 cycle):
 *   - 종목 검색 autocomplete (M1+ Combobox)
 *   - 메모 markdown / link parse
 *   - drag-and-drop reorder
 *
 * 관련:
 * - ADR-0011 D1 — note 280 자 제한
 * - M0_PLAN T39 / AC-F-06
 */

import { useTranslations } from "next-intl";
import { useState } from "react";

import type { WatchlistFolder, WatchlistItem } from "@/lib/api/watchlist";

const MAX_NOTE_LEN = 280;  // ADR-0011 D1

interface ItemListProps {
  readonly folder: WatchlistFolder;
  readonly items: ReadonlyArray<WatchlistItem>;
  readonly onAdd: (code: string, note: string | null) => Promise<void>;
  readonly onUpdateNote: (
    itemId: string,
    note: string | null,
  ) => Promise<void>;
  readonly onRemove: (itemId: string) => Promise<void>;
  readonly isMutating: boolean;
  /** add item 실패 시 마지막 에러 — null 이면 미표시. */
  readonly addError: string | null;
}

export function ItemList({
  folder,
  items,
  onAdd,
  onUpdateNote,
  onRemove,
  isMutating,
  addError,
}: ItemListProps): JSX.Element {
  const t = useTranslations("watchlist");
  const [codeInput, setCodeInput] = useState<string>("");
  const [noteInput, setNoteInput] = useState<string>("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingNote, setEditingNote] = useState<string>("");

  async function handleAdd(): Promise<void> {
    const code = codeInput.trim();
    if (code.length === 0 || isMutating) return;
    if (!/^\d{1,6}$/.test(code)) {
      // form 측에서 즉시 거부 — backend 가 search 단계서 404 줄 일을 줄임.
      return;
    }
    await onAdd(code, noteInput.trim().length > 0 ? noteInput.trim() : null);
    setCodeInput("");
    setNoteInput("");
  }

  function startEdit(item: WatchlistItem): void {
    setEditingId(item.id);
    setEditingNote(item.note ?? "");
  }

  async function commitEdit(itemId: string): Promise<void> {
    const trimmed = editingNote.trim();
    await onUpdateNote(itemId, trimmed.length > 0 ? trimmed : null);
    setEditingId(null);
  }

  const codeIsValid = /^\d{1,6}$/.test(codeInput.trim());
  const canAdd =
    codeInput.trim().length > 0 && codeIsValid && !isMutating;

  return (
    <div className="flex-1 p-6">
      <header className="mb-4">
        <h2 className="text-lg font-semibold text-neutral-900">
          {folder.name}
        </h2>
        <p className="mt-0.5 text-xs text-neutral-500">
          {t("itemCount", { count: items.length })}
          {folder.is_default ? t("defaultFolderSuffix") : ""}
        </p>
      </header>

      <section className="mb-6 space-y-2 rounded-lg border border-neutral-200 bg-neutral-50 p-3">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={codeInput}
            onChange={(e) => setCodeInput(e.target.value)}
            placeholder={t("codeInputPlaceholder")}
            aria-label={t("codeInputAriaLabel")}
            className="w-40 rounded border border-neutral-300 px-2 py-1 font-mono text-sm"
            disabled={isMutating}
          />
          <input
            type="text"
            value={noteInput}
            onChange={(e) => setNoteInput(e.target.value)}
            placeholder={t("noteInputPlaceholder")}
            aria-label={t("noteInputAriaLabel")}
            maxLength={MAX_NOTE_LEN}
            className="flex-1 rounded border border-neutral-300 px-2 py-1 text-sm"
            disabled={isMutating}
          />
          <button
            type="button"
            onClick={() => void handleAdd()}
            disabled={!canAdd}
            className="rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-800 disabled:cursor-not-allowed disabled:bg-neutral-300"
          >
            {t("addButton")}
          </button>
        </div>
        {codeInput.length > 0 && !codeIsValid ? (
          <p role="alert" className="text-xs text-amber-700">
            {t("codeInvalidMessage")}
          </p>
        ) : null}
        {addError !== null ? (
          <p
            role="alert"
            className="text-xs text-red-700"
          >
            {addError}
          </p>
        ) : null}
      </section>

      {items.length === 0 ? (
        <p className="text-sm text-neutral-500">
          {t("emptyFolder")}
        </p>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <li
              key={item.id}
              className="rounded-md border border-neutral-200 bg-white p-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 space-y-1">
                  {/* code_lineage_id 자체는 종목코드가 아님. 종목코드 / 이름
                      해석은 별도 fetch 필요 — M0 = id 만 표시. M1+ batch fetch. */}
                  <div className="font-mono text-xs text-neutral-500">
                    {t("lineageLabel")}: {item.code_lineage_id}
                  </div>
                  {editingId === item.id ? (
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        value={editingNote}
                        onChange={(e) => setEditingNote(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void commitEdit(item.id);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        maxLength={MAX_NOTE_LEN}
                        aria-label={t("editNoteAriaLabel")}
                        className="flex-1 rounded border border-neutral-300 px-2 py-1 text-sm"
                        autoFocus
                      />
                      <button
                        type="button"
                        onClick={() => void commitEdit(item.id)}
                        className="rounded bg-neutral-800 px-2 py-1 text-xs text-white hover:bg-neutral-700"
                      >
                        {t("saveButton")}
                      </button>
                    </div>
                  ) : (
                    <div className="text-sm text-neutral-700">
                      {item.note ?? (
                        <span className="text-neutral-400">{t("noNote")}</span>
                      )}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1 text-xs text-neutral-500">
                  {editingId !== item.id ? (
                    <button
                      type="button"
                      onClick={() => startEdit(item)}
                      className="hover:underline"
                    >
                      {t("editNoteButton")}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => void onRemove(item.id)}
                    className="hover:underline"
                  >
                    {t("removeItemButton")}
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
