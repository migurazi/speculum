/**
 * SecurityTypeSelector 단위 테스트 + 시각 게이트 — ADR-0023 D4 (No Advice).
 *
 * 테스트 매트릭스:
 *   (a) 기본 common 체크 렌더 — DEFAULT_SECURITY_TYPES = ["common"].
 *   (b) 선택 토글 → onChange 페이로드에 security_types 반영.
 *   (c) 동등 가시성 — 보통주가 특권적 스타일·별도 위계 클래스 없음.
 *   (d) 시각 게이트 — 등락색 클래스(text-red/text-blue 등)·추천 어휘 0.
 *   (e) 최소 1개 강제 — 유일 선택 항목 체크박스가 disabled.
 *   (f) 4개 자산군 모두 동등 체크박스로 노출.
 */

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SecurityTypeSelector } from "../SecurityTypeSelector";
import {
  DEFAULT_SECURITY_TYPES,
  SECURITY_TYPE_OPTIONS,
  type SecurityType,
} from "@/lib/api/screen";
import { renderWithIntl } from "@/test-utils/intl";

/**
 * 등락/판단 의미색 Tailwind 토큰 — 자산군 위젯 className 에 절대 등장 금지.
 * ADR-0023 D4 / ADR-0007 D2.2 grayscale 정신.
 */
const FORBIDDEN_COLOR_TOKENS: ReadonlyArray<string> = [
  "red",
  "rose",
  "blue",
  "sky",
  "indigo",
  "green",
  "emerald",
  "teal",
  "lime",
  "amber",
  "orange",
  "yellow",
  "purple",
  "violet",
  "fuchsia",
  "pink",
  "cyan",
];

/**
 * 추천·랭킹·큐레이션 어휘 — No Advice §2.2.
 * 자산군 위젯 텍스트에 이 단어가 있으면 ADR-0023 D4 위반.
 */
const FORBIDDEN_ADVISORY_WORDS: ReadonlyArray<string> = [
  "추천",
  "인기",
  "베스트",
  "Best",
  "best",
  "Top",
  "top",
  "랭킹",
  "순위",
  "1위",
];

function renderSelector(
  selected: ReadonlyArray<SecurityType> = DEFAULT_SECURITY_TYPES,
  onChange = vi.fn(),
) {
  return renderWithIntl(
    <SecurityTypeSelector selected={selected} onChange={onChange} />,
  );
}

describe("SecurityTypeSelector — (a) 기본 렌더", () => {
  it("기본값 DEFAULT_SECURITY_TYPES 는 common 만 포함한다", () => {
    expect(DEFAULT_SECURITY_TYPES).toEqual(["common"]);
  });

  it("기본 common 체크박스가 checked 상태로 렌더된다", () => {
    renderSelector();
    const commonCheckbox = screen.getByRole("checkbox", { name: "보통주" });
    expect(commonCheckbox).toBeChecked();
  });

  it("common 이외 3개 자산군 체크박스는 기본 unchecked 상태이다", () => {
    renderSelector();
    expect(screen.getByRole("checkbox", { name: "우선주" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "ETF" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "리츠" })).not.toBeChecked();
  });

  it("4개 자산군 체크박스가 모두 렌더된다", () => {
    renderSelector();
    // 각 자산군 체크박스를 개별 aria-label 로 확인.
    expect(screen.getByRole("checkbox", { name: "보통주" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "우선주" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "ETF" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "리츠" })).toBeTruthy();
    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(SECURITY_TYPE_OPTIONS.length);
  });
});

describe("SecurityTypeSelector — (b) 선택 토글 → onChange 페이로드", () => {
  it("미선택 항목 클릭 시 onChange 에 해당 type 이 추가된다", async () => {
    const onChange = vi.fn();
    renderSelector(["common"], onChange);

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "ETF" }));

    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0]?.[0] as ReadonlyArray<SecurityType>;
    expect(next).toContain("common");
    expect(next).toContain("etf");
  });

  it("선택된 항목 클릭 시 onChange 에서 해당 type 이 제거된다", async () => {
    const onChange = vi.fn();
    renderSelector(["common", "etf"], onChange);

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "ETF" }));

    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0]?.[0] as ReadonlyArray<SecurityType>;
    expect(next).toContain("common");
    expect(next).not.toContain("etf");
  });

  it("여러 자산군 선택 시 모두 onChange payload 에 포함된다", async () => {
    const onChange = vi.fn();
    renderSelector(["common", "preferred", "reit"], onChange);

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "ETF" }));

    const next = onChange.mock.calls[0]?.[0] as ReadonlyArray<SecurityType>;
    expect(next).toContain("common");
    expect(next).toContain("preferred");
    expect(next).toContain("reit");
    expect(next).toContain("etf");
  });
});

