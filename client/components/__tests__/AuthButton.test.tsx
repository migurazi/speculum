/**
 * AuthButton 단위 테스트 — T68 Google OAuth 골격.
 *
 * 매트릭스:
 *   1. 로딩 상태 — disabled 버튼 렌더
 *   2. 미인증 상태 — "Google 로그인" 버튼 렌더
 *   3. 인증 상태 — 이메일 + "로그아웃" 버튼 렌더
 *   4. "Google 로그인" 클릭 → signIn("google") 호출
 *   5. "로그아웃" 클릭 → signOut() 호출
 */

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import { AuthButton } from "../AuthButton";
import { renderWithIntl } from "@/test-utils/intl";

// next-auth/react 모킹 — useSession / signIn / signOut.
const mockUseSession = vi.fn();
const mockSignIn = vi.fn();
const mockSignOut = vi.fn();

vi.mock("next-auth/react", () => ({
  useSession: () => mockUseSession(),
  signIn: (...args: unknown[]) => mockSignIn(...args),
  signOut: (...args: unknown[]) => mockSignOut(...args),
}));

describe("AuthButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("로딩 상태 — disabled 버튼을 렌더한다", () => {
    mockUseSession.mockReturnValue({ data: null, status: "loading" });
    renderWithIntl(<AuthButton />);
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("aria-busy", "true");
  });

  it("미인증 상태 — 'Google 로그인' 버튼을 렌더한다", () => {
    mockUseSession.mockReturnValue({ data: null, status: "unauthenticated" });
    renderWithIntl(<AuthButton />);
    expect(screen.getByText("Google 로그인")).toBeInTheDocument();
  });

  it("인증 상태 — 이메일 + '로그아웃' 버튼을 렌더한다", () => {
    mockUseSession.mockReturnValue({
      data: { user: { email: "test@example.com" } },
      status: "authenticated",
    });
    renderWithIntl(<AuthButton />);
    expect(screen.getByText("test@example.com")).toBeInTheDocument();
    expect(screen.getByText("로그아웃")).toBeInTheDocument();
  });

  it("'Google 로그인' 클릭 → signIn('google') 호출", async () => {
    mockUseSession.mockReturnValue({ data: null, status: "unauthenticated" });
    mockSignIn.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithIntl(<AuthButton />);
    await user.click(screen.getByText("Google 로그인"));
    expect(mockSignIn).toHaveBeenCalledWith("google");
  });

  it("'로그아웃' 클릭 → signOut() 호출", async () => {
    mockUseSession.mockReturnValue({
      data: { user: { email: "test@example.com" } },
      status: "authenticated",
    });
    mockSignOut.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithIntl(<AuthButton />);
    await user.click(screen.getByText("로그아웃"));
    expect(mockSignOut).toHaveBeenCalledTimes(1);
  });
});
