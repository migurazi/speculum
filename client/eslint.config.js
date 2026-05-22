/**
 * Speculum 클라이언트 ESLint flat config (v9).
 *
 * ADR-0007 D4.4 의 client-side gate 활성화.
 */

import tsParser from "@typescript-eslint/parser";

import speculum from "./eslint-rules/index.js";

export default [
  {
    // 검사 대상.
    files: ["lib/**/*.ts", "lib/**/*.tsx", "app/**/*.ts", "app/**/*.tsx"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      speculum,
    },
    rules: {
      // 8 기둥 §2.2 No Advice 의 컴파일 타임 강제. ADR-0007 D4.4.
      "speculum/no-forbidden-words": "error",
    },
  },
  {
    // 테스트·플러그인 자체는 검사 제외 — 의도적 위반 fixture 가 들어가므로.
    ignores: [
      "**/__tests__/**",
      "eslint-rules/**",
      "node_modules/**",
      "dist/**",
      ".next/**",
    ],
  },
];
