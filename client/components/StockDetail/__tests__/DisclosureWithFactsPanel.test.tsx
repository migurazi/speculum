/**
 * DisclosureWithFactsPanel — A-1 Fragment key 회귀 가드 + 기본 렌더.
 *
 * A-1: map() 콜백이 `<>...</>` Fragment 를 반환하면서 key 가 자식 <tr> 에만
 *      있어 React 가 list key 경고를 냈음(자식 key 는 Fragment 의 list key 가
 *      아님). `<Fragment key={rowKey}>` 로 교체 후 자식 중복 key 제거 →
 *      경고 0. 본 테스트는 console.error 에 key 경고가 없음을 검증한다.
 *
 * 셋업: fetchDisclosures 를 직접 mock(네트워크 무관) + QueryClient + intl.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, afterEach } from "vitest";

import { DisclosureWithFactsPanel } from "../DisclosureWithFactsPanel";
import { fetchDisclosures } from "@/lib/api/disclosures";
import type { DisclosureList } from "@/lib/api/disclosures";
import { renderWithIntl } from "@/test-utils/intl";

vi.mock("@/lib/api/disclosures", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/api/disclosures")>();
  return { ...actual, fetchDisclosures: vi.fn() };
});

const LIST: DisclosureList = {
  code: "005930",
  asOf: "2026-05-15",
  disclosures: [
    {
      reportName: "분기보고서 (2026.03)",
      rceptDate: "2026-05-15",
      dartUrl: "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515000001",
    },
    {
      reportName: "주요사항보고서",
      rceptDate: "2026-05-14",
      dartUrl: "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260514000002",
    },
  ],
};

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPanel() {
  return renderWithIntl(
    <QueryClientProvider client={makeClient()}>
      <DisclosureWithFactsPanel code="005930" asOf="2026-05-15" />
    </QueryClientProvider>,
  );
}

describe("DisclosureWithFactsPanel", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("공시 행을 렌더한다 (Fragment 매핑)", async () => {
    vi.mocked(fetchDisclosures).mockResolvedValue(LIST);
    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("분기보고서 (2026.03)")).toBeInTheDocument();
    });
    expect(screen.getByText("주요사항보고서")).toBeInTheDocument();
  });

  it("React list key 경고를 발생시키지 않는다 (A-1 Fragment key)", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(fetchDisclosures).mockResolvedValue(LIST);

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("분기보고서 (2026.03)")).toBeInTheDocument();
    });

    const keyWarnings = errorSpy.mock.calls.filter((call) =>
      String(call[0]).includes('unique "key" prop'),
    );
    expect(keyWarnings).toHaveLength(0);

    errorSpy.mockRestore();
  });
});
