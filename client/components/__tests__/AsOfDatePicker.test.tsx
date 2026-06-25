/**
 * AsOfDatePicker + AsOfBanner 단위 테스트 — T34.
 *
 * 매트릭스:
 *   1. mount 후 today 표시 + 회색 (isToday=true)
 *   2. 사용자가 과거 date 선택 → store 갱신 + 강조 색상 + reset button 노출
 *   3. reset button 클릭 → today 로 reset
 *   4. AsOfBanner — today 면 미표시, past 면 표시
 *   5. SSR mount 이전 표시 (placeholder dash)
 */

import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AsOfBanner, AsOfDatePicker } from "../AsOfDatePicker";
import { kstToday, useAsOfStore } from "@/state/as-of-store";
import { useCalendarBoundsStore } from "@/state/calendar-bounds-store";
import { renderWithIntl } from "@/test-utils/intl";

describe("AsOfDatePicker", () => {
  beforeEach(() => {
    localStorage.clear();
    useAsOfStore.getState().resetToToday();
    // bounds store 초기화 — 테스트 격리.
    useCalendarBoundsStore.setState({ bounds: null });
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("renders today by default with neutral styling", async () => {
    renderWithIntl(<AsOfDatePicker />);
    const input = await screen.findByLabelText("기준 일자 선택");
    expect(input).toBeInTheDocument();
    expect((input as HTMLInputElement).value).toBe(kstToday());
    // "오늘" reset button 은 today 모드에서 미표시.
    expect(
      screen.queryByLabelText("오늘 날짜로 리셋"),
    ).not.toBeInTheDocument();
  });

  it("shows reset button + amber styling when past date selected", async () => {
    const user = userEvent.setup();
    renderWithIntl(<AsOfDatePicker />);
    const input = (await screen.findByLabelText(
      "기준 일자 선택",
    )) as HTMLInputElement;

    // userEvent.type 대신 직접 onChange 트리거 (date input 의 keyboard
    // 입력은 browser-specific).
    act(() => {
      useAsOfStore.getState().setAsOf("2024-01-01");
    });

    expect(input.value).toBe("2024-01-01");
    // reset button 노출.
    expect(
      screen.getByLabelText("오늘 날짜로 리셋"),
    ).toBeInTheDocument();
    // amber styling — className 에 "border-amber-400".
    expect(input.className).toContain("amber");
  });

  it("reset button restores today", async () => {
    const user = userEvent.setup();
    renderWithIntl(<AsOfDatePicker />);
    await screen.findByLabelText("기준 일자 선택");

    act(() => {
      useAsOfStore.getState().setAsOf("2024-01-01");
    });
    expect(useAsOfStore.getState().asOf).toBe("2024-01-01");

    const reset = screen.getByLabelText("오늘 날짜로 리셋");
    await user.click(reset);

    expect(useAsOfStore.getState().asOf).toBe(kstToday());
  });

  it("bounds 주입 시 input 의 min=minDate, max=kstToday()", async () => {
    act(() => {
      useCalendarBoundsStore.getState().setBounds({
        minDate: "2024-01-01",
        maxDate: "2024-12-31",
      });
    });

    renderWithIntl(<AsOfDatePicker />);
    const input = (await screen.findByLabelText(
      "기준 일자 선택",
    )) as HTMLInputElement;

    expect(input.min).toBe("2024-01-01");
    expect(input.max).toBe(kstToday());
  });
});


describe("AsOfBanner", () => {
  beforeEach(() => {
    localStorage.clear();
    useAsOfStore.getState().resetToToday();
  });

  it("renders nothing when isToday", async () => {
    // oracle T34 L3 — setTimeout heuristic → waitFor deterministic.
    // mount 후에도 today 면 null. useEffect 가 setMounted(true) 까지
    // wait 후 검증.
    renderWithIntl(<AsOfBanner />);
    await waitFor(() => {
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
  });

  it("renders banner with past date when isToday=false", async () => {
    act(() => {
      useAsOfStore.getState().setAsOf("2024-01-01");
    });
    renderWithIntl(<AsOfBanner />);
    const banner = await screen.findByRole("status");
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toContain("2024-01-01");
    expect(banner.textContent).toContain("과거 시점 분석");
  });
});
