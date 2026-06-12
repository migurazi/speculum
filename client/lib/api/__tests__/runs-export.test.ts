/**
 * exportRun + reproduceRun API client 단위 테스트 — M2 T80 Phase 2.
 *
 * 검증 항목:
 *   1. exportRun — wire snake_case → camelCase 매핑 정확성.
 *   2. exportRun — 올바른 endpoint 호출 (GET /api/runs/{id}/export).
 *   3. exportRun — non-2xx → ApiError 전파.
 *   4. reproduceRun — wire snake_case → camelCase 매핑 (matches/codes/hash/note).
 *   5. reproduceRun — POST /api/runs/reproduce 호출 + body 전달.
 *   6. reproduceRun — note: null 그대로 유지.
 *   7. reproduceRun — non-2xx → ApiError 전파.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { exportRun, reproduceRun } from "../runs";
import { ApiError } from "../client";

const MOCK_BASE_URL = "http://test.local";

function makeResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const EXPORT_WIRE = {
  export_format: "speculum-screen-run-export-v1",
  snapshot_schema_version: 1,
  run: {
    run_id: "aaaa-bbbb",
    conditions: [{ factor: "per:ttm-consolidated-ifrs", op: "<", value: "10" }],
    selected_factors: ["per:ttm-consolidated-ifrs"],
    as_of: "2024-09-30",
    result_codes: ["005930", "000660"],
    result_hash: "deadbeef",
    data_versions: { factor_pack: "v1.0" },
  },
};

const REPRODUCE_WIRE = {
  matches: true,
  reproduced_result_codes: ["005930", "000660"],
  original_result_codes: ["005930", "000660"],
  result_hash: "deadbeef",
  note: null,
};

const REPRODUCE_WIRE_MISMATCH = {
  matches: false,
  reproduced_result_codes: ["005930"],
  original_result_codes: ["005930", "000660"],
  result_hash: "cafebabe",
  note: "버전 불일치",
};

describe("exportRun", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    process.env["NEXT_PUBLIC_API_BASE_URL"] = MOCK_BASE_URL;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("wire snake_case → camelCase 매핑 — 최상위 필드", async () => {
    globalThis.fetch = vi.fn(async () => makeResponse(EXPORT_WIRE)) as unknown as typeof fetch;

    const result = await exportRun("aaaa-bbbb");

    expect(result.exportFormat).toBe("speculum-screen-run-export-v1");
    expect(result.snapshotSchemaVersion).toBe(1);
  });

  it("run 필드 매핑 — runId, resultCodes, resultHash, dataVersions", async () => {
    globalThis.fetch = vi.fn(async () => makeResponse(EXPORT_WIRE)) as unknown as typeof fetch;

    const result = await exportRun("aaaa-bbbb");

    expect(result.run.runId).toBe("aaaa-bbbb");
    expect(result.run.resultCodes).toEqual(["005930", "000660"]);
    expect(result.run.resultHash).toBe("deadbeef");
    expect(result.run.dataVersions).toEqual({ factor_pack: "v1.0" });
    expect(result.run.asOf).toBe("2024-09-30");
    expect(result.run.selectedFactors).toEqual(["per:ttm-consolidated-ifrs"]);
  });

  it("GET /api/runs/{id}/export — 올바른 endpoint URL", async () => {
    const fetchSpy = vi.fn(async () => makeResponse(EXPORT_WIRE));
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    await exportRun("aaaa-bbbb");

    const calls = fetchSpy.mock.calls as unknown as ReadonlyArray<[unknown, ...unknown[]]>;
    expect(String(calls[0]?.[0])).toBe(
      `${MOCK_BASE_URL}/api/runs/aaaa-bbbb/export`,
    );
  });

  it("non-2xx → ApiError 전파", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response("unauthorized", { status: 401 }),
    ) as unknown as typeof fetch;

    await expect(exportRun("aaaa-bbbb")).rejects.toThrow(ApiError);
  });
});

describe("reproduceRun", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    process.env["NEXT_PUBLIC_API_BASE_URL"] = MOCK_BASE_URL;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("wire snake_case → camelCase 매핑 — matches 일치 케이스", async () => {
    globalThis.fetch = vi.fn(async () => makeResponse(REPRODUCE_WIRE)) as unknown as typeof fetch;

    const result = await reproduceRun({ some: "payload" });

    expect(result.matches).toBe(true);
    expect(result.reproducedResultCodes).toEqual(["005930", "000660"]);
    expect(result.originalResultCodes).toEqual(["005930", "000660"]);
    expect(result.resultHash).toBe("deadbeef");
    expect(result.note).toBeNull();
  });

  it("wire snake_case → camelCase 매핑 — matches 불일치 케이스 + note", async () => {
    globalThis.fetch = vi.fn(async () => makeResponse(REPRODUCE_WIRE_MISMATCH)) as unknown as typeof fetch;

    const result = await reproduceRun({ some: "payload" });

    expect(result.matches).toBe(false);
    expect(result.reproducedResultCodes).toEqual(["005930"]);
    expect(result.originalResultCodes).toEqual(["005930", "000660"]);
    expect(result.resultHash).toBe("cafebabe");
    expect(result.note).toBe("버전 불일치");
  });

  it("POST /api/runs/reproduce — 올바른 endpoint + body 전달", async () => {
    const fetchSpy = vi.fn(async () => makeResponse(REPRODUCE_WIRE));
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    const payload = { exportFormat: "speculum-screen-run-export-v1" };
    await reproduceRun(payload);

    const calls = fetchSpy.mock.calls as unknown as ReadonlyArray<[unknown, RequestInit]>;
    expect(String(calls[0]?.[0])).toBe(`${MOCK_BASE_URL}/api/runs/reproduce`);
    expect(JSON.parse(calls[0]?.[1]?.body as string)).toEqual(payload);
  });

  it("non-2xx → ApiError 전파", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response("server error", { status: 500 }),
    ) as unknown as typeof fetch;

    await expect(reproduceRun({})).rejects.toThrow(ApiError);
  });
});
