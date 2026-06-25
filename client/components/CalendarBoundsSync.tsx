"use client";

/**
 * CalendarBoundsSync — GET /api/calendar 결과를 store 에 동기화하고,
 * stale as_of 를 자동 클램프하는 렌더 없는 sync 컴포넌트.
 *
 * QueryClientProvider 하위에서 단 1회 마운트 (providers.tsx 참고).
 *
 * 동작:
 *   1. useQuery 로 GET /api/calendar 조회 (staleTime=Infinity — 세션 중 불변).
 *   2. data 도착 시 CalendarBoundsStore 갱신 (AsOfDatePicker min/max 반영).
 *   3. data 도착 시 현재 asOf 클램프 — persist rehydration 으로 asOf 가 나중에
 *      바뀌어도 deps=[data, asOf, setAsOf] 로 재실행. 클램프는 멱등이라
 *      무한루프 없음 (clamped===asOf 이면 setAsOf 미호출).
 *
 * 패턴: AuthTokenSync.tsx 의 렌더 없는 sync 컴포넌트 패턴을 미러.
 *
 * 관련:
 *   - lib/api/calendar.ts (fetchCalendarCoverage, clampUpperBound, clampAsOf)
 *   - state/calendar-bounds-store.ts (CalendarBounds, useCalendarBoundsStore)
 *   - state/as-of-store.ts (useAsOfStore)
 *   - app/providers.tsx (마운트 지점)
 */

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  fetchCalendarCoverage,
  clampUpperBound,
  clampAsOf,
} from "@/lib/api/calendar";
import { useCalendarBoundsStore } from "@/state/calendar-bounds-store";
import { useAsOfStore } from "@/state/as-of-store";

export function CalendarBoundsSync(): null {
  const { data } = useQuery({
    queryKey: ["calendar-coverage"],
    queryFn: ({ signal }) => fetchCalendarCoverage(signal),
    // 캘린더 범위는 세션 중 사실상 불변 — 재조회 불필요.
    staleTime: Infinity,
  });

  const setBounds = useCalendarBoundsStore((s) => s.setBounds);
  const asOf = useAsOfStore((s) => s.asOf);
  const setAsOf = useAsOfStore((s) => s.setAsOf);

  // effect 1: data 도착 시 CalendarBoundsStore 갱신.
  useEffect(() => {
    if (!data) return;
    setBounds({
      minDate: data.minDate,
      maxDate: data.maxDate,
      lowerBound: data.earliestBusinessDay,
      upperBound: clampUpperBound(data),
    });
  }, [data, setBounds]);

  // effect 2: data 도착 시 현재 asOf 클램프.
  // deps 에 asOf 포함 — persist rehydration 후 asOf 값이 바뀌면 재실행.
  // clampAsOf 는 순수함수+멱등 — clamped===asOf 이면 setAsOf 미호출 → 무한루프 없음.
  useEffect(() => {
    if (!data) return;
    const lower = data.earliestBusinessDay;
    const upper = clampUpperBound(data);
    const clamped = clampAsOf(asOf, lower, upper);
    if (clamped !== asOf) {
      setAsOf(clamped);
    }
  }, [data, asOf, setAsOf]);

  return null;
}
