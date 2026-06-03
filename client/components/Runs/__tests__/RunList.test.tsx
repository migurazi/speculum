/**
 * RunList + VersionsDiffBadge + ExportButton 단위 테스트 — Recent Runs page.
 *
 * 매트릭스:
 *   1. 빈 배열 → placeholder
 *   2. Run row 1개 → 모든 column 표시
 *   3. Run ID 8자 prefix + ellipsis
 *   4. computed_at → KST 형식
 *   5. VersionsDiffBadge — 빈 diff → "재현 가능" (녹색)
 *   6. VersionsDiffBadge — 있음 → "재현 변경: N 키" (황색) + tooltip
 *   7. VersionsDiffBadge — fetch 실패 → "버전 확인 실패"
 *   8. ExportButton — 클릭 → exportRun fetch 호출 + Blob 다운로드 트리거
 *   9. ExportButton — fetch 실패 → 에러 텍스트 표시
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

import { renderWithIntl } from "@/test-utils/intl";
import { RunList } from "../RunList";
import { VersionsDiffBadge } from "../VersionsDiffBadge";
import type { ScreenRunSnapshot } from "@/lib/api/runs";

const SAMPLE_RUN: ScreenRunSnapshot = {
  id: "11111111-1111-1111-1111-111111111111",
  user_id: "22222222-2222-2222-2222-222222222222",
  as_of: "2024-09-30",
  result_codes: ["005930", "000660", "035420"],
  result_hash: "abc123",
  data_versions: { factor_pack: "v1.0", price_adjustment: "v1.0" },
  computed_at: "2026-05-28T03:14:15Z",
  conditions: [
    { factor: "per:ttm-consolidated-ifrs", op: "<", value: "10" },
    { factor: "roe:ttm-consolidated-ifrs", op: ">", value: "0.1" },
  ],
  selected_factors: ["per:ttm-consolidated-ifrs"],
};

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

// QueryClientProvider 는 ui 내부에 유지 — renderWithIntl 이 외부 intl provider 담당.
function wrap(ui: React.ReactNode): React.ReactElement {
  return <QueryClientProvider client={makeClient()}>{ui}</QueryClientProvider>;
}

describe("RunList", () => {
  let fetchSpy: MockInstance<typeof fetch>;
  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
    // VersionsDiffBadge 가 매 row 마다 fetchRunDiff 호출 — default empty diff.
    fetchSpy.mockResolvedValue(
      new Response(
        JSON.stringify({ snapshot_id: SAMPLE_RUN.id, diff: {} }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders placeholder when runs empty", () => {
    renderWithIntl(wrap(<RunList runs={[]} />));
    expect(
      screen.getByText(/저장된 Run 이 없습니다/),
    ).toBeInTheDocument();
  });

  it("renders Run row with as_of + counts", () => {
    renderWithIntl(wrap(<RunList runs={[SAMPLE_RUN]} />));
    expect(screen.getByText("2024-09-30")).toBeInTheDocument();
    expect(screen.getByText("3 종목")).toBeInTheDocument();
    // conditions count (2).
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("renders Run ID prefix + ellipsis", () => {
    renderWithIntl(wrap(<RunList runs={[SAMPLE_RUN]} />));
    // id slice(0,8) = "11111111".
    expect(screen.getByText(/11111111…/)).toBeInTheDocument();
  });
});


describe("VersionsDiffBadge", () => {
  let fetchSpy: MockInstance<typeof fetch>;
  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows '재현 가능' for empty diff", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ snapshot_id: SAMPLE_RUN.id, diff: {} }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    renderWithIntl(wrap(<VersionsDiffBadge runId={SAMPLE_RUN.id} />));
    expect(await screen.findByText("재현 가능")).toBeInTheDocument();
  });

  it("shows '재현 변경' for non-empty diff", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          snapshot_id: SAMPLE_RUN.id,
          diff: {
            factor_pack: ["v1.0", "v1.1"],
            price_adjustment: ["v1.0", "v1.0.1"],
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    renderWithIntl(wrap(<VersionsDiffBadge runId={SAMPLE_RUN.id} />));
    const badge = await screen.findByText(/재현 변경/);
    expect(badge.textContent).toContain("2");  // 2 키 변경
    // tooltip = title 속성에 키 list.
    expect(badge.getAttribute("title")).toContain("factor_pack");
    expect(badge.getAttribute("title")).toContain("v1.0 → v1.1");
  });

  it("shows failure indicator on fetch error", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response("Internal Error", { status: 500 }),
    );
    renderWithIntl(wrap(<VersionsDiffBadge runId={SAMPLE_RUN.id} />));
    await waitFor(() => {
      expect(screen.getByText("버전 확인 실패")).toBeInTheDocument();
    });
  });
});

describe("ExportButton (RunList 내)", () => {
  let fetchSpy: MockInstance<typeof fetch>;
  // URL.createObjectURL / URL.revokeObjectURL mock — jsdom 미구현.
  let createObjectURLMock: ReturnType<typeof vi.fn>;
  let revokeObjectURLMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
    // VersionsDiffBadge 의 diff fetch — default empty.
    fetchSpy.mockResolvedValue(
      new Response(
        JSON.stringify({ snapshot_id: SAMPLE_RUN.id, diff: {} }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    createObjectURLMock = vi.fn(() => "blob:mock-url");
    revokeObjectURLMock = vi.fn();
    URL.createObjectURL = createObjectURLMock;
    URL.revokeObjectURL = revokeObjectURLMock;
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("내보내기 버튼 클릭 — exportRun fetch 호출 + Blob 생성", async () => {
    const exportWire = {
      export_format: "speculum-screen-run-export-v1",
      snapshot_schema_version: 1,
      run: {
        run_id: SAMPLE_RUN.id,
        conditions: [],
        selected_factors: [],
        as_of: SAMPLE_RUN.as_of,
        result_codes: SAMPLE_RUN.result_codes,
        result_hash: SAMPLE_RUN.result_hash,
        data_versions: {},
      },
    };
    // 첫 번째 fetch = diff (VersionsDiffBadge), 두 번째 = export.
    fetchSpy
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ snapshot_id: SAMPLE_RUN.id, diff: {} }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(exportWire), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    const user = userEvent.setup();
    renderWithIntl(wrap(<RunList runs={[SAMPLE_RUN]} />));

    const exportBtn = await screen.findByRole("button", { name: "내보내기" });
    await user.click(exportBtn);

    // createObjectURL 이 호출됐는지 확인 — Blob 다운로드 트리거 증거.
    await waitFor(() => {
      expect(createObjectURLMock).toHaveBeenCalledTimes(1);
    });
  });

  it("내보내기 fetch 실패 — 에러 텍스트 표시", async () => {
    fetchSpy
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ snapshot_id: SAMPLE_RUN.id, diff: {} }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response("not found", { status: 404 }),
      );

    const user = userEvent.setup();
    renderWithIntl(wrap(<RunList runs={[SAMPLE_RUN]} />));

    const exportBtn = await screen.findByRole("button", { name: "내보내기" });
    await user.click(exportBtn);

    await waitFor(() => {
      expect(screen.getByText("내보내기 실패")).toBeInTheDocument();
    });
  });
});
