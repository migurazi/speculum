# ADR-0007: 디폴트 UI 규약 (No Advice 의 코드 수준 implementation)

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-05-22 |
| **Deciders** | 사용자 |
| **Related** | `docs/CONCEPT.md §2.2 No Advice`, `§2.3 Active Inspection`, `docs/M0_PLAN.md T7, T29`, [[adr-0006-legal-review]] D2, D8 |

## Context

[[adr-0006-legal-review]] D8 의 시스템 디자인 제약을 구체적 UI 규약으로 정리. CONCEPT §2.2 (No Advice) 와 §2.3 (Active Inspection) 의 단순 disclaimer 가 아닌 시스템 전반의 디자인 제약을 만드는 ADR.

librarian D-5 의 우려 — "디스클레이머만으로는 부족, UI 가 권유적 성격을 띠면 무의미" — 에 대한 직접 대응.

규정해야 할 항목:
1. 기본 정렬 (default sort)
2. 강조 / 하이라이트 색상 의미
3. 푸시 알림
4. 금지 어휘
5. 점수 / 랭킹 표시
6. 광고·홍보 영역

## Decision

### D1. 기본 정렬 — 중립적 정렬 only

| 화면 | 기본 정렬 | 사용자 토글 |
|---|---|---|
| **Screener 결과** | `시가총액 ↓` (시장 가치 큰 순서) | 모든 컬럼 정렬 가능 |
| **검색 결과** | `종목명 가나다 ↑` | 알파벳 / 시가총액 |
| **Watchlist** | 사용자 입력 순서 (드래그 가능) | - |
| **Sector 뷰** (M1) | `KRX 업종분류 표준 순서` | - |
| **Market Overview** (M1) | `거래대금 ↓` (사실) | - |

**금지된 기본 정렬:**
- ❌ `PER ↑` — "PER 낮은 종목" 이 좋다는 가치 시그널
- ❌ `ROE ↓` — 수익성 좋은 순서
- ❌ `등락률 ↓` — 가격 변동 추세 시그널
- ❌ 합산 점수 (multi-factor score) — D5

**Rationale**: 시가총액·종목코드·가나다는 가치 판단을 내포하지 않는 사실. PER/ROE/등락률 같은 정렬은 사용자가 명시적 클릭으로만 진입.

### D2. 색상 의미 — "분포·차이" 만, "좋다·나쁘다" 금지

#### D2.1 금지된 색상 사용

| 패턴 | 이유 |
|---|---|
| ❌ PER 낮을수록 녹색, 높을수록 빨강 | "낮은 PER = 좋다" 시그널 |
| ❌ ROE 높을수록 녹색 | "높은 ROE = 좋다" 시그널 |
| ❌ 등락률 빨강(상승)/파랑(하락) (한국 관행) | M0 에서 도입하지 않음 — D2.2 의 단계화 |
| ❌ "BUY/SELL" 라벨의 빨강·녹색 | 매매 시그널 |

#### D2.2 허용된 색상 사용

| 패턴 | 의미 |
|---|---|
| ✓ Percentile 표시 — 단색 grayscale gradient | 분포 내 위치 (가치 무관) |
| ✓ 히트맵 — 차원 단순 표시 (cool/warm color, no positive/negative) | 분포 |
| ✓ 차트 시각화 — TradingView 표준 캔들 색상 (M0 한국 관행 유지: 양봉 빨강 / 음봉 파랑) | 차트의 사실 표시 (M0 는 도입 유지, M1 재검토 — librarian D-5 의 우려와 균형) |

#### D2.3 시각 강조 (highlight)

- **허용**: 사용자가 hover 한 row 의 배경색 변경 (interaction feedback)
- **허용**: 검색 일치 키워드 yellow background (사용자 의도)
- **금지**: 시스템이 "주목할 만한" 종목에 임의 강조 (예: "오늘의 종목" 표시)
- **금지**: "AI 추천" / "유망" / "관심" 배지

### D3. 푸시 알림 — M0 미구현

ADR-0011 D1 결정. M0 에서 push notification 자체 없음.

