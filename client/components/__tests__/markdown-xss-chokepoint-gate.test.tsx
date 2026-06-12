/**
 * XSS chokepoint 게이트 — T76 Notes Markdown 렌더 (ADR-0007 D4.5 / R6).
 *
 * ESLint react/no-danger 로 1차 강제(components/** 전면 금지 + 검증된 chokepoint 호출처만 명시 예외),
 * 본 게이트는 보조 tripwire(문자열 휴리스틱 — data-flow 미추적).
 *
 * `client/lib/markdown.ts` 의 `renderMarkdown` 은 `dangerouslySetInnerHTML` 에
 * 삽입될 HTML 을 만드는 **단일 chokepoint**(DOMPurify sanitize). 이 게이트는
 * 그 불변식을 강제한다: 프로덕션 소스에서 `dangerouslySetInnerHTML` 을 쓰는 파일은
 * 반드시 `renderMarkdown` 을 거쳐야 한다(= 그 식별자를 참조해야 한다). 누군가
 * sanitize 를 건너뛰고 raw HTML / marked 출력을 직접 주입하면 즉시 CI 가 빨갛게.
 *
 * asset-class-no-advice-gate.test.tsx 의 소스 스캔 패턴과 동형. 테스트 파일은
 * 스캔 제외(악성 입력 문자열을 담으므로 자기참조 false positive 회피).
 *
 * 관련:
 *  - ADR-0007 D4.5 — Notes XSS: DOMPurify 단일 chokepoint, dangerouslySetInnerHTML
 *    는 그 chokepoint import 파일만.
 *  - work-order R6 — Notes Markdown 렌더 XSS(허용 태그 whitelist).
 *  - client/lib/markdown.ts — renderMarkdown chokepoint.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
// client/components/__tests__ → client 루트.
const CLIENT_ROOT = resolve(__dirname, "../../");

const SCAN_DIRS = ["app", "components", "lib"];
const SOURCE_EXT = /\.(ts|tsx)$/;
const TEST_FILE = /\.test\.(ts|tsx)$/;
const SKIP_DIRS = new Set(["node_modules", ".next", "dist", "coverage", "__tests__"]);

// dangerouslySetInnerHTML 사용 신호 + chokepoint 경유 신호.
const DANGEROUS_HTML = "dangerouslySetInnerHTML";
const CHOKEPOINT = "renderMarkdown";

/** 디렉토리 재귀 walk — 프로덕션 .ts/.tsx 만(테스트 제외). */
function collectSourceFiles(dir: string): string[] {
  const out: string[] = [];
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
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

describe("XSS chokepoint 게이트 (ADR-0007 D4.5 / T76 Notes)", () => {
  it("스캔 대상 프로덕션 소스가 존재(게이트 무력화 방지)", () => {
    expect(SOURCE_FILES.length).toBeGreaterThan(0);
  });

  it("dangerouslySetInnerHTML 사용 파일은 renderMarkdown chokepoint 를 거친다", () => {
    const violations: string[] = [];
    for (const file of SOURCE_FILES) {
      const content = readFileSync(file, "utf-8");
      if (!content.includes(DANGEROUS_HTML)) continue;
      // chokepoint(renderMarkdown) 정의 파일 자신 또는 그것을 참조하는 파일만 허용.
      if (!content.includes(CHOKEPOINT)) {
        violations.push(file.slice(CLIENT_ROOT.length + 1));
      }
    }
    expect(
      violations,
      `dangerouslySetInnerHTML 을 renderMarkdown chokepoint 없이 사용: ` +
        `${violations.join(", ")}. ADR-0007 D4.5 — 모든 HTML 주입은 ` +
        `lib/markdown.ts 의 renderMarkdown(DOMPurify sanitize)을 거쳐야 합니다.`,
    ).toEqual([]);
  });
});
