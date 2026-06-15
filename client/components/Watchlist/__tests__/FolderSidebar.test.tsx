/**
 * FolderSidebar 단위 테스트 — T39.
 *
 * 매트릭스:
 *   1. folder list 렌더
 *   2. default 폴더 — rename/delete 버튼 미표시
 *   3. 사용자 폴더 — rename / delete 호출
 *   4. 폴더 생성 — Enter / 버튼
 *   5. 선택 indicator
 */

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FolderSidebar } from "../FolderSidebar";
import type { WatchlistFolder } from "@/lib/api/watchlist";
import { renderWithIntl } from "@/test-utils/intl";

const DEFAULT_FOLDER: WatchlistFolder = {
  id: "f-default",
  parent_id: null,
  name: "Default",
  display_order: 0,
  is_default: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const USER_FOLDER: WatchlistFolder = {
  id: "f-1",
  parent_id: null,
  name: "코스피 대형주",
  display_order: 1,
  is_default: false,
  created_at: "2026-01-02T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

function renderSidebar(
  overrides: Partial<Parameters<typeof FolderSidebar>[0]> = {},
): {
  onSelect: ReturnType<typeof vi.fn>;
  onCreate: ReturnType<typeof vi.fn>;
  onRename: ReturnType<typeof vi.fn>;
  onDelete: ReturnType<typeof vi.fn>;
} {
  const onSelect = vi.fn();
  const onCreate = vi.fn(async () => undefined);
  const onRename = vi.fn(async () => undefined);
  const onDelete = vi.fn(async () => undefined);
  renderWithIntl(
    <FolderSidebar
      folders={[DEFAULT_FOLDER, USER_FOLDER]}
      selectedFolderId={DEFAULT_FOLDER.id}
      onSelect={onSelect}
      onCreate={onCreate}
      onRename={onRename}
      onDelete={onDelete}
      isMutating={false}
      {...overrides}
    />,
  );
  return { onSelect, onCreate, onRename, onDelete };
}

describe("FolderSidebar", () => {
  it("renders all folders with names", () => {
    renderSidebar();
    expect(screen.getByText("Default")).toBeInTheDocument();
    expect(screen.getByText("코스피 대형주")).toBeInTheDocument();
    expect(screen.getByText("(기본)")).toBeInTheDocument();
  });

  it("hides rename/delete buttons for default folder", () => {
    renderSidebar();
    expect(
      screen.queryByLabelText("Default 이름 변경"),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Default 삭제")).not.toBeInTheDocument();
  });

  it("shows rename/delete for user folder", () => {
    renderSidebar();
    const renameBtn = screen.getByLabelText("코스피 대형주 이름 변경");
    const deleteBtn = screen.getByLabelText("코스피 대형주 삭제");
    expect(renameBtn).toBeInTheDocument();
    expect(deleteBtn).toBeInTheDocument();
    // 가시 텍스트가 하드코딩이 아닌 i18n 키(renameButton/deleteButton)에서 렌더 —
    // check:i18n 게이트가 키 존재를, 본 단언이 올바른 버튼 매핑을 보증.
    expect(renameBtn).toHaveTextContent("이름");
    expect(deleteBtn).toHaveTextContent("삭제");
  });

  it("invokes onSelect when clicking folder name", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderSidebar();
    await user.click(screen.getByText("코스피 대형주"));
    expect(onSelect).toHaveBeenCalledWith("f-1");
  });

  it("invokes onDelete when user folder delete clicked", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderSidebar();
    await user.click(screen.getByLabelText("코스피 대형주 삭제"));
    expect(onDelete).toHaveBeenCalledWith("f-1");
  });

  it("creates folder on Enter key", async () => {
    const user = userEvent.setup();
    const { onCreate } = renderSidebar();
    const input = screen.getByLabelText("새 폴더 이름");
    await user.type(input, "신규폴더{Enter}");
    expect(onCreate).toHaveBeenCalledWith("신규폴더");
  });

  it("creates folder on button click", async () => {
    const user = userEvent.setup();
    const { onCreate } = renderSidebar();
    await user.type(screen.getByLabelText("새 폴더 이름"), "테스트");
    await user.click(screen.getByText("폴더 추가"));
    expect(onCreate).toHaveBeenCalledWith("테스트");
  });

  it("does not create folder on empty name", async () => {
    const user = userEvent.setup();
    const { onCreate } = renderSidebar();
    const btn = screen.getByText("폴더 추가");
    expect(btn).toBeDisabled();
    // Enter on empty input — onCreate 미호출.
    await user.click(screen.getByLabelText("새 폴더 이름"));
    await user.keyboard("{Enter}");
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("disables controls when isMutating=true", () => {
    renderSidebar({ isMutating: true });
    expect(screen.getByLabelText("새 폴더 이름")).toBeDisabled();
    expect(screen.getByText("폴더 추가")).toBeDisabled();
  });
});
