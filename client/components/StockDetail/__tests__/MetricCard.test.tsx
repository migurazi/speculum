/**
 * MetricCard 단위 테스트 — T37.
 *
 * 매트릭스:
 *   1. 정상 값 — name + value + SourceAttribution inline
 *   2. is_na — "N/A" 표시 + na_reason
 *   3. source inference — market-cap → KRX, per:* → DART
 *   4. unit 표시 (ratio 는 미표시, krw 같은 unit 은 표시)
 */

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MetricCard } from "../MetricCard";
import type { FactorValue } from "@/lib/api/stocks";
import { renderWithIntl } from "@/test-utils/intl";

const BASE_FACTOR: FactorValue = {
  canonical_id: "per:ttm-consolidated-ifrs",
  name: "PER (TTM, 연결)",
  unit: "ratio",
  value: "12.34",
  is_na: false,
  na_reason: null,
  evaluator_version: "1.0.0",
};

describe("MetricCard", () => {
  it("renders factor name + value", () => {
    renderWithIntl(<MetricCard factor={BASE_FACTOR} asOf="2024-09-30" />);
    expect(screen.getByText("PER (TTM, 연결)")).toBeInTheDocument();
    expect(screen.getByText("12.34")).toBeInTheDocument();
  });

  it("shows canonical_id at bottom", () => {
    renderWithIntl(<MetricCard factor={BASE_FACTOR} asOf="2024-09-30" />);
    expect(
      screen.getByText("per:ttm-consolidated-ifrs"),
    ).toBeInTheDocument();
  });

  it("renders 'N/A' when is_na=true", () => {
    const naFactor: FactorValue = {
      ...BASE_FACTOR,
      value: null,
      is_na: true,
      na_reason: "분모가 0 — 자기자본 음수",
    };
    renderWithIntl(<MetricCard factor={naFactor} asOf="2024-09-30" />);
    expect(screen.getByText("N/A")).toBeInTheDocument();
    expect(screen.getByText("분모가 0 — 자기자본 음수")).toBeInTheDocument();
  });

  it("renders 'N/A' when value=null even if is_na=false", () => {
    const nullFactor: FactorValue = {
      ...BASE_FACTOR,
      value: null,
      is_na: false,
    };
    renderWithIntl(<MetricCard factor={nullFactor} asOf="2024-09-30" />);
    expect(screen.getByText("N/A")).toBeInTheDocument();
  });

  it("infers source=KRX for market-cap factor", () => {
    const marketCapFactor: FactorValue = {
      canonical_id: "market-cap:ex-treasury",
      name: "시가총액 (자사주 제외)",
      unit: "krw",
      value: "70000000000000",
      is_na: false,
      na_reason: null,
      evaluator_version: "1.0.0",
    };
    renderWithIntl(<MetricCard factor={marketCapFactor} asOf="2024-09-30" />);
    // inline suffix 의 source = "KRX".
    expect(screen.getByText(/KRX · 2024-09-30/)).toBeInTheDocument();
  });

  it("infers source=DART for per/pbr/roe factors", () => {
    renderWithIntl(<MetricCard factor={BASE_FACTOR} asOf="2024-09-30" />);
    expect(screen.getByText(/DART · 2024-09-30/)).toBeInTheDocument();
  });

  it("shows unit suffix for non-ratio units", () => {
    const krwFactor: FactorValue = {
      ...BASE_FACTOR,
      unit: "krw",
      value: "70000",
    };
    renderWithIntl(<MetricCard factor={krwFactor} asOf="2024-09-30" />);
    expect(screen.getByText("krw")).toBeInTheDocument();
  });

  it("hides unit when ratio", () => {
    renderWithIntl(<MetricCard factor={BASE_FACTOR} asOf="2024-09-30" />);
    expect(screen.queryByText("ratio")).not.toBeInTheDocument();
  });
});
