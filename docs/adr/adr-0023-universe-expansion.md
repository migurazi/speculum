# ADR-0023: Universe 확장(ETF·우선주·리츠) — 자산군 경계 + 분포 partition + 재현성

| | |
|---|---|
| **Status** | PROPOSED (M2 선결 — T77/T78/T79 착수 전 확정 필요) |
| **Date** | 2026-06-01 |
| **Deciders** | 사용자 |
| **Related** | [[adr-0002-factor-fact-model]](Primary→Derived DAG·multi-id), [[adr-0004-market-cap-eps-per]](EPS 귀속 — 우선주 함정 근거), [[adr-0007-default-ui-rules]] D1/D2.3/D5/D8.2(자동 강조·랭킹 금지, percentile/grayscale 허용), [[adr-0009-corporate-action]] D6(lineage·code_history)/D10(우선주→보통주 전환 청구)/D11(stock_status 시계열), [[adr-0024-asset-class-statistical-limits]](ETF/우선주 소표본 통계 한계 디스클로저 — CONCEPT §11 예약 항목. 원 §11 표기는 "ADR-013" 이나 0013 은 forbidden-word ADR 이 선점 → 0024 재배정), [[adr-0021-multiuser-transition]](재현 무해 논증의 동형 패턴), [[adr-0022-composite-factor-operators]] D3(분포 모집단=as_of active universe·N/A 제외·DISTRIBUTION_POLICY_VERSION 버전화); `docs/CONCEPT.md` §2.1 Fidelity / §2.2 No Advice / §2.3 Active Inspection / §2.4 PIT / §2.7 Observation / §2.10 Reproducibility / §11(통계 한계 ADR 예약 — 0024 재배정); `docs/work-orders/m2-milestone.md` T77/T78/T79; oracle 분석(2026-06-01) |

## Context

M0~M1 universe = KOSPI/KOSDAQ 보통주(common stock). M2 는 ETF·우선주·리츠를 관측 대상에 합류시킨다(ROADMAP §4, work-order T77/T78/T79). 그런데 이 확장은 단순 종목 추가가 아니라 **factor 의미 체계·분포 모집단·재현성 freeze 세 축을 동시에 건드린다.**

**oracle 분석의 코드 현실 5가지(모든 결정의 제약):**
1. **`security_type` 분류 필드가 어디에도 없다.** `StocksMasterORM`/`StockMasterRecord` 는 `market`(KOSPI/KOSDAQ/KONEX)·`fiscal_month`·`ifrs_preference_default` 만 가진다. 자산군 구분 수단 자체가 부재.
2. **Universe = `list_active(as_of)` 단일 정의**(`listing_date<=as_of AND (delisting_date IS NULL OR >as_of)`). 자산군 무차별. 이 한 메서드가 **Screener·ADR-0022 분포 모집단·Market Overview 세 곳을 동시 공급**한다.
3. **ADR-0022 분포 모집단이 곧 `list_active`**(`db_universe_distribution.py` `_list_active_codes`). 여기에 ETF/우선주가 섞이면 percentile/zscore 분포가 이종 혼합으로 오염된다 — **가장 날카로운 지점**.
4. **재현성 freeze 키에 universe *멤버십* 해시는 없으나 `distribution` 정책 버전은 있다.** `collect_active_policy_versions` 는 pack/calendar/pit/adjustment/ca/evaluator/**distribution**/schema + batch_id 를 freeze 한다. 모집단 *멤버십*(어떤 종목이 들었나) 자체를 박는 별도 해시는 없지만, **분포 *정책*(모집단 정의 규칙)은 `DISTRIBUTION_POLICY_VERSION` 으로 이미 freeze 됨**(ADR-0022 D3-5). 즉 partition 규칙을 바꾸면 이 버전을 올려 옛/새 run 을 분리 재현할 수 있다(D5/D6 의 토대). 위험은 "규칙은 그대로 둔 채 모집단에 ETF 가 silent 혼입"되는 경우 — 그때 버전이 안 올라 같은 `as_of`+`batch_id` 라도 percentile 이 달라진다(갭 A/B).
5. **factor N/A propagation 은 이미 강건**(`factor_evaluator` `missing_input:<field>` 단조 전파). ETF 에 PER 적용 시 재무 fact 부재 → 자연 N/A. **N/A 인프라 재사용이 factor 경계의 1차 방어선.**

## Decision

