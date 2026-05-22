# Speculum — M0 v0.1.0 작업 계획서

> **Milestone**: M0 v0.1.0 — MVP 4 뷰 + 1차 자료 파이프라인
> **추정**: 3~5 개월
> **상위 문서**: [CONCEPT.md](CONCEPT.md), [ROADMAP.md](ROADMAP.md), [ARCHITECTURE.md](ARCHITECTURE.md)
> **자매 프로젝트 참조**: `etc/norma/docs/M0_PLAN.md`, `etc/tessera/docs/work-orders/`

---

## 0. M0 진입 정의

> **시작 조건**: 본 plan 문서 + CONCEPT v0.1 + ROADMAP + ARCHITECTURE 스켈레톤 작성 완료. ADR-001~ADR-008 (H 우선) 결정 완료.
>
> **종료 조건**: §2 Acceptance 통과. Momus conformance review (KRX/K-IFRS/DART/자본시장법) 통과. Squash merge to develop + tag v0.1.0.

---

## 1. Task 분해

작업 단위는 Tessera 의 work-order 패턴 + Norma 의 T0~T22 패턴을 따름. 각 T 는 squash 단위로 정의.

### Phase 0 — ADR (사전 결정, 코드 작성 전)

**Goal**: M0 진입 전 모든 H 우선순위 ADR 작성. 결정 없이 코드 작성하면 후일 깨짐.

**Status (2026-05-22)**: **T1~T12 의 12 ADR 작성 완료**. T0 (scaffold) 만 남음.

| T | 제목 | 산출물 | 의존 | Status |
|---|---|---|---|---|
| T0 | 프로젝트 scaffold (Next.js + FastAPI + Postgres + Docker compose) | `client/`, `server/`, `docker-compose.yml`, README.md 초안 | - | pending |
| T1 | ADR-0001 가격 보정 정책 | [`docs/adr/adr-0001-price-adjustment.md`](adr/adr-0001-price-adjustment.md) | - | ✅ ACCEPTED |
| T2 | ADR-0002 Factor / Fact 데이터 모델 | [`docs/adr/adr-0002-factor-fact-model.md`](adr/adr-0002-factor-fact-model.md) | - | ✅ ACCEPTED |
| T3 | ADR-0003 Data Source Adapter 패턴 | [`docs/adr/adr-0003-data-source-adapter.md`](adr/adr-0003-data-source-adapter.md) | T2 | ✅ ACCEPTED |
| T4 | ADR-0004 시가총액·EPS·PER 산출 정의 | [`docs/adr/adr-0004-market-cap-eps-per.md`](adr/adr-0004-market-cap-eps-per.md) | T2 | ✅ ACCEPTED |
| T5 | ADR-0005 K-IFRS 연결 vs 별도 | [`docs/adr/adr-0005-ifrs-consolidated.md`](adr/adr-0005-ifrs-consolidated.md) | T2, T4 | ✅ ACCEPTED |
| T6 | ADR-0006 법률 검토 (유사투자자문업 / 데이터 라이선스 / 개인정보) | [`docs/adr/adr-0006-legal-review.md`](adr/adr-0006-legal-review.md) | - | ✅ ACCEPTED (조건부, M0 무료 운영 한정) |
| T7 | ADR-0007 디폴트 UI 규약 (No Advice 강제) | [`docs/adr/adr-0007-default-ui-rules.md`](adr/adr-0007-default-ui-rules.md) | T6 | ✅ ACCEPTED |
| T8 | ADR-0008 As-of date picker 일급 UI element | [`docs/adr/adr-0008-as-of-date.md`](adr/adr-0008-as-of-date.md) | T2 | ✅ ACCEPTED |
| T9 | ADR-0009 Corporate Action 분류 + 보정 정책 | [`docs/adr/adr-0009-corporate-action.md`](adr/adr-0009-corporate-action.md) | T1, T2 | ✅ ACCEPTED |
| T10 | ADR-0010 홈 화면 정체성 | [`docs/adr/adr-0010-home-screen.md`](adr/adr-0010-home-screen.md) | T6, T7 | ✅ ACCEPTED |
| T11 | ADR-0011 Watchlist scope | [`docs/adr/adr-0011-watchlist-scope.md`](adr/adr-0011-watchlist-scope.md) | T6, T7, T8, T9, T10 | ✅ ACCEPTED |
| T12 | Conformance review work-order 템플릿 | [`docs/work-orders/_template-conformance-review.md`](work-orders/_template-conformance-review.md) | - | ✅ DONE |

