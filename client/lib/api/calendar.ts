/**
 * Calendar Coverage API client — GET /api/calendar.
 *
 * 서버가 지원하는 KRX 캘린더 유효 범위를 조회한다. 클라이언트가 이를 통해
 * zustand persist 에 저장된 stale as_of(미래 날짜 등)를 자동 보정한다.
 *
 * 설계:
 *   - backend `app.api.routes.calendar.get_calendar_coverage` 의 wire schema 와 1:1 매핑.
 *   - `fetchJson` wrapper 재사용 (client.ts 와 동일 패턴).
 *   - content_hash 는 클라이언트가 사용하지 않으므로 매핑 제외.
 *   - clampAsOf / clampUpperBound 는 순수함수 — 테스트 독립 가능.
 *
 * 클램프 규칙:
 *   - 하한 = earliestBusinessDay
 *   - 상한 = latestDataDate ?? latestBusinessDay (실데이터 최신일 우선)
 *   - ISO date(YYYY-MM-DD) 는 사전식(lexicographic) 비교 = 시간순과 일치.
 *
 * 관련:
 *   - backend `app.api.routes.calendar`
 *   - state/calendar-bounds-store.ts (CalendarBounds 저장)
 *   - components/CalendarBoundsSync.tsx (useQuery 배선 + as_of 클램프)
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

/**
 * 클램프 상한 결정 — latestDataDate 있으면 그것, 없으면 latestBusinessDay.
 *
 * 실데이터 최신일이 영업일 최종일보다 앞설 수 있어(운영 적재 지연) 실데이터 일자
 * 기준으로 상한을 제한한다.
 *
 * @param c CalendarCoverage — fetchCalendarCoverage 반환값.
 * @returns 상한 날짜 문자열 ("YYYY-MM-DD").
 */
export function clampUpperBound(c: CalendarCoverage): string {
  return c.latestDataDate ?? c.latestBusinessDay;
}

/**
 * as_of 를 [lower, upper] 범위로 클램프.
 *
 * ISO date string("YYYY-MM-DD") 의 사전식 비교는 시간순과 동치이므로
 * 단순 string 비교로 범위 판정한다.
 *
 * @param asOf 현재 as_of 값 ("YYYY-MM-DD").
 * @param lower 하한 ("YYYY-MM-DD") — CalendarCoverage.earliestBusinessDay.
 * @param upper 상한 ("YYYY-MM-DD") — clampUpperBound 반환값.
 * @returns 클램프 결과 ("YYYY-MM-DD"). 범위 내면 asOf 그대로.
 */
export function clampAsOf(asOf: string, lower: string, upper: string): string {
  if (asOf < lower) return lower;
  if (asOf > upper) return upper;
  return asOf;
}
