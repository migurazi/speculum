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

import { getTranslations } from "next-intl/server";
import Link from "next/link";

export async function generateMetadata() {
  const t = await getTranslations("legal");
  return {
    title: t("terms.pageTitle"),
  };
}

export default async function TermsPage(): Promise<JSX.Element> {
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
        {t("terms.heading")}
      </h1>
      <p className="mt-1 text-xs text-neutral-500">
        {t("terms.effectiveDate")}
      </p>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("terms.article1Heading")}
        </h2>
        <p className="mt-2">
          {t("terms.article1Body")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("terms.article2Heading")}
        </h2>
        <ol className="mt-2 list-decimal space-y-2 pl-6">
          <li>{t("terms.article2Item1")}</li>
          <li>{t("terms.article2Item2")}</li>
          <li>{t("terms.article2Item3")}</li>
        </ol>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("terms.article3Heading")}
        </h2>
        <p className="mt-2">
          {t("terms.article3Body")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("terms.article4Heading")}
        </h2>
        <ol className="mt-2 list-decimal space-y-1 pl-6">
          <li>{t("terms.article4Item1")}</li>
          <li>{t("terms.article4Item2")}</li>
          <li>{t("terms.article4Item3")}</li>
        </ol>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("terms.article5Heading")}
        </h2>
        <ol className="mt-2 list-decimal space-y-1 pl-6">
          <li>{t("terms.article5Item1")}</li>
          <li>{t("terms.article5Item2")}</li>
          <li>{t("terms.article5Item3")}</li>
        </ol>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("terms.article6Heading")}
        </h2>
        <p className="mt-2">
          {t("terms.article6Body")}
        </p>
        <ul className="mt-2 list-disc pl-6">
          <li>{t("terms.article6Item1")}</li>
          <li>{t("terms.article6Item2")}</li>
          <li>{t("terms.article6Item3")}</li>
          <li>{t("terms.article6Item4")}</li>
        </ul>
        <p className="mt-2">
          {t("terms.article6Body2")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("terms.article7Heading")}
        </h2>
        <p className="mt-2">
          {t("terms.article7Body")}
        </p>
        <ul className="mt-2 list-disc pl-6">
          <li>{t("terms.article7Item1")}</li>
          <li>{t("terms.article7Item2")}</li>
          <li>{t("terms.article7Item3")}</li>
        </ul>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("terms.article8Heading")}
        </h2>
        <p className="mt-2">
          {t("terms.article8Body")}
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("terms.article9Heading")}
        </h2>
        <p className="mt-2">
          {t("terms.article9Body")}
        </p>
      </section>

      <p className="mt-8 text-xs text-neutral-500">
        {t("terms.footnote")}
      </p>
    </main>
  );
}
