/**
 * Factor pack import/export — JSON 직렬화 + 붙여넣기 파싱.
 *
 * 저장(user-scoped)은 멀티유저(T69) 의존이라 본 작업 범위 밖. export/import 는
 * 전체 pack JSON 텍스트 왕복만 담당한다. 파싱은 순수 함수로 분리해 단위 테스트.
 *
 * import 파싱 책임:
 *   - JSON.parse 실패 → 명시적 오류.
 *   - top-level 이 object 가 아니거나 factors 배열이 없으면 오류.
 *   - export wrapper marker(`speculum-factor-pack-export-v1`) 인지 — 있으면
 *     `pack` 필드를 추출해 legacy 호환 경로로 전달(D8.5).
 *   - 각 factor 의 누락 필드는 안전한 기본값으로 채워 editor 가 깨지지 않게 함
 *     (최종 무결성은 validate endpoint 가 판정 — client 는 형태만 정규화).
 *
 * export:
 *   - exportPackJson — 순수 stringify(legacy / textarea 표시용).
 *   - pack-io 자체는 backend 호출을 하지 않는다. PackIO 컴포넌트가 export
 *     흐름에서 /api/factor-packs/export 를 직접 호출하고 wrapper JSON 을 내려받아
 *     다운로드한다(D8.5 — content_hash 는 backend 만 계산 가능).
 */

import type {
  FactorDef,
  FactorPack,
  FactorUnit,
} from "@/lib/api/factor-packs";
import { FACTOR_UNITS, PACK_EXPORT_FORMAT_MARKER } from "@/lib/api/factor-packs";
import type { Expr } from "@/lib/factor/ast";

/** export — pack 을 보기 좋은 들여쓰기 JSON 문자열로(legacy / textarea 표시용). */
export function exportPackJson(pack: FactorPack): string {
  return JSON.stringify(pack, null, 2);
}

export interface ParseResult {
  readonly ok: boolean;
  readonly pack?: FactorPack;
  /** 실패 사유(i18n key 가 아닌 원인 텍스트 — UI 가 메시지로 감쌈). */
  readonly error?: string;
}

function asString(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function asUnit(v: unknown): FactorUnit {
  return typeof v === "string" && (FACTOR_UNITS as readonly string[]).includes(v)
    ? (v as FactorUnit)
    : "unitless";
}

function asStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

/** factor 한 건을 editor 형태로 정규화 (누락 필드 기본값). */
function normalizeFactor(raw: unknown): FactorDef {
  const r = (raw ?? {}) as Record<string, unknown>;
  const formula = (r["formula"] ?? {}) as Record<string, unknown>;
  return {
    canonical_id: asString(r["canonical_id"]),
    uuid: asString(r["uuid"]),
    name: asString(r["name"]),
    description: asString(r["description"]),
    unit: asUnit(r["unit"]),
    tags: asStringArray(r["tags"]),
    formula: {
      // ast 는 그대로 보존 (renderFormula/AstBuilder 가 형태를 해석).
      ast: (formula["ast"] ?? null) as Expr | null,
      inputs: asStringArray(formula["inputs"]),
    },
  };
}

/**
 * 붙여넣은 JSON 텍스트 → FactorPack. 실패 시 { ok:false, error }.
 *
 * export wrapper marker(`speculum-factor-pack-export-v1`) 인지:
 *   - `export_format` 필드가 marker 와 일치하면 `pack` 서브필드를 추출해 파싱.
 *   - 없으면 legacy 호환 — top-level 을 그대로 FactorPack 으로 파싱.
 *
 * ADR-0022 D8.5.
 */
export function parsePackJson(text: string): ParseResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (cause) {
    return { ok: false, error: (cause as Error).message };
  }

  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ok: false, error: "not-an-object" };
  }

  let obj = parsed as Record<string, unknown>;

  // export wrapper marker 인지 — 있으면 `pack` 서브필드를 파싱 대상으로 교체.
  if (obj["export_format"] === PACK_EXPORT_FORMAT_MARKER) {
    const inner = obj["pack"];
    if (inner === null || typeof inner !== "object" || Array.isArray(inner)) {
      return { ok: false, error: "not-an-object" };
    }
    obj = inner as Record<string, unknown>;
  }
  if (!Array.isArray(obj["factors"])) {
    return { ok: false, error: "missing-factors" };
  }

  const citationRaw = (obj["citation"] ?? {}) as Record<string, unknown>;
  const pack: FactorPack = {
    pack_slug: asString(obj["pack_slug"]),
    version: asString(obj["version"], "0.1.0"),
    factors: (obj["factors"] as unknown[]).map(normalizeFactor),
    citation: {
      title: asString(citationRaw["title"]),
      ...(typeof citationRaw["version"] === "string"
        ? { version: citationRaw["version"] }
        : {}),
      ...(typeof citationRaw["url"] === "string" ? { url: citationRaw["url"] } : {}),
      ...(typeof citationRaw["publisher"] === "string"
        ? { publisher: citationRaw["publisher"] }
        : {}),
      ...(typeof citationRaw["doi"] === "string" ? { doi: citationRaw["doi"] } : {}),
      ...(typeof citationRaw["note"] === "string"
        ? { note: citationRaw["note"] }
        : {}),
    },
  };

  return { ok: true, pack };
}