describe("SecurityTypeSelector — (c) 동등 가시성 (ADR-0023 D4)", () => {
  it("전체 선택 시 모든 자산군 label 의 className 이 동일하다", () => {
    // 4개 모두 선택 → none disabled → checked-variant 클래스 동일해야 함.
    renderSelector(["common", "preferred", "etf", "reit"]);
    const labels = screen.getAllByRole("checkbox").map(
      (cb) => cb.closest("label")?.className ?? "",
    );
    expect(labels).toHaveLength(4);
    const firstClass = labels[0];
    for (const cls of labels) {
      expect(cls).toBe(firstClass);
    }
  });

  it("보통주 label 에만 존재하는 위계 클래스(font-bold, text-lg 등)가 없다", () => {
    renderSelector(DEFAULT_SECURITY_TYPES);
    const commonLabel = screen.getByRole("checkbox", { name: "보통주" }).closest("label");
    // 특권 클래스 부재 확인.
    expect(commonLabel?.className).not.toMatch(/font-bold|text-lg|text-xl|ring-2|ring-blue/);
  });

  it("4개 자산군이 모두 같은 UI 요소(checkbox)로 렌더된다 — 보통주가 별도 섹션 아님", () => {
    renderSelector();
    const checkboxes = screen.getAllByRole("checkbox");
    // 모두 type="checkbox" — select/radio 혼용 없음.
    for (const cb of checkboxes) {
      expect((cb as HTMLInputElement).type).toBe("checkbox");
    }
    expect(checkboxes).toHaveLength(4);
  });
});

describe("SecurityTypeSelector — (d) 시각 게이트: 등락색·추천 어휘 0 (ADR-0023 D4)", () => {
  it("자산군 위젯의 모든 체크박스 label className 에 등락/판단 의미색 토큰이 없다", () => {
    renderSelector(["common", "preferred", "etf", "reit"]);
    const labels = screen.getAllByRole("checkbox").map(
      (cb) => cb.closest("label")?.className ?? "",
    );
    for (const cls of labels) {
      for (const token of FORBIDDEN_COLOR_TOKENS) {
        expect(cls).not.toMatch(new RegExp(`-${token}-`));
      }
    }
  });

  it("섹션 레이블 텍스트에 추천·랭킹·큐레이션 어휘가 없다", () => {
    renderSelector();
    // fieldset 전체 텍스트 수집.
    const fieldset = screen.getByRole("group");
    const text = fieldset.textContent ?? "";
    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text).not.toContain(word);
    }
  });

  it("자산군 레이블 텍스트에 추천·랭킹 어휘가 없다", () => {
    renderSelector();
    const labels = ["보통주", "우선주", "ETF", "리츠"];
    for (const label of labels) {
      for (const word of FORBIDDEN_ADVISORY_WORDS) {
        expect(label).not.toContain(word);
      }
    }
  });
});

describe("SecurityTypeSelector — (e) 최소 1개 강제", () => {
  it("유일 선택 항목은 체크박스가 disabled 된다", () => {
    renderSelector(["common"]);
    const commonCheckbox = screen.getByRole("checkbox", { name: "보통주" });
    // 유일 선택 → disabled 로 제거 차단.
    expect(commonCheckbox).toBeDisabled();
  });

  it("2개 이상 선택 시 각 체크박스가 enabled 된다", () => {
    renderSelector(["common", "etf"]);
    expect(screen.getByRole("checkbox", { name: "보통주" })).not.toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "ETF" })).not.toBeDisabled();
  });

  it("유일 선택 항목 클릭 시 onChange 를 호출하지 않는다", async () => {
    const onChange = vi.fn();
    renderSelector(["etf"], onChange);

    const user = userEvent.setup();
    const etfCheckbox = screen.getByRole("checkbox", { name: "ETF" });
    // disabled 체크박스는 userEvent 클릭에 반응하지 않아야 함.
    await user.click(etfCheckbox);

    expect(onChange).not.toHaveBeenCalled();
  });
});