### D1. 자산군 범위·단계화 — 단계적 도입, 우선주 → 리츠 → ETF (work-order T순서 역순)

기존 factor/fact 모델(ADR-0002/0004)과의 **거리순**으로 정렬한다:

| 자산군 | 재무제표 | 기존 모델 거리 | 핵심 위험 |
|---|---|---|---|
| **우선주**(T78) | 발행사 재무 **공유**(동일 DART 법인, 별개 종목코드) | 가장 가까움 — fact 파이프라인 재사용 | EPS 귀속(D3-b) |
| **리츠**(T79) | 특수회계(FFO/배당), DART 법인 존재 | 중간 — 새 Primary 계정 | FFO 계정 매핑 |
| **ETF**(T77) | **재무제표 무의미**(NAV/구성종목) | 가장 멈 — 재무 factor 전면 N/A + 새 fact(NAV/괴리율/AUM) | factor 체계 전체 무력 |

ETF 가 T77 로 먼저인 work-order 순서는 "가장 어려운 것 먼저"라 위험. 우선주부터 하면 fact 파이프라인 재사용이 검증되고 security_type 메타·분포 partition 인프라(D2/D5)를 먼저 깐 뒤 ETF 의 이질성을 다룬다. **동시 도입 기각**(factor 의미 충돌의 결이 자산군마다 달라 "무엇을 정의 가능한가" 통제가 흐려짐).

**T번호 재정의 아님 — 내부 실행 순서 권고 + 공통 선결.** work-order 는 T77=ETF / T78=우선주 / T79=리츠로 번호=주제를 고정한다. 본 ADR 은 그 번호를 바꾸지 않고 **착수 순서만 T78→T79→T77 로 권고**하며, 세 task 의 **P0 공통 선결 = security_type 메타 도입(D2)** 을 신설 번호 대신 "T77~T79 선결 sub-task" 로 둔다. **Consequences 액션**: work-order §3.6 에 "T77~T79 P0 선결: security_type" 행 추가 + 실행 순서 주석.

### D2. `security_type` 메타 신설 — enum + PIT 불변 분류, KRX 출처 (T77~79 의 0번 선결)

`StocksMasterORM`/`StockMasterRecord` 에 `security_type: Enum(common|preferred|etf|reit|...)` 신설.

- **위치**: lineage entity(stocks_master) — fact 가 아니라 **종목의 정체**(ADR-0009 D6 lineage·code_history 일부).
- **PIT — 한 lineage 가 살아있는 동안 그 security_type 은 불변(immutable per live lineage).** 단 **"우선주가 보통주로 변하지 않는다"는 과한 단정은 철회한다.** ADR-0009 **D10**(`우선주 → 보통주 전환 청구`)은 전환을 lineage 신설이 아니라 **기존 우선주 lineage 의 발행주식수 감소 + 보통주 lineage 증가(두 종목 각각 corporate_action)** 로 모델링한다. 전환 청구가 누적되면 우선주 lineage 가 **전량 전환으로 폐지**될 수 있다. 따라서 정밀 규칙: **security_type 은 lineage 분류 라벨로서 시계열 불변이되, lineage 자체는 전환으로 폐지(`delisting_date` set)될 수 있고 이는 PIT(`list_active` 의 `delisting_date>as_of`)로 이미 처리된다.** security_type 에 valid_from/valid_to 는 여전히 불요(분류는 안 바뀌고, 종목 소멸만 발생). BW/CB 행사(ADR-0009 D10)도 동일 — 발행주식수 변동(corporate_action)이지 분류 변경이 아니다.
- **출처**: KRX(`pykrx`) 종목 구분 1차(§2.8 Conformance), DART/운용사 공시 보조. ADR-0003 adapter citation 동반.
- **마이그레이션**: 기존 전 종목 = `common` backfill(현 universe 정의). stocks_master(lineage 메타)만 — fact 테이블 무변경(D6 재현 무해의 토대).
- **선결성**: D5(분포 partition)·D7(screener 필터)이 security_type 에 의존 → **이 필드 신설이 T77~T79 전체의 0번 선결**(갭 E).

### D3. factor 적용 경계 — universe 포함 + factor N/A 강제(제외 아님), 3갈래