- 이메일도 운영 알림 (회원가입 환영, 비밀번호 변경 등) 만. 종목 관련 알림 X.
- M1+ 검토 항목: 사용자 보유 종목의 DART 공시 발생 알림 (사실 전달, 매매 시그널 아님). 그러나 ADR-0006 재검토 필요.

### D4. 금지 어휘 (forbidden words)

[[adr-0006-legal-review]] D8 의 코드 수준 implementation. ESLint rule + CI 게이트:

#### D4.1 절대 금지 — 사용자 visible 텍스트 (한국어)

**전체 list 는 `shared/forbidden-words.json` 의 `ko_absolute` 배열이 single
source of truth (D4.6).** ADR 본문은 정책 근거 + 대표 예시만 — list 갱신 시
ADR 동기 필수는 없으나 SoT 와 본문 불일치를 회피하기 위해 본문은 카테고리별
대표 예시로 한정.

대표 예시 (카테고리별):
- **추천·평가 어휘**: 추천 / 유망 / 기대 / 주목 / 종목 픽 / 탑픽 / 강추
- **강도 어휘**: 강력 매수 / 강력 추천 / 비중 확대 / 강력 매도
- **거래 행위 단독**: 매수 / 매도 / 매집 / 손절 / 익절 / 사세요 / 파세요
- **거래 시점**: 진입 시점 / 적기
- **평가 라벨**: 목표 주가 / 투자 의견 / 고평가 / 저평가 / 보유 권유 / 관망 / 베스트
- **picks**: 픽 / 추천종목 / 유망종목 / 유망주 / 기대주 / 관심 종목 픽

전체 어휘 + 별도 ADR 발행 이력 (예: ADR-0013 의 `진입` 단독 제거) 은 SoT JSON
참조. 본 카테고리 분류는 D9 (어휘 변경 절차) 의 의사결정 도구일 뿐.

> **Note**: "Top Pick", "Best Buy" 등 영문 표현은 D4.2 카테고리에서만 다룬다.
> 한국어 패턴은 substring 매칭이라 영문 표현이 한국어 카테고리에 있으면
> 분류 (kind) 가 KO_ABSOLUTE 로 나와 동일 어휘가 D4.2 의 EN_ABSOLUTE 와 충돌.

#### D4.2 절대 금지 — 사용자 visible 텍스트 (영문)

**전체 list 는 `shared/forbidden-words.json` 의 `en_absolute` 배열 (D4.6).**

대표 예시 (카테고리별):
- **거래 행위**: Buy / Sell / Long / Short / Accumulate
- **추천**: Recommended / Recommendation / Top Pick / Best Buy / Best
- **점수·라벨**: Conviction / Outperform / Underperform / Overweight / Underweight / Upgrade / Downgrade
- **강도**: Strong Buy / Strong Sell / Should Buy / Worth Buying / Bullish / Bearish
- **목표·보유**: Price Target / Target Price / Hold

#### D4.3 허용된 표현

| 의미 | 권장 표현 |
|---|---|
| 조건 통과 종목 | "조건에 부합하는 종목" / "필터 통과 항목" |
| 정렬 | "PER 낮은 순" / "ROE 높은 순" (사용자가 명시적으로 선택한 경우만) |
| 사용자 관심 종목 | "관심 종목" (Watchlist 라벨 — 사용자가 명시적으로 추가) |
| 차트 강조 | "선택한 종목" / "비교 대상" |

#### D4.4 강제 — CI 게이트 + 런타임 미들웨어

`server/app/services/forbidden_words.py` + `client/lib/forbidden-words.ts` + ESLint rule + FastAPI middleware:

```typescript
// .eslintrc.json (M0 후반)
{
  "rules": {
    "speculum/no-forbidden-words": "error"
  }
}
```

- 코드 (TSX, MDX) 의 텍스트 리터럴 검사 (M0 후반 — ESLint plugin)
- 빌드 시 i18n / 콘텐츠 JSON 검사 (M0 후반 — build script)
- 이메일 템플릿 검사 (M1+)
- **API response 검사 (런타임)** — `ForbiddenWordsGuardMiddleware` 가 모든 JSON
  응답을 가로채 검사. M0 시점 구현 완료 (2026-05-22).

