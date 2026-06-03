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

/**
 * SecurityType — backend `app.schemas.screen.SecurityTypeEnum` 와 동일.
 * 허용 4종: common(보통주) · preferred(우선주) · etf(ETF) · reit(리츠).
 * 빈 배열·미지원값은 backend 422.
 */
export type SecurityType = "common" | "preferred" | "etf" | "reit";

/** 허용 자산군 4종 — UI 체크박스 순서. ADR-0023 D4 동등 가시성. */
export const SECURITY_TYPE_OPTIONS: ReadonlyArray<SecurityType> = [
  "common",
  "preferred",
  "etf",
  "reit",
];

/** 기본 선택: 보통주만 (ADR-0023 D4 / D7 — M0~M1 universe 연속성 보존). */
export const DEFAULT_SECURITY_TYPES: ReadonlyArray<SecurityType> = ["common"];

/** ScreenRunQueryIn — POST /api/screen body. */
export interface ScreenQuery {
  readonly conditions: ReadonlyArray<ScreenCondition>;
  readonly selected_factors: ReadonlyArray<string>;
  /**
   * 자산군 필터. 미지정 시 backend 가 `["common"]` 으로 해소.
   * ADR-0023 D7 — screen query 일부 → result_hash 자동 freeze.
   */
  readonly security_types?: ReadonlyArray<SecurityType>;
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
