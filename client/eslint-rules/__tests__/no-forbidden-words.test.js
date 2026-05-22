/**
 * `speculum/no-forbidden-words` rule 단위 테스트.
 *
 * ESLint v9 의 `RuleTester` 사용. valid / invalid 매트릭스.
 *
 * Python `test_forbidden_words.py` / vitest `forbidden-words.test.ts` 와 함께
 * 같은 SoT (shared/forbidden-words.json) 의 3-언어 검증 layer.
 */

import { RuleTester } from "eslint";
import tsParser from "@typescript-eslint/parser";
import { describe, it } from "vitest";

import rule from "../no-forbidden-words.js";

const tester = new RuleTester({
  languageOptions: {
    parser: tsParser,
    parserOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      ecmaFeatures: { jsx: true },
    },
  },
});

// RuleTester.run 자체는 vitest 의 describe/it 와 통합되지만, 명시적으로 describe
// 래핑 하면 결과 출력이 더 깔끔.
describe("speculum/no-forbidden-words", () => {
  it("RuleTester 매트릭스", () => {
    tester.run("no-forbidden-words", rule, {
      valid: [
        // ============ 정상 한국어 텍스트 ============
        { code: 'const a = "관심 종목을 추가했습니다";' },
        { code: 'const a = "조건에 부합하는 종목";' },
        { code: 'const a = "필터 통과 항목";' },
        { code: 'const a = "선택한 종목";' },
        { code: 'const a = "비교 대상 종목";' },

        // 화이트리스트 표현 — substring 으로 잡혀선 안 됨
        { code: 'const a = "이베스트투자증권 종목 정보";' },
        { code: 'const a = "기대수명 통계";' },
        { code: 'const a = "베스트셀러 목록";' },
        { code: 'const a = "적시 공시 의무";' },
        { code: 'const a = "장기 보유 전략";' },

        // 디스클레이머 self-text
        {
          code:
            'const disclaimer = "Speculum 은 종목을 추천하지 않습니다. 본 도구는 투자 자문이 아닙니다.";',
        },

        // ============ 정상 영문 텍스트 ============
        { code: 'const a = "Best Practice for accessibility";' },
        { code: 'const a = "Best Effort delivery semantics";' },
        { code: 'const a = "Long-term outlook of KOSPI";' },
        { code: 'const a = "Short-term volatility chart";' },
        { code: 'const a = "Holdings overview by sector";' },
        { code: 'const a = "Buy-side analyst report";' },

        // ============ 비-사용자-visible 컨텍스트 (검사 skip) ============
        // import path 에 금지 어휘가 있어도 통과
        { code: 'import { foo } from "./components/추천";' },
        { code: 'import x from "buy-utils";' },
        { code: 'import x from "buy";' },  // npm 패키지 이름
        { code: 'const x = require("buy-pkg");' },
        { code: 'const m = await import("./매수-helper");' },
        { code: 'export { default } from "./추천-data";' },

        // ============ JSX 정상 ============
        {
          code: `function App() { return <div>조건에 부합하는 종목 목록</div>; }`,
        },
        {
          code: `function App() { return <button title="관심 종목 추가">+</button>; }`,
        },

        // ============ 템플릿 리터럴 정상 ============
        { code: "const a = `관심 종목: ${name}`;" },
        { code: "const a = `Best Practice — ${desc}`;" },

        // ============ 식별자·prop name·변수명은 검사 X ============
        { code: "const buy = 1;" },  // 식별자 (변수명)
        { code: "function selectBest(items) { return items; }" },
        { code: "const obj = { buy: true, sell: false };" },  // prop name
      ],

      invalid: [
        // ============ 한국어 금지 ============
        {
          code: 'const a = "이번주 추천 종목입니다";',
          errors: [{ messageId: "forbiddenWord", data: { word: "추천", adrRef: "ADR-0007 D4.1" } }],
        },
        {
          code: 'const a = "유망주 TOP 10";',
          errors: 1,
        },
        {
          code: 'const a = "기대주 분석";',
          errors: 1,
        },
        {
          code: 'const a = "강력 매수 신호";',
          errors: 1,  // "강력 매수" 한 매치 (길이 우선)
        },
        {
          code: 'const a = "매수와 매도 시점";',
          errors: 2,
        },
        {
          code: 'const a = "탑픽 발표";',
          errors: 1,
        },

        // oracle v1 A1 보강 어휘
        {
          code: 'const a = "고평가 종목";',
          errors: 1,
        },
        {
          code: 'const a = "저평가 상태";',
          errors: 1,
        },
        {
          code: 'const a = "손절 라인";',
          errors: 1,
        },
        {
          code: 'const a = "목표가 10만원";',
          errors: 1,
        },
        {
          code: 'const a = "투자 의견 매수";',
          errors: 2,  // "투자 의견" + "매수"
        },

        // ============ 영문 금지 ============
        {
          code: 'const a = "Buy this stock";',
          errors: [{ messageId: "forbiddenWord", data: { word: "Buy", adrRef: "ADR-0007 D4.2" } }],
        },
        {
          code: 'const a = "BUY signal";',
          errors: 1,
        },
        {
          code: 'const a = "Sell rating";',
          errors: 1,
        },
        {
          code: 'const a = "Strong Buy of the week";',
          errors: 1,  // "Strong Buy" 한 매치
        },
        {
          code: 'const a = "Top Pick analysis";',
          errors: 1,
        },
        {
          code: 'const a = "Outperform expected";',
          errors: 1,
        },

        // oracle v1 A2 보강 어휘
        {
          code: 'const a = "Go Long on KOSPI";',
          errors: 1,
        },
        {
          code: 'const a = "Overweight rating";',
          errors: 1,
        },
        {
          code: 'const a = "Hold this stock";',
          errors: 1,
        },
        {
          code: 'const a = "Target Price raised";',
          errors: 1,
        },

        // ============ JSX 금지 ============
        {
          code: `function App() { return <div>오늘의 추천 종목</div>; }`,
          errors: 1,
        },
        {
          code: `function App() { return <button title="매수">+</button>; }`,
          errors: 1,
        },

        // ============ 템플릿 리터럴 금지 ============
        {
          code: "const a = `오늘의 추천: ${name}`;",
          errors: 1,
        },
        {
          code: "const a = `Buy ${count} shares`;",
          errors: 1,
        },

        // ============ NFKC fullwidth 회피 ============
        {
          code: 'const a = "Ｂｕｙ now";',
          errors: 1,
        },
        {
          code: 'const a = "ＳＥＬＬ signal";',
          errors: 1,
        },

        // ============ 한영 혼용 ============
        {
          code: 'const a = "Buy 를 추천합니다";',
          errors: 2,
        },

        // ============ TypeScript-specific AST (oracle v3 C3) ============
        // TSLiteralType — type alias 안의 string literal
        {
          code: 'type X = "추천";',
          errors: 1,
        },
        // TSAsExpression — as const
        {
          code: 'const a = "매수" as const;',
          errors: 1,
        },
        // TSSatisfiesExpression — satisfies
        {
          code: 'const a = "추천" satisfies string;',
          errors: 1,
        },
        // TSEnumDeclaration — string member
        {
          code: 'enum X { A = "매수", B = "정상" }',
          errors: 1,
        },
        // Decorator argument — string literal
        // (parser 가 decorator 지원해야 함 — ecmaVersion latest + ts-eslint 으로 OK)
        // 다만 stage-3 decorator 의 정확한 AST shape 은 parser 별로 다름. 단순
        // function call 안의 string 으로 검증:
        {
          code: 'function setup(label) {} setup("매수");',
          errors: 1,
        },
        // Object value
        {
          code: 'const obj = { label: "추천 종목" };',
          errors: 1,
        },
        // Array element
        {
          code: 'const arr = ["매수", "관망"];',
          errors: 2,
        },
      ],
    });
  });
});