→ **Phase 0 종료 = 12 ADR + 1 template + scaffold (T0 pending)**.

**M0 release 전 추가 검토 권장 ADR** (Phase 0 의 부산물):
- ADR-0018 KRX 정보데이터시스템 라이선스 답변 반영 (krxdata@krx.co.kr 문의)
- ADR-0019 변호사 자문 결과 반영 (ADR-0006 SUPERSEDED 후보)
- ADR-0013 ETF/우선주/리츠 v0.2 분리의 통계적 한계 명시

### Phase 1 — Data Layer (백엔드 기초)

**Goal**: 1 차 자료 파이프라인이 단단히 작동. ADR-002, ADR-003 구현.

| T | 제목 | 산출물 | 의존 |
|---|---|---|---|
| T13 | PostgreSQL 16 schema 초안 (stocks_master, prices_daily, financials, corporate_actions, source_citations) | Alembic migration + ER 다이어그램 | T2, T9 |
| T14 | Source Adapter 인터페이스 + 구현 — FDR | `server/app/adapters/fdr_adapter.py` + tests | T3, T13 |
| T15 | Source Adapter 구현 — pykrx | `server/app/adapters/pykrx_adapter.py` + tests | T3, T13 |
| T16 | Source Adapter 구현 — DART OpenAPI (재무제표 XBRL/HTML 파싱) | `server/app/adapters/dart_adapter.py` + dart_account_mapper + tests | T3, T13 |
| T17 | KRX 영업일 캘린더 entity + 휴장일·반장 처리 | `server/app/services/krx_calendar.py` + tests | T13 |
| T18 | 일배치 — KRX 16:30 KST (가격·시가총액·거래량·종목 마스터 변경 감지) | `server/batch/krx_daily.py` + Sentry alert | T14, T15, T17 |
| T19 | 일배치 — DART 03:00 KST (재무제표 + corporate action 공시) | `server/batch/dart_daily.py` + rate limit 정책 | T16, T17 |
| T20 | Corporate Action 보정 엔진 (액면분할·무상증자·유상증자·합병·분할·자사주) | `server/app/services/corporate_action.py` + tests | T9, T18, T19 |
| T21 | PIT Enforcer service (모든 historical 쿼리에 as_of 강제) | `server/app/services/pit_enforcer.py` + tests + repository 통합 | T13, T20 |
| T22 | Factor Definition 엔티티 + ~30 빌트인 factor pack JSON | `server/builtin-packs/factors/v1.0.json` + 산출식 모듈 | T2, T4, T5, T21 |
| T23 | Source Citation 의무 필드 강제 (모든 fact 가 citation 보유) | `server/app/models/source_citation.py` + 빌드 게이트 | T13, T22 |

### Phase 2 — Backend API

**Goal**: 4 뷰가 호출할 API endpoint. ADR-007, ADR-008 구현.

| T | 제목 | 산출물 | 의존 |
|---|---|---|---|
| T24 | FastAPI app 부트스트랩 + 인증 미들웨어 (NextAuth JWT 검증) | `server/app/main.py` + auth middleware + tests | T13 |
| T25 | `/api/stocks/search` + `/api/stocks/{code}` | router + service + tests | T22, T23 |
| T26 | `/api/screen` (조건 빌더 입력 + as_of + 결과) | router + service + Screen Run schema 자리 | T22, T8 |
| T27 | `/api/compare` (2~6 종목 multi-fetch) | router + tests | T25 |
| T28 | `/api/watchlist` CRUD + `/api/screener_sets` CRUD | router + repository + tests | T13, T24, T11 |
| T29 | 금지 어휘 검사 — request/response 모두 (8 기둥 §2.2) | `server/app/services/forbidden_words.py` + CI 게이트 | T24 |
| T30 | Screen Run snapshot DB schema (자리만, M0 본격 X) | `server/app/models/screen_run.py` + nullable | T13, §2.10 |

### Phase 3 — Frontend MVP 4 뷰

**Goal**: 4 뷰가 작동. ADR-010 홈 화면 결정 반영.

