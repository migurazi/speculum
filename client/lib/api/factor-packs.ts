/**
 * Factor Pack API client — validate / evaluate / export / import-check / import / save.
 *
 * Factor Lab editor 가 작성 중인 pack 정의를 backend endpoint 로 보내
 * 검증·평가·export·충돌 검사·import·사용자 저장을 수행한다.
 *
 * 설계:
 *   - fetchJson wrapper 재사용 (client.ts 패턴).
 *   - body = pack JSON 그대로 (shared/schemas/factor-pack-v1.json 구조).
 *   - 응답 wire snake→camel 매핑은 evaluateFactorPack 패턴을 따른다.
 *
 * export/import 계약 (ADR-0022 D8 / T75):
 *   - POST /api/factor-packs/export → ExportResult (content_hash 봉인 wrapper).
 *   - POST /api/factor-packs/import-check → ImportCheckResult (dry-run 충돌 리스트).
 *   - POST /api/factor-packs/import → ImportResult (해소 후 봉인 pack).
 *
 * 저장 계약 (ADR-0022 D9 / T69 — user-scoped 영속):
 *   - POST /api/factor-packs/saved → SavedPackOut (id·slug·버전·factor_count).
 *   - GET  /api/factor-packs/saved → { items: SavedPackOut[] }.
 *   - GET  /api/factor-packs/saved/{id} → FactorPack (전체 body).
 *   - DELETE /api/factor-packs/saved/{id} → 204.
 */

import type { Expr } from "@/lib/factor/ast";

import { fetchJson } from "./client";

/** citation — schema 의 #/$defs/citation (title 필수). */
export interface PackCitation {
  title: string;
  version?: string;
  url?: string;
  publisher?: string;
  doi?: string;
  note?: string;
}

/** formula — { ast, inputs }. */
export interface FactorFormula {
  ast: Expr | null;
  inputs: string[];
}

/** 단일 factor 정의 — schema 의 #/$defs/factor (편집 중 부분 상태 허용). */
export interface FactorDef {
  canonical_id: string;
  uuid: string;
  name: string;
  description: string;
  unit: FactorUnit;
  tags: string[];
  formula: FactorFormula;
}

/** unit enum — schema 의 unit. */
export type FactorUnit =
  | "ratio"
  | "percent"
  | "krw"
  | "shares"
  | "unitless"
  | "days";

export const FACTOR_UNITS: ReadonlyArray<FactorUnit> = [
  "ratio",
  "percent",
  "krw",
  "shares",
  "unitless",
  "days",
];

/**
 * Factor pack — editor 상태. schema 의 일부 필드(publisher/license/created_at/
 * content_hash 등)는 backend 가 검증 시 채우거나 export 시 부가되므로 editor 가
 * 직접 다루는 핵심 필드만 둔다. export 시 전체 JSON 으로 직렬화.
 */
export interface FactorPack {
  pack_slug: string;
  version: string;
  factors: FactorDef[];
  citation: PackCitation;
}

/** 검증 issue — stage ∈ schema/identity/acyclic/citation/forbidden_vocab. */
export interface ValidationIssue {
  readonly stage: string;
  readonly message: string;
}

/** POST /api/factor-packs/validate 응답. */
export interface ValidationResult {
  readonly valid: boolean;
  readonly issues: ReadonlyArray<ValidationIssue>;
}

/**
 * POST /api/factor-packs/validate — pack 정의 검증.
 *
 * @param pack  factor pack JSON (작성 중 부분 상태도 전송 — backend 가 stage 별 issue 반환)
 * @param signal AbortController signal (호출자가 cancel 시 주입)
 * @returns { valid, issues }
 * @throws ApiError — fetch 실패 또는 non-2xx 응답.
 */
export async function validateFactorPack(
  pack: unknown,
  signal?: AbortSignal,
): Promise<ValidationResult> {
  return fetchJson<ValidationResult>("/api/factor-packs/validate", {
    method: "POST",
    body: pack,
    signal,
  });
}

