# Speculum M1 Work Plan

> 작성: Prometheus 기획 세션 (2026-05-29) · Metis 사전 분석 반영
> 상태: **DRAFT — 실행 게이트는 `tag v0.1.0` 발행 이후** (M0_PLAN.md §6.5). 본 플랜은 릴리스 대기 중 선작성된 prep 산출물.
> 실행: `/sisyphus` 로 본 플랜 로드

---

## 1. 요구사항 요약

M0(4뷰 MVP + KRX/DART 1차 파이프라인)는 코드 완성. M0에서 **"구조는 만들고 연결은 미룬 것들"** 을 M1에서 실연결한다. 핵심은 stub 위에 stub을 쌓지 않고, 10 기둥(특히 PIT·Reproducibility) 정합성을 유지한 채 4뷰를 실데이터로 살리는 것.

### 범위 (IN)
- **A. FieldProvider 실연결** — `factor_evaluator.py:108` Protocol을 DB에 wiring. 4뷰가 precomputed stub이 아닌 실시간 factor 평가로 동작.
- **B. 차트 UX** — lightweight-charts 도입. Stock Detail 가격차트 + 재무 시계열 표 + Compare 오버레이.
- **C. PIT 정밀화** — DART list.json fetch로 정확한 `rcept_dt` 취득 → `effective_date`의 estimated marker 제거 (ADR-0012 D6).
- **D. 표면 확장** — `GET /api/market-overview` 신규 + 홈 시장통계 + Sector/Market Overview view.
- **E. ECOS (표시용)** — 한국은행 매크로 어댑터 신규. **M1은 표시용**, vintage_date 컬럼은 선설계하여 M2 factor 입력 확장 대비.

### 범위 (OUT → M2)
- NextAuth Google OAuth + 멀티유저 전환 (backend `SYSTEM_USER_ID` 단일 사용자 유지).
- ECOS factor 입력 (vintage 인프라만 M1, 실제 factor 산출은 M2).

### 확정된 정책 결정
| 결정 | 값 | 근거 |
|------|-----|------|
| Corporate Action 보정 시점 | **read-time** (raw 저장 + 쿼리 시 as_of 기준 동적 보정) | `price_adjuster.py:602-684` 이미 as_of-aware. PIT/raw-adjusted 토글(AC-P-09) + 재현성 유리 |
| 입력 테이블 mutability | **append-only** (financials/corporate_actions UPDATE 금지) | read-time 보정 재현성의 필수 조건 |
| C의 정밀화 적용 방식 | **supersede chain (새 row)**, UPDATE 금지 | ADR-0009 D5 chain 재사용, 저장된 run 재실행 시 보정 불변 보장 |
| ECOS 용도 | M1 표시용 + vintage_date 선설계 | PIT 충돌 회피하면서 M2 확장 경로 확보 |

---

## 2. 핵심 리스크 (Metis 분석)

| # | 리스크 | 완화 (작업) |
|---|--------|-------------|
| R1 **[P0]** | A 착수 즉시 batch_id 없는 **재현 불가 run** 누적 → Reproducibility 기둥 붕괴 | T48 (batch_id freeze)을 **M1 0번**으로 선행. `snapshot_versions.py:81` 이미 예약, ADR-0008 D7-bis 근거 |
| R2 **[P0]** | read-time 보정이 입력 UPDATE되면 저장된 run 재실행 시 결과 달라짐 | T49 ADR로 append-only 불변식 명문화. C(T54)는 supersede chain |
| R3 **[P0]** | ECOS 매크로는 사후 개정(잠정→확정). vintage 없으면 PIT 불가 | T62 `MacroIndicator`에 `vintage_date` 선추가 (ADR-0003 minor revision) |
| R4 **[P1]** | FieldProvider가 정정공시 chain·KRX 캘린더 window 미통과 → V1(DART PIT) 회귀 | T50 AC에 `pit_enforcer.latest_active_by_key` + `DEFAULT_CALENDAR` 사용 박기 |
| R5 **[P1]** | ECOS API 이용약관이 "공공데이터법 자유이용"(ADR-0006 D5.5) 가정을 override할 수 있음 | T65 — ECOS 약관 원문 확인을 V6 변호사 자문 범위에 합류 (release blocker) |
| R6 **[P1]** | C의 list.json fetch 실패 시 보수추정으로 회귀 → effective_date가 날짜별로 변동 (비결정성) | T54 — 한 번 정밀화된 종목은 보수추정 회귀 금지. 커버리지 측정 + 혼재 시 disclaimer |
| R7 **[P2]** | 차트 marker/색/랭킹이 Active Inspection·No Advice 위반 (텍스트 검사 `check_forbidden_words.py`로 미포착) | T59 — 시각 요소 수동 review gate를 M1 DoD에 추가 |
| R8 **[P2]** | market-overview가 섹터 랭킹/추천 리스트로 비대화 → Active Inspection 위반 | T60 — 응답을 집계 통계(평균/중앙값/분포)로만 제한 |
| R9 | 20일 window가 거래정지·상장 20일 미만·액면분할 구간에서 왜곡 | T51 — KRX 영업일 카운트, window 부족 시 N/A(partial 금지), trading_value는 raw 정의 |

