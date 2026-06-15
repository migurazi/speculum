/**
 * ConditionBuilder — R-3 회귀 가드: 중간 row 삭제 시 나머지 input 값 보존.
 *
 * 버그(과거): `key={index}` 사용 → 중간 항목 삭제 시 React 가 row 를 index 로
 * 재조정해 뒤 row 의 input(특히 focus / 비제어 잔여 상태)이 인접으로 밀린다.
 * 수정: 각 row 에 안정적 `id` 를 부여해 `key={id}` 사용.
 *
 * 본 테스트는 실제 useState 를 가진 stateful harness 로 ConditionBuilder 를
 * 구동한다(controlled 흐름 그대로). 3개 row 에 서로 다른 값을 넣고 가운데를
 * 삭제한 뒤, 남은 두 row 의 값이 첫째/셋째 그대로인지 검증한다. 또한 삭제 후
 * 남은 DOM input 노드 동일성(remount 없음)을 확인해 key 안정성을 직접 가드한다.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";

import { ConditionBuilder } from "../ConditionBuilder";
import {
  newEditableCondition,
  type EditableCondition,
} from "@/lib/ui/editable-condition";
import { renderWithIntl } from "@/test-utils/intl";

// factor fetch 실패 → text input fallback 경로 사용(드롭다운 비동기 의존 제거,
// value 입력이 단순 textbox 로 가능). 본 테스트는 key 안정성만 검증한다.
const FAILED_FACTORS = new Response("err", { status: 500 });

function Harness({
  initial,
}: {
  readonly initial: ReadonlyArray<EditableCondition>;
}): JSX.Element {
  const [conditions, setConditions] = useState(initial);
  return <ConditionBuilder conditions={conditions} onChange={setConditions} />;
}

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("ConditionBuilder key stability (R-3)", () => {
  let fetchSpy: MockInstance<typeof fetch>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("중간 row 삭제 시 나머지 row 의 값/factor 가 보존된다", async () => {
    // 모든 ConditionBuilder 인스턴스가 factor fetch 실패 → text input fallback.
    fetchSpy.mockResolvedValue(FAILED_FACTORS.clone());

    const initial: EditableCondition[] = [
      newEditableCondition({ factor: "per", op: "<", value: "10" }),
      newEditableCondition({ factor: "roe", op: ">", value: "20" }),
      newEditableCondition({ factor: "pbr", op: "<", value: "30" }),
    ];

    renderWithIntl(
      <QueryClientProvider client={makeClient()}>
        <Harness initial={initial} />
      </QueryClientProvider>,
    );

    // fallback text input 렌더 대기
    await waitFor(() => {
      expect(
        screen.getByRole("textbox", { name: "조건 1 factor" }),
      ).toBeInTheDocument();
    });

    // 삭제 전: 첫째 value input DOM 노드 캡처(remount 없음 검증용)
    const firstValueBefore = screen.getByRole("textbox", {
      name: "조건 1 값",
    }) as HTMLInputElement;
    expect(firstValueBefore.value).toBe("10");

    const user = userEvent.setup();
    // 가운데(조건 2) 삭제
    await user.click(screen.getByRole("button", { name: "조건 2 삭제" }));

    // 삭제 후: row 2개. 남은 값이 첫째(10)/셋째(30) 그대로여야 한다.
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "조건 3 삭제" }),
      ).not.toBeInTheDocument();
    });

    const firstValueAfter = screen.getByRole("textbox", {
      name: "조건 1 값",
    }) as HTMLInputElement;
    const secondValueAfter = screen.getByRole("textbox", {
      name: "조건 2 값",
    }) as HTMLInputElement;

    // 핵심: 가운데 삭제 후 남은 두 row 는 per/10 과 pbr/30 (roe/20 제거됨).
    expect(firstValueAfter.value).toBe("10");
    expect(secondValueAfter.value).toBe("30");

    const firstFactorAfter = screen.getByRole("textbox", {
      name: "조건 1 factor",
    }) as HTMLInputElement;
    const secondFactorAfter = screen.getByRole("textbox", {
      name: "조건 2 factor",
    }) as HTMLInputElement;
    expect(firstFactorAfter.value).toBe("per");
    expect(secondFactorAfter.value).toBe("pbr");

    // key 안정성 직접 증거: 살아남은 첫째 row 의 input DOM 노드가 동일 인스턴스
    // (stable key → React 가 remount 하지 않음). index key 였다면 재조정으로
    // 노드 정체성이 달라질 수 있다.
    expect(firstValueAfter).toBe(firstValueBefore);
  });

  it("각 row id 는 서로 다르다", () => {
    const a = newEditableCondition();
    const b = newEditableCondition();
    expect(a.id).not.toBe(b.id);
  });
});