**(a) ETF — universe 포함, 재무 factor 자연 N/A.** universe 에서 빼지 않는다(§2.3 Active Inspection — "ETF 는 보면 안 됨"이라 시스템이 정하면 의제 설정). ETF 는 DART 재무 fact 가 없어 PER/ROE 가 `missing_input` 으로 자연 N/A(**추가 코드 0, N/A 인프라 재사용**). ETF 전용 factor(NAV/괴리율/AUM)는 별도 canonical_id 네임스페이스(`nav-premium:krx`, `aum:krx`)로 ADR-0002 multi-id 에 추가. 단 **분포 모집단에서는 분리**(D5).

**(b) 우선주 — 발행사 재무 공유하나 EPS 귀속이 함정.** 우선주 PER = 우선주가격 / EPS 인데 ADR-0004 D2 의 EPS 는 **보통주 귀속 순이익** 기반. 우선주가격(별도 시총)을 보통주 EPS 로 나누면 분자/분모 모집단 불일치 → 수치 정의 불명.
- **권고(D3-b-1)**: 발행사-귀속 재무 factor(PER/PBR/ROE)의 **default = 우선주 종목 N/A**(발행사 보통주 종목에 귀속). 우선주는 가격·**배당수익률·보통주 대비 괴리율** 관측 대상.
- **기각(D3-b-2)**: 우선주에 보통주 EPS 를 **정의 은폐한 채** 차용한 PER(보통주 PER 와 같은 canonical_id) — §2.1 Fidelity(모집단 불일치 은폐) + §2.2 No Advice("우선주 저평가" 가짜 서사) 위반.
- **양면 — multi-id variant 는 봉쇄하지 않는다(과잉 경계).** §2.1 Fidelity 의 본질은 "표시 금지"가 아니라 "**정의 명시**"다. ADR-0002 D2 multi-id 정신상, "우선주가격 ÷ 보통주 EPS" 는 **정의를 밝힌 별도 canonical_id**(`per:preferred-over-common-eps`)로는 정직하게 표시 가능한 대상이다(분자/분모 모집단을 라벨에 노출하면 은폐가 아님). default N/A 와 정의-명시 variant 허용은 양립한다 — 이 variant 도입 여부는 **D8 미해결**로 열어둔다(전면 금지 아님). "default N/A 가 곧 multi-id variant 금지"가 아님을 명기.
- 우선주/보통주 괴리율(및 variant 표시 시)은 ADR-0022 D4 출력 게이트(grayscale only, 색조·등락색 금지) 적용 — "할인 큰 우선주=저평가" 서사 차단.

**(c) 리츠 — FFO/NAV 는 새 Primary 계정, 일반 재무 factor 와 N/A 공존.** 리츠는 순이익 기반 PER 이 부동산 감가상각으로 왜곡(FFO 가 업계 표준). 리츠 전용 Primary 계정(`ffo`, `nav_per_share`, 임대수익) + 전용 canonical_id(`ffo-multiple:reit`, `dividend-yield:reit`)를 ADR-0002 Layer 1 에 추가. **일반 PER 의 리츠 적용(N/A 강제 vs 값+Fidelity 라벨)은 D8 미해결**(ETF 의 완전 N/A 와 달리 값은 나오나 의미가 다른 미묘 지점).

### D4. No Advice — universe 편입은 추천 아님, 자산군 필터 UI 가 위험 지점

- universe 에 자산군을 넣는 행위 = §2.3 관측 대상 확대. 추천은 **시스템이 특정 자산군을 기본 노출/강조**하는 것(ADR-0007 D1/D2.3 자동 강조 금지로 이미 차단).
- **위험: 자산군 선택 필터가 "추천 자산군"으로 변질.** 방어:
  - 자산군 필터 = **사용자-구동 중립 선택**(체크박스 "ETF 포함/제외"). 기본값 = `[common]`(보통주만).
  - ADR-0007 D8.2 CI 게이트 확장: `<RecommendedETFs>`/`<TopReits>` 등 자산군 변형 컴포넌트 **코드베이스 부재 + 검색 게이트**.
  - 자산군 라벨(ETF 명·운용사명)은 forbidden-words `EXTERNAL_QUOTE` scope(외부 사실, 검사 skip).
