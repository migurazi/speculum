/**
 * 금지 어휘 검사 — 8 기둥 §2.2 No Advice 의 클라이언트 측 implementation.
 *
 * Python `server/app/services/forbidden_words.py` 의 미러. 어휘 list 의 SoT 는
 * `shared/forbidden-words.json` — Python 도 동일 JSON 을 import 하므로 분기 불가
 * (oracle 리뷰 D1).
 *
 * 관련 ADR:
 *  - ADR-0006 §D2 — 법적 회피 근거
 *  - ADR-0007 §D4 — 금지 어휘 정책 + CI 게이트
 *  - ADR-0007 §D4.5 — CheckScope 별 정책
 *  - ADR-0007 §D9 — 어휘 변경 절차
 *
 * 사용 예:
 *   import { scanText, assertClean, CheckScope } from "@/lib/forbidden-words";
 *
 *   assertClean(cardTitle, { scope: "system", context: "stock-detail-card" });
 *   assertClean(stockName, { scope: "external-quote" });  // 검사 skip
 *
 *   const matches = scanApiResponse(payload, { excludePaths: ["stock_name"] });
 *
 * 설계 결정 (oracle 리뷰 반영):
 *  - JSON SoT 를 빌드 타임 import (D1).
 *  - CheckScope 4 종으로 호출 의도 명시 (C3).
 *  - excludePaths 로 회사명 등 false positive 차단 (C1).
 *  - NFKC normalize 로 fullwidth 회피 차단 (G4).
 *  - Iterative walker (B2).
 */

import vocab from "../../shared/forbidden-words.json" with { type: "json" };

export type ForbiddenKind = "ko-absolute" | "en-absolute";

/**
 * 검사 scope — ADR-0007 D4.5.
 *
 * - `system`: 시스템 생성 텍스트 (UI 라벨 등). 가장 엄격.
 * - `user-private`: 사용자 본인만 보는 텍스트 (Watchlist 메모 M0). 검사 X.
 * - `user-shared`: 다른 사용자에게 공유되는 텍스트 (Factor Lab pack M2). 검사 함.
 * - `external-quote`: 회사명·DART 공시 제목 등 외부 인용. 검사 X.
 */
export type CheckScope =
  | "system"
  | "user-private"
  | "user-shared"
  | "external-quote";

export interface Match {
  /** 검출된 원문 어휘 (NFKC normalize 후). */
  readonly word: string;
  /** 등록된 표준형 (Buy/buy/BUY/Ｂｕｙ 모두 "Buy"). */
  readonly canonical: string;
  /** 분류. */
  readonly kind: ForbiddenKind;
  /** 정규화된 텍스트에서의 시작 인덱스 (0-based, UTF-16 code unit). */
  readonly index: number;
}

// JSON SoT — 빌드 타임 import. Python `forbidden_words.py` 가 동일 JSON 을 load.
const KO_FORBIDDEN_RAW: readonly string[] = vocab.ko_absolute;
const EN_FORBIDDEN_RAW: readonly string[] = vocab.en_absolute;
const ALLOWED_PHRASES: readonly string[] = vocab.allowed_phrases;

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// 길이 내림차순 + codepoint 순 (Python 정렬과 일관 — oracle D4).
function sortByLengthDesc(words: readonly string[]): string[] {
  return [...new Set(words)].sort((a, b) => {
    if (a.length !== b.length) return b.length - a.length;
    return a < b ? -1 : a > b ? 1 : 0;
  });
}

// Pattern source string 만 module-scope 에 보존 — oracle v3 H1.
// `lastIndex` 재진입 hazard 차단을 위해 RegExp 객체는 scanText 매 호출마다 fresh.
// V8 가 같은 source string 의 RegExp 컴파일을 캐싱하므로 비용은 사실상 0.
const KO_PATTERN_SRC = sortByLengthDesc(KO_FORBIDDEN_RAW).map(escapeRegex).join("|");
const EN_PATTERN_SRC = `\\b(?:${sortByLengthDesc(EN_FORBIDDEN_RAW).map(escapeRegex).join("|")})\\b`;

function freshKoPattern(): RegExp {
  return new RegExp(KO_PATTERN_SRC, "g");
}

function freshEnPattern(): RegExp {
  return new RegExp(EN_PATTERN_SRC, "gi");
}

function buildAllowedSpans(text: string): readonly [number, number][] {
  const spans: [number, number][] = [];
  for (const phrase of ALLOWED_PHRASES) {
    let start = 0;
    let idx: number;
    while ((idx = text.indexOf(phrase, start)) !== -1) {
      spans.push([idx, idx + phrase.length]);
      start = idx + 1;
    }
  }
  return spans;
}

function isInsideAnySpan(
  start: number,
  end: number,
  spans: readonly [number, number][],
): boolean {
  return spans.some(([s, e]) => s <= start && end <= e);
}

