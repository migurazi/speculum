# M0 Conformance Review (2026-05-28)

> 사용자 메모리 [[tessera-milestone-conformance-review]] 의 동형 정책 — 매
> 마일스톤 squash 직전 Momus 의 전방위 표준 검토. Speculum 의 기둥 §2.8
> Conformance to Standards 의 process implementation.

| | |
|---|---|
| **상태** | 라운드 2 완료 — **OKAY (squash 가능)** |
| **마일스톤** | M0 v0.1.0 — MVP 4 뷰 + 1차 자료 파이프라인 |
| **선행** | T0~T44 완료 + T46 fix (V1/V2/V3/V4/V5/V15) |
| **차단** | T47 (squash + tag v0.1.0) — release blocker (V6 외부 자문) 는 squash 후 develop 에서 처리 |
| **라운드 1 보고서** | [m0-conformance-review-rev1.md](m0-conformance-review-rev1.md) — REJECT (Critical 2 / High 4) |
| **라운드 2 보고서** | [m0-conformance-review-rev2.md](m0-conformance-review-rev2.md) — OKAY |

## 1. 검토 범위

본 마일스톤의 산출물 + M1 진입 전 의도된 한계 + 핵심 문서의 전방위 검토.

### 1.1 산출물 (검토 대상)

- **ADR-0001~0011 + template** (`docs/adr/`) — 12 결정 + 1 template
- **데이터 모델** (`server/app/db/orm/`, Alembic `versions/0001~0003`)
- **Adapter 계층** (`server/app/adapters/{pykrx,fdr,dart}_adapter.py` + `base.py`)
- **PIT enforcement** (`server/app/services/pit_enforcer.py`)
- **Corporate action 보정** (`server/app/services/price_adjuster.py`)
- **Factor pack** (`server/builtin-packs/factors/speculum-builtin-v1.0.0.json`)
- **일배치** (`server/batch/{krx,dart}_daily.py` + `alerts.py`)
- **Backend API** (`server/app/api/routes/` — stocks / screen / watchlists / runs / screener_sets)
- **Source Citation invariant** (`server/app/models/source_citation.py`)
- **Forbidden words** (`server/app/services/forbidden_words.py` + middleware + CI gate)
- **Client 4 뷰** (`client/app/{screener,stock,compare,watchlist,runs}/`)
- **CI 게이트 5 종** (`.github/workflows/{check-forbidden-words,disclaimer-coverage,source-attribution,pit-bypass,server-ci,client-ci,client-e2e,integration-nightly}.yml`)
- **OSS infra** (LICENSE / CHANGELOG / CONTRIBUTING / SECURITY / CODE_OF_CONDUCT / TROUBLESHOOTING / README ko/en)
- **E2E** (`client/tests/e2e/` — Playwright 16 tests)
- **개발 환경** (`docker-compose.yml` + `.env.example`)

### 1.2 알려진 의도된 한계 (M1+ 진입)

본 항목은 review 대상 외 — M0 scope 결정 사항. 발견 시 "intended" 분류.

- **T17.1**: KRX 캘린더 verified 범위 = 2024 단년 (2020~2027 확장은 별도 work-order).
- **T20 batch 통합**: PriceAdjuster service 완성 + 단위 test 41 개 통과. T18 batch (`krx_daily.py`) 의 `close_adjusted = close_raw` placeholder 는 별도 cycle — read-time 보정 vs write-time backfill 정책 결정 필요.
- **T35 NextAuth OAuth**: M0 = single-user SYSTEM_USER_ID. Google OAuth 실 통합은 별도 cycle.
- **합병 / 분할 보정**: ADR-0009 D7 — M0 detect-only, 본격 시계열 통합 M1.
- **시장 통계 (`/api/market-overview`)**: ADR-0010 D2.2 — M0 = 빈 placeholder, M1 본격.
- **lightweight-charts**: T37/T38 — M0 scope 외 (M1 가격 차트).
- **TanStack Table 가상화**: T36 — M0 = plain table (1만 종목 가상화 backlog).
- **Sentry 통합**: AC-O-02 — BatchAlertHandler protocol 도입 완료, SentryAlertHandler 합류는 별도 cycle.
- **Adr-0018/0019 후속**: KRX 라이선스 답변 + 변호사 자문 결과 — M0 release 전 별도 처리.

## 2. 검토 기준

