/**
 * Publisher API client — handle claim (ADR-0034 D1/D4).
 *
 * publisher claim 계약:
 *   - POST /api/publishers (CurrentUserDep 필요 — Bearer 토큰)
 *     body: { handle: string }  (pattern ^[a-z0-9][a-z0-9-]*$ ≤ 64)
 *     응답: { handle, created_at }
 *     에러: 409 이미 등록(handle 중복 또는 사용자 이미 claim),
 *           422 예약 handle (community/user/speculum-builtin/speculum) 또는 패턴 불일치.
 *
 * wire(snake) → camel 매핑 패턴은 factor-packs.ts 와 일관.
 */

import { fetchJson } from "./client";

/** publisher claim 응답 (camel). */
export interface Publisher {
  /** 등록된 handle. @ 없음. */
  readonly handle: string;
  /** 등록 일시 (ISO 8601 문자열). */
  readonly createdAt: string;
}

/** wire (snake) — POST /api/publishers 응답. */
interface PublisherWire {
  handle: string;
  created_at: string;
}

/**
 * POST /api/publishers — publisher handle claim.
 *
 * 인증 Bearer 토큰이 필요하다 (fetchJson 이 _authToken 자동 주입).
 * 예약 handle → 422, handle/사용자 중복 → 409. 호출측에서 ApiError 처리.
 *
 * @param handle  claim 할 handle (^[a-z0-9][a-z0-9-]*$, ≤ 64자)
 * @param signal  AbortController signal
 * @returns Publisher { handle, createdAt }
 * @throws ApiError — 409 중복, 422 예약/패턴, fetch 실패.
 */
export async function claimPublisher(
  handle: string,
  signal?: AbortSignal,
): Promise<Publisher> {
  const wire = await fetchJson<PublisherWire>("/api/publishers", {
    method: "POST",
    body: { handle },
    signal,
  });
  return {
    handle: wire.handle,
    createdAt: wire.created_at,
  };
}
