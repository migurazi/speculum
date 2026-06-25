"use client";

/**
 * StockSearch — NavBar 의 종목 검색 combobox.
 *
 * 기존 NavBar 의 readOnly 스텁(M0 T36/T37 미구현)을 교체한다.
 * 백엔드 GET /api/stocks/search 를 TanStack Query 로 호출하고,
 * WAI-ARIA combobox 패턴으로 드롭다운 결과를 표시한다.
 *
 * 설계 결정:
 *   - 디바운스 250ms — 타이핑 중 과도한 API 호출 억제. useEffect + setTimeout.
 *   - staleTime 30_000 — 같은 query 는 30초 내 재요청 없음(검색어 재입력 UX).
 *   - 외부 클릭 닫힘 — document mousedown 감지 + ref 비교. onBlur 지연 대신
 *     mousedown 을 쓰는 이유: onBlur 는 클릭 대상 항목의 onClick 보다 먼저
 *     발화하여 click 이벤트가 cancel 되는 race condition 이 있다.
 *   - SSR 안전 — mounted state 로 hydration 깜빡임 방지(AsOfDatePicker 패턴).
 *     단 입력 자체는 mount 전에도 레이아웃 유지를 위해 표시한다.
 *   - 키보드 a11y (WAI-ARIA combobox):
 *       ArrowDown/ArrowUp — highlighted index 순환
 *       Enter — 하이라이트 항목 선택 (없으면 첫 항목)
 *       Escape — 드롭다운 닫기
 *
 * 관련:
 *   - M0 T36/T37 (종목 검색 → Stock Detail 이동).
 *   - lib/api/stocks.ts searchStocks().
 *   - state/as-of-store.ts useAsOfStore.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { searchStocks } from "@/lib/api/stocks";
import type { StockSummary } from "@/lib/api/stocks";
import { useAsOfStore } from "@/state/as-of-store";

interface StockSearchProps {
  readonly className?: string;
  /**
   * 디바운스 대기 시간(ms). 기본 250ms.
   * 테스트 환경에서 0 을 전달하면 디바운스 없이 즉시 query 실행 가능.
   */
  readonly debounceMs?: number;
  /**
   * 종목 선택 시 콜백. 전달하면 `/stock/{code}` 이동 대신 본 콜백을 호출한다
   * (예: Compare 의 "검색해서 추가"). 미전달(기본)이면 상세 화면으로 이동 —
   * NavBar 검색의 기존 동작 하위호환.
   */
  readonly onSelect?: (item: StockSummary) => void;
  /** 입력 placeholder override. 미전달 시 공통 검색 placeholder. */
  readonly placeholder?: string;
}