/** codes 입력 상한 — backend 와 동일(POST evaluate 가 20 초과를 거부). */
export const EVALUATE_MAX_CODES = 20;

/**
 * 단일 factor 평가 셀 — POST evaluate 의 result.factors[] 요소.
 *
 * wire(snake) → camel 매핑: is_na → isNa, na_reason → naReason,
 * sample_size → sampleSize, small_sample → smallSample.
 * `value` 는 str|null — 백엔드가 정밀도 보존 위해 문자열로 반환(부동소수 왜곡
 * 회피). percentile 등 유니버스-상대 op 도 실값(str). UI 는 이 값을 **사실로만**
 * 표시 — 부호/크기로 색칠하거나 순위 매기지 않는다(No Advice / ADR-0022 D4).
 *
 * 소표본 필드(ADR-0024 Phase 2 / §2.5):
 *   - sampleSize: 유니버스-상대 factor 의 모집단 크기(여러 field 면 min).
 *     종목-국소 factor 는 null.
 *   - smallSample: sampleSize < 30(SMALL_SAMPLE_THRESHOLD). 유니버스-상대
 *     아니면 false. n==1 의 percentile=100 도 small_sample=true — "최상위" 오인
 *     차단이 핵심 목적(ADR-0024 D3).
 *   - 값 자체는 소표본이어도 보존 표시 — N/A 가 아님. 모집단 사실만 중립 고지.
 */
export interface EvaluatedFactor {
  readonly canonicalId: string;
  readonly name: string;
  readonly unit: FactorUnit | string;
  /** 산출값(문자열, 정밀도 보존). is_na=true 면 null. */
  readonly value: string | null;
  /** 결손 여부 — true 면 value=null, 셀은 "N/A" 로 표시. */
  readonly isNa: boolean;
  /** 결손 사유(데이터 부재/입력 결손 등). is_na=false 면 null. */
  readonly naReason: string | null;
  /**
   * 유니버스-상대 factor 의 모집단 크기(여러 field 면 min). 종목-국소면 null.
   * UI 는 이 값을 "모집단 N개" 사실로만 표시 — 품질 판단·추천어 금지(§2.2).
   */
  readonly sampleSize: number | null;
  /**
   * sampleSize < SMALL_SAMPLE_THRESHOLD(30). 유니버스-상대 아니면 false.
   * true 일 때만 소표본 디스클로저를 표시 — 경고가 아닌 사실 고지.
   */
  readonly smallSample: boolean;
}

/** 한 종목의 평가 행 — code + factor 셀 배열(pack 정의 순서 보존). */
export interface EvaluatedRow {
  readonly code: string;
  readonly factors: ReadonlyArray<EvaluatedFactor>;
}

/**
 * POST /api/factor-packs/evaluate 응답.
 *
 * invalid pack 이면 valid=false + issues(검증 stage/message), results 는 빈 배열.
 * valid 면 results 가 codes **입력 순서 그대로**(default sort ≠ score, ADR-0022 D1).
 */
export interface EvaluateResult {
  readonly valid: boolean;
  readonly issues: ReadonlyArray<ValidationIssue>;
  readonly results: ReadonlyArray<EvaluatedRow>;
}

/** wire(snake) result row — 매핑 입력 타입. */
interface EvaluateResultWire {
  valid: boolean;
  issues: ReadonlyArray<ValidationIssue>;
  results: ReadonlyArray<{
    code: string;
    factors: ReadonlyArray<{
      canonical_id: string;
      name: string;
      unit: string;
      value: string | null;
      is_na: boolean;
      na_reason: string | null;
      /** 유니버스-상대 factor 의 모집단 크기. 종목-국소면 null. */
      sample_size: number | null;
      /** sampleSize < SMALL_SAMPLE_THRESHOLD. 유니버스-상대 아니면 false. */
      small_sample: boolean;
    }>;
  }>;
}

