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

**Status (2026-05-28)**: **T0 + T1~T12 의 12 ADR + template 모두 작성 완료**.
T0 scaffold = `client/` + `server/` + README (ko/en) + `docker-compose.yml`
(Postgres 16 dev service) + `.env.example`.

| T | 제목 | 산출물 | 의존 | Status |
|---|---|---|---|---|
| T0 | 프로젝트 scaffold (Next.js + FastAPI + Postgres + Docker compose) | `client/`, `server/`, `docker-compose.yml`, README.md 초안 | - | ✅ done (`docker-compose.yml` + `.env.example` 추가) |
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

→ **Phase 0 종료 = 12 ADR + 1 template + scaffold (T0 ✅ done — docker-compose + .env.example 합류)**.

**M0 release 전 추가 검토 권장 ADR** (Phase 0 의 부산물):
- ADR-0018 KRX 정보데이터시스템 라이선스 답변 반영 (krxdata@krx.co.kr 문의)
- ADR-0019 변호사 자문 결과 반영 (ADR-0006 SUPERSEDED 후보)
- ADR-0013 ETF/우선주/리츠 v0.2 분리의 통계적 한계 명시

### Phase 1 — Data Layer (백엔드 기초)

**Goal**: 1 차 자료 파이프라인이 단단히 작동. ADR-002, ADR-003 구현.

| T | 제목 | 산출물 | 의존 | Status |
|---|---|---|---|---|
| T13 | PostgreSQL 16 schema 초안 (stocks_master, prices_daily, financials, corporate_actions, source_citations) | Alembic migration + ER 다이어그램 | T2, T9 | ✅ done |
| T14 | Source Adapter 인터페이스 + 구현 — FDR | `server/app/adapters/fdr_adapter.py` + tests | T3, T13 | ✅ done |
| T15 | Source Adapter 구현 — pykrx | `server/app/adapters/pykrx_adapter.py` + tests | T3, T13 | ✅ done |
| T16 | Source Adapter 구현 — DART OpenAPI (재무제표 XBRL/HTML 파싱) | `server/app/adapters/dart_adapter.py` + dart_account_mapper + tests | T3, T13 | ✅ done |
| T17 | KRX 영업일 캘린더 entity + 휴장일·반장 처리 | `server/app/services/krx_calendar.py` + tests | T13 | ✅ done (v1.0 = 2024 단년 verified, T17.1 확장 backlog) |
| T18 | 일배치 — KRX 16:30 KST (가격·시가총액·거래량·종목 마스터 변경 감지) | `server/batch/krx_daily.py` + Sentry alert | T14, T15, T17 | ✅ done (SAVEPOINT rollback + ConflictDetector, scheduler 미통합) |
| T19 | 일배치 — DART 03:00 KST (재무제표 + corporate action 공시) | `server/batch/dart_daily.py` + rate limit 정책 | T16, T17 | ✅ done (회사별 SAVEPOINT, scheduler 미통합) |
| T20 | Corporate Action 보정 엔진 (액면분할·무상증자·유상증자·합병·분할·자사주) | `server/app/services/price_adjuster.py` + tests | T9, T18, T19 | ✅ done (PriceAdjuster service + 41 단위 test, ADR-0009 D2 13 action_type 매트릭스. T18 batch 통합은 별도 cycle — read-time vs write-time 보정 정책 결정 필요) |
| T21 | PIT Enforcer service (모든 historical 쿼리에 as_of 강제) | `server/app/services/pit_enforcer.py` + tests + repository 통합 | T13, T20 | ✅ done |
| T22 | Factor Definition 엔티티 + ~30 빌트인 factor pack JSON | `server/builtin-packs/factors/v1.0.json` + 산출식 모듈 | T2, T4, T5, T21 | ✅ done (`speculum-builtin-v1.0.0.json`) |
| T23 | Source Citation 의무 필드 강제 (모든 fact 가 citation 보유) | `server/app/models/source_citation.py` + 빌드 게이트 | T13, T22 | ✅ done |

### Phase 2 — Backend API

**Goal**: 4 뷰가 호출할 API endpoint. ADR-007, ADR-008 구현.

