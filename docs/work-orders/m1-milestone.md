# 작업 지시서: M1 마일스톤 (T48~T67)

> 작성: 2026-05-29 (Prometheus 기획 + Metis 사전분석 + 코드 조사 기반)
> 상위 플랜: `.sisyphus/plans/speculum-m1.md`
> 선행 게이트: `tag v0.1.0` (M0_PLAN §6.5) — 본 지시서 구현은 사용자 명시 동의로 게이트 선행.

---

## 배경 및 목적

M0(4뷰 MVP + KRX/DART 1차 파이프라인)는 코드 완성. M0에서 **"Protocol·schema·stub은 만들고 DB 연결은 미룬 것들"** 을 M1에서 실연결한다. 핵심 목표는 **stub 위에 stub을 쌓지 않고**, 10 기둥(특히 PIT·Reproducibility) 정합성을 유지한 채 4뷰를 실데이터로 살리는 것.

확정 범위: A(FieldProvider 실연결) + B(차트) + C(PIT 정밀화) + D(표면 확장) + E(ECOS 표시용). 제외: NextAuth/멀티유저(M2). 정책: Corporate Action 보정 = read-time + 입력 테이블 append-only.

---

## 현재 상태 (코드 조사 결과)

| 영역 | 현재 상태 | 근거 |
|------|-----------|------|
| FieldProvider | Protocol 정의됨, DB wiring 미착수 | `factor_evaluator.py:108`, `stocks.py:219` stub |
| batch_id 추적 | uuid4 생성되나 `source_citations.batch_id`에만 저장. `batch_runs` 테이블 없음 | `krx_daily.py:182`, `dart_daily.py:173`, `orm/source_citations.py:54` |
| data_versions freeze | 11키. batch_id 미포함 | `snapshot_versions.py:57,81` |
| financials/corporate_actions append-only | DB 트리거 없음 (source_citations만 있음) | `alembic/.../20260527_0001:79-95` (citations만) |
| superseded_by | 컬럼 존재, 정정 로직 미구현 (None 하드코딩) | `orm/financials.py:73`, `dart_daily.py:350` |
| estimated_fields | DB 미저장 (메모리 한정) | `dart_adapter.py:255` |
| DART list.json | stub조차 없음 (ADR 주석만) | `dart_adapter.py:249-256, 541-543` |
| 차트 | 라이브러리 미설치 | `client/package.json`, `stock/[code]/page.tsx:9` |
| market-overview | endpoint·view 미구현 | `client/app/page.tsx` 시장통계 stub |
| ECOS | 어댑터 없음 | — |

---

## 변경 계획 (의존성 순서: 0 → A → C → (B→D) → E → Z)

### Phase M1-0 — Reproducibility Foundation (P0, 선행)

**T48 — batch_id를 data_versions에 freeze** (Momus C2/H1/H2/H3 반영)

*T48a. batch_runs 테이블 (SoT 명시 — H1)*
- 신규: `server/app/db/orm/batch_runs.py` + Alembic migration — `batch_runs(id UUID PK, market TEXT, source TEXT, started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, success_count INT, status TEXT)`. `BatchSummary`/`DartBatchSummary` 영속화.
- **SoT 관계**: `batch_runs` = run 메타데이터의 권위 테이블. `source_citations.batch_id`(`orm/source_citations.py:54`)에 **FK 추가** → `batch_runs.id` 참조. 불변식: 모든 `source_citations.batch_id` ∈ `batch_runs.id`. 기존 2-hop 추적(price→citation→batch_id)은 "어느 batch가 이 row 생산" 용도로 유지; `batch_runs`는 "as_of 시점 최신 성공 batch" 쿼리 + run 메타를 추가.
- `krx_daily.py:182`/`dart_daily.py:173`: 배치 종료 시 `batch_runs` INSERT (기존 uuid4 재사용).

*T48b. data_versions 합류 — 순수 함수 비파괴 분리 (C2 해소)*
- `collect_active_policy_versions()`는 **무인자 순수 함수 그대로 유지** (11키, `snapshot_versions.py:60`). 시그니처 변경 금지.
- 신규: `collect_batch_versions(as_of, session) -> Mapping[str,str]` — `{krx_batch_id, dart_batch_id}` 반환. 값 = `status='success' AND started_at::date <= as_of`인 batch_runs의 source별 max(started_at) row id, str(UUID). 없으면 빈 문자열 "".
- **run 생성 경로**(`routes/screen.py`, `routes/runs.py`)만 두 결과를 merge하여 `ScreenRunBuilder.build(data_versions=...)`에 **명시 주입**. `screen_run.py:305`의 `data_versions is None` fallback은 정책-only(11키) 유지 — 테스트/비-run 컨텍스트용.
- `GET /api/policy-versions`(`server/app/api/routes/meta.py:49-59`)는 as_of 컨텍스트 없음 → **정책 버전 11키만 노출, batch_id 제외** (batch_id는 run-scoped이지 meta-scoped 아님 — 명문 결정).
- `SNAPSHOT_SCHEMA_VERSION` "1.0"→"1.1" (`snapshot_versions.py:57`).