export function StockSearch({
  className,
  debounceMs = 250,
  onSelect,
  placeholder,
}: StockSearchProps): JSX.Element {
  const t = useTranslations("common");
  const router = useRouter();
  const asOf = useAsOfStore((s) => s.asOf);

  // 입력값(즉시)과 디바운스된 query(API 호출용) 분리
  const [inputValue, setInputValue] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");

  // 드롭다운 열림 상태 + 키보드 하이라이트 인덱스
  const [isOpen, setIsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);

  // SSR hydration 안전 — 첫 mount 전까지 드롭다운 로직 비활성화
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  // 외부 클릭 감지를 위한 컨테이너 ref
  const containerRef = useRef<HTMLDivElement>(null);

  // WAI-ARIA id 연결용
  const listboxId = useId();
  const inputId = useId();

  // ─── 디바운스 ────────────────────────────────────────────────────────────
  // debounceMs(기본 250ms) 후에만 debouncedQ 를 갱신 → useQuery enabled 조건 충족 시에만 호출.
  // 테스트에서 debounceMs=0 을 전달하면 setTimeout(fn, 0) → 마이크로태스크 이후 즉시 실행.
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQ(inputValue.trim());
    }, debounceMs);
    return () => clearTimeout(timer);
  }, [inputValue, debounceMs]);

  // query 가 바뀌면 하이라이트 초기화
  useEffect(() => {
    setHighlightedIndex(-1);
  }, [debouncedQ]);

  // ─── TanStack Query ──────────────────────────────────────────────────────
  const { data, isLoading, isError } = useQuery({
    queryKey: ["stock-search", debouncedQ, asOf],
    queryFn: ({ signal }) =>
      searchStocks(debouncedQ, asOf, { limit: 10, signal }),
    // query 길이 0 이면 미호출 — 빈 검색으로 전체 목록 로드 방지
    enabled: mounted && debouncedQ.length > 0,
    staleTime: 30_000,
  });

  const items: ReadonlyArray<StockSummary> = data?.items ?? [];

  // 하이라이트 인덱스를 현재 결과 범위로 보정 — 재요청으로 items 가 줄어들면
  // 직전 highlightedIndex 가 범위를 벗어나 aria-activedescendant 가 존재하지 않는
  // option id 를 가리킬 수 있다(dangling). 표시·aria 계산에는 보정값을 사용한다.
  const safeHighlightedIndex =
    highlightedIndex >= 0 && highlightedIndex < items.length
      ? highlightedIndex
      : -1;

  // ─── 외부 클릭 시 드롭다운 닫기 ──────────────────────────────────────────
  // mousedown 을 쓰는 이유: blur → onClick 순서 race 를 피하기 위해
  useEffect(() => {
    if (!mounted) return;

    function handleMouseDown(e: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    }

    document.addEventListener("mousedown", handleMouseDown);
    return () => document.removeEventListener("mousedown", handleMouseDown);
  }, [mounted]);

  // ─── 드롭다운 표시 조건 ──────────────────────────────────────────────────
  // mounted 되고, query 있고, 드롭다운 open 상태일 때만 표시
  const showDropdown =
    mounted && isOpen && debouncedQ.length > 0;

  // ─── 종목 선택 → /stock/{code} 이동 ─────────────────────────────────────
  const selectItem = useCallback(
    (item: StockSummary) => {
      setInputValue("");
      setDebouncedQ("");
      setIsOpen(false);
      setHighlightedIndex(-1);
      // onSelect 가 있으면 그 콜백(예: Compare 추가)이 우선 — 이동 안 함.
      if (onSelect) {
        onSelect(item);
        return;
      }
      router.push(`/stock/${item.code}`);
    },
    [router, onSelect],
  );

  // ─── 키보드 이벤트 핸들러 ────────────────────────────────────────────────
  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!showDropdown) return;

    switch (e.key) {
      case "ArrowDown": {
        e.preventDefault();
        // 순환: 마지막 → 처음
        setHighlightedIndex((prev) =>
          items.length === 0 ? -1 : (prev + 1) % items.length,
        );
        break;
      }
      case "ArrowUp": {
        e.preventDefault();
        // 순환: 처음 → 마지막
        setHighlightedIndex((prev) =>
          items.length === 0 ? -1 : (prev - 1 + items.length) % items.length,
        );
        break;
      }
      case "Enter": {
        e.preventDefault();
        // 하이라이트된 항목이 있으면 선택, 없으면 첫 항목
        const target =
          highlightedIndex >= 0
            ? items[highlightedIndex]
            : items[0];
        if (target) {
          selectItem(target);
        }
        break;
      }
      case "Escape": {
        e.preventDefault();
        setIsOpen(false);
        setHighlightedIndex(-1);
        break;
      }
    }
  }

  // 하이라이트된 option 의 id — aria-activedescendant 연결
  const activeDescendant =
    safeHighlightedIndex >= 0
      ? `${listboxId}-option-${safeHighlightedIndex}`
      : undefined;

  return (
    <div ref={containerRef} className={`relative ${className ?? ""}`}>
      {/* ─── 검색 입력 ─────────────────────────────────────────────────── */}
      <input
        id={inputId}
        type="search"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={showDropdown}
        aria-controls={listboxId}
        aria-activedescendant={activeDescendant}
        aria-autocomplete="list"
        aria-label={t("searchAriaLabel")}
        placeholder={placeholder ?? t("searchPlaceholder")}
        value={inputValue}
        autoComplete="off"
        className="w-full max-w-md rounded-md border border-neutral-300 px-3 py-1.5 text-sm placeholder:text-neutral-400 focus:border-neutral-500 focus:outline-none focus:ring-1 focus:ring-neutral-400"
        onChange={(e) => {
          const val = e.target.value;
          setInputValue(val);
          // 입력이 생기면 드롭다운 열기, 지우면 닫기
          if (val.trim().length > 0) {
            setIsOpen(true);
          } else {
            setIsOpen(false);
          }
        }}
        onFocus={() => {
          // 포커스 시 query 있으면 다시 열기
          if (inputValue.trim().length > 0) {
            setIsOpen(true);
          }
        }}
        onKeyDown={handleKeyDown}
      />

      {/* ─── 드롭다운 ──────────────────────────────────────────────────── */}
      {showDropdown && (
        <div
          id={listboxId}
          role="listbox"
          aria-label={t("search.resultsAriaLabel")}
          className="absolute left-0 top-full z-50 mt-1 w-full max-w-md overflow-hidden rounded-md border border-neutral-200 bg-white shadow-lg"
        >
          {/* 로딩 상태 */}
          {isLoading && (
            <div
              role="status"
              aria-live="polite"
              className="px-3 py-2 text-sm text-neutral-500"
            >
              {t("search.loading")}
            </div>
          )}

          {/* 에러 상태 */}
          {isError && !isLoading && (
            <div
              role="alert"
              className="px-3 py-2 text-sm text-neutral-500"
            >
              {t("search.error")}
            </div>
          )}

          {/* 빈 결과 */}
          {!isLoading && !isError && items.length === 0 && (
            <div
              role="status"
              aria-live="polite"
              className="px-3 py-2 text-sm text-neutral-500"
            >
              {t("search.empty")}
            </div>
          )}

          {/* 결과 목록 */}
          {!isLoading && !isError && items.length > 0 && (
            <ul className="max-h-64 overflow-y-auto py-1">
              {items.map((item, index) => (
                <li
                  key={item.id}
                  id={`${listboxId}-option-${index}`}
                  role="option"
                  aria-selected={index === safeHighlightedIndex}
                  // mousedown(not click) — input 의 blur 보다 먼저 발화하지 않도록
                  // onMouseDown + preventDefault 로 blur race 방지 후 선택
                  onMouseDown={(e) => {
                    // blur 이벤트가 선택을 방해하지 않도록 기본 focus 이동 차단
                    e.preventDefault();
                    selectItem(item);
                  }}
                  onMouseEnter={() => setHighlightedIndex(index)}
                  className={`flex cursor-pointer items-center justify-between px-3 py-2 text-sm ${
                    index === safeHighlightedIndex
                      ? "bg-neutral-100"
                      : "hover:bg-neutral-50"
                  }`}
                >
                  {/* 종목명 + 코드 */}
                  <span className="flex items-center gap-2">
                    <span className="font-medium text-neutral-900">
                      {item.name}
                    </span>
                    <span className="text-xs text-neutral-400">{item.code}</span>
                  </span>
                  {/* 시장 구분 */}
                  <span className="shrink-0 rounded bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-500">
                    {item.market}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
