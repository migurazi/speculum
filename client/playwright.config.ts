/**
 * Playwright E2E config — M0_PLAN T42.
 *
 * 4 뷰 (Screener / Stock Detail / Compare / Watchlist) + 동의 모달 + 홈
 * 페이지 sanity 시나리오의 실 브라우저 검증. unit test (vitest) 가 component
 * 단위라면 E2E 는 page-level 사용자 흐름.
 *
 * 설계 결정:
 *
 * 1. **chromium only** (M0). Firefox / WebKit 은 M1+ backlog — 운영 사용자
 *    Chrome 우위 + chromium 1 개만 CI 빠름 (~2 분 vs ~6 분). 4 뷰 page-level
 *    검증은 chromium 1 개로 충분.
 *
 * 2. **webServer = `next dev`**. prod build (`next build` + `next start`) 도
 *    가능하나 dev mode 가 hot reload + 빠른 startup (~5s vs ~30s). E2E 의
 *    의도는 사용자 flow — prod build 차이 (image optimization 등) 는 별도
 *    smoke 가 책임. CI 에서도 dev 사용 (build cache 회피 + 단순).
 *
 * 3. **Korean locale + Asia/Seoul timezone**. 화면 표시 (날짜 / 통화 / 한글
 *    UI text) 가 사용자 환경과 일치. AC-D-05 의 KST 일자 invariant 와 합치.
 *
 * 4. **CI retries = 2**. 로컬은 0 — 실패 즉시 인지. CI 의 network flakiness
 *    (예: pnpm install transient) 흡수.
 *
 * 5. **screenshot + video = on-failure only**. 통과 시 artifact 비용 0,
 *    실패 시만 운영자 진단 자료. trace 는 first retry 한정 (default).
 *
 * 6. **baseURL = http://localhost:3100** (default `pnpm dev` 의 3000 과 분리).
 *    개발자가 동시에 `pnpm dev` 띄워 작업해도 E2E webServer 와 port 충돌 없음.
 *    또한 다른 프로젝트가 localhost:3000 사용 중인 환경에서 reuseExistingServer
 *    가 잘못된 서버 잡는 사고 차단.
 *
 * 관련:
 * - M0_PLAN T42 (E2E Playwright)
 * - AC-F-01 (Google OAuth → 최초 진입 동의 모달 → 메인)
 * - AC-P-03 (Active Inspection — 홈 화면 추천 위젯 0)
 */

import { defineConfig, devices } from "@playwright/test";

// CI 환경 감지 — GitHub Actions / GitLab CI 표준 env var.
const isCI = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./tests/e2e",

  // E2E 는 sequential 보다 병렬이 빠름 — fully parallel.
  fullyParallel: true,

  // CI 에서 `test.only` 가 commit 되면 fail — 실수 차단.
  forbidOnly: isCI,

  // CI 에서 transient flakiness 흡수. 로컬은 0 — 즉시 인지.
  retries: isCI ? 2 : 0,

  // 로컬은 worker auto (CPU 기반), CI 는 1 worker — Next dev server 가
  // 단일 instance 라 다중 worker 가 race condition 위험.
  workers: isCI ? 1 : undefined,

  // HTML reporter — 실패 시 trace 포함된 report. CI 의 artifact 로 upload.
  reporter: isCI ? [["html", { open: "never" }], ["github"]] : [["html", { open: "never" }]],

  use: {
    baseURL: "http://localhost:3100",
    // 한국 사용자 표준. UI 의 날짜/통화 format 검증.
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    // 실패 시만 screenshot + video. 통과 시 artifact 0 (CI 비용).
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    // first retry 시 trace 생성 — 운영자가 timeline 확인.
    trace: "on-first-retry",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  // Next dev server 자동 spawn — port 3100 (default 3000 과 분리, 위 §6 참조).
  // reuseExistingServer = !CI — 로컬은 이미 띄운 dev server 재사용 (개발
  // 사이클 빠름), CI 는 항상 fresh.
  //
  // env: NextAuth 의 AUTH_SECRET / NEXTAUTH_SECRET 강제 주입 — 운영 .env
  // 없이도 dev server 가 boot 가능. 본 값은 E2E test 한정 (production secret X).
  webServer: {
    command: "pnpm exec next dev --port 3100",
    url: "http://localhost:3100",
    timeout: 120_000,  // Next dev 첫 빌드 + cold start 안전 마진.
    reuseExistingServer: !isCI,
    stdout: "ignore",
    stderr: "pipe",
    env: {
      AUTH_SECRET: "playwright-e2e-only-secret-not-for-production",
      NEXTAUTH_SECRET: "playwright-e2e-only-secret-not-for-production",
      // NextAuth 가 trusted origin 검증 시 NEXTAUTH_URL 필요.
      AUTH_URL: "http://localhost:3100",
      NEXTAUTH_URL: "http://localhost:3100",
    },
  },
});
