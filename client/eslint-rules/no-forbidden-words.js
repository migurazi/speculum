/**
 * @file ESLint rule — `speculum/no-forbidden-words`
 *
 * 8 기둥 §2.2 No Advice 의 컴파일 타임 (lint) 강제. 사용자에게 노출될 수 있는
 * 모든 문자열 리터럴 / JSX 텍스트 / 템플릿 리터럴을 검사하여 ADR-0007 D4.1·D4.2
 * 의 금지 어휘를 차단.
 *
 * 어휘 SoT: `../../shared/forbidden-words.json` — Python middleware
 * (`server/app/services/forbidden_words.py`) + Client lib
 * (`client/lib/forbidden-words.ts`) + 본 lint rule 3 곳이 같은 JSON 을 import.
 *
 * 관련 ADR / 리뷰:
 *  - ADR-0006 D2, D8 — 자본시장법 회피 implementation chain
 *  - ADR-0007 D4.4 — middleware + ESLint plugin + build scan 의 3-단계 강제
 *  - oracle v1 G1 (Critical) — 강제 메커니즘 미연결. 본 rule 이 client-side piece
 *  - oracle v1 D1 (Critical) — SoT 분리. shared/forbidden-words.json 으로 통합
 *  - oracle v3 (2026-05-22) C1~C3, H1~H5 — JSXAttribute dead code, FS error
 *    handling, TS AST 검증, RegExp 재진입, NFKC index drift, column 계산,
 *    cross-language parity, ADR hardcode
 *
 * 검사 대상 AST 노드:
 *  - `Literal` (string 값) — `"추천 종목"`. JSX 속성 / 일반 const / object value /
 *    array element 등 모든 string literal 컨텍스트.
 *  - `TemplateElement` — 백틱 안의 raw 텍스트
 *  - `JSXText` — JSX 의 텍스트 child (`<p>오늘의 추천</p>`)
 *
 * 검사 제외:
 *  - `ImportDeclaration` / `ExportAllDeclaration` / `ExportNamedDeclaration` source —
 *    라이브러리 path 는 사용자 visible 아님
 *  - `ImportExpression` source — dynamic import path
 *  - `require("...")` argument — CommonJS 호환
 *  - 식별자 (Identifier) — 변수명·prop name 은 사용자 visible 아님
 *  - 주석 — 코드 주석은 사용자 visible 아님
 *  - 정규식 — `RegExpLiteral`
 *
 * 한계:
 *  - 동적 문자열 (`"오늘의 " + word`) 의 일부 조합은 lint 시점에 cover 불가.
 *    runtime middleware 가 보완.
 *  - i18n JSON 의 별도 파일은 build-time content scan 으로 (다음 사이클).
 *  - NFKC normalize 가 string length 를 변경하는 일부 unicode (e.g. `ﬁ` → `fi`)
 *    에서 reported index 가 normalized 기준이라 source column 보정이 부정확.
 *    이 경우는 `node.loc` 전체 범위로 fallback (H3 fix).
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// ESM 에서 __dirname 재현 — eslint plugin 이 cli / API 어디서 호출되어도 안정.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// =============================================================================
// Vocabulary load with graceful degradation (oracle v3 C2)
// =============================================================================
//
// 본 plugin 은 ESLint module load 시 SoT JSON 을 1회 읽음. 파일 누락 / 파싱 실패
// 시 ESLint 자체가 crash 하면 silent disable — 가장 나쁜 실패 모드 (regulatory
// compliance gate 가 조용히 disabled).
//
// 대신 try/catch 후 degenerate rule 로 fallback — 모든 파일에 1회 error 보고하여
// CI 가 빨갛게 뜨도록.

const VOCAB_PATH = resolve(__dirname, "../../shared/forbidden-words.json");

let vocabLoadError = null;
/** @type {{ko_absolute: string[], en_absolute: string[], allowed_phrases: string[], governance_adr: string}} */
let vocab;
try {
  vocab = JSON.parse(readFileSync(VOCAB_PATH, "utf-8"));
} catch (err) {
  vocabLoadError = err;
  vocab = {
    ko_absolute: [],
    en_absolute: [],
    allowed_phrases: [],
    governance_adr: "ADR-0007",
  };
}