#### D4.4.1 ForbiddenWordsGuardMiddleware 정책 표

| 환경 | Default Policy | 동작 |
|---|---|---|
| **DEV** | `REDACT` | 검출 어휘를 `***` 로 치환. 응답 본문에 어휘 echo 0 |
| **STAGING** | `REDACT` | 동일 |
| **PROD** | `BLOCK` | 응답 자체를 generic 500 으로 swap. `code: FORBIDDEN_WORDS_GUARD_BLOCK` |

**WARN_ONLY 정책** — 모든 환경에서 명시적 opt-in 만:

```bash
SPECULUM_FORBIDDEN_POLICY=warn-only
```

WARN_ONLY 는 응답에 어휘를 echo 하므로 ADR-0006 D2 의 자본시장법 회피 의도와 정면
충돌. 개발자 디버깅 편의용으로 사용하되 **production-bound 환경에서는 절대 금지**.

#### D4.4.2 Middleware 의 운영적 보장 (oracle 리뷰 v2 반영, 2026-05-22)

| 항목 | 보장 |
|---|---|
| **응답 본문 echo** | BLOCK = generic message, REDACT = `***`. 어휘 자체 노출 0 |
| **BackgroundTask 보존** | `_replace_body` 가 `background` 속성 전달 |
| **Multi-value 헤더** | `Set-Cookie` 등 raw 순회로 보존 |
| **무결성 헤더** | REDACT 시 `ETag` / `Content-MD5` / `Content-Digest` / `Repr-Digest` 자동 제거 |
| **압축 응답** | `Content-Encoding: gzip` 등은 검사 skip (의도된 bypass, 라우터 단에서 압축 전 검사 권장) |
| **Body size cap** | 기본 1 MiB. 초과 시 검사 skip + marker 본문 + audit |
| **JSON parse 깊이 폭발** | `json.loads` 의 `RecursionError` 까지 catch |
| **AuditSink 예외** | swallow + `logger.exception`. 감사 실패가 응답 차단 안 함 |
| **NFKC normalize** | REDACT 가 fullwidth `Ｂｕｙ` 같은 회피 차단 |
| **exclude_keys** | dict key 단위 검사 제외. EXTERNAL_QUOTE scope (§D4.5) implementation |

#### D4.5 검사 Scope — 텍스트 출처별 정책 (oracle 리뷰 C3, C1)

`CheckScope` enum 으로 호출자가 텍스트 출처를 명시. 모든 텍스트에 일률 엄격 검사를
적용하면 회사명·종목명·사용자 본인 메모에서 false positive 폭증.

| Scope | 정책 | 사용 위치 |
|---|---|---|
| `SYSTEM` | **검사. 가장 엄격.** | UI 라벨, 카드 제목, 시스템 메시지, 이메일 템플릿 |
| `USER_PRIVATE` | **검사 skip.** 본인만 보는 메모는 자기 의제 → No Advice 무관 | Watchlist 메모 (M0), 사용자 개인 노트 |
| `USER_SHARED` | **검사.** 다른 사용자에게 영향력 발생 → No Advice 적용 | Factor Lab pack name/description (M2), 공유 Watchlist (M2+) |
| `EXTERNAL_QUOTE` | **검사 skip.** 외부 사실 — Speculum 의 의제 아님 | 종목명·회사명·ETF 이름, DART 공시 제목, KSIC 업종명 |

`scan_api_response` 의 `exclude_paths` 파라미터로 dict key 단위 제외 가능 — 종목명 등의
구조적 path 를 운영 시 false positive 없이 검사 영역에서 제외.

