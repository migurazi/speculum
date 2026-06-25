# Speculum 실데이터 파이프라인 전체 복구 로드맵 (v2)

작성: 2026-06-25 · 기획: prometheus (+metis 사전분석, oracle 5-서브시스템 리뷰, **momus 비판 REJECT 반영 v2**)
브랜치 기준: `feature/as-of-clamp-and-demo-removal`(as_of 클램프+demo제거+검색백엔드검증 커밋 `6c40c21`, push 미실행)

> **v2 변경(momus REJECT 반영)**: ①Phase 3 분할(3a/3b)+precompute producer 정합 ②B1 account FLOW/STOCK/NO_DIFF 분류 결론 ③Phase5↔S1/B1/B4 순환의존+4분기 현실성 ④Phase 0 red→`xfail(strict)` ⑤B4 격상(attributable 데이터 결손)+content_hash 주입경로 ⑥경로 오기 수정(`app/adapters/dart_account_mapper.py`) ⑦S5 종목검색 UI 연결 추가.

---

## 1. 문제 요약

단위 테스트 2729개가 통과함에도 **실데이터로 스크리너가 결과를 못 낸다.** 근본 원인은 테스트가 전부 합성 Fake tautology(producer↔consumer를 같은 손제작 fixture로 각자 검증)이고, 실응답 캡처 fixture 0개·관통 e2e 0개라 레이어 간 계약(어휘·구조·시간)이 검증되지 않은 채 누적된 것. 일부 테스트는 `today=2024` 하드코딩으로 production 실패를 적극 은폐.

## 2. 확정 설계 결정 (locked)

