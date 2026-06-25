# Speculum ROADMAP v2 — Function-First 재기획

작성: 2026-06-25 · 기획: prometheus(+metis 사전분석, oracle 기획검토, **momus REJECT 반영 v2.1**)
상태: **제안(draft)** — 기존 `ROADMAP.md`/`M0~M9_PLAN.md`를 대체할 새 시퀀싱. ADR은 supersede가 아니라 amendment(§6).

> **v2.2 변경(momus CONDITIONAL 반영)**: ①V1c 시총 — momus는 "keyless라 정정하라"했으나 **실측(재확인)으로 반증**: `fetch_market_cap_by_date_range`가 KRX_ID/PW 경고+빈 응답으로 실패. 코드주석(`krx_daily.py:23`)·복구플랜 B3의 "keyless 시총"이 stale/오류임을 명시. `backfill_market_caps.py` 허구 인용은 제거(실 근거=어댑터 직접 호출). V1c=viability 규명 선행. ②§7 시총 external 근거 정정 ③§8 클램프 철거 순서 불변식(B2 후) 추가.
>
> **v2.1 변경(momus REJECT 반영)**: ①§2.4 freeze 이동이 허구(data_versions 미왕복, save_run이 이미 저장시점 재freeze)→"현 동작 확인"으로 정정, no-op ②§4 V1 keyless-EPS가 attributable 결손으로 N/A→V1=복구플랜 전체, EPS는 B4 재적재 후, 시총은 external ③§2.5 read-bypass freshness gate는 correctness라 유지(비교 프레이밍만 제거) ④§2.3↔V2 캘린더 모순→V2가 B2 확장에 종속 명시 ⑤§6 amendment 연쇄에 ADR-0035·production 8경로 추가, "70개"→16 정정 ⑥§3 tautology 테스트=xfail(strict) ⑦momus의 git base 지적은 **오류**(실제=feature/as-of-clamp-and-demo-removal `6c40c21`+미커밋 42, §8/§9 전제 정확).

---

## 0. 왜 v2 인가 (oracle 기획검토 평결)

단위 테스트 2729 통과인데 2026년 현재 실데이터 e2e 전멸. oracle 문서근거 검토 결론:
**기술자산(PIT/citation/lineage)은 유효. 시퀀싱·게이팅·"ready" 정의가 근본 오류.**
- M0가 데이터흐름 아닌 게이트·모델의 토대였고, squash 게이트는 내부정합뿐 — "실데이터 ≥1건"이 acceptance에 없었다(`M0_PLAN §6.1`). AC-O-01(7일 무인배치)은 [blocked]인데 "완료" 선언 가능.
- 캘린더가 가용성 self-destruct 게이트(ADR-0008 미인지). 차트 엔드포인트는 이미 몰래 우회 = 결함 자백.
- lazy fetch가 원래 `CONCEPT §4.3`에 있었으나 `ARCHITECTURE §5`에서 배치전용으로 퇴화.
- `RELEASE_READINESS §E`가 90% 자가진단했으나 "마일스톤 닫는 기준이 1마일을 영영 미루게 설계됨"이라는 구조비판으로 못 이어짐.

## 1. 1순위 원칙

> **가용성·기능은 어떤 엄밀성 게이트보다, 어떤 수작업 산출물보다 앞선다.**
> **거울이 아무것도 비추지 못하면, 그것이 얼마나 정직한 거울인지는 의미가 없다.**

파생 규칙:
- **R1 기능먼저**: 데이터가 끝까지 흐르는 가장 얇은 수직 슬라이스를 먼저 살린다. 엄밀성은 그 위 옵트인 레이어.
- **R2 fail-loud는 정확성에만**: 데이터 *정확성*은 fail-loud(추정 금지). *가용성*은 graceful degrade(응답은 한다).
- **R3 어떤 수작업 산출물(캘린더·키·배치상태)도 가용성을 죽이지 않는다.**
- **R4 ready = 실데이터 결과 ≥1**: 내부 green은 필요조건일 뿐. 마일스톤은 실데이터로 사용자 결과가 나와야 닫힌다.

## 2. 아키텍처 재배치 — PIT를 게이트에서 레이어로

