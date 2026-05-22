/**
 * ESLint API 통합 테스트 — flat config + plugin 실제 동작 검증.
 *
 * RuleTester 가 rule 자체의 정확성을 검증하는 반면, 본 테스트는 ESLint v9 의
 * 실제 lint pipeline (parser 로딩 + plugin 등록 + 메시지 생성) 까지 통합 검증.
 *
 * `lintText` API 를 사용 — 파일 시스템 의존 없이 in-memory 코드를 직접 lint.
 * (flat config 의 base-path 제약 회피.)
 */

import { ESLint } from "eslint";
import tsParser from "@typescript-eslint/parser";
import { describe, expect, it } from "vitest";

import speculum from "../index.js";

const FLAT_CONFIG = [
  {
    files: ["**/*.ts", "**/*.tsx"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: { speculum },
    rules: { "speculum/no-forbidden-words": "error" },
  },
];

const eslint = new ESLint({
  overrideConfigFile: true,
  overrideConfig: FLAT_CONFIG,
});

async function lintCode(code, filePath = "input.ts") {
  const results = await eslint.lintText(code, { filePath });
  return results[0];
}

describe("ESLint API 통합", () => {
  it("정상 코드 — 0 errors", async () => {
    const result = await lintCode('const a: string = "관심 종목 목록";');
    expect(result.errorCount).toBe(0);
    expect(result.warningCount).toBe(0);
  });

  it("금지 어휘 포함 코드 — error 발생", async () => {
    const result = await lintCode('const a: string = "오늘의 추천 종목";');
    expect(result.errorCount).toBeGreaterThan(0);
    expect(result.messages[0].ruleId).toBe("speculum/no-forbidden-words");
    expect(result.messages[0].message).toContain("추천");
  });

  it("JSX 금지 어휘", async () => {
    const result = await lintCode(
      'const c = (<button title="매수">click</button>);',
      "jsx.tsx",
    );
    expect(result.errorCount).toBeGreaterThan(0);
  });

  it("import path 의 금지 어휘 — skip (false positive 회피)", async () => {
    const result = await lintCode(
      'import { foo } from "./components/매수-helper";',
    );
    expect(result.errorCount).toBe(0);
  });

  it("템플릿 리터럴의 정적 부분 검사", async () => {
    const result = await lintCode("const a = `Buy ${count} shares`;");
    expect(result.errorCount).toBeGreaterThan(0);
  });

  it("error 메시지에 ADR 참조 + 권장 대체 표현", async () => {
    const result = await lintCode('const a = "추천 종목";');
    expect(result.messages[0].message).toMatch(/ADR-0007/);
    expect(result.messages[0].message).toMatch(/조건에 부합/);
  });

  it("실제 plugin name space 가 'speculum'", async () => {
    const result = await lintCode('const a = "추천";');
    expect(result.messages[0].ruleId).toBe("speculum/no-forbidden-words");
  });

  // ============ oracle v3 H5 — Jamo 까지 한국어 분류 ============
  it("Hangul Jamo (U+1100 영역) 도 한국어로 분류", async () => {
    // "추천" 의 Jamo 분해 형태 — NFKC normalize 후 syllable 로 복원.
    // 정확히는 NFC/NFKC 가 ㅊ+ㅜ+ㅊ+ㅓ+ㄴ → 추천 으로 합성.
    const decomposed = "추천".normalize("NFD"); // ㅊㅜㅊㅓㄴ
    // 이 문자열을 코드에 그대로 넣으면 NFKC 후 "추천" 으로 복원되어 검출되어야 함.
    const result = await lintCode(`const a = "${decomposed} 종목";`);
    expect(result.errorCount).toBeGreaterThan(0);
    // 메시지가 D4.1 (한국어) 로 분류되어야 함.
    expect(result.messages[0].message).toMatch(/ADR-0007 D4\.1/);
  });
});

// ============ oracle v3 H4 — Cross-language NFKC parity ============
// Node 의 String.prototype.normalize 와 Python 의 unicodedata.normalize 가 같은
// 출력을 내는지 fixture 단위로 검증. 본격 hash 검증은 별도 CI step (다음 사이클).
describe("Cross-language NFKC parity (smoke test)", () => {
  it.each([
    ["Ｂｕｙ", "Buy"],
    ["ＳＥＬＬ", "SELL"],
    ["ﬁle", "file"],   // ligature decomposition
    ["①②③", "123"],
    ["①추천", "1추천"],
  ])("'%s'.normalize('NFKC') === '%s'", (input, expected) => {
    expect(input.normalize("NFKC")).toBe(expected);
  });
});
