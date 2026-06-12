/**
 * renderMarkdown XSS 방어 + 정상 Markdown 보존 테스트 — T76 P0.
 *
 * 검증:
 *   1. <script> 태그가 출력에 없다.
 *   2. onerror 속성이 출력에 없다.
 *   3. <img> 태그가 출력에 없다 (onerror XSS 벡터 차단).
 *   4. **bold** → <strong> 보존.
 *   5. [link](http://x) → <a> 보존 + rel=noopener noreferrer 강제.
 *   6. javascript: href 가 제거된다.
 *   7. data: URI 가 제거된다.
 *   8. <blockquote> 보존.
 *   9. `code` → <code> 보존.
 */

import { describe, expect, it } from "vitest";

// jsdom 환경에서 DOMPurify 가 window 를 필요로 한다. vitest.config.ts 가
// environment="jsdom" 이므로 정상 동작.

// DOMParser + window stub — vitest jsdom 이 제공하나 DOMPurify 가
// sanitize 전에 window 존재를 검사하므로 vitest.setup 이 이미 jsdom을 활성.
import { renderMarkdown } from "@/lib/markdown";

describe("renderMarkdown — XSS 방어", () => {
  it("<script>alert(1)</script> 입력 — 출력에 script 태그 없음", () => {
    const result = renderMarkdown("<script>alert(1)</script>");
    expect(result).not.toContain("<script");
    expect(result).not.toContain("alert(1)");
  });

  it("<img src=x onerror=alert(1)> 입력 — img 태그 및 onerror 없음", () => {
    const result = renderMarkdown("<img src=x onerror=alert(1)>");
    expect(result).not.toContain("<img");
    expect(result).not.toContain("onerror");
  });

  it("onclick 속성이 있는 a 태그 — onclick 제거", () => {
    const result = renderMarkdown('<a href="http://example.com" onclick="alert(1)">link</a>');
    expect(result).not.toContain("onclick");
  });

  it("javascript: href — href 제거 또는 태그 제거", () => {
    const result = renderMarkdown("[evil](javascript:alert(1))");
    expect(result).not.toContain("javascript:");
  });

  it("data: URI href — href 제거 또는 태그 제거", () => {
    const result = renderMarkdown('[evil](data:text/html,<script>alert(1)</script>)');
    expect(result).not.toContain("data:");
  });
});

describe("renderMarkdown — 정상 Markdown 보존", () => {
  it("**bold** → <strong> 태그 보존", () => {
    const result = renderMarkdown("**bold**");
    expect(result).toContain("<strong>");
    expect(result).toContain("bold");
    expect(result).toContain("</strong>");
  });

  it("[link](http://x) → <a> 태그 보존 + rel=noopener noreferrer 강제", () => {
    const result = renderMarkdown("[link](http://x)");
    expect(result).toContain("<a");
    expect(result).toContain("href");
    expect(result).toContain("noopener");
    expect(result).toContain("noreferrer");
    expect(result).toContain("link");
  });

  it("https:// href — 허용됨", () => {
    const result = renderMarkdown("[secure](https://example.com)");
    expect(result).toContain("https://example.com");
  });

  it("*italic* → <em> 태그 보존", () => {
    const result = renderMarkdown("*italic*");
    expect(result).toContain("<em>");
    expect(result).toContain("</em>");
  });

  it("`code` → <code> 태그 보존", () => {
    const result = renderMarkdown("`code`");
    expect(result).toContain("<code>");
  });

  it("> blockquote → <blockquote> 태그 보존", () => {
    const result = renderMarkdown("> quote");
    expect(result).toContain("<blockquote>");
  });

  it("빈 문자열 → 빈 결과 (crash 없음)", () => {
    expect(() => renderMarkdown("")).not.toThrow();
  });
});