실측(momus 정정): `collect_run_data_versions` 의존 production 경로는 **8곳**(screen·runs·market·backtest·reproduce·screen_custom·snapshot_daily·backtest_snapshot). 스크리너 평가는 **이미 live**(§2.4로 freeze 이동은 사실상 no-op). 즉 PIT 재배치의 실제 작업은 "freeze 이동"이 아니라 **경로별 캘린더 정책 분기(§2.3) + read-bypass freshness gate 유지(§2.5) + browse freeze 수집을 표시용으로 격하**다. browse(screen/market)도 data_versions를 *수집*하나 이는 UI banner·serve freshness gate용이지 frozen run 산출이 아님 — 이 구분이 핵심.

### 2.1 browse vs frozen 경로 매트릭스 (구현 전 확정 산출물)

| endpoint | 모드 | data_versions freeze | batch_cutoff 주입 | 캘린더 | 비고 |
|----------|------|----------------------|-------------------|--------|------|
| `GET /api/stocks/{code}/prices` | **browse(live+lazy)** | X | X | degrade | 이미 lazy-fetch 구현 |
| `GET /api/stocks/{code}` 상세 | **browse** | X | X | degrade | factor live 평가 |
| `GET /api/stocks/compare` | **browse** | X | X | degrade | |
| `GET /api/market/*` | **browse** | X | X | degrade | |
| `POST /api/screen` | **browse(live)** | △ 표시용 수집(banner) | X | degrade | frozen run 아님, banner 유지 |
| `POST /api/runs`(Save Run) | **frozen(저장시)** | O (저장시점 자체수집, 현 동작) | X | strict | live 산출+재현가능(§2.4, 변경 거의 없음) |
| `POST /api/runs/{id}/reproduce` | **frozen** | O(검증) | O | strict | |
| `POST /api/backtest` | **frozen** | O | O | strict | 정확 휴장일 필수 |
| `GET /api/market/*` (재게재) | **browse** | △ serve freshness gate용 수집 | X | degrade | §2.5 gate 유지 |

> 이 표가 PIT 재배치 LOE의 단일 기준. 구현 1단계 = 이 표를 코드와 대조해 차이만 수정.

### 2.2 자산 3분류 (2분류 불충분 — metis)

- **(a) 기능 토대** (없으면 화면에 값 안 보임 — 전 경로 유지): factor_evaluator, db_field_provider, backtest engine(ADR-0027), portfolio(ADR-0029), 가격/재무/시총 적재, 주가 lazy-fetch, corporate_action 보정(차트 정확성 측면).
- **(b) 성능 토대** (값은 보이나 느림 — 유지하되 재현성 결합 제거): read-bypass snapshot serve(`snapshot_serve.py`), stock_snapshots precompute, N+1 caching repos. → **유지하되 serve-vs-recompute 재현성 비교 로직 제거**(live와 byte-동일 보장 부담 소멸).
- **(c) 순수 엄밀성** (값과 무관, freeze/재현 — 백테스트로 격리): `collect_run_data_versions` freeze, result_hash, reproduce 진단, 17키 정책버전.

### 2.3 캘린더 이중성 — browse degrade / frozen strict (불변식)

- **browse 경로**: as_of 범위 밖이면 `latest_business_day` calendar-days fallback(휴장일 근사) → **항상 200**. R3.
- **frozen 경로**(runs/backtest): as_of가 verified 캘린더 범위 내일 것을 **hard 요구**. 범위 밖이면 "정확 백테스트 불가" 명시 거부(정확 휴장일 없이는 재현 무의미). R2.
- 코드: `as_of_policy`에 경로별 정책 파라미터 추가(`krx_calendar.py:321` degrade 분기).

### 2.4 Save Run freeze — 실제 baseline (momus 정정)

**momus 정정**: v1 초안의 "screen 응답 data_versions를 클라가 runs로 왕복(2-call race 방어)" 서술은 **허구였다**. 코드 실측:
- `client/lib/api/runs.ts:63-78` `saveRun()` body = `{conditions, selected_factors, security_types}` — **data_versions 미전달.**
- `runs.py:108-133` `save_run`은 client data_versions를 안 받고 `screen_active_codes`를 **독립 재실행** + `collect_run_data_versions(as_of, session)`를 **저장 시점에 자체 수집**.
- `schemas/screen.py:179-186`이 이미 명문화: "runs는 body의 data_versions를 안 받고 호출시점 재수집... 정책 module-level Final이라 실질 race 위험 0."