*T48c. 재현성 의미 단일 확정 (H2 — AC-M1-P-02 모호성 제거)*
- **재현(reproduction)**: 저장된 snapshot의 frozen batch_id로 재쿼리 → **byte-동일**. fact row를 `citation.batch_id`가 frozen batch_id의 batch_run `started_at` **이하**인 것으로 필터(append-only라 이후 batch의 정정 row 제외). 이 필터를 T50 FieldProvider 재현 모드에 포함. **구현 주의**: `FinancialRecord`는 `batch_id`가 아닌 `citation_id`만 보유(`pit_protocols.py`, `orm/financials.py:67`) → 재현 쿼리에 `financials→source_citations→batch_runs` join 또는 `{citation_id → batch started_at}` 사전 resolve map 추가 필요.
- **새 run**: 현재 최신 active batch 사용 — 다른 연산. 같은 as_of라도 새 배치 성공 시 batch_id가 달라지는 것은 **위반 아님** (snapshot이 재현 단위, freshness는 `diff_versions` banner로 통지).
- **load-bearing 의존 명시 (Momus rev2 High)**: 본 재현 정확성은 다음 3자 상호작용에 의존 — ① T49가 정정 시 원 row의 `superseded_by`를 in-place set(NULL→non-NULL), ② T48c 필터가 successor(후속 batch) row를 제외, ③ `pit_enforcer.py:308-310`의 보수적 분기(`successor is None → active=True`)가 "필터로 사라진 successor"를 가진 원 row를 active로 유지. 이 chain이 byte-동일 재현을 보장. `_is_active_one_hop`을 non-conservative로 바꾸면 재현이 조용히 깨지므로, 이 의존을 코드 주석 + 회귀 테스트로 못박을 것.
- H3 확인: `diff_versions`는 누락 키를 `("", new)`/`(old, "")`로 처리함 (`screen_run.py:243-244` `.get(k,"")`) — 기존 11키 run 하위호환 보장 검증됨(추정 아님).
- **완료 기준**: 저장된 run 재현 시 byte-동일(frozen batch_id 필터). 새 배치 후 동일 run 재현해도 result_hash 불변. freshness diff에 batch_id 변경 노출. **회귀 테스트**: "원 row + 필터로 제외된 successor(후속 batch 정정)" 시나리오에서 원 row가 active로 재현되어 result_hash byte-동일임을 assert.

**T49 — ADR-0020 신설 + append-only 불변식** (Momus C1 해소)

자기모순 주의: ADR-0009 D5(`adr-0009-corporate-action.md:144-145`) 정정 프로토콜은 "새 row INSERT → **옛 row의 `superseded_by` UPDATE**". 정정 row id는 원 row INSERT 시점에 알 수 없으므로(정정은 수개월 후 임의 도착) INSERT-time 설정 불가 — 반드시 사후 UPDATE. 따라서 `source_citations`의 **무조건 차단** 트리거(`20260527_0001:76-95`, RAISE EXCEPTION, 컬럼 분기 없음)를 그대로 복사하면 정정 chain이 DB 레벨에서 막힘 → PIT/Temporal Continuity 위반.

- 신규: `docs/adr/adr-0020-append-only-invariant.md` — 명문 결정: financials/corporate_actions는 **"`superseded_by`의 NULL→non-NULL 단일 전이만 허용, 그 외 모든 컬럼 변경·DELETE 금지"**. 이미 non-NULL인 `superseded_by`의 재변경도 금지(chain 1회성). `source_citations`는 `superseded_by` 자체가 없어 무조건 차단 유지(`adr-0002:49` Layer 3, 성격 다름 — 복사하지 말 것).
- Alembic migration: `financials`, `corporate_actions`에 PG `BEFORE UPDATE` **조건부** 트리거:
  ```sql
  -- NULL→non-NULL superseded_by 단일 전이 외 모든 UPDATE 차단
  IF NOT (OLD.superseded_by IS NULL AND NEW.superseded_by IS NOT NULL
          AND ROW(NEW.*) IS NOT DISTINCT FROM ROW(OLD.*) EXCEPT superseded_by)
  THEN RAISE EXCEPTION '... append-only: superseded_by NULL→set 외 변경 금지 (ADR-0020)';
  ```
  (정확한 "그 외 컬럼 불변" 비교는 컬럼 명시 비교로 구현 — `ROW(...) EXCEPT`는 의사코드). BEFORE DELETE 트리거도 추가(무조건 차단).
