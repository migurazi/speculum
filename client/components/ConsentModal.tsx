"use client";

/**
 * ConsentModal — 최초 방문 동의 모달 (ADR-0006 D7.1 + T46 V2 fix).
 *
 * 4 절 구성 (ADR-0006 D7.1 본문 그대로):
 *   [1] 본 서비스의 성격 — 투자 자문 아님, 데이터 정확성 보증 X.
 *   [2] 개인정보 수집·이용 동의 — 수집 항목 / 목적 / 보유 기간 / 거부 권리.
 *       + [2-2] 국외 이전 동의 (Vercel 미국 / Fly.io 미국 — D6.5).
 *   [3] 연령 확인 — 만 14세 이상 (D6.4 + 개인정보보호법 제22조의2).
 *   [4] 처리방침 / 이용약관 link.
 *
 * 3 별도 체크박스 — 개인정보보호법 제22조 "별도 동의" 의무. 단일 button 으로
 * 다항 일괄 동의는 위반 위험 (Momus M0 review V2). 사용자가 3 개 모두 체크
 * 해야 "동의하고 시작" enable.
 *
 * 정책:
 *   - blocking modal — overlay click / Escape 으로 dismiss X.
 *   - localStorage key version v2 — v1 사용자도 재동의.
 *   - 처리방침/이용약관 link 는 모달 내부에서 새 탭 — modal blocking 유지.
 *
 * 관련 ADR / 문서:
 * - ADR-0006 D6.1 (필수 고지), D6.4 (만 14세), D6.5 (국외 이전), D7.1 (모달 4 절)
 * - ADR-0007 D2 (모든 화면 footer + 최초 방문 modal 의무)
 * - 개인정보보호법 제15조 (수집·이용 동의), 제22조 (별도 동의), 제22조의2 (만 14세)
 * - Momus M0 review V2 (Critical) — T46 fix
 * - M0_PLAN AC-F-01 / AC-L-01
 */

import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";

import { useConsent } from "@/lib/hooks/useConsent";

