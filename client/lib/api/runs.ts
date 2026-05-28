/**
 * Screen Run API client — `/api/runs` CRUD.
 *
 * Backend `app/api/routes/runs.py` + `app/schemas/screen.py` 의 wire schema
 * 와 1:1 매핑.
 *
 * Save Run 의미 (ADR-0008 D7 / D7-bis):
 *   "현재 화면의 조건·결과·data_versions 를 immutable snapshot 으로 freeze".
 *   재실행 + 저장이 같은 호출 (M0 단순화, oracle T26 자문). T18 합류 후 평가
 *   비용 커지면 실행/저장 분리 token 패턴으로 교체.
 *
 * 관련:
 * - ADR-0002 D4 (append-only Run)
 * - ADR-0008 D7 (Run snapshot freeze)
 * - M0_PLAN T30 (backend) / T40 (frontend)
 */

import { fetchJson } from "./client";
import type { ScreenCondition } from "./screen";

/** ScreenRunSnapshotOut — `/api/runs` POST/GET 응답. */
export interface ScreenRunSnapshot {
  readonly id: string;
  readonly user_id: string;
  readonly as_of: string;  // ISO 8601 date
  readonly result_codes: ReadonlyArray<string>;
  readonly result_hash: string;
  readonly data_versions: Readonly<Record<string, string>>;
  readonly computed_at: string;  // ISO 8601 datetime
  /** 사용자 입력 보존 — UI 가 Run 재현 시 같은 conditions 표시. */
  readonly conditions: ReadonlyArray<Readonly<Record<string, string>>>;
  readonly selected_factors: ReadonlyArray<string>;
}

/** ScreenRunListOut — `/api/runs` GET 응답. */
export interface ScreenRunList {
  readonly items: ReadonlyArray<ScreenRunSnapshot>;
  readonly total: number;
}

/** VersionsDiffOut — `/api/runs/{id}/diff` 응답. */
export interface VersionsDiff {
  readonly snapshot_id: string;
  /** 변경된 키만 — `{key: [old, new]}`. 빈 객체 = 일치 (재현 OK). */
  readonly diff: Readonly<Record<string, readonly [string, string]>>;
}

/**
 * POST /api/runs — Save Run.
 *
 * Body 는 POST /api/screen 과 동일 (`ScreenRunQueryIn`). backend 가
 * conditions/selected_factors/as_of 로 재실행 + snapshot 저장.
 *
 * @param query conditions + selected_factors (executeScreen 과 동일 입력)
 * @param asOf  PIT 기준 일자.
 */
export async function saveRun(
  query: {
    readonly conditions: ReadonlyArray<ScreenCondition>;
    readonly selected_factors: ReadonlyArray<string>;
  },
  asOf: string,
  signal?: AbortSignal,
): Promise<ScreenRunSnapshot> {
  return fetchJson<ScreenRunSnapshot>("/api/runs", {
    method: "POST",
    body: query,
    searchParams: { as_of: asOf },
    signal,
  });
}

/**
 * GET /api/runs — 현재 사용자의 recent runs (default limit 20).
 */
export async function listRecentRuns(
  limit?: number,
  signal?: AbortSignal,
): Promise<ScreenRunList> {
  const searchParams: Record<string, string> = {};
  if (limit !== undefined) {
    searchParams["limit"] = String(limit);
  }
  return fetchJson<ScreenRunList>("/api/runs", {
    method: "GET",
    searchParams,
    signal,
  });
}

/**
 * GET /api/runs/{id} — 단일 Run snapshot.
 */
export async function fetchRun(
  runId: string,
  signal?: AbortSignal,
): Promise<ScreenRunSnapshot> {
  return fetchJson<ScreenRunSnapshot>(
    `/api/runs/${encodeURIComponent(runId)}`,
    { method: "GET", signal },
  );
}

/**
 * GET /api/runs/{id}/diff — snapshot data_versions vs 현재 active.
 *
 * 빈 diff = 재현 가능 (정책 무변경). 변경된 키가 있으면 UI 가 "이 Run 은 더
 * 이상 재현 불가" 배지.
 */
export async function fetchRunDiff(
  runId: string,
  signal?: AbortSignal,
): Promise<VersionsDiff> {
  return fetchJson<VersionsDiff>(
    `/api/runs/${encodeURIComponent(runId)}/diff`,
    { method: "GET", signal },
  );
}
