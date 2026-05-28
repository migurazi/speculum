/**
 * Screen API client — POST /api/screen.
 *
 * Backend `app/api/routes/screen.py` 의 wire schema 와 1:1 매핑. backend
 * Pydantic 의 ConditionIn / ScreenRunQueryIn / ScreenResultOut 의 TypeScript
 * 표현.
 *
 * 운영:
 *   - factor canonical_id 검증은 backend Pydantic regex 가 강제 — client
 *     도 같은 패턴 노출 (form validation 보강 backlog).
 *   - data_versions = backend 가 호출 시점 freeze. UI freshness banner
 *     (M1) 가 본 값으로 비교.
 */

import { fetchJson } from "./client";

/** OpEnum — backend `app.schemas.screen.OpEnum` 와 동일. */
export type ScreenOp = "<" | "<=" | "=" | ">=" | ">" | "!=";

export const SCREEN_OP_OPTIONS: ReadonlyArray<ScreenOp> = [
  "<",
  "<=",
  "=",
  ">=",
  ">",
  "!=",
];

/** ConditionIn wire — backend Pydantic strict 모드. */
export interface ScreenCondition {
  readonly factor: string;
  readonly op: ScreenOp;
  readonly value: string;
}

/** ScreenRunQueryIn — POST /api/screen body. */
export interface ScreenQuery {
  readonly conditions: ReadonlyArray<ScreenCondition>;
  readonly selected_factors: ReadonlyArray<string>;
}

/** ScreenResultOut — POST /api/screen 200 응답. */
export interface ScreenResult {
  readonly result_codes: ReadonlyArray<string>;
  readonly total: number;
  readonly data_versions: Readonly<Record<string, string>>;
}

/**
 * POST /api/screen — Screener 실행. Save Run 안 함.
 *
 * @param query 조건 + selected_factors
 * @param asOf  PIT 기준 일자 (ISO 8601, "YYYY-MM-DD")
 * @param signal AbortController signal (TanStack Query 가 cancel 시 주입)
 */
export async function executeScreen(
  query: ScreenQuery,
  asOf: string,
  signal?: AbortSignal,
): Promise<ScreenResult> {
  return fetchJson<ScreenResult>("/api/screen", {
    method: "POST",
    body: query,
    searchParams: { as_of: asOf },
    signal,
  });
}