**[T76 결정 — 2026-06-02] M2 종목별 Notes = USER_PRIVATE 전용 (work-order §7.5 closure).**
종목별 사용자 Markdown 메모(T76)는 **USER_PRIVATE** scope 로 구현 — forbidden-words 검사
skip(본인만 보는 자기 의제, No Advice 무관, 위 표 행과 일치). **USER_SHARED 는 enum 에
예약돼 있으나 M2 미구현** — Notes 공유 surface 가 부재하므로(공유 서버는 M3+, work-order
§8) SHARED 강제는 도달 불가 경로를 지키는 dead code. repository 경계에서 `scope !=
USER_PRIVATE` 를 `NotesDataError` 로 거부해 SHARED 를 구조적으로 봉쇄(M3 공유 도입 시
`assert_clean(..., scope=USER_SHARED)` 한 줄 추가로 전환). **시스템 생성 Notes 0**(No
Advice §2.2) — Notes 작성 경로는 인증된 user route 뿐, system-write path 부재(seeder/
migration insert/builtin 0). **grep-gate** 로 강제(T73 `<RecommendedStocks>` 부재 게이트
패턴). **XSS(R6)** — Markdown 은 raw 저장, 렌더 시 sanitize(허용 태그 whitelist + DOMPurify
단일 chokepoint, `dangerouslySetInnerHTML` 는 그 chokepoint import 파일만). backend 는
길이 cap 만(Markdown 은 HTML 이 아니므로 서버 HTML sanitize 는 잘못된 레이어). Notes 는
mutable CRUD — append-only(ADR-0020)는 재현 freeze artifact(screen_runs)에만 적용, 메모는
편집/삭제가 자연(재현 불변식 없음).

#### D4.6 어휘 SoT — `shared/forbidden-words.json` (oracle 리뷰 D1)

어휘는 `shared/forbidden-words.json` 단일 정의. Python + TypeScript 모두 빌드 시 동일
JSON 을 import → 양 언어 구현 분기 불가. ADR 본문은 이 SoT 의 정책 근거만 명시하고,
구체 어휘 list 는 JSON 을 참조.

### D5. 점수 / 합산 / 랭킹 표시 금지

#### D5.1 빌트인에 multi-factor score 없음

- M0 의 빌트인 ~30 factor 는 모두 단일 지표.
- "Magic Formula 점수", "F-Score", "퀄리티 점수" 등 합산 점수 빌트인 X.
- M2 Factor Lab 에서 사용자가 정의 가능 — 그 경우 정의·산출식이 visible (Fidelity).
- **[M2 개정 — ADR-0022 D7]** 사용자 정의 Composite 의 **허용 연산자 = [[adr-0022-composite-factor-operators]] D1 확정 집합**(weighted_sum / zscore / percentile / min_max_scale / winsorize). rank / top_n / sign 은 enum 에 **부재**(D5.2). 빌트인 composite/weighted factor **0** 은 T73 CI 게이트(`validate_builtin_no_composite`, `server/tests/test_factor_pack.py` §12)로 자동 강제 — 수동 검토 아님.

#### D5.2 랭킹 (Top N) 의 경계

- "PER 하위 10 종목" — 정렬 결과의 상위 표시 (사용자가 명시적 정렬 후) → **허용**
- "오늘의 Top 10" / "Best 10" — 시스템이 큐레이션 → **금지**
- "내 관심 종목 중 PER 낮은 순" — 사용자 명시적 → **허용**
- **[M2 개정 — ADR-0022 D7]** **rank / top_n 연산자는 factor 식 enum 에 부재**([[adr-0022-composite-factor-operators]] D1). 순위는 *연산*(factor 정의 내 서수화)이 아니라 **표시 시점 user sort** 로만 — 사용자가 결과 테이블을 명시적으로 정렬. composite 식에 rank/top_n 을 넣어 "Top-N 큐레이션"을 우회하는 통로를 **연산자 집합 레벨에서 차단**(코드베이스에 부재가 가장 안전, Alternative C). percentile 은 분포 내 위치(사실, D2.2 허용)이지 서수 선택이 아니므로 rank 와 구분.

### D6. 홈 화면 정체성 — ADR-0010 으로

홈 화면은 별도 ADR (디자인 비중 큼). 본 ADR 은 일반 규약.

### D7. 광고·홍보 영역 금지

