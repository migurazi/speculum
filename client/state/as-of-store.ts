"use client";

/**
 * As-of date global store — ADR-0008 D1.2.
 *
 * 모든 화면이 본 store 를 통과해 PIT 기준 일자를 인지. zustand 사용 — context
 * 없이 hook 호출만으로 전역 access. localStorage 영구화 + 새 session 시작
 * 시 default reset (oracle ADR-0008 D1.2 의 "로그인 직후 default 는 항상
 * 최근 영업일").
 *
 * 운영 약속:
 *   - `asOf` 는 ISO 8601 date string ("YYYY-MM-DD"). 시간 단위 미지원
 *     (ADR-0008 D8 의 일 단위 PIT).
 *   - `setAsOf` 는 backend 의 영업일 normalize 와 무관 — UI 가 그대로 전달.
 *     실 영업일은 backend AsOfPolicy 가 X-AsOf-Normalized header 로 반환
 *     (M0 backlog 의 UI 동기화).
 *   - `kstToday()` 는 client browser 의 timezone 무관 KST 기준 today.
 *
 * 관련 ADR:
 * - ADR-0008 D1 (전역 state), D2 (영업일), D4 (today vs past UI).
 * - ADR-0008 D8 (일 단위 PIT — 시간 단위 미사용).
 */

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

const STORAGE_KEY = "speculum-as-of-v1";

/**
 * KST timezone 기준 today (`YYYY-MM-DD`).
 *
 * Browser default `new Date()` 는 user local timezone — 운영 KST 와 다를
 * 위험. `Intl.DateTimeFormat` 의 `timeZone: "Asia/Seoul"` 강제로 일관.
 * 형식 `en-CA` = ISO YYYY-MM-DD.
 */
export function kstToday(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

interface AsOfState {
  /** ISO 8601 date — "YYYY-MM-DD". 항상 영업일 정규화는 backend 책임. */
  readonly asOf: string;
  /** asOf 변경 + localStorage 영구화. */
  readonly setAsOf: (date: string) => void;
  /** Today 로 reset — header picker 의 "오늘" button 용. */
  readonly resetToToday: () => void;
}

/**
 * 본 store 의 단일 export hook.
 *
 * 첫 mount 시 `kstToday()` default. localStorage 의 저장된 값은 본 cycle
 * 의 scope 상 사용 X — ADR-0008 D1.2 의 "로그인 직후 default 는 최근
 * 영업일" 약속. 단 같은 session 안의 navigation 에서는 zustand persist 가
 * 유지 (탭 전환 등).
 *
 * oracle 리뷰 M1 — `isToday` 는 store state 에서 derived selector 로 분리.
 * `useIsToday()` hook 호출 시 매번 `kstToday()` 와 비교. 장점:
 *   - zustand persist 의 onRehydrateStorage mutation 의존성 제거 (v5 호환).
 *   - 자정 crossover 시 자동 stale 해소 (re-render 시점에 재계산).
 *   - store 의 readonly interface 와 mutation assertion 충돌 해소.
 *
 * Note (Hydration 안전):
 *   `persist` middleware 는 client mount 후 storage 에서 값을 rehydrate.
 *   SSR 첫 render = default state (kstToday). 첫 hydration 이후 storage
 *   value 가 반영. layout.tsx 의 SSR 출력과 첫 client render 일치 보장.
 */
export const useAsOfStore = create<AsOfState>()(
  persist(
    (set) => ({
      asOf: kstToday(),
      setAsOf: (date: string) => {
        set({ asOf: date });
      },
      resetToToday: () => {
        set({ asOf: kstToday() });
      },
    }),
    {
      name: STORAGE_KEY,
      // localStorage 만 사용 — sessionStorage 는 새 tab 마다 reset.
      storage: createJSONStorage(() => localStorage),
      // asOf 만 영구화 — 다른 derived 값 없음.
      partialize: (state) => ({ asOf: state.asOf }),
    },
  ),
);


/**
 * `asOf` 가 KST today 와 일치하는지 — derived selector.
 *
 * 호출 시점 (매 re-render) 마다 `kstToday()` 재계산 → 자정 crossover 도 자동
 * 반영. mount 시 store 변경 없이 derived 라 zustand re-render 영향 X (asOf
 * 변경 시에만 re-render).
 */
export function useIsToday(): boolean {
  const asOf = useAsOfStore((s) => s.asOf);
  return asOf === kstToday();
}
