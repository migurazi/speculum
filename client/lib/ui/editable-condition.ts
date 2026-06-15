/**
 * EditableCondition — UI 전용 condition row 모델.
 *
 * 배경 (R-3):
 *   Screener / Backtest 의 조건 목록은 사용자가 add/remove 하는 동적 리스트다.
 *   React list 의 `key={index}` 는 중간 항목 삭제 시 뒤 항목의 input state 가
 *   인접 row 로 밀려 들어가는 실제 UX 버그를 유발한다(remount 누락).
 *   각 row 에 안정적 `id` 를 부여해 key 로 사용하면 해소된다.
 *
 * payload 격리:
 *   `ScreenCondition` 은 wire schema(backend Pydantic strict)와 1:1 이므로
 *   `id` 가 절대 API body 에 섞이면 안 된다. 그래서 `id` 는 **UI 전용** 필드로
 *   `EditableCondition` 에만 두고, 전송 직전 `toScreenConditions` 로 벗겨낸다.
 *   `EditableCondition` 은 `ScreenCondition` 을 extends 하므로, 상위 state 는
 *   `EditableCondition[]` 로 보유하고 mutation/snapshot/save 경계에서만 strip.
 */

import type { ScreenCondition } from "@/lib/api/screen";

/** UI 전용 condition row — wire `ScreenCondition` + 안정적 `id`. */
export interface EditableCondition extends ScreenCondition {
  /** React list key 전용. API payload 에 절대 포함하지 않는다. */
  readonly id: string;
}

/**
 * 안정적 row id 생성. `crypto.randomUUID` 우선, 미지원 환경은 fallback.
 *
 * 충돌만 피하면 되는 비암호학 용도이므로 fallback 도 충분하다.
 */
export function newConditionId(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }
  return `cond-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * 새 EditableCondition row. 기본값은 빈 조건(`factor:"" op:"<" value:""`).
 *
 * @param init factor/op/value 부분 override (선택).
 */
export function newEditableCondition(
  init?: Partial<ScreenCondition>,
): EditableCondition {
  return {
    id: newConditionId(),
    factor: init?.factor ?? "",
    op: init?.op ?? "<",
    value: init?.value ?? "",
  };
}

/**
 * 기존 wire conditions(예: Run 재현 입력)를 EditableCondition[] 로 승격.
 * 각 row 에 새 id 를 부여한다(원본에 id 없음 전제).
 */
export function toEditableConditions(
  rows: ReadonlyArray<ScreenCondition>,
): EditableCondition[] {
  return rows.map((r) => newEditableCondition(r));
}

/**
 * 전송 직전 strip — `id` 를 제거해 순수 wire `ScreenCondition[]` 로 변환.
 *
 * **반드시** API body / snapshot freeze / save 경계에서 호출한다. 그래야
 * `id` 가 backend 요청·저장 hash 입력에 섞이지 않는다(ADR-0023 D7 result_hash
 * 격리, reproduce 일관성).
 */
export function toScreenConditions(
  rows: ReadonlyArray<EditableCondition>,
): ScreenCondition[] {
  // 명시적 필드 추출 — rest spread 로 id 만 떼는 것보다 wire 표면을 못박는다.
  return rows.map(({ factor, op, value }) => ({ factor, op, value }));
}