- repository: `sql_repositories.py`에 `update_superseded_by(record_id, successor_id)` 메서드 신설 — 유일하게 허용된 UPDATE 경로. `dart_daily.py:350`의 `superseded_by=None` 하드코딩 정정 로직을 별도 cycle(정정공시 처리)로 분리하되, 본 메서드를 그 진입점으로.
- SQLite(테스트): 트리거 미지원 → repository layer에서 동일 불변식 검증(non-superseded_by UPDATE/DELETE 거부).
- **완료 기준**: financials/corporate_actions에 (a) 비-superseded_by UPDATE 차단 (b) DELETE 차단 (c) `superseded_by` NULL→set 1회 허용 — 3종 트리거 테스트 통과.

### Phase M1-A — FieldProvider 실연결

**T50 — FieldProvider 구현체 wiring**
- 신규: `server/app/services/field_provider_impl.py` (또는 repositories 내) — `FieldProvider` Protocol(`factor_evaluator.py:108`) 구현. PriceRepository + FinancialRepository 주입.
- `get_scalar`/`get_quarterly_series`가 **반드시** `pit_enforcer.latest_active_by_key`(`pit_enforcer.py:360`, 정정공시 chain) + `DEFAULT_CALENDAR`(KRX 영업일) 통과.
- `stocks.py:219-264` stub 제거 → 실제 `FactorEvaluator.evaluate(as_of=...)` 호출.
- **완료 기준**: 4뷰가 precomputed stub 0. 정정공시 발생 종목에서 active row만 사용 (stale 0).

**T51 — volume-turnover/batch 필드 활성**
- `trading_value_20d_avg` 등: KRX 캘린더 기준 **20영업일** 집계(단순 row count 금지). 상장<20일·거래정지 종목 → N/A(partial 금지, `factor_evaluator.py:27` strict 정책 일관). trading_value는 raw 가격 기반(보정 대상 아님) 명세.
- **완료 기준**: window 정확성 테스트 (휴장일·거래정지·상장초기 edge).

**T52 — FieldProvider 재현성·정확성 AC**
- 테스트: factor 값 byte-동일(동일 as_of+batch_id) + 20일 window 정확성.

### Phase M1-C — PIT 정밀화

**T53 — DART list.json fetch 어댑터** (Momus M2 — rate limit 구체화)
- `dart_adapter.py`: `fetch_disclosure_list(corp_code, bgn_de, end_de)` 신규 — DART `list.json` endpoint로 정확한 `rcept_dt` 취득 (list.json은 회사·기간당 다건 반환이라 호출 수 = 회사수, 분기수 아님).
- **rate limit 정책 (일 10,000)**: (a) **backfill** = 1회성 스크립트, 회사 단위 chunk + 일일 한도 내 분할(`batch_runs`에 진행상황 기록, 중단 시 resume). (b) **증분** = 일배치(`dart_daily.py`)가 직전 batch_run 이후 신규/정정 공시만 list.json 조회. 두 경로 명시 분리.

