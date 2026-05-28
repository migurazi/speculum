/**
 * NextAuth catch-all route — `/api/auth/*` (signin / signout / callback).
 *
 * NextAuth v5 의 표준 패턴. `lib/auth.ts` 의 handlers 가 GET/POST 모두 처리.
 * T35 cycle 의 Google OAuth 실 통합 시 본 파일 변경 없이 `lib/auth.ts` 만
 * 갱신.
 */

import { handlers } from "@/lib/auth";

export const { GET, POST } = handlers;