| T | 제목 | 산출물 | 의존 | Status |
|---|---|---|---|---|
| T24 | FastAPI app 부트스트랩 + 인증 미들웨어 (NextAuth JWT 검증) | `server/app/main.py` + auth middleware + tests | T13 | ✅ done |
| T25 | `/api/stocks/search` + `/api/stocks/{code}` | router + service + tests | T22, T23 | ✅ done |
| T26 | `/api/screen` (조건 빌더 입력 + as_of + 결과) | router + service + Screen Run schema 자리 | T22, T8 | ✅ done |
| T27 | `/api/compare` (2~6 종목 multi-fetch) | router + tests | T25 | ✅ done (compare 는 `/api/stocks` multi-fetch 로 client 합성, 별도 endpoint X) |
| T28 | `/api/watchlist` CRUD + `/api/screener_sets` CRUD | router + repository + tests | T13, T24, T11 | ✅ done |
| T29 | 금지 어휘 검사 — request/response 모두 (8 기둥 §2.2) | `server/app/services/forbidden_words.py` + CI 게이트 | T24 | ✅ done |
| T30 | Screen Run snapshot DB schema (자리만, M0 본격 X) | `server/app/models/screen_run.py` + nullable | T13, §2.10 | ✅ done (ORM + Alembic 0003 + `/api/runs`) |

### Phase 3 — Frontend MVP 4 뷰

**Goal**: 4 뷰가 작동. ADR-010 홈 화면 결정 반영.

| T | 제목 | 산출물 | 의존 | Status |
|---|---|---|---|---|
| T31 | Next.js scaffold + Tailwind + shadcn/ui + NextAuth Google | `client/app/`, `client/lib/`, `client/components/ui/` | T0 | ✅ done (shadcn/ui 미도입, Tailwind primitives 직접) |
| T32 | 동의 모달 + footer disclaimer + 금지 어휘 ESLint rule | `client/components/disclaimers/` + `.eslintrc` | T6, T29 | ✅ done (ESLint rule 은 backend CI 게이트로 대체) |
| T33 | Source Attribution component (모든 값 옆 식·출처·기준일) | `client/components/SourceAttribution.tsx` + 빌드 게이트 | T23 | ✅ done |
| T34 | As-of date picker (전역 가능, 일급 UI element) | `client/components/AsOfDatePicker.tsx` + 전역 store | T8 | ✅ done |
| T35 | 홈 화면 — ADR-010 결정대로 | `client/app/(app)/page.tsx` | T10, T34 | ✅ done |
| T36 | Screener 뷰 — 조건 빌더 + 결과 테이블 (TanStack Table 가상화) | `client/app/(app)/screener/` | T26, T33, T34 | ✅ done (가상화는 M0 scope 외, plain table) |
| T37 | Stock Detail 뷰 — 지표 카드 + 가격 차트 (Lightweight Charts) + 재무 시계열 | `client/app/(app)/stock/[code]/` | T25, T33, T34 | ✅ done (Lightweight Charts 는 M0 scope 외, MetricCard + CodeHistory) |
| T38 | Compare 뷰 — 2~6 종목 그리드 + 차트 오버레이 | `client/app/(app)/compare/` | T27, T33 | ✅ done (차트 오버레이 backlog) |
| T39 | Watchlist 뷰 — 폴더 트리 + 메모 + Screen Run 저장 (ADR-011 정책 따름) | `client/app/(app)/watchlist/` | T28, T11 | ✅ done |
| T40 | "Save Run" 버튼 — Screen Run snapshot 생성 (§2.10) | `client/components/SaveRunButton.tsx` + `/api/runs` | T30 | ✅ done (+ Recent Runs page) |

### Phase 4 — Polish + Conformance + Release

**Goal**: 8+2 기둥 conformance 검토. 릴리즈 무결성.

