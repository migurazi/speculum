/**
 * Notes API client — `/api/notes` CRUD.
 *
 * Backend `app/api/routes/notes.py` 의 wire schema 와 1:1 매핑.
 * snake_case ↔ camelCase 변환은 watchlist.ts 패턴 동일.
 *
 * 인증:
 *   fetchJson 이 credentials="include" 로 JWT cookie 자동 전송.
 *   user-scoped — GET/PUT/DELETE 는 본인 note 만 접근(타인 → 404).
 *
 * 관련:
 *   - T76 종목별 Notes 패널.
 *   - M0_PLAN watchlist.ts 패턴.
 */

import { fetchJson } from "./client";

// =============================================================================
// Wire types (backend snake_case)
// =============================================================================

interface NoteOutWire {
  readonly id: string;
  readonly code_lineage_id: string;
  readonly body: string;
  readonly scope: string;
  readonly created_at: string;
  readonly updated_at: string;
}

interface NoteListWire {
  readonly notes: ReadonlyArray<NoteOutWire>;
}

// =============================================================================
// Domain types (camelCase)
// =============================================================================

export interface Note {
  readonly id: string;
  readonly codeLineageId: string;
  readonly body: string;
  readonly scope: string;
  readonly createdAt: string;  // ISO 8601 datetime
  readonly updatedAt: string;
}

export interface NoteList {
  readonly notes: ReadonlyArray<Note>;
}

// =============================================================================
// Mapping
// =============================================================================

function wireToNote(w: NoteOutWire): Note {
  return {
    id: w.id,
    codeLineageId: w.code_lineage_id,
    body: w.body,
    scope: w.scope,
    createdAt: w.created_at,
    updatedAt: w.updated_at,
  };
}

// =============================================================================
// API functions
// =============================================================================

/**
 * 특정 종목(lineage)의 내 메모 목록 조회.
 * GET /api/notes?code_lineage_id=<UUID>
 */
export async function listNotes(
  codeLineageId: string,
  signal?: AbortSignal,
): Promise<NoteList> {
  const wire = await fetchJson<NoteListWire>("/api/notes", {
    method: "GET",
    searchParams: { code_lineage_id: codeLineageId },
    signal,
  });
  return { notes: wire.notes.map(wireToNote) };
}

/**
 * 새 메모 작성.
 * POST /api/notes body {code_lineage_id, body}
 * 201 → NoteOut. body 10000자 초과 → 422.
 */
export async function createNote(
  codeLineageId: string,
  body: string,
  signal?: AbortSignal,
): Promise<Note> {
  const wire = await fetchJson<NoteOutWire>("/api/notes", {
    method: "POST",
    body: { code_lineage_id: codeLineageId, body },
    signal,
  });
  return wireToNote(wire);
}

/**
 * 메모 수정.
 * PUT /api/notes/{note_id} body {body}
 * 200 → NoteOut. 타인 note → 404.
 */
export async function updateNote(
  noteId: string,
  body: string,
  signal?: AbortSignal,
): Promise<Note> {
  const wire = await fetchJson<NoteOutWire>(
    `/api/notes/${encodeURIComponent(noteId)}`,
    { method: "PUT", body: { body }, signal },
  );
  return wireToNote(wire);
}

/**
 * 메모 삭제.
 * DELETE /api/notes/{note_id} → 204. 타인 note → 404.
 */
export async function deleteNote(
  noteId: string,
  signal?: AbortSignal,
): Promise<void> {
  await fetchJson<void>(
    `/api/notes/${encodeURIComponent(noteId)}`,
    { method: "DELETE", signal },
  );
}
