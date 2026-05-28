"use client";

/**
 * ConsentModal — 최초 방문 동의 모달.
 *
 * 8 기둥 §2.2 No Advice + §2.4 PIT + §2.10 Reproducibility 의 사용자 인지
 * 점. ADR-0006 D2 의 시스템 차원 No Advice 의무 + 자본시장법 유사투자자문업
 * 회피의 사용자 인지 보강.
 *
 * 정책:
 *   - 사용자가 명시 동의 button click 전까지 modal 표시 (blocking).
 *   - localStorage `speculum-consent-v1` 에 동의 영구 기록.
 *   - 동의 본문 변경 시 key version bump 로 재동의 유도.
 *   - close 버튼 / overlay click / Escape 으로 dismiss X (blocking modal).
 *
 * a11y:
 *   - radix-ui Dialog — focus trap + aria-modal + aria-labelledby 자동.
 *   - dismissable=false 패턴 — `onPointerDownOutside`/`onEscapeKeyDown` preventDefault.
 *
 * 관련 ADR / 문서:
 * - ADR-0006 D2 (No Advice — 법적 implementation)
 * - ADR-0007 D2 (모든 화면 footer + 최초 방문 modal 의무)
 * - M0_PLAN T32 / AC-F-01 (Google OAuth → 최초 방문 동의 → 메인)
 */

import * as Dialog from "@radix-ui/react-dialog";

import { useConsent } from "@/lib/hooks/useConsent";

export function ConsentModal(): JSX.Element | null {
  const { hasConsented, grantConsent } = useConsent();

  // hydration 진행 중 (null) 또는 이미 동의 완료 (true) → 미렌더.
  if (hasConsented !== false) {
    return null;
  }

  return (
    <Dialog.Root open={true}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-lg bg-white p-6 shadow-xl"
          // Blocking modal — outside click / Escape 으로 dismiss 안 됨.
          // 사용자가 button click 으로만 다음 단계로 이동.
          onPointerDownOutside={(event) => event.preventDefault()}
          onEscapeKeyDown={(event) => event.preventDefault()}
        >
          <Dialog.Title className="text-lg font-semibold text-neutral-900">
            Speculum 이용 안내
          </Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-neutral-700">
            본 도구는 정보 제공 목적의 정량 데이터 탐색기이며 투자 자문이
            아닙니다.
          </Dialog.Description>

          <ul className="mt-4 space-y-2 text-sm text-neutral-700">
            <li>
              <span className="font-medium">시스템은 종목·시점·전략을 권유하지
              않습니다.</span> 모든 결과는 사용자가 정의한 조건의 필터링 표시입
              니다.
            </li>
            <li>
              <span className="font-medium">모든 지표 값에 식·출처·기준일이
              함께 표시됩니다.</span> Point-in-Time 정합성을 8 기둥 §2.4 로
              강제합니다.
            </li>
            <li>
              <span className="font-medium">실행 결과는 Save Run snapshot 으로
              freeze 가능합니다.</span> 6 개월 후 동일 결과 재현 (§2.10).
            </li>
            <li>
              데이터는 금융감독원(DART), 한국거래소(KRX), pykrx,
              FinanceDataReader 의 1차/검증 자료를 사용합니다.
            </li>
          </ul>

          <div className="mt-6 flex justify-end">
            <button
              type="button"
              onClick={grantConsent}
              className="rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-800 focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-2"
            >
              동의하고 시작
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