M0:
- 광고 영역 없음 (수익 모델 아님).
- 외부 링크 (제휴 등) 없음.
- "추천 도서", "프리미엄 기능" 같은 cross-sell 없음.

M1+ 변경 시 [[adr-0006-legal-review]] 재검토.

### D8. 컴포넌트 수준 강제

#### D8.1 `SourceAttribution` component 의무

모든 정량 값 표시는 `<SourceAttribution>` wrapping 또는 props 의무:

```tsx
<MetricCard
  factorId="per:ttm-consolidated-ifrs"
  value={12.3}
  asOf="2026-05-22"
  source={citation}     // ADR-0002 D3 의 7-tuple
/>
```

빌드 게이트 (`check-source-attribution`) — props 누락 시 빌드 실패.

#### D8.2 "추천 위젯" 컴포넌트 자체가 존재하지 않음

- `<RecommendedStocks>`, `<TodaysPicks>`, `<AiSuggestion>` 같은 컴포넌트 코드베이스에 없음.
- 누군가 추가하면 PR review 차단 (검색어 검사 + 코드 owner alert).

#### D8.3 차트 라이브러리의 default 제어

Lightweight Charts default 가 서구 관행 (녹색 양봉 / 빨강 음봉) → 한국 관행 **빨강 양봉(상승) / 파랑 음봉(하락)** 으로 override.

**M1 결정 (T59 차트 시각요소 review gate, 2026-06-01)** — M0 구현이 실제로는 lightweight-charts 의 서구 관행(녹색 상승 `#16a34a` / 빨강 하락)을 그대로 두어 본 ADR 의 "한국 관행" 명시와 불일치했다. M1 에서 한국 관행(상승=빨강 `#dc2626` / 하락=파랑 `#2563eb`)으로 확정·일치화한다.

- **§2.2 No Advice 와의 관계** — 등락색은 "그날 종가 > 시가" 라는 **사실** 의 표시이며, 금지 대상인 매매 신호·추천·점수 라벨이 아니다. 네이버 금융·KRX·키움 모두 동일 관행으로, 색이 "좋다/나쁘다" 가치판단이 아닌 등락 사실을 전달한다(§2.5 Open Data Sufficiency). 차트에는 corporate action 일자(사실) 외 annotation·랭킹·추천 marker 를 넣지 않는다.
- **Compare 오버레이(T58)** — 여러 종목의 절대가격을 로그 Y축에 겹치며, 종목 구분은 **등락 의미 없는 중립 6색 팔레트**(빨강 상승/녹색 하락 같은 등락색 미사용)로만 한다. 가격 정규화(리베이싱)는 "어느 종목이 더 올랐나" 성과비교가 §2.3 Active Inspection 경계에 닿아 채택하지 않는다.
- **회귀 방지** — 캔들 색은 명명 상수(`CANDLE_UP_COLOR`/`CANDLE_DOWN_COLOR`)로 export 하고, `client/components/__tests__/chart-visual-gate.test.tsx` 가 한국 관행 색 + 서구 녹색 상승색 부재를 검증(텍스트 검사 `no-forbidden-words` 로 미포착되는 시각 요소 gate).

## Rationale

1. **librarian D-5 우려 대응** — Disclaimer 만으로는 부족, UI 디자인 자체가 No Advice 의 implementation.
2. **§2.2 No Advice 의 코드 수준 강제** — ESLint rule + CI 게이트 + component contract.
3. **§2.3 Active Inspection** — 기본 정렬·홈 화면 모두 의제 없는 fact 위주.
4. **§2.1 Fidelity** — SourceAttribution 의무.
5. **법적 안전** — 추천 기능 가능성 자체를 코드베이스에서 제거 (`<RecommendedStocks>` 컴포넌트 비존재).

## Consequences

### Positive
- **No Advice 의 단단한 implementation** — disclaimer + 시스템 디자인 일관.
- **CI 게이트** — silent regression 방지.
- **컴포넌트 수준 contract** — 새 개발자 합류 시 자연스럽게 규약 학습.

