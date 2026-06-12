/**
 * 사용 안내 페이지 — `/guide`.
 *
 * 홈(app/page.tsx) 의 "가이드" 링크(ADR-0010 D2.4) 가 가리키는 페이지. M0 에서는
 * link placeholder(404) 였던 것을 M1 에서 실제 페이지로 채운다. 도구의 사용
 * 방식과 8 기둥 원칙(PIT·재현성·출처 표시·No Advice)을 사용자에게 설명.
 *
 * No Advice(8 기둥 §2.2): 본 페이지 텍스트도 추천·매수/매도 신호·점수/랭킹 어휘를
 * 쓰지 않는다("추천하지 않" 같은 부정 맥락만 allowed_phrases 로 허용). 도구가
 * 무엇을 하고 무엇을 하지 않는지 사실로만 안내.
 *
 * 서버 컴포넌트 + getTranslations("guide") — disclaimer/terms 페이지와 동일 패턴.
 *
 * 관련:
 * - ADR-0010 D2.4 (홈 가이드 entry point)
 * - ADR-0007 (No Advice UI 규약)
 * - CONCEPT §2.2 No Advice / §2.3 Active Inspection / §2.4 PIT
 */

import { getTranslations } from "next-intl/server";
import Link from "next/link";

export async function generateMetadata() {
  const t = await getTranslations("guide");
  return {
    title: t("pageTitle"),
  };
}

export default async function GuidePage(): Promise<JSX.Element> {
  // 서버 컴포넌트의 i18n 패턴: getTranslations(네임스페이스).
  const t = await getTranslations("guide");

  return (
    <main className="mx-auto max-w-3xl px-6 py-10 text-sm leading-7 text-neutral-800">
      <Link
        href="/"
        className="text-xs text-neutral-500 hover:text-neutral-700"
      >
        {t("backHome")}
      </Link>
      <h1 className="mt-3 text-2xl font-semibold text-neutral-900">
        {t("heading")}
      </h1>
      <p className="mt-3">{t("intro")}</p>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("section1Heading")}
        </h2>
        <p className="mt-2">{t("section1Body")}</p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("section2Heading")}
        </h2>
        <p className="mt-2">{t("section2Body")}</p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("section3Heading")}
        </h2>
        <p className="mt-2">{t("section3Body")}</p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("section4Heading")}
        </h2>
        <p className="mt-2">{t("section4Body")}</p>
        <ul className="mt-2 list-disc pl-6">
          <li>{t("section4Item1")}</li>
          <li>{t("section4Item2")}</li>
          <li>{t("section4Item3")}</li>
          <li>{t("section4Item4")}</li>
        </ul>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("relatedHeading")}
        </h2>
        <ul className="mt-2 list-disc pl-6">
          <li>
            <Link
              href="/disclaimer"
              className="font-medium text-neutral-900 underline"
            >
              {t("relatedDisclaimer")}
            </Link>
          </li>
          <li>
            <Link
              href="/terms"
              className="font-medium text-neutral-900 underline"
            >
              {t("relatedTerms")}
            </Link>
          </li>
          <li>
            <Link
              href="/privacy"
              className="font-medium text-neutral-900 underline"
            >
              {t("relatedPrivacy")}
            </Link>
          </li>
        </ul>
      </section>

      <p className="mt-8 text-xs text-neutral-500">{t("footnote")}</p>
    </main>
  );
}
