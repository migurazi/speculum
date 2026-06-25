/**
 * calendar.ts 단위 테스트.
 *
 * 검증 항목:
 *   1. clampAsOf — 하한 미달·범위 내·상한 초과·경계값(동일값).
 *   2. clampUpperBound — latestDataDate 있으면 그것, null 이면 latestBusinessDay.
 *   3. fetchCalendarCoverage — snake→camel 매핑 정확성, latestDataDate null 케이스,
 *      /api/calendar 경로 호출 확인, ApiError 전파.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clampAsOf, clampUpperBound, fetchCalendarCoverage } from "../calendar";
import { ApiError } from "../client";
import type { CalendarCoverage } from "../calendar";

const MOCK_BASE_URL = "http://test.local";

function makeResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// ── clampAsOf ──────────────────────────────────────────────────────────────

describe("clampAsOf", () => {
  it("하한 미달 → lower 반환", () => {
    expect(clampAsOf("2023-12-31", "2024-01-02", "2024-06-28")).toBe(
      "2024-01-02",
    );
  });

  it("범위 내 → asOf 그대로 반환", () => {
    expect(clampAsOf("2024-03-15", "2024-01-02", "2024-06-28")).toBe(
      "2024-03-15",
    );
  });

  it("상한 초과 → upper 반환", () => {
    expect(clampAsOf("2026-06-19", "2024-01-02", "2024-06-28")).toBe(
      "2024-06-28",
    );
  });

  it("경계값 하한 동일 → 변경 없음", () => {
    expect(clampAsOf("2024-01-02", "2024-01-02", "2024-06-28")).toBe(
      "2024-01-02",
    );
  });

  it("경계값 상한 동일 → 변경 없음", () => {
    expect(clampAsOf("2024-06-28", "2024-01-02", "2024-06-28")).toBe(
      "2024-06-28",
    );
  });
});

// ── clampUpperBound ────────────────────────────────────────────────────────

describe("clampUpperBound", () => {
  const base: CalendarCoverage = {
    minDate: "2024-01-01",
    maxDate: "2024-12-31",
    earliestBusinessDay: "2024-01-02",
    latestBusinessDay: "2024-12-30",
    latestDataDate: null,
    version: "1.0.0",
  };

  it("latestDataDate 있으면 그것을 상한으로", () => {
    const c: CalendarCoverage = { ...base, latestDataDate: "2024-06-28" };
    expect(clampUpperBound(c)).toBe("2024-06-28");
  });

  it("latestDataDate null 이면 latestBusinessDay 를 상한으로", () => {
    const c: CalendarCoverage = { ...base, latestDataDate: null };
    expect(clampUpperBound(c)).toBe("2024-12-30");
  });
});

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
