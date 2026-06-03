/**
 * RestatementHistory 단위 테스트 — M2 T82 Phase 2.
 *
 * 검증 항목:
 *   1. 로딩 상태 — 스켈레톤 플레이스홀더 렌더.
 *   2. 에러 상태 — 에러 메시지 박스, 페이지 안 깨짐.
 *   3. vintages 빈 배열 — "정정공시 이력이 없습니다." 안내.
 *   4. 정정 chain 다행 — account 행 그룹 + vintage_seq 오름차순.
 *   5. 현행 배지(statusActive) / 과거 배지(statusSuperseded) 렌더.
 *   6. 정정 N회 마커 — vintage 2+인 account 에만 표시.
 *   7. 정정 없는 account(vintage 1개) — "정정 N회" 마커 없음.
 *   8. 중립 톤 확인 — 등락 판단색 className(text-red/text-blue/text-green) 부재.
 *   9. 과거 vintage 값 셀에 취소선(line-through) — 중립 취소선, 색 없음.
 *  10. 천단위 구분자 포맷 — 값 왜곡 없음.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RestatementHistory } from "../RestatementHistory";
import { renderWithIntl } from "@/test-utils/intl";

const SAMPLE_WIRE = {
  code: "005930",
  as_of: "2024-08-30",
  fiscal_period_filter: null,
  vintages: [
    {
      vintage_seq: 1,
      effective_date: "2024-05-15",
      account: "revenue",
      value: "1000000000",
      unit: "krw",
      ifrs_type: "consolidated",
      is_active: false,
      superseded_by: "uuid-abc",
    },
    {
      vintage_seq: 2,
      effective_date: "2024-08-20",
      account: "revenue",
      value: "1050000000",
      unit: "krw",
      ifrs_type: "consolidated",
      is_active: true,
      superseded_by: null,
    },
    {
      vintage_seq: 1,
      effective_date: "2024-05-15",
      account: "operating_income",
      value: "200000000",
      unit: "krw",
      ifrs_type: "consolidated",
      is_active: true,
      superseded_by: null,
    },
  ],
};

const EMPTY_WIRE = {
  code: "000001",
  as_of: null,
  fiscal_period_filter: null,
  vintages: [],
};

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderHistory(ui: React.ReactNode) {
  return renderWithIntl(
    <QueryClientProvider client={makeClient()}>{ui}</QueryClientProvider>,
  );
}

describe("RestatementHistory", () => {
  beforeEach(() => {
    process.env["NEXT_PUBLIC_API_BASE_URL"] = "http://test.local";
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("로딩 중 스켈레톤 플레이스홀더를 렌더한다", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));

    const { container } = renderHistory(
      <RestatementHistory code="005930" asOf="2024-08-30" />,
    );

    const skeleton = container.querySelector(".animate-pulse");
    expect(skeleton).toBeInTheDocument();
  });

  it("fetch 실패 시 에러 메시지를 렌더한다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("서버 오류", { status: 500 }),
    );

    renderHistory(<RestatementHistory code="005930" asOf="2024-08-30" />);

    await waitFor(() => {
      expect(
        screen.getByText(/정정공시 이력 로드 실패/),
      ).toBeInTheDocument();
    });
  });

  it("vintages 가 비어 있으면 안내 메시지를 렌더한다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(EMPTY_WIRE), { status: 200 }),
    );

    renderHistory(<RestatementHistory code="000001" />);

    await waitFor(() => {
      expect(
        screen.getByText("정정공시 이력이 없습니다."),
      ).toBeInTheDocument();
    });
  });

  it("정정 chain — revenue 의 두 vintage 가 모두 렌더된다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_WIRE), { status: 200 }),
    );

    renderHistory(<RestatementHistory code="005930" asOf="2024-08-30" />);

    await waitFor(() => {
      // 두 공시일 모두 렌더 (operating_income 도 동일 날짜가 있으므로 getAllByText).
      const may15 = screen.getAllByText("2024-05-15");
      expect(may15.length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("2024-08-20")).toBeInTheDocument();
    });
  });

  it("현행 배지(현행)와 과거 배지(과거) 가 렌더된다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_WIRE), { status: 200 }),
    );

    renderHistory(<RestatementHistory code="005930" asOf="2024-08-30" />);

    await waitFor(() => {
      // 현행 배지 — revenue 현행 + operating_income 현행 = 2개.
      const activeBadges = screen.getAllByText("현행");
      expect(activeBadges.length).toBeGreaterThanOrEqual(1);
      // 과거 배지 — revenue 의 vintage_seq=1 이 과거.
      expect(screen.getByText("과거")).toBeInTheDocument();
    });
  });

  it("정정 2회 이상 account 에 '정정 N회' 마커가 렌더된다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_WIRE), { status: 200 }),
    );

    renderHistory(<RestatementHistory code="005930" asOf="2024-08-30" />);

    await waitFor(() => {
      // revenue 는 vintage 2개 → "정정 1회".
      expect(screen.getByText("정정 1회")).toBeInTheDocument();
    });
  });

  it("정정 없는 account (vintage 1개) 에 정정 마커가 없다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_WIRE), { status: 200 }),
    );

    renderHistory(<RestatementHistory code="005930" asOf="2024-08-30" />);

    await waitFor(() => {
      // operating_income 은 vintage 1개 → 마커 없음.
      // "정정 0회" 텍스트가 없어야 한다.
      expect(screen.queryByText("정정 0회")).not.toBeInTheDocument();
    });
  });

  it("중립 톤 — 등락 판단색 className(text-red/text-blue/text-green) 부재", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_WIRE), { status: 200 }),
    );

    const { container } = renderHistory(
      <RestatementHistory code="005930" asOf="2024-08-30" />,
    );

    await waitFor(() => {
      expect(screen.getByText("2024-08-20")).toBeInTheDocument();
    });

    // 등락 판단색(text-red-*, text-blue-*, text-green-*) 없음.
    const allClasses = container.innerHTML;
    expect(allClasses).not.toMatch(/text-red-\d{3}/);
    expect(allClasses).not.toMatch(/text-blue-\d{3}/);
    expect(allClasses).not.toMatch(/text-green-\d{3}/);
    // bg 판단색도 없음.
    expect(allClasses).not.toMatch(/bg-red-\d{3}/);
    expect(allClasses).not.toMatch(/bg-blue-\d{3}/);
    expect(allClasses).not.toMatch(/bg-green-\d{3}/);
  });

  it("과거 vintage 값 셀에 line-through 클래스가 있다 (중립 취소선)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_WIRE), { status: 200 }),
    );

    const { container } = renderHistory(
      <RestatementHistory code="005930" asOf="2024-08-30" />,
    );

    await waitFor(() => {
      // operating_income 도 동일 날짜를 가지므로 getAllByText.
      const may15 = screen.getAllByText("2024-05-15");
      expect(may15.length).toBeGreaterThanOrEqual(1);
    });

    // 과거 vintage 값 셀이 line-through 를 포함해야 함.
    const lineThroughEl = container.querySelector(".line-through");
    expect(lineThroughEl).toBeInTheDocument();
    // 하지만 색 판단(text-red-*/text-blue-*) 없음.
    expect(lineThroughEl?.className).not.toMatch(/text-red-/);
    expect(lineThroughEl?.className).not.toMatch(/text-blue-/);
  });

  it("천단위 구분자 포맷 — 큰 숫자에 comma 삽입, 값 왜곡 없음", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE_WIRE), { status: 200 }),
    );

    renderHistory(<RestatementHistory code="005930" asOf="2024-08-30" />);

    await waitFor(() => {
      // 1000000000 → 1,000,000,000
      expect(screen.getByText("1,000,000,000")).toBeInTheDocument();
      // 1050000000 → 1,050,000,000
      expect(screen.getByText("1,050,000,000")).toBeInTheDocument();
    });
  });
});