export function ConsentModal(): JSX.Element | null {
  const { hasConsented, grantConsent } = useConsent();

  // 3 개 필수 체크박스 state — 모두 true 일 때만 "동의하고 시작" enable.
  const [privacyChecked, setPrivacyChecked] = useState(false);
  const [overseasChecked, setOverseasChecked] = useState(false);
  const [age14Checked, setAge14Checked] = useState(false);

  // hydration 진행 중 (null) 또는 이미 동의 완료 (true) → 미렌더.
  if (hasConsented !== false) {
    return null;
  }

  const allChecked = privacyChecked && overseasChecked && age14Checked;

  return (
    <Dialog.Root open={true}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-full max-w-2xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg bg-white p-6 shadow-xl"
          // Blocking modal — outside click / Escape 으로 dismiss 안 됨.
          // 사용자가 button click 으로만 다음 단계로 이동.
          onPointerDownOutside={(event) => event.preventDefault()}
          onEscapeKeyDown={(event) => event.preventDefault()}
        >
          <Dialog.Title className="text-lg font-semibold text-neutral-900">
            Speculum 서비스 이용 안내
          </Dialog.Title>

          {/* [1] 본 서비스의 성격 — ADR-0006 D7.1 [1] 본문 그대로 */}
          <section className="mt-4">
            <h3 className="text-sm font-medium text-neutral-800">
              [1] 본 서비스의 성격
            </h3>
            <Dialog.Description className="mt-2 text-sm text-neutral-700">
              Speculum 은 한국 주식 시장의 정량 데이터(재무 지표, 시세, 통계
              등)를 탐색하는 도구입니다.{" "}
              <strong>투자 권유, 투자자문, 종목 추천을 제공하지 않습니다.</strong>
            </Dialog.Description>
            <p className="mt-2 text-sm text-neutral-700">
              표시되는 모든 정보는 정보 제공 목적이며, 자본시장법상 투자 자문
              또는 유사투자자문업이 아닙니다. 투자 결정 및 그 결과에 대한 최종
              책임은 이용자 본인에게 있습니다. 당사는 데이터의 정확성·완전성을
              보증하지 않으며, 정보 이용으로 인한 손실에 대해 법적 책임을 지지
              않습니다.
            </p>
          </section>

          {/* [2] 개인정보 수집·이용 동의 — ADR-0006 D6.1 + 별도 체크박스 */}
          <section className="mt-5 rounded-md border border-neutral-200 bg-neutral-50 p-3">
            <h3 className="text-sm font-medium text-neutral-800">
              [2] 개인정보 수집·이용 동의
            </h3>
            <ul className="mt-2 space-y-1 text-xs text-neutral-700">
              <li>
                <span className="font-medium">수집 항목</span>: 이메일 주소,
                Google 프로필 이름·사진, Google 계정 식별자
              </li>
              <li>
                <span className="font-medium">수집·이용 목적</span>: 회원 인증,
                사용자 데이터 (관심 종목·조건셋 등) 저장
              </li>
              <li>
                <span className="font-medium">보유·이용 기간</span>: 회원 탈퇴
                또는 1 년 미접속 시 즉시 파기
              </li>
              <li>
                <span className="font-medium">동의 거부 권리</span>: 거부 시
                서비스 이용 불가
              </li>
            </ul>

            <label className="mt-3 flex items-start gap-2 text-sm text-neutral-800">
              <input
                type="checkbox"
                checked={privacyChecked}
                onChange={(e) => setPrivacyChecked(e.target.checked)}
                aria-label="개인정보 수집·이용 동의 (필수)"
                className="mt-0.5 h-4 w-4 cursor-pointer"
              />
              <span>
                위 개인정보 수집·이용에 동의합니다.{" "}
                <span className="text-red-700">(필수)</span>
              </span>
            </label>

            <label className="mt-2 flex items-start gap-2 text-sm text-neutral-800">
              <input
                type="checkbox"
                checked={overseasChecked}
                onChange={(e) => setOverseasChecked(e.target.checked)}
                aria-label="개인정보 국외 이전 동의 (필수)"
                className="mt-0.5 h-4 w-4 cursor-pointer"
              />
              <span>
                본 서비스의 사용자 데이터가 해외 (미국) 에 저장됨을 이해하고
                동의합니다.{" "}
                <span className="text-red-700">(필수)</span>
              </span>
            </label>
          </section>

          {/* [3] 연령 확인 — ADR-0006 D6.4 + 개인정보보호법 제22조의2 */}
          <section className="mt-4 rounded-md border border-neutral-200 bg-neutral-50 p-3">
            <h3 className="text-sm font-medium text-neutral-800">
              [3] 연령 확인
            </h3>
            <label className="mt-2 flex items-start gap-2 text-sm text-neutral-800">
              <input
                type="checkbox"
                checked={age14Checked}
                onChange={(e) => setAge14Checked(e.target.checked)}
                aria-label="만 14세 이상 확인 (필수)"
                className="mt-0.5 h-4 w-4 cursor-pointer"
              />
              <span>
                본인은 만 14세 이상임을 확인합니다.{" "}
                <span className="text-red-700">(필수)</span>
              </span>
            </label>
          </section>

          {/* [4] 자세한 내용 — 처리방침 / 이용약관 link */}
          <section className="mt-4 text-xs text-neutral-600">
            <p>
              자세한 내용:{" "}
              <a
                href="/privacy"
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-neutral-900 underline"
              >
                개인정보처리방침
              </a>{" "}
              ·{" "}
              <a
                href="/terms"
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-neutral-900 underline"
              >
                이용약관
              </a>{" "}
              ·{" "}
              <a
                href="/disclaimer"
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-neutral-900 underline"
              >
                면책조항
              </a>
            </p>
          </section>

          <div className="mt-6 flex justify-end gap-2">
            <button
              type="button"
              onClick={() =>
                grantConsent({
                  privacy: privacyChecked,
                  overseasTransfer: overseasChecked,
                  age14: age14Checked,
                })
              }
              disabled={!allChecked}
              aria-label="동의하고 시작"
              className="rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-800 focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-neutral-300"
            >
              동의하고 시작
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
