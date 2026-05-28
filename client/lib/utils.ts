/**
 * Speculum client utility helpers — T32 cycle.
 *
 * `cn` = shadcn/ui 의 표준 className combiner. clsx 의 truthy 검사 + tailwind-
 * merge 의 충돌 클래스 dedup. Component 의 conditional styling 의 표준 도구.
 */

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Conditional Tailwind className 결합.
 *
 * Example:
 *   cn("px-4", isActive && "bg-blue-500", className) → 정렬·dedup 된 단일 string.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
