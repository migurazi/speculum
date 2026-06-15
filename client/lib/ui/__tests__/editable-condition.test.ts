/**
 * editable-condition 헬퍼 단위 테스트 — R-3 payload 격리 + id 안정성.
 *
 * 핵심 계약:
 *   - newEditableCondition: 고유 id + 기본/override 필드.
 *   - toScreenConditions: id 를 strip 해 순수 wire ScreenCondition[] 반환
 *     (API body / save hash 에 id 가 절대 섞이지 않음).
 */

import { describe, expect, it } from "vitest";

import {
  newConditionId,
  newEditableCondition,
  toEditableConditions,
  toScreenConditions,
} from "../editable-condition";

describe("editable-condition", () => {
  it("newEditableCondition 은 기본 빈 조건 + id 를 만든다", () => {
    const c = newEditableCondition();
    expect(c.factor).toBe("");
    expect(c.op).toBe("<");
    expect(c.value).toBe("");
    expect(typeof c.id).toBe("string");
    expect(c.id.length).toBeGreaterThan(0);
  });

  it("newEditableCondition 은 override 를 반영한다", () => {
    const c = newEditableCondition({ factor: "per", op: ">", value: "10" });
    expect(c.factor).toBe("per");
    expect(c.op).toBe(">");
    expect(c.value).toBe("10");
  });

  it("연속 생성 id 는 서로 다르다", () => {
    const ids = new Set(
      Array.from({ length: 50 }, () => newConditionId()),
    );
    expect(ids.size).toBe(50);
  });

  it("toScreenConditions 는 id 를 strip 한다 (payload 격리)", () => {
    const rows = [
      newEditableCondition({ factor: "per", op: "<", value: "10" }),
      newEditableCondition({ factor: "roe", op: ">", value: "20" }),
    ];
    const wire = toScreenConditions(rows);

    expect(wire).toHaveLength(2);
    // 정확한 wire 표면 — id 없음.
    expect(wire[0]).toEqual({ factor: "per", op: "<", value: "10" });
    expect(wire[1]).toEqual({ factor: "roe", op: ">", value: "20" });
    expect(Object.keys(wire[0] as object)).toEqual(["factor", "op", "value"]);
    expect("id" in (wire[0] as object)).toBe(false);

    // JSON 직렬화(실 전송 형태)에도 id 가 없어야 한다.
    const serialized = JSON.parse(JSON.stringify(wire)) as unknown[];
    expect("id" in (serialized[0] as object)).toBe(false);
  });

  it("toEditableConditions 는 wire 를 id 부여 row 로 승격한다", () => {
    const wire = [
      { factor: "per", op: "<" as const, value: "10" },
      { factor: "roe", op: ">" as const, value: "20" },
    ];
    const editable = toEditableConditions(wire);
    expect(editable).toHaveLength(2);
    expect(editable[0]?.factor).toBe("per");
    expect(editable[0]?.id).not.toBe(editable[1]?.id);
    // round-trip: 다시 strip 하면 원본 wire 와 동일.
    expect(toScreenConditions(editable)).toEqual(wire);
  });
});
