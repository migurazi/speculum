/**
 * Speculum 홈 페이지 — ADR-0010 D1 의 Watchlist + 빠른 시작 + 가이드.
 *
 * 8 기둥 §2.3 Active Inspection — 빌트인 위젯 0, 사용자가 의도 표시 후
 * 데이터 표시. ADR-0010 D2 의 "오늘의 종목 TOP" 같은 의제 설정 위젯 미존재.
 *
 * scope:
 *   - Watchlist 영역 (M0 빈 placeholder — Watchlist 도 비어있다는 가정).
 *     T39 Watchlist 뷰 합류 시 실 data fetch.
 *   - 시장 통계 — `GET /api/market-overview`(집계 통계) + `/market` 뷰는 M1 에서
 *     구현. 홈은 빌트인 위젯 없이 QUICK_LINKS 의 `/market` link 만 노출(§2.3
 *     Active Inspection — 사용자가 진입할 때만 fetch, 의제 설정 위젯 0).
 *   - 빠른 시작 navigation (5 뷰: screener/stock/compare/watchlist/market) —
 *     ADR-0010 D2.3.
 *   - 가이드 link (`/guide`, M1 구현) — ADR-0010 D2.4.
 *
 * 관련 ADR / 문서:
 * - ADR-0010 (홈 화면 정체성).
 * - 8 기둥 §2.3 (Active Inspection).
 * - M0_PLAN T35.
 */

import { getTranslations } from "next-intl/server";
import Link from "next/link";

// href 는 라우팅 상수, key 는 home 네임스페이스의 quickLinks.<key> 를 가리킨다
// (title / description 두 하위 키). 라벨 문자열은 messages/ko/home.json 에 위치.
const QUICK_LINKS = [
  { href: "/screener", key: "screener" },
  { href: "/stock", key: "stock" },
  { href: "/compare", key: "compare" },
  { href: "/watchlist", key: "watchlist" },
  { href: "/market", key: "market" },
] as const;

export default async function HomePage(): Promise<JSX.Element> {
  // 서버 컴포넌트의 i18n 패턴: getTranslations(네임스페이스).
  const t = await getTranslations("home");

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      {/* Watchlist 영역 — ADR-0010 D2.1.
          M0 = 빈 placeholder. T39 cycle 의 실 fetch 합류 시 component 화. */}
      <section className="rounded-lg border border-neutral-200 p-6">
        <h2 className="text-base font-semibold text-neutral-900">
          {t("watchlistHeading")}
        </h2>
        <p className="mt-2 text-sm text-neutral-600">
          {t("watchlistEmpty")}
        </p>
      </section>

      {/* 빠른 시작 navigation — ADR-0010 D2.3. */}
      <section className="mt-8">
        <h2 className="text-sm font-medium uppercase tracking-wide text-neutral-500">
          {t("quickStartHeading")}
        </h2>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {QUICK_LINKS.map(({ href, key }) => (
            <Link
              key={href}
              href={href}
              className="rounded-lg border border-neutral-200 p-4 transition-colors hover:border-neutral-400 hover:bg-neutral-50"
            >
              <div className="text-sm font-medium text-neutral-900">
                {t(`quickLinks.${key}.title`)}
              </div>
              <div className="mt-1 text-xs text-neutral-600">
                {t(`quickLinks.${key}.description`)}
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* 가이드 — ADR-0010 D2.4. M0 = link placeholder. */}
      <section className="mt-8 rounded-lg border border-neutral-200 bg-neutral-50 px-6 py-4 text-sm text-neutral-700">
        <p>
          {t("guideIntro")}{" "}
          <Link
            href="/guide"
            className="font-medium text-neutral-900 underline-offset-2 hover:underline"
          >
            {t("guideLink")}
          </Link>
        </p>
      </section>
    </main>
  );
}
