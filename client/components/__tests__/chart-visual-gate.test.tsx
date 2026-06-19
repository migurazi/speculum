/**
 * 차트 시각요소 review gate — T59 (Active Inspection / No Advice).
 *
 * `check_forbidden_words.py` / eslint `no-forbidden-words` 는 **텍스트** 만
 * 검사한다. 차트의 색·marker·랭킹 같은 **시각 요소** 는 그 검사로 포착되지
 * 않으므로 별도 gate 가 필요(M1 플랜 R7). 본 테스트는 가격 차트 컴포넌트
 * (PriceChart 캔들, CompareChart 오버레이)의 시각 정책 불변식을 회귀 방지로
 * 박는다 — 색상을 명명 상수로 export 했기에 실제 렌더 색과 1:1 대응한다.
 *
 * 검증:
 *   1. 캔들 색 = 한국 관행 (상승=빨강 #dc2626 / 하락=파랑 #2563eb) — ADR-0007
 *      D8.3 M1 결정. 색은 "그날 종가>시가" 라는 등락 사실이며 No Advice(§2.2)의
 *      매매 신호·추천과 구분(차트에 추천 어휘·점수·랭킹 라벨 없음).
 *   2. 상승/하락 색이 서로 달라 등락 사실을 구분 가능.
 *   3. 서구 관행 녹색 상승색(#16a34a 등)이 캔들·오버레이 어디에도 재도입되지
 *      않음 — lightweight-charts default 회귀 차단.
 *
 * 수동 gate(자동화 불가, M1 DoD 문서에 기록): corporate action 일자 외
 * annotation 금지, 랭킹/추천 marker 부재 — `docs/work-orders/m1-milestone.md`
 * T59 DoD 체크리스트 참조.
 */

import { describe, expect, it } from "vitest";

import { COMPARE_COLORS } from "@/components/Compare/CompareChart";
import {
  CANDLE_DOWN_COLOR,
  CANDLE_UP_COLOR,
  TOTAL_RETURN_LINE_COLOR,
} from "@/components/StockDetail/PriceChart";

/**
 * 서구 관행(녹색 상승)의 대표 녹색 hex — 한국 관행 차트에 재도입 금지.
 * lightweight-charts default(#26a69a 류) + tailwind green 계열을 포함.
 */
const FORBIDDEN_WESTERN_UP_GREENS: ReadonlyArray<string> = [
  "#16a34a", // tailwind green-600 (M0 PriceChart 의 옛 상승색)
  "#22c55e", // green-500
  "#15803d", // green-700
  "#26a69a", // lightweight-charts default up
];

describe("차트 시각요소 gate (T59)", () => {
  it("캔들 상승색은 한국 관행 빨강(#dc2626)이다", () => {
    expect(CANDLE_UP_COLOR.toLowerCase()).toBe("#dc2626");
  });

  it("캔들 하락색은 한국 관행 파랑(#2563eb)이다", () => {
    expect(CANDLE_DOWN_COLOR.toLowerCase()).toBe("#2563eb");
  });

  it("상승색과 하락색이 서로 달라 등락 사실을 구분한다", () => {
    expect(CANDLE_UP_COLOR.toLowerCase()).not.toBe(
      CANDLE_DOWN_COLOR.toLowerCase(),
    );
  });

  it("서구 녹색 상승색이 캔들 색에 재도입되지 않는다", () => {
    const forbidden = FORBIDDEN_WESTERN_UP_GREENS.map((c) => c.toLowerCase());
    expect(forbidden).not.toContain(CANDLE_UP_COLOR.toLowerCase());
    expect(forbidden).not.toContain(CANDLE_DOWN_COLOR.toLowerCase());
  });

  it("Total Return 라인색이 서구 녹색(성과=좋음) 상승색이 아니다 (No Advice)", () => {
    // 배당 재투자 라인에 "상승=좋음" 의미 녹색을 쓰면 성과 우열 암시 (§2.2 위반).
    const forbidden = FORBIDDEN_WESTERN_UP_GREENS.map((c) => c.toLowerCase());
    expect(forbidden).not.toContain(TOTAL_RETURN_LINE_COLOR.toLowerCase());
  });

  it("Total Return 라인색이 캔들 등락색과 구분된다 (중립 별도 hue)", () => {
    // 등락 의미 빨강/파랑과 겹치지 않아 total-return 을 별개 시계열로 인지.
    expect(TOTAL_RETURN_LINE_COLOR.toLowerCase()).not.toBe(
      CANDLE_UP_COLOR.toLowerCase(),
    );
    expect(TOTAL_RETURN_LINE_COLOR.toLowerCase()).not.toBe(
      CANDLE_DOWN_COLOR.toLowerCase(),
    );
  });

  it("Compare 오버레이 팔레트에 등락 의미 녹색이 없다 (중립 구분색)", () => {
    const palette = COMPARE_COLORS.map((c) => c.toLowerCase());
    for (const green of FORBIDDEN_WESTERN_UP_GREENS) {
      expect(palette).not.toContain(green.toLowerCase());
    }
  });

  it("Compare 팔레트는 종목 구분용으로 충분한 고유 색을 가진다", () => {
    // 최대 6 종목 — 색 충돌 시 종목 식별 불가. 중복 없는 6 색 보장.
    const unique = new Set(COMPARE_COLORS.map((c) => c.toLowerCase()));
    expect(unique.size).toBe(COMPARE_COLORS.length);
    expect(COMPARE_COLORS.length).toBeGreaterThanOrEqual(6);
  });
});
