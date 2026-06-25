/**
 * Stock 검색 랜딩(`/stock` index) 회귀 테스트.
 *
 * NavBar 의 "종목" 링크가 `/stock` 으로 가는데 index 페이지가 없어 404 가 뜨던
 * 버그(route 부재)의 가드. 본 페이지가 존재하고, 검색 진입점 + 대표 종목
 * 바로가기(`/stock/[code]`)를 렌더하는지 검증한다.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import StockLandingPage from "@/app/stock/page";
import { renderWithIntl } from "@/test-utils/intl";

// StockSearch 가 useRouter 를 쓰므로 next/navigation mock(렌더만 — push 미발화).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrap = ({ children }: { readonly children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderWithIntl(wrap({ children: <StockLandingPage /> }));
}

describe("StockLandingPage (/stock index)", () => {
  it("검색 헤딩과 입력(combobox)을 렌더한다", () => {
    renderPage();
    // 실 ko 문자열(stock.landing.heading) — h1 으로 표시.
    expect(
      screen.getByRole("heading", { name: "종목 검색" }),
    ).toBeInTheDocument();
    // StockSearch 의 combobox 진입점.
    expect(screen.getByRole("combobox")).toBeInTheDocument();
  });

  it("대표 종목 바로가기가 /stock/{code} 로 링크된다", () => {
    renderPage();
    const samsung = screen.getByRole("link", { name: /삼성전자/ });
    expect(samsung).toHaveAttribute("href", "/stock/005930");
    // 네 개의 quick link 모두 동적 상세 경로를 가리킨다(404 였던 route 의 실 진입).
    for (const code of ["005930", "000660", "035420", "005380"]) {
      const link = screen
        .getAllByRole("link")
        .find((el) => el.getAttribute("href") === `/stock/${code}`);
      expect(link, `quick link /stock/${code} 누락`).toBeDefined();
    }
  });
});
