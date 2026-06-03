/**
 * Screen Run API client — `/api/runs` CRUD + export/reproduce (M2 T80).
 *
 * Backend `app/api/routes/runs.py` + `app/schemas/screen.py` 의 wire schema
 * 와 1:1 매핑.
 *
 * Save Run 의미 (ADR-0008 D7 / D7-bis):
 *   "현재 화면의 조건·결과·data_versions 를 immutable snapshot 으로 freeze".
 *   재실행 + 저장이 같은 호출 (M0 단순화, oracle T26 자문). T18 합류 후 평가
 *   비용 커지면 실행/저장 분리 token 패턴으로 교체.
 *
 * export/reproduce (M2 T80 Phase 2):
 *   - exportRun: GET /api/runs/{id}/export → 자기완결 JSON (git 첨부·블로그용).
 *   - reproduceRun: POST /api/runs/reproduce → matches/result_codes/hash 사실만.
 *     user 비종속 — 공유 JSON 누구든 재현 가능 (로그인 불필요).
 *
 * 관련:
 * - ADR-0002 D4 (append-only Run)
 * - ADR-0008 D7 (Run snapshot freeze)
 * - M0_PLAN T30 (backend) / T40 (frontend)
 * - M2_PLAN T80 (export/reproduce)
 */

import { fetchJson } from "./client";
import type { ScreenCondition, SecurityType } from "./screen";

/** ScreenRunSnapshotOut — `/api/runs` POST/GET 응답. */
export interface ScreenRunSnapshot {
  readonly id: string;
  readonly user_id: string;
  readonly as_of: string;  // ISO 8601 date
  readonly result_codes: ReadonlyArray<string>;
  readonly result_hash: string;
  readonly data_versions: Readonly<Record<string, string>>;
  readonly computed_at: string;  // ISO 8601 datetime
  /** 사용자 입력 보존 — UI 가 Run 재현 시 같은 conditions 표시. */
  readonly conditions: ReadonlyArray<Readonly<Record<string, string>>>;
  readonly selected_factors: ReadonlyArray<string>;
}

/** ScreenRunListOut — `/api/runs` GET 응답. */
export interface ScreenRunList {
  readonly items: ReadonlyArray<ScreenRunSnapshot>;
  readonly total: number;
}

/** VersionsDiffOut — `/api/runs/{id}/diff` 응답. */
export interface VersionsDiff {
  readonly snapshot_id: string;
  /** 변경된 키만 — `{key: [old, new]}`. 빈 객체 = 일치 (재현 OK). */
  readonly diff: Readonly<Record<string, readonly [string, string]>>;
}

/**
 * POST /api/runs — Save Run.
 *
 * Body 는 POST /api/screen 과 동일 (`ScreenRunQueryIn`). backend 가
 * conditions/selected_factors/as_of 로 재실행 + snapshot 저장.
 *
 * @param query conditions + selected_factors (executeScreen 과 동일 입력)
 * @param asOf  PIT 기준 일자.
 */
export async function saveRun(
  query: {
    readonly conditions: ReadonlyArray<ScreenCondition>;
    readonly selected_factors: ReadonlyArray<string>;
    readonly security_types?: ReadonlyArray<SecurityType>;
  },
  asOf: string,
  signal?: AbortSignal,
): Promise<ScreenRunSnapshot> {
  return fetchJson<ScreenRunSnapshot>("/api/runs", {
    method: "POST",
    body: query,
    searchParams: { as_of: asOf },
    signal,
  });
}

/**
 * GET /api/runs — 현재 사용자의 recent runs (default limit 20).
 */
export async function listRecentRuns(
  limit?: number,
  signal?: AbortSignal,
): Promise<ScreenRunList> {
  const searchParams: Record<string, string> = {};
  if (limit !== undefined) {
    searchParams["limit"] = String(limit);
  }
  return fetchJson<ScreenRunList>("/api/runs", {
    method: "GET",
    searchParams,
    signal,
  });
}

/**
 * GET /api/runs/{id} — 단일 Run snapshot.
 */
export async function fetchRun(
  runId: string,
  signal?: AbortSignal,
): Promise<ScreenRunSnapshot> {
  return fetchJson<ScreenRunSnapshot>(
    `/api/runs/${encodeURIComponent(runId)}`,
    { method: "GET", signal },
  );
}

/**
 * GET /api/runs/{id}/diff — snapshot data_versions vs 현재 active.
 *
 * 빈 diff = 재현 가능 (정책 무변경). 변경된 키가 있으면 UI 가 "이 Run 은 더
 * 이상 재현 불가" 배지.
 */
export async function fetchRunDiff(
  runId: string,
  signal?: AbortSignal,
): Promise<VersionsDiff> {
  return fetchJson<VersionsDiff>(
    `/api/runs/${encodeURIComponent(runId)}/diff`,
    { method: "GET", signal },
  );
}

