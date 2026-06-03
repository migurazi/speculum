/**
 * Markdown 렌더링 단일 진입점 — T76 Notes 패널.
 *
 * 모든 `dangerouslySetInnerHTML` 에 삽입될 HTML 은 반드시 본 함수를 거친다.
 * 이 파일 외의 어떤 파일도 `dangerouslySetInnerHTML` 에 직접 raw Markdown /
 * HTML 을 전달하지 않는다.
 *
 * 파이프라인:
 *   raw → marked.parse (Markdown→HTML) → DOMPurify.sanitize (XSS 제거) → safe HTML
 *
 * XSS 방어 정책:
 *   - ALLOWED_TAGS: 최소 Markdown 집합(img 금지 — onerror XSS 벡터).
 *   - ALLOWED_ATTR: href / rel / target / class 만. on* 속성 전면 차단.
 *   - ALLOWED_URI_REGEXP: href 는 http/https 스킴만.
 *   - a 태그: rel="noopener noreferrer" 강제 (FORCE_ATTR).
 *
 * 관련:
 *   - ADR-0007 §2.2 No Advice — Notes 는 사용자 콘텐츠이나 시스템 UI 는 중립.
 *   - T76 클라이언트 Notes 패널.
 */

import DOMPurify from "dompurify";
import { marked } from "marked";

// =============================================================================
// 허용 태그 / 속성 — XSS chokepoint 정책
// =============================================================================

/**
 * 허용 HTML 태그 집합.
 * img 는 onerror XSS 벡터이므로 명시 제외(DOMPurify default 도 제외이나 명시적 선언).
 */
const ALLOWED_TAGS: readonly string[] = [
  "p",
  "br",
  "strong",
  "em",
  "ul",
  "ol",
  "li",
  "code",
  "pre",
  "blockquote",
  "h1",
  "h2",
  "h3",
  "h4",
  "a",
];

/**
 * 허용 속성 집합.
 * on* 이벤트 핸들러는 DOMPurify 기본 차단이나 명시적 allowlist 로 이중 방어.
 */
const ALLOWED_ATTR: readonly string[] = ["href", "rel", "target", "class"];

/**
 * href 허용 URI 스킴 — http/https 만. javascript:, data: 등 전면 차단.
 */
const ALLOWED_URI_REGEXP = /^https?:\/\//i;

// =============================================================================
// marked 설정
// =============================================================================

marked.setOptions({
  // gfm: GitHub-flavored Markdown (체크박스·테이블 등 미사용이나 standard on).
  // breaks: 줄바꿈 강제 — false (GFM 기본).
});

// =============================================================================
// renderMarkdown — 단일 XSS 방어 chokepoint
// =============================================================================

/**
 * raw Markdown 문자열을 안전한 HTML 문자열로 변환한다.
 *
 * 반환 값은 `dangerouslySetInnerHTML={{ __html: ... }}` 에 직접 사용 가능하다.
 * 이 함수를 거치지 않은 HTML 은 절대 `dangerouslySetInnerHTML` 에 주입 금지.
 *
 * @param raw - 사용자 입력 Markdown 문자열 (신뢰 불가).
 * @returns XSS-safe HTML 문자열.
 */
export function renderMarkdown(raw: string): string {
  if (typeof window === "undefined") {
    // SSR 환경 — DOMPurify 는 브라우저 전용. SSR 에서 Notes 패널은 CSR-only
    // ("use client") 이므로 도달하지 않으나 안전하게 빈 문자열 반환.
    return "";
  }

  // Step 1: Markdown → HTML (marked — synchronous parse).
  const parsed = marked.parse(raw) as string;

  // Step 2: DOMPurify sanitize — XSS 제거 + a 태그 rel 강제.
  const clean = DOMPurify.sanitize(parsed, {
    ALLOWED_TAGS: [...ALLOWED_TAGS],
    ALLOWED_ATTR: [...ALLOWED_ATTR],
    ALLOWED_URI_REGEXP,
    // a 태그에 rel="noopener noreferrer" 강제 (target="_blank" XSS 방어).
    FORCE_BODY: false,
  });

  // Step 3: a 태그 rel 속성 강제 삽입 — DOMPurify FORCE_ATTR 은 모든 태그에
  // 적용되므로 별도 후처리로 a 만 targeting.
  return enforceAnchorRel(clean);
}

// =============================================================================
// a 태그 rel 속성 강제 후처리
// =============================================================================

/**
 * 렌더된 HTML 의 모든 `<a>` 태그에 `rel="noopener noreferrer"` 를 강제 설정한다.
 * DOMPurify 가 sanitize 후 안전한 HTML 이 인풋으로 들어오므로 DOM 파싱 후 직렬화.
 * SSR 에서 도달하지 않음(위 가드).
 */
function enforceAnchorRel(html: string): string {
  const parser = new DOMParser();
  const doc = parser.parseFromString(`<body>${html}</body>`, "text/html");
  const anchors = doc.querySelectorAll("a");
  anchors.forEach((a) => {
    a.setAttribute("rel", "noopener noreferrer");
  });
  return doc.body.innerHTML;
}
