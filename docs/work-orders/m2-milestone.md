# Speculum M2 Work Order — v1.0.0

> 작성: 2026-06-01 (M1 종료 직후). ROADMAP §4 + M1 plan OUT→M2 기반.
> 개정: 2026-06-01 rev1 — Momus 검토(C1~C3/M1~M4/m1~m4) 반영.
> 상태: **DRAFT(rev1)** — 상세 task plan 은 M2 착수 시 `.sisyphus/plans/speculum-m2.md` 로 전개.
> 선행: M1 squash + tag `v0.5.0`, M1 release blocker(T65 ECOS 약관 V6 자문) 처리.

---

## 1. 요구사항 요약

M1(확장 뷰 + PIT + 거시지표 표시)에서 **"데이터를 정직하게 비추는"** 토대를 완성했다. M2 는 그 위에 **사용자가 스스로 분석 도구를 만드는** 단계 — Norma 의 "분석법 빌더" 패턴을 주식에 적용한다. 핵심은 ① 사용자 정의 팩터(Factor Lab) ② 멀티유저 전환 ③ universe 확장(ETF/우선주/리츠)이며, 8 기둥(특히 No Advice·Fidelity·Reproducibility)을 **사용자 정의 영역에서도** 유지하는 것이 관건이다.

### 범위 (IN)
- **멀티유저 전환**: M1 의 `SYSTEM_USER_ID` placeholder(`auth.py`) → NextAuth Google OAuth 실 사용자. user-scoped 데이터(watchlist/notes/screener_sets/custom pack) 격리 + **M1 기존 데이터 마이그레이션**(C3).
- **Factor Lab**: Primary(재무제표 원문 필드) → Derived(PER/ROE 등) → Composite(multi-factor) 의 DAG 정의·저장·export. Identity 3-tier(canonical / community / custom). immutable pack hash.
- **Notes**: 종목별 사용자 Markdown 메모.
- **universe 확장**: ETF(기초자산/NAV/괴리율/AUM)·우선주(보통주 할인율/배당수익률)·리츠(임대수익률/NAV 대비) 별도 모듈.
- **Self-identifying export**: Screener 결과 = {조건 hash, 데이터 hash(batch_id), 기준일} JSON 재현(M1 `reproduce`/`snapshot_versions` 재사용 — 검증됨).
- **ECOS factor 입력**: M1 vintage 인프라(표시용) 위에 ECOS 매크로를 factor 입력으로 산출.
- **정정공시 history view**: ROADMAP §4 / CONCEPT §2.4 스코프 — 종목의 effective_date supersede chain 표시(과거 정정 이력).

### 범위 (OUT → M3+)
- 백테스트 엔진 / Portfolio 회계 / 세금 계산 / AI 공시 요약 — ROADMAP §5. No Advice·법적 경계 신중 검토 후 별도.
- community pack **공유 서버**(ROADMAP §5) — M2 는 JSON export/import(수동)까지로 한정.
- 정정공시 supersede-chain 정밀화 backfill — ADR-0009(chain)/ADR-0020(append-only) 소관, M3 deferred.

---

## 2. 핵심 리스크

