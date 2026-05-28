/**
 * 개인정보처리방침 페이지 — ADR-0006 D6.2 + 개인정보보호법 제30조.
 *
 * 처리방침의 항시 공개 의무 (제30조). ConsentModal 의 link 와 DisclaimerFooter
 * 의 link 모두 본 페이지를 가리킴. M0 release 전 변호사 자문 후 본문 최종
 * 확정 의무 (ADR-0006 D9).
 *
 * 본 페이지는 M0 의 1차 초안 — librarian 조사 + ADR-0006 D6 기반. 변호사
 * 자문 결과 (ADR-0019) 반영 시 본문 갱신.
 *
 * 관련:
 * - ADR-0006 D6.2 (처리방침 의무 항목)
 * - 개인정보보호법 제30조 (처리방침 항시 공개)
 * - Momus M0 review V2 (T46 fix) — AC-L-04 미통과 해결
 */

import Link from "next/link";

export const metadata = {
  title: "개인정보처리방침 — Speculum",
};

export default function PrivacyPage(): JSX.Element {
  return (
    <main className="mx-auto max-w-3xl px-6 py-10 text-sm leading-7 text-neutral-800">
      <Link
        href="/"
        className="text-xs text-neutral-500 hover:text-neutral-700"
      >
        ← 홈으로
      </Link>
      <h1 className="mt-3 text-2xl font-semibold text-neutral-900">
        개인정보처리방침
      </h1>
      <p className="mt-1 text-xs text-neutral-500">
        시행일: 2026-05-28 (M0 v0.1.0 — 변호사 자문 전 1차 초안)
      </p>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          1. 처리 목적
        </h2>
        <p className="mt-2">
          Speculum (이하 &ldquo;서비스&rdquo;) 은 다음 목적을 위해 이용자의
          개인정보를 처리합니다.
        </p>
        <ul className="mt-2 list-disc pl-6">
          <li>회원 인증 (Google OAuth 식별)</li>
          <li>사용자 데이터 (관심 종목, 조건셋, Screen Run snapshot) 저장</li>
          <li>서비스 이용 분석 (이용 통계, 오류 추적)</li>
        </ul>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          2. 처리 항목
        </h2>
        <ul className="mt-2 list-disc pl-6">
          <li>이메일 주소 (Google 계정)</li>
          <li>Google 프로필 이름·사진 URL</li>
          <li>Google 계정 식별자 (sub)</li>
          <li>이용자가 직접 입력한 데이터: 관심 종목 목록, 조건셋, 메모</li>
          <li>접속 로그: IP 주소, 접속 시각, 브라우저 정보 (오류 추적용)</li>
        </ul>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          3. 보유·이용 기간
        </h2>
        <p className="mt-2">
          개인정보는 다음 시점 중 빠른 시점에 즉시 파기합니다.
        </p>
        <ul className="mt-2 list-disc pl-6">
          <li>회원 탈퇴 요청 시</li>
          <li>1 년 이상 미접속 시</li>
        </ul>
        <p className="mt-2">
          파기 방식: 데이터베이스에서 물리적 삭제 (soft-delete 미사용).
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          4. 제 3 자 제공
        </h2>
        <p className="mt-2">
          이용자의 동의 없이 개인정보를 제 3 자에게 제공하지 않습니다. 단,
          관련 법령에 따라 수사 기관의 적법한 요청이 있는 경우 예외.
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          5. 처리 위탁 + 국외 이전
        </h2>
        <p className="mt-2">
          서비스 운영을 위해 다음 사업자에게 처리를 위탁하며, 이용자의 데이터는
          해외 서버에 저장됩니다 (ADR-0006 D6.5).
        </p>
        <table className="mt-3 w-full border-collapse text-xs">
          <thead>
            <tr className="border-b border-neutral-300 text-left">
              <th className="py-1 pr-3">수탁자</th>
              <th className="py-1 pr-3">위탁 업무</th>
              <th className="py-1">위치</th>
            </tr>
          </thead>
          <tbody className="text-neutral-700">
            <tr className="border-b border-neutral-100">
              <td className="py-1 pr-3">Vercel Inc.</td>
              <td className="py-1 pr-3">Frontend 호스팅</td>
              <td className="py-1">미국</td>
            </tr>
            <tr className="border-b border-neutral-100">
              <td className="py-1 pr-3">Fly.io / Railway</td>
              <td className="py-1 pr-3">Backend + Database 호스팅</td>
              <td className="py-1">미국</td>
            </tr>
            <tr>
              <td className="py-1 pr-3">Google LLC</td>
              <td className="py-1 pr-3">OAuth 인증</td>
              <td className="py-1">미국</td>
            </tr>
          </tbody>
        </table>
        <p className="mt-2">
          국외 이전에 대한 동의는 가입 시 별도 체크박스로 확인합니다 (개인정보
          보호법 제17조 제3항).
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          6. 정보주체의 권리
        </h2>
        <p className="mt-2">
          이용자는 언제든지 다음 권리를 행사할 수 있습니다 (개인정보보호법
          제35조·제36조·제37조).
        </p>
        <ul className="mt-2 list-disc pl-6">
          <li>
            <strong>열람</strong>: 본인 데이터 열람 — 서비스 내 &ldquo;내 정보&rdquo;
            페이지 (M1 합류).
          </li>
          <li>
            <strong>정정·삭제</strong>: 관심 종목·메모는 직접 수정/삭제 가능.
            이메일·이름은 Google 계정 의존이라 Google 에서 변경.
          </li>
          <li>
            <strong>처리 정지</strong>: 즉시 비활성화 옵션 (M1 합류).
          </li>
          <li>
            <strong>탈퇴</strong>: 즉시 데이터 파기 (M1 합류).
          </li>
        </ul>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          7. 만 14세 미만 정책
        </h2>
        <p className="mt-2">
          서비스는 만 14세 이상만 이용 가능합니다 (개인정보보호법 제22조의2).
          가입 시 본인이 만 14세 이상임을 확인하는 동의 항목이 있으며, 만 14세
          미만 가입은 차단됩니다.
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          8. 처리방침 변경
        </h2>
        <p className="mt-2">
          본 처리방침이 변경되는 경우 시행일 7 일 전 공지하며, 중요한 변경 시
          이용자에게 직접 통지합니다.
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          9. 처리 책임자
        </h2>
        <p className="mt-2">
          개인정보 처리에 관한 문의는 GitHub Issue 또는 SECURITY.md 의 보안 채널
          참조. M0 release 후 정식 문의 채널 공개 예정.
        </p>
      </section>

      <p className="mt-8 text-xs text-neutral-500">
        본 처리방침은 M0 v0.1.0 의 1차 초안입니다. 변호사 자문 (ADR-0019)
        결과에 따라 본문이 갱신될 수 있습니다.
      </p>
    </main>
  );
}
