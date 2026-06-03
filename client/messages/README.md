# i18n 메시지 (next-intl) — Phase B 전환 가이드

Speculum 은 next-intl 의 **"without i18n routing"** 패턴을 사용한다.
단일 고정 locale `ko` — URL 에 locale prefix 가 없다. 향후 다국어 확장 시
routing 을 도입할 수 있도록 구조만 갖춘다.

## 디렉터리 구조

```
messages/
  README.md            ← 본 문서
  ko/
    common.json        ← 공통(앱 이름, 네비, 검색 등) 네임스페이스
    home.json          ← 홈 화면 네임스페이스
    <view>.json        ← 뷰/도메인별 네임스페이스 (Phase B 에서 추가)
i18n/
  request.ts           ← getRequestConfig — ko/*.json 을 동적 머지
```

`i18n/request.ts` 가 `messages/ko/*.json` 을 **런타임에 디렉터리에서 동적으로
읽어** 머지한다. **파일명(확장자 제외) = top-level 네임스페이스** 다
(예: `compare.json` → `useTranslations("compare")`).

## 네임스페이스 규약 (뷰 = 네임스페이스)

| 파일 | 네임스페이스 | 범위 |
| --- | --- | --- |
| `common.json` | `common` | 앱 전역 공통 텍스트(네비, 검색, 앱 이름) |
| `home.json` | `home` | `app/page.tsx` 홈 화면 |
| `compare.json` | `compare` | `app/compare/` + `components/Compare/` |
| `screener.json` | `screener` | `app/screener/` + `components/Screener/` |
| `watchlist.json` | `watchlist` | `app/watchlist/` + `components/Watchlist/` |
| `runs.json` | `runs` | `app/runs/` + `components/Runs/` |
| `stock.json` | `stock` | `app/stock/` + `components/StockDetail/` |
| `market.json` | `market` | `app/market/` |
| `legal.json` | `legal` | `app/disclaimer`·`privacy`·`terms` + Disclaimer/Consent |

여러 뷰가 공유하는 텍스트만 `common` 에 둔다. 한 뷰에만 쓰이면 해당 뷰
네임스페이스에 둔다. 새 뷰는 새 파일을 만들면 된다 (코드 수정 불필요).

## 키 네이밍

- 평면 키: `compare.title`, `compare.runButton`
- 중첩 키(그룹화): `common.nav.screener`, `home.quickLinks.compare.title`
- json 안에서는 네임스페이스명을 다시 쓰지 않는다. `compare.json` 의 루트가
  곧 `compare` 네임스페이스이므로 `{ "title": "...", "runButton": "..." }`.
- **No Advice (8 기둥 §2.2)**: 키 값(번역 문자열)에도 금지 어휘(추천/유망 등)
  를 넣지 않는다. 기존 하드코딩 텍스트를 **그대로** 옮기되 어휘를 추가·각색
  하지 않는다. ESLint `speculum/no-forbidden-words` 가 코드를 검사하지만
  json 값은 사람이 직접 지켜야 한다.

## 호출 패턴 (서버 vs 클라이언트)

### 서버 컴포넌트 (파일 상단에 `"use client"` 없음)

```tsx
import { getTranslations } from "next-intl/server";

export default async function CompareView() {
  const t = await getTranslations("compare");
  return <h1>{t("title")}</h1>;
}
```

메타데이터(`generateMetadata`)도 동일하게 `getTranslations` 를 쓴다.
참고 샘플: `app/layout.tsx`(메타데이터), `app/page.tsx`(홈 본문).

### 클라이언트 컴포넌트 (`"use client"`)

```tsx
"use client";
import { useTranslations } from "next-intl";

export function RunButton() {
  const t = useTranslations("compare");
  return <button>{t("runButton")}</button>;
}
```

참고 샘플: `components/NavBar.tsx`.

> 동적 키(배열 map 등)는 `t(\`quickLinks.${key}.title\`)` 처럼 템플릿 리터럴로
> 조합할 수 있다 (`app/page.tsx` 참고).

## 새 네임스페이스 추가 방법 (Phase B)

1. `messages/ko/<view>.json` 파일을 새로 만든다.
2. 전환 대상 컴포넌트에서 `getTranslations("<view>")` /
   `useTranslations("<view>")` 로 사용한다.
3. **끝.** `i18n/request.ts` 나 `next.config.mjs` 는 수정하지 않는다 —
   디렉터리를 동적으로 읽으므로 자동 반영된다.

## 병렬 전환 충돌 회피 (핵심 설계 목표)

`i18n/request.ts` 는 **명시적 import 나열이 없다.** `messages/ko/` 디렉터리를
동적으로 스캔하므로:

- 서로 다른 agent 가 **서로 다른 `<view>.json`** 을 동시에 추가/편집해도
  공유 파일(`request.ts` 등) 을 건드리지 않아 git·편집 충돌이 없다.
- 같은 네임스페이스(같은 json) 안에서도 키가 겹치지 않으면 충돌이 없다.
- 한 뷰는 한 agent 가 담당하는 것을 권장 (같은 json 동시 편집 최소화).
