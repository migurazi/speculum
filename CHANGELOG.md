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

### Decided (ADR)

12 개 ADR — `docs/adr/` 참조. 핵심:

- **ADR-0001** 가격 보정 정책 — raw + adjusted 양립.
- **ADR-0002** Factor / Fact 데이터 모델 — multi-id ambiguous indicators
  + Source Citation 7-tuple + append-only invariant.
- **ADR-0006** 법률 검토 — M0 무료 운영 한정. 유사투자자문업 미해당.
- **ADR-0007** No Advice UI 규약 — 금지 어휘 강제 + Source Attribution
  의무.
- **ADR-0008** As-of Date Picker 일급 — 모든 historical 쿼리가 as_of 통과.
- **ADR-0009** Corporate Action — 이중 PIT (announced / effective).
- **ADR-0011** Watchlist scope — 폴더 + 메모 + Save Run, 가격 알림 미포함.

### Out of scope (M0 미진행 — M1+ 또는 별도 cycle)

- 가격 차트 (lightweight-charts) — Stock Detail / Compare 의 phase B.
- Factor 평가 pipeline (T18 evaluator) — backend 결과 codes 는 M0 fixture.
- NextAuth Google OAuth 합류 (T31 잔여).
- Playwright E2E (T42).
- Backend integration test (T43).
- T44 OSS infra 잔여 — SECURITY / CODE_OF_CONDUCT / TROUBLESHOOTING.
- T41 i18n keys 게이트.
- Momus conformance review (T45).

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
