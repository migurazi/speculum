"use client";

/**
 * useConsent — 최초 방문 시 동의 모달 표시 / 동의 후 localStorage 영구화.
 *
 * ADR-0006 D7.1 의 4 절 (서비스 성격 + 개인정보 + 만 14세 + 처리방침) 모두
 * 동의를 받아야 service 진입. 3 개 별도 체크박스 (개인정보 / 국외 이전 /
 * 만 14세) 가 모두 true 일 때만 grantConsent 성공 — 개인정보보호법 제22조
 * 별도 동의 의무.
 *
 * SSR 안전:
 *   - 초기 state = null (SSR + 첫 hydration 일치 보장).
 *   - useEffect 가 client side mount 후 localStorage read → setState.
 *
 * 동의 본문 / 항목 변경 시 storage key version bump (v1 → v2) 로 모든 사용자
 * 가 다시 동의 — 이미 v1 동의한 사용자는 v2 항목 (국외 이전 동의 / 만 14세
 * 확인) 미동의 상태이므로 재동의 의무. T46 Momus review V2 fix 의 핵심
 * 의미론.
 *
 * 관련 ADR / 문서:
 * - ADR-0006 D6.1 (필수 고지 4 항목), D6.4 (만 14세), D6.5 (국외 이전), D7.1
 *   (모달 4 절)
 * - Momus M0 review V2 (개인정보보호법 제22조 별도 동의)
 * - M0_PLAN AC-F-01 / AC-L-01
 */

import { useCallback, useEffect, useState } from "react";

// v1 = 단일 boolean (T32). v2 = 3 항목 별도 동의 (T46 V2 fix).
// 본 key version bump 으로 v1 사용자도 v2 항목 재동의 강제.
const CONSENT_STORAGE_KEY = "speculum-consent-v2";

/**
 * 영구화된 동의 record — 3 개 항목 모두 true 일 때만 hasConsented = true.
 *
 * `version` 은 향후 추가 항목 (M1+ 광고 동의 등) 호환을 위해 보존 — 본 hook 은
 * v2 만 인식, 다른 version 은 미동의 처리.
 */
interface ConsentRecord {
  readonly version: 2;
  readonly privacy: boolean;       // [2] 개인정보 수집·이용 동의
  readonly overseasTransfer: boolean;  // [2-2] 국외 이전 동의 (D6.5)
  readonly age14: boolean;         // [3] 만 14세 이상 확인 (D6.4)
  readonly grantedAt: string;      // ISO 8601 timestamp
}

interface UseConsentReturn {
  /** null = SSR/hydration 진행 중, true = 3 항목 모두 동의 완료, false = 미동의 */
  readonly hasConsented: boolean | null;
  /**
   * 동의 처리 — 3 항목 모두 true 면 영구화 + state 갱신.
   * 일부라도 false 면 silent no-op (UI button disable 보강).
   */
  readonly grantConsent: (checks: {
    readonly privacy: boolean;
    readonly overseasTransfer: boolean;
    readonly age14: boolean;
  }) => void;
}

export function useConsent(): UseConsentReturn {
  const [hasConsented, setHasConsented] = useState<boolean | null>(null);

  useEffect(() => {
    // SSR 에서는 window 미존재 — useEffect 안전.
    try {
      const raw = window.localStorage.getItem(CONSENT_STORAGE_KEY);
      if (raw === null) {
        setHasConsented(false);
        return;
      }
      // v2 record JSON 파싱 — invalid 또는 다른 version 이면 false.
      const parsed = JSON.parse(raw) as Partial<ConsentRecord>;
      const allConsented =
        parsed.version === 2
        && parsed.privacy === true
        && parsed.overseasTransfer === true
        && parsed.age14 === true;
      setHasConsented(allConsented);
    } catch {
      // localStorage 접근 실패 (private mode) 또는 JSON 파싱 실패 → 미동의 처리.
      // 사용자가 모달 다시 보고 명시 동의 후 진입 — 안전 측면 default.
      setHasConsented(false);
    }
  }, []);

  const grantConsent = useCallback(
    (checks: {
      readonly privacy: boolean;
      readonly overseasTransfer: boolean;
      readonly age14: boolean;
    }): void => {
      // 모든 필수 동의 항목이 true 인 경우만 영구화 — ConsentModal 의 button
      // disabled 와 이중 가드.
      if (!checks.privacy || !checks.overseasTransfer || !checks.age14) {
        return;
      }
      const record: ConsentRecord = {
        version: 2,
        privacy: true,
        overseasTransfer: true,
        age14: true,
        grantedAt: new Date().toISOString(),
      };
      try {
        window.localStorage.setItem(
          CONSENT_STORAGE_KEY,
          JSON.stringify(record),
        );
      } catch {
        // localStorage 실패 — UI 사용 허용하되 다음 방문 시 다시 동의 요청.
      }
      setHasConsented(true);
    },
    [],
  );

  return { hasConsented, grantConsent };
}
