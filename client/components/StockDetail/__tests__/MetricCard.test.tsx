/**
 * MetricCard 단위 테스트 — T37.
 *
 * 매트릭스:
 *   1. 정상 값 — name + value + SourceAttribution inline
 *   2. is_na — "N/A" 표시 + na_reason
 *   3. source inference — market-cap → KRX, per:* → DART
 *   4. unit 표시 (ratio 는 미표시, krw 같은 unit 은 표시)
 *   5. percent unit ×100 포맷 — 0.05 → "5.00%"
 *   6. N/A 유지 — percent 값이 null 이면 "N/A" (이중 변환 없음)
 *   7. source inference — price-return → KRX, dividend-yield → FSC
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

  // ─── percent unit ×100 포맷 (ADR-0035 D7 / M7 #6) ───────────────────────

  it("formats percent unit: 0.05 → '5.00%'", () => {
    const percentFactor: FactorValue = {
      canonical_id: "dividend-yield:trailing-annual",
      name: "배당수익률 (전년 기준)",
      unit: "percent",
      value: "0.05",
      is_na: false,
      na_reason: null,
      evaluator_version: "1.0.0",
    };
    renderWithIntl(<MetricCard factor={percentFactor} asOf="2024-09-30" />);
    expect(screen.getByText("5.00%")).toBeInTheDocument();
  });

  it("formats percent unit: 0.1234 → '12.34%'", () => {
    const percentFactor: FactorValue = {
      canonical_id: "price-return:total-annual",
      name: "총수익률 (1년, 배당 재투자)",
      unit: "percent",
      value: "0.1234",
      is_na: false,
      na_reason: null,
      evaluator_version: "1.0.0",
    };
    renderWithIntl(<MetricCard factor={percentFactor} asOf="2024-09-30" />);
    expect(screen.getByText("12.34%")).toBeInTheDocument();
  });

  it("renders N/A for percent factor when value is null", () => {
    const naPercentFactor: FactorValue = {
      canonical_id: "dividend-yield:trailing-annual",
      name: "배당수익률 (전년 기준)",
      unit: "percent",
      value: null,
      is_na: true,
      na_reason: "배당 데이터 없음",
      evaluator_version: "1.0.0",
    };
    renderWithIntl(<MetricCard factor={naPercentFactor} asOf="2024-09-30" />);
    expect(screen.getByText("N/A")).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("hides unit suffix for percent (formatted inline, no separate suffix)", () => {
    const percentFactor: FactorValue = {
      canonical_id: "price-return:total-annual",
      name: "총수익률 (1년, 배당 재투자)",
      unit: "percent",
      value: "0.05",
      is_na: false,
      na_reason: null,
      evaluator_version: "1.0.0",
    };
    renderWithIntl(<MetricCard factor={percentFactor} asOf="2024-09-30" />);
    // "percent" 텍스트는 suffix 로 노출되지 않음.
    expect(screen.queryByText("percent")).not.toBeInTheDocument();
  });

  // ─── source inference — M7 #6 복합 출처 §2.8 ────────────────────────────

  it("infers compound source KRX·FSC for price-return factor (§2.8)", () => {
    // price-return = 가격(KRX) + 배당재투자(FSC) 복합 출처.
    const prFactor: FactorValue = {
      canonical_id: "price-return:total-annual",
      name: "총수익률 (1년, 배당 재투자)",
      unit: "percent",
      value: "0.10",
      is_na: false,
      na_reason: null,
      evaluator_version: "1.0.0",
    };
    renderWithIntl(<MetricCard factor={prFactor} asOf="2024-09-30" />);
    expect(screen.getByText(/KRX·FSC · 2024-09-30/)).toBeInTheDocument();
  });

  it("infers compound source KRX·FSC for dividend-yield factor (§2.8)", () => {
    // dividend-yield = 배당(FSC) / 가격(KRX) 복합 출처.
    const dyFactor: FactorValue = {
      canonical_id: "dividend-yield:trailing-annual",
      name: "배당수익률 (전년 기준)",
      unit: "percent",
      value: "0.03",
      is_na: false,
      na_reason: null,
      evaluator_version: "1.0.0",
    };
    renderWithIntl(<MetricCard factor={dyFactor} asOf="2024-09-30" />);
    expect(screen.getByText(/KRX·FSC · 2024-09-30/)).toBeInTheDocument();
  });
});
