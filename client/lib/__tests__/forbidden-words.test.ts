/**
 * forbidden-words 단위 테스트 — Python test_forbidden_words.py 의 미러.
 *
 * 두 구현이 동일한 SoT (`shared/forbidden-words.json`) 를 import 하므로 어휘
 * divergence 는 발생하지 않지만, 분류·동작·정책 분기는 양쪽에서 별도 검증해야 함.
 */

import { describe, expect, it } from "vitest";

import {
  assertClean,
  type Match,
  normalize,
  scanApiResponse,
  scanText,
} from "../forbidden-words";

describe("scanText — 한국어 절대 금지 (보강 어휘 포함)", () => {
  const cases: Array<[string, string]> = [
    ["이번주 추천 종목입니다", "추천"],
    ["유망주 TOP 10", "유망주"],
    ["기대주 분석", "기대주"],
    ["주목할 만한 종목", "주목할"],
    ["강력 매수 시그널", "강력 매수"],
    ["강력매수", "강력매수"],
    ["매수 추천", "매수"],
    ["매도 타이밍", "매도"],
    ["탑픽 발표", "탑픽"],
    ["종목 픽 공개", "종목 픽"],
    ["강추 종목", "강추"],
    ["비중 확대 권고", "비중 확대"],
    ["지금이 적기", "적기"],
    // oracle A1
    ["고평가 종목", "고평가"],
    ["저평가 상태", "저평가"],
    ["매집 시기", "매집"],
    ["손절 라인", "손절"],
    ["익절 권고", "익절"],
    ["투자 의견 매수", "투자 의견"],
    ["목표가 10만원", "목표가"],
    ["진입 시점", "진입 시점"],
    ["비중 축소 권고", "비중 축소"],
    ["강력 매도 의견", "강력 매도"],
    ["유망종목 발표", "유망종목"],
  ];

  it.each(cases)("'%s' → '%s' 검출", (text, expected) => {
    const matches = scanText(text);
    expect(matches.length).toBeGreaterThan(0);
    expect(matches.some((m) => m.word === expected)).toBe(true);
    expect(matches[0]!.kind).toBe("ko-absolute");
  });
});

describe("scanText — 영문 절대 금지 (보강 어휘 + case-insensitive)", () => {
  const cases: Array<[string, string]> = [
    ["Buy this stock", "Buy"],
    ["BUY signal detected", "Buy"],
    ["buy now", "Buy"],
    ["Strong Buy rating", "Strong Buy"],
    ["Sell recommendation", "Sell"],
    ["Top Pick of the week", "Top Pick"],
    ["Bullish outlook", "Bullish"],
    ["Bearish trend", "Bearish"],
    ["Outperform expected", "Outperform"],
    ["This is the Best stock", "Best"],
    // oracle A2
    ["Go Long on this", "Long"],
    ["Go Short", "Short"],
    ["Overweight rating", "Overweight"],
    ["Underweight position", "Underweight"],
    ["Hold this stock", "Hold"],
    ["Accumulate slowly", "Accumulate"],
    ["Upgrade to outperform", "Upgrade"],
    ["Downgrade announcement", "Downgrade"],
    ["Target Price raised", "Target Price"],
    ["Price Target updated", "Price Target"],
  ];

  it.each(cases)("'%s' → '%s' 검출", (text, canonical) => {
    const matches = scanText(text);
    expect(matches.length).toBeGreaterThan(0);
    expect(matches.some((m) => m.canonical === canonical)).toBe(true);
  });

  it.each([
    "buyer satisfied",
    "buyout completed",
    "sellable goods",
    "Bestseller list",
    "holdings overview",
    "shortage report",
    "longitude data",
  ])("'%s' compound 는 false positive X (\\b)", (text) => {
    const matches = scanText(text);
    const lowered = new Set(matches.map((m) => m.word.toLowerCase()));
    for (const bad of ["buy", "sell", "best", "hold", "short", "long"]) {
      expect(lowered.has(bad)).toBe(false);
    }
  });
});

