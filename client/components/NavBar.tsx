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
import { usePathname } from "next/navigation";

import { AsOfDatePicker } from "@/components/AsOfDatePicker";
import { AuthButton } from "@/components/AuthButton";
import { StockSearch } from "@/components/StockSearch";
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

/**
 * 현재 경로가 해당 nav link 의 active 대상인지 판정.
 *
 * 정확 일치(`/screener`) 또는 하위 세그먼트(`/stock/005930` → `/stock`)를 active
 * 로 본다. 단순 startsWith 는 `/tax` 가 `/taxfoo` 를 오탐하므로 `${href}/` 접두로
 * 세그먼트 경계를 강제한다. (예외: href `/` 는 NAV_LINKS 에 없어 본 함수 무관.)
 */
export function isNavLinkActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function NavBar({ className }: NavBarProps): JSX.Element {
  // 클라이언트 컴포넌트의 i18n 패턴 (Phase B 골든 샘플 ②):
  // useTranslations(네임스페이스) 로 t 를 얻어 t(key) 로 조회.
  // getTranslations(서버) 와 대비.
  const t = useTranslations("common");
  // 현재 경로 — active nav link 판정용. usePathname 은 client 전용 hook
  // (NavBar 는 "use client"). 라우트 전환 시 re-render 되어 active 갱신.
  const pathname = usePathname();

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
          {/* 종목 검색 — StockSearch combobox (T36/T37). */}
          <StockSearch />
        </div>
        <AsOfDatePicker />
        <AuthButton />
      </div>

      {/* 하단 row: 4 link navigation */}
      <nav
        className="mx-auto flex max-w-7xl gap-6 border-t border-neutral-100 px-6 py-2 text-sm"
        aria-label={t("primaryNavAriaLabel")}
      >
        {NAV_LINKS.map(({ href, labelKey }) => {
          const active = isNavLinkActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              // 스크린리더에 현재 위치 노출 — 코드베이스 첫 aria-current 도입.
              aria-current={active ? "page" : undefined}
              // 시각 active 표시도 부재였음 — neutral grayscale 만 사용
              // (판단색 금지 컨벤션, ADR-0007/§2.2 일관).
              className={cn(
                "hover:text-neutral-900",
                active
                  ? "font-semibold text-neutral-900 underline underline-offset-4"
                  : "text-neutral-700",
              )}
            >
              {t(labelKey)}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