| T | 제목 | 산출물 | 의존 | Status |
|---|---|---|---|---|
| T41 | CI 게이트 — 5 종 (forbidden words, source attribution, disclaimer coverage, PIT bypass, i18n keys) | `.github/workflows/` 또는 `.gitlab-ci.yml` | T29, T32, T33 | ✅ done (i18n keys 는 M1+, 나머지 4 종 + source-attribution file-system) |
| T42 | E2E tests (Playwright) — 4 뷰 기본 시나리오 + 동의 모달 | `client/tests/e2e/` | T36~T40 | ✅ done — 21 tests (A: setup + consent + home / B: 4 뷰 시나리오 + privacy/terms/disclaimer page sanity). chromium / ko-KR / Asia/Seoul. CI `client-e2e.yml` |
| T43 | 백엔드 통합 테스트 — 일배치 dry-run + adapter 충돌 시 alert | `server/tests/integration/` | T18, T19, T22 | ✅ done (A: adapter 실 호출 smoke + nightly CI / B: dry-run + BatchAlertHandler + DB e2e) |
| T44 | OSS infra — README (ko + en) / LICENSE / CHANGELOG / RELEASE_NOTES / CONTRIBUTING / SECURITY / CODE_OF_CONDUCT / TROUBLESHOOTING | repo 루트 | - | ✅ done (RELEASE_NOTES 는 T47 시점 작성) |
| T45 | M0 conformance review — KRX / K-IFRS / DART / 자본시장법 (Momus 의뢰) | `docs/work-orders/m0-conformance-review.md` | T1~T40 완료 | ✅ done — 라운드 1 REJECT → 라운드 2 **OKAY (squash 가능)**. [rev1](work-orders/m0-conformance-review-rev1.md) / [rev2](work-orders/m0-conformance-review-rev2.md) |
| T46 | Conformance review 결과 반영 (필요 시 fix) | ADR-0012 + ADR-0013 + V2 페이지 + V3 schema + V15 SoT + rev2 후속 (W1/W4/W6/W7/V2 CI 가드) | T45 | ✅ done — Critical 2 (V1 DART PIT / V2 ConsentModal) + High 3 (V3/V4/V5) + Medium V15 / V2 잔존 CI 가드 + Low V12/W1/W4/W6/W7 fix. V6 (ADR-0018/0019/AC-L-02) 는 외부 자문 의존 — release blocker (T47 전, squash 와 분리) |
| T47 | Squash merge to develop + tag v0.1.0 + RELEASE_NOTES | - | T46 | pending (T13~T44 squash 는 d7101ac 으로 1차 완료. 본 feature branch (m0-plan-status-update) 의 12 commits squash + tag 는 사용자 명시 동의 + release blocker V6 처리 후) |

---

## 2. Acceptance (M0 종료 조건)

> **구현 현황 (실측 검증 2026-06-18)**. 범례: `[x]` 코드 확인 / `[~]` 부분·계획과 차이 / `[ ]` 코드 없음 / `[blocked]` 외부 의존(키·실데이터·자문·운영). 데이터(§2.2)·운영(§2.5) 다수는 코드는 준비됐으나 실데이터 적재/운영 cycle 에 게이트됨.

### 2.1 기능 (8 항목)

- [~] AC-F-01: Google OAuth 로그인 → 동의 모달 → 진입 — 코드 완비(`client/lib/auth.ts` GoogleProvider, `ConsentModal.tsx`)이나 실 OAuth 키 미설정 시 로그인 불가
- [~] AC-F-02: 종목 검색 250ms — 코드 존재(`routes/stocks.py` /search), 250ms SLA 는 실데이터·실환경 측정 필요
- [~] AC-F-03: Screener 60fps (1만 종목 **가상화**) — **가상화(useVirtualizer) 미구현, plain table 만**(plan §3 T36 "M0 scope 외" 명시)
- [x] AC-F-04: Stock Detail 지표카드+가격차트+재무 시계열 — `stock/[code]/page.tsx`, `StockDetail/PriceChart.tsx`(lightweight-charts), `FinancialSeriesTable.tsx`
- [x] AC-F-05: Compare 2~6 종목 + 차트 오버레이 — `compare/page.tsx`, `Compare/CompareChart.tsx`
- [x] AC-F-06: Watchlist 폴더링+추가/제거+메모 — `watchlist/page.tsx`, `routes/watchlists.py`
- [x] AC-F-07: 조건셋 저장/불러오기 — `routes/screener_sets.py`
- [x] AC-F-08: Screen Run snapshot 저장 — `SaveRunButton.tsx`, `routes/runs.py`, `orm/screen_runs.py`