**T54 — effective_date 정밀화 (estimated marker 제거)** (Momus M1/M3 — DB 표현 확정)
- **DB 표현 결정**: `financials`에 `effective_date_precise BOOLEAN NOT NULL DEFAULT false` 컬럼 추가 (Alembic migration). True=실제 rcept_dt, False=`_disclosure_deadline()` 보수추정. (별도 marker 테이블·estimated_fields 영속화 대안은 기각 — 단일 플래그가 쿼리·disclaimer 단순.)
- **ADR-0012 D6 supersession (Momus rev2 Medium)**: ADR-0012 D3/D6은 `estimated_fields` empty-vs-`frozen({"effective_date"})` marker 방식을 명령. 본 T54는 `effective_date_precise BOOLEAN`으로 대체하므로 **ADR-0012 minor revision** 동반(D6의 marker 방식 → 컬럼 방식 supersede 명문화, 존재하지 않는 `docs/work-orders/dart-rcept-dt-precision.md` 참조도 본 T53/T54로 대체 명시). disclaimer 위치는 `/disclaimer` §3.
- 정밀화 = **supersede chain(새 row, effective_date_precise=true)** + 원 row `superseded_by` set(T49 허용 전이). UPDATE 금지.
- **정밀→보수 회귀 금지 메커니즘 (M3)**: 정밀 row INSERT 전, `(code_lineage_id, fiscal_period, account, ifrs_type)`의 active row(`pit_enforcer.latest_active_by_key`)가 이미 `effective_date_precise=true`면 보수추정 row INSERT skip. (financials는 `fiscal_year`/`fiscal_quarter` 컬럼 없음 — `fiscal_period` String "2024Q1" + row당 `account`+`ifrs_type`, `orm/financials.py:60-66`. 한 fiscal_period = 다수 account row이므로 account/ifrs_type 미포함 시 판정 모호.) 회귀 판정 = active row의 플래그 조회.
- 커버리지 측정(정밀/전체 비율) + 혼재 시 disclaimer(ADR-0012 D5 확장).
- **완료 기준**: `effective_date<=as_of` 위반 0. 정밀값 ≤ 보수추정값(early disclosure, 늦으면 V1 회귀). 정밀화된 종목은 이후 fetch 실패에도 보수값으로 회귀 0. run 재현 시 byte-동일(T48c frozen batch_id 필터).

### Phase M1-B — 차트 UX

**T56 — lightweight-charts 도입**
- `client/package.json` 의존성 + 공용 차트 컴포넌트. as_of별 재보정 호출(클라 캐시 금지 — 보정은 as_of 의존).

**T57 — Stock Detail 가격차트 + 재무 시계열 표**
- `stock/[code]/page.tsx:9` stub 제거. raw/adjusted 토글 + ▾ 마커(ADR-0001 D6). **AC-F-04 완성**.

**T58 — Compare 차트 오버레이**
- 종목별 `adj_policy` 버전 tooltip(ADR-0001 D6). **AC-F-05 완성**.

**T59 — 차트 시각 요소 review gate**
- marker/색/랭킹이 Active Inspection·No Advice 위반 0. 사실(corporate action 일자) 외 annotation 금지. 수동 gate + M1 DoD 항목 (`check_forbidden_words.py`는 텍스트만 검사하므로).

**T59 review gate 결과 (2026-06-01, 통과)** — 가격 차트 컴포넌트 2종(PriceChart 캔들, CompareChart 오버레이)의 시각 요소를 8 기둥 §2.2 No Advice / §2.3 Active Inspection 기준으로 검토:

| 항목 | 대상 | 판정 | 근거 |
|------|------|------|------|
| 캔들 색상 | PriceChart | **수정 후 통과** | M0 가 서구 녹색 상승(`#16a34a`)이라 ADR-0007 D8.3 "한국 관행" 명시와 불일치 → 빨강 양봉(`#dc2626`)/파랑 음봉(`#2563eb`)으로 일치화. 색 = 등락 사실(매매 신호 아님). |
| 오버레이 색상 | CompareChart | 통과 | 종목 구분용 중립 6색(`COMPARE_COLORS`) — 등락 의미색(빨강 상승/녹색 하락) 미사용. |
| 가격 정규화 | CompareChart | 통과 | 절대가격 로그축 — 리베이싱(성과 비교) 미채택(Active Inspection 경계 회피, T58 결정). |
| 랭킹/점수/추천 라벨 | 양 차트 | 통과 | 차트에 순위·점수·추천 어휘·marker 0. 범례는 종목명/코드만(해석 텍스트 0). |
| annotation | 양 차트 | 통과 | corporate action 일자 외 annotation 없음(현재 CA marker 미구현 — 추가 시 사실만 허용). |

- **자동 회귀 게이트**: `client/components/__tests__/chart-visual-gate.test.tsx` — 캔들 색 상수(한국 관행) + 서구 녹색 상승색 부재 + 오버레이 중립 팔레트 검증. 텍스트 검사(`no-forbidden-words`)가 못 잡는 시각 요소를 lint/test 로 박음.
- **ADR**: ADR-0007 D8.3 에 M1 결정 명문화.

### Phase M1-D — 표면 확장

**T60 — GET /api/market-overview 신규**
- 집계 통계(평균/중앙값/분포)만 — 섹터 랭킹/추천 리스트 금지(Active Inspection). 집계 PIT 강제(모든 입력 `effective_date<=as_of`).

