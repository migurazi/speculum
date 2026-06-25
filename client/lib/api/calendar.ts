/**
 * Calendar Coverage API client — GET /api/calendar.
 *
 * 서버가 지원하는 KRX 캘린더 유효 범위를 조회한다. 클라이언트가 이를 통해
 * picker min/max 범위를 결정한다.
 *
 * 설계:
 *   - backend `app.api.routes.calendar.get_calendar_coverage` 의 wire schema 와 1:1 매핑.
 *   - `fetchJson` wrapper 재사용 (client.ts 와 동일 패턴).
 *   - content_hash 는 클라이언트가 사용하지 않으므로 매핑 제외.
 *
 * 관련:
 *   - backend `app.api.routes.calendar`
 *   - state/calendar-bounds-store.ts (CalendarBounds 저장)
 *   - components/CalendarBoundsSync.tsx (useQuery 배선)
 *   - components/AsOfDatePicker.tsx (input min/max 속성 반영)
 */

import { fetchJson } from "./client";

/** GET /api/calendar 의 wire schema (snake_case). */
interface CalendarWire {
  readonly min_date: string;
  readonly max_date: string;
  readonly earliest_business_day: string;
  readonly latest_business_day: string;
  /** 실데이터가 적재된 가장 최신 일자. 데이터 없으면 null. */
  readonly latest_data_date: string | null;
  readonly version: string;
  readonly content_hash: string;
}

/**
 * 서버 캘린더 유효 범위 — snake→camelCase 매핑 결과.
 *
 * content_hash 는 클라이언트가 사용하지 않아 제외.
 */
export interface CalendarCoverage {
  /** 지원 최소 일자 ("YYYY-MM-DD"). */
  readonly minDate: string;
  /** 지원 최대 일자 ("YYYY-MM-DD"). */
  readonly maxDate: string;
  /** 지원 범위 내 첫 번째 영업일 ("YYYY-MM-DD"). */
  readonly earliestBusinessDay: string;
  /** 지원 범위 내 마지막 영업일 ("YYYY-MM-DD"). */
  readonly latestBusinessDay: string;
  /** 실데이터가 있는 가장 최신 일자 ("YYYY-MM-DD" 또는 null). */
  readonly latestDataDate: string | null;
  /** API 버전 문자열. */
  readonly version: string;
}

/**
 * GET /api/calendar — 서버 KRX 캘린더 유효 범위 조회.
 *
 * @param signal AbortController signal (TanStack Query 가 cancel 시 주입).
 * @returns CalendarCoverage — snake→camelCase 매핑.
 * @throws ApiError — fetch 실패 또는 non-2xx 응답.
 */
export async function fetchCalendarCoverage(
  signal?: AbortSignal,
): Promise<CalendarCoverage> {
  const raw = await fetchJson<CalendarWire>("/api/calendar", { signal });

  return {
    minDate: raw.min_date,
    maxDate: raw.max_date,
    earliestBusinessDay: raw.earliest_business_day,
    latestBusinessDay: raw.latest_business_day,
    latestDataDate: raw.latest_data_date,
    version: raw.version,
  };
}
