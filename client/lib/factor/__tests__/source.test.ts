/**
 * inferFactorSource 단위 테스트 — T38 oracle L1 회귀 가드.
 *
 * MetricCard 와 CompareGrid 가 동일 함수 호출 — drift 발생 시 본 테스트 실패.
 *
 * M7 #6 (§2.8 Conformance): 복합 출처 factor(price-return·dividend-yield)는
 * SourceLabel[] 반환 — 출처 누락 0 보장.
 */

import { describe, expect, it } from "vitest";

import { inferFactorSource } from "../source";

describe("inferFactorSource", () => {
  it("returns KRX for market-cap namespace", () => {
    expect(inferFactorSource("market-cap:ex-treasury")).toBe("KRX");
    expect(inferFactorSource("market-cap:common")).toBe("KRX");
  });

  it("returns KRX for price namespace", () => {
    expect(inferFactorSource("price:close")).toBe("KRX");
  });

  it("returns KRX for volume-turnover namespace", () => {
    expect(inferFactorSource("volume-turnover:avg-20d")).toBe("KRX");
  });

  it("returns [KRX, FSC] compound for price-return namespace (§2.8)", () => {
    // price-return = 가격(KRX) + 배당재투자(FSC) — 복합 출처 의무 표기.
    expect(inferFactorSource("price-return:total-annual")).toEqual(["KRX", "FSC"]);
  });

  it("returns [KRX, FSC] compound for dividend-yield namespace (§2.8)", () => {
    // dividend-yield = 배당(FSC) / 가격(KRX) — 복합 출처 의무 표기.
    expect(inferFactorSource("dividend-yield:trailing-annual")).toEqual(["KRX", "FSC"]);
  });

  it("returns DART for per/pbr/roe/eps namespaces", () => {
    expect(inferFactorSource("per:ttm-consolidated-ifrs")).toBe("DART");
    expect(inferFactorSource("pbr:trailing-consolidated")).toBe("DART");
    expect(inferFactorSource("roe:ttm-consolidated-ifrs")).toBe("DART");
    expect(inferFactorSource("eps:trailing")).toBe("DART");
  });

  it("falls back to DART for unknown prefix", () => {
    expect(inferFactorSource("unknown-factor:variant")).toBe("DART");
    expect(inferFactorSource("anything-else:x")).toBe("DART");
  });

  it("falls back to DART for empty canonical_id", () => {
    expect(inferFactorSource("")).toBe("DART");
  });

  it("falls back to DART for no-namespace string", () => {
    expect(inferFactorSource("nofactor")).toBe("DART");
  });
});