| T | 제목 | 산출물 | 의존 |
|---|---|---|---|
| T31 | Next.js scaffold + Tailwind + shadcn/ui + NextAuth Google | `client/app/`, `client/lib/`, `client/components/ui/` | T0 |
| T32 | 동의 모달 + footer disclaimer + 금지 어휘 ESLint rule | `client/components/disclaimers/` + `.eslintrc` | T6, T29 |
| T33 | Source Attribution component (모든 값 옆 식·출처·기준일) | `client/components/SourceAttribution.tsx` + 빌드 게이트 | T23 |
| T34 | As-of date picker (전역 가능, 일급 UI element) | `client/components/AsOfDatePicker.tsx` + 전역 store | T8 |
| T35 | 홈 화면 — ADR-010 결정대로 | `client/app/(app)/page.tsx` | T10, T34 |
| T36 | Screener 뷰 — 조건 빌더 + 결과 테이블 (TanStack Table 가상화) | `client/app/(app)/screener/` | T26, T33, T34 |
| T37 | Stock Detail 뷰 — 지표 카드 + 가격 차트 (Lightweight Charts) + 재무 시계열 | `client/app/(app)/stock/[code]/` | T25, T33, T34 |
| T38 | Compare 뷰 — 2~6 종목 그리드 + 차트 오버레이 | `client/app/(app)/compare/` | T27, T33 |
| T39 | Watchlist 뷰 — 폴더 트리 + 메모 + Screen Run 저장 (ADR-011 정책 따름) | `client/app/(app)/watchlist/` | T28, T11 |
| T40 | "Save Run" 버튼 — Screen Run snapshot 생성 (§2.10) | `client/components/SaveRunButton.tsx` + `/api/runs` | T30 |

### Phase 4 — Polish + Conformance + Release

**Goal**: 8+2 기둥 conformance 검토. 릴리즈 무결성.

| T | 제목 | 산출물 | 의존 |
|---|---|---|---|
| T41 | CI 게이트 — 5 종 (forbidden words, source attribution, disclaimer coverage, PIT bypass, i18n keys) | `.github/workflows/` 또는 `.gitlab-ci.yml` | T29, T32, T33 |
| T42 | E2E tests (Playwright) — 4 뷰 기본 시나리오 + 동의 모달 | `client/tests/e2e/` | T36~T40 |
| T43 | 백엔드 통합 테스트 — 일배치 dry-run + adapter 충돌 시 alert | `server/tests/integration/` | T18, T19, T22 |
| T44 | OSS infra — README (ko + en) / LICENSE / CHANGELOG / RELEASE_NOTES / CONTRIBUTING / SECURITY / CODE_OF_CONDUCT / TROUBLESHOOTING | repo 루트 | - |
| T45 | M0 conformance review — KRX / K-IFRS / DART / 자본시장법 (Momus 의뢰) | `docs/work-orders/m0-conformance-review.md` | T1~T40 완료 |
| T46 | Conformance review 결과 반영 (필요 시 fix) | TBD | T45 |
| T47 | Squash merge to develop + tag v0.1.0 + RELEASE_NOTES | - | T46 |

---

## 2. Acceptance (M0 종료 조건)

### 2.1 기능 (8 항목)

- [ ] AC-F-01: Google OAuth 로그인 → 첫 진입 동의 모달 → 메인 진입
- [ ] AC-F-02: 종목 검색 (한글 / 종목코드) 250ms 이내 응답
- [ ] AC-F-03: Screener — 조건 추가/제거 + 결과 60fps (1만 종목 가상화)
- [ ] AC-F-04: Stock Detail — 지표 카드 + 가격 차트 + 재무 시계열 표 (최근 4 분기)
- [ ] AC-F-05: Compare — 2~6 종목 동시 비교 + 차트 오버레이
- [ ] AC-F-06: Watchlist — 폴더링 + 종목 추가/제거 + 메모
- [ ] AC-F-07: 조건셋 저장 / 불러오기
- [ ] AC-F-08: Screen Run snapshot 저장 (§2.10)

### 2.2 데이터 (6 항목)

- [ ] AC-D-01: KOSPI/KOSDAQ 전 보통주 마스터 (~2,500 종목)
- [ ] AC-D-02: 분기 재무제표 4 분기 이상 (~10,000 record)
- [ ] AC-D-03: 가격 시계열 5 년 이상
- [ ] AC-D-04: 휴장일 캘린더 정확성 (지난 3 년 검증)
- [ ] AC-D-05: 모든 fact 에 `effective_date` 또는 `as_of` 보존
- [ ] AC-D-06: 정정공시 → 새 record 추가 (기존 변경 X)

### 2.3 10 기둥 conformance (10 항목)