**즉 "저장 시점 서버 freeze"는 이미 현재 동작이다 — 이동할 freeze가 없다.** 따라서:
- **변경 거의 없음**: Save Run 재현성은 현 모델(저장시점 재실행+재freeze)로 이미 성립. v2는 이를 **유지**(폐기 아님).
- **유일한 browse 변경**: `/api/screen` 응답의 data_versions(`screen.py:171`)는 **UI freshness banner 표시용**(`screen.ts:69`, `SaveRunButton.tsx:115` `versionCount`). live default로 가도 banner를 위해 **유지 가능** — 굳이 제거하면 banner가 깨지므로 **제거하지 않는다**. browse가 freeze를 "수집"하지만 그건 표시용이지 frozen run 산출이 아님.
- **race 모델 정직화**: screen과 save는 **별개 요청**이라 단일 트랜잭션이 둘을 못 묶는다(v1 초안의 "단일 트랜잭션 차단"은 틀림). 현 silent-drift 위험은 정책이 in-repo Final이라 실질 0 — 이 기존 한계를 **그대로 상속**(새로 안 만들고 안 고침).

→ **순효과: §2.4는 "변경"이 아니라 "현 동작이 이미 옳음을 확인"이다.** PIT 재배치의 실제 작업은 §2.1 매트릭스의 *browse 경로 캘린더 degrade*(§2.3)와 *read-bypass freshness 유지*(§2.5)이지 freeze 이동이 아니다.

### 2.5 read-bypass(snapshot serve) 운명 (momus 정정)

성능 토대로 **유지**. **momus 정정**: `current_data_versions` equality(`market.py:154,270-275`·`screen.py:667-694`)는 "재현성 비교"가 아니라 **stale snapshot 서빙 차단 = freshness gate**다. precompute snapshot 이후 batch_id(krx/dart) 갱신 시 snapshot은 stale이 되고, equality 불일치→live fallback이 **유일한 freshness 메커니즘**. **이 gate를 제거하면 live default 하에서 stale 값을 서빙 → R2(정확성) 위반.**

→ 따라서 **freshness gate는 그대로 유지**. 바뀌는 건 *프레이밍*뿐: "serve==live byte-동일 재현성 보장"이라는 부담은 내려놓되(browse는 frozen 아님), **stale 차단 gate는 freshness로 존속**. 별도 freshness 대체설계 불요(기존 gate 재사용). MEMORY Slice 1b의 "serve-vs-recompute 재현성 증명"만 무관해지고, gate 자체는 산다.

## 3. "ready"의 새 정의

마일스톤은 다음을 **모두** 충족해야 닫힌다:
1. 내부 green(pytest/vitest/ruff/typecheck) — 필요조건.
2. **실데이터 e2e ≥1**: 캡처 실응답 fixture로 적재→서빙→화면값이 default suite에서 green(xfail 제거).
3. 외부게이트 기능은 **캡처 fixture 코드경로 e2e**로 검증(실 자격증명 운영적재는 별도 external 트랙, §7).

**기존 2729 tautology 테스트 처리(momus 약점6)**: 폐기 아님. live default 전환으로 회귀하는 테스트(`test_as_of_dependency.py:114` today=2024 우회 등)는 **`@pytest.mark.xfail(strict=True)` + 인벤토리**(복구플랜 D2/0.4)로 — green CI 유지하며 production 실패를 가시화. 수정 완료 시 xfail 제거가 전환 가드. 단위 테스트 자체는 유지하되 "실데이터 e2e가 진짜 게이트"로 위상 강등.

## 4. 마일스톤 시퀀싱 (function-first)

기존 M0(ADR12개+게이트)는 **V0의 전제문서로 격하**. 새 시퀀스:

### V0 — "거울이 비춘다" (실데이터 1슬라이스 + 운영 골격)
**목표**: 한 종목 실 가격이 KRX(keyless)에서 라이브로 당겨져 차트가 그려진다.
- 주가 lazy-fetch(이미 구현, 이 세션) — browse 기본 경로.
- 캘린더 browse degrade(§2.3) → 오늘 날짜 200.
- 스케줄러 배선 + 수동 트리거(`batch.scheduler`) + **실응답 캡처 fixture 기반 관통 e2e**.
- **AC(keyless)**: as_of 미명시(오늘)로 `/api/stocks/005930/prices` → 실 최근 OHLCV bars ≥1. e2e green.

### V1 — "스크리너가 실값을 낸다" (= 복구플랜 실행, 정직화)