- **정직한 긴장 인정(§2.3).** "기본 = 보통주"는 **완전 중립이 아니다** — 그 자체로 "보통주가 기준 자산군"이라는 은근한 default 의제일 수 있고 §2.3 Active Inspection(홈 default 가 의제가 되어선 안 됨)과 미묘히 충돌한다. 그럼에도 기본 전체 포함은 D5 분포 오염·D7 혼란을 부르므로, 기본 보통주는 **"M0~M1 universe 정의의 연속성 보존"이라는 실용 선택**(가장 덜 나쁜 안)으로 정당화하되 중립이라 강변하지 않는다. 의제 설정 최소화 장치: 자산군 필터는 **명시적·동등 가시성**(보통주도 다른 자산군과 같은 체크박스로 노출 — "보통주가 특별"이라는 시각적 위계 금지, ADR-0007 D2.2 grayscale 정신). 즉 default 는 연속성 때문에 보통주이되, UI 위계상으로는 자산군이 동등하게 보인다.

### D5. ADR-0022 분포 모집단 partition — security_type 별 분리 (가장 중요한 정합성 결정)

현 `_list_active_codes` 가 `list_active` 전체를 단일 모집단으로 percentile/zscore 계산. ETF/우선주 혼입 시:
- **재무 factor 분포는 ETF 가 N/A 제외(ADR-0022 D3-3)로 자동 보호** — PER 분포에 ETF 자동 미산입.
- **그러나 가격·시총 field(`market-cap` percentile 등)는 N/A 가 아니라 산입됨** → 이종 자산군 혼합 분포, §2.7 의미 붕괴("이 보통주 시총이 ETF 포함 전체에서 몇 %"는 무의미). **구멍은 N/A 가 아닌 field.**
- **권고: 분포 모집단을 `security_type` 으로 partition.** percentile/zscore 는 **같은 자산군 내에서만**. 보통주 PER percentile=보통주 모집단, 리츠 FFO percentile=리츠 모집단.
- **소표본 경계(개선 — partition 의 副작용).** partition 은 모집단을 쪼개므로 작은 자산군(리츠 등 십수 개)에서 분포가 통계적으로 빈약해진다. 현 코드는 일부 자동 보호가 있다: `_build_field_distribution` 은 `n<=1` 이면 stddev=None(zscore N/A), `n==0` 이면 전부 None. **그러나 `percentile` 은 n=2~3 에서도 값을 산출**(거친 계단함수)하므로 §2.7 Observation 의미가 약해진다. → partition 모집단 n 이 임계(예: <30) 미만이면 percentile/zscore 에 **소표본 디스클로저**(통계적 한계 UI 명시)를 동반. 이는 [[adr-0024-asset-class-statistical-limits]](CONCEPT §11 예약 — "ETF/우선주 v0.2 분리의 통계적 한계 명시")가 다룰 영역으로, 임계값·디스클로저 형태는 **D8 미해결**.
- **재현성 직결**: partition 규칙 변경 = `DISTRIBUTION_POLICY_VERSION`("1.0") **bump**. ADR-0022 D3-5 가 "모집단 정의를 이 버전으로 버전화"라고 이미 명문화 — universe 확장은 이 버전을 올리는 사건. **D5 와 D6 은 한 몸.**

### D6. 재현성 — universe 확장은 기존 저장 run 을 깨지 않는다 (조건부)

ADR-0021 의 "user_id 는 hash 입력 아님 → 마이그레이션 무해" 와 **동형 논증**:
- **저장 run 의 `result_codes` 는 freeze 됨.** 과거 M1 run 재실행은 frozen batch_id 의 fact 만 본다(`screen.py` `krx_batch_cutoff`). ETF fact 는 미래 batch 에만 존재 → 과거 batch_cutoff 로 안 보임 → **result_codes byte 동일**.
- **backfill batch 도 안전(메커니즘 명시).** "ETF fact 는 미래 batch 에만"보다 정확히는 — `snapshot_versions.collect_batch_versions` 의 batch 선택 필터가 `func.date(started_at) <= as_of` 다(`effective_date` 가 아님). 과거 데이터를 채우는 backfill batch 는 `started_at` 이 오늘(미래 as_of 기준)이므로, 과거 as_of 의 frozen run 에서 `started_at::date > as_of` 로 **미선택**된다. 따라서 effective_date 가 과거인 backfill 로 ETF fact 가 들어와도 옛 run 의 batch_cutoff 에 잡히지 않아 재현 무해. backfill 반례로부터 `started_at` 필터가 보호한다.
- **조건: security_type `common` backfill 이 기존 batch 의 fact 무변경**(stocks_master 메타만). AC-M2-C-05(M1 run byte 재현)가 universe 확장 후에도 성립하는 정확한 조건.
- **깨지는 시나리오**: D5 partition 없이 ETF 를 모집단에 넣으면 **percentile 쓴 저장 run**(Factor Lab composite) 재실행 시 모집단이 달라져 percentile 변동. D5 의 partition + `DISTRIBUTION_POLICY_VERSION` bump 이 차단(옛 run=옛 버전 재현, 신 run=신 버전).
- **권고**: universe 구성 변경을 `DISTRIBUTION_POLICY_VERSION` bump 에 흡수(별도 universe 해시 키 신설은 과잉 — partition 규칙이 분포 정책의 일부).

