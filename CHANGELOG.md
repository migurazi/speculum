# Changelog

본 파일은 Speculum 의 사용자 가시 변경 기록입니다.
[Keep a Changelog 1.1.0](https://keepachangelog.com/ko/1.1.0/) 형식과
[SemVer](https://semver.org/lang/ko/) 를 따릅니다.

자세한 commit 단위 history 는 `git log` 또는 GitHub Releases 참조.
M0 진행 상황은 [`docs/M0_PLAN.md`](docs/M0_PLAN.md), 결정 사항은
[`docs/adr/`](docs/adr/) 참조.

---

## [Unreleased]

M0 v0.1.0 작업 중 — pre-release. 본 섹션은 release 시점에 `[0.1.0] - YYYY-MM-DD`
로 promotion.

### Added

- **Frontend MVP 4 뷰** — Screener / Stock Detail / Compare / Watchlist
  완성. 각 뷰가 backend `/api/screen` · `/api/stocks/{code}` ·
  `/api/stocks/compare` · `/api/watchlists` 합류 (T36~T39).
- **Recent Runs 페이지** — `/runs` — Save Run 으로 저장한 snapshot 목록 +
  VersionsDiff 재현 가능 여부 표시 (T40 backlog follow-up).
- **Save Run 버튼** — Screener 결과를 immutable snapshot 으로 freeze
  (ADR-0008 D7). execute 시점의 conditions/selectedFactors/asOf 캡처본 사용
  (oracle review C1/C2).
- **As-of Date Picker** — 일급 UI element. zustand persist + 전역 store
  (T34, ADR-0008 D1.1).
- **SourceAttribution component** — 8 기둥 §2.1 Fidelity 의 UI backbone.
  값 + radix Tooltip (source · formula · asOf) (T33).
- **동의 모달 + Disclaimer Footer** — 모든 페이지 mount (T32, ADR-0006 D2
  + ADR-0007 D2).
- **Backend FastAPI app** — `/api/screen` · `/api/stocks/{code}` ·
  `/api/stocks/search` · `/api/stocks/compare` · `/api/watchlists` ·
  `/api/screener-sets` · `/api/runs` · `/api/runs/{id}/diff` (T24~T28).
- **PostgreSQL 16 schema** — 9 ORM 모델 + 3 Alembic migrations (T13).
- **Data adapters** — pykrx / FDR / DART OpenAPI + ConflictDetector +
  CorpCode mapping (T14~T16).
- **일배치** — KRX 16:30 KST + DART 03:00 KST orchestrators + SAVEPOINT
  per-code transactional integrity (T18, T19).
- **Source Citation 7-tuple** — ADR-0002 D3 의 immutable 4-pillar +
  3-context tuple (T23).
- **금지 어휘 검사** — Python scanner + ESLint rule + FastAPI middleware
  (T29, ADR-0007 D4).
- **CI 게이트 (4/5)** — forbidden words / disclaimer coverage / source
  attribution / PIT bypass. GitHub Actions + GitLab CI mirror (T41).
- **License** — MIT (코드). 데이터는 1차 자료 제공자 라이선스 따름
  ([ADR-0006 D5](docs/adr/adr-0006-legal-review.md)).
- **Playwright E2E** — 16+5 tests (T42-A setup + T42-B 4 뷰 시나리오 +
  /privacy /terms /disclaimer page sanity). chromium / ko-KR / Asia/Seoul.
  CI workflow (`client-e2e.yml`) push/PR + nightly + workflow_dispatch.
- **Backend integration tests** — `server/tests/integration/` — adapter
  실 호출 smoke (pykrx/FDR/DART) + 일배치 e2e (SQLite + Fake adapter +
  alert hook). nightly CI (`integration-nightly.yml`).
- **일배치 dry-run + alert hook** — `KrxDailyBatch` / `DartDailyBatch`
  에 `dry_run` 파라미터 + `BatchAlertHandler` Protocol (NullAlertHandler
  / LoggingAlertHandler). conflict / failure / complete 3 이벤트 (T43-B).
- **개인정보처리방침 + 이용약관 + 면책조항 페이지** — `/privacy`,
  `/terms`, `/disclaimer` 세 페이지 신설 (1차 초안, 변호사 자문 ADR-0019
  반영 release 전 갱신). DisclaimerFooter 실 link (T46 V2).
- **ConsentModal v2** — 개인정보보호법 제22조 별도 동의 — 3 체크박스
  (개인정보 / 국외 이전 / 만 14세 이상) 모두 체크 시 enable. localStorage
  v2 key (v1 사용자 재동의 강제). useConsent JSON ConsentRecord (T46 V2).
- **OSS infra 잔여** — SECURITY.md / CODE_OF_CONDUCT.md / TROUBLESHOOTING.md
  (T44).
- **CI 게이트 (5/5)** — i18n keys 게이트 외 4 종 + source attribution
  file-system 가드. T42 의 `client-e2e.yml` 합류.
- **개발 환경** — `docker-compose.yml` (Postgres 16 dev service +
  optional Adminer profile) + `.env.example` (server + client 통합
  template) (T0 잔여 완성).
- **PriceRecord.trading_value 컬럼** — KRX 거래대금 영구화 schema 준비
  (Alembic 0004). factor pack `volume-turnover:avg-20d` 의 입력 schema
  준비 (T46 V3).

### Changed

- **DART effective_date 보수 정책** — 분기말 → 자본시장법 제160조 신고기한
  (Q1~Q3 = +45일, Q4 = +90일). `_disclosure_deadline` 함수 + ADR-0012
  신설. silent look-ahead bias 0 보장 (T46 V1, Momus M0 review V1).
- **Forbidden words SoT** — `진입` 단독 제거 (phrase `진입 시점` /
  `진입시점` 만 유지) — ADR-0013 신설 + ADR-0007 D9.2 어휘 제거 절차
  준수 (T46 V4). ADR-0007 D4.1/D4.2 본문은 카테고리별 대표 예시 +
  "전체 list 는 SoT (D4.6)" 명시 (T46 V5). `allowed_phrases` 보강
  (`추천 위젯` / `종목 추천을 제공` / `alembic upgrade` 등 — T46 V15).

### Decided (ADR)

14 개 ADR — `docs/adr/` 참조. 핵심:

- **ADR-0001** 가격 보정 정책 — raw + adjusted 양립.
- **ADR-0002** Factor / Fact 데이터 모델 — multi-id ambiguous indicators
  + Source Citation 7-tuple + append-only invariant.
- **ADR-0006** 법률 검토 — M0 무료 운영 한정. 유사투자자문업 미해당.
- **ADR-0007** No Advice UI 규약 — 금지 어휘 강제 + Source Attribution
  의무.
- **ADR-0008** As-of Date Picker 일급 — 모든 historical 쿼리가 as_of 통과.
- **ADR-0009** Corporate Action — 이중 PIT (announced / effective).
- **ADR-0011** Watchlist scope — 폴더 + 메모 + Save Run, 가격 알림 미포함.
- **ADR-0012** (신규, T46 V1) DART effective_date 보수 정책 — 자본시장법
  제160조 신고기한 (분기 +45일 / 사업 +90일).
- **ADR-0013** (신규, T46 V4) Forbidden word `진입` 단독 제거 — phrase
  `진입 시점` / `진입시점` 만 유지. ADR-0007 D9.2 절차 준수.

### Conformance (T45 Momus review)

- **라운드 1** (rev1) — REJECT (Critical 2 / High 4 / Medium 5 / Low 3).
- **라운드 2** (rev2) — **OKAY (squash 가능)**. Critical 0 / High 0,
  W1~W7 모두 Low/M1 backlog.

### Out of scope (M0 미진행 — M1+ 또는 별도 cycle)

- **가격 차트** (lightweight-charts) — Stock Detail / Compare 의 phase B.
- **Factor 평가 pipeline** — FieldProvider wiring 합류 (`volume-turnover`
  + 빌트인 ~30 factor evaluator 실 호출).
- **NextAuth Google OAuth 합류** (T31 잔여) — M0 single-user SYSTEM_USER_ID.
- **DART list.json fetch** — 정확한 rcept_dt (ADR-0012 D6).
- **변호사 자문** (ADR-0019) + KRX 라이선스 답변 (ADR-0018) — release 전
  의무 (AC-L-02).
- **Corporate action batch 통합** — PriceAdjuster service 완성 (T20 ✅
  done), batch (krx_daily) wiring 은 read-time vs write-time 정책 결정
  필요.
- **합병 / 분할 본격 보정** (ADR-0009 D7) — M0 detect-only.

---

## 형식 가이드

각 release 섹션은 다음 카테고리 사용:

- **Added** — 새 기능
- **Changed** — 기존 기능 변경
- **Deprecated** — 곧 제거될 기능
- **Removed** — 제거된 기능
- **Fixed** — 버그 수정
- **Security** — 보안 fix
- **Decided** — ADR / 정책 결정

상위 milestone 단위 (M0 / M1 / ...) 는 `## [Mn.minor.patch]` heading.