describe("화이트리스트 (oracle A5 보강 포함)", () => {
  const allowed: string[] = [
    "관심 종목 목록",
    "관심종목 추가",
    "조건에 부합하는 종목입니다",
    "필터 통과 항목",
    "선택한 종목 정보",
    "비교 대상 종목",
    "Speculum 은 종목을 추천하지 않습니다",
    "본 도구는 추천이 아닙니다",
    "추천 안 함 정책",
    "추천 위젯 없음",
    "Buy-side analyst",
    "Buy Side institution",
    // oracle A5
    "Best Practice 사례",
    "Best Effort delivery",
    "베스트셀러 리스트",
    "이베스트투자증권",
    "기대수명 분석",
    "기대치 평가",
    "적시 공시 의무",
    "주목받는 시장 동향",
    "매수자 보호 규정",
    "장기 보유 전략",
    "Long-term outlook",
    "Short-term volatility",
  ];

  it.each(allowed)("'%s' 는 허용 — false positive X", (text) => {
    expect(scanText(text)).toEqual([]);
  });

  it("디스클레이머 self-clean", () => {
    const text =
      "Speculum 은 한국 주식 시장의 정량 데이터 탐색 도구이며 " +
      "투자 권유 또는 투자자문이 아닙니다. 종목을 추천하지 않습니다.";
    expect(scanText(text)).toEqual([]);
  });
});

describe("NFKC 정규화 (oracle G4)", () => {
  it.each([
    ["Ｂｕｙ", "Buy"],
    ["ＳＥＬＬ", "Sell"],
    ["Ｂｅｓｔ Pick", "Best"],
  ])("fullwidth '%s' 차단됨", (text, expected) => {
    const matches = scanText(text);
    expect(matches.length).toBeGreaterThan(0);
    expect(matches.some((m) => m.canonical === expected)).toBe(true);
  });

  it("normalize() 직접 호출", () => {
    expect(normalize("Ｂｕｙ")).toBe("Buy");
    expect(normalize("ＳＥＬＬ")).toBe("SELL");
    expect(normalize("일반 텍스트")).toBe("일반 텍스트");
  });
});

describe("CheckScope 별 정책 (oracle C3)", () => {
  it("system — 검사 (default)", () => {
    expect(() => assertClean("매수 추천", { scope: "system" })).toThrow();
  });

  it("user-private — 검사 skip", () => {
    expect(() =>
      assertClean("매수 추천 종목", { scope: "user-private" }),
    ).not.toThrow();
  });

  it("external-quote — 검사 skip (종목명 등)", () => {
    expect(() =>
      assertClean("이베스트투자증권", { scope: "external-quote" }),
    ).not.toThrow();
    expect(() =>
      assertClean("매수의 정석 (책 제목)", { scope: "external-quote" }),
    ).not.toThrow();
  });

  it("user-shared — 검사 함", () => {
    expect(() => assertClean("추천 팩터", { scope: "user-shared" })).toThrow();
  });

  it("scope 미지정 default = system", () => {
    expect(() => assertClean("매수 추천")).toThrow();
  });
});

describe("excludePaths — 종목명 등 (oracle C1)", () => {
  it("지정된 key 는 검사 제외", () => {
    const payload = {
      title: "Stock Detail",
      stock_name: "추천 가상기업",
      company_name: "유망 가상회사",
      value: 12.3,
    };
    expect(
      scanApiResponse(payload, {
        excludePaths: ["stock_name", "company_name"],
      }),
    ).toEqual([]);
  });

  it("nested list 안의 동일 key 도 제외", () => {
    const payload = {
      stocks: [
        { stock_name: "추천 회사 A" },
        { stock_name: "유망 회사 B" },
      ],
      title: "검색 결과",
    };
    expect(
      scanApiResponse(payload, { excludePaths: ["stock_name"] }),
    ).toEqual([]);
  });

  it("excludePaths 없으면 검사", () => {
    const payload = { some_field: "추천 종목" };
    const matches = scanApiResponse(payload);
    expect(matches.some((m) => m.word === "추천")).toBe(true);
  });
});

