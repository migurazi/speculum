"use client";

/**
 * useConsent — 최초 방문 시 동의 모달 표시 / 동의 후 localStorage 영구화.
 *
 * 8 기둥 §2.2 No Advice + ADR-0006 D2 의 사용자 인지 강제. 동의 키는 version
 * 포함 (`speculum-consent-v1`) — 동의 본문이 바뀌면 (M1+ 변호사 자문 결과
 * 반영 시) key version bump 로 재동의 유도.
 *
 * SSR 안전:
 *   - 초기 state = null (SSR + 첫 hydration 일치 보장).
 *   - useEffect 가 client side mount 후 localStorage read → setState. 그 사이
 *     `hasConsented === null` 로 UI 가 모달 노출 결정 가능.
 *
 * 관련 ADR / 문서:
 * - ADR-0006 D2 (No Advice — 사용자 인지 강제 의무)
 * - M0_PLAN AC-F-01 (최초 방문 동의 모달)
 */

import { useCallback, useEffect, useState } from "react";

// 동의 본문 version. 변경 시 모든 사용자가 다시 동의.
const CONSENT_STORAGE_KEY = "speculum-consent-v1";

interface UseConsentReturn {
  /** null = SSR/hydration 진행 중, true = 동의 완료, false = 미동의 */
  readonly hasConsented: boolean | null;
  /** 동의 처리 — localStorage 영구화 + state 갱신 */
  readonly grantConsent: () => void;
}

export function useConsent(): UseConsentReturn {
  const [hasConsented, setHasConsented] = useState<boolean | null>(null);

  useEffect(() => {
    // SSR 에서는 window 미존재 — useEffect 안전.
    try {
      const value = window.localStorage.getItem(CONSENT_STORAGE_KEY);
      setHasConsented(value === "true");
    } catch {
      // localStorage 접근 실패 (private mode 등) → 미동의로 간주.
      setHasConsented(false);
    }
  }, []);

  const grantConsent = useCallback(() => {
    try {
      window.localStorage.setItem(CONSENT_STORAGE_KEY, "true");
    } catch {
      // localStorage 실패 — UI 사용 허용하되 다음 방문 시 다시 동의 요청.
    }
    setHasConsented(true);
  }, []);

  return { hasConsented, grantConsent };
}
