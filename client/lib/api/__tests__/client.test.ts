/**
 * fetchJson client + ApiError 단위 테스트 — T36.
 *
 * vi.fn 으로 globalThis.fetch mock. base URL composition / search params /
 * error normalization 검증.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, fetchJson } from "../client";

describe("fetchJson", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    // 명시 base URL — `NEXT_PUBLIC_API_BASE_URL` env var. process.env 의
    // mutation 은 test isolation 위해.
    process.env["NEXT_PUBLIC_API_BASE_URL"] = "http://test.local";
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("composes URL with base + path + search params", async () => {
    const fetchSpy = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    await fetchJson("/api/screen", {
      method: "POST",
      body: { conditions: [], selected_factors: [] },
      searchParams: { as_of: "2024-05-07" },
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const calls = fetchSpy.mock.calls as unknown as ReadonlyArray<[unknown, ...unknown[]]>;
    const calledUrl = String(calls[0]?.[0]);
    expect(calledUrl).toBe(
      "http://test.local/api/screen?as_of=2024-05-07",
    );
  });

  it("sets JSON content-type + accept headers", async () => {
    const fetchSpy = vi.fn(async () =>
      new Response("{}", { status: 200 }),
    );
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    await fetchJson("/api/screen", {
      method: "POST",
      body: {},
    });

    const calls = fetchSpy.mock.calls as unknown as ReadonlyArray<
      [unknown, RequestInit]
    >;
    const init = calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
    expect((init.headers as Record<string, string>)["Accept"]).toBe(
      "application/json",
    );
  });

  it("returns parsed JSON for 2xx response", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ result_codes: ["005930"], total: 1 }), {
        status: 200,
      }),
    ) as unknown as typeof fetch;

    const result = await fetchJson<{
      result_codes: string[];
      total: number;
    }>("/api/screen", { method: "POST" });
    expect(result.result_codes).toEqual(["005930"]);
    expect(result.total).toBe(1);
  });

  it("throws ApiError with status + body on non-2xx", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response("validation failed", { status: 422 }),
    ) as unknown as typeof fetch;

    await expect(fetchJson("/api/screen", { method: "POST" }))
      .rejects.toThrow(ApiError);

    try {
      await fetchJson("/api/screen", { method: "POST" });
    } catch (err) {
      expect((err as ApiError).status).toBe(422);
      expect((err as ApiError).body).toBe("validation failed");
    }
  });

  it("throws ApiError on JSON parse failure", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response("not json {{{", { status: 200 }),
    ) as unknown as typeof fetch;

    await expect(fetchJson("/api/screen")).rejects.toThrow(/JSON parse/);
  });
});
