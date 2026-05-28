/**
 * Speculum 홈 페이지 — ADR-0010 D1 의 Watchlist + 빠른 시작 + 가이드.
 *
 * 8 기둥 §2.3 Active Inspection — 빌트인 위젯 0, 사용자가 의도 표시 후
 * 데이터 표시. ADR-0010 D2 의 "오늘의 종목 TOP" 같은 의제 설정 위젯 미존재.
 *
 * M0 scope:
 *   - Watchlist 영역 (M0 빈 placeholder — Watchlist 도 비어있다는 가정).
 *     T39 Watchlist 뷰 합류 시 실 data fetch.
 *   - 시장 통계 (KOSPI / KOSDAQ 지수 + 거래대금) — M1 backlog. backend 의
 *     `/api/market-overview` endpoint 미구현. 본 cycle 미렌더.
 *   - 빠른 시작 navigation (4 뷰) — ADR-0010 D2.3.
 *   - 가이드 link — ADR-0010 D2.4.
 *
 * 관련 ADR / 문서:
 * - ADR-0010 (홈 화면 정체성).
 * - 8 기둥 §2.3 (Active Inspection).
 * - M0_PLAN T35.
 */

import Link from "next/link";

const QUICK_LINKS = [
  {
    href: "/screener",
    title: "Screener",
    description: "정량 조건으로 종목 필터링.",
  },
  {
    href: "/stock",
    title: "종목 조회",
    description: "단일 종목의 지표·가격·재무 상세.",
  },
  {
    href: "/compare",
    title: "Compare",
    description: "2~6 종목 나란히 비교.",
  },
  {
    href: "/watchlist",
    title: "Watchlist",
    description: "관심 종목 폴더링·메모.",
  },
] as const;

export default function HomePage(): JSX.Element {
  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      {/* Watchlist 영역 — ADR-0010 D2.1.
          M0 = 빈 placeholder. T39 cycle 의 실 fetch 합류 시 component 화. */}
      <section className="rounded-lg border border-neutral-200 p-6">
        <h2 className="text-base font-semibold text-neutral-900">
          내 관심 종목
        </h2>
        <p className="mt-2 text-sm text-neutral-600">
          관심 종목을 추가하면 여기에 표시됩니다. Screener 로 종목을 탐색하거나
          종목 조회에서 직접 검색하세요.
        </p>
      </section>

      {/* 빠른 시작 navigation — ADR-0010 D2.3. */}
      <section className="mt-8">
        <h2 className="text-sm font-medium uppercase tracking-wide text-neutral-500">
          빠른 시작
        </h2>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {QUICK_LINKS.map(({ href, title, description }) => (
            <Link
              key={href}
              href={href}
              className="rounded-lg border border-neutral-200 p-4 transition-colors hover:border-neutral-400 hover:bg-neutral-50"
            >
              <div className="text-sm font-medium text-neutral-900">
                {title}
              </div>
              <div className="mt-1 text-xs text-neutral-600">{description}</div>
            </Link>
          ))}
        </div>
      </section>

      {/* 가이드 — ADR-0010 D2.4. M0 = link placeholder. */}
      <section className="mt-8 rounded-lg border border-neutral-200 bg-neutral-50 px-6 py-4 text-sm text-neutral-700">
        <p>
          Speculum 은 한국 주식 시장의 정량 데이터 탐색 도구입니다. 어떻게
          시작하나요?{" "}
          <Link
            href="/guide"
            className="font-medium text-neutral-900 underline-offset-2 hover:underline"
          >
            가이드 보기
          </Link>
        </p>
      </section>
    </main>
  );
}