/**
 * export wrapper marker 상수 — backend POST /api/factor-packs/export 응답의
 * `export_format` 필드 값. import 측에서 wrapper 인지에 사용.
 * ADR-0022 D8.5.
 */
export const PACK_EXPORT_FORMAT_MARKER = "speculum-factor-pack-export-v1";

/**
 * POST /api/factor-packs/export 응답.
 *
 * backend 가 content_hash 를 봉인해 반환하는 self-identifying wrapper.
 * pack 필드는 봉인된 FactorPack 본체(content_hash 포함).
 */
export interface ExportResult {
  readonly exportFormat: string;
  readonly packSchemaVersion: string;
  readonly tier: "community" | "custom";
  readonly pack: FactorPack & { content_hash?: string };
}

/** wire (snake) — POST /api/factor-packs/export 응답 매핑 입력. */
interface ExportResultWire {
  export_format: string;
  pack_schema_version: string;
  tier: "community" | "custom";
  pack: FactorPack & { content_hash?: string };
}

/**
 * import-check / import 공통 — 충돌 항목.
 *
 * canonical_id 가 같고 content hash 가 다른 경우(ADR-0022 D8.1).
 * allowed_resolutions 는 backend 가 tier 에 따라 결정 — canonical tier 충돌은
 * replace 부재 (D8.2).
 */
export interface ImportConflict {
  readonly canonicalId: string;
  readonly incomingHash: string;
  readonly existingHash: string;
  readonly existingTier: string;
  readonly allowedResolutions: ReadonlyArray<"skip" | "rename" | "replace">;
}

/** wire (snake) 충돌 항목. */
interface ImportConflictWire {
  canonical_id: string;
  incoming_hash: string;
  existing_hash: string;
  existing_tier: string;
  allowed_resolutions: string[];
}

/** POST /api/factor-packs/import-check (dry-run) 응답. */
export interface ImportCheckResult {
  readonly valid: boolean;
  readonly issues: ReadonlyArray<ValidationIssue>;
  readonly conflicts: ReadonlyArray<ImportConflict>;
  readonly clean: ReadonlyArray<string>;
  /** ADR-0032 D2 — 외부 pack 의 선언 content_hash 가 본문과 불일치(봉인 파손). 비차단
   *  경고. 실 API 응답은 항상 boolean(매핑이 false 기본). optional 은 hand-construct
   *  테스트 fixture 호환용 — 소비자는 undefined 를 false 로 취급(`?? false`/falsy). */
  readonly hashMismatch?: boolean;
}

/** wire (snake) — import-check 응답 매핑 입력. */
interface ImportCheckResultWire {
  valid: boolean;
  issues: ReadonlyArray<ValidationIssue>;
  conflicts: ImportConflictWire[];
  clean: string[];
  hash_mismatch?: boolean;
}

/** 충돌 해소 action. */
export type ResolutionAction = "skip" | "rename" | "replace";

/** 각 충돌에 대한 사용자 해소 지시 — import body 의 resolutions 맵 값. */
export interface ConflictResolution {
  readonly action: ResolutionAction;
  /** rename 선택 시 필수. skip/replace 시 undefined. */
  readonly newCanonicalId?: string;
}

/** POST /api/factor-packs/import 응답 내 applied 항목. */
export interface AppliedResolution {
  readonly canonicalId: string;
  readonly action: ResolutionAction;
  readonly newCanonicalId?: string;
}

/** POST /api/factor-packs/import (apply) 응답. */
export interface ImportResult {
  readonly valid: boolean;
  readonly issues: ReadonlyArray<ValidationIssue>;
  /** 해소+봉인된 pack. 미해결 충돌 있으면 null. */
  readonly pack: (FactorPack & { content_hash?: string }) | null;
  readonly applied: ReadonlyArray<AppliedResolution>;
}

/** wire (snake) — import 응답 applied 항목. */
interface AppliedResolutionWire {
  canonical_id: string;
  action: string;
  new_canonical_id?: string;
}

