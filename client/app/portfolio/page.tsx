"use client";

/**
 * Portfolio 페이지 — `/portfolio`.
 *
 * 사용자 개인 거래내역 장부 + 포지션 조회 (ADR-0029).
 *
 * USER_PRIVATE scope — 인증 필수. 미로그인 시 PortfolioPanel 내부에서
 * 401 포착 + 로그인 유도 (CustomScreenPanel 패턴).
 *
 * 가드레일 (ADR-0029 D3):
 *   - 손익률/평가 라벨/등락색 0.
 *   - PortfolioPanel 에 위임.
 *
 * 관련:
 *   - components/Portfolio/PortfolioPanel.tsx — 실제 UI.
 *   - ADR-0029 (Portfolio 회계).
 */

import { useTranslations } from "next-intl";

import { PortfolioPanel } from "@/components/Portfolio/PortfolioPanel";

export default function PortfolioPage(): JSX.Element {
  // Portfolio 네임스페이스에서 탭 라벨을 재사용해 heading 구성.
  // 별도 heading 키 추가가 더 명확하지만 현 messages 구조 일관성 유지.
  const t = useTranslations("common");

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      {/* sr-only heading — 접근성 (lab/page.tsx 패턴) */}
      <h1 className="text-xl font-semibold text-neutral-900">
        {t("nav.portfolio")}
      </h1>

      <div className="mt-6">
        <PortfolioPanel />
      </div>
    </main>
  );
}
