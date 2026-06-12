/**
 * importCheckPack 매핑 단위 테스트 — ADR-0032 D2 (M4 #3 hash-mismatch).
 *
 * wire 의 `hash_mismatch`(snake) → result 의 `hashMismatch`(camel) 매핑 + 필드
 * 부재 시 false 기본(구버전 호환)을 검증한다. fetch 를 mock 해 네트워크 0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { importCheckPack } from "../factor-packs";

const MOCK_BASE_URL = "http://test.local";

function makeResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const _BASE_WIRE = {
  valid: true,
  issues: [],
  conflicts: [],
  clean: ["x:y"],
};

describe("importCheckPack — hash_mismatch 매핑 (ADR-0032 D2)", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    process.env["NEXT_PUBLIC_API_BASE_URL"] = MOCK_BASE_URL;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("hash_mismatch=true → hashMismatch=true", async () => {
    globalThis.fetch = vi.fn(
      async () => makeResponse({ ..._BASE_WIRE, hash_mismatch: true }),
    ) as unknown as typeof fetch;
    const result = await importCheckPack({});
    expect(result.hashMismatch).toBe(true);
    expect(result.valid).toBe(true);
  });

  it("hash_mismatch=false → hashMismatch=false", async () => {
    globalThis.fetch = vi.fn(
      async () => makeResponse({ ..._BASE_WIRE, hash_mismatch: false }),
    ) as unknown as typeof fetch;
    const result = await importCheckPack({});
    expect(result.hashMismatch).toBe(false);
  });

  it("필드 부재 → hashMismatch=false(구버전 호환)", async () => {
    globalThis.fetch = vi.fn(
      async () => makeResponse(_BASE_WIRE),
    ) as unknown as typeof fetch;
    const result = await importCheckPack({});
    expect(result.hashMismatch).toBe(false);
  });
});
