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

import Link from "next/link";

import { AsOfDatePicker } from "@/components/AsOfDatePicker";
import { cn } from "@/lib/utils";

const NAV_LINKS = [
  { href: "/screener", label: "Screener" },
  { href: "/stock", label: "Stocks" },
  { href: "/compare", label: "Compare" },
  { href: "/watchlist", label: "Watchlist" },
] as const;

interface NavBarProps {
  readonly className?: string;
}

export function NavBar({ className }: NavBarProps): JSX.Element {
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
          Speculum
        </Link>
        <div className="flex-1">
          {/* 종목 검색 placeholder — T36/T37 의 lookup 합류 시 실 input. */}
          <input
            type="search"
            placeholder="종목명 또는 코드 검색"
            aria-label="종목 검색"
            disabled
            className="w-full max-w-md rounded-md border border-neutral-300 px-3 py-1.5 text-sm placeholder:text-neutral-400 disabled:cursor-not-allowed disabled:bg-neutral-50"
          />
        </div>
        <AsOfDatePicker />
      </div>

      {/* 하단 row: 4 link navigation */}
      <nav
        className="mx-auto flex max-w-7xl gap-6 border-t border-neutral-100 px-6 py-2 text-sm"
        aria-label="주 navigation"
      >
        {NAV_LINKS.map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            className="text-neutral-700 hover:text-neutral-900"
          >
            {label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