/**
 * ScreenRunExport — GET /api/runs/{id}/export 응답.
 *
 * 자기완결(self-identifying) JSON: export_format / snapshot_schema_version 으로
 * 어떤 도구가 만든 파일인지 식별. git 첨부·블로그 supplementary 용.
 * run.data_versions 로 어느 정책 버전의 결과인지 추적 가능.
 */
export interface ScreenRunExport {
  /** 포맷 식별자 — "speculum-screen-run-export-v1". */
  readonly exportFormat: string;
  /** 내부 스키마 버전 번호 (정수). */
  readonly snapshotSchemaVersion: number;
  /** Run snapshot 핵심 필드. */
  readonly run: {
    readonly runId: string;
    readonly conditions: ReadonlyArray<Readonly<Record<string, string>>>;
    readonly selectedFactors: ReadonlyArray<string>;
    /** PIT 기준 일자 (ISO 8601 "YYYY-MM-DD"). */
    readonly asOf: string;
    /** 필터 통과 종목 코드 목록. */
    readonly resultCodes: ReadonlyArray<string>;
    /** 결과 집합의 해시 (재현 검증용). */
    readonly resultHash: string;
    /** 정책 버전 맵 — 어떤 버전 데이터로 산출했는지 기록. */
    readonly dataVersions: Readonly<Record<string, string>>;
  };
}

/** wire schema — backend snake_case 응답 그대로. */
interface ScreenRunExportWire {
  export_format: string;
  snapshot_schema_version: number;
  run: {
    run_id: string;
    conditions: Array<Record<string, string>>;
    selected_factors: string[];
    as_of: string;
    result_codes: string[];
    result_hash: string;
    data_versions: Record<string, string>;
  };
}

/**
 * GET /api/runs/{id}/export — 자기완결 export JSON fetch.
 *
 * 본인 Run 만 — 401 (미인증), 404 (존재 안 함 또는 타인 Run).
 * 반환값을 Blob 으로 변환해 파일 다운로드 트리거 가능 (exportAndDownloadRun 참조).
 *
 * @param runId 저장된 Run UUID.
 * @param signal AbortController signal.
 */
export async function exportRun(
  runId: string,
  signal?: AbortSignal,
): Promise<ScreenRunExport> {
  const wire = await fetchJson<ScreenRunExportWire>(
    `/api/runs/${encodeURIComponent(runId)}/export`,
    { method: "GET", signal },
  );

  // snake_case → camelCase 매핑 (재현성 §2.10 — 사실만, 등락색/랭킹/추천 0).
  return {
    exportFormat: wire.export_format,
    snapshotSchemaVersion: wire.snapshot_schema_version,
    run: {
      runId: wire.run.run_id,
      conditions: wire.run.conditions,
      selectedFactors: wire.run.selected_factors,
      asOf: wire.run.as_of,
      resultCodes: wire.run.result_codes,
      resultHash: wire.run.result_hash,
      dataVersions: wire.run.data_versions,
    },
  };
}

/**
 * ReproduceResult — POST /api/runs/reproduce 응답 (camelCase).
 *
 * 중립 톤 — matches 는 검증 상태(통과/불일치)이며 등락 판단이 아님.
 * user 비종속: 공유 JSON 을 가진 누구든 재현 가능 (로그인 불필요).
 */
export interface ReproduceResult {
  /** true = 재현 결과가 원본과 일치. false = 불일치. */
  readonly matches: boolean;
  /** 재현 실행의 결과 종목 코드 목록. */
  readonly reproducedResultCodes: ReadonlyArray<string>;
  /** 원본 export 의 결과 종목 코드 목록. */
  readonly originalResultCodes: ReadonlyArray<string>;
  /** 결과 집합의 해시. */
  readonly resultHash: string;
  /** 추가 메모 (버전 불일치 이유 등, 없으면 null). */
  readonly note: string | null;
}

/** wire schema — backend snake_case 응답 그대로. */
interface ReproduceResultWire {
  matches: boolean;
  reproduced_result_codes: string[];
  original_result_codes: string[];
  result_hash: string;
  note: string | null;
}

/**
 * POST /api/runs/reproduce — export JSON 으로 재현 검증.
 *
 * user 비종속 — 공유 JSON 을 가진 누구든 호출 가능 (로그인 불필요).
 * body = export JSON 전체 또는 run 객체 (backend 가 양쪽 허용).
 *
 * @param exportJson exportRun() 반환값 또는 다운로드한 JSON.
 * @param signal AbortController signal.
 */
export async function reproduceRun(
  exportJson: ScreenRunExport | unknown,
  signal?: AbortSignal,
): Promise<ReproduceResult> {
  const wire = await fetchJson<ReproduceResultWire>("/api/runs/reproduce", {
    method: "POST",
    body: exportJson,
    signal,
  });

  // snake_case → camelCase 매핑.
  return {
    matches: wire.matches,
    reproducedResultCodes: wire.reproduced_result_codes,
    originalResultCodes: wire.original_result_codes,
    resultHash: wire.result_hash,
    note: wire.note,
  };
}
