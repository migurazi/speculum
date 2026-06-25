"use client";

/**
 * CalendarBoundsSync — GET /api/calendar 결과를 store 에 동기화하는
 * 렌더 없는 sync 컴포넌트.
 *
 * QueryClientProvider 하위에서 단 1회 마운트 (providers.tsx 참고).
 *
 * 동작:
 *   1. useQuery 로 GET /api/calendar 조회 (staleTime=Infinity — 세션 중 불변).
 *   2. data 도착 시 CalendarBoundsStore 갱신 (AsOfDatePicker min 반영).
 *
 * 패턴: AuthTokenSync.tsx 의 렌더 없는 sync 컴포넌트 패턴을 미러.
 *
 * 관련:
 *   - lib/api/calendar.ts (fetchCalendarCoverage)
 *   - state/calendar-bounds-store.ts (CalendarBounds, useCalendarBoundsStore)
 *   - app/providers.tsx (마운트 지점)
 */

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchCalendarCoverage } from "@/lib/api/calendar";
import { useCalendarBoundsStore } from "@/state/calendar-bounds-store";

export function CalendarBoundsSync(): null {
  const { data } = useQuery({
    queryKey: ["calendar-coverage"],
    queryFn: ({ signal }) => fetchCalendarCoverage(signal),
    // 캘린더 범위는 세션 중 사실상 불변 — 재조회 불필요.
    staleTime: Infinity,
  });

  const setBounds = useCalendarBoundsStore((s) => s.setBounds);

  // data 도착 시 CalendarBoundsStore 갱신 (picker min 용).
  useEffect(() => {
    if (!data) return;
    setBounds({
      minDate: data.minDate,
      maxDate: data.maxDate,
    });
  }, [data, setBounds]);

  return null;
}