---

## 3. 작업 분해 (T48~T67)

순서 제약 (Metis): **batch_id freeze → A → C → (B → D) → E**. C·(B/D)는 A 이후 병렬 가능.

### Phase M1-0 — Reproducibility Foundation (선행, 모든 것보다 먼저)
| T | 제목 | 산출물 | 의존 |
|---|------|--------|------|
| T48 | `data_versions`에 `krx_batch_id`+`dart_batch_id` 추가 + `SNAPSHOT_SCHEMA_VERSION`→1.1 | `snapshot_versions.py`, `screen_run.py:307` str 검증 통과 + 재현성 테스트(동일 as_of+batch_id→result_hash byte 동일) | - |
| T49 | ADR-0020 신설 — read-time 보정 입력 테이블 append-only 불변식 | `docs/adr/adr-0020-append-only-invariant.md` (financials/corporate_actions UPDATE 금지, 정밀화는 supersede chain) | T48 |

### Phase M1-A — FieldProvider 실연결
| T | 제목 | 산출물 | 의존 |
|---|------|--------|------|
| T50 | FieldProvider 구현체 wiring | PriceRepository+FinancialRepository → FieldProvider 실구현. `pit_enforcer.latest_active_by_key`(정정공시 chain) + `DEFAULT_CALENDAR`(영업일) 통과. `stocks.py:219` stub 제거 | T48, T49 |
| T51 | volume-turnover/batch 필드 활성 | `trading_value_20d_avg` 등 20영업일 집계. KRX 캘린더 기준 영업일 카운트. 상장<20일·거래정지→N/A(partial 금지). raw 가격 기반 정의 명세 | T50 |
| T52 | FieldProvider 재현성·정확성 AC | factor 값 byte-동일(동일 as_of+batch_id) + 20일 window 정확성 테스트 | T50, T51 |

### Phase M1-C — PIT 정밀화 (A 직후, 회귀 위험으로 통합 검증)
| T | 제목 | 산출물 | 의존 |
|---|------|--------|------|
| T53 | DART list.json fetch 어댑터 | `dart_adapter.py` (현 `:250/:542` stub) — 정확한 `rcept_dt` 취득. rate limit(일 10,000) 고려: backfill 1회성 vs 일배치 증분 구분 | T50 |
| T54 | effective_date 정밀화 (estimated marker 제거) | supersede chain(새 row), UPDATE 금지. fetch 실패 시 정밀→보수 회귀 금지. 커버리지 측정 + 혼재 시 disclaimer(ADR-0012 D5 확장) | T53, T49 |
| T55 | C 무회귀 검증 | PIT Enforcer `effective_date<=as_of` 위반 0. 정밀값 ≤ 보수추정값(early disclosure, 늦으면 V1 회귀). run 재실행 시 result_hash 동일 또는 batch_id diff explicit | T54 |

### Phase M1-B — 차트 UX
| T | 제목 | 산출물 | 의존 |
|---|------|--------|------|
| T56 | lightweight-charts 도입 | `client/package.json` 의존성 + 공용 차트 컴포넌트 (as_of별 재보정 호출, 클라 캐시 금지) | T50 |
| T57 | Stock Detail 가격차트 + 재무 시계열 표 | `stock/[code]/page.tsx:9` stub 제거. raw/adjusted 토글 + ▾ 마커(ADR-0001 D6). **AC-F-04 완성** | T56, T52 |
| T58 | Compare 차트 오버레이 | `CompareGrid` + 오버레이. 종목별 `adj_policy` 버전 tooltip(ADR-0001 D6). **AC-F-05 완성** | T56, T52 |
| T59 | 차트 시각 요소 review gate | marker/색/랭킹이 Active Inspection·No Advice 위반 0 (수동 gate + M1 DoD 항목). 사실(corporate action 일자) 외 annotation 금지 | T57, T58 |

### Phase M1-D — 표면 확장
| T | 제목 | 산출물 | 의존 |
|---|------|--------|------|
| T60 | `GET /api/market-overview` 신규 | 집계 통계(평균/중앙값/분포)만 — 섹터 랭킹/추천 리스트 금지. 집계 PIT 강제(모든 입력 `effective_date<=as_of`) | T52 |
| T61 | 홈 시장통계 + Sector/Market Overview view | `client/app/page.tsx` 시장통계 stub 제거 + 신규 view. B의 차트 컴포넌트 재사용 | T60, T56 |

