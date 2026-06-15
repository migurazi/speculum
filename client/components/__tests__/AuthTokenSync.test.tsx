/**
 * AuthTokenSync — useSession 의 accessToken 을 fetchJson(setAuthToken)에 주입.
 *
 * lib/next-auth.d.ts 로 Session.accessToken 을 augment 한 뒤 우회 캐스트
 * (`as unknown as Record<string, unknown>`) 없이 `session?.accessToken` 으로
 * 추출한다. 본 테스트는 그 추출/주입 로직(타입 변경 후 동작 보존)을 검증.
 *
 * 검증:
 *   1. accessToken 보유 session → setAuthToken(token).
 *   2. 미로그인(session null) → setAuthToken("").
 *   3. accessToken 부재 session → setAuthToken("").
 */

import { render } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { AuthTokenSync } from "@/components/AuthTokenSync";

// next-auth/react useSession 모킹 — AuthButton.test 패턴(untyped vi.fn).
const mockUseSession = vi.fn();
vi.mock("next-auth/react", () => ({
  useSession: () => mockUseSession(),
}));

// setAuthToken spy — 주입 인자 검증.
const mockSetAuthToken = vi.fn();
vi.mock("@/lib/api/client", () => ({
  setAuthToken: (...args: unknown[]) => mockSetAuthToken(...args),
}));

describe("AuthTokenSync", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("accessToken 보유 session → setAuthToken(token)", () => {
    mockUseSession.mockReturnValue({
      data: { accessToken: "jws-access-token", expires: "2099-01-01" },
      status: "authenticated",
    });
    render(<AuthTokenSync />);
    expect(mockSetAuthToken).toHaveBeenCalledWith("jws-access-token");
  });

  it("미로그인(session null) → setAuthToken('')", () => {
    mockUseSession.mockReturnValue({ data: null, status: "unauthenticated" });
    render(<AuthTokenSync />);
    expect(mockSetAuthToken).toHaveBeenCalledWith("");
  });

  it("accessToken 부재 session → setAuthToken('')", () => {
    mockUseSession.mockReturnValue({
      data: { user: { email: "x@y.z" }, expires: "2099-01-01" },
      status: "authenticated",
    });
    render(<AuthTokenSync />);
    expect(mockSetAuthToken).toHaveBeenCalledWith("");
  });
});
