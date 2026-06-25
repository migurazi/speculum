"use client";

/**
 * Calendar Bounds Store — 서버 유효 캘린더 범위 저장.
 *
 * CalendarBoundsSync 컴포넌트가 GET /api/calendar 응답을 받아 이 store 에
 * 저장한다. persist 없는 단순 메모리 store — 서버 유래 데이터라 세션 간 영구화
 * 불필요. 페이지 새로고침 시 CalendarBoundsSync 가 재조회.
 *
 * 소비자:
 *   - components/AsOfDatePicker.tsx — input min 속성 반영(picker 하한).
 *
 * 관련:
 *   - lib/api/calendar.ts (CalendarCoverage, fetchCalendarCoverage)
 *   - components/CalendarBoundsSync.tsx (store 쓰기 주체)
 *   - state/as-of-store.ts (참고 패턴, persist 미사용 차이점)
 */

import { create } from "zustand";

/** AsOfDatePicker 가 사용하는 캘린더 유효 범위. */
export interface CalendarBounds {
  /** 지원 최소 일자 ("YYYY-MM-DD"). */
  readonly minDate: string;
  /** 지원 최대 일자 ("YYYY-MM-DD"). */
  readonly maxDate: string;
}

interface CalendarBoundsState {
  /** 서버에서 조회한 캘린더 범위. 조회 전 null. */
  readonly bounds: CalendarBounds | null;
  /** 범위 설정 — CalendarBoundsSync 가 useQuery data 도착 시 호출. */
  readonly setBounds: (b: CalendarBounds) => void;
}

/**
 * 캘린더 범위 전역 store.
 *
 * persist 없음 — 서버 유래 데이터라 localStorage 영구화 불필요.
 * 초기값 bounds=null. CalendarBoundsSync 가 mount 후 데이터를 채운다.
 */
export const useCalendarBoundsStore = create<CalendarBoundsState>((set) => ({
  bounds: null,
  setBounds: (bounds) => set({ bounds }),
}));
