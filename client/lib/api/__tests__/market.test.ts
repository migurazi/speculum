/**
 * fetchMarketOverview 단위 테스트 — M1 T61.
 *
 * 검증 항목:
 *   1. snake→camel 매핑 정확성 (as_of→asOf, universe_count→universeCount,
 *      market_breakdown→marketBreakdown, canonical_id→canonicalId,
 *      na_count→naCount).
 *   2. as_of 쿼리 파라미터 전달.
 *   3. null 통계값 그대로 null 유지 (number 변환 안 함).
 *   4. universeCount=0 (빈 유니버스) 정상 처리.
 *   5. count=0 인 factor 의 통계값 null 처리.
 *   6. ApiError 전파 — non-2xx 응답.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchMarketOverview } from "../market";
import { ApiError } from "../client";

const MOCK_BASE_URL = "http://test.local";

function makeResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("fetchMarketOverview", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    process.env["NEXT_PUBLIC_API_BASE_URL"] = MOCK_BASE_URL;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  const FULL_WIRE = {
    as_of: "2024-05-07",
    universe_count: 3,
    market_breakdown: [
      { market: "KOSPI", count: 2 },
      { market: "KOSDAQ", count: 1 },
    ],
    factors: [
      {
        canonical_id: "market-cap:ex-treasury",
        name: "시가총액 (자사주 제외)",
        unit: "원",
        count: 0,
        na_count: 3,
        mean: null,
        median: null,
        p25: null,
        p75: null,
        min: null,
        max: null,
      },
      {
        canonical_id: "eps:basic-ttm-consolidated-ifrs",
        name: "EPS (기본, TTM, 연결, K-IFRS)",
        unit: "원",
        count: 3,
        na_count: 0,
        mean: "8000.000000",
        median: "8000.000000",
        p25: "6000.000000",
        p75: "10000.000000",
        min: "4000",
        max: "12000",
      },
    ],
  };

  it("snake→camel 매핑 — 최상위 필드", async () => {
    globalThis.fetch = vi.fn(async () => makeResponse(FULL_WIRE)) as unknown as typeof fetch;

    const result = await fetchMarketOverview("2024-05-07");

    expect(result.asOf).toBe("2024-05-07");
    expect(result.universeCount).toBe(3);
  });

  it("marketBreakdown 배열 정확 매핑", async () => {
    globalThis.fetch = vi.fn(async () => makeResponse(FULL_WIRE)) as unknown as typeof fetch;

    const result = await fetchMarketOverview("2024-05-07");

    expect(result.marketBreakdown).toHaveLength(2);
    expect(result.marketBreakdown[0]).toEqual({ market: "KOSPI", count: 2 });
    expect(result.marketBreakdown[1]).toEqual({ market: "KOSDAQ", count: 1 });
  });

  it("factors snake→camel — canonicalId, naCount 매핑", async () => {
    globalThis.fetch = vi.fn(async () => makeResponse(FULL_WIRE)) as unknown as typeof fetch;

    const result = await fetchMarketOverview("2024-05-07");

    expect(result.factors[0]?.canonicalId).toBe("market-cap:ex-treasury");
    expect(result.factors[0]?.naCount).toBe(3);
    expect(result.factors[1]?.canonicalId).toBe("eps:basic-ttm-consolidated-ifrs");
    expect(result.factors[1]?.naCount).toBe(0);
  });

  it("null 통계값 — count=0 factor 의 모든 통계가 null 유지", async () => {
    globalThis.fetch = vi.fn(async () => makeResponse(FULL_WIRE)) as unknown as typeof fetch;

    const result = await fetchMarketOverview("2024-05-07");
    const nullFactor = result.factors[0]!;

    expect(nullFactor.count).toBe(0);
    expect(nullFactor.mean).toBeNull();
    expect(nullFactor.median).toBeNull();
    expect(nullFactor.p25).toBeNull();
    expect(nullFactor.p75).toBeNull();
    expect(nullFactor.min).toBeNull();
    expect(nullFactor.max).toBeNull();
  });

  it("통계값 string 그대로 유지 — number 변환 없음", async () => {
    globalThis.fetch = vi.fn(async () => makeResponse(FULL_WIRE)) as unknown as typeof fetch;

    const result = await fetchMarketOverview("2024-05-07");
    const epsFactor = result.factors[1]!;

    expect(epsFactor.mean).toBe("8000.000000");
    expect(epsFactor.median).toBe("8000.000000");
    expect(epsFactor.p25).toBe("6000.000000");
    expect(epsFactor.p75).toBe("10000.000000");
    expect(epsFactor.min).toBe("4000");
    expect(epsFactor.max).toBe("12000");
  });

  it("as_of 쿼리 파라미터 전달", async () => {
    const fetchSpy = vi.fn(async () => makeResponse(FULL_WIRE));
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    await fetchMarketOverview("2024-05-07");

    const calls = fetchSpy.mock.calls as unknown as ReadonlyArray<[unknown, ...unknown[]]>;
    const calledUrl = String(calls[0]?.[0]);
    expect(calledUrl).toBe(`${MOCK_BASE_URL}/api/market-overview?as_of=2024-05-07`);
  });

  it("빈 유니버스 — universeCount=0, 배열 비어있음", async () => {
    const emptyWire = {
      as_of: "2024-05-07",
      universe_count: 0,
      market_breakdown: [],
      factors: [],
    };
    globalThis.fetch = vi.fn(async () => makeResponse(emptyWire)) as unknown as typeof fetch;

    const result = await fetchMarketOverview("2024-05-07");

    expect(result.universeCount).toBe(0);
    expect(result.marketBreakdown).toHaveLength(0);
    expect(result.factors).toHaveLength(0);
  });

  it("non-2xx 응답 — ApiError 전파", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response("internal server error", { status: 500 }),
    ) as unknown as typeof fetch;

    await expect(fetchMarketOverview("2024-05-07")).rejects.toThrow(ApiError);
  });
});
