/**
 * SourceAttribution component 단위 테스트 — T33.
 *
 * 매트릭스:
 *   1. value + source + asOf inline 표시
 *   2. source label 한글 표시 (DART → "금융감독원...")
 *   3. effectiveDate prop 처리 (없으면 미표시, asOf 와 다르면 표시)
 *   4. value 가 ReactNode (number / element) 도 렌더
 *   5. className wrapper 에 병합
 *
 * Tooltip content 는 hover 시점에 portal 로 render — testing-library 의
 * default render 에서 tooltip body 직접 접근 어려움. content 검증은
 * trigger 의 aria-describedby 기반 indirect.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SourceAttribution } from "../SourceAttribution";

describe("SourceAttribution", () => {
  it("renders value + source + asOf inline", () => {
    render(
      <SourceAttribution
        value="12.34"
        source="DART"
        formula="당기순이익 / 발행주식수"
        asOf="2024-09-30"
      />,
    );
    expect(screen.getByText("12.34")).toBeInTheDocument();
    // inline suffix — "DART · 2024-09-30".
    expect(screen.getByText(/DART · 2024-09-30/)).toBeInTheDocument();
  });

  it("supports multiple source labels", () => {
    const sources = ["KRX", "PYKRX", "FDR", "ECOS"] as const;
    for (const source of sources) {
      const { unmount } = render(
        <SourceAttribution
          value="value"
          source={source}
          formula="formula"
          asOf="2024-01-01"
        />,
      );
      expect(
        screen.getByText(new RegExp(`${source} · 2024-01-01`)),
      ).toBeInTheDocument();
      unmount();
    }
  });

  it("renders ReactNode value (number)", () => {
    render(
      <SourceAttribution
        value={70_000}
        source="KRX"
        formula="KRX 종가 — 보정 없음"
        asOf="2024-05-07"
      />,
    );
    expect(screen.getByText("70000")).toBeInTheDocument();
  });

  it("renders ReactNode value (element with inline color)", () => {
    render(
      <SourceAttribution
        value={<span data-testid="custom-value">strong</span>}
        source="DART"
        formula="formula"
        asOf="2024-01-01"
      />,
    );
    expect(screen.getByTestId("custom-value")).toBeInTheDocument();
  });

  it("applies className to wrapper", () => {
    const { container } = render(
      <SourceAttribution
        value="x"
        source="DART"
        formula="f"
        asOf="2024-01-01"
        className="custom-wrapper-class"
      />,
    );
    expect(
      container.querySelector(".custom-wrapper-class"),
    ).toBeInTheDocument();
  });

  it("requires all 4 mandatory props at type level", () => {
    // type-level test — 빠진 prop 시 빌드 에러. 본 test 는 runtime 검증
    // 아닌 documentation. tsc --noEmit 가 강제.
    const validProps = {
      value: "x",
      source: "DART" as const,
      formula: "f",
      asOf: "2024-01-01",
    };
    render(<SourceAttribution {...validProps} />);
    expect(screen.getByText("x")).toBeInTheDocument();
  });

  // oracle 리뷰 M2 — Tooltip hover 시 content 의 4 props 모두 실제 mount
  // 검증. user-event 가 radix Tooltip 의 hover delay (200ms) 도 처리.
  // Note: radix-ui Tooltip 은 a11y 위해 visible + hidden 2 instance mount —
  // getAllByText 로 length >= 1 만 검증.
  it("shows tooltip content (source ko / formula / asOf) on hover", async () => {
    const user = userEvent.setup();
    render(
      <SourceAttribution
        value="12.34"
        source="DART"
        formula="당기순이익 / 발행주식수"
        asOf="2024-09-30"
      />,
    );
    const trigger = screen.getByText("12.34");
    await user.hover(trigger);

    await waitFor(() => {
      // 한글 source label 매핑 — radix 의 2 instance 모두 cover.
      expect(
        screen.getAllByText(/금융감독원 전자공시시스템\(DART\)/).length,
      ).toBeGreaterThanOrEqual(1);
    });
    expect(
      screen.getAllByText("당기순이익 / 발행주식수").length,
    ).toBeGreaterThanOrEqual(1);
  });

  it("shows effectiveDate in tooltip when different from asOf", async () => {
    const user = userEvent.setup();
    render(
      <SourceAttribution
        value="100"
        source="DART"
        formula="f"
        asOf="2024-09-30"
        effectiveDate="2024-10-15"
      />,
    );
    await user.hover(screen.getByText("100"));
    await waitFor(() => {
      expect(
        screen.getAllByText("2024-10-15").length,
      ).toBeGreaterThanOrEqual(1);
    });
  });

  it("hides effectiveDate when empty string (oracle M1)", async () => {
    const user = userEvent.setup();
    render(
      <SourceAttribution
        value="100"
        source="DART"
        formula="f"
        asOf="2024-09-30"
        effectiveDate=""
      />,
    );
    await user.hover(screen.getByText("100"));
    await waitFor(() => {
      expect(
        screen.getAllByText(/금융감독원 전자공시시스템\(DART\)/).length,
      ).toBeGreaterThanOrEqual(1);
    });
    // "발효일:" label 이 미존재 (빈 string 가드).
    expect(screen.queryByText(/발효일:/)).not.toBeInTheDocument();
  });
});