### D7. Screener 무차별 적용 — 자산군 사전 필터 UI 필수, 기본 = 보통주

- 현 `screen_active_codes` 는 `list_active` 전체에 conditions AND. ETF 혼입 시 "PER<10" 이 ETF 를 N/A→False 로 **자동 탈락**(기능 안전, 단 사용자 혼란).
- **권고**: universe 사전 필터(security_type 선택)를 screen API/UI 에 추가. 기본 = `[common]`(universe 확장이 기본 동작 불변). 사용자 명시 선택 시 포함.
- **재현성**: 선택된 security_type 집합이 **screen query 일부 → result_hash 입력**(`screen_run.py` hash=query/as_of/result_codes/data_versions). 자산군 선택은 query 에 들어가 자동 freeze — 추가 freeze 키 불요.

### D8. 미해결 (T77~T79 착수 시 결정)

- **[T79 결정완료 — 2026-06-02] 리츠 일반 재무 factor(PER/EPS 등) = 값 + Fidelity 라벨. N/A 강제 기각.**(D3-c) — 리츠는 DART 법인이 존재해 순이익 fact 가 *실재*하므로 PER/EPS 가 값을 산출(N/A 아님). 우선주(D3-b: 우선주가격 ÷ 보통주 EPS = 분자/분모 모집단 불일치 → 정의 불명 → N/A)와 결정적으로 다르다 — 리츠 PER 은 분자/분모 일관(정의 명확)하고, "부동산 감가상각 왜곡·업계는 FFO 선호"는 정의 불명이 아니라 **해석 한계**다. 해석 한계를 사실 은폐(N/A 강제)로 다루면 §2.3 Active Inspection("리츠 PER 은 보면 안 됨"을 시스템이 정함=의제 설정) + §2.1 Fidelity + [[adr-0024-asset-class-statistical-limits]] 디스클로저 정신을 삼중 위반. 해석 한계는 ADR-0024 패턴대로 **사실 + 소표본 디스클로저**(리츠 모집단은 작아 percentile 류에서 small_sample 자동 부착)로 다룬다. FFO 기반 지표(`ffo-multiple:reit`)는 데이터 합류(R4) 시 *추가* canonical_id 로 PER 과 병존(ADR-0002 multi-id) — PER 을 지우는 게 아니라 FFO 를 더한다. **귀결: 코드 무변경**(N/A 강제 분기 미신설). 회귀 가드 = `server/tests/test_api/test_screen_reit_filter.py`(리츠 일반 재무 factor 값 산출 고정) + `test_db_universe_distribution.py` §12(3-way partition·리츠 소표본). **FFO/NAV 전용 Primary 계정·canonical_id 는 데이터 의존(R4)으로 본 사이클 미포함**(T78 우선주 전용 factor 미포함과 동형).
- **우선주 `per:preferred-over-common-eps` multi-id variant 도입 여부**(D3-b 양면) — default N/A 는 확정, 정의-명시 variant 노출은 미정.
- **소표본 분포 디스클로저 임계값·UI 형태**(D5) — [[adr-0024-asset-class-statistical-limits]] 소관. partition n<임계 시 percentile/zscore 통계적 한계 표시.
- **[리츠 FFO 출처 결정 — 2026-06-02, P0 실 DART 검증] 리츠 FFO/배당 1차 출처 = DART 재무제표(기존 financial 파이프라인 확장, KRX 아님).** 실 리츠 종목(신한알파리츠 293940·ESR켄달스퀘어 365550·SK리츠 395400) DART `fnlttSinglAcntAll` 응답에서 구성 계정 **실재 확인**: 순이익(`ifrs-full_ProfitLoss`→net_income, 기존 매핑)·영업수익(`ifrs-full_Revenue`→revenue, 임대수익)·배당(`ifrs-full_DividendsPaidClassifiedAsFinancingActivities`)·감가상각(`ifrs-full_AdjustmentsForDepreciationExpense` — 일부 리츠 별도/일부 통합조정)·투자부동산(`ifrs-full_InvestmentProperty`). **FFO = net_income + depreciation**(감가상각 보고 리츠만 값, 미보고는 자연 N/A=§2.1 Fidelity). **dividend-yield:reit = 연배당총액/시총**(전 리츠 가능). 리츠는 DART 법인 재무 실재라 ETF(KRX NAV 미확보)와 구분. **재현성 경로(중대): 리츠 factor 를 빌트인 pack(1.0.0) in-place 추가 금지** — content_hash 변경 → 기존 저장 run 의 `snapshot_versions` frozen factor_pack_content_hash 와 불일치 → §2.10 재현 붕괴. **reference custom pack(`user/` tier, ADR-0022 D9 영속화/import 활용) 또는 PackRegistry version 발행(D9.4 Phase 2)으로 제공.** account 매핑·resolver field 등록은 데이터 정규화라 재현성 무관(factor pack 미변경) — 선행 가능.
- **[T77 결정완료 — 2026-06-02] ETF NAV/괴리율 1차 출처 = KRX(한국거래소 공식 일일 공시).** 운용사 공시는 보조(중복 확인용). KRX 정보데이터시스템이 ETF 의 NAV·iNAV·괴리율을 거래소 공식 자료로 일일 산출·공시하므로 §2.5 Open Data Sufficiency 충족 1차 자료다. ADR-0003 adapter + SourceCitation 패턴 재사용(effective_date 동반 PIT). **단 실제 NAV/괴리율/AUM 데이터 어댑터·전용 canonical_id(`nav-premium:krx`·`aum:krx`)는 데이터 합류(R4) 시점 — 본 사이클(T77)은 출처 정책만 결정, ETF 전용 factor 미포함**(T78 우선주·T79 리츠 전용 factor 미포함과 동형). **D3-a(ETF universe 포함 + 재무 factor 자연 N/A, 추가 코드 0) 회귀 가드** = `server/tests/test_api/test_screen_etf_filter.py`(ETF 재무 자연 N/A·필터·hash) + `test_db_universe_distribution.py` §13(ETF 시총 partition — "구멍은 N/A 아닌 field")·§13 4-way partition(universe 확장 트랙 완결).
- 우선주-보통주 lineage 연결(같은 발행사 묶기) — Compare 뷰 필요 시.
- Sector view 와 ETF/리츠의 KSIC 분류 상호작용(work-order §7.6) — Sector view 도입 시.

