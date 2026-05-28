/**
 * Speculum NextAuth config — T31 scaffold placeholder.
 *
 * 본 cycle 의 scope = "module loadable + route 가 import 가능" 만. Google
 * OAuth 실 통합 + JWT 검증 + backend FastAPI 와의 PUBLIC_KEY 공유 등은
 * T35 cycle 의 운영 합류.
 *
 * 환경변수 (T35 합류 시) — NextAuth v5 의 AUTH_* prefix:
 *   - AUTH_URL (운영 host, 자동 추론 가능)
 *   - AUTH_SECRET (JWT 서명 키 — `openssl rand -base64 32` 권장)
 *   - AUTH_GOOGLE_ID / AUTH_GOOGLE_SECRET
 *
 * 관련 ADR / 문서:
 * - ARCHITECTURE.md §6 — 인증/권한
 * - M0_PLAN T31 (scaffold), AC-F-01 (Google OAuth)
 */

import NextAuth from "next-auth";

export const { handlers, auth, signIn, signOut } = NextAuth({
  // T35 합류 시 Google provider 추가. 본 cycle 은 빈 array — module
  // loadable + route handler import 가능 검증.
  providers: [],
  // M0 placeholder — session strategy 는 T35 에서 결정 (jwt vs database).
});
