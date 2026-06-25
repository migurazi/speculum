/**
 * useAsOfStore 단위 테스트 — T34.
 *
 * 매트릭스:
 *   1. kstToday — YYYY-MM-DD 형식 반환
 *   2. setAsOf — asOf 갱신 + isToday 재계산
 *   3. resetToToday — today 로 reset
 *   4. localStorage 영구화 — 동일 process 재 hydration 시 보존
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { kstToday, useAsOfStore, useIsToday } from "../as-of-store";
import { renderHook } from "@testing-library/react";

describe("kstToday", () => {
  it("returns ISO 8601 YYYY-MM-DD format", () => {
    const today = kstToday();
    expect(today).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("represents Asia/Seoul timezone", () => {
    // browser local timezone 의 today 와 다를 수 있음 (UTC 자정 직후 등).
    // 검증 — KST 기준 valid date 인가만.
    const today = kstToday();
    const [y, m, d] = today.split("-").map(Number);
    expect(y).toBeGreaterThan(2020);
    expect(m).toBeGreaterThanOrEqual(1);
    expect(m).toBeLessThanOrEqual(12);
    expect(d).toBeGreaterThanOrEqual(1);
    expect(d).toBeLessThanOrEqual(31);
  });
});


describe("useAsOfStore", () => {
  beforeEach(() => {
    // 각 test isolation — localStorage clean + store default reset.
    localStorage.clear();
    useAsOfStore.getState().resetToToday();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("initial state = today", () => {
    expect(useAsOfStore.getState().asOf).toBe(kstToday());
  });

  it("setAsOf updates asOf", () => {
    useAsOfStore.getState().setAsOf("2024-01-01");
    expect(useAsOfStore.getState().asOf).toBe("2024-01-01");
  });

  it("resetToToday rolls back from past date", () => {
    useAsOfStore.getState().setAsOf("2020-01-01");
    useAsOfStore.getState().resetToToday();
    expect(useAsOfStore.getState().asOf).toBe(kstToday());
  });

  it("setAsOf does NOT persist to localStorage (ADR-0008 D1.2 — 새 로드 시 최근일)", () => {
    // localStorage 영구화 제거 — stale as_of(공시 전 일자)가 사용자를 가두던 회귀
    // 방지. 세션 내 메모리는 유지되나 localStorage 에는 쓰지 않는다.
    useAsOfStore.getState().setAsOf("2024-05-07");
    // 메모리 state 는 갱신.
    expect(useAsOfStore.getState().asOf).toBe("2024-05-07");
    // localStorage 에는 어떤 as_of 키도 쓰이지 않음.
    expect(localStorage.getItem("speculum-as-of-v1")).toBeNull();
  });
});


describe("useIsToday (derived selector)", () => {
  beforeEach(() => {
    localStorage.clear();
    useAsOfStore.getState().resetToToday();
  });

  it("returns true when asOf === kstToday()", () => {
    const { result } = renderHook(() => useIsToday());
    expect(result.current).toBe(true);
  });

  it("returns false after setAsOf(past)", () => {
    useAsOfStore.getState().setAsOf("2024-01-01");
    const { result } = renderHook(() => useIsToday());
    expect(result.current).toBe(false);
  });
});