/** wire (snake) — import 응답 매핑 입력. */
interface ImportResultWire {
  valid: boolean;
  issues: ReadonlyArray<ValidationIssue>;
  pack: (FactorPack & { content_hash?: string }) | null;
  applied: AppliedResolutionWire[];
}

/** 충돌 wire → camel 변환 헬퍼. */
function mapConflict(c: ImportConflictWire): ImportConflict {
  return {
    canonicalId: c.canonical_id,
    incomingHash: c.incoming_hash,
    existingHash: c.existing_hash,
    existingTier: c.existing_tier,
    allowedResolutions: c.allowed_resolutions.filter(
      (r): r is "skip" | "rename" | "replace" =>
        r === "skip" || r === "rename" || r === "replace",
    ),
  };
}

/**
 * POST /api/factor-packs/export — 봉인된 self-identifying export wrapper 생성.
 *
 * 클라이언트는 content_hash 를 계산할 수 없으므로 backend 에 위임.
 * 빌트인 pack 은 422 반환(server 측 거부 — D8.5).
 *
 * @param pack  editor 의 현재 pack
 * @param signal AbortController signal
 * @returns ExportResult (export_format marker + content_hash 봉인 pack)
 * @throws ApiError
 */
export async function exportPack(
  pack: unknown,
  signal?: AbortSignal,
): Promise<ExportResult> {
  const wire = await fetchJson<ExportResultWire>(
    "/api/factor-packs/export",
    {
      method: "POST",
      body: { pack },
      signal,
    },
  );
  return {
    exportFormat: wire.export_format,
    packSchemaVersion: wire.pack_schema_version,
    tier: wire.tier,
    pack: wire.pack,
  };
}

/**
 * POST /api/factor-packs/import-check — dry-run 충돌 검사.
 *
 * 실제 import 없이 conflicts 리스트만 반환. uuid 재사용은 422.
 *
 * @param pack  import 할 pack JSON (wrapper 포함 가능)
 * @param signal AbortController signal
 * @returns { valid, issues, conflicts, clean }
 * @throws ApiError
 */
export async function importCheckPack(
  pack: unknown,
  signal?: AbortSignal,
): Promise<ImportCheckResult> {
  const wire = await fetchJson<ImportCheckResultWire>(
    "/api/factor-packs/import-check",
    {
      method: "POST",
      body: { pack },
      signal,
    },
  );
  return {
    valid: wire.valid,
    issues: wire.issues,
    conflicts: wire.conflicts.map(mapConflict),
    clean: wire.clean,
    // ADR-0032 D2 — 봉인 불일치 경고(필드 부재 = 구버전 호환 → false).
    hashMismatch: wire.hash_mismatch ?? false,
  };
}

/**
 * POST /api/factor-packs/import — 충돌 해소 후 실제 import.
 *
 * resolutions 에 canonical_id → { action, new_canonical_id? } 맵을 동반해야 한다.
 * 미해결 충돌이 있으면 422(D8.4). uuid 재사용 = 422.
 *
 * @param pack         import 할 pack JSON
 * @param resolutions  충돌 해소 지시 맵(canonical_id → resolution)
 * @param signal       AbortController signal
 * @returns { valid, issues, pack, applied }
 * @throws ApiError
 */
export async function importPack(
  pack: unknown,
  resolutions: Readonly<Record<string, ConflictResolution>>,
  signal?: AbortSignal,
): Promise<ImportResult> {
  // wire 형태로 변환 — camel → snake.
  const wireResolutions: Record<
    string,
    { action: string; new_canonical_id?: string }
  > = {};
  for (const [id, res] of Object.entries(resolutions)) {
    wireResolutions[id] = {
      action: res.action,
      ...(res.newCanonicalId !== undefined
        ? { new_canonical_id: res.newCanonicalId }
        : {}),
    };
  }

  const wire = await fetchJson<ImportResultWire>(
    "/api/factor-packs/import",
    {
      method: "POST",
      body: { pack, resolutions: wireResolutions },
      signal,
    },
  );
  return {
    valid: wire.valid,
    issues: wire.issues,
    pack: wire.pack,
    applied: wire.applied.map((a) => ({
      canonicalId: a.canonical_id,
      action: a.action as ResolutionAction,
      ...(a.new_canonical_id !== undefined
        ? { newCanonicalId: a.new_canonical_id }
        : {}),
    })),
  };
}

