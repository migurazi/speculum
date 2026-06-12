/**
 * next-intl 요청 설정 — i18n routing 없는 단일 고정 locale 구성.
 *
 * 본 프로젝트는 M0~M1 단일 언어(ko)이므로 next-intl 공식 "without i18n
 * routing" 패턴을 사용한다. URL 에 locale prefix 가 없고, 모든 요청이
 * 아래 LOCALE 로 고정된다. 향후 다국어 확장 시 routing 도입을 위한 구조만
 * 갖춰 둔다.
 *
 * 네임스페이스 머지 전략 (Phase B 병렬 전환 충돌 회피 — 중대):
 *   messages/ko/ 디렉터리의 *.json 파일을 런타임에 동적으로 모두 읽어,
 *   "파일명(확장자 제외) = top-level 네임스페이스" 로 머지한다. 명시적
 *   import 나열이 없으므로 새 네임스페이스 파일을 추가해도 본 파일을 수정할
 *   필요가 없다 → Phase B 의 여러 agent 가 서로 다른 json 파일을 동시
 *   편집/추가해도 git·편집 충돌이 발생하지 않는다.
 *
 * 관련 문서:
 * - messages/README.md (네임스페이스 규약 + Phase B 전환 가이드).
 * - 8 기둥 §2.2 No Advice — 키 값(번역 문자열)에도 금지 어휘 금지.
 */

import { promises as fs } from "node:fs";
import path from "node:path";

import { getRequestConfig } from "next-intl/server";

/** 단일 고정 locale. 향후 다국어 확장 시 진입점. */
const LOCALE = "ko";

/** 네임스페이스 json 이 위치한 디렉터리. */
const MESSAGES_DIR = path.join(process.cwd(), "messages", LOCALE);

/**
 * messages/ko/*.json 을 모두 읽어 네임스페이스 단위로 머지한다.
 * 파일명(확장자 제외)이 top-level 네임스페이스 키가 된다
 * (예: compare.json → messages.compare).
 */
async function loadMessages(): Promise<Record<string, unknown>> {
  const entries = await fs.readdir(MESSAGES_DIR);
  const jsonFiles = entries.filter((name) => name.endsWith(".json")).sort();

  const messages: Record<string, unknown> = {};
  for (const file of jsonFiles) {
    const namespace = path.basename(file, ".json");
    const raw = await fs.readFile(path.join(MESSAGES_DIR, file), "utf-8");
    messages[namespace] = JSON.parse(raw) as unknown;
  }
  return messages;
}

export default getRequestConfig(async () => ({
  locale: LOCALE,
  messages: await loadMessages(),
}));
