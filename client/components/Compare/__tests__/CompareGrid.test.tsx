/**
 * CompareGrid 단위 테스트 — T38.
 *
 * 매트릭스:
 *   1. 빈 배열 — placeholder 표시
 *   2. 2 종목 — header 2 + factor row 렌더
 *   3. factor union — 종목 A 에만 있는 factor 가 종목 B 컬럼에 "—"
 *   4. N/A 셀 — na_reason 표시
 *   5. SourceAttribution — KRX (market-cap) vs DART (per) 분기
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CompareGrid } from "../CompareGrid";
import type { FactorValue, StockDetail } from "@/lib/api/stocks";

const PER: FactorValue = {
  canonical_id: "per:ttm-consolidated-ifrs",
  name: "PER",
  unit: "ratio",
  value: "12.34",
  is_na: false,
  na_reason: null,
  evaluator_version: "1.0.0",
};

const MARKET_CAP: FactorValue = {
  canonical_id: "market-cap:ex-treasury",
  name: "시가총액",
  unit: "krw",
  value: "70000000000000",
  is_na: false,
  na_reason: null,
  evaluator_version: "1.0.0",
};

const ROE_NA: FactorValue = {
  canonical_id: "roe:ttm-consolidated-ifrs",
  name: "ROE",
  unit: "pct",
  value: null,
  is_na: true,
  na_reason: "자기자본 음수",
  evaluator_version: "1.0.0",
};

function makeStock(
  overrides: Partial<StockDetail> & { id: string; code: string },
): StockDetail {
  return {
    id: overrides.id,
    code: overrides.code,
    name: overrides.name ?? `종목-${overrides.code}`,
    market: overrides.market ?? "KOSPI",
    listing_date: overrides.listing_date ?? "2000-01-01",
    delisting_date: overrides.delisting_date ?? null,
    fiscal_month: overrides.fiscal_month ?? 12,
    ifrs_preference: overrides.ifrs_preference ?? "consolidated",
    status: overrides.status ?? "active",
    code_history: overrides.code_history ?? [],
    factors: overrides.factors ?? [],
  };
}

describe("CompareGrid", () => {
  it("renders placeholder when stocks array empty", () => {
    render(<CompareGrid stocks={[]} asOf="2024-09-30" />);
    expect(screen.getByText("표시할 종목이 없습니다.")).toBeInTheDocument();
  });

  it("renders header for each stock + name + code", () => {
    const stocks = [
      makeStock({
        id: "s1",
        code: "005930",
        name: "삼성전자",
        factors: [PER],
      }),
      makeStock({
        id: "s2",
        code: "000660",
        name: "SK하이닉스",
        factors: [PER],
      }),
    ];
    render(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    expect(screen.getByText("삼성전자")).toBeInTheDocument();
    expect(screen.getByText("SK하이닉스")).toBeInTheDocument();
    expect(screen.getByText("005930")).toBeInTheDocument();
    expect(screen.getByText("000660")).toBeInTheDocument();
  });

  it("renders factor row with values from each stock", () => {
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [PER] }),
      makeStock({
        id: "s2",
        code: "000660",
        factors: [{ ...PER, value: "8.91" }],
      }),
    ];
    render(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    // factor name in row header (한 번).
    expect(screen.getByText("PER")).toBeInTheDocument();
    // 양 종목 값.
    expect(screen.getAllByText("12.34").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("8.91").length).toBeGreaterThanOrEqual(1);
  });

  it("shows N/A + na_reason for is_na factor", () => {
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [ROE_NA] }),
      makeStock({ id: "s2", code: "000660", factors: [ROE_NA] }),
    ];
    render(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    // 두 종목 모두 N/A.
    expect(screen.getAllByText("N/A").length).toBe(2);
    expect(screen.getAllByText("자기자본 음수").length).toBe(2);
  });

  it("infers KRX source for market-cap factor", () => {
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [MARKET_CAP] }),
      makeStock({ id: "s2", code: "000660", factors: [MARKET_CAP] }),
    ];
    render(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    // SourceAttribution inline suffix.
    expect(screen.getAllByText(/KRX · 2024-09-30/).length).toBeGreaterThanOrEqual(1);
  });

  it("infers DART source for per factor", () => {
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [PER] }),
      makeStock({ id: "s2", code: "000660", factors: [PER] }),
    ];
    render(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    expect(screen.getAllByText(/DART · 2024-09-30/).length).toBeGreaterThanOrEqual(1);
  });

  it("renders '—' placeholder when factor missing in one stock", () => {
    // 종목 A 는 PER + MARKET_CAP, 종목 B 는 PER 만 → MARKET_CAP row 에서 종목 B 셀이 "—".
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [PER, MARKET_CAP] }),
      makeStock({ id: "s2", code: "000660", factors: [PER] }),
    ];
    render(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders status badge label for each stock", () => {
    const stocks = [
      makeStock({ id: "s1", code: "005930", status: "active", factors: [PER] }),
      makeStock({
        id: "s2",
        code: "999999",
        status: "delisted",
        factors: [PER],
      }),
    ];
    render(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    expect(screen.getByText("거래 중")).toBeInTheDocument();
    expect(screen.getByText("상장폐지")).toBeInTheDocument();
  });

  // oracle T38 L3 — factor row 순서가 stocks[] 입력 순서에 비결정적이지 않은지.
  it("renders factor rows in deterministic order independent of stock input order", () => {
    const factorsAlpha = [PER, MARKET_CAP]; // canonical: per:* / market-cap:*
    const factorsReversed = [MARKET_CAP, PER];

    const { unmount } = render(
      <CompareGrid
        stocks={[
          makeStock({ id: "s1", code: "005930", factors: factorsAlpha }),
          makeStock({ id: "s2", code: "000660", factors: factorsAlpha }),
        ]}
        asOf="2024-09-30"
      />,
    );
    const firstOrderRows = Array.from(
      document.querySelectorAll("tbody th[scope='row']"),
    ).map((el) => el.querySelector(".font-mono")?.textContent ?? "");
    unmount();

    render(
      <CompareGrid
        stocks={[
          makeStock({ id: "s1", code: "005930", factors: factorsReversed }),
          makeStock({ id: "s2", code: "000660", factors: factorsReversed }),
        ]}
        asOf="2024-09-30"
      />,
    );
    const secondOrderRows = Array.from(
      document.querySelectorAll("tbody th[scope='row']"),
    ).map((el) => el.querySelector(".font-mono")?.textContent ?? "");

    // 입력 순서 무관 — 동일 (사전식: market-cap:* < per:*).
    expect(secondOrderRows).toEqual(firstOrderRows);
    expect(firstOrderRows).toEqual([
      "market-cap:ex-treasury",
      "per:ttm-consolidated-ifrs",
    ]);
  });

  // oracle T38 L3 — column header 가 a11y scope="col" 보유.
  it("sets scope='col' on all column headers", () => {
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [PER] }),
      makeStock({ id: "s2", code: "000660", factors: [PER] }),
    ];
    render(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    const colHeaders = document.querySelectorAll(
      "thead th[scope='col']",
    );
    // "지표" + stock 2 = 3 column header.
    expect(colHeaders.length).toBe(3);
  });
});
