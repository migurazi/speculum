/**
 * fetchFinancialHistory 단위 테스트 — M2 T82 Phase 2.
 *
 * 검증 항목:
 *   1. snake→camel 매핑 — vintage_seq→vintageSeq, effective_date→effectiveDate,
 *      is_active→isActive, superseded_by→supersededBy, ifrs_type→ifrsType,
 *      fiscal_period_filter→fiscalPeriodFilter, as_of→asOf.
 *   2. as_of / fiscal_period 쿼리 파라미터 전달.
 *   3. 쿼리 파라미터 미제공 → 빈 파라미터(as_of 없이 호출).
 *   4. supersededBy null 유지 — 현행 vintage 는 superseded_by=null 그대로.
 *   5. vintages 빈 배열 — 이력 없음 정상 처리.
 *   6. non-2xx 응답 — ApiError 전파.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchFinancialHistory } from "../financial-history";
import { ApiError } from "../client";

const MOCK_BASE_URL = "http://test.local";

function makeResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SAMPLE_WIRE = {
  code: "005930",
  as_of: "2024-08-30",
  fiscal_period_filter: null,
  vintages: [
    {
      vintage_seq: 1,
      effective_date: "2024-05-15",
      account: "revenue",
      value: "1000.0",
      unit: "krw",
      ifrs_type: "consolidated",
      is_active: false,
      superseded_by: "uuid-abc",
    },
    {
      vintage_seq: 2,
      effective_date: "2024-08-20",
      account: "revenue",
      value: "1050.0",
      unit: "krw",
      ifrs_type: "consolidated",
      is_active: true,
      superseded_by: null,
    },
  ],
};

const EMPTY_WIRE = {
  code: "000001",
  as_of: null,
  fiscal_period_filter: null,
  vintages: [],
};

describe("fetchFinancialHistory", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    process.env["NEXT_PUBLIC_API_BASE_URL"] = MOCK_BASE_URL;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.clearAllMocks();
  });

  it("snake→camel 매핑 — 최상위 필드", async () => {
    globalThis.fetch = vi.fn(async () =>
      makeResponse(SAMPLE_WIRE),
    ) as unknown as typeof fetch;

    const result = await fetchFinancialHistory("005930", { asOf: "2024-08-30" });

    expect(result.code).toBe("005930");
    expect(result.asOf).toBe("2024-08-30");
    expect(result.fiscalPeriodFilter).toBeNull();
  });

  it("vintage snake→camel — vintageSeq, effectiveDate, isActive, supersededBy, ifrsType", async () => {
    globalThis.fetch = vi.fn(async () =>
      makeResponse(SAMPLE_WIRE),
    ) as unknown as typeof fetch;

    const result = await fetchFinancialHistory("005930");

    expect(result.vintages).toHaveLength(2);

    const v1 = result.vintages[0]!;
    expect(v1.vintageSeq).toBe(1);
    expect(v1.effectiveDate).toBe("2024-05-15");
    expect(v1.isActive).toBe(false);
    expect(v1.supersededBy).toBe("uuid-abc");
    expect(v1.ifrsType).toBe("consolidated");

    const v2 = result.vintages[1]!;
    expect(v2.vintageSeq).toBe(2);
    expect(v2.effectiveDate).toBe("2024-08-20");
    expect(v2.isActive).toBe(true);
    expect(v2.supersededBy).toBeNull();
  });

  it("현행 vintage 의 supersededBy 가 null 로 유지됨", async () => {
    globalThis.fetch = vi.fn(async () =>
      makeResponse(SAMPLE_WIRE),
    ) as unknown as typeof fetch;

    const result = await fetchFinancialHistory("005930");
    const active = result.vintages.find((v) => v.isActive)!;

    expect(active.supersededBy).toBeNull();
  });

  it("값(value) 이 string 으로 그대로 유지됨 — number 변환 없음", async () => {
    globalThis.fetch = vi.fn(async () =>
      makeResponse(SAMPLE_WIRE),
    ) as unknown as typeof fetch;

    const result = await fetchFinancialHistory("005930");

    expect(result.vintages[0]!.value).toBe("1000.0");
    expect(result.vintages[1]!.value).toBe("1050.0");
  });

  it("as_of 쿼리 파라미터 전달", async () => {
    const fetchSpy = vi.fn(async () => makeResponse(SAMPLE_WIRE));
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    await fetchFinancialHistory("005930", { asOf: "2024-08-30" });

    const calls = fetchSpy.mock.calls as unknown as ReadonlyArray<
      [unknown, ...unknown[]]
    >;
    const calledUrl = String(calls[0]?.[0]);
    expect(calledUrl).toBe(
      `${MOCK_BASE_URL}/api/stocks/005930/financials/history?as_of=2024-08-30`,
    );
  });

  it("fiscal_period 쿼리 파라미터 전달", async () => {
    const fetchSpy = vi.fn(async () => makeResponse(SAMPLE_WIRE));
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    await fetchFinancialHistory("005930", { fiscalPeriod: "2023Q4" });

    const calls = fetchSpy.mock.calls as unknown as ReadonlyArray<
      [unknown, ...unknown[]]
    >;
    const calledUrl = String(calls[0]?.[0]);
    expect(calledUrl).toContain("fiscal_period=2023Q4");
  });

  it("파라미터 미제공 → 쿼리 파라미터 없이 호출", async () => {
    const fetchSpy = vi.fn(async () => makeResponse(SAMPLE_WIRE));
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    await fetchFinancialHistory("005930");

    const calls = fetchSpy.mock.calls as unknown as ReadonlyArray<
      [unknown, ...unknown[]]
    >;
    const calledUrl = String(calls[0]?.[0]);
    expect(calledUrl).toBe(
      `${MOCK_BASE_URL}/api/stocks/005930/financials/history`,
    );
  });

  it("vintages 빈 배열 — 이력 없음 정상 처리", async () => {
    globalThis.fetch = vi.fn(async () =>
      makeResponse(EMPTY_WIRE),
    ) as unknown as typeof fetch;

    const result = await fetchFinancialHistory("000001");

    expect(result.vintages).toHaveLength(0);
    expect(result.asOf).toBeNull();
  });

  it("non-2xx 응답 — ApiError 전파", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response("internal server error", { status: 500 }),
    ) as unknown as typeof fetch;

    await expect(fetchFinancialHistory("005930")).rejects.toThrow(ApiError);
  });
});