## Rationale

- **factor N/A propagation 재사용**이 ETF 경계의 가장 깨끗한 해 — universe 제외(§2.3 위반) 대신 자연 N/A.
- **분포 partition(D5)이 이 ADR 의 심장.** 재무 factor 는 N/A 제외로 자동 보호되나 가격/시총 field 가 구멍이고, 이건 재현성(D6)과 한 몸이라 `DISTRIBUTION_POLICY_VERSION` 으로 freeze.
- **우선주 EPS 차용 거부**는 ADR-0004 multi-id 정신 + Fidelity + No Advice 삼중 일관.
- **단계화·security_type 선결**은 갭 E(분류 부재 시 silent 혼입) 회피 — D5/D7 이 D2 에 구현 의존.

## Consequences

### Positive
- universe 가 ETF/우선주/리츠로 확대되며 §2.3 Active Inspection 충족, factor 의미는 자산군별로 보존.
- 분포 partition + 버전 bump 로 universe 확장이 §2.10 재현성을 깨지 않음(D6 증명).
- No Advice 경계가 자산군 필터 레벨에서 못박힘(기본 보통주 + CI 게이트).

### Negative / 비용
- security_type 메타 신설 + 전 종목 backfill(선결).
- `db_universe_distribution` 모집단 partition 수정 + `DISTRIBUTION_POLICY_VERSION` bump.
- 자산군별 전용 fact/canonical_id(ETF NAV·리츠 FFO) 신설 — fact 파이프라인 확장.
- screen API/UI 에 security_type 사전 필터 + result_hash query 확장.

