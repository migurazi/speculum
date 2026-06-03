/**
 * Factors API client — GET /api/factors.
 *
 * 활성 factor pack 의 factor 목록을 가져와 Screener UI 의 드롭다운 선택지로
 * 사용한다. canonical_id 를 직접 타이핑하는 오타 페인포인트를 해결.
 *
 * 설계:
 *   - backend `app.api.routes.meta.get_factors` 의 wire schema 와 1:1 매핑.
 *   - `fetchJson` wrapper 재사용 (client.ts 와 동일 패턴).
 *   - factor pack 은 정적 — staleTime 을 길게 설정해 불필요한 refetch 차단
 *     (호출자가 useQuery staleTime 옵션으로 제어).
 *
 * 관련:
 *   - backend `app.api.routes.meta.get_factors`
 *   - M0_PLAN Screener factor 입력 개선 (오타 방지 드롭다운).
 */

import { fetchJson } from "./client";

/** 단일 factor 항목 — backend `{canonical_id, name, unit, tags}` 와 1:1. */
export interface Factor {
  /** 실제 조건·표시 입력에 사용하는 canonical identifier. */
  readonly canonicalId: string;
  /** 사람이 읽는 라벨 (예: "PER (TTM, 연결, K-IFRS)"). */
  readonly name: string;
  /** 단위 문자열 (예: "배", "%" — 빈 문자열 가능). */
  readonly unit: string;
  /** 카테고리 태그 목록 (예: ["valuation"]). */
  readonly tags: ReadonlyArray<string>;
}

/** GET /api/factors 응답 전체. */
export interface FactorsResponse {
  readonly packSlug: string;
  readonly packVersion: string;
  readonly factors: ReadonlyArray<Factor>;
}

/**
 * GET /api/factors — 활성 pack 의 factor 목록.
 *
 * @param signal AbortController signal (TanStack Query 가 cancel 시 주입).
 * @returns Factor 배열.
 * @throws ApiError — fetch 실패 또는 non-2xx 응답.
 */
export async function fetchFactors(signal?: AbortSignal): Promise<Factor[]> {
  const raw = await fetchJson<{
    pack_slug: string;
    pack_version: string;
    factors: Array<{
      canonical_id: string;
      name: string;
      unit: string;
      tags: string[];
    }>;
  }>("/api/factors", { method: "GET", signal });

  return raw.factors.map((f) => ({
    canonicalId: f.canonical_id,
    name: f.name,
    unit: f.unit,
    tags: f.tags,
  }));
}