- **D1 — B1 누적값 = 읽기 시 보정.** 원시 누적값 그대로 저장(사실 fidelity 보존), account별 FLOW/STOCK/NO_DIFF 표지, db_field_provider가 FLOW만 차분으로 standalone/TTM 산출. **단 read 경로뿐 아니라 precompute producer(`snapshot_daily.py`)도 동일 경로를 타므로 양쪽 정합 필수**(momus #1).
- **D2 — 순서 = 테스트 계층 먼저.** 골든 fixture + 관통 e2e + 시간-비고정 as_of 계약 테스트를 선행. **단 미수정 가드는 `@pytest.mark.xfail(strict=True)`로 커밋(green CI 유지, 수정 시 xfail 제거가 전환 가드)**(momus #4).

## 3. metis+momus 사전분석 핵심 (계획 반영된 숨은 요구사항)

- **H1 [CRITICAL]** B1 차분은 factor 값→`result_hash` 변경. 현재 provider 산식을 freeze하는 data_versions 키 없음. → 신규 `field_resolution_policy_version`(수동 semver) **+ `field_resolution_policy_hash`(AccountKind registry content_hash 자동)** 2키를 `collect_active_policy_versions`에 추가(14→**16**키), `SNAPSHOT_SCHEMA_VERSION` "1.3"→"1.4". reproduce에 `field_policy_superseded` 진단(`reproduce.py:578-581` `engine_version_superseded` 동형). **B1 산식 변경보다 선행**(momus #5: 주입 경로 명시).
- **H2** flow/stock/no_diff 표지는 `app/adapters/dart_account_mapper.py`의 `_MAPPING`을 `{ifrs_id:(canonical, AccountKind)}`로 확장(`AccountKind ∈ {FLOW, STOCK, NO_DIFF}`). DB 마이그레이션 불요(read-time 메타). registry content_hash가 H1의 `field_resolution_policy_hash`로 주입.
- **H3** 차분 PIT 정합: 직전 분기 vintage가 as_of-active·정정 일관이어야. 비대칭/결손/음수 시 보수적 N/A.
- **H4** 골든 fixture는 `server/tests/fixtures/captured/`(=`**/tests/**` forbidden-words 자동 exclude). 회사명 노출 시 allowed_phrases. 1회 캡처→동결.
- **H5 [격상]** B4: mapper·provider canonical은 `net_income_attributable_to_owners`로 일치하나, **실데이터에 그 account가 결손**(적재분은 `net_income`=ProfitLoss뿐, 확인됨). alert는 가시화일 뿐 **데이터를 생성 못 함** → 핵심 EPS/ROE/PER가 B1 무관하게 N/A. **해결=현재 매퍼로 재적재해 attributable 라인 적재**(DART CFS가 해당 라인 제공 시) → S1 수정 + 재적재에 종속(순환).
- **H6** B2 캘린더는 외부(KRX 공식 휴장일) 의존·**미래 연도 매년 수작업이 영구 부채**(복구 아님) → §8 비-목표 격상 + runbook.
- **H7** S3 신규 필드 Optional+default, `ScreenRunSnapshotOut`(저장본) 불변(result_hash 격리), S4와 묶기.
- **H8** S2는 `success==0`(전 실패/전 skip)→SUCCESS 역버그 존재 → EMPTY status 분리 + `collect_batch_versions` freeze 후보 필터(`snapshot_versions.py:233`) 동시 수정.
- **H9 [momus #1]** precompute producer `snapshot_daily.py:473`이 `DbFieldProvider` 호출 → B1 차분이 snapshot **write**에도 적용. 정책 bump 시 기존 `stock_snapshots` stale → 무효화/재생산 절차 필요.
- **H10 [momus #3]** Phase 5 실적재가 S1/B1/B4 수정에 **종속**이면서 동시에 B1 검증 데이터를 제공하는 닭-달걀. 실데이터 10종목·2분기로 4분기 TTM 차분 산출 불가 → "0건" 재발 위험. → 골든 **합성 4분기** fixture로 B1 검증 분리.

## 4. 의존 그래프 (v2)

```
Phase 0 (골든fixture[라이브1회+합성4분기] + 관통e2e + 계약테스트, xfail-strict)
   │  └ 0.1 캡처가 sentinel·attributable결손·반기누적 케이스 必포함 (S1/B4/B1 가드 substrate)
   ├── Phase 1 (재현성 인프라: field_resolution_policy_version+_hash + AccountKind registry) ── B1·B4 게이트
   │        └── Phase 3a (B1 FLOW 차분 + account 분류 + 합성4분기 테스트)
   │                 └── Phase 3b (PIT vintage 일관 + precompute producer 정합[H9] + 정책 2.0)
   ├── Phase 2 (병렬: S1 sentinel → [B4 재적재 게이트] / B3 시총백필 / B2 캘린더)
   │        └ S1 → B4 재적재 → (attributable 적재) ── B1 factor가 값을 가질 전제(H5)
   └── Phase 4 (병렬: S2 / S3+S4 / S5 검색UI)
            └── Phase 5 (전체 실적재[S1·B4 후] + 검증; 4분기 부족 시 골든합성으로 B1 검증 대체)
```

순환(H10): 실데이터 4분기 확보가 Phase 5에 달렸고 Phase 5는 S1/B4에 종속 → **B1 산식 정확성 검증은 Phase 3a의 골든 합성 4분기로 독립**시키고, Phase 5는 "실데이터로 ≥1 factor 산출"의 통합 확인만 담당.

빠른 실결과 = Phase 2의 **B3(시총 백필, 누적·attributable 무관)** 완료 시점.

---

## 5. 단계별 실행 계획

### Phase 0 — 테스트 기반 (선행, xfail-strict 가드)

**0.1 골든 fixture** `server/tests/fixtures/captured/`
- (a) **라이브 1회 캡처**(`scripts/capture_golden_fixtures.py`): DART `fnlttSinglAcntAll`(005930 + 비-삼성 1종, **Q1(11013)/반기(11012)/사업(11011)**), `stockTotqySttus`, KRX OHLCV/market_cap. **必포함 케이스: `-표준계정코드 미사용-` sentinel 복수행(S1), attributable 라인 유무(B4), 반기 누적값(B1).**
- (b) **합성 4분기 fixture**(`fixtures/synthetic/`): 한 종목의 Q1/Q2(반기누적)/Q3(9M누적)/Q4(연간) — 실데이터 4분기 부족(H10)을 대체해 B1 차분 정확성 검증.
- AC: `**/tests/**` 하위라 forbidden-words 스캔 제외. 회사명 코드/문서 노출 시 allowed_phrases.

**0.2 계약(replay) 테스트** `test_adapters/test_dart_golden.py`·`test_krx_golden.py`
- 캡처본 `_parse_response` 출력 account 집합이 빌트인 pack factor input account를 **커버**하는지(어휘 drift). **adapter 출력에서 유도**(consumer 기대 하드코딩 금지).
- 반기 캡처본 income `thstrm_amount` > Q1(누적 명문화).

**0.3 관통 e2e** `test_db/test_realdata_pipeline_e2e.py` — `@pytest.mark.xfail(strict=True)`
- 캡처본 → 실 batch(실 SQLite) 적재 → 실 provider+evaluator → `market-cap>0`·`eps>0` ≥1건 + market_caps row>0.

**0.4 시간-비고정 as_of 계약** `test_api/test_as_of_contract.py` — `xfail(strict=True)`
- `today` override 없이 `kst_today()`로 `normalize(None)` → 200. 기존 `today=2024` 우회(`test_as_of_dependency.py:114`) 제거.

**0.5 cross-report PIT** `test_batch/test_dart_cross_report.py` — `xfail(strict=True)`
- 동일 종목 Q1+반기 순차 적재 → corruption 없이 단일 active head.

**0.6 demo 커버리지 인벤토리** (momus #4c)
- 삭제된 seed_demo가 커버하던 시나리오(6종목 cross-factor·distribution·portfolio) 목록화 → 골든 fixture 커버리지 매트릭스. gap(universe 규모·distribution_policy·portfolio)은 **명시적 비-목표**로 기록.

> xfail-strict: 미수정 가드가 fail해도 CI green 유지. 수정 완료 시 xfail 제거(또는 xpass→strict fail)가 green 전환 가드 = AC7과 양립.

---

### Phase 1 — 재현성 인프라 (B1·B4 게이트, H1·H2·H9)

**1.1 정책 버전/해시 2키** `snapshot_versions.py`
- `field_resolution_policy_version`(수동 semver "1.0") + `field_resolution_policy_hash`(AccountKind registry content_hash 자동, `price_adjustment_policy_hash` 선례) 추가 → 14→**16**키. `SNAPSHOT_SCHEMA_VERSION` "1.4". `test_meta_endpoints.py` 키 기대 갱신.

**1.2 reproduce 진단** `reproduce.py`
- `field_policy_superseded`(frozen != 현재 → True, `matches` 독립).

**1.3 AccountKind registry** `app/adapters/dart_account_mapper.py`
- `_MAPPING` → `{ifrs_id:(canonical, AccountKind)}`, `AccountKind ∈ {FLOW, STOCK, NO_DIFF}`. content_hash 산출 → 1.1의 `field_resolution_policy_hash`로 주입. `FieldResolution`(`db_field_provider.py:152`)에 `account_kind` slot. **provider 중복 canonical 상수는 mapper에서 import(SoT, B4 전제)**.

---

### Phase 2 — 빠른 데이터 경로 (병렬)

**2.1 S1 — sentinel 파싱 제외** `app/adapters/dart_adapter.py` `_parse_response`(849-991)
- `_NO_STANDARD_CODE_SENTINELS = {"-표준계정코드 미사용-", ""}`. **검사는 `account_id` 기준, `map_ifrs_account` 호출(919) 이전**(momus 경로 지적). `continue`+`no_standard_code_count`(silent drop 금지). 0.5 green 전환.

**2.2 B4 — SoT 통합 + attributable 재적재 [격상, H5]**
- (a) provider canonical을 mapper SoT로 단일화(Phase 1.3 포함). (b) **조사: 현재 매퍼로 005930 재적재 시 `net_income_attributable_to_owners`가 실제 적재되는지**(DART CFS 제공 여부) — `0.1` 캡처본으로 확인. (c) 제공되면 S1 수정 후 재적재가 해결; 미제공이면 **factor 분자를 attributable 부재 시 어떻게 할지 설계 결정 필요**(fallback 금지=§2.1, 즉 해당 종목 N/A 수용). alert는 가시화. **B1보다 상위 게이트일 수 있음을 인정**.

**2.3 B3 — 시총 백필 + KRX 가시성** (빠른 실결과, 누적·attributable 무관)
- `scripts/backfill_market_caps.py` 신설(pykrx → `SqlMarketCapRepository`).
- `krx_daily.py:542-555,549`: 전 종목 degrade면 PARTIAL(현 success 오기록), `degraded_count`를 `batch_runs` 기록(finalize 시그니처 확장), dead `BatchSummary.market_cap_rows` 제거.
- e2e read-back(실 SQLite write→`fetch_latest`). AC: market_caps row>0, 시총 스크린 종목 반환.

**2.4 B2 — 캘린더 확장** `shared/data/calendar/krx-calendar-v1.json`
- 2025-2026 KRX 공식 휴장일 반영, `coverage.max_date` 확장, `_fill_content_hash` 재생성, `verified_at`/`verified_source` 갱신. 0.4 green.
- runbook(`docs/runbooks/calendar-yearly-update.md`) + **§8 영구부채 격상**(미래연도 자동 불가, T18 cross-check는 탐지만).

---

### Phase 3 — B1 누적 읽기-보정 (Phase 1 이후, **3a/3b 분할**)

**Phase 3a — FLOW 차분 + account 분류**
- `db_field_provider.py:_resolve_financial_series/_annual`. **account 분류표(결론)**:

| account | kind | 처리 |
|---------|------|------|
| net_income_attributable_consolidated_ifrs(+_annual) | FLOW | 차분 standalone, TTM=standalone 4합 |
| basic_eps | FLOW | 차분 (EPS도 누적 보고) |
| equity_attributable_to_owners(+_period_begin) | STOCK | 시점값, 차분 없음 |
| depreciation_expense_annual | **NO_DIFF** | 이미 연누적 의미(`:250` magnitude=False), 최신 Q4 누적 사용 |
| dividends_paid_annual | **NO_DIFF** | 현금흐름 연누적+`magnitude=True`, 차분 시 부호/절댓값 충돌 → 차분 금지 |

  - fiscal_quarter별: Q1=누적=standalone / Q2=cum(H1)−cum(Q1) / Q3=cum(9M)−cum(H1) / Q4=annual−cum(9M).
  - **magnitude×차분 결정**: NO_DIFF account만 magnitude 유지, FLOW는 차분 후 부호 그대로(절댓값 미적용).
- 0.1(b) 합성 4분기 테스트가 TTM=12M(=30M 아님) green. (실데이터 4분기 불충분해도 B1 정확성은 합성으로 검증 — H10.)

**Phase 3b — PIT 안전 + precompute 정합 + 정책 bump**
- **PIT(H3)**: 차분 두 분기 effective_date/rcept_no 일관 검증, 정정 비대칭/분기 결손/음수 → strict N/A.
- **precompute 정합(H9, momus #1)**: `snapshot_daily.py:473` producer가 동일 차분 통과. 정책 버전 bump 시 **기존 `stock_snapshots` 무효화/재생산 절차** + serve-vs-recompute 정합(기존 stale snapshot purge 또는 정책버전 태깅).
- `field_resolution_policy_version` "1.0"→"2.0". 기존 frozen run은 1.2 진단으로 superseded 구분.

---

### Phase 4 — 견고성·투명성·UI (병렬, H7·H8)

**4.1 S2 — batch status** `dart_daily.py:323`·`krx_daily.py:358`·`snapshot_versions.py:233`
- `success_count==0` → `EMPTY` status, `collect_batch_versions` freeze 후보 필터 제외. `success>0 & failure>0`만 PARTIAL.

**4.2 S3+S4 — 투명성(묶음)** `screen.py`·`schemas/screen.py`·`client/lib/api/client.ts`·`app/screener/page.tsx`
- S3: `ScreenResultOut`에 `universe_size:int`·`na_excluded_count:int=0`(Optional default). `ScreenRunSnapshotOut` 불변. S4: `extractApiErrorDetail`(watchlist hoist+export), screener가 서버 `detail`/`code`(AS_OF_OUT_OF_RANGE 등) + S3 필드("X종목 데이터 미적재 제외"·"데이터 없음") 표시.

**4.3 S5 — 종목 검색 UI 연결** `client/components/NavBar.tsx:85-92` (사용자 요청 — **백엔드 검증 완료**)
- 현재 `readOnly`+`aria-disabled` 스텁. 백엔드 `GET /api/stocks/search?q=&as_of=`는 동작 확인됨(`q=삼성`→삼성전자). 
- `lib/api/stocks.ts`에 `searchStocks` 함수 추가(없으면), NavBar 입력을 controlled+디바운스(~250ms)→`/api/stocks/search`→드롭다운(name/code/market)→선택 시 `/stocks/{code}` 이동. as_of는 store 값(클램프된 2024-06-28) 사용. `readOnly`/`aria-disabled` 제거. 키보드 a11y(↑↓/Enter/Esc).
- AC: 타이핑 가능, "삼성"/"005930" 입력 시 결과 드롭다운, 선택 시 상세 이동. 빈 결과/로딩/에러 상태 표시.

---

### Phase 5 — 전체 실적재 + 검증 (S1·B4 후)

- **선행**: S1(반기 적재 가능)+B4(attributable 적재) 완료 후 `scheduler --job all`(또는 타겟)로 **4분기+ 재무·시총·가격** 실적재. 적재 전 "4분기 연속 확보 가능 종목 수" 사전 측정.
- **DoD**: (a) 시총 조건 종목 반환(B3), (b) TTM 재무 조건이 누적-보정된 정확값(B1, 합성으로 정확성·실데이터로 산출 확인), (c) 데이터부재 vs 0매칭 UI 구별(S3/S4), (d) 종목 검색 동작(S5), (e) 관통 e2e+계약 테스트 default suite green(xfail 제거됨).

---

## 6. 수용 기준

- AC1: `kst_today()` 실제 오늘 as_of로 PIT 200 (B2, 0.4).
- AC2: market_caps row>0, 시총 스크린 종목 반환 (B3).
- AC3: DART 반기/분기 적재가 PIT corruption 없이 성공 (S1, 0.5).
- AC4: TTM/연간 factor가 누적 보정된 정확값 — **합성 4분기로 검증**(실데이터 4분기 불충분 가능, H10) (B1, 0.1b).
- AC5: provider 산식 변경이 `field_resolution_policy_version`+`_hash`로 freeze, frozen run reproduce가 superseded 구분(matches 오염 0) (H1·H9).
- AC6: 스크리너가 universe_size/na_excluded_count로 데이터부재 구별, client가 서버 detail 표시 (S3/S4).
- AC7: 골든 fixture 기반 관통 e2e가 default suite green(xfail 제거 후), 미수정 중에는 xfail로 CI green 유지.
- AC8: batch 0-success가 freeze 후보 제외 (S2).
- AC9: 종목 검색 입력→결과→상세 이동 동작 (S5).
- AC10: B4 — attributable 적재 가능 여부 확정(재적재로 해결 or N/A 수용 결정) (H5).

## 7. 리스크 & 완화

| 리스크 | 완화 |
|--------|------|
| B1 차분이 §2.10 silent 위반 + precompute 발산 | H1·H9 — 정책 키 2종 선행 + producer 정합 + 기존 snapshot 무효화 |
| B1이 "정확하나 대부분 N/A"로 0건 재발 | H10 — 합성 4분기로 정확성 검증, B4 재적재로 데이터 확보, 4분기 사전측정 |
| depreciation/dividends_paid 차분 오류 | 3a 분류표 — NO_DIFF 확정 |
| Phase5↔S1/B1/B4 순환 | B1 검증을 합성으로 독립, Phase5는 통합확인만 |
| Phase0 red가 CI 차단 | xfail(strict=True) |
| B4 attributable 데이터 결손(alert로 미해결) | 2.2 — 재적재 가능 여부 조사 후 N/A 수용 결정 |
| 캘린더 미래연도 영구 수작업 | §8 비-목표 격상 + runbook |
| 골든이 demo near-e2e 미대체 | 0.6 커버리지 매트릭스, gap 비-목표 명시 |

## 8. 비-목표 / 영구 부채 / 외부 게이트

- **B2 캘린더 미래연도 = 영구 부채**(매년 수작업, 자동화 불가 — KRX 공식 발표 의존). T18 cross-check는 탐지만.
- demo가 커버하던 universe규모·distribution·portfolio 통합 시나리오 — 골든으로 미대체(비-목표).
- 전 종목 라이브 통합 확대, KOSIS 운영키, OAuth, survivorship backfill, ADR-0006/0030 자문 — 범위 밖.

## 9. 작업 단위 산정 (v2)

- Phase 0: 中~大(캡처+합성 fixture+5 테스트+인벤토리)
- Phase 1: 中~大(버전 2키+content_hash 주입+registry, 재현성 민감)
- Phase 2: 大(S1·B4재적재·B3백필·B2외부데이터 4건, B4 격상)
- Phase 3a: 中(차분+분류표+합성 테스트)
- Phase 3b: 大(PIT 안전+precompute 정합+정책 bump, 재현성 위험구간 — 마라톤 tail 주의)
- Phase 4: 中(S2·S3+S4·S5)
- Phase 5: 中~大(실적재 외부의존+검증)
