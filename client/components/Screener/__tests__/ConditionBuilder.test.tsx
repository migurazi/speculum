/**
 * ConditionBuilder 단위 테스트 — factor 드롭다운 UX 검증.
 *
 * /api/factors 는 fetch mock 으로 대체. QueryClientProvider wrapper 필수.
 *
 * 테스트 매트릭스:
 *   1. 로딩 중 select disabled + placeholder 텍스트 표시
 *   2. 성공 시 factor option 목록 렌더 (name label, canonical_id value)
 *   3. factor 선택 → onChange 에 canonical_id 전달
 *   4. 조건 추가 / 삭제
 *   5. fetch 실패 시 text input fallback + 경고 메시지
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
import type { ScreenCondition } from "@/lib/api/screen";
import { renderWithIntl } from "@/test-utils/intl";

// /api/factors 응답 fixture
const MOCK_FACTORS_RESPONSE = {
  pack_slug: "krx-default",
  pack_version: "0.1.0",
  factors: [
    {
      canonical_id: "per:ttm-consolidated-ifrs",
      name: "PER (TTM, 연결, K-IFRS)",
      unit: "배",
      tags: ["valuation"],
    },
    {
      canonical_id: "roe:ttm-consolidated-ifrs",
      name: "ROE (TTM, 연결, K-IFRS)",
      unit: "%",
      tags: ["profitability"],
    },
  ],
};

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function wrap(ui: React.ReactNode): React.ReactElement {
  return (
    <QueryClientProvider client={makeClient()}>{ui}</QueryClientProvider>
  );
}

describe("ConditionBuilder", () => {
  let fetchSpy: MockInstance<typeof fetch>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("factor select 가 로딩 중 disabled + placeholder 표시", async () => {
    // fetch 가 resolve 되지 않는 pending promise → 로딩 상태 유지
    fetchSpy.mockReturnValueOnce(new Promise(() => undefined));

    const onChange = vi.fn();
    renderWithIntl(
      wrap(
        <ConditionBuilder
          conditions={[{ factor: "", op: "<", value: "" }]}
          onChange={onChange}
        />,
      ),
    );

    const select = screen.getByRole("combobox", { name: "조건 1 factor" });
    expect(select).toBeDisabled();
    // placeholder option 텍스트
    expect(select).toHaveTextContent("factor 목록 로딩 중...");
  });

  it("fetch 성공 시 factor option 목록 렌더", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify(MOCK_FACTORS_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const onChange = vi.fn();
    renderWithIntl(
      wrap(
        <ConditionBuilder
          conditions={[{ factor: "", op: "<", value: "" }]}
          onChange={onChange}
        />,
      ),
    );

    // factor option 이 렌더될 때까지 대기
    await waitFor(() => {
      expect(
        screen.getByRole("option", { name: "PER (TTM, 연결, K-IFRS)" }),
      ).toBeInTheDocument();
    });

    expect(
      screen.getByRole("option", { name: "ROE (TTM, 연결, K-IFRS)" }),
    ).toBeInTheDocument();

    // option value 가 canonical_id 인지 확인
    const perOption = screen.getByRole("option", {
      name: "PER (TTM, 연결, K-IFRS)",
    }) as HTMLOptionElement;
    expect(perOption.value).toBe("per:ttm-consolidated-ifrs");
  });

  it("factor 선택 시 onChange 에 canonical_id 전달", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify(MOCK_FACTORS_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const conditions: ScreenCondition[] = [{ factor: "", op: "<", value: "" }];
    const onChange = vi.fn((next: ReadonlyArray<ScreenCondition>) => {
      conditions.splice(0, conditions.length, ...next);
    });

    const { rerender } = renderWithIntl(
      wrap(
        <ConditionBuilder conditions={conditions} onChange={onChange} />,
      ),
    );

    await waitFor(() => {
      expect(
        screen.getByRole("option", { name: "PER (TTM, 연결, K-IFRS)" }),
      ).toBeInTheDocument();
    });

    const user = userEvent.setup();
    const select = screen.getByRole("combobox", { name: "조건 1 factor" });
    await user.selectOptions(select, "per:ttm-consolidated-ifrs");

    expect(onChange).toHaveBeenCalledTimes(1);
    const callArg = onChange.mock.calls[0]?.[0] as ReadonlyArray<ScreenCondition>;
    expect(callArg[0]?.factor).toBe("per:ttm-consolidated-ifrs");
  });

  it("조건 추가 버튼 클릭 시 onChange 에 row 추가", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify(MOCK_FACTORS_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const onChange = vi.fn();
    renderWithIntl(
      wrap(
        <ConditionBuilder conditions={[]} onChange={onChange} />,
      ),
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /조건 추가/ }));

    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0]?.[0] as ReadonlyArray<ScreenCondition>;
    expect(next).toHaveLength(1);
    expect(next[0]?.factor).toBe("");
  });

  it("삭제 버튼 클릭 시 해당 row 제거", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify(MOCK_FACTORS_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const initialConditions: ReadonlyArray<ScreenCondition> = [
      { factor: "per:ttm-consolidated-ifrs", op: "<", value: "10" },
    ];
    const onChange = vi.fn();
    renderWithIntl(
      wrap(
        <ConditionBuilder conditions={initialConditions} onChange={onChange} />,
      ),
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "조건 1 삭제" }));

    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0]?.[0] as ReadonlyArray<ScreenCondition>;
    expect(next).toHaveLength(0);
  });

  it("fetch 실패 시 text input fallback + 경고 메시지 표시", async () => {
    // QueryClient retry: false → 1회 실패로 즉시 error 상태 진입
    fetchSpy.mockResolvedValueOnce(
      new Response("Internal Server Error", { status: 500 }),
    );

    const onChange = vi.fn();
    renderWithIntl(
      wrap(
        <ConditionBuilder
          conditions={[{ factor: "", op: "<", value: "" }]}
          onChange={onChange}
        />,
      ),
    );

    // 에러 상태로 전환 대기
    await waitFor(() => {
      expect(
        screen.getByText(/factor 목록을 불러오지 못했습니다/),
      ).toBeInTheDocument();
    });

    // text input fallback 렌더 확인
    const textInput = screen.getByRole("textbox", { name: "조건 1 factor" });
    expect(textInput).toBeInTheDocument();
    // select 는 없어야 함
    expect(screen.queryByRole("combobox", { name: "조건 1 factor" })).not.toBeInTheDocument();
  });
});
