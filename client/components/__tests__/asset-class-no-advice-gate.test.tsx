/**
 * 자산군 추천 컴포넌트 부재 게이트 — ADR-0023 D4 + ADR-0007 D8.2 자동화.
 *
 * `no-forbidden-words`(eslint) / `check_forbidden_words.py` 는 사용자 노출 **텍스트**
 * 의 추천 *어휘* 를 막고, `lab-visual-gate` 는 평가 표의 *시각요소* 를 막는다.
 * 본 게이트는 그 둘이 포착하지 못하는 **컴포넌트/심볼 이름** 을 막는다 — 즉
 * `<RecommendedStocks>`, `<RecommendedETFs>` 같은 큐레이션 컴포넌트가 코드베이스에
 * *존재하지 않음* 을 자동 강제(ADR-0007 D8.2 가 "검색어 검사 + owner alert" 로
 * 명시했으나 README 상 "in progress" 로 미자동화 상태였던 것을, ADR-0023 D4 의
 * 자산군 변형 게이트 신설과 함께 자동화).
 *
 * 검사 대상: client 프로덕션 소스(app/components/lib 의 .ts/.tsx, **테스트 파일
 * 제외**). 테스트 파일은 사용자 노출 아님 + 본 게이트 자신이 금지명 상수를
 * 담으므로(*.test.tsx) 스캔에서 제외해 자기참조 false positive 를 회피한다.
 *
 * 강제 방식: 각 금지 심볼을 단어경계 정규식으로 검색해 프로덕션 소스 어디에도
 * 식별자로 등장하지 않음을 단언(pytest 의 T73 `BuiltinCompositeError` 부재 단언과
 * 동형 — "그런 것이 없음" 을 회귀로 박는다). 누군가 추천 컴포넌트를 추가하면
 * 즉시 CI 가 빨갛게 뜬다.
 *
 * 관련:
 *  - ADR-0007 D8.2 — 추천/픽/AI제안 컴포넌트 부재 게이트(원본, 미자동화였음)
 *  - ADR-0023 D4 — 자산군 필터의 "추천 자산군" 변질 방어(`<RecommendedETFs>`/
 *    `<TopReits>` 자산군 변형 부재)
 *  - T73 `server/tests/test_factor_pack.py` §12 — 빌트인 composite 부재 게이트(동형)
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
// client/components/__tests__ → client 루트.
const CLIENT_ROOT = resolve(__dirname, "../../");

// 프로덕션 소스 루트만 스캔(테스트·설정·노출 무관 디렉토리 제외).
const SCAN_DIRS = ["app", "components", "lib"];
const SOURCE_EXT = /\.(ts|tsx)$/;
const TEST_FILE = /\.test\.(ts|tsx)$/;
const SKIP_DIRS = new Set(["node_modules", ".next", "dist", "coverage", "__tests__"]);

// 금지 추천/큐레이션 컴포넌트·심볼 이름.
// ADR-0007 D8.2 원본 + ADR-0023 D4 자산군 변형 + 일반 큐레이션.
const FORBIDDEN_ADVICE_SYMBOLS: readonly string[] = [
  // D8.2 원본 — 추천 종목 / 오늘의 픽 / AI 제안.
  "RecommendedStocks",
  "RecommendedStock",
  "TodaysPicks",
  "TodaysPick",
  "TodayPicks",
  "AiSuggestion",
  "AiSuggestions",
  "AiSuggested",
  // D4 — 자산군 변형(ETF/리츠/우선주).
  "RecommendedETFs",
  "RecommendedETF",
  "RecommendedEtf",
  "RecommendedEtfs",
  "RecommendedReits",
  "RecommendedReit",
  "TopReits",
  "TopReit",
  "RecommendedPreferred",
  // 일반 큐레이션 변질.
  "TopPicks",
  "BestStocks",
  "HotStocks",
  "TrendingStocks",
];

/** 디렉토리를 재귀 walk 하며 프로덕션 .ts/.tsx 파일 경로를 모은다(테스트 제외). */
function collectSourceFiles(dir: string): string[] {
  const out: string[] = [];
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return out; // 디렉토리 부재(예: app 미사용 구조) → 빈 목록.
  }
  for (const entry of entries) {
    if (SKIP_DIRS.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...collectSourceFiles(full));
    } else if (SOURCE_EXT.test(entry) && !TEST_FILE.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

const SOURCE_FILES: readonly string[] = SCAN_DIRS.flatMap((d) =>
  collectSourceFiles(join(CLIENT_ROOT, d)),
);

describe("자산군 추천 컴포넌트 부재 게이트 (ADR-0023 D4 / ADR-0007 D8.2)", () => {
  it("스캔 대상 프로덕션 소스가 존재(게이트가 빈 집합을 통과로 오인하지 않음)", () => {
    // SOURCE_FILES 가 0 이면 경로 가정이 틀린 것 — 게이트가 무력화되므로 fail-loud.
    expect(SOURCE_FILES.length).toBeGreaterThan(0);
  });

  for (const symbol of FORBIDDEN_ADVICE_SYMBOLS) {
    it(`'${symbol}' 추천 컴포넌트/심볼이 프로덕션 소스에 부재`, () => {
      const pattern = new RegExp(`\\b${symbol}\\b`);
      const hits = SOURCE_FILES.filter((f) =>
        pattern.test(readFileSync(f, "utf-8")),
      ).map((f) => f.slice(CLIENT_ROOT.length + 1));
      expect(
        hits,
        `금지 추천 심볼 '${symbol}' 가 발견됨: ${hits.join(", ")}. ` +
          "ADR-0023 D4 / ADR-0007 D8.2 — 큐레이션 컴포넌트 금지(중립 명명 사용).",
      ).toEqual([]);
    });
  }
});
