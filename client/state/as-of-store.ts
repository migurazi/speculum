"use client";

/**
 * As-of date global store — ADR-0008 D1.2.
 *
 * 모든 화면이 본 store 를 통과해 PIT 기준 일자를 인지. zustand 사용 — context
 * 없이 hook 호출만으로 전역 access.
 *
 * **localStorage 영구화 안 함** (ADR-0008 D1.2 — "로그인 직후 default 는 항상
 * 최근 영업일"). 세션 내 navigation(탭/페이지 전환)은 zustand 메모리로 유지되나,
 * 새 페이지 로드(새로고침/새 세션)는 항상 `kstToday()` 로 default. 과거에 localStorage
 * 에 stale 한 as_of(예: 재무 공시 전 일자)가 남아 사용자를 가두던 회귀를 제거 —
 * stale 날짜는 전 factor 가 PIT-NA 라 스크리너가 0건이 되는데, 그게 "데이터 없음"
 * 처럼 보였다. 최근일 default 로 실데이터가 바로 보이게 한다.
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
  /** asOf 변경 (세션 내 메모리 유지 — 영구화 없음). */
  readonly setAsOf: (date: string) => void;
  /** Today 로 reset — header picker 의 "오늘" button 용. */
  readonly resetToToday: () => void;
}

/**
 * 본 store 의 단일 export hook.
 *
 * 첫 mount + 매 페이지 로드 시 `kstToday()` default (localStorage 영구화 없음 —
 * ADR-0008 D1.2). 같은 session 안의 navigation(탭/페이지 전환)은 zustand 메모리로
 * 선택값 유지. SSR 첫 render 와 client 첫 render 모두 kstToday 라 hydration mismatch
 * 없음.
 *
 * `isToday` 는 store state 에서 derived selector(`useIsToday`)로 분리 — 매 re-render
 * 시 `kstToday()` 재계산(자정 crossover 자동 반영).
 */
export const useAsOfStore = create<AsOfState>()((set) => ({
  asOf: kstToday(),
  setAsOf: (date: string) => {
    set({ asOf: date });
  },
  resetToToday: () => {
    set({ asOf: kstToday() });
  },
}));


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