/**
 * NFKC 정규화 — fullwidth `Ｂｕｙ` → `Buy` 같은 회피 차단 (oracle G4).
 */
export function normalize(text: string): string {
  return text.normalize("NFKC");
}

/**
 * 텍스트에서 금지 어휘 모두 검출. 빈 입력은 빈 배열.
 *
 * 내부적으로 NFKC normalize 후 검사. Match.index 는 normalize 된 텍스트 기준.
 */
export function scanText(text: string): Match[] {
  if (!text) return [];

  const normalized = normalize(text);
  const allowed = buildAllowedSpans(normalized);
  const results: Match[] = [];

  // Fresh RegExp — module-scope 재진입 hazard 차단 (oracle v3 H1).
  const koPattern = freshKoPattern();
  const enPattern = freshEnPattern();

  let koMatch: RegExpExecArray | null;
  while ((koMatch = koPattern.exec(normalized)) !== null) {
    const start = koMatch.index;
    const end = start + koMatch[0].length;
    if (isInsideAnySpan(start, end, allowed)) continue;
    results.push({
      word: koMatch[0],
      canonical: koMatch[0],
      kind: "ko-absolute",
      index: start,
    });
  }

  let enMatch: RegExpExecArray | null;
  while ((enMatch = enPattern.exec(normalized)) !== null) {
    const start = enMatch.index;
    const end = start + enMatch[0].length;
    if (isInsideAnySpan(start, end, allowed)) continue;
    const lowered = enMatch[0].toLowerCase();
    const canonical =
      EN_FORBIDDEN_RAW.find((w) => w.toLowerCase() === lowered) ?? enMatch[0];
    results.push({
      word: enMatch[0],
      canonical,
      kind: "en-absolute",
      index: start,
    });
  }

  results.sort((a, b) => a.index - b.index);
  return results;
}

export interface ScanApiOptions {
  /** 검사 제외 dict key 이름 (예: ["stock_name", "company_name"]). oracle C1. */
  readonly excludePaths?: readonly string[];
  /** 재귀 깊이 한계. 기본 64. */
  readonly maxDepth?: number;
}

const DEFAULT_MAX_DEPTH = 64;

/**
 * 임의 JSON-like 객체의 모든 문자열 값에 대해 검사.
 *
 * Iterative walker (스택 기반) — 무한 자기참조 JSON 의 stack overflow 방지.
 *
 * @throws Error maxDepth 초과 시.
 */
export function scanApiResponse(
  payload: unknown,
  options: ScanApiOptions = {},
): Match[] {
  const excludeSet = new Set(options.excludePaths ?? []);
  const maxDepth = options.maxDepth ?? DEFAULT_MAX_DEPTH;
  const matches: Match[] = [];
  const stack: Array<[unknown, number]> = [[payload, 0]];

  while (stack.length > 0) {
    const popped = stack.pop();
    if (popped === undefined) break;
    const [node, depth] = popped;

    if (depth > maxDepth) {
      throw new Error(
        `Max recursion depth ${maxDepth} exceeded — possible self-referential JSON`,
      );
    }

    if (typeof node === "string") {
      matches.push(...scanText(node));
    } else if (Array.isArray(node)) {
      for (const item of node) stack.push([item, depth + 1]);
    } else if (node !== null && typeof node === "object") {
      for (const [key, value] of Object.entries(node)) {
        if (excludeSet.has(key)) continue;
        stack.push([value, depth + 1]);
      }
    }
  }

  return matches;
}

export interface AssertOptions {
  /** 검사 scope. 기본 "system" (가장 엄격). */
  readonly scope?: CheckScope;
  /** 에러 메시지의 디버깅용 컨텍스트. **사용자 입력 직접 전달 금지** (oracle B4). */
  readonly context?: string;
}

/**
 * 텍스트가 깨끗하지 않으면 throw. 런타임 / 테스트 게이트.
 *
 * **주의 (oracle B4)**: throw 된 Error 메시지를 클라이언트 응답에 그대로 노출하면
 * 금지 어휘가 사용자에게 echo 됨 (의도 반대 효과). 에러 핸들러에서 generic 메시지로
 * 변환할 것.
 */
export function assertClean(text: string, options: AssertOptions = {}): void {
  const scope = options.scope ?? "system";
  if (scope === "user-private" || scope === "external-quote") return;

  const matches = scanText(text);
  if (matches.length > 0) {
    const details = matches
      .map((m) => `${JSON.stringify(m.word)}@${m.index}`)
      .join(", ");
    // context 의 control character 제거 (log injection 방어).
    const safeContext = options.context
      ? options.context.replace(/[\x00-\x1f]/g, "?")
      : "";
    const suffix = safeContext ? ` [${safeContext}]` : "";
    throw new Error(
      `Forbidden words detected${suffix} (scope=${scope}): ${details}`,
    );
  }
}
