/**
 * Speculum HTML root layout — T31 scaffold + T32 disclaimer/consent +
 * T35 NavBar + AsOfBanner.
 *
 * 모든 페이지가 본 layout 안에서 render. 모든 화면에 의무 mount:
 *   - NavBar (ADR-0010 D1 + ADR-0008 D1.1)
 *   - AsOfBanner (ADR-0008 D4.1 — 과거 시점일 때만)
 *   - ConsentModal (ADR-0006 D2 — 최초 방문 시)
 *   - DisclaimerFooter (ADR-0007 D2)
 *
 * 구조:
 *   <html>
 *     <body>
 *       <Providers>
 *         <NavBar />          ← 모든 페이지 header
 *         <AsOfBanner />      ← 과거 시점일 때만 strong notice
 *         <main>{children}</main>
 *         <ConsentModal />    ← 최초 방문 blocking modal
 *       </Providers>
 *       <DisclaimerFooter />  ← provider context 미의존 (oracle T31 L-3)
 *     </body>
 *   </html>
 */

import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { AsOfBanner } from "@/components/AsOfDatePicker";
import { ConsentModal } from "@/components/ConsentModal";
import { DisclaimerFooter } from "@/components/DisclaimerFooter";
import { NavBar } from "@/components/NavBar";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Speculum",
  description:
    "한국 주식 시장의 정량 데이터 탐색기 — 본 도구는 정보 제공 목적이며 투자 자문이 아닙니다.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

interface RootLayoutProps {
  readonly children: ReactNode;
}

export default function RootLayout({
  children,
}: RootLayoutProps): JSX.Element {
  return (
    <html lang="ko">
      <body className="flex min-h-screen flex-col">
        <Providers>
          <NavBar />
          <AsOfBanner />
          <div className="flex-1">{children}</div>
          {/* ConsentModal 은 useConsent hook 의 hasConsented === false 일 때만
              render. SSR 단계는 null 반환 (hydration 안전). */}
          <ConsentModal />
        </Providers>
        {/* footer 는 provider context 미의존 — Providers 밖에 두어 렌더 트리
            의미론 명시 (oracle T31 L-3). */}
        <DisclaimerFooter />
      </body>
    </html>
  );
}
