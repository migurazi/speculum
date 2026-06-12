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
import { NextIntlClientProvider } from "next-intl";
import { getMessages, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

import { AsOfBanner } from "@/components/AsOfDatePicker";
import { ConsentModal } from "@/components/ConsentModal";
import { DisclaimerFooter } from "@/components/DisclaimerFooter";
import { NavBar } from "@/components/NavBar";

import "./globals.css";
import { Providers } from "./providers";

/**
 * 서버 컴포넌트의 i18n 패턴 (Phase B 골든 샘플 ①):
 * 메타데이터 같은 비동기 서버 영역에서는 getTranslations 로 네임스페이스를
 * 받아 t(key) 로 조회한다. useTranslations(클라이언트) 와 대비.
 */
export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("common");
  return {
    title: t("metaTitle"),
    description: t("metaDescription"),
  };
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

interface RootLayoutProps {
  readonly children: ReactNode;
}

export default async function RootLayout({
  children,
}: RootLayoutProps): Promise<JSX.Element> {
  // next-intl: 서버에서 머지된 messages 를 받아 클라이언트 컴포넌트
  // (useTranslations) 에 제공. NextIntlClientProvider 는 DisclaimerFooter 까지
  // 포함해 body 전체를 감싼다 — Providers(react-query/next-auth) 와 공존.
  const messages = await getMessages();

  return (
    <html lang="ko">
      <body className="flex min-h-screen flex-col">
        <NextIntlClientProvider messages={messages}>
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
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
