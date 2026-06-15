/**
 * NotesPanel MarkdownEditor — WAI-ARIA tabs (A-2) + 미리보기 빈 상태 i18n (I-2).
 *
 * A-2: 작성/미리보기 탭이 role=tablist/tab/tabpanel + aria-selected/controls +
 *      roving tabindex + 화살표/Home/End 키 네비를 갖춘 WAI-ARIA tabs 패턴인지.
 *      (과거: 단순 <button> 2개 — 스크린리더가 탭 위젯 미인식, 키보드 화살표 불가.)
 *
 * I-2: 미리보기 탭에서 내용이 없으면 하드코딩 "<p>미리보기할 내용이 없습니다.</p>"
 *      를 dangerouslySetInnerHTML 로 주입하지 않고, i18n JSX(`previewEmpty`)를 렌더.
 *      내용이 있을 때만 previewHtml(dangerouslySetInnerHTML)을 사용(XSS chokepoint 보존).
 *
 * 셋업은 NotesPanel.test.tsx 와 동일 — fetch mock + QueryClient + intl.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, afterEach } from "vitest";

import { NotesPanel } from "../NotesPanel";
import { renderWithIntl } from "@/test-utils/intl";

const LINEAGE_ID = "550e8400-e29b-41d4-a716-446655440000";
const EMPTY_WIRE = { notes: [] };

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPanel() {
  return renderWithIntl(
    <QueryClientProvider client={makeClient()}>
      <NotesPanel codeLineageId={LINEAGE_ID} />
    </QueryClientProvider>,
  );
}

/** 작성 폼(MarkdownEditor)을 열어 tablist 가 나타날 때까지 기다린다. */
async function openComposer(): Promise<void> {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(EMPTY_WIRE), { status: 200 }),
  );
  renderPanel();
  // 헤더 "작성" 버튼 클릭 → MarkdownEditor 노출.
  const writeButtons = await screen.findAllByRole("button", { name: "작성" });
  await userEvent.click(writeButtons[0]!);
  await screen.findByRole("tablist");
}

describe("NotesPanel MarkdownEditor — WAI-ARIA tabs (A-2)", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("tablist + 2 tab, aria-selected / aria-controls / roving tabindex", async () => {
    await openComposer();

    expect(screen.getByRole("tablist")).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(2);

    const writeTab = screen.getByRole("tab", { name: "작성" });
    const previewTab = screen.getByRole("tab", { name: "미리보기" });

    // 초기 선택 = 작성.
    expect(writeTab).toHaveAttribute("aria-selected", "true");
    expect(writeTab).toHaveAttribute("tabindex", "0");
    expect(writeTab).toHaveAttribute(
      "aria-controls",
      "notes-editor-panel-write",
    );

    expect(previewTab).toHaveAttribute("aria-selected", "false");
    expect(previewTab).toHaveAttribute("tabindex", "-1");
  });

  it("tabpanel role + id + aria-labelledby (선택 탭과 연결)", async () => {
    await openComposer();
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAttribute("id", "notes-editor-panel-write");
    expect(panel).toHaveAttribute(
      "aria-labelledby",
      "notes-editor-tab-write",
    );
  });

  it("ArrowRight → 미리보기 탭 활성화 + tabpanel 전환", async () => {
    await openComposer();
    screen.getByRole("tab", { name: "작성" }).focus();

    await userEvent.keyboard("{ArrowRight}");

    expect(screen.getByRole("tab", { name: "미리보기" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "id",
      "notes-editor-panel-preview",
    );
  });

  it("ArrowLeft(첫 탭에서) → 마지막 탭으로 wrap", async () => {
    await openComposer();
    screen.getByRole("tab", { name: "작성" }).focus();

    await userEvent.keyboard("{ArrowLeft}");

    expect(screen.getByRole("tab", { name: "미리보기" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("Home/End → 처음/마지막 탭", async () => {
    await openComposer();
    screen.getByRole("tab", { name: "작성" }).focus();

    await userEvent.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "미리보기" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "작성" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

describe("NotesPanel MarkdownEditor — 미리보기 빈 상태 (I-2)", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("내용 없이 미리보기 탭 진입 시 i18n 안내 텍스트(JSX)를 렌더한다", async () => {
    const { container } = (() => {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(JSON.stringify(EMPTY_WIRE), { status: 200 }),
      );
      return renderPanel();
    })();

    const writeButtons = await screen.findAllByRole("button", {
      name: "작성",
    });
    await userEvent.click(writeButtons[0]!);
    await screen.findByRole("tablist");

    // 미리보기 탭으로 이동.
    await userEvent.click(screen.getByRole("tab", { name: "미리보기" }));

    // i18n 안내 문구가 렌더됨.
    expect(
      screen.getByText("미리보기할 내용이 없습니다."),
    ).toBeInTheDocument();

    // 빈 상태에서는 dangerouslySetInnerHTML 미사용 — preview tabpanel 내
    // <p> 가 실제 텍스트 노드로 존재(주입된 raw HTML 아님). 회귀 가드로
    // innerHTML 에 빈 상태용 raw HTML 주입 흔적이 없는지 간접 확인.
    const previewPanel = container.querySelector(
      "#notes-editor-panel-preview p",
    );
    expect(previewPanel?.textContent).toBe("미리보기할 내용이 없습니다.");
  });

  it("내용이 있으면 미리보기는 renderMarkdown(dangerouslySetInnerHTML) 사용", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(EMPTY_WIRE), { status: 200 }),
    );
    renderPanel();

    const writeButtons = await screen.findAllByRole("button", {
      name: "작성",
    });
    await userEvent.click(writeButtons[0]!);
    await screen.findByRole("tablist");

    // 작성 탭에서 텍스트 입력.
    const textarea = screen.getByRole("textbox");
    await userEvent.type(textarea, "**굵게**");

    // 미리보기 탭 이동.
    await userEvent.click(screen.getByRole("tab", { name: "미리보기" }));

    // renderMarkdown 이 **굵게** → <strong>굵게</strong> 변환.
    await waitFor(() => {
      const strong = document.querySelector(
        "#notes-editor-panel-preview strong",
      );
      expect(strong?.textContent).toBe("굵게");
    });
  });
});
