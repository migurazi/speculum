/**
 * parseNaReason 단위 테스트 — backend 기계 코드 구조 분해.
 */

import { describe, expect, it } from "vitest";

import { parseNaReason } from "../na-reason";

describe("parseNaReason", () => {
  it("missing_input:<field> 분해", () => {
    expect(parseNaReason("missing_input:shares_issued")).toEqual({
      kind: "missing_input",
      field: "shares_issued",
    });
  });

  it("field 에 underscore 가 여러 개여도 보존", () => {
    expect(parseNaReason("missing_input:market_cap_ex_treasury")).toEqual({
      kind: "missing_input",
      field: "market_cap_ex_treasury",
    });
  });

  it("insufficient_series:<field>:requested=N,got=M 분해", () => {
    expect(
      parseNaReason("insufficient_series:basic_eps_consolidated_ifrs:requested=4,got=1"),
    ).toEqual({
      kind: "insufficient_series",
      field: "basic_eps_consolidated_ifrs",
      requested: 4,
      got: 1,
    });
  });

  it("insufficient_series 에 got=0 도 정상 파싱(0 은 falsy 가 아님)", () => {
    const r = parseNaReason(
      "insufficient_series:net_income_attributable_consolidated_ifrs:requested=4,got=0",
    );
    expect(r).toEqual({
      kind: "insufficient_series",
      field: "net_income_attributable_consolidated_ifrs",
      requested: 4,
      got: 0,
    });
  });

  it("insufficient_series 에 requested/got 누락 시 null (형식 변형 방어)", () => {
    expect(parseNaReason("insufficient_series:some_field:malformed")).toEqual({
      kind: "insufficient_series",
      field: "some_field",
      requested: null,
      got: null,
    });
  });

  it("알 수 없는 형식은 unknown 으로 raw 보존(정보 손실 0)", () => {
    expect(parseNaReason("unmapped:ifrs-full_SomeTag")).toEqual({
      kind: "unknown",
      raw: "unmapped:ifrs-full_SomeTag",
    });
    expect(parseNaReason("")).toEqual({ kind: "unknown", raw: "" });
  });
});