// ──────────────────────────────────────────────────────────────────────────────
// 저장 (user-scoped 영속, ADR-0022 D9)
// ──────────────────────────────────────────────────────────────────────────────

/**
 * POST /api/factor-packs/saved 응답 (camel).
 *
 * backend wire 의 snake 필드(pack_slug, content_hash, factor_count, created_at)를
 * 경계에서 camel 로 변환한다.
 */
export interface SavedPackOut {
  readonly id: string;
  readonly packSlug: string;
  readonly version: string;
  readonly contentHash: string;
  readonly factorCount: number;
  readonly createdAt: string;
  /**
   * ADR-0032 D3 — import provenance(출처 URL). editor 직접 작성 pack 은 null.
   * **content_hash 봉인과 무관한 row 메타** — 표시(provenance) 용도. 구버전 wire
   * (필드 부재)는 null 로 매핑(후방호환).
   */
  readonly sourceUrl: string | null;
}

/** wire (snake) — POST /api/factor-packs/saved + GET 목록 응답 항목. */
interface SavedPackOutWire {
  id: string;
  pack_slug: string;
  version: string;
  content_hash: string;
  factor_count: number;
  created_at: string;
  // ADR-0032 D3 — provenance. 구버전 응답엔 부재할 수 있어 optional.
  source_url?: string | null;
}

/** snake → camel 변환 헬퍼. */
function mapSavedPackOut(w: SavedPackOutWire): SavedPackOut {
  return {
    id: w.id,
    packSlug: w.pack_slug,
    version: w.version,
    contentHash: w.content_hash,
    factorCount: w.factor_count,
    createdAt: w.created_at,
    // 필드 부재(구버전)는 null — provenance 없음(출처 미상)으로 취급.
    sourceUrl: w.source_url ?? null,
  };
}

/**
 * POST /api/factor-packs/saved — 현재 editor pack 을 user-scoped 영속 저장.
 *
 * invalid pack/canonical slug 은 422, 같은 (slug, version) 다른 정의는 409.
 *
 * @param pack  editor 의 현재 pack
 * @param sourceUrl ADR-0032 D3 provenance — URL import 로 들어온 pack 의 출처.
 *   editor 직접 작성/편집본은 null(출처 없음). **body 가 아닌 row 메타**로 전송돼
 *   content_hash 봉인에 영향 0(server 가 pack 만 봉인).
 * @param signal AbortController signal
 * @returns SavedPackOut (id, packSlug, version, contentHash, factorCount, createdAt, sourceUrl)
 * @throws ApiError — 422 검증 오류/위험 scheme source_url, 409 slug+version 충돌, fetch 실패.
 */
export async function savePack(
  pack: unknown,
  sourceUrl?: string | null,
  signal?: AbortSignal,
): Promise<SavedPackOut> {
  const wire = await fetchJson<SavedPackOutWire>(
    "/api/factor-packs/saved",
    {
      method: "POST",
      // source_url 은 provenance 있을 때만 전송 — null/undefined 는 출처 없음.
      body: { pack, source_url: sourceUrl ?? null },
      signal,
    },
  );
  return mapSavedPackOut(wire);
}

/**
 * GET /api/factor-packs/saved — 본인 저장 pack 목록.
 *
 * @param signal AbortController signal
 * @returns { items: SavedPackOut[] }
 * @throws ApiError
 */
export async function listMyPacks(
  signal?: AbortSignal,
): Promise<{ readonly items: ReadonlyArray<SavedPackOut> }> {
  const wire = await fetchJson<{ items: SavedPackOutWire[] }>(
    "/api/factor-packs/saved",
    { signal },
  );
  return { items: wire.items.map(mapSavedPackOut) };
}

