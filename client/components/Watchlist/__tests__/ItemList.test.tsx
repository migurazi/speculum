/**
 * ItemList 단위 테스트 — T39.
 *
 * 매트릭스:
 *   1. items 렌더 (lineage id + note)
 *   2. 빈 폴더 placeholder
 *   3. add — code 검증 + onAdd
 *   4. 잘못된 code (1~6 자리 numeric 아님) → onAdd 미호출
 *   5. addError 표시
 *   6. note edit 시작 → 저장
 *   7. delete 호출
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ItemList } from "../ItemList";
import type {
  WatchlistFolder,
  WatchlistItem,
} from "@/lib/api/watchlist";

const FOLDER: WatchlistFolder = {
  id: "f-1",
  parent_id: null,
  name: "내 폴더",
  display_order: 0,
  is_default: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const ITEM1: WatchlistItem = {
  id: "i-1",
  watchlist_id: FOLDER.id,
  code_lineage_id: "11111111-1111-1111-1111-111111111111",
  note: "관심 종목",
  display_order: 0,
  added_at: "2026-01-02T00:00:00Z",
};

const ITEM2: WatchlistItem = {
  id: "i-2",
  watchlist_id: FOLDER.id,
  code_lineage_id: "22222222-2222-2222-2222-222222222222",
  note: null,
  display_order: 1,
  added_at: "2026-01-03T00:00:00Z",
};

function renderList(
  overrides: Partial<Parameters<typeof ItemList>[0]> = {},
): {
  onAdd: ReturnType<typeof vi.fn>;
  onUpdateNote: ReturnType<typeof vi.fn>;
  onRemove: ReturnType<typeof vi.fn>;
} {
  const onAdd = vi.fn(async () => undefined);
  const onUpdateNote = vi.fn(async () => undefined);
  const onRemove = vi.fn(async () => undefined);
  render(
    <ItemList
      folder={FOLDER}
      items={[ITEM1, ITEM2]}
      onAdd={onAdd}
      onUpdateNote={onUpdateNote}
      onRemove={onRemove}
      isMutating={false}
      addError={null}
      {...overrides}
    />,
  );
  return { onAdd, onUpdateNote, onRemove };
}

describe("ItemList", () => {
  it("renders items with lineage id + note", () => {
    renderList();
    expect(screen.getByText(/lineage: 11111111/)).toBeInTheDocument();
    expect(screen.getByText("관심 종목")).toBeInTheDocument();
    expect(screen.getByText("메모 없음")).toBeInTheDocument();
  });

  it("renders placeholder for empty folder", () => {
    renderList({ items: [] });
    expect(
      screen.getByText("이 폴더에는 아직 종목이 없습니다."),
    ).toBeInTheDocument();
    expect(screen.getByText(/0 종목/)).toBeInTheDocument();
  });

  it("invokes onAdd with valid code + trimmed note", async () => {
    const user = userEvent.setup();
    const { onAdd } = renderList();
    await user.type(screen.getByLabelText("종목코드"), "005930");
    await user.type(screen.getByLabelText("메모"), "  대장주  ");
    await user.click(screen.getByText("추가"));
    expect(onAdd).toHaveBeenCalledWith("005930", "대장주");
  });

  it("invokes onAdd with null note when note empty", async () => {
    const user = userEvent.setup();
    const { onAdd } = renderList();
    await user.type(screen.getByLabelText("종목코드"), "005930");
    await user.click(screen.getByText("추가"));
    expect(onAdd).toHaveBeenCalledWith("005930", null);
  });

  it("does not call onAdd for non-numeric code", async () => {
    const user = userEvent.setup();
    const { onAdd } = renderList();
    await user.type(screen.getByLabelText("종목코드"), "abc");
    await user.click(screen.getByText("추가"));
    expect(onAdd).not.toHaveBeenCalled();
    expect(
      screen.getByText("종목코드는 1~6 자리 숫자여야 합니다."),
    ).toBeInTheDocument();
  });

  it("does not call onAdd for too-long code", async () => {
    const user = userEvent.setup();
    const { onAdd } = renderList();
    await user.type(screen.getByLabelText("종목코드"), "1234567");
    await user.click(screen.getByText("추가"));
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("renders addError as alert", () => {
    renderList({ addError: "종목코드 \"999999\" 를 찾을 수 없습니다." });
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("999999");
  });

  it("edits note — startEdit shows input, save invokes onUpdateNote", async () => {
    const user = userEvent.setup();
    const { onUpdateNote } = renderList();
    // 첫 item 의 메모 수정 (ITEM1.note = "관심 종목").
    const editBtns = screen.getAllByText("메모 수정");
    await user.click(editBtns[0]!);
    const editInput = screen.getByLabelText("메모 변경");
    await user.clear(editInput);
    await user.type(editInput, "신규 메모");
    await user.click(screen.getByText("저장"));
    expect(onUpdateNote).toHaveBeenCalledWith("i-1", "신규 메모");
  });

  it("note edit with empty value calls onUpdateNote with null", async () => {
    const user = userEvent.setup();
    const { onUpdateNote } = renderList();
    await user.click(screen.getAllByText("메모 수정")[0]!);
    const editInput = screen.getByLabelText("메모 변경");
    await user.clear(editInput);
    await user.click(screen.getByText("저장"));
    expect(onUpdateNote).toHaveBeenCalledWith("i-1", null);
  });

  it("invokes onRemove on delete click", async () => {
    const user = userEvent.setup();
    const { onRemove } = renderList();
    const deleteBtns = screen.getAllByText("삭제");
    await user.click(deleteBtns[0]!);
    expect(onRemove).toHaveBeenCalledWith("i-1");
  });
});
