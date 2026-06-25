/**
 * NavBar active link 표시 — a11y(aria-current) + 시각 active 상태.
 *
 * 과거: nav link 에 active 표시가 시맨틱(aria-current)·시각 둘 다 전무 → 스크린
 * 리더 사용자가 현재 위치를 알 수 없고 일반 사용자도 현재 탭 구분 불가.
 *
 * 검증:
 *   1. isNavLinkActive — 정확 일치 / 하위 세그먼트 / 오탐 경계.
 *   2. 활성 경로의 link 에만 aria-current="page".
 *   3. 하위 경로(/stock/005930) → /stock link active.
 *
 * AuthButton(useSession) / AsOfDatePicker(store) 는 NavBar 테스트 대상이 아니라
 * mock 으로 대체 — provider 의존 제거.
 */

import { screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { isNavLinkActive, NavBar } from "@/components/NavBar";
import { renderWithIntl } from "@/test-utils/intl";

vi.mock("next/navigation", () => ({ usePathname: vi.fn() }));

vi.mock("@/components/AuthButton", () => ({
  AuthButton: () => <div data-testid="auth-button" />,
}));

vi.mock("@/components/AsOfDatePicker", () => ({
  AsOfDatePicker: () => <div data-testid="asof-picker" />,
}));

// StockSearch — NavBar 테스트 대상이 아니라 mock 으로 대체.
vi.mock("@/components/StockSearch", () => ({
  StockSearch: () => <div data-testid="stock-search" />,
}));

import { usePathname } from "next/navigation";

describe("isNavLinkActive", () => {
  it("정확 일치 → active", () => {
    expect(isNavLinkActive("/screener", "/screener")).toBe(true);
  });

  it("하위 세그먼트 → active", () => {
    expect(isNavLinkActive("/stock/005930", "/stock")).toBe(true);
  });

  it("세그먼트 경계 오탐 방지 (startsWith 만으로는 오탐)", () => {
    // "/taxfoo" 는 "/tax" link 를 active 로 만들면 안 됨.
    expect(isNavLinkActive("/taxfoo", "/tax")).toBe(false);
  });

  it("무관 경로 → 비active", () => {
    expect(isNavLinkActive("/watchlist", "/screener")).toBe(false);
  });
});

describe("NavBar 검색 영역 (T36/T37)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(usePathname).mockReturnValue("/screener");
  });

  it("StockSearch 컴포넌트가 NavBar 안에 렌더된다", () => {
    const { container } = renderWithIntl(<NavBar />);
    // StockSearch 는 mock 으로 data-testid="stock-search" 반환.
    const stockSearch = container.querySelector("[data-testid='stock-search']");
    expect(stockSearch).not.toBeNull();
  });
});

describe("NavBar active link 표시", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("활성 경로의 link 에만 aria-current=page", () => {
    vi.mocked(usePathname).mockReturnValue("/screener");
    const { container } = renderWithIntl(<NavBar />);

    const screenerLink = container.querySelector('a[href="/screener"]');
    expect(screenerLink).not.toBeNull();
    expect(screenerLink).toHaveAttribute("aria-current", "page");

    // 비활성 link 는 aria-current 부재.
    const watchlistLink = container.querySelector('a[href="/watchlist"]');
    expect(watchlistLink).not.toBeNull();
    expect(watchlistLink).not.toHaveAttribute("aria-current");
  });

  it("하위 경로(/stock/005930) → /stock link 가 active", () => {
    vi.mocked(usePathname).mockReturnValue("/stock/005930");
    const { container } = renderWithIntl(<NavBar />);

    const stockLink = container.querySelector('a[href="/stock"]');
    expect(stockLink).toHaveAttribute("aria-current", "page");
  });

  it("active link 는 정확히 1개 (현재 경로 외 비활성)", () => {
    vi.mocked(usePathname).mockReturnValue("/backtest");
    const { container } = renderWithIntl(<NavBar />);

    const currentLinks = container.querySelectorAll('a[aria-current="page"]');
    expect(currentLinks).toHaveLength(1);
    expect(currentLinks[0]).toHaveAttribute("href", "/backtest");
  });
});
