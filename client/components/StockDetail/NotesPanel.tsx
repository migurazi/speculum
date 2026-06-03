"use client";

/**
 * NotesPanel — 종목별 Markdown 메모 패널 (T76).
 *
 * 기능:
 *   - 본인 메모 목록 표시 (react-query useQuery).
 *   - 새 메모 작성 — textarea (raw Markdown) + 미리보기 탭.
 *   - 메모 편집 (PUT) / 삭제 (DELETE) — react-query useMutation.
 *   - Markdown 렌더: 반드시 renderMarkdown 경유 → dangerouslySetInnerHTML.
 *     이 컴포넌트 외 어디서도 dangerouslySetInnerHTML 에 직접 raw HTML 주입 금지.
 *
 * 중립 톤 원칙 (§2.2 No Advice):
 *   - Notes 는 사용자 자유 콘텐츠지만 시스템 UI 라벨은 판단색·추천어휘 0.
 *   - 등락색(red/blue/green) className 미사용.
 *   - 라벨: 작성/저장/편집/삭제/미리보기 (중립 동사).
 *
 * XSS 방어:
 *   - dangerouslySetInnerHTML 은 본 컴포넌트에만, renderMarkdown 경유만.
 *   - renderMarkdown: marked.parse → DOMPurify.sanitize → enforceAnchorRel.
 *
 * 관련:
 *   - T76 Notes 패널.
 *   - lib/markdown.ts — XSS chokepoint.
 *   - lib/api/notes.ts — API client.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { renderMarkdown } from "@/lib/markdown";
import {
  createNote,
  deleteNote,
  listNotes,
  updateNote,
} from "@/lib/api/notes";
import type { Note } from "@/lib/api/notes";
import { cn } from "@/lib/utils";

// =============================================================================
// Constants
// =============================================================================

const MAX_BODY_LEN = 10000;  // backend 422 임계치

// =============================================================================
// Types
// =============================================================================

interface NotesPanelProps {
  /** 종목 lineage UUID — GET/POST 의 code_lineage_id. */
  readonly codeLineageId: string;
  readonly className?: string;
}

type WriteTab = "write" | "preview";

// =============================================================================
// Sub-components
// =============================================================================

/**
 * 단일 메모 카드 — 표시 + 편집/삭제 인터페이스.
 * dangerouslySetInnerHTML 은 이 컴포넌트 내 renderMarkdown 경유 1회만.
 */
function NoteCard({
  note,
  onEdit,
  onDelete,
  isDeleting,
  t,
}: {
  note: Note;
  onEdit: (note: Note) => void;
  onDelete: (noteId: string) => void;
  isDeleting: boolean;
  t: ReturnType<typeof useTranslations<"stock">>;
}): JSX.Element {
  // renderMarkdown 경유 — XSS chokepoint (lib/markdown.ts).
  const safeHtml = renderMarkdown(note.body);

  return (
    <article className="rounded-lg border border-neutral-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        {/* 본문 — dangerouslySetInnerHTML: renderMarkdown 경유만 허용. */}
        {/* eslint-disable react/no-danger -- renderMarkdown(DOMPurify) chokepoint 경유, ADR-0007 D4.5 */}
        <div
          className="prose prose-sm max-w-none flex-1 text-neutral-800"
          dangerouslySetInnerHTML={{ __html: safeHtml }}
        />
        {/* eslint-enable react/no-danger */}
        <div className="flex shrink-0 items-center gap-2 text-xs text-neutral-500">
          <button
            type="button"
            onClick={() => onEdit(note)}
            className="hover:text-neutral-800 hover:underline"
            disabled={isDeleting}
          >
            {t("notes.editButton")}
          </button>
          <button
            type="button"
            onClick={() => onDelete(note.id)}
            className="hover:text-neutral-800 hover:underline"
            disabled={isDeleting}
          >
            {t("notes.deleteButton")}
          </button>
        </div>
      </div>
      <p className="mt-2 text-[11px] text-neutral-400">
        {t("notes.dateLabel")}: {note.updatedAt.slice(0, 10)}
      </p>
    </article>
  );
}

/**
 * Markdown 편집기 — 작성/미리보기 탭 포함.
 * 미리보기 렌더는 renderMarkdown 경유.
 */
