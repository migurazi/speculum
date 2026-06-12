/**
 * pack-io 테스트 — exportPackJson / parsePackJson.
 *
 * import 파싱: 유효 JSON 정규화 + 오류 분기(JSON 파싱 실패 / 비객체 /
 * factors 누락) + 누락 필드 기본값 + marker 인지 round-trip (ADR-0022 D8.5).
 */

import { describe, expect, it } from "vitest";

import type { FactorPack } from "@/lib/api/factor-packs";
import { PACK_EXPORT_FORMAT_MARKER } from "@/lib/api/factor-packs";

import { exportPackJson, parsePackJson } from "../pack-io";

const SAMPLE_PACK: FactorPack = {
  pack_slug: "user/my-pack",
  version: "0.2.0",
  factors: [
    {
      canonical_id: "x:v1",
      uuid: "00000000-0000-4000-8000-000000000000",
      name: "X",
      description: "정의",
      unit: "ratio",
      tags: ["valuation"],
      formula: { ast: { field: "revenue" }, inputs: ["revenue"] },
    },
  ],
  citation: { title: "출처" },
};

describe("exportPackJson", () => {
  it("들여쓰기 JSON 으로 round-trip 가능", () => {
    const text = exportPackJson(SAMPLE_PACK);
    expect(text).toContain('"pack_slug": "user/my-pack"');
    const back = JSON.parse(text) as FactorPack;
    expect(back.factors[0]?.canonical_id).toBe("x:v1");
  });
});

describe("parsePackJson — 성공", () => {
  it("유효 pack 을 그대로 파싱", () => {
    const result = parsePackJson(exportPackJson(SAMPLE_PACK));
    expect(result.ok).toBe(true);
    expect(result.pack?.pack_slug).toBe("user/my-pack");
    expect(result.pack?.factors).toHaveLength(1);
    expect(result.pack?.factors[0]?.formula.ast).toEqual({ field: "revenue" });
  });

  it("누락 필드는 안전한 기본값으로 채움", () => {
    const result = parsePackJson(
      JSON.stringify({ factors: [{ name: "Y" }] }),
    );
    expect(result.ok).toBe(true);
    const f = result.pack?.factors[0];
    expect(f?.name).toBe("Y");
    expect(f?.canonical_id).toBe("");
    expect(f?.unit).toBe("unitless");
    expect(f?.tags).toEqual([]);
    expect(f?.formula.ast).toBeNull();
    expect(f?.formula.inputs).toEqual([]);
  });

  it("알 수 없는 unit 은 unitless 로 정규화", () => {
    const result = parsePackJson(
      JSON.stringify({ factors: [{ unit: "rank" }] }),
    );
    expect(result.pack?.factors[0]?.unit).toBe("unitless");
  });

  it("citation 선택 필드 보존", () => {
    const result = parsePackJson(
      JSON.stringify({
        factors: [],
        citation: { title: "T", url: "https://x", publisher: "P" },
      }),
    );
    expect(result.pack?.citation.title).toBe("T");
    expect(result.pack?.citation.url).toBe("https://x");
    expect(result.pack?.citation.publisher).toBe("P");
  });
});

describe("parsePackJson — 오류", () => {
  it("JSON 파싱 실패", () => {
    const result = parsePackJson("{ not json");
    expect(result.ok).toBe(false);
    expect(result.error).toBeDefined();
    expect(result.error).not.toBe("not-an-object");
  });

  it("최상위가 배열 → not-an-object", () => {
    const result = parsePackJson("[]");
    expect(result.ok).toBe(false);
    expect(result.error).toBe("not-an-object");
  });

  it("factors 누락 → missing-factors", () => {
    const result = parsePackJson(JSON.stringify({ pack_slug: "x" }));
    expect(result.ok).toBe(false);
    expect(result.error).toBe("missing-factors");
  });
});

describe("parsePackJson — export wrapper marker 인지 (ADR-0022 D8.5)", () => {
  /** backend /export 응답 wrapper 시뮬 — content_hash 포함. */
  const WRAPPER = {
    export_format: PACK_EXPORT_FORMAT_MARKER,
    pack_schema_version: "1",
    tier: "community",
    pack: {
      ...SAMPLE_PACK,
      content_hash: "aabbcc112233",
    },
  };

  it("marker wrapper 를 인지해 내부 pack 을 추출한다", () => {
    const result = parsePackJson(JSON.stringify(WRAPPER));
    expect(result.ok).toBe(true);
    expect(result.pack?.pack_slug).toBe("user/my-pack");
    expect(result.pack?.factors).toHaveLength(1);
  });

  it("wrapper 내 pack 의 factor 정의를 정규화한다", () => {
    const result = parsePackJson(JSON.stringify(WRAPPER));
    expect(result.pack?.factors[0]?.canonical_id).toBe("x:v1");
    expect(result.pack?.factors[0]?.formula.ast).toEqual({ field: "revenue" });
  });

  it("marker 없는 legacy pack 은 종전대로 파싱한다 (backward compat)", () => {
    const result = parsePackJson(exportPackJson(SAMPLE_PACK));
    expect(result.ok).toBe(true);
    expect(result.pack?.pack_slug).toBe("user/my-pack");
  });

  it("wrapper 의 pack 서브필드가 비객체면 not-an-object 반환", () => {
    const bad = { export_format: PACK_EXPORT_FORMAT_MARKER, pack: [] };
    const result = parsePackJson(JSON.stringify(bad));
    expect(result.ok).toBe(false);
    expect(result.error).toBe("not-an-object");
  });

  it("wrapper 의 pack 서브필드에 factors 없으면 missing-factors 반환", () => {
    const bad = {
      export_format: PACK_EXPORT_FORMAT_MARKER,
      pack: { pack_slug: "x" },
    };
    const result = parsePackJson(JSON.stringify(bad));
    expect(result.ok).toBe(false);
    expect(result.error).toBe("missing-factors");
  });
});
