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

import { getTranslations } from "next-intl/server";
import Link from "next/link";

export async function generateMetadata() {
  const t = await getTranslations("legal");
  return {
    title: t("privacy.pageTitle"),
  };
}

export default async function PrivacyPage(): Promise<JSX.Element> {
  // 서버 컴포넌트의 i18n 패턴: getTranslations(네임스페이스).
  const t = await getTranslations("legal");

  return (
    <main className="mx-auto max-w-3xl px-6 py-10 text-sm leading-7 text-neutral-800">
      <Link
        href="/"
        className="text-xs text-neutral-500 hover:text-neutral-700"
      >
        {t("backHome")}
      </Link>
      <h1 className="mt-3 text-2xl font-semibold text-neutral-900">
        {t("privacy.heading")}
      </h1>
      <p className="mt-1 text-xs text-neutral-500">
        {t("privacy.effectiveDate")}
      </p>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("privacy.section1Heading")}
        </h2>
        <p className="mt-2">
          {t("privacy.section1Body")}
        </p>
        <ul className="mt-2 list-disc pl-6">
          <li>{t("privacy.section1Item1")}</li>
          <li>{t("privacy.section1Item2")}</li>
          <li>{t("privacy.section1Item3")}</li>
        </ul>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("privacy.section2Heading")}
        </h2>
        <ul className="mt-2 list-disc pl-6">
          <li>{t("privacy.section2Item1")}</li>
          <li>{t("privacy.section2Item2")}</li>
          <li>{t("privacy.section2Item3")}</li>
          <li>{t("privacy.section2Item4")}</li>
          <li>{t("privacy.section2Item5")}</li>
        </ul>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("privacy.section3Heading")}
        </h2>
        <p className="mt-2">
          {t("privacy.section3Body")}
        </p>
        <ul className="mt-2 list-disc pl-6">
          <li>{t("privacy.section3Item1")}</li>
          <li>{t("privacy.section3Item2")}</li>
        </ul>
        <p className="mt-2">
          {t("privacy.section3Body2")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("privacy.section4Heading")}
        </h2>
        <p className="mt-2">
          {t("privacy.section4Body")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("privacy.section5Heading")}
        </h2>
        <p className="mt-2">
          {t("privacy.section5Body")}
        </p>
        <table className="mt-3 w-full border-collapse text-xs">
          <thead>
            <tr className="border-b border-neutral-300 text-left">
              <th className="py-1 pr-3">{t("privacy.section5TableHeaderTrustee")}</th>
              <th className="py-1 pr-3">{t("privacy.section5TableHeaderTask")}</th>
              <th className="py-1">{t("privacy.section5TableHeaderLocation")}</th>
            </tr>
          </thead>
          <tbody className="text-neutral-700">
            <tr className="border-b border-neutral-100">
              <td className="py-1 pr-3">{t("privacy.section5Row1Trustee")}</td>
              <td className="py-1 pr-3">{t("privacy.section5Row1Task")}</td>
              <td className="py-1">{t("privacy.section5Row1Location")}</td>
            </tr>
            <tr className="border-b border-neutral-100">
              <td className="py-1 pr-3">{t("privacy.section5Row2Trustee")}</td>
              <td className="py-1 pr-3">{t("privacy.section5Row2Task")}</td>
              <td className="py-1">{t("privacy.section5Row2Location")}</td>
            </tr>
            <tr>
              <td className="py-1 pr-3">{t("privacy.section5Row3Trustee")}</td>
              <td className="py-1 pr-3">{t("privacy.section5Row3Task")}</td>
              <td className="py-1">{t("privacy.section5Row3Location")}</td>
            </tr>
          </tbody>
        </table>
        <p className="mt-2">
          {t("privacy.section5Body2")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("privacy.section6Heading")}
        </h2>
        <p className="mt-2">
          {t("privacy.section6Body")}
        </p>
        <ul className="mt-2 list-disc pl-6">
          <li>
            <strong>{t("privacy.section6Item1Strong")}</strong>: {t("privacy.section6Item1")}
          </li>
          <li>
            <strong>{t("privacy.section6Item2Strong")}</strong>: {t("privacy.section6Item2")}
          </li>
          <li>
            <strong>{t("privacy.section6Item3Strong")}</strong>: {t("privacy.section6Item3")}
          </li>
          <li>
            <strong>{t("privacy.section6Item4Strong")}</strong>: {t("privacy.section6Item4")}
          </li>
        </ul>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("privacy.section7Heading")}
        </h2>
        <p className="mt-2">
          {t("privacy.section7Body")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("privacy.section8Heading")}
        </h2>
        <p className="mt-2">
          {t("privacy.section8Body")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("privacy.section9Heading")}
        </h2>
        <p className="mt-2">
          {t("privacy.section9Body")}
        </p>
      </section>

      <p className="mt-8 text-xs text-neutral-500">
        {t("privacy.footnote")}
      </p>
    </main>
  );
}