function MarkdownEditor({
  value,
  onChange,
  placeholder,
  disabled,
  t,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  disabled: boolean;
  t: ReturnType<typeof useTranslations<"stock">>;
}): JSX.Element {
  const [activeTab, setActiveTab] = useState<WriteTab>("write");
  const previewHtml = renderMarkdown(value);

  return (
    <div className="rounded-lg border border-neutral-200 bg-neutral-50">
      {/* 탭 헤더 */}
      <div className="flex border-b border-neutral-200">
        <button
          type="button"
          onClick={() => setActiveTab("write")}
          className={cn(
            "px-4 py-2 text-sm font-medium",
            activeTab === "write"
              ? "border-b-2 border-neutral-900 text-neutral-900"
              : "text-neutral-500 hover:text-neutral-700",
          )}
        >
          {t("notes.writeTab")}
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("preview")}
          className={cn(
            "px-4 py-2 text-sm font-medium",
            activeTab === "preview"
              ? "border-b-2 border-neutral-900 text-neutral-900"
              : "text-neutral-500 hover:text-neutral-700",
          )}
        >
          {t("notes.previewTab")}
        </button>
      </div>

      {/* 탭 본문 */}
      <div className="p-3">
        {activeTab === "write" ? (
          <textarea
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            maxLength={MAX_BODY_LEN}
            disabled={disabled}
            rows={6}
            className="w-full resize-none rounded border border-neutral-300 bg-white px-3 py-2 font-mono text-sm text-neutral-800 placeholder:text-neutral-400 focus:border-neutral-500 focus:outline-none disabled:cursor-not-allowed disabled:bg-neutral-100"
            aria-label={placeholder}
          />
        ) : (
          <>
            {/* 미리보기 — renderMarkdown 경유. dangerouslySetInnerHTML 은 이곳에서도 허용. */}
            {/* eslint-disable react/no-danger -- renderMarkdown(DOMPurify) chokepoint 경유, ADR-0007 D4.5 */}
            <div
              className={cn(
                "prose prose-sm min-h-[9rem] max-w-none rounded border border-neutral-200 bg-white p-3 text-neutral-800",
                value.trim().length === 0 && "text-neutral-400",
              )}
              dangerouslySetInnerHTML={{
                __html:
                  value.trim().length > 0
                    ? previewHtml
                    : "<p>미리보기할 내용이 없습니다.</p>",
              }}
            />
            {/* eslint-enable react/no-danger */}
          </>
        )}
        {/* 글자 수 */}
        <p
          className={cn(
            "mt-1 text-right text-xs",
            value.length > MAX_BODY_LEN
              ? "text-neutral-700"
              : "text-neutral-400",
          )}
        >
          {value.length.toLocaleString()} / {MAX_BODY_LEN.toLocaleString()}
        </p>
      </div>
    </div>
  );
}

// =============================================================================
// NotesPanel
// =============================================================================

/**
 * 종목별 메모 패널 — 목록 + 작성 + 편집 + 삭제.
 */
