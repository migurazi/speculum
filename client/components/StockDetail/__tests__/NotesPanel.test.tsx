/**
 * NotesPanel 단위 테스트 — T76 XSS 방어 + 기본 렌더 (chart-visual-gate 패밀리).
 *
 * 검증:
 *   1. 로딩 상태 — 스켈레톤 렌더.
 *   2. 메모 목록 렌더 — body 가 DOM 에 표시.
 *   3. 빈 목록 — 안내 메시지.
 *   4. XSS 방어 — <script> 태그가 DOM 에 script/onerror 없이 표시.
 *   5. XSS 방어 — <img onerror=...> 가 DOM 에 없음.
 *   6. dangerouslySetInnerHTML 이 renderMarkdown 경유 — 악성 입력이 무력화.
 *   7. 중립 톤 — 등락 판단색(text-red/text-blue/text-green) className 부재.
 *   8. 에러 상태 — 에러 메시지 렌더.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, afterEach } from "vitest";

import { NotesPanel } from "../NotesPanel";
import { renderWithIntl } from "@/test-utils/intl";

const LINEAGE_ID = "550e8400-e29b-41d4-a716-446655440000";

/** 정상 메모 목록 wire 응답 */
const NOTES_WIRE = {
  notes: [
    {
      id: "note-001",
      code_lineage_id: LINEAGE_ID,
      body: "**분석 노트**: 주요 지표 확인 완료.",
      scope: "user",
      created_at: "2026-05-01T09:00:00Z",
      updated_at: "2026-05-01T09:00:00Z",
    },
  ],
};

/** XSS 악성 입력을 body 로 가진 메모 */
const XSS_NOTES_WIRE = {
  notes: [
    {
      id: "note-xss",
      code_lineage_id: LINEAGE_ID,
      body: '<script>alert("xss")</script><img src=x onerror=alert(1)>',
      scope: "user",
      created_at: "2026-05-01T09:00:00Z",
      updated_at: "2026-05-01T09:00:00Z",
    },
  ],
};

/** 빈 메모 목록 */
const EMPTY_WIRE = { notes: [] };

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPanel(ui: React.ReactNode) {
  return renderWithIntl(
    <QueryClientProvider client={makeClient()}>{ui}</QueryClientProvider>,
  );
}

describe("NotesPanel", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("로딩 중 스켈레톤 플레이스홀더를 렌더한다", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));

    const { container } = renderPanel(
      <NotesPanel codeLineageId={LINEAGE_ID} />,
    );

    const skeleton = container.querySelector(".animate-pulse");
    expect(skeleton).toBeInTheDocument();
  });

  it("메모 목록을 렌더한다 — body 가 DOM 에 표시", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(NOTES_WIRE), { status: 200 }),
    );

    renderPanel(<NotesPanel codeLineageId={LINEAGE_ID} />);

    await waitFor(() => {
      // **분석 노트** 는 renderMarkdown 후 <strong> 안에 있음 — textContent 로 검색.
      expect(screen.getByText(/분석 노트/)).toBeInTheDocument();
    });
  });

  it("빈 메모 목록이면 안내 메시지를 렌더한다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(EMPTY_WIRE), { status: 200 }),
    );

    renderPanel(<NotesPanel codeLineageId={LINEAGE_ID} />);

    await waitFor(() => {
      expect(screen.getByText("메모가 없습니다.")).toBeInTheDocument();
    });
  });

  it("fetch 실패 시 에러 메시지를 렌더한다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("서버 오류", { status: 500 }),
    );

    renderPanel(<NotesPanel codeLineageId={LINEAGE_ID} />);

    await waitFor(() => {
      expect(screen.getByText(/메모 로드 실패/)).toBeInTheDocument();
    });
  });

  it("XSS 방어 — <script> 태그가 DOM 에 렌더되지 않는다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(XSS_NOTES_WIRE), { status: 200 }),
    );

    const { container } = renderPanel(
      <NotesPanel codeLineageId={LINEAGE_ID} />,
    );

    await waitFor(() => {
      // note 가 렌더됨을 확인하기 위해 편집 버튼 등 확인.
      expect(screen.getByText("편집")).toBeInTheDocument();
    });

    // DOM 에 <script> 태그 없음.
    const scriptTags = container.querySelectorAll("script");
    expect(scriptTags.length).toBe(0);

    // onerror 속성 없음.
    expect(container.innerHTML).not.toContain("onerror");
  });

  it("XSS 방어 — <img> 태그가 DOM 에 없다 (onerror XSS 벡터 차단)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(XSS_NOTES_WIRE), { status: 200 }),
    );

    const { container } = renderPanel(
      <NotesPanel codeLineageId={LINEAGE_ID} />,
    );

    await waitFor(() => {
      expect(screen.getByText("편집")).toBeInTheDocument();
    });

    // img 태그 없음.
    const imgTags = container.querySelectorAll("img");
    expect(imgTags.length).toBe(0);
  });

  it("중립 톤 — 등락 판단색 className 부재 (text-red/text-blue/text-green)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(NOTES_WIRE), { status: 200 }),
    );

    const { container } = renderPanel(
      <NotesPanel codeLineageId={LINEAGE_ID} />,
    );

    await waitFor(() => {
      expect(screen.getByText(/분석 노트/)).toBeInTheDocument();
    });

    const html = container.innerHTML;
    expect(html).not.toMatch(/text-red-\d{3}/);
    expect(html).not.toMatch(/text-blue-\d{3}/);
    expect(html).not.toMatch(/text-green-\d{3}/);
    expect(html).not.toMatch(/bg-red-\d{3}/);
    expect(html).not.toMatch(/bg-blue-\d{3}/);
    expect(html).not.toMatch(/bg-green-\d{3}/);
  });
});
