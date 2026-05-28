/**
 * Vitest setup — @testing-library/jest-dom custom matcher 활성 + jsdom
 * 미지원 API polyfill.
 *
 * - jest-dom: `toBeInTheDocument()` 등 DOM matcher.
 * - ResizeObserver / IntersectionObserver: radix-ui 컴포넌트가 의존. jsdom
 *   미지원 — minimal stub 으로 throw 회피.
 */

import "@testing-library/jest-dom/vitest";

// radix-ui (Tooltip / Dialog 등) 가 ResizeObserver 사용. jsdom 미지원.
class ResizeObserverStub {
  observe(): void {
    // no-op
  }
  unobserve(): void {
    // no-op
  }
  disconnect(): void {
    // no-op
  }
}
globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;

// IntersectionObserver — radix-ui 의 일부 component 가 의존.
class IntersectionObserverStub {
  readonly root: Element | null = null;
  readonly rootMargin: string = "";
  readonly thresholds: ReadonlyArray<number> = [];
  observe(): void {
    // no-op
  }
  unobserve(): void {
    // no-op
  }
  disconnect(): void {
    // no-op
  }
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}
globalThis.IntersectionObserver =
  IntersectionObserverStub as unknown as typeof IntersectionObserver;