| # | 리스크 | 완화 |
|---|--------|------|
| R1 **[P0]** | Composite multi-factor 의 **연산 가능 범위**(정규화/percentile/rank)가 곧 No Advice §2.2 회색지대 — schema 에 rank/percentile 추가 시 ADR-0007 D5/D1 의 랭킹 금지를 코드 레벨에서 우회하는 통로 | **누가 정의하느냐(사용자)뿐 아니라 무엇을 정의 가능한가(연산자 집합)를 통제.** Composite 허용 연산자(정규화/percentile/rank 포함 여부)를 **ADR-0022 선결 결정**으로 못 박은 뒤에야 schema 확장(T70b) 착수. 빌트인 composite/weighted factor **0**(자동 CI 게이트, T73) |
| R2 **[P0]** | 멀티유저 전환이 PIT/재현성을 깨거나 user 격리 실패 | fact(가격/재무/매크로)는 **user 무관 공용**. user 소유는 watchlist/notes/screener_sets/custom pack 뿐. **repo-레벨 owner-check** 격리(미들웨어=인증, repo=격리) + **우회 negative test**(타 user 조회 → 404). 저장 run 재현은 user 무관(R2-bis) |
| R2-bis **[P0]** | (oracle: 현 구현에서 **거짓**) M1 마이그레이션이 재현을 깰 위험 — 단 **`user_id` 는 result_hash 입력이 아니다**(`screen_run.py:318` hash=query/as_of/result_codes/data_versions). `computed_by` 컬럼은 존재하지 않음 | system-owned 유지(user_id UPDATE 0). 불변식 = **result_hash byte 불변**(user_id 무관). **`computed_by` 를 hash 입력화 금지**(ADR-0021 D3) |
| R2-ter **[P1]** | 격리 갭(oracle): `SqlScreenRunRepository.save` 가 owner 미검증 overwrite(IDOR) + `users` 테이블/FK 실제 부재(migration 0002 deferred) | screen_runs **append-only 전환**(save owner 거부) + users 테이블/FK 신설(system sentinel row 선행). T69 |
| R3 **[P1]** | community pack import 시 factor identity 충돌(같은 이름·다른 정의). 현 `factor_pack.validate_identity` 는 pack **내부** 중복만 검사 | pack **간**(canonical vs community/custom) 충돌 매핑 **신규 구현**. import 시 명시 매핑 + canonical 우선 + UUID/hash 구분. silent override 금지 (Norma §2.3) |
| R4 **[P1]** | ETF/우선주/리츠 신규 소스의 PIT·출처·1차자료 충족 결여 | adapter/citation 패턴(ADR-0003) 재사용. 각 fact 에 effective_date + SourceCitation. **1차 자료 출처(KRX vs 운용사 공시)는 §2.5 Open Data Sufficiency 로 사전 검증**(§7) |
| R5 **[P1]** | Factor DAG 순환(Composite 자기 참조). M1 `_resolving` 는 **runtime 재진입 탐지**(데이터 1건 평가 시점)지 **정적 DAG 검증 아님** | T70 에 **정적 acyclicity 검증 신규 구현**(데이터 없이 pack 정의만으로 순환 거부). "재사용" 아님 — 신규 |
| R6 **[P2]** | Notes Markdown 렌더 XSS + scope 정책 | 렌더 sanitize(허용 태그 whitelist). **scope 결정**: USER_PRIVATE(검사 skip) vs 공유 시 USER_SHARED(forbidden-words 대상, ADR-0007 D4.5) — T76 명시 |
| R7 **[P2]** | Self-identifying export hash 가 batch_id freeze 와 불일치 | M1 `snapshot_versions`/`reproduce` 의 batch_id + result_hash 를 그대로 포함(재발명 금지 — **재사용 검증됨**) |

---

## 3. 작업 분해 (T68~T86, 초안)

순서 제약: **멀티유저(0) → Factor Lab 선결 ADR(A-0) → Factor Lab(A) → (Notes·universe·ECOS factor·정정history 병렬) → Conformance(Z)**.

### Phase M2-0 — 멀티유저 기반 (선행)
| T | 제목 | 핵심 |
|---|------|------|
| T68 | NextAuth Google OAuth | `auth.py` 의 `SYSTEM_USER_ID` placeholder → 실 user 세션. 인증 미들웨어, 미인증 정책(§7) |
| T69 | user-scoped 격리 + **M1 데이터 마이그레이션** | watchlist/notes/screener_sets/pack `user_id` 실연결 + 권한(본인만). **users 테이블/FK 신설(현재 부재) + save owner 검증 갭 폐쇄(append-only)**. 기존 SYSTEM_USER_ID run system-owned 유지(user_id UPDATE 0 → result_hash byte 불변). ADR-0021 |

### Phase M2-A0 — Factor Lab 선결 결정 (Composite 착수 전 필수)
| T | 제목 | 핵심 |
|---|------|------|
| T70a | **ADR-0022 — Composite 연산자 집합 + No Advice 경계** | Composite 가 허용하는 연산자(정규화 z-score/percentile/rank 포함 여부)를 **결정**. schema `exprOp` 확장 범위를 ADR-0007 D5/D1 랭킹 금지와 함께 못 박음. **이 ADR 없이 T70b 착수 금지**(R1) |

