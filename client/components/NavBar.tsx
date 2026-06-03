"use client";

/**
 * NavBar — 모든 페이지의 header. ADR-0010 D1 의 mockup 의 상단 nav.
 *
 * 구조:
 *   [Speculum 로고] [종목 검색 placeholder] [AsOfDatePicker] [사용자]
 *   ──────────────────────────────────────────────────────────
 *   Screener / Stocks / Compare / Watchlist 4 link
 *
 * M0 의 4 link 는 placeholder href — T36 (Screener) / T37 (Detail) /
 * T38 (Compare) / T39 (Watchlist) 합류 시 실 navigation.
 *
 * 종목 검색 input 도 placeholder — T36/T37 의 lookup 구현 시 본격
 * navigation (Stock Detail 로 routing).
 *
 * 관련 ADR / 문서:
 * - ADR-0010 D1 (홈 화면 + nav layout).
 * - ADR-0008 D1.1 (header 의 AsOfDatePicker 항상 보임).
 * - M0_PLAN T35.
 */

import { useTranslations } from "next-intl";
import Link from "next/link";

import { AsOfDatePicker } from "@/components/AsOfDatePicker";
import { AuthButton } from "@/components/AuthButton";
import { cn } from "@/lib/utils";

// 라벨은 i18n 키로 분리 — href 는 라우팅 상수이므로 코드에 유지.
// labelKey 는 common 네임스페이스의 nav.* 키를 가리킨다.
const NAV_LINKS = [
  { href: "/screener", labelKey: "nav.screener" },
  { href: "/stock", labelKey: "nav.stocks" },
  { href: "/compare", labelKey: "nav.compare" },
  { href: "/watchlist", labelKey: "nav.watchlist" },
  { href: "/lab", labelKey: "nav.lab" },
  { href: "/backtest", labelKey: "nav.backtest" },
  { href: "/portfolio", labelKey: "nav.portfolio" },
  { href: "/tax", labelKey: "nav.tax" },
] as const;

interface NavBarProps {
  readonly className?: string;
}

export function NavBar({ className }: NavBarProps): JSX.Element {
  // 클라이언트 컴포넌트의 i18n 패턴 (Phase B 골든 샘플 ②):
  // useTranslations(네임스페이스) 로 t 를 얻어 t(key) 로 조회.
  // getTranslations(서버) 와 대비.
  const t = useTranslations("common");

  return (
    <header
      className={cn(
        "border-b border-neutral-200 bg-white",
        className,
      )}
    >
      {/* 상단 row: 로고 + 검색 + AsOfDatePicker */}
      <div className="mx-auto flex max-w-7xl items-center gap-4 px-6 py-3">
        <Link
          href="/"
          className="text-lg font-semibold text-neutral-900 hover:text-neutral-700"
        >
          {t("appName")}
        </Link>
        <div className="flex-1">
          {/* 종목 검색 placeholder — T36/T37 의 lookup 합류 시 실 input. */}
          <input
            type="search"
            placeholder={t("searchPlaceholder")}
            aria-label={t("searchAriaLabel")}
            disabled
            className="w-full max-w-md rounded-md border border-neutral-300 px-3 py-1.5 text-sm placeholder:text-neutral-400 disabled:cursor-not-allowed disabled:bg-neutral-50"
          />
        </div>
        <AsOfDatePicker />
        <AuthButton />
      </div>

      {/* 하단 row: 4 link navigation */}
      <nav
        className="mx-auto flex max-w-7xl gap-6 border-t border-neutral-100 px-6 py-2 text-sm"
        aria-label={t("primaryNavAriaLabel")}
      >
        {NAV_LINKS.map(({ href, labelKey }) => (
          <Link
            key={href}
            href={href}
            className="text-neutral-700 hover:text-neutral-900"
          >
            {t(labelKey)}
          </Link>
        ))}
      </nav>
    </header>
  );
}