### 2.2 데이터 (6 항목)

- [blocked] AC-D-01: 전 보통주 마스터 (~2,500) — 코드 `batch/krx_daily.py`+`orm/stocks_master.py`, 실 적재 필요
- [blocked] AC-D-02: 분기 재무제표 4분기+ — 코드 `batch/dart_daily.py`+`orm/financials.py`, DART 키+적재 필요
- [blocked] AC-D-03: 가격 시계열 5년+ — 코드 `batch/krx_daily.py`+`orm/prices_daily.py`, 실 적재 필요
- [~] AC-D-04: 휴장일 캘린더 정확성 (지난 3년) — `services/krx_calendar.py` 존재하나 **2024 단년만 verified**(T17.1 백로그). "3년" 미충족
- [x] AC-D-05: 모든 fact `effective_date`/`as_of` 보존 — prices/financials/corporate_actions ORM + `pit_enforcer.py`
- [x] AC-D-06: 정정공시 → 새 record(기존 변경 X) — `financials.py:superseded_by` chain + append-only trigger(migration 0007)

### 2.3 10 기둥 conformance (10 항목) — 전부 코드 확인

- [x] AC-P-01 (Fidelity): 값 hover → 식·출처·기준일 — `SourceAttribution.tsx` + `tools/check_source_attribution.py`
- [x] AC-P-02 (No Advice): 금지 어휘 0 — `services/forbidden_words.py` + middleware + `shared/forbidden-words.json` + CI
- [x] AC-P-03 (Active Inspection): 홈 추천 위젯 0 — `client/app/page.tsx`(QUICK_LINKS 만)
- [x] AC-P-04 (PIT): 모든 historical 쿼리 PIT Enforcer — `pit_enforcer.py` + CI `tools/check_pit_bypass.py`
- [x] AC-P-05 (Open Data): 유료/크롤링 0 — pykrx/FDR/DART/ECOS/KOSIS 공개 API only
- [x] AC-P-06 (KRX-Native): K-IFRS 연결/별도·휴장일 일급 — `orm/financials.py`(ifrs_type) + `krx_calendar.py`
- [x] AC-P-07 (Observation): UI 사람 작성 텍스트 0 — i18n 키만, 공시 링크 metadata
- [x] AC-P-08 (Conformance): KRX·DART·K-IFRS 1차 자료 only
- [x] AC-P-09 (Temporal Continuity): raw/adjusted 토글 — `price_adjuster.py` + `PriceChart.tsx`
- [x] AC-P-10 (Reproducibility): snapshot schema + Save Run — `orm/screen_runs.py` + `SaveRunButton.tsx`

### 2.4 법적 (4 항목)

- [x] AC-L-01: 동의 모달 + footer disclaimer — `ConsentModal.tsx`, `DisclaimerFooter.tsx` + CI `check_disclaimer_coverage.py`
- [blocked] AC-L-02: ADR-006 법률 자문 반영 — ADR-0006 D9 자문 미실시, ADR-0018/0019 파일 부재. 외부 자문 대기
- [x] AC-L-03: 데이터 라이선스 footer 표기 — `DisclaimerFooter.tsx`(DART/KRX/pykrx/FDR)
- [x] AC-L-04: 처리방침 + 이용약관 노출 — `app/privacy/page.tsx`, `app/terms/page.tsx`(1차 초안, 자문 후 확정)

### 2.5 운영 (3 항목)

- [blocked] AC-O-01: 일배치 7일 연속 무결 — 코드 `batch/krx_daily.py`·`dart_daily.py`·`scheduler.py`, 실 운영 cycle 필요
- [~] AC-O-02: Sentry 통합 + 실패 alert — `batch/alerts.py` Protocol + `LoggingAlertHandler` 만, **`SentryAlertHandler` 미구현**(alerts.py 주석 "별도 cycle")
- [x] AC-O-03: Adapter 충돌(FDR vs pykrx) 감지 + 로그 — `services/conflict_detector.py` + `krx_daily.py` cross-check

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

## 6. T47 — Squash + tag v0.1.0 절차

