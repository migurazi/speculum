"use client";

/**
 * Stock 검색 랜딩 — `/stock` (index).
 *
 * NavBar 의 "종목"(`nav.stocks`) 링크는 `/stock` 으로 이동하나, 실 라우트는
 * 동적 `/stock/[code]`(종목코드 필수)만 존재해 index 가 없으면 404("This page
 * could not be found") 가 떴다. 본 페이지가 그 index 갭을 메운다 — 종목을 고를
 * 화면이 없으면 상세로 갈 진입점이 없다.
 *
 * 동작: `StockSearch`(자족 combobox — 선택 시 `router.push('/stock/{code}')`)를
 * 랜딩 중앙에 배치 + 대표 종목 바로가기. as_of 는 전역 store 를 StockSearch 가
 * 직접 구독(별도 배선 불요).
 *
 * 관련:
 * - components/StockSearch.tsx (검색 → 상세 이동).
 * - components/NavBar.tsx `/stock` 링크.
 * - app/stock/[code]/page.tsx (상세).
 */

import Link from "next/link";
import { useTranslations } from "next-intl";

import { StockSearch } from "@/components/StockSearch";

// 대표 대형주 바로가기 — 검색어 입력 없이 즉시 상세 진입(편의). 코드만 하드코딩,
// 종목명은 i18n 무관 고유명사라 그대로 표기. 미적재 시 상세 화면이 자체 빈상태
// 처리(여기서 가용성 가정 안 함).
const QUICK_LINKS: ReadonlyArray<{ readonly code: string; readonly name: string }> = [
  { code: "005930", name: "삼성전자" },
  { code: "000660", name: "SK하이닉스" },
  { code: "035420", name: "NAVER" },
  { code: "005380", name: "현대차" },
];

export default function StockLandingPage(): JSX.Element {
  const t = useTranslations("stock.landing");

  return (
    <main className="mx-auto flex max-w-2xl flex-col items-center px-4 py-16">
      <h1 className="mb-3 text-2xl font-semibold text-neutral-900">
        {t("heading")}
      </h1>
      <p className="mb-8 text-center text-sm text-neutral-500">
        {t("description")}
      </p>

      <StockSearch className="w-full" />

      <div className="mt-8 w-full">
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-400">
          {t("examplesLabel")}
        </p>
        <ul className="flex flex-wrap gap-2">
          {QUICK_LINKS.map(({ code, name }) => (
            <li key={code}>
              <Link
                href={`/stock/${code}`}
                className="inline-flex items-center gap-2 rounded-full border border-neutral-200 px-3 py-1.5 text-sm text-neutral-700 transition hover:border-neutral-400 hover:bg-neutral-50"
              >
                <span className="font-medium">{name}</span>
                <span className="text-xs text-neutral-400">{code}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </main>
  );
}
