/**
 * Speculum 사내 ESLint plugin — 정책 강제 룰 모음.
 *
 * 현재 룰:
 *  - `speculum/no-forbidden-words` — ADR-0007 D4.4 client-side gate
 *
 * 사용 (`eslint.config.js`):
 *
 *   import speculum from "./eslint-rules/index.js";
 *   export default [
 *     {
 *       plugins: { speculum },
 *       rules: { "speculum/no-forbidden-words": "error" },
 *     },
 *   ];
 */

import noForbiddenWords from "./no-forbidden-words.js";

const plugin = {
  meta: {
    name: "speculum",
    version: "0.1.0-dev",
  },
  rules: {
    "no-forbidden-words": noForbiddenWords,
  },
};

export default plugin;
