"use client";

/**
 * Speculum 의 client-side providers — T31 scaffold.
 *
 * QueryClientProvider (TanStack Query) + SessionProvider (NextAuth) 를 layout
 * 에서 wrap. 모든 client component 가 본 provider 안에서 동작.
 *
 * 관련 ADR / 문서:
 * - ARCHITECTURE.md §2.1 (TanStack Query — 서버 상태)
 * - M0_PLAN T31 (NextAuth Google OAuth — 본 cycle 은 placeholder)
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import { useState, type ReactNode } from "react";

import { AuthTokenSync } from "@/components/AuthTokenSync";
import { CalendarBoundsSync } from "@/components/CalendarBoundsSync";

interface ProvidersProps {
  readonly children: ReactNode;
}

export function Providers({ children }: ProvidersProps): JSX.Element {
  // QueryClient 는 인스턴스 단위 — useState 로 SSR/CSR 경계의 hydration 안전.
  // staleTime default 0 (운영 backend 의 cache 정책 우선) — endpoint 별 override.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            refetchOnWindowFocus: false,
            staleTime: 30 * 1000,
          },
        },
      }),
  );

  return (
    <SessionProvider>
      {/* AuthTokenSync: useSession → setAuthToken 배선 (ADR-0021 D1.1 모델 B). */}
      <AuthTokenSync />
      <QueryClientProvider client={queryClient}>
        {/* CalendarBoundsSync: GET /api/calendar → as_of 자동 클램프 + input min/max 반영. */}
        <CalendarBoundsSync />
        {children}
      </QueryClientProvider>
    </SessionProvider>
  );
}