**냉정한 현실**: **오늘 keyless로 실값을 내는 스크리너 factor는 없다.** EPS/ROE/PER은 적재된 DART에 `net_income_attributable_to_owners`가 **결손**(복구플랜 H5 확인, `net_income`=ProfitLoss만)이라 B4 재적재 전엔 N/A.

**시총 viability — 문서 vs 실측 충돌(momus CONDITIONAL 정정)**: 코드 주석(`batch/krx_daily.py:23`)과 복구플랜 B3는 "pykrx/FDR 출처라 키 불필요 → keyless 시총 적재 가능"이라 **주장**한다. 그러나 **이 세션 실측(2회 재확인)**: `PykrxAdapter.fetch_market_cap_by_date_range('005930', 2024-06-28)`가 **AdapterError로 실패** — pykrx가 `"KRX 로그인 정보 없음: KRX_ID 또는 KRX_PW 환경변수 미설정"` 경고 + `get_market_cap_by_date: Expecting value`(빈 응답). 즉 **현 pykrx/환경에서 시총은 사실상 게이트됨 — 코드 주석·복구플랜 B3의 "keyless" 가정은 stale/오류**(복구플랜 B3 "빠른 keyless 실결과" 전제도 함께 반증 → 복구플랜 정정 필요). (이전 v2.1의 `backfill_market_caps.py` 인용은 오류 — 그 파일은 미존재[복구플랜 B3 신설 예정], 실 근거는 위 어댑터 직접 호출 실측.)

따라서 V1은 "쉬운 keyless AC"가 아니라 **복구플랜 Phase 0~3의 무거운 작업 전체** + **시총 viability 규명**(pykrx 버전/엔드포인트가 정말 KRX 로그인을 요구하는지 vs 일시 아웃티지)이다. 숨기지 않는다.

**V1a — DART 재무 e2e (keyless, EPS 첫 실값)**:
- 골든 fixture 캡처(복구플랜 Phase 0) + S1 sentinel(이미 fix) → **B4 attributable 재적재**(현 매퍼로 재적재 시 attributable 라인 적재 확인) → EPS factor 실값.
- 캘린더 2025-2026 확장(B2) → live default 성립, **as_of 클램프 제거**(§8).
- **AC**: 골든 fixture 기반 관통 e2e에서 EPS 조건 스크리너가 실 종목 ≥1 반환(default suite green, xfail 제거). keyless(DART 키만).

**V1b — TTM 정확성 (B1 누적값)**:
- B1 읽기보정(복구플랜 Phase 1 정책키 인프라 선행 → Phase 3 차분). FLOW/STOCK/NO_DIFF 분류.
- **AC**: 합성 4분기 fixture로 TTM=12M(=30M 아님) 검증(복구플랜 H10).

**V1c — 시총/PER (viability 규명 선행)**:
- **먼저 진단**: pykrx `get_market_cap_by_date`가 정말 KRX 로그인을 요구하는지(pykrx 버전 이슈), 아니면 일시 엔드포인트 아웃티지인지, 아니면 대체 keyless 경로(FDR 시총·DART `stockTotqySttus` 발행주식수×종가)가 있는지. 실측은 현재 KRX_ID/PW 경고+빈 응답.
- 진단 결과 KRX 로그인 필수 → **external 트랙(§7)**. 대체 keyless 경로 발견 → V1 keyless AC로 승격.
- 스크리너 투명성(S3/S4 이미 구현)으로 시총 결손 종목을 "데이터 미적재 제외"로 정직 표시.

> V1의 진실: function-first지만 V1은 작다고 거짓말하지 않는다 — **복구플랜 전체가 V1이다.** 빠른 keyless 실결과 후보는 **EPS(B4 재적재 후)** 또는 **시총(viability 규명 후, 현재 실측은 게이트)** 중 먼저 풀리는 것 — 둘 다 선행작업 있음. "오늘 keyless로 바로 되는 factor 없음"이 정직한 출발점.

