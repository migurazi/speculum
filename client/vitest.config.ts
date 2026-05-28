/**
 * Vitest config — T33 cycle 의 testing-library 도입.
 *
 * 변경:
 *   - environment="jsdom" — React component 의 DOM API 접근.
 *   - setupFiles — @testing-library/jest-dom 의 custom matcher 활성.
 *   - paths alias "@/" — tsconfig.json 의 paths 와 일관 (Vitest 가 별도
 *     resolver 사용 — 명시 alias 필요).
 *
 * 기존 forbidden-words tests 는 plain TypeScript — jsdom 무관. coexist.
 */

import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: [
      "lib/**/__tests__/**/*.{test,spec}.{ts,tsx,js}",
      "components/**/__tests__/**/*.{test,spec}.{ts,tsx}",
      "state/**/__tests__/**/*.{test,spec}.{ts,tsx}",
      "eslint-rules/**/__tests__/**/*.{test,spec}.{ts,js}",
    ],
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  // esbuild 의 JSX automatic transform — React 17+ 의 jsx-runtime 사용으로
  // test 파일에서 `import React` 명시 불필요. Next.js 도 동일 transform.
  esbuild: {
    jsx: "automatic",
  },
});
