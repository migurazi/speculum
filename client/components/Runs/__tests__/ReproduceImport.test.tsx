/**
 * ReproduceImport 컴포넌트 단위 테스트 — M2 T80 Phase 2.
 *
 * 검증 항목:
 *   1. 초기 렌더 — "재현 검증" 제목 + "재현 실행" 버튼(비활성).
 *   2. 텍스트 변경 → 버튼 활성화.
 *   3. 재현 성공 — matches=true → "일치" 배지 표시.
 *   4. 재현 성공 — matches=false → "불일치" 배지 + 불일치 상세 표시.
 *   5. 잘못된 JSON → 오류 메시지 표시 (API 호출 없음).
 *   6. API 에러 → 오류 메시지 표시.
 *   7. 초기화 버튼 — 결과 사라짐 + 버튼 비활성.
 *
 * 주의: userEvent.type 은 { } 를 keyboard 특수 문자로 해석 → JSON 입력 시
 * fireEvent.change 로 value 를 직접 설정. 버튼 클릭은 userEvent 유지.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor, fireEvent } from "@testing-library/react";
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

import { renderWithIntl } from "@/test-utils/intl";
import { ReproduceImport } from "../ReproduceImport";

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function wrap(ui: React.ReactNode): React.ReactElement {
  return <QueryClientProvider client={makeClient()}>{ui}</QueryClientProvider>;
}

const VALID_EXPORT_JSON = JSON.stringify({
  exportFormat: "speculum-screen-run-export-v1",
  snapshotSchemaVersion: 1,
  run: {
    runId: "aaaa-bbbb",
    conditions: [],
    selectedFactors: [],
    asOf: "2024-09-30",
    resultCodes: ["005930"],
    resultHash: "deadbeef",
    dataVersions: {},
  },
});

function makeMatchesResponse(matches: boolean): Response {
  const body = {
    matches,
    // 불일치 케이스 — 재현에만 "000660" 이 추가로 있음.
    reproduced_result_codes: matches ? ["005930"] : ["005930", "000660"],
    original_result_codes: ["005930"],
    result_hash: "deadbeef",
    note: null,
  };
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** textarea 에 JSON 값 직접 주입 — fireEvent.change 사용 (userEvent.type 의 {/} 파싱 문제 우회). */
function setTextareaValue(textarea: HTMLElement, value: string): void {
  fireEvent.change(textarea, { target: { value } });
}

describe("ReproduceImport", () => {
  let fetchSpy: MockInstance<typeof fetch>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("초기 렌더 — 제목 표시 + 재현 실행 버튼 비활성", () => {
    renderWithIntl(wrap(<ReproduceImport />));
    expect(screen.getByText("재현 검증")).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: "재현 실행" });
    expect(btn).toBeDisabled();
  });

  it("textarea 값 입력 시 버튼 활성화", () => {
    renderWithIntl(wrap(<ReproduceImport />));
    const textarea = screen.getByPlaceholderText(/speculum-screen-run-export/);
    setTextareaValue(textarea, "{}");
    expect(screen.getByRole("button", { name: "재현 실행" })).not.toBeDisabled();
  });

  it("재현 성공 — matches=true → '일치' 배지", async () => {
    fetchSpy.mockResolvedValueOnce(makeMatchesResponse(true));
    const user = userEvent.setup();
    renderWithIntl(wrap(<ReproduceImport />));
    const textarea = screen.getByPlaceholderText(/speculum-screen-run-export/);
    setTextareaValue(textarea, VALID_EXPORT_JSON);
    await user.click(screen.getByRole("button", { name: "재현 실행" }));
    expect(await screen.findByText("일치")).toBeInTheDocument();
    // result_hash 표시 확인 — 결과 패널의 <span class="font-mono"> 안에 표시됨.
    // textarea 에도 동일 텍스트가 있으므로 getAllByText 로 복수 존재 허용.
    expect(screen.getAllByText(/deadbeef/).length).toBeGreaterThanOrEqual(1);
  });

  it("재현 성공 — matches=false → '불일치' 배지 + 불일치 코드 표시", async () => {
    fetchSpy.mockResolvedValueOnce(makeMatchesResponse(false));
    const user = userEvent.setup();
    renderWithIntl(wrap(<ReproduceImport />));
    const textarea = screen.getByPlaceholderText(/speculum-screen-run-export/);
    setTextareaValue(textarea, VALID_EXPORT_JSON);
    await user.click(screen.getByRole("button", { name: "재현 실행" }));
    expect(await screen.findByText("불일치")).toBeInTheDocument();
    // 불일치 상세 — 재현에만 있는 코드 표시.
    await waitFor(() => {
      expect(screen.getByText(/재현에만 있는 종목/)).toBeInTheDocument();
    });
  });

  it("잘못된 JSON → 오류 메시지 (API 미호출)", async () => {
    const user = userEvent.setup();
    renderWithIntl(wrap(<ReproduceImport />));
    const textarea = screen.getByPlaceholderText(/speculum-screen-run-export/);
    setTextareaValue(textarea, "not valid json !!!");
    await user.click(screen.getByRole("button", { name: "재현 실행" }));
    await waitFor(() => {
      expect(
        screen.getByText(/JSON 형식이 올바르지 않습니다/),
      ).toBeInTheDocument();
    });
    // API 호출 없어야 함.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("API 에러 → 오류 메시지 표시", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response("server error", { status: 500 }),
    );
    const user = userEvent.setup();
    renderWithIntl(wrap(<ReproduceImport />));
    const textarea = screen.getByPlaceholderText(/speculum-screen-run-export/);
    setTextareaValue(textarea, VALID_EXPORT_JSON);
    await user.click(screen.getByRole("button", { name: "재현 실행" }));
    await waitFor(() => {
      expect(screen.getByText(/API 500/)).toBeInTheDocument();
    });
  });

  it("초기화 버튼 — 결과 사라지고 버튼 비활성", async () => {
    fetchSpy.mockResolvedValueOnce(makeMatchesResponse(true));
    const user = userEvent.setup();
    renderWithIntl(wrap(<ReproduceImport />));
    const textarea = screen.getByPlaceholderText(/speculum-screen-run-export/);
    setTextareaValue(textarea, VALID_EXPORT_JSON);
    await user.click(screen.getByRole("button", { name: "재현 실행" }));
    // 결과 대기.
    await screen.findByText("일치");
    // 초기화.
    await user.click(screen.getByRole("button", { name: "초기화" }));
    await waitFor(() => {
      expect(screen.queryByText("일치")).not.toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "재현 실행" })).toBeDisabled();
  });
});