### Negative
- **사용자 일부 기대치 충돌** — "왜 PER 낮은 순이 default 가 아닌가?" 도움말로 답변 필요.
- **빌트인 score 없음** — Magic Formula 등 학술 팩터를 한 클릭으로 적용 불가. M2 Factor Lab 으로 위임.
- **CI 게이트의 false positive** — "추천" 단어가 코드 주석에 있어도 빌드 실패 가능. 정밀한 path 분리 필요.

### Neutral / Unknown
- **D2.2 의 한국 관행 빨강 양봉** — 8 기둥 §2.2 와의 미묘한 충돌. M0 는 사용자 익숙도 우선, M1 재검토.
- **차트 강조 (선택 종목 highlight)** — 의도된 사용자 interaction → OK. 그러나 자동 강조 (시스템이 알아서 강조) 는 금지.

## Alternatives Considered

- **A. 빨강·녹색 양봉/음봉 완전 금지** — 8 기둥 §2.2 엄격 해석. 그러나 한국 사용자 익숙도 저하. M1 재검토로 균형. **거부**.
- **B. CI 게이트 없이 컨벤션만** — 가볍지만 silent regression 위험. **거부**.
- **C. "추천 위젯" 컴포넌트 존재 + 운영자만 활성화 가능** — 관리 부담 + 미래에 활성화될 위험. 코드베이스에 없는 게 안전. **거부**.
- **D. 광고 영역 허용 (수익화)** — librarian D-3 비조치의견서 패턴상 광고만은 신고 불요로 해석되나, ADR-0006 D3 의 무료 운영 단순성 유지. M2+ 검토. **거부**.

## D9. 어휘 변경 절차 (oracle 리뷰 E2)

`shared/forbidden-words.json` 변경 시 다음 절차 강제:

### D9.1 어휘 **추가** — 가벼움

- PR 로 JSON 갱신 + 양 언어 테스트 케이스 추가
- 추가는 안전 강화 방향 → 별도 ADR 불필요
- M0 release 이후 운영 데이터로 누적 추가 (audit log 기반 보강 — oracle G5)

### D9.2 어휘 **제거** / 화이트리스트 **확장** — 무거움

- **반드시 별도 ADR 발행** (예: `adr-NNNN-forbidden-word-removal-{slug}.md`)
- ADR 에 다음 명시 의무:
  - 제거 또는 화이트리스트 추가의 법적 근거 ([[adr-0006-legal-review]] D9 의 변호사 자문 결과)
  - false positive 누적 통계 (운영 데이터)
  - 제거 후 동등한 보호를 제공하는 대체 메커니즘 (예: scope 별 정책 강화)
- M0 release 전 어휘 제거는 원칙적으로 금지 (보호 약화 방향)

### D9.3 SoT 동기화 검증

- 모든 PR 의 CI 게이트로 `shared/forbidden-words.json` 의 hash 와 Python / TS 가 로드한
  list 의 hash 가 일치하는지 확인 (oracle 리뷰 D1)
- 마일스톤 종료 시 Momus 검토에 다음 항목 추가:
  - SoT JSON 과 코드 구현의 일치 확인
  - 새 페이지·컴포넌트의 `assertClean` 호출 coverage 확인
  - 운영 false positive 통계 검토 → 화이트리스트 보강 또는 scope 재검토

## References

- [[adr-0006-legal-review]] D2, D8 — 본 ADR 의 상위 정책
- librarian 조사 보고서 D-5 — disclaimer 효력 한계 우려
- oracle 리뷰 v1 (2026-05-22) — Critical 6 / High 5 / Medium 8 항목.
  본 ADR 의 D4.5, D4.6, D9 신설 + 어휘 보강이 그 직접 반영.
- [Lightweight Charts — 색상 customization](https://tradingview.github.io/lightweight-charts/) — D8.3 의 default override
- Norma `docs/CONCEPT.md §2.8.1` Display State — UI 의 explicit visibility 패턴 동형
- Tessera `docs/adr/0024-ohif-v3-customization-strategy.md` — UI 컨벤션 강제 패턴 참조
