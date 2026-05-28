/**
 * SaveRunButton 단위 테스트 — T40.
 *
 * 매트릭스:
 *   1. disabled when canSave=false
 *   2. disabled when conditions empty
 *   3. disabled when selectedFactors empty
 *   4. POST /api/runs body 정확 (conditions + selected_factors + as_of)
 *   5. 성공 시 inline notice 표시 + onSaved 콜백
 *   6. 실패 시 alert 표시
 *   7. pending 시 "저장 중..." + disabled
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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

import { SaveRunButton } from "../SaveRunButton";
import type { ScreenCondition } from "@/lib/api/screen";

const CONDITION: ScreenCondition = {
  factor: "per:ttm-consolidated-ifrs",
  op: "<",
  value: "10",
};

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function wrap(ui: React.ReactNode): React.ReactElement {
  return <QueryClientProvider client={makeClient()}>{ui}</QueryClientProvider>;
}

const SAMPLE_SNAPSHOT_RESPONSE = {
  id: "11111111-1111-1111-1111-111111111111",
  user_id: "22222222-2222-2222-2222-222222222222",
  as_of: "2024-09-30",
  result_codes: ["005930", "000660"],
  result_hash: "abc123",
  data_versions: { factor_pack: "v1.0", price_adjustment: "v1.0" },
  computed_at: "2026-05-28T00:00:00Z",
  conditions: [{ factor: "per:ttm-consolidated-ifrs", op: "<", value: "10" }],
  selected_factors: ["per:ttm-consolidated-ifrs"],
};

describe("SaveRunButton", () => {
  let fetchSpy: MockInstance<typeof fetch>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("disabled when canSave=false", () => {
    render(
      wrap(
        <SaveRunButton
          conditions={[CONDITION]}
          selectedFactors={["per:ttm-consolidated-ifrs"]}
          asOf="2024-09-30"
          canSave={false}
        />,
      ),
    );
    expect(screen.getByText("Save Run")).toBeDisabled();
  });

  it("disabled when conditions empty", () => {
    render(
      wrap(
        <SaveRunButton
          conditions={[]}
          selectedFactors={["per:ttm-consolidated-ifrs"]}
          asOf="2024-09-30"
          canSave={true}
        />,
      ),
    );
    expect(screen.getByText("Save Run")).toBeDisabled();
  });

  it("disabled when selectedFactors empty", () => {
    render(
      wrap(
        <SaveRunButton
          conditions={[CONDITION]}
          selectedFactors={[]}
          asOf="2024-09-30"
          canSave={true}
        />,
      ),
    );
    expect(screen.getByText("Save Run")).toBeDisabled();
  });

  it("POSTs /api/runs with conditions + selected_factors + as_of", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_SNAPSHOT_RESPONSE), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const user = userEvent.setup();
    render(
      wrap(
        <SaveRunButton
          conditions={[CONDITION]}
          selectedFactors={["per:ttm-consolidated-ifrs"]}
          asOf="2024-09-30"
          canSave={true}
        />,
      ),
    );
    await user.click(screen.getByText("Save Run"));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));

    const calls = fetchSpy.mock.calls as unknown as ReadonlyArray<
      [unknown, ...unknown[]]
    >;
    const [url, init] = calls[0]!;
    expect(String(url)).toContain("/api/runs?as_of=2024-09-30");
    expect((init as RequestInit).method).toBe("POST");
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body).toEqual({
      conditions: [CONDITION],
      selected_factors: ["per:ttm-consolidated-ifrs"],
    });
  });

  it("shows success notice + invokes onSaved", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_SNAPSHOT_RESPONSE), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(
      wrap(
        <SaveRunButton
          conditions={[CONDITION]}
          selectedFactors={["per:ttm-consolidated-ifrs"]}
          asOf="2024-09-30"
          canSave={true}
          onSaved={onSaved}
        />,
      ),
    );
    await user.click(screen.getByText("Save Run"));
    const notice = await screen.findByRole("status");
    expect(notice.textContent).toContain("저장 완료");
    expect(notice.textContent).toContain(SAMPLE_SNAPSHOT_RESPONSE.id);
    expect(notice.textContent).toContain("2 종목");
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(onSaved.mock.calls[0]![0].id).toBe(SAMPLE_SNAPSHOT_RESPONSE.id);
  });

  it("shows alert on error", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response("Internal Error", { status: 500 }),
    );
    const user = userEvent.setup();
    render(
      wrap(
        <SaveRunButton
          conditions={[CONDITION]}
          selectedFactors={["per:ttm-consolidated-ifrs"]}
          asOf="2024-09-30"
          canSave={true}
        />,
      ),
    );
    await user.click(screen.getByText("Save Run"));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("저장 실패");
  });

  // oracle T40 M1/M2 회귀 — 직전 success 후 error 시 양 notice 동시 표시 차단.
  it("clears prior success notice before next attempt", async () => {
    fetchSpy
      .mockResolvedValueOnce(
        new Response(JSON.stringify(SAMPLE_SNAPSHOT_RESPONSE), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response("Internal Error", { status: 500 }),
      );
    const user = userEvent.setup();
    render(
      wrap(
        <SaveRunButton
          conditions={[CONDITION]}
          selectedFactors={["per:ttm-consolidated-ifrs"]}
          asOf="2024-09-30"
          canSave={true}
        />,
      ),
    );
    await user.click(screen.getByText("Save Run"));
    await screen.findByRole("status"); // 첫 success.

    await user.click(screen.getByText("Save Run"));
    // 두번째 시도 — error 표시 + 직전 success 제거.
    await waitFor(() => {
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
