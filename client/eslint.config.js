/**
 * Speculum 클라이언트 ESLint flat config (v9).
 *
 * ADR-0007 D4.4 의 client-side gate 활성화.
 * ADR-0007 D4.5 (V-M2-3): react/no-danger 로 dangerouslySetInnerHTML 린트 레벨 강제.
 */

import { createRequire } from "node:module";

import tsParser from "@typescript-eslint/parser";

import speculum from "./eslint-rules/index.js";

// eslint-plugin-react, eslint-plugin-react-hooks 는 CJS 모듈 —
// ESM flat config 에서 createRequire 로 로드.
const _require = createRequire(import.meta.url);
const reactPlugin = _require("eslint-plugin-react");
const reactHooksPlugin = _require("eslint-plugin-react-hooks");

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
    // ADR-0007 D4.5 (V-M2-3): components/ 에 react/no-danger 전용 블록.
    // dangerouslySetInnerHTML 신규 사용을 lint 레벨에서 전면 금지.
    // 검증된 chokepoint 호출처(NotesPanel.tsx renderMarkdown 경유)는
    // 각 사용 지점에 eslint-disable 블록으로 명시 허용.
    // no-forbidden-words 는 lib/app 범위만 — 이 블록에서는 미적용.
    // react-hooks 플러그인은 기존 eslint-disable 주석 인식용으로만 등록.
    files: ["components/**/*.tsx", "components/**/*.ts"],
    linterOptions: {
      // 기존 컴포넌트의 pre-existing eslint-disable 주석이
      // 이 블록에서 활성화되지 않은 규칙을 참조해도 경고 미생성.
      reportUnusedDisableDirectives: false,
    },
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      react: reactPlugin,
      "react-hooks": reactHooksPlugin,
    },
    rules: {
      // V-M2-3: dangerouslySetInnerHTML 신규 사용 전면 금지.
      // 검증된 chokepoint(renderMarkdown 경유)만 명시 inline override 허용.
      "react/no-danger": "error",
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