본 섹션은 T47 의 정확한 실행 절차 + release blocker checklist. squash 와
release 가 분리됨에 주의 — squash 는 Momus rev2 OKAY 후 즉시 가능, tag /
publication 은 변호사 자문 (ADR-0019) 후.

### 6.1 사전 조건 (T46 완료 검증)

squash 진입 전 확인:

- [x] Momus rev2 결론 = OKAY ([rev2 보고서](work-orders/m0-conformance-review-rev2.md))
- [x] Critical 0 / High 0 (Momus rev2 §3 분류 요약)
- [x] 모든 actionable Medium/Low 처리 (V12/V15/V2 CI 가드/W1/W4/W6/W7)
- [x] server pytest 902+/902+ pass, ruff clean
- [x] client lint + vitest 199/199 + E2E 21/21 pass
- [x] check_forbidden_words.py 0 위반
- [x] check_disclaimer_coverage.py 0 위반

### 6.2 Squash 절차 (사용자 명시 동의 후)

CLAUDE.md global 정책상 squash merge + push + branch 삭제는 **사용자 명시
동의 의무**.

```bash
# 1. 동기화 — origin develop 의 새 commit 확인.
git fetch origin
git checkout develop && git pull --ff-only

# 2. feature branch 의 commits 를 develop 의 working tree 로 squash.
git merge --squash feature/m0-plan-status-update

# 3. 단일 commit 생성 — 본 cycle 의 squash commit message 는 사용자가 작성
#    또는 본 cycle 의 commit log 종합. M0 release 의 사용자 가시 history.
git commit -m "<사용자 작성 또는 종합>"

# 4. push — 사용자 명시 동의 (대기 정책 [[feedback-no-push-without-explicit-command]]).
git push origin develop

# 5. branch 정리 — 사용자 명시 동의.
git branch -D feature/m0-plan-status-update
```

### 6.3 Release blocker checklist (tag v0.1.0 전 의무)

본 항목들은 **squash 후 develop branch 에서 처리** — squash 와 release 가
분리됨에 주의.

- [ ] **ADR-0018 신설** — KRX 정보데이터시스템 라이선스 답변 반영
  (krxdata@krx.co.kr 문의 결과).
- [ ] **ADR-0019 신설** — 변호사 자문 결과 반영 (ADR-0006 D9 의 4 항목 검증).
- [ ] **AC-L-02 통과** — ADR-0006 법률 자문 결과 반영.
- [ ] **처리방침 / 이용약관 / 면책조항** (현재 1차 초안) 의 변호사 검토 반영
  — `/privacy`, `/terms`, `/disclaimer` 페이지 본문 갱신.
- [ ] (선택) Momus 라운드 3 — release blocker 처리 후 최종 검증.

### 6.4 Tag + RELEASE_NOTES (release 시점)

```bash
# 1. RELEASE_NOTES.md 작성 — CHANGELOG.md Unreleased 의 항목을 0.1.0 으로
#    promote (Keep a Changelog 형식).
# 2. tag.
git tag -a v0.1.0 -m "M0 v0.1.0 — MVP 4 뷰 + 1차 자료 파이프라인"
git push origin v0.1.0
# 3. GitHub Release / GitLab Release 페이지 — RELEASE_NOTES.md 본문 복사.
```

Pre-release 옵션 — release blocker 처리 전 사용자 ~5 명 검증 필요 시
`v0.1.0-rc.1` 태그로 사전 공개:

```bash
git tag -a v0.1.0-rc.1 -m "M0 v0.1.0 release candidate 1"
git push origin v0.1.0-rc.1
```

### 6.5 Tag 직후 — M1 plan 작성 진입

tag v0.1.0 publication 후 M1 plan 작성 진입 — §7 (M1 진입 전 검증 사항).

---

## 7. M1 진입 전 검증 사항 (M0 종료 후)

M0 가 release 된 뒤 M1 진입 전:

- [ ] 7 일 운영 통계 — 일배치 무결, 평균 응답 시간, Sentry 에러 0
- [ ] 첫 사용자 ~5 명의 feedback (소규모 공개 시작)
- [ ] M1 plan 작성 (Sector / Market Overview / PIT 토글 / ECOS 도입)
- [ ] CONCEPT.md 업데이트 (M0 경험 반영)
