"use client";

/**
 * AuthTokenSync — useSession 의 accessToken 을 fetchJson 에 주입 (T68).
 *
 * SessionProvider 하위에서 마운트. useSession 이 반환하는 accessToken(HS256 JWS)을
 * setAuthToken 으로 module-level 변수에 설정 → 이후 fetchJson 이 Bearer 헤더에 포함.
 *
 * 로그아웃 / 세션 만료 시 accessToken 이 undefined 가 되어 setAuthToken("") 호출
 * → fetchJson 이 Authorization 헤더를 전송하지 않음 → backend 401 이 정상.
 *
 * 본 컴포넌트는 UI 를 렌더하지 않는다. layout 의 Providers 안에 단 1 회 삽입.
 *
 * 관련 ADR / 문서:
 * - ADR-0021 D1.1 (2026-06-02) — 모델 B Bearer 토큰 배선
 * - M2_PLAN T68
 */

import { useSession } from "next-auth/react";
import { useEffect } from "react";

import { setAuthToken } from "@/lib/api/client";

export function AuthTokenSync(): null {
  const { data: session } = useSession();

  useEffect(() => {
    // session 은 NextAuth Session 타입이지만 callbacks.session 에서 accessToken 을
    // 동적으로 확장했으므로 unknown 경유로 안전하게 추출.
    const raw = (session as unknown as Record<string, unknown> | null);
    const token =
      typeof raw?.["accessToken"] === "string" ? raw["accessToken"] : "";
    setAuthToken(token);
  }, [session]);

  return null;
}