**T61 — 홈 시장통계 + Sector/Market Overview view**
- `client/app/page.tsx` stub 제거 + 신규 view. B의 차트 컴포넌트 재사용.

### Phase M1-E — ECOS (표시용 + vintage 선설계)

**T62 — ADR-0003 minor revision (`MacroIndicator`에 vintage_date)**
- `MacroIndicator(indicator_id, date, value, unit, vintage_date)`. vintage_date = 발표일 = PIT effective_date. M2 factor 입력 확장 대비.

**T63 — ecos_adapter.py 신규**
- 한국은행 ECOS API 어댑터. Citation 자동 생성. 캘린더 일자 그대로 비교(영업일 snap 미적용 — 매크로는 KRX 영업일 무관).

**T64 — ECOS 매크로 표시**
- Market Overview에 값+출처+기준일만. factor 입력 금지(M1). 해석 텍스트 0(Observation 기둥).

**T65 — ECOS API 약관 원문 확인 → V6 합류**
- release blocker. ADR-0006 D5.5 "공공데이터법 자유이용" 가정 검증 (변호사 자문 범위).

### Phase M1-Z — Conformance

**T66 — M1 conformance review (Momus)** — 10 기둥 + V1/V3 회귀 baseline.
**T67 — i18n keys CI 게이트** (T41에서 M1+로 이연).

---

## 제약 조건

- **하위 호환성**: `SNAPSHOT_SCHEMA_VERSION` bump 시 기존 저장된 run의 data_versions(11키)는 `diff_versions`에서 batch_id 키가 `("", new)`로 표시됨 — 정상 동작 확인. 기존 run의 result_hash는 재계산하지 않음(freeze 약속).
- **PIT**: 모든 신규 쿼리 경로가 PIT Enforcer 통과 (raw query path 0). ECOS는 vintage_date 기준 `as_of < vintage_date` 제외.
- **append-only**: financials/corporate_actions/source_citations UPDATE 0 (T49 트리거).
- **No Advice / Active Inspection / Observation**: 차트·overview·ECOS에 추천/해석/랭킹 0.
- **단일 사용자**: `SYSTEM_USER_ID` 유지 (멀티유저는 M2).

## 완료 기준 (M1 종료)

`.sisyphus/plans/speculum-m1.md` §4 Acceptance Criteria 전체 + Momus M1 review OKAY(V1/V3 회귀 0). 핵심:
- [x] factor 값 동일 (as_of+batch_id)에서 byte-동일 (T52 — `test_db/test_field_provider_reproducibility.py`, factor 값 as_tuple byte-동일 + frozen cutoff 원값 복원)
- [x] 저장된 run **재현**(frozen batch_id 필터) 시 result_hash byte-동일 — 새 배치 성공과 무관. (freshness 변화는 `diff_versions` banner로 별도 통지, 재현 hash 불변) (T48c — `test_db/test_reproduction.py::test_reproduce_run_end_to_end_byte_identical`, matches=True)
- [x] 입력 테이블 UPDATE 0 (DB 레벨) (T49 ADR-0020 — `test_db/test_append_only_invariant.py` 8종 + macro_indicators 0011 무조건 차단 트리거)
- [x] C 정밀화 후 `effective_date<=as_of` 위반 0, 정밀값 ≤ 보수추정값 (T55 — `test_adapters/test_dart_adapter.py::test_precise_disclosure_not_later_than_deadline` + look-ahead 0 검증)
- [x] ECOS 각 값 vintage_date 보존, `as_of<vintage_date` 제외 (T64 — `test_db/test_macro_indicator_repository.py::test_*_future_vintage_excluded` + provisional_to_final 재현)
- [x] 차트·overview 시각 요소 conformance gate 통과 (T59, 2026-06-01 — chart-visual-gate.test.tsx + ADR-0007 D8.3 결정)
- [x] Momus M1 conformance review OKAY (T66, 2026-06-01 — 8 기둥 정합성 + V1(DART PIT)/V3(trading_value) 회귀 0. Critical/High 0. Minor 3: 홈 주석 drift[수정]·ADR-0012 D6 지각공시 명문화[수정]·ECOS vintage 관측근사[disclaimer 고지됨])

## 리스크 (Metis, `.sisyphus/plans/speculum-m1.md` §2 참조)

R1(batch_id 미freeze→재현불가) / R2(UPDATE→재현붕괴) / R3(ECOS vintage) = P0. R4(정정chain/캘린더 회귀) / R5(ECOS 약관) / R6(fetch 실패 비결정성) = P1. R7(차트 시각요소) / R8(overview 비대화) = P2.
