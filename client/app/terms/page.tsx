/**
 * 이용약관 페이지 — ADR-0006 D7 + AC-L-04.
 *
 * M0 의 1차 초안 — 변호사 자문 (ADR-0019) 후 본문 최종 확정.
 *
 * 관련:
 * - ADR-0006 D6 (회원제 처리 정책)
 * - ADR-0006 D7 (이용약관 권장 형식)
 * - Momus M0 review V2 (T46 fix) — AC-L-04 미통과 해결
 */

import Link from "next/link";

export const metadata = {
  title: "이용약관 — Speculum",
};

export default function TermsPage(): JSX.Element {
  return (
    <main className="mx-auto max-w-3xl px-6 py-10 text-sm leading-7 text-neutral-800">
      <Link
        href="/"
        className="text-xs text-neutral-500 hover:text-neutral-700"
      >
        ← 홈으로
      </Link>
      <h1 className="mt-3 text-2xl font-semibold text-neutral-900">
        이용약관
      </h1>
      <p className="mt-1 text-xs text-neutral-500">
        시행일: 2026-05-28 (M0 v0.1.0 — 변호사 자문 전 1차 초안)
      </p>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          제 1 조 (목적)
        </h2>
        <p className="mt-2">
          본 약관은 Speculum (이하 &ldquo;서비스&rdquo;) 의 이용 조건과 절차,
          이용자와 운영자의 권리·의무 및 책임 사항을 정함을 목적으로 합니다.
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          제 2 조 (서비스의 성격)
        </h2>
        <ol className="mt-2 list-decimal space-y-2 pl-6">
          <li>
            서비스는 한국 주식 시장의 정량 데이터 (재무 지표, 시세, 통계 등) 를
            탐색하는 도구입니다.
          </li>
          <li>
            서비스는 자본시장법상 투자 자문업 또는 유사투자자문업이 아니며,
            특정 종목의 매매·종목·시기에 대한 자문을 제공하지 않습니다.
          </li>
          <li>
            서비스에 표시되는 모든 정보는 정보 제공 목적이며, 투자 권유나
            종목 추천이 아닙니다.
          </li>
        </ol>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          제 3 조 (이용 자격)
        </h2>
        <p className="mt-2">
          본 서비스는 만 14세 이상이 이용할 수 있습니다 (개인정보보호법 제22조
          의2). 만 14세 미만의 이용은 제한됩니다.
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          제 4 조 (이용자의 의무)
        </h2>
        <ol className="mt-2 list-decimal space-y-1 pl-6">
          <li>이용자는 본 약관 및 관련 법령을 준수해야 합니다.</li>
          <li>
            이용자는 본 서비스의 데이터를 영리 목적으로 재배포하거나 자동화된
            방법으로 대량 수집해서는 안 됩니다.
          </li>
          <li>
            이용자는 본인 책임 하에 서비스를 이용하며, 투자 결정 및 그 결과에
            대한 책임은 이용자 본인에게 있습니다.
          </li>
        </ol>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          제 5 조 (운영자의 면책)
        </h2>
        <ol className="mt-2 list-decimal space-y-1 pl-6">
          <li>
            운영자는 서비스에 표시되는 데이터의 정확성·완전성을 보증하지
            않습니다.
          </li>
          <li>
            운영자는 이용자가 서비스의 정보를 이용함으로써 발생한 손실에 대해
            법적 책임을 지지 않습니다.
          </li>
          <li>
            운영자는 외부 데이터 출처 (DART, KRX, 한국은행 등) 의 일시적 장애 ·
            데이터 변경 · 서비스 중단으로 인한 불편에 대해 책임을 지지 않습니다.
          </li>
        </ol>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          제 6 조 (데이터 출처)
        </h2>
        <p className="mt-2">
          서비스는 다음 1차 자료를 사용합니다 (ADR-0006 D5).
        </p>
        <ul className="mt-2 list-disc pl-6">
          <li>금융감독원 전자공시시스템 (DART)</li>
          <li>한국거래소 (KRX) — 직접 또는 pykrx 라이브러리 경유</li>
          <li>한국은행 경제통계시스템 (ECOS) — M1 합류 예정</li>
          <li>FinanceDataReader 라이브러리 (검증 출처)</li>
        </ul>
        <p className="mt-2">
          각 출처의 라이선스는 출처 표시 의무를 따릅니다.
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          제 7 조 (이용 제한)
        </h2>
        <p className="mt-2">
          운영자는 다음 경우 사전 통지 없이 이용을 제한하거나 회원 자격을
          정지할 수 있습니다.
        </p>
        <ul className="mt-2 list-disc pl-6">
          <li>본 약관 또는 관련 법령 위반</li>
          <li>자동화된 대량 요청 (서비스 안정성 침해)</li>
          <li>다른 이용자의 권리 침해</li>
        </ul>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          제 8 조 (약관 변경)
        </h2>
        <p className="mt-2">
          본 약관이 변경되는 경우 시행일 7 일 전 서비스 내에 공지합니다.
          이용자가 변경된 약관에 동의하지 않는 경우 이용 중단·탈퇴할 수
          있습니다.
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          제 9 조 (준거법 + 분쟁 해결)
        </h2>
        <p className="mt-2">
          본 약관은 대한민국 법률을 따르며, 본 서비스 이용으로 발생한 분쟁은
          민사소송법상 관할 법원에 의합니다.
        </p>
      </section>

      <p className="mt-8 text-xs text-neutral-500">
        본 이용약관은 M0 v0.1.0 의 1차 초안입니다. 변호사 자문 (ADR-0019)
        결과에 따라 본문이 갱신될 수 있습니다.
      </p>
    </main>
  );
}
