/**
 * 면책조항 페이지 — ADR-0006 D7 + ADR-0007 D2.
 *
 * DisclaimerFooter 가 모든 페이지에 표시하는 짧은 disclaimer 의 전문 version.
 * 자본시장법 유사투자자문업 회피 + 데이터 정확성 한계 + 8 기둥 §2.2 No Advice
 * 의 사용자 인지.
 *
 * 관련:
 * - ADR-0006 D2 (자본시장법 회피의 법적 implementation)
 * - ADR-0007 D2 (모든 화면 footer disclaimer 의무)
 * - Momus M0 review V2 (T46 fix) — AC-L-04 미통과 해결
 */

import { getTranslations } from "next-intl/server";
import Link from "next/link";

export async function generateMetadata() {
  const t = await getTranslations("legal");
  return {
    title: t("disclaimer.pageTitle"),
  };
}

export default async function DisclaimerPage(): Promise<JSX.Element> {
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
        {t("disclaimer.heading")}
      </h1>
      <p className="mt-1 text-xs text-neutral-500">
        {t("disclaimer.effectiveDate")}
      </p>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("disclaimer.section1Heading")}
        </h2>
        <p className="mt-2">
          {t("disclaimer.section1Body1Pre")}{" "}
          <strong>{t("disclaimer.section1Body1Strong")}</strong>
          {t("disclaimer.section1Body1Post")}
        </p>
        <p className="mt-2">
          {t("disclaimer.section1Body2Pre")}{" "}
          <strong>
            {t("disclaimer.section1Body2Strong")}
          </strong>{" "}
          {t("disclaimer.section1Body2Post")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("disclaimer.section2Heading")}
        </h2>
        <p className="mt-2">
          {t("disclaimer.section2Body1Pre")}{" "}
          <strong>
            {t("disclaimer.section2Body1Strong")}
          </strong>
          {t("disclaimer.section2Body1Post")}
        </p>
        <p className="mt-2">
          {t("disclaimer.section2Body2")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("disclaimer.section3Heading")}
        </h2>
        <p className="mt-2">
          {t("disclaimer.section3Body1Pre")}{" "}
          <strong>{t("disclaimer.section3Body1Strong")}</strong>
          {t("disclaimer.section3Body1Post")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("disclaimer.section4Heading")}
        </h2>
        <p className="mt-2">
          {t("disclaimer.section4Body")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("disclaimer.section5Heading")}
        </h2>
        <p className="mt-2">
          {t("disclaimer.section5Body")}
        </p>
        <ul className="mt-2 list-disc pl-6">
          <li>{t("disclaimer.section5Item1")}</li>
          <li>{t("disclaimer.section5Item2")}</li>
          <li>{t("disclaimer.section5Item3")}</li>
          <li>{t("disclaimer.section5Item4")}</li>
        </ul>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("disclaimer.section6Heading")}
        </h2>
        <ul className="mt-2 list-disc pl-6">
          <li>
            <Link
              href="/privacy"
              className="font-medium text-neutral-900 underline"
            >
              {t("disclaimer.section6LinkPrivacy")}
            </Link>
          </li>
          <li>
            <Link
              href="/terms"
              className="font-medium text-neutral-900 underline"
            >
              {t("disclaimer.section6LinkTerms")}
            </Link>
          </li>
        </ul>
      </section>

      <p className="mt-8 text-xs text-neutral-500">
        {t("disclaimer.footnote")}
      </p>
    </main>
  );
}