| 기준 | 출처 | 우선순위 | M0 관련성 |
|---|---|---|---|
| **KRX 시장 운영 규정** | 한국거래소 규정집 | H | KOSPI/KOSDAQ 분류, 영업일 캘린더, 권리락 일자, 종목코드 |
| **K-IFRS 회계기준** | 금융감독원 회계기준원 | H | 연결/별도 dual variant (ADR-0005), 결산월 일급 |
| **DART 공시 양식 / XBRL 표준** | DART 시스템 매뉴얼 | H | dart_account_mapper, fnlttSinglAcntAll 응답 schema, rcept_dt PIT |
| **자본시장법 + 시행령** (특히 유사투자자문업) | 법제처 국가법령정보센터 | H | 금지 어휘 (ADR-0007 D4), 동의 모달 (ADR-0006), No Advice (§2.2) |
| **개인정보보호법** | 개인정보보호위원회 | H | NextAuth Google email + Watchlist 데이터 (M0 single-user 한정 평가) |
| **KSIC 한국표준산업분류** | 통계청 | M | M0 scope 외 — Sector view (M2). KRX 업종분류만 1차 |
| **한국은행 ECOS 데이터 정의** | ECOS 매뉴얼 | M | M1+ — 본 M0 review 대상 외 |
| **10 기둥 (CONCEPT §2)** | `docs/CONCEPT.md` | H | 전 항목 |
| **자매 프로젝트 ADR 일관성** | Tessera/Norma ADR | M | factor_pack content_hash / canonical schema / PIT 패턴 |

## 3. 10 기둥 acceptance 매핑

M0_PLAN §2.3 의 AC-P-01 ~ AC-P-10 의 코드/문서 합치 검증 — Momus 가 PASS / FAIL / 경고 판정.

| AC | 기둥 | M0 implementation | 검증 위치 |
|---|---|---|---|
| AC-P-01 | §2.1 Fidelity | SourceAttribution component + Source Citation 7-tuple invariant | `client/components/SourceAttribution.tsx`, `server/app/models/source_citation.py`, `tools/check_source_attribution.py` |
| AC-P-02 | §2.2 No Advice | 금지 어휘 검사 (request/response middleware + CI gate + ESLint rule) | `server/app/services/forbidden_words.py`, `tools/check_forbidden_words.py`, `.github/workflows/check-forbidden-words.yml` |
| AC-P-03 | §2.3 Active Inspection | 홈 화면 추천 위젯 0 (Watchlist placeholder + 빠른 시작 + 가이드) | `client/app/page.tsx`, `client/tests/e2e/home.spec.ts` D case |
| AC-P-04 | §2.4 PIT | PIT Enforcer + repository 통합 + PIT bypass CI gate | `server/app/services/pit_enforcer.py`, `tools/check_pit_bypass.py` |
| AC-P-05 | §2.5 Open Data | 유료 데이터 의존 0 (pykrx + FDR + DART OpenAPI + ECOS M1) | `server/app/adapters/`, `server/pyproject.toml` |
| AC-P-06 | §2.6 KRX-Native | K-IFRS 연결/별도 명시, 결산월 일급, 휴장일 일급 | ADR-0005, `server/app/services/krx_calendar.py`, `StockMaster.fiscal_month` |
| AC-P-07 | §2.7 Observation | UI 에 사람 작성 텍스트 0 (공시 link 는 metadata) | 4 뷰 모두 |
| AC-P-08 | §2.8 Conformance | KRX·DART·K-IFRS 1차 자료 only | adapter 계층, ADR-0003 |
| AC-P-09 | §2.9 Temporal Continuity | PriceAdjuster + corporate action raw/adjusted | `server/app/services/price_adjuster.py` (batch 통합 M1) |
| AC-P-10 | §2.10 Reproducibility | Screen Run snapshot + Save Run + factor_pack content_hash | `server/app/services/screen_run.py`, `client/components/SaveRunButton.tsx` |

## 4. 분류 요약 (라운드 1, 2026-05-28)

| 등급 | 개수 | 처리 상태 |
|---|---|---|
| **Critical** | 2 | **2 / 2** (V1 + V2 fix) |
| **High** | 4 | **3 / 4** (V3 + V4 + V5 fix; V6 외부 자문 의존) |
| **Medium** | 5 | 0 / 5 |
| **Low** | 3 | 0 / 3 |
| **합** | 14 | **4 / 14** |

### 라운드 1 후 신규 발견 (cycle 도중 자체 발견)

- ✅ **V15** (Medium, fix 완료): `tools/check_forbidden_words.py` 의
  false-positive 6 건 — disclaimer 본문 메타사용 + `alembic upgrade` 명령 +
  playwright-report artifact. **fix**: `allowed_phrases` 에 `"추천 위젯"` /
  `"추천을 제공하지"` / `"종목 추천을 제공"` / `"alembic upgrade"` /
  `"alembic downgrade"` 추가 + `_DEFAULT_EXCLUDES` 에 `**/playwright-report/**`
  + `**/test-results/**` 추가. 검사 0 위반.