/**
 * GET /api/factor-packs/saved/{id} — 저장된 pack 전체 body 불러오기.
 *
 * 타인 pack 이거나 존재하지 않으면 404.
 *
 * @param id   저장 pack ID
 * @param signal AbortController signal
 * @returns FactorPack (전체 body)
 * @throws ApiError — 404 미존재/타인.
 */
export async function getSavedPack(
  id: string,
  signal?: AbortSignal,
): Promise<FactorPack> {
  return fetchJson<FactorPack>(`/api/factor-packs/saved/${id}`, { signal });
}

/**
 * DELETE /api/factor-packs/saved/{id} — 저장 pack 삭제.
 *
 * 타인 pack 이거나 존재하지 않으면 404.
 *
 * @param id   저장 pack ID
 * @param signal AbortController signal
 * @throws ApiError — 404 미존재/타인.
 */
export async function deleteSavedPack(
  id: string,
  signal?: AbortSignal,
): Promise<void> {
  await fetchJson<void>(`/api/factor-packs/saved/${id}`, {
    method: "DELETE",
    signal,
  });
}

// ──────────────────────────────────────────────────────────────────────────────
// 참조 pack (reference tier, ADR-0023 D8 — 익명 접근)
// ──────────────────────────────────────────────────────────────────────────────

/**
 * GET /api/factor-packs/reference 의 단일 항목(camel).
 *
 * wire 의 snake 필드(pack_slug, factor_count)를 경계에서 camel 로 변환한다.
 * body 는 content_hash 봉인된 전체 factor pack JSON — editor 에 그대로 적재 가능.
 */
export interface ReferencePackItem {
  readonly packSlug: string;
  readonly version: string;
  readonly factorCount: number;
  readonly body: FactorPack & { content_hash?: string };
}

/** wire (snake) — GET /api/factor-packs/reference 응답 항목. */
interface ReferencePackItemWire {
  pack_slug: string;
  version: string;
  factor_count: number;
  body: FactorPack & { content_hash?: string };
}

/** snake → camel 변환 헬퍼. */
function mapReferencePackItem(w: ReferencePackItemWire): ReferencePackItem {
  return {
    packSlug: w.pack_slug,
    version: w.version,
    factorCount: w.factor_count,
    body: w.body,
  };
}

/**
 * GET /api/factor-packs/reference — 참조 pack 목록(익명, 인증 불필요).
 *
 * 참조 tier(community/speculum-reit-reference 등)의 전체 정의를 반환한다.
 * body 는 content_hash 봉인된 FactorPack — editor 에 그대로 setPack 가능.
 *
 * @param signal AbortController signal
 * @returns { packs: ReferencePackItem[] }
 * @throws ApiError — fetch 실패 또는 non-2xx 응답.
 */
export async function listReferencePacks(
  signal?: AbortSignal,
): Promise<{ readonly packs: ReadonlyArray<ReferencePackItem> }> {
  const wire = await fetchJson<{ packs: ReferencePackItemWire[] }>(
    "/api/factor-packs/reference",
    { signal },
  );
  return { packs: wire.packs.map(mapReferencePackItem) };
}

// ──────────────────────────────────────────────────────────────────────────────
// community (USER_SHARED, ADR-0028)
// ──────────────────────────────────────────────────────────────────────────────

/**
 * GET /api/factor-packs/community 의 단일 항목(camel).
 *
 * wire 의 snake 필드(pack_slug, factor_count, content_hash, created_at)를
 * 경계에서 camel 로 변환한다.
 * **인기/순위/다운로드 필드 없음** — ADR-0028 D3 중립 정렬, 큐레이션 0.
 */
export interface CommunityPack {
  readonly packSlug: string;
  readonly version: string;
  readonly factorCount: number;
  readonly createdAt: string;
  readonly contentHash: string;
  /** 공유자가 지정한 이름 (없으면 null). 추천 신호로 사용 금지. */
  readonly name: string | null;
  /** 공유자가 지정한 설명 (없으면 null). 추천 신호로 사용 금지. */
  readonly description: string | null;
}