describe("Iterative walker — depth / 자기참조 방어 (oracle B2)", () => {
  it("깊이 100 nested 처리 (재귀 stack overflow 안 남)", () => {
    let node: unknown = "추천 종목";
    for (let i = 0; i < 100; i++) {
      node = { child: node };
    }
    const matches = scanApiResponse(node, { maxDepth: 200 });
    expect(matches.some((m) => m.word === "추천")).toBe(true);
  });

  it("maxDepth 초과 시 throw", () => {
    let node: unknown = "text";
    for (let i = 0; i < 100; i++) {
      node = [node];
    }
    expect(() => scanApiResponse(node, { maxDepth: 10 })).toThrowError(/depth/);
  });

  it("non-string primitive 무시", () => {
    expect(
      scanApiResponse({ count: 42, ratio: 1.23, flag: true, empty: null }),
    ).toEqual([]);
  });
});

describe("assertClean — 메시지 + 컨텍스트", () => {
  it("safe 텍스트는 throw 안 함", () => {
    expect(() => assertClean("관심 종목에 추가되었습니다")).not.toThrow();
  });

  it("context 가 메시지에 포함", () => {
    expect(() =>
      assertClean("매수 추천", { context: "api.screen.title" }),
    ).toThrowError(/api\.screen\.title/);
  });

  it("control character 가 context 에서 제거됨 (log injection 방어)", () => {
    try {
      assertClean("매수", { context: "path\nwith\rnewline" });
      throw new Error("should have thrown");
    } catch (e) {
      const msg = (e as Error).message;
      expect(msg).not.toContain("\n");
      expect(msg).not.toContain("\r");
      expect(msg).toContain("?");
    }
  });
});

describe("우선순위 + edge case", () => {
  it("'강력 매수' 가 '매수' 보다 먼저", () => {
    const matches = scanText("강력 매수 알림");
    expect(matches.some((m) => m.word === "강력 매수")).toBe(true);
    expect(matches.filter((m) => m.word === "매수").length).toBe(0);
  });

  it("빈 텍스트", () => {
    expect(scanText("")).toEqual([]);
  });

  it("순수 숫자/기호 안전", () => {
    expect(scanText("12.3% (TTM 연결)")).toEqual([]);
  });

  it("문장부호 옆 매칭", () => {
    expect(scanText("추천!!! 종목.").some((m) => m.word === "추천")).toBe(true);
  });

  it("여러 매치는 index 순서", () => {
    const matches = scanText("매수 추천 + Sell signal");
    const indices = matches.map((m) => m.index);
    expect(indices).toEqual([...indices].sort((a, b) => a - b));
  });

  it("index 정확", () => {
    const text = "오늘의 추천 종목";
    const matches = scanText(text);
    expect(matches.length).toBe(1);
    expect(matches[0]!.index).toBe(text.indexOf("추천"));
  });

  it("한영 혼용", () => {
    const matches = scanText("Buy 를 추천합니다");
    const words = new Set(matches.map((m) => m.word));
    expect(words.has("Buy")).toBe(true);
    expect(words.has("추천")).toBe(true);
  });
});

describe("Match 객체 구조 일관 (Python 미러)", () => {
  it("필드 = {word, canonical, kind, index}", () => {
    const [first] = scanText("Buy");
    const match: Match = first!;
    expect(typeof match.word).toBe("string");
    expect(typeof match.canonical).toBe("string");
    expect(["ko-absolute", "en-absolute"]).toContain(match.kind);
    expect(typeof match.index).toBe("number");
  });
});
