/**
 * Custom Screen API client — POST /api/screen/custom + POST /api/runs/custom.
 *
 * ADR-0025 D4/D6 — custom pack 으로 screen 실행 및 run 저장.
 *
 * 계약:
 *   - POST /api/screen/custom?as_of=YYYY-MM-DD
 *       body { pack_slug, version, conditions, selected_factors, security_types? }
 *       → 200 ScreenResultOut (result_codes 등 기존 /api/screen 과 동일 구조).
 *       인증 필요(AUTH_SECRET 설정 시 Bearer). 타 user/미존재 pack → 404.
 *       빌트인 slug(speculum-builtin) → 400.
 *   - POST /api/runs/custom?as_of=YYYY-MM-DD
 *       동일 body → 201 ScreenRunSnapshotOut (저장 run, custom pack hash freeze).
 *
 * snake↔camel: conditions.factor 는 custom pack 의 canonical_id 그대로 전달.
 * 응답은 기존 ScreenResult / ScreenRunSnapshot 구조 재사용.
 *
 * No Advice / ADR-0025 D6:
 *   - 결과는 boolean 필터 통과 종목코드 목록만.
 *   - 값/점수의 자동 강조·랭킹 0. 행 순서 결정적(자동 정렬 금지).
 *   - 이 모듈은 transport 전용 — 시각 정책 강제는 UI 계층.
 *
 * 관련:
 *   - lib/api/screen.ts — ScreenResult / ScreenCondition / SecurityType
 *   - lib/api/runs.ts — ScreenRunSnapshot (custom run 응답 재사용)
 *   - lib/api/client.ts — fetchJson (Authorization Bearer 자동 주입)
 */

import { fetchJson } from "./client";
import type {
  ScreenCondition,
  ScreenResult,
  SecurityType,
} from "./screen";
import type { ScreenRunSnapshot } from "./runs";

/** POST /api/screen/custom body wire 구조. */
interface CustomScreenBodyWire {
  pack_slug: string;
  version: string;
  conditions: ReadonlyArray<{
    factor: string;
    op: string;
    value: string;
  }>;
  selected_factors: ReadonlyArray<string>;
  security_types?: ReadonlyArray<string>;
}

/**
 * POST /api/screen/custom — custom pack 으로 screen 실행(저장 없음).
 *
 * @param packSlug       저장 pack 의 pack_slug
 * @param version        저장 pack 의 version
 * @param conditions     조건 배열 (factor = custom pack 의 canonical_id)
 * @param selectedFactors 결과 표시 factor canonical_id 배열
 * @param asOf           PIT 기준 일자 (ISO 8601, "YYYY-MM-DD")
 * @param securityTypes  자산군 필터 (미지정 시 backend 가 ["common"] 으로 해소)
 * @param signal         AbortController signal
 * @returns ScreenResult (result_codes, total, data_versions)
 * @throws ApiError — 401 미인증, 404 타인/미존재 pack, 400 빌트인 slug, 422 검증 실패.
 */
export async function customScreen(
  packSlug: string,
  version: string,
  conditions: ReadonlyArray<ScreenCondition>,
  selectedFactors: ReadonlyArray<string>,
  asOf: string,
  securityTypes?: ReadonlyArray<SecurityType>,
  signal?: AbortSignal,
): Promise<ScreenResult> {
  const body: CustomScreenBodyWire = {
    pack_slug: packSlug,
    version,
    conditions,
    selected_factors: selectedFactors,
    ...(securityTypes !== undefined ? { security_types: securityTypes } : {}),
  };

  return fetchJson<ScreenResult>("/api/screen/custom", {
    method: "POST",
    body,
    searchParams: { as_of: asOf },
    signal,
  });
}

/**
 * POST /api/runs/custom — custom pack screen 을 run snapshot 으로 저장.
 *
 * conditions 의 factor 는 custom pack 의 canonical_id. backend 가
 * pack body 를 자기완결로 freeze — export/reproduce 는 기존 /api/runs/{id}/export
 * 패턴이 처리한다.
 *
 * @param packSlug       저장 pack 의 pack_slug
 * @param version        저장 pack 의 version
 * @param conditions     조건 배열 (factor = custom pack 의 canonical_id)
 * @param selectedFactors 결과 표시 factor canonical_id 배열
 * @param asOf           PIT 기준 일자 (ISO 8601, "YYYY-MM-DD")
 * @param securityTypes  자산군 필터
 * @param signal         AbortController signal
 * @returns ScreenRunSnapshot (201 응답, id + result_codes + data_versions 등)
 * @throws ApiError — 401 미인증, 404 타인/미존재 pack, 400 빌트인 slug.
 */
export async function saveCustomRun(
  packSlug: string,
  version: string,
  conditions: ReadonlyArray<ScreenCondition>,
  selectedFactors: ReadonlyArray<string>,
  asOf: string,
  securityTypes?: ReadonlyArray<SecurityType>,
  signal?: AbortSignal,
): Promise<ScreenRunSnapshot> {
  const body: CustomScreenBodyWire = {
    pack_slug: packSlug,
    version,
    conditions,
    selected_factors: selectedFactors,
    ...(securityTypes !== undefined ? { security_types: securityTypes } : {}),
  };

  return fetchJson<ScreenRunSnapshot>("/api/runs/custom", {
    method: "POST",
    body,
    searchParams: { as_of: asOf },
    signal,
  });
}
