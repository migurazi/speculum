/**
 * Speculum Next.js config — T31 scaffold.
 *
 * 운영 backend (FastAPI) 는 별도 service 로 분리 — 본 Next 앱은 backend 를
 * API_BASE_URL env var 로 호출. M0 = same-origin proxy 또는 별도 host.
 *
 * i18n: next-intl 의 "without i18n routing" 패턴. createNextIntlPlugin 이
 * i18n/request.ts 의 getRequestConfig 를 빌드/런타임에 연결한다 (locale 고정).
 */

import createNextIntlPlugin from "next-intl/plugin";

// 요청 설정 위치를 명시 — 기본값(./i18n/request.ts)과 동일하지만 의도를 드러낸다.
const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    // typed routes — Next 14 의 typed Link / router (M0 backlog).
  },
  // ADR-0007 D4.4 — server-side response 의 금지 어휘 검사는 backend middleware
  // 가 담당. 본 next config 는 frontend build-time 검사 (ESLint rule) 에 의존.
};

export default withNextIntl(nextConfig);
