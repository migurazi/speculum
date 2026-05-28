/**
 * inferFactorSource 단위 테스트 — T38 oracle L1 회귀 가드.
 *
 * MetricCard 와 CompareGrid 가 동일 함수 호출 — drift 발생 시 본 테스트 실패.
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