**Critical 2 개 fix 완료** — Squash 차단 해제. High 4 개는 release 차단 (T47 전
의무). Medium/Low 는 M1 backlog 또는 후속 cycle.

- V1 (DART PIT) — T46 cycle 에서 fix. ADR-0012 신설 + `_disclosure_deadline`
  도입 (신고기한 보수 정책). 35 단위 test 통과.
- V2 (ConsentModal + privacy 페이지) — T46 cycle 에서 fix. 3 체크박스 + 3
  페이지 + DisclaimerFooter 실 link. 21 E2E test 통과.

- **Critical** — squash 차단. 표준 정면 위반 또는 법적 위험.
- **High** — squash 가능하나 다음 마일스톤 진입 전 처리.
- **Medium** — 추후 마일스톤.
- **Low** — trivial.

## 5. 항목 형식 (Momus 보고서가 따를 형식)

각 발견 항목은 다음 형식:

```
### V{n}. <한 줄 제목> ({등급})

**위치**: <파일 경로:line> 또는 <문서 §>
**표준**: <어느 표준의 어느 조항>

문제:
- (구체)

권장:
- (구체)
- **ADR-NNNN 신설** (있으면)
```

## 6. 진행 단계

1. **라운드 1**: Momus (Opus, read-only) 위임 → 발견 리스트 (본 work-order §7 에 첨부).
2. **사용자 합의**: 우선순위 + 처리 시점 결정.
3. **T46 fix**: Critical / High 처리 (코드 + ADR + 문서).
4. **라운드 2**: 재검토 (필요 시).
5. **(필요 시) 라운드 3**.
6. **squash 가능 판정**: T47 진입.

## 7. 라운드 1 발견 (Momus 보고서)

상세는 [m0-conformance-review-rev1.md](m0-conformance-review-rev1.md) 별도 파일.

### Squash 차단 (Critical) — T46 의무 — **모두 fix 완료**

- ✅ **V1**: DART `effective_date` rcept_dt 아닌 분기말 → silent look-ahead bias. **fix**: ADR-0012 신설 + `_disclosure_deadline` (신고기한 보수 정책). Q1~Q3 = +45d / Q4 = +90d. estimated_fields marker 보존.
- ✅ **V2**: ConsentModal 의 개인정보보호법 제22조 별도 동의 누락 + 페이지 미존재. **fix**: 3 체크박스 + `/privacy` `/terms` `/disclaimer` 페이지 + DisclaimerFooter 실 link.

### Release 차단 (High) — T47 전 의무

- ✅ **V3**: factor `volume-turnover` 의 `trading_value_20d_avg` 입력 데이터 부재. **fix (schema 준비)**: PriceRecord + PriceDailyORM + converters + KRX batch 의 `trading_value` 컬럼 추가 + Alembic migration 0004. FieldProvider wiring (factor_evaluator 합류) 은 M1 backlog.
- ✅ **V4**: forbidden words `"진입"` 의 false-positive 폭증 위험. **fix**: ADR-0013 발행 + SoT 의 `진입` 단독 제거.
- ✅ **V5**: ADR-0007 D4.1/D4.2 본문 어휘 list 가 SoT 와 불일치. **fix**: 본문을 카테고리별 대표 예시로 정정 + "전체 list 는 SoT (D4.6)" 명시.
- **V6**: ADR-0018 (KRX 라이선스) / ADR-0019 (변호사 자문) / AC-L-02 미수행 — 외부 자문 의존.

### M1 backlog (Medium / Low)

- **V7**: volume raw/adjusted 분리 미구현 (ADR-0001 D4 amendment 필요).
- **V8**: 정정공시 supersede chain batch 미구현 (AC-D-06 부분 위반).
- **V9**: `close_adjusted` placeholder (intended → Low).
- **V10**: KRX 캘린더 verified 범위 = 2024 단년 (intended → Low).
- **V11**: ConflictDetector 가 DART estimated_fields 미활용.
- **V12**: `assert_clean` ValueError 가 검출 어휘 echo (handler 누락 시 silent 노출).
- **V13**: ADR-0007 D8.3 차트 색상 — M0 차트 미구현 (intended Low).
- **V14**: NavBar 검색창 disabled — AC-F-02 미통과 (Low → Medium 격상 후보).

## 8. 결과 archive

본 work-order 자체 + 라운드별 momus-rev{n}.md (있다면) + 최종 squash 가능 판정 메모.