### Phase M2-A — Factor Lab
| T | 제목 | 핵심 |
|---|------|------|
| T70b | Factor DAG schema 확장 + **정적 acyclicity 검증(신규)** | T70a 결정 연산자를 `factor-pack-v1.json` exprOp 에 반영. 데이터 없이 pack 정의만으로 순환 거부(신규, R5). immutable hash 확장 |
| T71 | Factor Lab editor UI | DAG 빌더. 산출식·입력 field·가중치 **항상 visible**(Fidelity). T70b 의존 |
| T72 | Composite multi-factor 평가 | 사용자 정의만, evaluator 확장(M1 derived_factor 패턴). 산출식/가중치 visible |
| T73 | **빌트인 composite 부재 CI 게이트** | 빌트인 pack 에 composite/weighted factor 0 을 자동 검사(ADR-0007 D8.2 의 `<RecommendedStocks>` 검색 게이트 패턴). No Advice 자동 강제(M4) |
| T74 | **Composite 출력 시각 게이트** | M1 T59/`chart-visual-gate.test.tsx` 패턴 복제 — score 출력에 등락색/랭킹 라벨/하이라이트 0 자동 검증(M4) |
| T75 | custom pack export/import + identity 매핑(신규) | community 공유(수동 JSON). pack **간** 충돌 매핑(canonical 우선, 명시 매핑) 신규 구현(R3) |

### Phase M2-B — Notes
| T | 제목 | 핵심 |
|---|------|------|
| T76 | 종목별 Markdown 메모 | user-scoped CRUD. **scope(USER_PRIVATE/SHARED) 명시** + 렌더 sanitize. 시스템 생성 0 |

### Phase M2-C — universe 확장
| T | 제목 | 핵심 |
|---|------|------|
| T77 | ETF 모듈 | 기초자산/NAV/괴리율/AUM. adapter + citation + factor. 1차자료 출처 확정(§7) |
| T78 | 우선주 모듈 | 보통주 대비 할인율/배당수익률 |
| T79 | 리츠 모듈 | 임대수익률/NAV 대비 가격 |

### Phase M2-D — export / ECOS factor / 정정 history
| T | 제목 | 핵심 |
|---|------|------|
| T80 | Screener self-identifying JSON | {조건 hash, 데이터 hash(batch_id), 기준일} — M1 reproduce/snapshot_versions 재사용(검증됨) |
| T81 | ECOS 매크로 factor 입력화 | M1 vintage 인프라 위 연산용 전환. `vintage_date<=as_of` PIT(AC-M1-P-05 연속) |
| T82 | 정정공시 history view | effective_date supersede chain 표시(ADR-0009/0020 chain). ROADMAP §4 / CONCEPT §2.4 |

### Phase M2-Z — Conformance
| T | 제목 | 핵심 |
|---|------|------|
| T83 | M2 conformance review (Momus) | 8 기둥 + 9번째(Reproducibility freeze). **Composite score No Advice 경계 집중**. M1 회귀 0(T66 baseline) |
| T84 | tag v1.0.0 | squash + release |

---

## 4. Acceptance Criteria (M2 종료 조건, 초안)

> **구현 현황 (실측 검증 2026-06-18)**. 범례: `[x]` 코드 확인 / `[~]` 부분·계획과 차이 / `[ ]` 코드 없음 / `[blocked]` 외부 의존. Tasks T68~T84 도 검증함(아래 요약). tag `v1.0.0` 은 실제 부착됨(squash `dbeee96` 계열).

### 기능
- [blocked] AC-M2-F-01: Google OAuth + user 세션 — 코드 골격 완비(`client/lib/auth.ts`, `dependencies/auth.py`, users 테이블 FK migration 0012), **실 Google credential 미주입**(외부 blocker, conformance deferred)
- [x] AC-M2-F-02: Factor Lab DAG 정의·저장·JSON export — `Lab/FactorForm.tsx`, `routes/factor_packs.py`(export), `routes/custom_packs.py`(DB 영속 저장)
- [x] AC-M2-F-03: custom pack import + 3-tier identity + 충돌 명시 매핑 — `services/factor_pack_identity.py`, `Lab/ImportConflictResolver.tsx`(canonical replace 금지)
- [x] AC-M2-F-04: Notes CRUD(user-scoped, Markdown, sanitize) — `routes/notes.py`, `lib/markdown.ts`(DOMPurify), `markdown-xss-chokepoint-gate.test.tsx`
- [~] AC-M2-F-05: ETF/우선주/리츠 뷰 + 전용 factor — security_type 컬럼/CHECK + `SecurityTypeSelector.tsx` + 리츠 reference pack 완료. **ETF NAV/괴리율/AUM 데이터 어댑터 부재**(R4 deferred, conformance 명시)
- [x] AC-M2-F-06: self-identifying JSON export + 재현 — `routes/runs.py:236` export/reproduce, `Runs/ReproduceImport.tsx`
- [x] AC-M2-F-07: 정정공시 history view — `routes/stocks.py:461` + `StockDetail/RestatementHistory.tsx`

