/**
 * PublisherClaim — publisher handle claim UI 테스트 (ADR-0034 D1/D4).
 *
 * 검증:
 *   1. claim mutation 성공 → 인증 publisher 표시 + namespace 안내 렌더.
 *   2. 409 에러 → 중복 메시지 표시(grayscale 에러 문구).
 *   3. 422 에러 → 예약 handle 메시지 표시(grayscale 에러 문구).
 *   4. 판단색(red/green/amber 등) className 없음(ADR-0028 D3 neutral 정책).
 *   5. 표시 텍스트에 금지 어휘(권위/추천/인기/순위) 없음.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { ApiError } from "@/lib/api/client";
import { claimPublisher } from "@/lib/api/publishers";
import { renderWithIntl } from "@/test-utils/intl";

import { PublisherClaim } from "../PublisherClaim";

// claimPublisher 만 mock — 실제 API 호출 차단.
vi.mock("@/lib/api/publishers", () => ({
  claimPublisher: vi.fn(),
}));

/** 금지 의미색 토큰 — backtest-visual-gate.test.tsx FORBIDDEN_SEMANTIC_COLOR_TOKENS 와 동일. */
const FORBIDDEN_SEMANTIC_COLOR_TOKENS: ReadonlyArray<string> = [
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

/** 권위/추천/순위 금지 어휘. */
const FORBIDDEN_ADVISORY_WORDS: ReadonlyArray<string> = [
  "추천",
  "인기",
  "1위",
  "랭킹",
  "Top",
  "우수",
  "최고",
  "공인",
  "공식",
  "보증",
  "권위",
];

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderClaim(): void {
  renderWithIntl(
    <QueryClientProvider client={makeClient()}>
      <PublisherClaim />
    </QueryClientProvider>,
  );
}

describe("PublisherClaim — claim mutation (ADR-0034 D1/D4)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("성공 시 인증된 publisher 표시 + namespace 안내가 렌더된다", async () => {
    vi.mocked(claimPublisher).mockResolvedValue({
      handle: "my-handle",
      createdAt: "2026-06-05T00:00:00Z",
    });

    renderClaim();

    const input = screen.getByTestId("publisher-claim-input");
    await userEvent.type(input, "my-handle");
    await userEvent.click(screen.getByTestId("publisher-claim-button"));

    const success = await screen.findByTestId("publisher-claim-success");
    expect(success.textContent).toContain("@my-handle");
    // namespace 안내 포함
    expect(success.textContent).toContain("@my-handle");
  });

  it("409 에러 → 중복 에러 메시지를 표시한다", async () => {
    vi.mocked(claimPublisher).mockRejectedValue(
      new ApiError(409, "API 409 Conflict", "{}"),
    );

    renderClaim();

    await userEvent.type(screen.getByTestId("publisher-claim-input"), "taken");
    await userEvent.click(screen.getByTestId("publisher-claim-button"));

    const errEl = await screen.findByTestId("publisher-claim-error");
    expect(errEl.textContent).toContain("이미 등록된");
  });

  it("422 에러 → 예약 handle 에러 메시지를 표시한다", async () => {
    vi.mocked(claimPublisher).mockRejectedValue(
      new ApiError(422, "API 422 Unprocessable", "{}"),
    );

    renderClaim();

    await userEvent.type(
      screen.getByTestId("publisher-claim-input"),
      "community",
    );
    await userEvent.click(screen.getByTestId("publisher-claim-button"));

    const errEl = await screen.findByTestId("publisher-claim-error");
    expect(errEl.textContent).toContain("예약된 handle");
  });
});

describe("PublisherClaim — grayscale 톤 게이트 (ADR-0028 D3)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("버튼·입력·에러 엘리먼트 className 에 판단 의미색이 없다(초기 렌더)", () => {
    renderClaim();

    // 버튼
    const btn = screen.getByTestId("publisher-claim-button");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(btn.className, `버튼에 금지 색 '${token}'`).not.toMatch(
        new RegExp(`-${token}-`),
      );
    }

    // 입력
    const input = screen.getByTestId("publisher-claim-input");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(input.className, `입력에 금지 색 '${token}'`).not.toMatch(
        new RegExp(`-${token}-`),
      );
    }
  });

  it("에러 메시지 className 에 판단 의미색이 없다 (409)", async () => {
    vi.mocked(claimPublisher).mockRejectedValue(
      new ApiError(409, "API 409 Conflict", "{}"),
    );

    renderClaim();

    await userEvent.type(screen.getByTestId("publisher-claim-input"), "taken");
    await userEvent.click(screen.getByTestId("publisher-claim-button"));

    const errEl = await screen.findByTestId("publisher-claim-error");
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(errEl.className, `에러에 금지 색 '${token}'`).not.toMatch(
        new RegExp(`-${token}-`),
      );
    }
  });
});

describe("PublisherClaim — 금지 어휘 게이트", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("성공 표시 텍스트에 권위/추천/순위 금지 어휘가 없다", async () => {
    vi.mocked(claimPublisher).mockResolvedValue({
      handle: "my-handle",
      createdAt: "2026-06-05T00:00:00Z",
    });

    renderClaim();

    await userEvent.type(
      screen.getByTestId("publisher-claim-input"),
      "my-handle",
    );
    await userEvent.click(screen.getByTestId("publisher-claim-button"));

    const success = await screen.findByTestId("publisher-claim-success");
    const text = success.textContent ?? "";

    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text, `성공 표시에 금지 어휘 '${word}'`).not.toContain(word);
    }
  });
});
