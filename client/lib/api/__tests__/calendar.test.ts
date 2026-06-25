/**
 * calendar.ts 단위 테스트.
 *
 * 검증 항목:
 *   1. fetchCalendarCoverage — snake→camel 매핑 정확성, latestDataDate null 케이스,
 *      /api/calendar 경로 호출 확인, ApiError 전파.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchCalendarCoverage } from "../calendar";
import { ApiError } from "../client";

const MOCK_BASE_URL = "http://test.local";

function makeResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// ── fetchCalendarCoverage ──────────────────────────────────────────────────

describe("fetchCalendarCoverage", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    process.env["NEXT_PUBLIC_API_BASE_URL"] = MOCK_BASE_URL;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  const FULL_WIRE = {
    min_date: "2024-01-01",
    max_date: "2024-12-31",
    earliest_business_day: "2024-01-02",
    latest_business_day: "2024-12-30",
    latest_data_date: "2024-06-28",
    version: "1.0.0",
    content_hash: "sha256:abc123",
  };

  it("snake→camel 매핑 정확성", async () => {
    globalThis.fetch = vi.fn(
      async () => makeResponse(FULL_WIRE),
    ) as unknown as typeof fetch;

    const result = await fetchCalendarCoverage();

    expect(result.minDate).toBe("2024-01-01");
    expect(result.maxDate).toBe("2024-12-31");
    expect(result.earliestBusinessDay).toBe("2024-01-02");
    expect(result.latestBusinessDay).toBe("2024-12-30");
    expect(result.latestDataDate).toBe("2024-06-28");
    expect(result.version).toBe("1.0.0");
  });

  it("content_hash 는 결과에 없음 (매핑 제외)", async () => {
    globalThis.fetch = vi.fn(
      async () => makeResponse(FULL_WIRE),
    ) as unknown as typeof fetch;

    const result = await fetchCalendarCoverage();

    // content_hash 키가 결과에 없어야 함.
    expect(Object.keys(result)).not.toContain("contentHash");
    expect(Object.keys(result)).not.toContain("content_hash");
  });

  it("latestDataDate null 케이스 — null 그대로 유지", async () => {
    const wireWithNull = { ...FULL_WIRE, latest_data_date: null };
    globalThis.fetch = vi.fn(
      async () => makeResponse(wireWithNull),
    ) as unknown as typeof fetch;

    const result = await fetchCalendarCoverage();

    expect(result.latestDataDate).toBeNull();
  });

  it("/api/calendar 경로로 GET 요청", async () => {
    const fetchSpy = vi.fn(async () => makeResponse(FULL_WIRE));
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    await fetchCalendarCoverage();

    const calls =
      fetchSpy.mock.calls as unknown as ReadonlyArray<[unknown, ...unknown[]]>;
    const calledUrl = String(calls[0]?.[0]);
    expect(calledUrl).toBe(`${MOCK_BASE_URL}/api/calendar`);
  });

  it("non-2xx 응답 — ApiError 전파", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response("not found", { status: 404 }),
    ) as unknown as typeof fetch;

    await expect(fetchCalendarCoverage()).rejects.toThrow(ApiError);
  });
});
