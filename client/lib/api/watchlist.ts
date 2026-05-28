/**
 * Watchlist + ScreenerSet API client — `/api/watchlists` CRUD.
 *
 * Backend `app/api/routes/watchlists.py` 의 wire schema 와 1:1 매핑.
 *
 * 인증:
 *   M0 single-user mode — backend 가 SYSTEM_USER_ID 자동 주입 (auth.py).
 *   T31 NextAuth 합류 후 fetchJson 이 JWT cookie 자동 전송 (브라우저
 *   credentials="include"). 본 모듈은 그때 추가 변경 X.
 *
 * 관련:
 * - ADR-0011 (Watchlist scope) — folder/item 모델
 * - M0_PLAN T28 (backend) / T39 (frontend)
 */

import { fetchJson } from "./client";

// =============================================================================
// Folder
// =============================================================================

export interface WatchlistFolder {
  readonly id: string;
  readonly parent_id: string | null;
  readonly name: string;
  readonly display_order: number;
  readonly is_default: boolean;
  readonly created_at: string;  // ISO 8601 datetime
  readonly updated_at: string;
}

export interface WatchlistFolderList {
  readonly items: ReadonlyArray<WatchlistFolder>;
  readonly total: number;
}

export async function listFolders(
  signal?: AbortSignal,
): Promise<WatchlistFolderList> {
  return fetchJson<WatchlistFolderList>("/api/watchlists", {
    method: "GET",
    signal,
  });
}

export async function createFolder(
  name: string,
  parentId: string | null = null,
  signal?: AbortSignal,
): Promise<WatchlistFolder> {
  return fetchJson<WatchlistFolder>("/api/watchlists", {
    method: "POST",
    body: { name, parent_id: parentId },
    signal,
  });
}

export async function updateFolder(
  folderId: string,
  patch: { readonly name?: string; readonly display_order?: number },
  signal?: AbortSignal,
): Promise<WatchlistFolder> {
  // PUT body 의 name / display_order 는 backend Pydantic 이 optional. JSON 의
  // undefined 키는 직렬화 시 누락되므로 명시적 제거 (서버가 undefined 를
  // explicit null 로 오해석 회피).
  const body: Record<string, unknown> = {};
  if (patch.name !== undefined) body["name"] = patch.name;
  if (patch.display_order !== undefined) body["display_order"] = patch.display_order;
  return fetchJson<WatchlistFolder>(`/api/watchlists/${encodeURIComponent(folderId)}`, {
    method: "PUT",
    body,
    signal,
  });
}

export async function deleteFolder(
  folderId: string,
  signal?: AbortSignal,
): Promise<void> {
  await fetchJson<void>(`/api/watchlists/${encodeURIComponent(folderId)}`, {
    method: "DELETE",
    signal,
  });
}

// =============================================================================
// Item
// =============================================================================

export interface WatchlistItem {
  readonly id: string;
  readonly watchlist_id: string;
  readonly code_lineage_id: string;
  readonly note: string | null;
  readonly display_order: number;
  readonly added_at: string;  // ISO 8601 datetime
}

export interface WatchlistItemList {
  readonly items: ReadonlyArray<WatchlistItem>;
  readonly total: number;
}

export async function listItems(
  folderId: string,
  signal?: AbortSignal,
): Promise<WatchlistItemList> {
  return fetchJson<WatchlistItemList>(
    `/api/watchlists/${encodeURIComponent(folderId)}/items`,
    { method: "GET", signal },
  );
}

export async function addItem(
  folderId: string,
  codeLineageId: string,
  note: string | null = null,
  signal?: AbortSignal,
): Promise<WatchlistItem> {
  return fetchJson<WatchlistItem>(
    `/api/watchlists/${encodeURIComponent(folderId)}/items`,
    {
      method: "POST",
      body: { code_lineage_id: codeLineageId, note },
      signal,
    },
  );
}

export async function updateItemNote(
  itemId: string,
  note: string | null,
  signal?: AbortSignal,
): Promise<WatchlistItem> {
  return fetchJson<WatchlistItem>(
    `/api/watchlists/items/${encodeURIComponent(itemId)}`,
    { method: "PATCH", body: { note }, signal },
  );
}

export async function deleteItem(
  itemId: string,
  signal?: AbortSignal,
): Promise<void> {
  await fetchJson<void>(
    `/api/watchlists/items/${encodeURIComponent(itemId)}`,
    { method: "DELETE", signal },
  );
}
