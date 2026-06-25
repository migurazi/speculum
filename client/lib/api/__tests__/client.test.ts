/**
 * fetchJson client + ApiError 단위 테스트 — T36.
 *
 * vi.fn 으로 globalThis.fetch mock. base URL composition / search params /
 * error normalization 검증.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, extractApiErrorCode, extractApiErrorDetail, fetchJson } from "../client";

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

  it("returns undefined (void) for empty 2xx body without throwing", async () => {
    // 빈 body — DELETE 류 endpoint(서버가 200 + 빈 본문). JSON.parse 호출 안 하고
    // void(undefined) 반환(T-2 계약). caller 는 fetchJson<void>(...) 로 호출.
    // (jsdom Response 는 204 의 본문을 금지하므로 200+빈본문으로 빈 body 경로 검증.)
    globalThis.fetch = vi.fn(async () =>
      new Response("", { status: 200 }),
    ) as unknown as typeof fetch;

    const result = await fetchJson<void>("/api/watchlists/x", {
      method: "DELETE",
    });
    expect(result).toBeUndefined();
  });

  it("returns undefined for empty body when called without type argument", async () => {
    // 타입인자 생략 시 오버로드가 Promise<void> 로 해소 — 빈 body 정상 통과.
    globalThis.fetch = vi.fn(async () =>
      new Response("", { status: 200 }),
    ) as unknown as typeof fetch;

    const result = await fetchJson("/api/noop", { method: "POST" });
    expect(result).toBeUndefined();
  });

  it("throws ApiError on JSON parse failure", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response("not json {{{", { status: 200 }),
    ) as unknown as typeof fetch;

    await expect(fetchJson("/api/screen")).rejects.toThrow(/JSON parse/);
  });
});

describe("extractApiErrorDetail", () => {
  it("ApiError body 에서 string detail 추출", () => {
    const err = new ApiError(400, "API 400 Bad Request", JSON.stringify({ detail: "기준일 범위 초과" }));
    expect(extractApiErrorDetail(err)).toBe("기준일 범위 초과");
  });

  it("422 ValidationError: detail 배열의 msg 들을 '; ' 로 join", () => {
    const body = JSON.stringify({
      detail: [
        { loc: ["body", "conditions"], msg: "field required", type: "missing" },
        { loc: ["body", "selected_factors"], msg: "value is not a valid list", type: "type_error" },
      ],
    });
    const err = new ApiError(422, "API 422 Unprocessable Entity", body);
    expect(extractApiErrorDetail(err)).toBe("field required; value is not a valid list");
  });

  it("body 가 JSON 이 아닌 경우 err.message fallback", () => {
    const err = new ApiError(500, "API 500 Internal Server Error", "plain text body");
    expect(extractApiErrorDetail(err)).toBe("API 500 Internal Server Error");
  });

  it("detail 배열에 msg 없는 entry 는 건너뜀", () => {
    const body = JSON.stringify({ detail: [{ loc: ["x"] }] });
    const err = new ApiError(422, "API 422", body);
    // msg 없는 entry 만 있으면 배열 분기 실패 → message fallback.
    expect(extractApiErrorDetail(err)).toBe("API 422");
  });

  it("ApiError 가 아닌 Error — message 반환", () => {
    const err = new Error("네트워크 오류");
    expect(extractApiErrorDetail(err)).toBe("네트워크 오류");
  });

  it("ApiError 가 아닌 문자열 — string 변환 반환", () => {
    expect(extractApiErrorDetail("unexpected string error")).toBe("unexpected string error");
  });
});

describe("extractApiErrorCode", () => {
  it("body 에 code 필드가 있으면 반환", () => {
    const body = JSON.stringify({ detail: "범위 초과", code: "AS_OF_OUT_OF_RANGE" });
    const err = new ApiError(400, "API 400 Bad Request", body);
    expect(extractApiErrorCode(err)).toBe("AS_OF_OUT_OF_RANGE");
  });

  it("body 에 code 필드 없으면 null", () => {
    const body = JSON.stringify({ detail: "some error" });
    const err = new ApiError(400, "API 400 Bad Request", body);
    expect(extractApiErrorCode(err)).toBeNull();
  });

  it("body 가 JSON 이 아니면 null", () => {
    const err = new ApiError(500, "Internal Server Error", "not json");
    expect(extractApiErrorCode(err)).toBeNull();
  });

  it("ApiError 가 아닌 경우 null", () => {
    expect(extractApiErrorCode(new Error("오류"))).toBeNull();
    expect(extractApiErrorCode("string error")).toBeNull();
    expect(extractApiErrorCode(null)).toBeNull();
  });
});
