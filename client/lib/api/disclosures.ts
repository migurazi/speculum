/**
 * Disclosures API client — GET /api/stocks/{code}/disclosures.
 *
 * ADR-0026 D1/D3: 사용자가 명시 진입한 1종목의 on-demand fetch.
 * 제목(reportName) + 접수일(rceptDate) + DART 원문 URL(dartUrl) 3필드만.
 * 본문·요약·자체 분류라벨 0 — transport 계층에서 타입으로 강제.
 *
 * ADR-0026 D4 (PIT 정합):
 *   as_of 파라미터를 backend 에 전달 → backend 가 rcept_date <= as_of 필터.
 *   look-ahead 0.
 *
 * 관련:
 *   - ADR-0026 공시 metadata 표시 설계.
 *   - lib/api/client.ts — fetchJson (Authorization Bearer 자동 주입).
 *   - components/StockDetail/DisclosurePanel.tsx — 표시 계층.
 *
 * snake↔camel 변환 패턴: notes.ts / financial-history.ts 와 동일.
 */

import { fetchJson } from "./client";

// =============================================================================
// Wire types (backend snake_case)
// =============================================================================

/** backend 응답 단일 공시 항목 wire 구조. */
interface DisclosureWire {
  readonly report_name: string;
  readonly rcept_date: string;
  readonly dart_url: string;
}

/** backend 응답 최상위 wire 구조. */
interface DisclosureListWire {
  readonly code: string;
  readonly as_of: string;
  readonly disclosures: ReadonlyArray<DisclosureWire>;
}

// =============================================================================
// Domain types (camelCase) — ADR-0026 D1 하드 제약
// =============================================================================

/**
 * 단일 공시 항목.
 *
 * ADR-0026 D1: 제목·접수일·DART 원문링크 3필드만.
 * 본문·요약·자체 분류라벨 필드 의도적으로 부재.
 */
export interface Disclosure {
  /** 공시 제목 (DART report_nm 그대로). ADR-0026 D2: EXTERNAL_QUOTE scope. */
  readonly reportName: string;
  /** 공시 접수일 ("YYYY-MM-DD"). */
  readonly rceptDate: string;
  /** DART 원문 viewer URL. 클릭 시 외부 페이지 이탈. */
  readonly dartUrl: string;
}

/** fetchDisclosures 반환 구조. */
export interface DisclosureList {
  readonly code: string;
  readonly asOf: string;
  readonly disclosures: ReadonlyArray<Disclosure>;
}

// =============================================================================
// Mapping
// =============================================================================

/** wire → domain 변환. 3필드 이외 추가 필드 미전파. */
function wireToDisclosure(w: DisclosureWire): Disclosure {
  return {
    reportName: w.report_name,
    rceptDate: w.rcept_date,
    dartUrl: w.dart_url,
  };
}

// =============================================================================
// API function
// =============================================================================

/** fetchDisclosures 옵션. */
export interface FetchDisclosuresOptions {
  /**
   * PIT 기준 일자 ("YYYY-MM-DD").
   * 미전달 시 backend 가 오늘 기준으로 처리 (ADR-0026 D4).
   */
  readonly asOf?: string;
  /**
   * 반환 최대 공시 수.
   * 미전달 시 backend default 사용.
   */
  readonly limit?: number;
}

/**
 * 종목 공시 목록 조회.
 *
 * GET /api/stocks/{code}/disclosures?as_of=YYYY-MM-DD&limit=N
 *
 * ADR-0026 D3: 사용자가 명시 진입한 1종목에 한해 on-demand 호출.
 * ADR-0026 D4: as_of 이후 공시 backend 에서 제외 — look-ahead 0.
 * ADR-0026 D1: 반환 타입 Disclosure — 제목/접수일/링크만.
 *
 * @param code         KRX 6자리 종목코드.
 * @param options      asOf / limit 선택 파라미터.
 * @param signal       AbortController signal.
 * @returns DisclosureList (camelCase, 최신순 backend 정렬 그대로).
 * @throws ApiError    network / 4xx / 5xx / JSON parse 실패.
 */
export async function fetchDisclosures(
  code: string,
  options: FetchDisclosuresOptions = {},
  signal?: AbortSignal,
): Promise<DisclosureList> {
  const searchParams: Record<string, string> = {};
  if (options.asOf) {
    searchParams["as_of"] = options.asOf;
  }
  if (options.limit !== undefined) {
    searchParams["limit"] = String(options.limit);
  }

  const wire = await fetchJson<DisclosureListWire>(
    `/api/stocks/${encodeURIComponent(code)}/disclosures`,
    { searchParams, signal },
  );

  return {
    code: wire.code,
    asOf: wire.as_of,
    disclosures: wire.disclosures.map(wireToDisclosure),
  };
}