export function NotesPanel({
  codeLineageId,
  className,
}: NotesPanelProps): JSX.Element {
  const t = useTranslations("stock");
  const queryClient = useQueryClient();

  // ─── 상태 ──────────────────────────────────────────────────────────────────
  const [isComposing, setIsComposing] = useState(false);
  const [newBody, setNewBody] = useState("");
  const [newError, setNewError] = useState<string | null>(null);

  const [editingNote, setEditingNote] = useState<Note | null>(null);
  const [editBody, setEditBody] = useState("");
  const [editError, setEditError] = useState<string | null>(null);

  const [deleteError, setDeleteError] = useState<string | null>(null);

  // ─── Query ─────────────────────────────────────────────────────────────────
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["notes", codeLineageId] as const,
    queryFn: ({ signal }) => listNotes(codeLineageId, signal),
    staleTime: 60 * 1000, // 1분
    enabled: codeLineageId.length > 0,
  });

  // ─── Mutations ─────────────────────────────────────────────────────────────
  const createMutation = useMutation({
    mutationFn: (body: string) => createNote(codeLineageId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notes", codeLineageId] });
      setNewBody("");
      setIsComposing(false);
      setNewError(null);
    },
    onError: (err: Error) => {
      setNewError(t("notes.saveError", { message: err.message }));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: string }) =>
      updateNote(id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notes", codeLineageId] });
      setEditingNote(null);
      setEditBody("");
      setEditError(null);
    },
    onError: (err: Error) => {
      setEditError(t("notes.saveError", { message: err.message }));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (noteId: string) => deleteNote(noteId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notes", codeLineageId] });
      setDeleteError(null);
    },
    onError: (err: Error) => {
      setDeleteError(t("notes.deleteError", { message: err.message }));
    },
  });

  // ─── 핸들러 ────────────────────────────────────────────────────────────────

  function handleStartEdit(note: Note): void {
    setEditingNote(note);
    setEditBody(note.body);
    setEditError(null);
    setIsComposing(false);
  }

  function handleCancelEdit(): void {
    setEditingNote(null);
    setEditBody("");
    setEditError(null);
  }

  function handleSaveNew(): void {
    const trimmed = newBody.trim();
    if (trimmed.length === 0) {
      setNewError(t("notes.bodyEmpty"));
      return;
    }
    if (trimmed.length > MAX_BODY_LEN) {
      setNewError(t("notes.bodyTooLong"));
      return;
    }
    createMutation.mutate(trimmed);
  }

  function handleSaveEdit(): void {
    if (!editingNote) return;
    const trimmed = editBody.trim();
    if (trimmed.length === 0) {
      setEditError(t("notes.bodyEmpty"));
      return;
    }
    if (trimmed.length > MAX_BODY_LEN) {
      setEditError(t("notes.bodyTooLong"));
      return;
    }
    updateMutation.mutate({ id: editingNote.id, body: trimmed });
  }

  function handleDelete(noteId: string): void {
    deleteMutation.mutate(noteId);
  }

  // ─── 렌더 ──────────────────────────────────────────────────────────────────

  return (
    <div className={cn("rounded-lg border border-neutral-200 bg-white", className)}>
      {/* 헤더 */}
      <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
        <h2 className="text-sm font-medium text-neutral-700">
          {t("notes.heading")}
        </h2>
        {!isComposing && editingNote === null ? (
          <button
            type="button"
            onClick={() => {
              setIsComposing(true);
              setNewError(null);
            }}
            className="rounded bg-neutral-900 px-3 py-1 text-xs font-medium text-white hover:bg-neutral-800"
          >
            {t("notes.writeLabel")}
          </button>
        ) : null}
      </div>

      <div className="space-y-4 p-4">
        {/* 새 메모 작성 폼 */}
        {isComposing ? (
          <div className="space-y-2">
            <MarkdownEditor
              value={newBody}
              onChange={setNewBody}
              placeholder={t("notes.bodyPlaceholder")}
              disabled={createMutation.isPending}
              t={t}
            />
            {newError !== null ? (
              <p role="alert" className="text-xs text-neutral-700">
                {newError}
              </p>
            ) : null}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setIsComposing(false);
                  setNewBody("");
                  setNewError(null);
                }}
                className="rounded border border-neutral-300 px-3 py-1.5 text-xs text-neutral-700 hover:bg-neutral-50"
                disabled={createMutation.isPending}
              >
                {t("notes.cancelButton")}
              </button>
              <button
                type="button"
                onClick={handleSaveNew}
                disabled={createMutation.isPending || newBody.trim().length === 0}
                className="rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-neutral-800 disabled:cursor-not-allowed disabled:bg-neutral-300"
              >
                {t("notes.saveButton")}
              </button>
            </div>
          </div>
        ) : null}

        {/* 로딩 */}
        {isLoading ? (
          <div className="space-y-3">
            <div className="h-24 animate-pulse rounded-lg bg-neutral-100" />
            <div className="h-20 animate-pulse rounded-lg bg-neutral-100" />
          </div>
        ) : null}

        {/* 에러 */}
        {isError ? (
          <div className="rounded-md border border-neutral-300 bg-neutral-50 px-4 py-3 text-sm text-neutral-800">
            {t("notes.loadError", { message: (error as Error).message })}
          </div>
        ) : null}

        {/* 삭제 에러 */}
        {deleteError !== null ? (
          <p role="alert" className="text-xs text-neutral-700">
            {deleteError}
          </p>
        ) : null}

        {/* 편집 중인 메모 */}
        {editingNote !== null ? (
          <div className="space-y-2">
            <MarkdownEditor
              value={editBody}
              onChange={setEditBody}
              placeholder={t("notes.bodyPlaceholder")}
              disabled={updateMutation.isPending}
              t={t}
            />
            {editError !== null ? (
              <p role="alert" className="text-xs text-neutral-700">
                {editError}
              </p>
            ) : null}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={handleCancelEdit}
                className="rounded border border-neutral-300 px-3 py-1.5 text-xs text-neutral-700 hover:bg-neutral-50"
                disabled={updateMutation.isPending}
              >
                {t("notes.cancelButton")}
              </button>
              <button
                type="button"
                onClick={handleSaveEdit}
                disabled={updateMutation.isPending || editBody.trim().length === 0}
                className="rounded bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-neutral-800 disabled:cursor-not-allowed disabled:bg-neutral-300"
              >
                {t("notes.saveButton")}
              </button>
            </div>
          </div>
        ) : null}

        {/* 메모 목록 */}
        {!isLoading && !isError && data ? (
          data.notes.length === 0 && !isComposing ? (
            <p className="text-sm text-neutral-500">{t("notes.empty")}</p>
          ) : (
            <ul className="space-y-3">
              {data.notes.map((note) =>
                editingNote?.id === note.id ? null : (
                  <li key={note.id}>
                    <NoteCard
                      note={note}
                      onEdit={handleStartEdit}
                      onDelete={handleDelete}
                      isDeleting={deleteMutation.isPending}
                      t={t}
                    />
                  </li>
                ),
              )}
            </ul>
          )
        ) : null}
      </div>
    </div>
  );
}
