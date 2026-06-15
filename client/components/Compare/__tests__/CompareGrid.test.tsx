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

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithIntl } from "@/test-utils/intl";
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
    renderWithIntl(<CompareGrid stocks={[]} asOf="2024-09-30" />);
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
    renderWithIntl(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
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
    renderWithIntl(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
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
    renderWithIntl(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    // 두 종목 모두 N/A.
    expect(screen.getAllByText("N/A").length).toBe(2);
    expect(screen.getAllByText("자기자본 음수").length).toBe(2);
  });

  it("infers KRX source for market-cap factor", () => {
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [MARKET_CAP] }),
      makeStock({ id: "s2", code: "000660", factors: [MARKET_CAP] }),
    ];
    renderWithIntl(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    // SourceAttribution inline suffix.
    expect(screen.getAllByText(/KRX · 2024-09-30/).length).toBeGreaterThanOrEqual(1);
  });

  it("infers DART source for per factor", () => {
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [PER] }),
      makeStock({ id: "s2", code: "000660", factors: [PER] }),
    ];
    renderWithIntl(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    expect(screen.getAllByText(/DART · 2024-09-30/).length).toBeGreaterThanOrEqual(1);
  });

  it("renders '—' placeholder when factor missing in one stock", () => {
    // 종목 A 는 PER + MARKET_CAP, 종목 B 는 PER 만 → MARKET_CAP row 에서 종목 B 셀이 "—".
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [PER, MARKET_CAP] }),
      makeStock({ id: "s2", code: "000660", factors: [PER] }),
    ];
    renderWithIntl(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
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
    renderWithIntl(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    expect(screen.getByText("거래 중")).toBeInTheDocument();
    expect(screen.getByText("상장폐지")).toBeInTheDocument();
  });

  // oracle T38 L3 — factor row 순서가 stocks[] 입력 순서에 비결정적이지 않은지.
  it("renders factor rows in deterministic order independent of stock input order", () => {
    const factorsAlpha = [PER, MARKET_CAP]; // canonical: per:* / market-cap:*
    const factorsReversed = [MARKET_CAP, PER];

    const { unmount } = renderWithIntl(
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

    renderWithIntl(
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

  // M7 #6 — percent factor ×100 표시 일관성 (CompareCell).
  it("formats percent unit factor ×100 in compare cell", () => {
    const percentFactor: FactorValue = {
      canonical_id: "dividend-yield:trailing-annual",
      name: "배당수익률",
      unit: "percent",
      value: "0.035",
      is_na: false,
      na_reason: null,
      evaluator_version: "1.0.0",
    };
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [percentFactor] }),
      makeStock({ id: "s2", code: "000660", factors: [{ ...percentFactor, value: "0.02" }] }),
    ];
    renderWithIntl(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    // 0.035 → "3.50%", 0.02 → "2.00%"
    expect(screen.getAllByText("3.50%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("2.00%").length).toBeGreaterThanOrEqual(1);
    // raw ratio 값이 표시되지 않아야 함.
    expect(screen.queryByText("0.035")).not.toBeInTheDocument();
  });

  // M7 #6 — compound source KRX·FSC 표시 (price-return / dividend-yield).
  it("shows compound source KRX·FSC for price-return factor (§2.8)", () => {
    const prFactor: FactorValue = {
      canonical_id: "price-return:total-annual",
      name: "총수익률",
      unit: "percent",
      value: "0.10",
      is_na: false,
      na_reason: null,
      evaluator_version: "1.0.0",
    };
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [prFactor] }),
      makeStock({ id: "s2", code: "000660", factors: [prFactor] }),
    ];
    renderWithIntl(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    expect(screen.getAllByText(/KRX·FSC · 2024-09-30/).length).toBeGreaterThanOrEqual(1);
  });

  // T-3 회귀 가드 — factor.value 가 계약 위반으로 null 이어도 crash 없음.
  it("renders empty string (not crash) when value is null but is_na=false (contract violation)", () => {
    const brokenFactor: FactorValue = {
      canonical_id: "per:ttm-consolidated-ifrs",
      name: "PER",
      unit: "ratio",
      value: null, // is_na=false 인데 value=null — 계약 위반 데이터
      is_na: false,
      na_reason: null,
      evaluator_version: "1.0.0",
    };
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [brokenFactor] }),
    ];
    // crash 없이 렌더 — N/A 가드가 value===null 도 처리.
    expect(() => {
      renderWithIntl(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    }).not.toThrow();
    // is_na=false, value=null → 상위 가드(value===null) 에 걸려 N/A 표시.
    expect(screen.getByText("N/A")).toBeInTheDocument();
  });

  // oracle T38 L3 — column header 가 a11y scope="col" 보유.
  it("sets scope='col' on all column headers", () => {
    const stocks = [
      makeStock({ id: "s1", code: "005930", factors: [PER] }),
      makeStock({ id: "s2", code: "000660", factors: [PER] }),
    ];
    renderWithIntl(<CompareGrid stocks={stocks} asOf="2024-09-30" />);
    const colHeaders = document.querySelectorAll(
      "thead th[scope='col']",
    );
    // "지표" + stock 2 = 3 column header.
    expect(colHeaders.length).toBe(3);
  });
});
