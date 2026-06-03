"use client";

/**
 * AuthButton — 로그인/로그아웃 버튼 골격 (T68 ADR-0021 D1.1).
 *
 * useSession 으로 인증 상태를 감지:
 *   - 미인증 → Google 로그인 버튼 표시 (signIn("google"))
 *   - 인증됨 → 이메일 + 로그아웃 버튼 표시 (signOut())
 *   - 로딩 → 버튼 disabled + "..." placeholder
 *
 * Google credential 미설정 환경(placeholder env)에서는 signIn 이 Google
 * OAuth 화면 없이 에러를 반환하는 것이 정상 — credential 확보 시 E2E 동작.
 * 본 컴포넌트 자체는 credential 없이 렌더·상태 전환 모두 동작.
 *
 * 관련 ADR / 문서:
 * - ADR-0021 D1.1 (2026-06-02) — Google OAuth 인증 설계 확정
 * - M2_PLAN T68 — NextAuth Google OAuth 골격
 */

import { useSession, signIn, signOut } from "next-auth/react";
import { useTranslations } from "next-intl";

export function AuthButton(): JSX.Element {
  const { data: session, status } = useSession();
  const t = useTranslations("common");

  const isLoading = status === "loading";

  if (isLoading) {
    return (
      <button
        disabled
        aria-busy="true"
        className="rounded-md border border-neutral-300 px-3 py-1 text-sm text-neutral-400 disabled:cursor-not-allowed"
      >
        {t("auth.loading")}
      </button>
    );
  }

  if (session) {
    return (
      <div className="flex items-center gap-2">
        <span
          className="max-w-[160px] truncate text-sm text-neutral-700"
          title={session.user?.email ?? ""}
        >
          {session.user?.email}
        </span>
        <button
          onClick={() => void signOut()}
          className="rounded-md border border-neutral-300 px-3 py-1 text-sm text-neutral-700 hover:border-neutral-400 hover:text-neutral-900"
        >
          {t("auth.signOut")}
        </button>
      </div>
    );
  }

  return (
    <button
      onClick={() => void signIn("google")}
      className="rounded-md border border-neutral-300 bg-white px-3 py-1 text-sm text-neutral-700 hover:border-neutral-400 hover:text-neutral-900"
    >
      {t("auth.signIn")}
    </button>
  );
}
