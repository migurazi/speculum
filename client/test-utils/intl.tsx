/**
 * 테스트용 i18n 헬퍼 — next-intl provider wrap (T67 i18n 전환).
 *
 * 컴포넌트가 `useTranslations` / `getTranslations` 로 전환되면 테스트 렌더에도
 * `NextIntlClientProvider` + 메시지 카탈로그가 필요하다(없으면 t() 가 컨텍스트
 * 부재로 throw). 본 헬퍼는 `messages/ko/` 의 모든 네임스페이스 json 을
 * `import.meta.glob`(vite/vitest) 로 머지해 **실제 ko 문자열** 로 렌더한다 —
 * 테스트가 키가 아닌 실제 한국어 텍스트(예: "비교 실행")를 그대로 검증할 수 있다.
 * production 의 `i18n/request.ts` 가 디렉터리 동적 스캔으로 머지하는 것과 동형이라,
 * 새 네임스페이스 json 이 추가돼도 본 헬퍼가 자동 반영(수정 0).
 *
 * 사용:
 *   import { renderWithIntl } from "@/test-utils/intl";
 *   renderWithIntl(<MyComponent />);
 *   // QueryClient 등 다른 provider 가 필요하면 ui 를 그것으로 감싸 전달:
 *   renderWithIntl(
 *     <QueryClientProvider client={qc}><MyComponent /></QueryClientProvider>,
 *   );
 */

import {
  render,
  type RenderOptions,
  type RenderResult,
} from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";

// messages/ko/*.json 을 빌드타임에 모두 로드(vite glob). 파일명 = 네임스페이스명.
// eager + default import → 각 모듈이 곧 그 json 객체.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const _modules = (import.meta as any).glob("../messages/ko/*.json", {
  eager: true,
  import: "default",
}) as Record<string, Record<string, unknown>>;

/** 네임스페이스별 머지된 테스트 메시지 — i18n/request.ts 머지와 동형. */
export const TEST_MESSAGES: Record<string, unknown> = Object.fromEntries(
  Object.entries(_modules).map(([path, mod]) => {
    // ".../messages/ko/compare.json" → "compare".
    const name = path.split("/").pop()!.replace(/\.json$/, "");
    return [name, mod];
  }),
);

/**
 * NextIntlClientProvider(locale="ko") 로 감싸 렌더. 기존 `render` 의 drop-in
 * 대체 — t() 가 실제 ko 문자열을 렌더한다.
 */
export function renderWithIntl(
  ui: ReactNode,
  options?: Omit<RenderOptions, "wrapper">,
): RenderResult {
  return render(
    <NextIntlClientProvider locale="ko" messages={TEST_MESSAGES}>
      {ui}
    </NextIntlClientProvider>,
    options,
  );
}
