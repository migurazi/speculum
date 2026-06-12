/**
 * ValidationPanel 렌더 테스트 — valid / invalid(issues) / pending / error / idle.
 *
 * 검증 결과를 중립 톤으로 표시하는지 + issue stage/message 가 나오는지 확인.
 */

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithIntl } from "@/test-utils/intl";

import { ValidationPanel } from "../ValidationPanel";

describe("ValidationPanel", () => {
  it("idle — 결과 없음 안내", () => {
    renderWithIntl(<ValidationPanel result={null} isPending={false} error={null} />);
    expect(
      screen.getByText("pack 을 편집하면 자동으로 검증합니다."),
    ).toBeInTheDocument();
  });

  it("pending — 검증 중 표시", () => {
    renderWithIntl(<ValidationPanel result={null} isPending={true} error={null} />);
    expect(screen.getByText("검증 중...")).toBeInTheDocument();
  });

  it("valid — 통과 메시지", () => {
    renderWithIntl(
      <ValidationPanel
        result={{ valid: true, issues: [] }}
        isPending={false}
        error={null}
      />,
    );
    expect(screen.getByText("검증을 통과했습니다.")).toBeInTheDocument();
  });

  it("invalid — stage + message issue 목록", () => {
    renderWithIntl(
      <ValidationPanel
        result={{
          valid: false,
          issues: [
            { stage: "schema", message: "canonical_id 가 비어 있습니다" },
            { stage: "acyclic", message: "순환 참조가 있습니다" },
          ],
        }}
        isPending={false}
        error={null}
      />,
    );
    expect(screen.getByText("schema")).toBeInTheDocument();
    expect(screen.getByText("canonical_id 가 비어 있습니다")).toBeInTheDocument();
    expect(screen.getByText("acyclic")).toBeInTheDocument();
    expect(screen.getByText("순환 참조가 있습니다")).toBeInTheDocument();
  });

  it("request error — 요청 실패 안내", () => {
    renderWithIntl(
      <ValidationPanel
        result={null}
        isPending={false}
        error="Network down"
      />,
    );
    expect(screen.getByText(/검증 요청 실패: Network down/)).toBeInTheDocument();
  });
});