### 정합성 (8 기둥) — 자동 게이트 우선 (전부 코드 확인)
- [x] AC-M2-C-01: 빌트인 composite/weighted factor 0 — `factor_pack.py:469` `validate_builtin_no_composite()`(load 시 자동)
- [x] AC-M2-C-02: Composite 출력 시각요소 0 — `lab-visual-gate.test.tsx`(neutral 톤·랭킹 배지 부재)
- [x] AC-M2-C-03: Composite 사용자 정의만 + 산출식·가중치 visible — `Lab/FormulaPreview.tsx`
- [x] AC-M2-C-04: user 격리(우회 403/404) — `test_screen_runs.py:256`, `test_custom_packs.py:215`(IDOR negative test 다수)
- [x] AC-M2-C-05: M1 run 마이그레이션 후 user 무관 재현 byte-동일 — `screen_run.py:318`(user_id hash 입력 아님) + `test_auth_jwt.py:18`
- [x] AC-M2-C-06: Factor DAG 정적 acyclic 저장 거부 — `factor_pack.py:488` `validate_acyclic()`(3색 DFS) `CyclicDependency`
- [x] AC-M2-C-07: ECOS factor 입력 `vintage_date<=as_of` PIT — `db_field_provider.py:905` 이중 PIT
- [x] AC-M2-C-08: Momus M2 review OKAY(M1 회귀 0) — `m2-conformance-review.md`(Critical 0/High 0)

**Tasks T68~T84 요약**: T68 OAuth 골격·T69 users FK+격리·T70a/b ADR-0022+validate_acyclic·T71~T75 Factor Lab(editor/composite/CI 게이트/시각 게이트/import 매핑)·T76 Notes·T77~T79 universe(뷰/CHECK, ETF 데이터 deferred)·T80 self-identifying·T81 ECOS factor·T82 정정 history·T83 Momus OKAY·**T84 tag v1.0.0 부착됨** — 전부 구현. claimed-done-but-no-code 0건.

**요약(2026-06-18)**: M2 전체 코드 구현·테스트 완료. 외부/의도 한계: AC-M2-F-01 OAuth credential(외부), AC-M2-F-05 ETF/리츠 데이터 어댑터(R4 deferred), §8 의 V-M2-* intended-limit(트리거 불가·FK 부재 등) 유지.

---

## 5. 신규/개정 ADR

| ADR | 내용 | 작업 |
|-----|------|------|
| ADR-0021 (신설) | NextAuth 멀티유저 + user-scoped 격리 + **M1 SYSTEM_USER_ID 마이그레이션**(system-owned 유지) | T68/T69 |
| ADR-0022 (신설) | Factor Lab DAG + **Composite 허용 연산자 집합 + No Advice 경계**(정규화/percentile/rank 결정) + immutable hash + identity 3-tier | T70a/T70b |
| ADR-0007 (확장) | D5 Composite score 의 No Advice 경계 — 사용자 정의·visible 의무 + 출력 시각요소 금지(T74) | T73/T74 |
| ADR-0023 (신설) | universe 확장(ETF/우선주/리츠) 데이터 모델 + factor 등록 + 1차자료 출처 | T77~T79 |
| ADR-0003 (확장) | ECOS factor 입력 vintage PIT (표시용→연산용) | T81 |

---

## 6. 검증 절차

1. **단위/통합 테스트**: 각 T 의 AC. 특히 T72(Composite)·T69(user 격리·마이그레이션)·T80(self-identifying 재현)·T73/T74(No Advice 자동 게이트) 필수.
2. **자동 게이트 우선**: No Advice 강제를 Momus 수동(T83)에만 의존하지 않고 CI 게이트(T73 빌트인 composite 부재 + T74 시각요소)로 박는다 — M1 의 forbidden-words/chart-visual-gate 패턴 계승.
3. **회귀 baseline**: M1 conformance(T66 OKAY) + V1/V3 유지.
4. **Momus M2 review** (T83): 8 기둥 + Composite 경계 집중.
5. **E2E**: Factor Lab DAG 빌더 + 멀티유저 로그인/격리 + universe 뷰.