const KO_FORBIDDEN_RAW = vocab.ko_absolute;
const EN_FORBIDDEN_RAW = vocab.en_absolute;
const ALLOWED_PHRASES = vocab.allowed_phrases;
const GOVERNANCE_ADR = vocab.governance_adr;

// =============================================================================
// Pattern construction
// =============================================================================

/**
 * 정규식 escape — RegExp 메타문자를 literal 로.
 */
function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * 길이 내림차순 + codepoint 정렬 (Python `forbidden_words.py` 와 일관).
 */
function sortByLengthDesc(words) {
  return [...new Set(words)].sort((a, b) => {
    if (a.length !== b.length) return b.length - a.length;
    return a < b ? -1 : a > b ? 1 : 0;
  });
}

const KO_SORTED = sortByLengthDesc(KO_FORBIDDEN_RAW);
const EN_SORTED = sortByLengthDesc(EN_FORBIDDEN_RAW);

const KO_PATTERN_STR = KO_SORTED.length > 0 ? KO_SORTED.map(escapeRegex).join("|") : null;
const EN_PATTERN_STR =
  EN_SORTED.length > 0 ? `\\b(?:${EN_SORTED.map(escapeRegex).join("|")})\\b` : null;

/**
 * Fresh RegExp 생성 — module-scope RegExp 의 `lastIndex` 재진입 hazard 차단
 * (oracle v3 H1). RegExp 컴파일 비용은 V8 캐싱으로 사실상 0.
 */
function freshKoPattern() {
  return KO_PATTERN_STR ? new RegExp(KO_PATTERN_STR, "g") : null;
}

function freshEnPattern() {
  return EN_PATTERN_STR ? new RegExp(EN_PATTERN_STR, "gi") : null;
}

// =============================================================================
// Allowed-phrase span detection
// =============================================================================

function buildAllowedSpans(text) {
  const spans = [];
  for (const phrase of ALLOWED_PHRASES) {
    let start = 0;
    let idx;
    while ((idx = text.indexOf(phrase, start)) !== -1) {
      spans.push([idx, idx + phrase.length]);
      start = idx + 1;
    }
  }
  return spans;
}

function isInsideAnySpan(start, end, spans) {
  return spans.some(([s, e]) => s <= start && end <= e);
}

// =============================================================================
// scanText — kind 까지 반환 (oracle v3 H5 — Jamo 정확 분류)
// =============================================================================

/**
 * 한국어 글자 범위 검사. Hangul Syllables (AC00-D7A3) + Jamo (1100-11FF) +
 * Compatibility Jamo (3130-318F) 모두 포함 — oracle v3 H5 의 `[ㄱ-힝]` 누락 보정.
 */
function containsKoreanChar(s) {
  // U+AC00-U+D7A3 = 한글 음절, U+1100-U+11FF = 한글 자모, U+3130-U+318F = 호환 자모.
  return /[가-힣ᄀ-ᇿ㄰-㆏]/.test(s);
}

/**
 * 단일 문자열 검사. NFKC normalize 후 매치.
 *
 * 반환 Match 의 `index` 는 normalized 문자열 기준 (oracle v3 H2).
 * 호출자가 source position 으로 사용할 때는 NFKC 가 길이 변경 시 부정확.
 */
function scanText(text) {
  if (!text) return [];
  const normalized = text.normalize("NFKC");
  const allowed = buildAllowedSpans(normalized);
  const results = [];

  // Fresh RegExp — 모듈 scope 재진입 hazard 차단.
  const koPattern = freshKoPattern();
  const enPattern = freshEnPattern();

  if (koPattern) {
    let m;
    while ((m = koPattern.exec(normalized)) !== null) {
      const start = m.index;
      const end = start + m[0].length;
      if (isInsideAnySpan(start, end, allowed)) continue;
      results.push({
        word: m[0],
        canonical: m[0],
        kind: "ko-absolute",
        index: start,
      });
    }
  }

  if (enPattern) {
    let m;
    while ((m = enPattern.exec(normalized)) !== null) {
      const start = m.index;
      const end = start + m[0].length;
      if (isInsideAnySpan(start, end, allowed)) continue;
      const lowered = m[0].toLowerCase();
      const canonical =
        EN_FORBIDDEN_RAW.find((w) => w.toLowerCase() === lowered) ?? m[0];
      results.push({
        word: m[0],
        canonical,
        kind: "en-absolute",
        index: start,
      });
    }
  }

  results.sort((a, b) => a.index - b.index);
  return results;
}

