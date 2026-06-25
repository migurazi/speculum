/**
 * fetchDisclosures 단위 테스트 — 정상 매핑 + 404 graceful degrade.
 *
 * backend 는 DART corp_code 매핑이 없는 종목에 404 를 던진다(대부분 종목이 crno
 * 매핑 미적재). 이는 정확성 결함이 아니라 가용성 부재 → 빈 목록으로 degrade 해야
 * 패널이 에러 배너가 아닌 "공시 없음" 빈 상태를 보인다(과거: undefined 크래시/에러).
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchDisclosures } from "../disclosures";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("fetchDisclosures", () => {
  it("정상 응답을 camelCase 도메인으로 매핑", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          code: "005930",
          as_of: "2026-06-25",
          disclosures: [
            {
              report_name: "사업보고서",
              rcept_date: "2026-03-30",
              dart_url: "https://dart.fss.or.kr/x",
            },
          ],
        }),
        { status: 200 },
      ),
    );
    const r = await fetchDisclosures("005930", { asOf: "2026-06-25" });
    expect(r.disclosures).toHaveLength(1);
    expect(r.disclosures[0]?.reportName).toBe("사업보고서");
    expect(r.disclosures[0]?.dartUrl).toBe("https://dart.fss.or.kr/x");
  });

  it("404(corp_code 매핑 부재)는 빈 목록으로 degrade — throw 안 함", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: "해당 종목의 DART corp_code 매핑을 찾을 수 없습니다.",
        }),
        { status: 404 },
      ),
    );
    const r = await fetchDisclosures("082850", { asOf: "2026-06-25" });
    expect(r.disclosures).toEqual([]);
    expect(r.code).toBe("082850");
  });

  it("502(DART 장애)는 그대로 throw — 외부 장애는 사용자에게 알림", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "DART 공시 조회 실패" }), {
        status: 502,
      }),
    );
    await expect(
      fetchDisclosures("005930", { asOf: "2026-06-25" }),
    ).rejects.toThrow();
  });
});