### V2 — "재현성 레이어" (백테스트/Save Run 격리)
**목표**: PIT freeze를 frozen 경로로 격리, browse는 live.
- **freeze 이동은 §2.4 정정으로 사실상 no-op**(이미 저장시점 freeze). 실제 작업 = 경로별 캘린더 정책(§2.3) browse degrade / frozen strict 분기.
- read-bypass는 freshness gate **유지**, 재현성 *비교 프레이밍*만 제거(§2.5).
- **캘린더 모순 해소(momus 약점4)**: frozen strict(Save Run/backtest가 verified 범위 강제)는 **V1a의 B2 캘린더 2025-2026 확장 선행 필수**. 확장 전엔 "오늘 스크린→Save Run 거부" 모순 발생 → **V2는 V1a(캘린더 확장)에 종속**. B2가 영구부채(매년 수작업, §7 external)임을 명시.
- **AC**: Save Run live 산출+reproduce matches=True(현 동작 보존). backtest frozen 캘린더 강제. browse 4경로 오늘 날짜 200(캘린더 degrade 후).

### V3+ — "확장" (기존 자산 기능 재배치)
기존 M3~M9 구현분을 기능 토대로 재배치. **momus 약점8 인정**: 아래는 단일 V가 아니라 **각 기능 = 독립 마일스톤**이며, 측정가능 AC는 **해당 마일스톤 진입 시점에 "실데이터로 화면값 ≥1" 형태로 구체화**(지금 일괄 정의 안 함 — 미정의가 아니라 deferred-by-design, V0/V1 검증 후 착수).
- portfolio(ADR-0029), watchlist, custom packs/Factor Lab(ADR-0025/0028/0032/0034), tax(ADR-0030), disclosure(ADR-0026), total-return 차트(ADR-0035), KOSIS macro(ADR-0036) — 각각 별도 V.
- 공통 게이트: 각 기능이 **실데이터로 ≥1 결과**(R4). 외부게이트 의존 기능은 §7 트랙.

## 5. 기존 코드자산 → v2 매핑 (버리지 않음)

| 기존 | 분류(§2.2) | v2 배치 |
|------|-----------|---------|
| factor evaluator·db_field_provider | 기능 | V0/V1 토대(유지) |
| 가격/재무/시총 적재·adapter(ADR-0003) | 기능 | V0/V1(lazy+배치 공존) |
| backtest engine(ADR-0027)·reproduce | 엄밀성 | V2(frozen 격리) |
| portfolio(ADR-0029) | 기능 | V3 |
| read-bypass·precompute·N+1 caching | 성능 | 유지, V2서 재현성결합 제거 |
| custom packs(ADR-0025/0034) | 기능+엄밀성 | V3(기능)+V2(freeze) |
| total-return(ADR-0035)·KOSIS(ADR-0036)·tax·disclosure | 기능 | V3 |
| collect_run_data_versions·17키·result_hash | 엄밀성 | V2(frozen 한정) |

**코드 폐기 없음.** 시퀀싱·게이팅만 재배치.

## 6. ADR 처리 — supersede 아닌 amendment 연쇄 (metis)

ADR은 결정 기록이지 로드맵이 아니므로 ROADMAP v2가 "supersede"하지 않는다. amendment 연쇄:
- **ADR-0008 (as-of)**: D3.1(Screener→snapshot 기준)·D7(browse freeze 의무) **amendment** — "browse=live, frozen=runs/backtest 한정". D2(캘린더 영구진실)에 **degrade 조항 추가**(browse calendar-days fallback).
- **연쇄**: 0008 D7 개정 → **ADR-0033**(재현성 D2~D4 — reproduce 범위를 frozen 경로로 한정 명문화) → **ADR-0025/0034**(pack content_hash 재현 서술) + **ADR-0035 D8**(total-return dividend cutoff freeze, `snapshot_versions.py`의 total_return_policy 2키) amendment.
- **production freeze 경로 8곳**(momus 정정, "70개 파일"은 import 집계 오류 → 실제 16파일/production 8): screen·runs·market·backtest·reproduce·screen_custom·snapshot_daily·backtest_snapshot. amendment는 이 8경로의 browse/frozen 재분류를 반영해야 함(특히 market·snapshot_daily가 browse인데 freshness gate용 freeze 수집).
- **유지(무충돌)**: 0001/0002/0003/0009(CA PIT 이중축은 backtest서 여전), 0007(No Advice), 0029/0030 등.
- 신규 ADR 1건 권고: **"PIT 레이어 모델"**(browse/frozen 분리 + 캘린더 이중성 + freshness gate 존속) — v2 게이팅의 단일 근거.

## 7. external-gated 트랙 (가용성을 외부에 안 묶음)

