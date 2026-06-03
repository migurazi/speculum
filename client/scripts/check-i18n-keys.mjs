#!/usr/bin/env node
/**
 * i18n keys 게이트 (T67) — 코드의 t('...') 참조 키가 messages/ko/*.json 카탈로그에
 * 존재하는지 검증한다. 텍스트 검사(no-forbidden-words)나 typecheck 가 잡지 못하는
 * "키 오타 / 카탈로그 누락 / 점진 전환 중 잔존" 을 CI 에서 차단.
 *
 * 검사:
 *   1. 누락(ERROR, exit 1): 코드가 참조하는 정적 키가 어느 네임스페이스에도 없음.
 *      (예: t('titel') 오타, 카탈로그에 키 안 넣음 → 런타임에 키 문자열 그대로 노출.)
 *   2. 미사용(WARN, exit 0 유지): 카탈로그에 있으나 코드의 정적 참조에 안 잡힌 키.
 *      동적 키(t(`a.${x}`))로 참조되는 항목은 정적 추출 불가 → false positive 가능.
 *      따라서 fail 시키지 않고 위생 신호로만 출력(silent truncation 금지 — 명시).
 *
 * 한계(의도적):
 *   - 동적 키 t(`prefix.${var}`) 는 정적 추출 대상이 아니다 → 누락 검사에서 제외,
 *     미사용 검사에서 false positive 유발(그래서 WARN). 동적 키 prefix 전체를
 *     ALLOW_UNUSED_PREFIXES 로 화이트리스트.
 *   - 한 파일에 useTranslations 네임스페이스가 여럿이면, t('key') 가 그중 어느
 *     ns 인지 정적으로 단정 못 함 → 파일의 ns 후보 중 하나라도 카탈로그에 있으면
 *     OK(느슨). "키 자체가 카탈로그에 전무" 한 누락만 잡는다(주 목적).
 */

import { readdirSync, readFileSync } from "node:fs";
import { basename, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const messagesDir = join(root, "messages", "ko");
const scanDirs = ["app", "components"].map((d) => join(root, d));

// 동적 키(t(`...${x}`))로 참조되는 prefix — 미사용 false positive 화이트리스트.
// 정적 추출이 불가능한 동적 참조만 등록(새 동적 패턴 도입 시 prefix 추가):
//   - home.quickLinks: app/page.tsx 의 t(`quickLinks.${key}.title`).
//   - common.nav:      components/NavBar.tsx 가 nav 링크를 map 으로 t(`nav.${id}`).
//   - compare.status:  components/Compare/CompareGrid.tsx 의 STATUS_LABEL_KEY 동적 매핑.
const ALLOW_UNUSED_PREFIXES = [
  "home.quickLinks",
  "common.nav",
  "compare.status",
  // lab.factor.units.* — FactorForm 의 t(`factor.units.${u}`) 동적 참조.
  "lab.factor.units",
];

// ── 1. 카탈로그 flatten 키 셋 (네임스페이스 prefix) ──────────────────────────
function flatten(obj, prefix, out) {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      flatten(v, key, out);
    } else {
      out.add(key);
    }
  }
}

const catalogKeys = new Set();
for (const f of readdirSync(messagesDir).filter((f) => f.endsWith(".json"))) {
  const ns = basename(f, ".json");
  const json = JSON.parse(readFileSync(join(messagesDir, f), "utf8"));
  flatten(json, ns, catalogKeys);
}

// ── 2. 코드 스캔 — 파일별 ns + t('literal') 참조 ─────────────────────────────
function walk(dir, files) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name !== "node_modules" && e.name !== "__tests__") walk(p, files);
    } else if (/\.(ts|tsx)$/.test(e.name)) {
      files.push(p);
    }
  }
}

const files = [];
for (const d of scanDirs) walk(d, files);

// useTranslations("ns") / getTranslations("ns") 의 ns.
const NS_RE = /(?:useTranslations|getTranslations)\(\s*["'`]([\w.-]+)["'`]/g;
// t("literal") — 변수명 t 기준(useTranslations 결과 관례). 동적 키는 아래서 스킵.
const T_RE = /\bt\(\s*(["'`])([^"'`]*)\1/g;

const referenced = new Set();
const missing = [];

for (const file of files) {
  const src = readFileSync(file, "utf8");
  const namespaces = [...src.matchAll(NS_RE)].map((m) => m[1]);
  if (namespaces.length === 0) continue; // i18n 미사용 파일.

  for (const m of src.matchAll(T_RE)) {
    const key = m[2];
    // 동적/템플릿/빈 키 — 정적 추출 불가, 누락 검사 제외.
    if (key.length === 0 || key.includes("${") || key.includes("{")) continue;
    const candidates = namespaces.map((ns) => `${ns}.${key}`);
    const found = candidates.find((c) => catalogKeys.has(c));
    if (found) {
      referenced.add(found);
    } else {
      missing.push({ file: relative(root, file), key, tried: candidates });
    }
  }
}

// ── 3. 미사용 키(WARN) — 동적 prefix 화이트리스트 제외 ───────────────────────
// prefix 매칭은 점 경계를 강제하지 않는다 — 동적 키 값이 camelCase
// (예: compare.statusActive, STATUS_LABEL_KEY 의 "statusActive") 인 경우도 흡수.
const unused = [...catalogKeys].filter(
  (k) => !referenced.has(k) && !ALLOW_UNUSED_PREFIXES.some((p) => k.startsWith(p)),
);

// ── 4. 리포트 ────────────────────────────────────────────────────────────────
let failed = false;

if (missing.length > 0) {
  failed = true;
  console.error(`\n[i18n] 누락 키 ${missing.length}건 — 카탈로그에 없는 참조:`);
  for (const m of missing) {
    console.error(`  ${m.file}: t('${m.key}') → 미발견 (시도: ${m.tried.join(", ")})`);
  }
}

if (unused.length > 0) {
  // WARN — fail 시키지 않음(동적 참조 false positive 가능). 단 침묵하지 않고 명시.
  console.warn(`\n[i18n] 미사용 의심 키 ${unused.length}건 (WARN, 동적 참조면 무시):`);
  for (const k of unused.sort()) {
    console.warn(`  ${k}`);
  }
}

if (!failed) {
  console.log(
    `\n[i18n] OK — 카탈로그 키 ${catalogKeys.size}개, 정적 참조 ${referenced.size}개, 누락 0건.`,
  );
}

process.exit(failed ? 1 : 0);