/** wire (snake) — GET /api/factor-packs/community 응답 항목. */
interface CommunityPackWire {
  pack_slug: string;
  version: string;
  factor_count: number;
  created_at: string;
  content_hash: string;
  name: string | null;
  description: string | null;
}

/** snake → camel 변환 헬퍼. */
function mapCommunityPack(w: CommunityPackWire): CommunityPack {
  return {
    packSlug: w.pack_slug,
    version: w.version,
    factorCount: w.factor_count,
    createdAt: w.created_at,
    contentHash: w.content_hash,
    name: w.name,
    description: w.description,
  };
}

/**
 * GET /api/factor-packs/community — 공개 community pack 목록(인증 불요).
 *
 * 정렬은 backend created_at 역순 그대로 — 클라이언트 재정렬 0(ADR-0028 D3).
 * 다운로드 수·인기·순위 필드 없음 — 큐레이션 신호 0.
 *
 * @param signal AbortController signal
 * @returns { packs: CommunityPack[], total }
 * @throws ApiError
 */
export async function listCommunityPacks(
  signal?: AbortSignal,
): Promise<{ readonly packs: ReadonlyArray<CommunityPack>; readonly total: number }> {
  const wire = await fetchJson<{ packs: CommunityPackWire[]; total: number }>(
    "/api/factor-packs/community",
    { signal },
  );
  return { packs: wire.packs.map(mapCommunityPack), total: wire.total };
}

/**
 * community pack full body 응답 — 메타 + pack body.
 * import 용 전체 body 포함.
 */
export interface CommunityPackBody {
  readonly packSlug: string;
  readonly version: string;
  readonly factorCount: number;
  readonly createdAt: string;
  readonly contentHash: string;
  readonly name: string | null;
  readonly description: string | null;
  /** 전체 pack body (content_hash 봉인 포함). import 에 그대로 사용. */
  readonly pack: FactorPack & { content_hash?: string };
}

/** wire (snake) — GET /api/factor-packs/community/{slug}/versions/{version}. */
interface CommunityPackBodyWire extends CommunityPackWire {
  pack: FactorPack & { content_hash?: string };
}

/**
 * GET /api/factor-packs/community/{pack_slug}/versions/{version} — full body 취득.
 *
 * content_hash 봉인된 pack body 를 반환한다. import 흐름에서 검증에 사용.
 * 인증 불요. ADR-0028 D4.
 *
 * @param packSlug  community pack slug
 * @param version   pack version
 * @param signal    AbortController signal
 * @throws ApiError
 */
export async function getCommunityPackBody(
  packSlug: string,
  version: string,
  signal?: AbortSignal,
): Promise<CommunityPackBody> {
  // packSlug 는 "user/my-pack" 형태라 슬래시를 encodeURIComponent 처리.
  const encodedSlug = packSlug.split("/").map(encodeURIComponent).join("/");
  const wire = await fetchJson<CommunityPackBodyWire>(
    `/api/factor-packs/community/${encodedSlug}/versions/${encodeURIComponent(version)}`,
    { signal },
  );
  return {
    packSlug: wire.pack_slug,
    version: wire.version,
    factorCount: wire.factor_count,
    createdAt: wire.created_at,
    contentHash: wire.content_hash,
    name: wire.name,
    description: wire.description,
    pack: wire.pack,
  };
}

/**
 * PATCH /api/factor-packs/saved/{pack_id}/visibility 응답 (camel).
 *
 * custom pack visibility 토글 결과. visibility 필드가 추가된 SavedPackOut 확장.
 * ADR-0028 D2.
 */
export interface CustomPackBodyOut {
  readonly id: string;
  readonly packSlug: string;
  readonly version: string;
  readonly contentHash: string;
  readonly factorCount: number;
  readonly createdAt: string;
  /** 현재 visibility 상태. */
  readonly visibility: "private" | "public";
  readonly pack: FactorPack & { content_hash?: string };
}