### 액션 (문서 갱신)
- **work-order `m2-milestone.md` §3.6 갱신**: T77~T79 에 "P0 공통 선결 = security_type 메타 도입(D2)" 행 추가 + 착수 순서 권고(T78→T79→T77, D1) 주석. T번호=주제 매핑은 유지(재정의 아님).
- **ADR-0007 minor revision**(D4 연동): 자산군 필터 동등 가시성 + `<RecommendedETFs>`/`<TopReits>` 검색 게이트를 D8.2 에 추가.
- **ADR-0024 신설 트리거**(통계 한계 디스클로저, CONCEPT §11 원 "ADR-013" 의 0024 재배정): 첫 partition 자산군(우선주, T78) 합류 시 소표본 디스클로저 결정 필요(D5).
- **CONCEPT §11 갱신**: 491행 예약표 `ADR-013`(통계 한계) → `ADR-0024` 로 정정(0013 은 forbidden-word ADR 이 이미 ACCEPTED 점유).

## "이대로 가면 깨지는 지점" (ADR 이 닫는 갭)

- **갭 A — 분포 모집단 오염(가장 치명적).** D5 partition 없이 ETF/우선주를 `list_active` 에 넣으면 가격·시총 percentile 이 이종 혼합(재무 factor 는 N/A 제외로 자동 보호되나 N/A 아닌 field 가 구멍). → D5 partition + 버전 bump.
- **갭 B — 재현성 붕괴(분포 경유).** percentile 쓴 저장 run 이 모집단 변화로 재현 깨짐. 갭 A 와 한 몸 — partition 을 정책 버전으로 freeze. universe 확장이 `DISTRIBUTION_POLICY_VERSION` 을 올리는 **명시적 사건**.
- **갭 C — No Advice 우회(우선주 EPS 차용).** 보통주 EPS 로 우선주 PER = "저평가" 가짜 서사. → D3-b-1, 발행사-귀속 factor 는 우선주 N/A.
- **갭 D — 자산군 필터의 큐레이션 변질.** "추천 ETF" 컴포넌트 가능성. → ADR-0007 D8.2 CI 게이트에 자산군 변형 추가, 기본 필터=보통주.
- **갭 E — security_type 부재로 silent 혼입.** 분류 필드가 없어 ETF fact 적재 순간 `list_active` 무차별 포함. D5/D7 이 D2 에 선행 의존 → **security_type 신설이 T77~T79 의 0번 선결.**

## Alternatives 기각

- **A. ETF/우선주/리츠 universe 완전 제외(별도 모듈·분리 DB).** §2.3 위반(관측 대상 분리=의제 설정) + 분포/screener 인프라 중복. security_type partition 으로 같은 universe 내 분리가 우위. 기각.
- **B. 단일 모집단 유지(분포 partition 없음).** 갭 A/B(이종 분포 + 재현 붕괴), ADR-0022 PIT 분포 freeze 무효화. 기각.
- **C. 우선주에 보통주 EPS 차용 PER.** 갭 C(Fidelity + No Advice), ADR-0004 multi-id 위반. 기각.
- **D. 세 자산군 동시 도입.** factor 의미 충돌 결이 자산군마다 달라 통제 흐려짐(D1). 기각.
- **E. ETF 재무 factor 0/dummy fill.** ADR-0004 D8 "0 fall-back 금지" + factor_evaluator "silent 0 fill 금지" 정면 위반. 기각.

## 단계화 (work-order T순서 권고)

```
[P0 선결 sub-task] security_type 메타 도입(D2) — T77~T79 공통, 신규 T번호 아님. 전 종목 common backfill. D5/D7 의존 기반.
   ↓
T78 우선주 — fact 재사용, 발행사-귀속 factor N/A + 배당/괴리율(D3-b)
   ↓
T79 리츠 — FFO/NAV Primary 계정 신설(D3-c)
   ↓
T77 ETF — 재무 전면 N/A, NAV/괴리율/AUM 전용 fact(D3-a)
   ↓ (각 단계마다)
분포 partition(D5) + DISTRIBUTION_POLICY_VERSION bump + AC-M2-C-05 재현 회귀 검증
```

**지금 못박음**: D1(순서)·D2(security_type·PIT 불변)·D3(N/A 경계 3갈래)·D4(중립 필터)·D5(분포 partition+버전 bump)·D6(재현 조건)·D7(사전 필터+기본 보통주).
**M2 착수 시로 미룸(D8)**: 리츠 일반 PER 처리·NAV 출처·우선주-보통주 lineage 연결·Sector/KSIC 상호작용.