- [ ] AC-P-01 (Fidelity): 모든 지표 값 hover/inspect → 식·출처·기준일 표시
- [ ] AC-P-02 (No Advice): 금지 어휘 0 (코드 + 콘텐츠 + 이메일)
- [ ] AC-P-03 (Active Inspection): 홈 화면이 추천 위젯 0
- [ ] AC-P-04 (PIT): 모든 historical 쿼리가 PIT Enforcer 통과 (raw query path 없음)
- [ ] AC-P-05 (Open Data): 유료 데이터 의존 0, 네이버/다음 크롤링 0
- [ ] AC-P-06 (KRX-Native): K-IFRS 연결/별도 명시, 결산기 일급, 휴장일 일급
- [ ] AC-P-07 (Observation): UI 에 사람 작성 텍스트 0 (공시 링크는 metadata 만)
- [ ] AC-P-08 (Conformance): KRX·DART·K-IFRS 1차 자료 only, multi-id ambiguous indicators
- [ ] AC-P-09 (Temporal Continuity): corporate action 보정 raw/adjusted 토글
- [ ] AC-P-10 (Reproducibility): Screen Run snapshot DB schema + Save Run 버튼

### 2.4 법적 (4 항목)

- [ ] AC-L-01: 동의 모달 + footer disclaimer 모든 화면
- [ ] AC-L-02: ADR-006 법률 자문 결과 반영
- [ ] AC-L-03: 데이터 라이선스 footer 표기 (DART/KRX 출처)
- [ ] AC-L-04: 개인정보처리방침 + 이용약관 작성·노출

### 2.5 운영 (3 항목)

- [ ] AC-O-01: 일배치 (KRX 16:30 + DART 03:00) 7 일 연속 무결 성공
- [ ] AC-O-02: Sentry 통합 + 일배치 실패 alert
- [ ] AC-O-03: Adapter 충돌 (FDR vs pykrx 값 불일치) 감지 + 로그

---

## 3. 리스크 + 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| DART rate limit 초과 | 일배치 실패 | 분당 throttling + exponential backoff + 키 rotation 대비 |
| DART 양식 회사별 차이 | 재무 지표 정확성 | dart_account_mapper 일급 모듈 + 회사별 override + 미매핑 alert |
| 법률 자문 결과 큰 변경 (예: 유사투자자문업 등록 필요) | 사업 중단 가능 | T6 (법률 자문) 을 Phase 0 에 배치, 코드 작성 전 결정 |
| pykrx / FDR 데이터 충돌 | Fidelity 위반 | 출처 우선순위 ADR (T3) + adapter 별 source citation |
| Look-ahead bias 의 silent regression | 8기둥 §2.4 위반 | PIT Enforcer + CI 게이트 (raw query 차단) |
| Active Inspection vs UX 빈 화면 trade-off | 사용성 / 8기둥 §2.3 충돌 | ADR-010 으로 단단히 결정, Tessera 의 ADR 패턴 |

---

## 4. 도구 / 의존성

### 4.1 외부 데이터 키 / 권한

- [ ] DART OpenAPI 인증키 (https://opendart.fss.or.kr)
- [ ] Google OAuth Client ID / Secret
- [ ] (선택) Sentry 프로젝트, Plausible site

### 4.2 핵심 dependency (예상 버전)

**Frontend** (package.json):
- next ^14
- typescript ^5
- tailwindcss ^3
- @radix-ui/* (shadcn/ui 기반)
- @tanstack/react-query ^5
- @tanstack/react-table ^8
- lightweight-charts ^4
- zustand ^4
- next-auth ^5 (beta)

**Backend** (pyproject.toml):
- python ^3.12
- fastapi ^0.111
- sqlalchemy[asyncio] ^2.0
- alembic ^1.13
- pydantic ^2
- finance-datareader ^0.9
- pykrx ^1.0
- opendartreader ^0.3 또는 직접 httpx + DART API
- httpx ^0.27
- apscheduler ^3.10

---

## 5. 마일스톤 종료 시 의무

자매 프로젝트 정책 일관 — squash 직전 Momus 검토 (T45):

- [ ] KRX 분류·휴장일·corporate action 처리 표준 검토
- [ ] K-IFRS 회계기준 + DART 양식 conformance
- [ ] 자본시장법 유사투자자문업 경계 재검토
- [ ] 개인정보보호법 준수
- [ ] 8+2 기둥 모든 항목 재검토

검토 결과 fix 반영 (T46) 후 release.

---

## 6. M1 진입 전 검증 사항 (M0 종료 후)

M0 가 release 된 뒤 M1 진입 전:

- [ ] 7 일 운영 통계 — 일배치 무결, 평균 응답 시간, Sentry 에러 0
- [ ] 첫 사용자 ~5 명의 feedback (소규모 공개 시작)
- [ ] M1 plan 작성 (Sector / Market Overview / PIT 토글 / ECOS 도입)
- [ ] CONCEPT.md 업데이트 (M0 경험 반영)