/** wire (snake) — PATCH visibility 응답. */
interface CustomPackBodyOutWire {
  id: string;
  pack_slug: string;
  version: string;
  content_hash: string;
  factor_count: number;
  created_at: string;
  visibility: "private" | "public";
  pack: FactorPack & { content_hash?: string };
}

/**
 * PATCH /api/factor-packs/saved/{pack_id}/visibility — visibility 토글.
 *
 * public 전환 시 name/description 에 금지어휘 포함 → 422.
 * 타인 pack → 404. ADR-0028 D2.
 *
 * @param packId    저장 pack ID
 * @param visibility 전환할 visibility
 * @param signal    AbortController signal
 * @returns CustomPackBodyOut (id, packSlug, version, visibility 등)
 * @throws ApiError — 422 금지어휘, 404 미존재/타인.
 */
export async function setPackVisibility(
  packId: string,
  visibility: "private" | "public",
  signal?: AbortSignal,
): Promise<CustomPackBodyOut> {
  const wire = await fetchJson<CustomPackBodyOutWire>(
    `/api/factor-packs/saved/${packId}/visibility`,
    {
      method: "PATCH",
      body: { visibility },
      signal,
    },
  );
  return {
    id: wire.id,
    packSlug: wire.pack_slug,
    version: wire.version,
    contentHash: wire.content_hash,
    factorCount: wire.factor_count,
    createdAt: wire.created_at,
    visibility: wire.visibility,
    pack: wire.pack,
  };
}

/**
 * SavedPackOut with visibility — PackLibrary 가 목록에서 visibility 를 표시하기 위해
 * PATCH 성공 후 로컬 캐시를 업데이트할 때 사용하는 확장 타입.
 */
export interface SavedPackOutWithVisibility extends SavedPackOut {
  readonly visibility: "private" | "public";
}

// ──────────────────────────────────────────────────────────────────────────────

/**
 * POST /api/factor-packs/evaluate — 저장 없이 pack 을 즉시 평가.
 *
 * editor 가 정의 중인 pack 을 codes(최대 20)에 대해 as_of 기준으로 평가해
 * 사실값(또는 N/A)을 반환한다. 저장(user-scoped, T69) 의존 없이 미리보기만.
 *
 * 매핑: wire 의 snake(is_na/na_reason/canonical_id)를 camel 로 정규화 —
 * UI 계층이 wire 형태에 결합되지 않도록 경계에서 변환.
 *
 * @param pack  factor pack JSON
 * @param asOf  PIT 기준 일자("YYYY-MM-DD", 전역 as-of store)
 * @param codes KRX 종목코드 배열(상한 EVALUATE_MAX_CODES)
 * @param signal AbortController signal
 * @returns { valid, issues, results }
 * @throws ApiError — fetch 실패 또는 non-2xx 응답.
 */
export async function evaluateFactorPack(
  pack: unknown,
  asOf: string,
  codes: ReadonlyArray<string>,
  signal?: AbortSignal,
): Promise<EvaluateResult> {
  const wire = await fetchJson<EvaluateResultWire>(
    "/api/factor-packs/evaluate",
    {
      method: "POST",
      body: { pack, as_of: asOf, codes },
      signal,
    },
  );

  return {
    valid: wire.valid,
    issues: wire.issues,
    // results 순서 그대로 유지 — codes 입력 순서가 곧 표시 순서(자동 정렬 금지).
    results: wire.results.map((row) => ({
      code: row.code,
      factors: row.factors.map((f) => ({
        canonicalId: f.canonical_id,
        name: f.name,
        unit: f.unit,
        value: f.value,
        isNa: f.is_na,
        naReason: f.na_reason,
        // 소표본 필드 snake→camel 매핑(ADR-0024 Phase 2 / §2.5).
        sampleSize: f.sample_size ?? null,
        smallSample: f.small_sample ?? false,
      })),
    })),
  };
}