각 마일스톤 AC를 2단 분리:
- **keyless 필수 AC**(green CI 게이트): 주가(pykrx/FDR)·DART재무(키 보유)로만 검증.
- **external-gated AC**(별도 마킹): 캘린더 verified 휴장일 확장(B2, 외부데이터)·시총(viability 규명 후 KRX 로그인 필수로 판명 시) 등 — **실응답 캡처 fixture로 코드경로 e2e 검증=통과**, 실 운영적재는 외부 트랙(dead path 방지). 외부 게이트 미충족이 마일스톤을 막지 않음. (시총은 §4 V1c 진단 전까지 external 확정 아님 — 실측 게이트이나 원인 미규명.)

## 8. 복구플랜 흡수 + as_of 클램프 재검토 (metis 충돌 해소)

- `.sisyphus/plans/speculum-realdata-pipeline-recovery.md`(B1~B4, 골든fixture, e2e)는 **v2에 흡수** — Phase 0/1 → V1, Phase 3(B1) → V1, 테스트계층 → V0/V1 AC.
- **as_of 클램프(이 세션 `6c40c21`)는 live-default와 직접 충돌**(클램프=증상치료, 근본=캘린더 2024단년). → **클램프 제거, 캘린더 확장(B2)으로 대체**(V1).
- **순서 불변식(momus 권고)**: 클램프 철거는 **B2 캘린더 확장 랜딩 *후*에만**. 그 전에 철거하면 그 사이 today→verified 범위 밖→`AsOfOutOfRangeError`(`as_of_policy.py:84-92`) 재발(frozen 경로). browse 경로는 §2.3 degrade로 200 유지하나, **frozen 경로 보호 위해 철거는 B2 후**. (즉 V1a B2 → 클램프 철거 순.)
- 골든 fixture as_of 기준 = **캡처 당일 실거래일**(클램프된 과거 아님) — live default 정합.

## 9. 이 세션 미커밋 작업의 처리 (feature/as-of-clamp-and-demo-removal)

| 작업 | v2 정합 | 처리 |
|------|---------|------|
| demo 제거 | ✅ | 유지(실데이터 단일경로) |
| S5 종목검색 | ✅ | 유지(기능) |
| S3/S4 스크리너 투명성 | ✅ | 유지(V1) |
| S1 DART sentinel | ✅ | 유지(V1 재무적재 unblock) |
| 주가 lazy-fetch | ✅ | 유지(V0 핵심) |
| **as_of 클램프 + /api/calendar** | ⚠️ 충돌 | V1서 캘린더 확장으로 **대체/철거** |

→ 커밋하되 as_of 클램프는 "임시 degrade, V1서 제거 예정" 주석/이슈로 표시.

## 10. 리스크 & guardrails

| 리스크 | 완화 |
|--------|------|
| PIT 재배치가 예상보다 큰 리팩토링 | §2.1 매트릭스를 코드 대조 후 LOE 확정 후 착수 |
| freeze 지점 이동이 Save Run 재현성 깨뜨림 | §2.4 — 저장시점 freeze, 기존 frozen run은 그시점 캘린더로 byte-불변 |
| 캘린더 degrade가 백테스트 오염 | §2.3 browse-only degrade, frozen strict 불변식 |
| ADR amendment 연쇄 누락 | §6 amendment 그래프, supersede 금지 |
| 외부게이트 코드가 dead path | §7 캡처 fixture 코드경로 AC |
| 복구플랜 base(클램프) 충돌 | §8 클램프 철거+캘린더 확장 |
| live 전환 시 tautology 테스트 회귀 | 사전 회귀수 측정(`test_as_of_dependency.py:114` today=2024 우회 등) |

### Undefined(구현 전 확정 필요)
1. "frozen 경로" 정의 = **endpoint 기준**(runs/backtest), as_of 명시는 browse PIT 시점일 뿐 freeze 아님.
2. read-bypass 유지/폐기 — **유지, 재현성 비교만 제거**.
3. 골든 fixture as_of = **캡처 당일**.

---

## 다음 단계
1. §2.1 endpoint 매트릭스를 코드 대조 → PIT 재배치 실 LOE 확정.
2. ADR-0008/0033 amendment 초안 + 신규 "PIT 레이어 모델" ADR.
3. V0 착수: 캘린더 browse degrade + 스케줄러 e2e 게이트(주가 lazy-fetch는 완료).
4. momus 비판 검토 권고(이 v2 자체).