// =============================================================================
// Visit context detection
// =============================================================================

/**
 * 비-사용자-visible 컨텍스트 — import/export source, require() argument.
 *
 * 종료 조건: 다음 노드 type 에 도달하면 결정.
 *   - skip 대상이면 true 반환.
 *   - 그 외 known 경계 (Statement, Declaration, Block 등) 에 도달하면 false.
 *
 * (oracle v3 C1 — JSX 컨텍스트는 별도 `Literal` 가 처리하고 ancestry 종료가
 * 명확하지 않으면 default false 안전.)
 */
function isNonUserVisibleContext(node) {
  let cur = node.parent;
  while (cur) {
    switch (cur.type) {
      case "ImportDeclaration":
      case "ExportNamedDeclaration":
      case "ExportAllDeclaration":
      case "ImportExpression":
        return true;
      case "CallExpression": {
        const callee = cur.callee;
        if (callee && callee.type === "Identifier" && callee.name === "require") {
          return true;
        }
        return false;
      }
      // 일반 expression / statement 경계에 도달 — user-visible 가능.
      case "BlockStatement":
      case "Program":
      case "FunctionDeclaration":
      case "ArrowFunctionExpression":
      case "FunctionExpression":
      case "ClassBody":
        return false;
      default:
        cur = cur.parent;
    }
  }
  return false;
}

// =============================================================================
// Rule definition
// =============================================================================

const rule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Disallow user-visible recommendation / buy / sell vocabulary " +
        "(Speculum 8-pillar §2.2 No Advice).",
      recommended: true,
    },
    schema: [],
    messages: {
      forbiddenWord:
        '"{{word}}" is a forbidden recommendation vocabulary ({{adrRef}}). ' +
        "Replace with neutral phrasing (e.g. \"조건에 부합하는 종목\", " +
        '"필터 통과 항목", "관심 종목").',
      vocabLoadFailed:
        "Speculum forbidden-words vocabulary failed to load from {{path}}: {{error}}. " +
        "The no-forbidden-words gate is non-functional. Run from the monorepo root, " +
        "ensure shared/ exists, and verify shared/forbidden-words.json is valid JSON.",
    },
  },

  create(context) {
    // Degenerate mode — vocab load 실패 시 모든 파일에 1회 error (silent disable 차단).
    if (vocabLoadError) {
      return {
        Program(node) {
          context.report({
            node,
            messageId: "vocabLoadFailed",
            data: {
              path: VOCAB_PATH,
              error: String(vocabLoadError.message || vocabLoadError),
            },
          });
        },
      };
    }

    /**
     * 텍스트와 노드 받아 검출 어휘 report.
     *
     * Column 계산은 단순화 (oracle v3 H3) — 정확한 source position 계산은
     * NFKC normalize · template literal · JSX whitespace 등에서 너무 복잡.
     * 노드 전체 loc 으로 highlight + 메시지에 어휘 표시.
     */
    function reportMatches(text, node) {
      if (isNonUserVisibleContext(node)) return;
      const matches = scanText(text);
      for (const m of matches) {
        context.report({
          node,
          // node.loc 전체로 highlight — 정확한 column 보정의 fragility 회피.
          messageId: "forbiddenWord",
          data: {
            word: m.word,
            adrRef: `${GOVERNANCE_ADR} D4.${m.kind === "ko-absolute" ? "1" : "2"}`,
          },
        });
      }
    }

    return {
      // string literal — "추천", '매수', JSX 속성의 string, object value 등 모든 컨텍스트.
      Literal(node) {
        if (typeof node.value === "string") {
          reportMatches(node.value, node);
        }
      },

      // 백틱 안의 정적 텍스트 부분.
      TemplateElement(node) {
        const raw = node.value && node.value.raw;
        if (typeof raw === "string" && raw.length > 0) {
          reportMatches(raw, node);
        }
      },

      // JSX 의 텍스트 child: <p>오늘의 추천</p>
      JSXText(node) {
        if (typeof node.value === "string") {
          reportMatches(node.value, node);
        }
      },
    };
  },
};

export default rule;