---

## 7. 미해결 기획 질문 (M2 착수 전 결정 — ADR 로 승격)

1. **[ADR-0022 결정 완료 — 2026-06-01]** Composite 연산자 범위: `docs/adr/adr-0022-composite-factor-operators.md` 확정. **허용**(weighted_sum/zscore/percentile/min_max_scale/winsorize — 유니버스-상대는 PIT 분포 freeze + 출력 게이트 동반), **금지**(rank/top_n/sign — 서수=큐레이션 우회, enum 에 처음부터 부재). percentile 은 사실(D2.2), rank 만 D5.2 위반. T70b schema 확장 착수 가능. (T70a 완료)
2. **[ADR-0021 결정완료]** M1 마이그레이션: system-owned 유지(user_id UPDATE 0, system sentinel row + FK). 불변식 = result_hash byte 불변(user_id 는 hash 무관 — `computed_by` 컬럼 없음, 용어 교정). 운영 DB 한정, dev/demo 비대상.
3. **[ADR-0021 결정완료]** 미인증 정책: 공용 fact(가격/재무/매크로/screen 평가/overview/detail/compare) 익명 허용(§2.3), user-scoped(watchlist/notes/pack/Save Run) 인증. **screen 에 인증 금지**(§2.3). 현 라우트 분리와 일치.
4. **[ADR-0023] universe 1차 자료**: ETF NAV/괴리율·리츠 임대수익률의 출처(KRX vs 운용사 공시) — §2.5 Open Data Sufficiency 검증.
5. **[ADR-0007 D4.5] Notes scope**: USER_PRIVATE(검사 skip)만? 공유 가능 시 USER_SHARED(forbidden-words 대상)?
6. **Sector view 관계**: CONCEPT §2.6 "Sector view M2 도입 시 결정" — universe 확장(ETF/리츠)이 KSIC/sector 분류에 미치는 영향.
8. **community 공유 범위**: M2 는 JSON export/import(수동)까지(ROADMAP §5 와 정합). 공유 서버는 M3+.

---

## 8. M2 intended-limits (T83 conformance 후 명시 — `m2-conformance-review.md`)

v1.0.0 squash 시점에 **의도적으로 남긴 한계**. M3+ 에서 닫는다(momus 권고 — ADR 레벨 정직 인정).

- **V-M2-1 screener universe-상대 조건(트리거 불가)**: screen 은 빌트인 pack(composite 0, T73 게이트)만 사용하고 `_compile_conditions` 가 active pack 에 없는 factor 를 400 거부하므로, percentile/zscore factor 를 screen 조건에 넣는 것 자체가 불가능 → **현재 트리거 불가**. custom pack 을 screen 에 주입하는 경로(M3+) 도입 시 분포 provider 주입 또는 422 명시 거부 선결.
- **V-M2-5 code_lineage_id FK 부재(Notes/Watchlist)**: stocks_master 미bootstrap 환경 호환을 위해 의도적 FK 미설정(m0 W2 동형). dangling lineage 메모 가능하나 user 격리·재현 무관 — **intended**.
- **AC-M2-F-01 OAuth(T68)**: 외부 Google credential blocker — M2.1/M3.
- **AC-M2-F-02 custom pack 영속화**: stateless export/import JSON 으로 갈음. DB 영속화는 M3+.
- **AC-M2-F-05 ETF/리츠 전용 factor**: NAV/괴리율/AUM·FFO 데이터 어댑터(R4) 의존. 뷰·선택 UI·출처(ETF=KRX) 완료, 데이터 합류 deferred.

**post-v1.0.0 해소(squash 후 수정)**: V-M2-2(off-universe Fidelity → `_OFF_UNIVERSE_TARGET` sentinel) · V-M2-4(security_type DB CHECK constraint, 마이그레이션 0015 + ORM `__table_args__`) · V-M2-3(XSS 게이트 → ESLint `react/no-danger` 격상).