### Phase M1-E — ECOS (표시용 + vintage 선설계)
| T | 제목 | 산출물 | 의존 |
|---|------|--------|------|
| T62 | ADR-0003 minor revision — `MacroIndicator`에 `vintage_date` 추가 | `docs/adr/adr-0003-data-source-adapter.md` D2 갱신 + schema/migration | - |
| T63 | `ecos_adapter.py` 신규 | 한국은행 ECOS API 어댑터. Citation 자동 생성. 캘린더 일자 그대로 비교(영업일 snap 미적용 — 매크로는 KRX 영업일 무관) | T62 |
| T64 | ECOS 매크로 표시 | Market Overview에 값+출처+기준일만. factor 입력 금지(M1). 해석 텍스트 0(Observation 기둥) | T63, T61 |
| T65 | ECOS API 약관 원문 확인 → V6 합류 | release blocker. ADR-0006 D5.5 "공공데이터법 자유이용" 가정 검증 (변호사 자문 범위) | T63 |

### Phase M1-Z — Conformance
| T | 제목 | 산출물 | 의존 |
|---|------|--------|------|
| T66 | M1 conformance review (Momus) | 10 기둥 정합성 + V1(DART PIT)/V3(trading_value) 회귀 baseline 검증 | T48~T64 |
| T67 | i18n keys CI 게이트 (T41에서 M1+로 이연) | `.github/workflows/` i18n keys 검사 추가 | - |

---

## 4. Acceptance Criteria (M1 종료 조건)

### 기능
- [ ] AC-M1-F-04: Stock Detail 가격차트(raw/adjusted 토글) + 재무 시계열 표 (M0 AC-F-04 완성)
- [ ] AC-M1-F-05: Compare 차트 오버레이 + 정책 버전 tooltip (M0 AC-F-05 완성)
- [ ] AC-M1-F-09: Sector/Market Overview view + 홈 시장통계 (실데이터)
- [ ] AC-M1-F-10: 4뷰가 FieldProvider 실평가로 동작 (precomputed stub 0)

### 재현성·PIT (10 기둥)
- [ ] AC-M1-P-01: factor 값이 동일 (as_of + batch_id)에서 byte-동일
- [ ] AC-M1-P-02: 저장된 Screen Run 재실행 시 result_hash 동일, 또는 `data_versions` diff에 batch_id 변경 explicit 노출
- [ ] AC-M1-P-03: 모든 입력 테이블 append-only (UPDATE 0건 — 정밀화/정정 모두 supersede chain)
- [ ] AC-M1-P-04: C 정밀화 후 `effective_date<=as_of` 위반 0, 정밀값 ≤ 보수추정값
- [ ] AC-M1-P-05: ECOS 각 값에 `vintage_date` 보존, `as_of<vintage_date` 값은 조회 제외
- [ ] AC-M1-P-06: market-overview 집계의 모든 입력이 `effective_date<=as_of`
- [ ] AC-M1-P-07: 20영업일 window = KRX 캘린더 기준, 상장<20일·거래정지 종목 N/A

### 정합성·무회귀
- [ ] AC-M1-C-01: Momus M1 review OKAY (V1/V3 회귀 0)
- [ ] AC-M1-C-02: 차트·overview 시각 요소가 Active Inspection·No Advice 위반 0 (T59 gate 통과)
- [ ] AC-M1-C-03: ECOS 표시에 해석 텍스트 0 (Observation 기둥)

### 법적 (release 의존)
- [ ] AC-M1-L-01: ECOS API 이용약관 변호사 검토 (V6 합류, release blocker)

---

## 5. 신규/개정 ADR

| ADR | 내용 | 작업 |
|-----|------|------|
| ADR-0020 (신설) | read-time 보정 입력 테이블 append-only 불변식 | T49 |
| ADR-0003 (개정) | `MacroIndicator`에 `vintage_date` 추가 | T62 |
| ADR-0012 (확장) | C fetch 혼재 시 effective_date 의미 disclaimer (D5 확장) | T54 |
| ADR-0006 (의존) | ECOS API 약관 검증 → ADR-0019 자문 범위 합류 | T65 |

---

## 6. 검증 절차

1. **단위/통합 테스트**: 각 T의 AC를 pytest로. 특히 T52(재현성), T55(무회귀)는 result_hash 동일성 테스트 필수.
2. **회귀 baseline**: M0 conformance rev2의 V1/V3 fix를 baseline으로 — `docs/work-orders/m0-conformance-review-rev2.md` 참조.
3. **Momus M1 review** (T66): 10 기둥 정합성 최종 검증.
4. **E2E (Playwright)**: 차트 렌더링 + market-overview view 시나리오 추가 (M0 `client-e2e.yml` 확장).

---

## 7. 실행 전 확인 (게이트)

- 본 플랜 실행은 **`tag v0.1.0` 발행 이후** (M0_PLAN.md §6.5). 그 전 = release blocker(V6 변호사 자문) 처리 필요.
- T65(ECOS 약관)는 V6 자문 범위에 미리 합류시켜 M1 끝에서 재대기하지 않도록.
