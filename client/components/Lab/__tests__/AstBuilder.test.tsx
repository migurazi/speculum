/**
 * AstBuilder 렌더 테스트 — op 드롭다운에 허용 op 만 노출(No Advice, ADR-0022 D1).
 *
 * 핵심: rank/top_n/sign 등 서수·신호 op 가 선택지에 절대 없어야 한다. 빌더가
 * ALLOWED_OPS 단일 출처로만 op 옵션을 생성하므로 이를 렌더로 확인한다.
 */

import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ALLOWED_OPS } from "@/lib/factor/ast";
import { renderWithIntl } from "@/test-utils/intl";

import { AstBuilder } from "../AstBuilder";

describe("AstBuilder — op 선택지 (No Advice)", () => {
  it("op 노드 선택 시 허용 op 12종을 모두 옵션으로 노출", () => {
    renderWithIntl(
      <AstBuilder expr={{ op: "add", left: { const: 0 }, right: { const: 0 } }} onChange={vi.fn()} />,
    );

    for (const op of ALLOWED_OPS) {
      expect(screen.getByRole("option", { name: op })).toBeInTheDocument();
    }
  });

  it("금지 서수·신호 op 가 옵션에 부재 — rank/top_n/bottom_n/sign", () => {
    renderWithIntl(
      <AstBuilder expr={{ op: "add", left: { const: 0 }, right: { const: 0 } }} onChange={vi.fn()} />,
    );

    for (const op of ["rank", "top_n", "bottom_n", "sign", "step"]) {
      expect(screen.queryByRole("option", { name: op })).not.toBeInTheDocument();
    }
  });

  it("weighted_sum 선택 시 가중치 입력 노출 (Fidelity)", () => {
    renderWithIntl(
      <AstBuilder
        expr={{ op: "weighted_sum", args: [{ field: "a" }], weights: [0.5] }}
        onChange={vi.fn()}
      />,
    );
    // 항 1 가중치 입력
    expect(
      screen.getByLabelText("항 1 가중치"),
    ).toBeInTheDocument();
  });

  it("winsorize 선택 시 하한/상한 경계 입력 노출 (Fidelity)", () => {
    renderWithIntl(
      <AstBuilder
        expr={{ op: "winsorize", args: [{ field: "x" }], lower: 0, upper: 100 }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("하한 경계")).toBeInTheDocument();
    expect(screen.getByLabelText("상한 경계")).toBeInTheDocument();
  });
});
