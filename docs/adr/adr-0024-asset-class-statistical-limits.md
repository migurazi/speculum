# ADR-0024: 소표본 분포 통계 한계 디스클로저 (자산군 partition 副작용)

| | |
|---|---|
| **Status** | PROPOSED (T78 우선주 = **첫 partition 자산군**으로 트리거 — 동일 디스클로저가 T79 리츠·T77 ETF 의 작은 모집단에도 공통 적용. universe-상대 op 표시 전 확정 필요) |
| **Date** | 2026-06-01 |
| **Deciders** | 사용자 |
| **Related** | [[adr-0023-universe-expansion]] D5(분포 모집단 security_type partition — 소표본 副작용)/D8(임계값·형태 미해결로 본 ADR 위임), [[adr-0022-composite-factor-operators]] D3(분포 모집단=as_of active·N/A 제외·DISTRIBUTION_POLICY_VERSION), [[adr-0007-default-ui-rules]] D2.2(percentile grayscale·가치 무관 표시); `docs/CONCEPT.md` §2.1 Fidelity / §2.5 Open Data Sufficiency / §2.7 Observation / §11(원 "ADR-013" 예약 → 0024 재배정); `db_universe_distribution.py`(FieldDistribution.n·_build_field_distribution) |

## Context

ADR-0023 D5 가 분포 모집단을 `security_type` 으로 partition 하면서(T78 구현 완료) **作은 자산군(우선주·리츠 등)의 모집단이 통계적으로 빈약**해지는 副작용이 실재화됐다. 현 `db_universe_distribution.py`:

- `FieldDistribution.n`(모집단 크기) 필드는 **이미 존재**한다.
- `_build_field_distribution` 의 소표본 처리: `n==0` → 전부 None(전 종목 N/A), `n<=1` → stddev None(zscore N/A). **여기까지는 안전.**
- **그러나 `percentile` 은 `2<=n<임계` 에서도 값을 산출**(거친 계단함수 — n=3 이면 percentile 이 0/33/67/100 4단계뿐). `zscore` 도 n=2 면 ±1σ 두 점만. 즉 **값은 나오나 통계적 해석이 무의미**한 구간이 partition 으로 새로 열렸다.
- **결정적 구멍**: provider 의 `percentile`/`zscore` 가 `Decimal` 만 반환하고 **`n`(모집단 크기)을 동반하지 않는다.** 호출자(factor_evaluator → UI)가 "이 percentile 이 N=3 모집단에서 나온 것"임을 알 길이 없다. §2.5 Open Data Sufficiency(데이터가 결론을 지지하기에 충분한가) 위반 위험.

§2.7 Observation 관점: percentile 값 자체는 사실(관측)이다. 문제는 **그 사실의 통계적 한계가 사용자에게 보이지 않는 것**. → N/A 강등(과잉, 사실 은폐)도, 무경고 표시(과소, 한계 은폐)도 아닌 **사실 + 한계 디스클로저**가 정답.

## Decision

### D1. 소표본 임계값 — `SMALL_SAMPLE_THRESHOLD = 30`

- 모집단 `n < 30` 이면 해당 field 의 percentile/zscore/min_max_scale 을 **"소표본"으로 표식**. 30 은 **단일 디스클로저 트리거**이지 통계적 정설이 아니다.
- **연산별 소표본 문제의 결이 다름(정직한 근거)**: percentile/min_max 의 소표본 문제는 **분위/극값 해상도**(ADR-0023 D5·본 ADR Context 가 진단한 "거친 계단함수" — n=3 이면 percentile 이 0/33/67/100 4단계뿐, min_max 는 극값 2점 의존)이지 중심극한정리(CLT)가 아니다. CLT(표본 *평균*의 정규근사)는 평균·표준편차에 의존하는 **zscore 에만 느슨히** 닿는다. → **percentile/min_max 에 CLT 근거를 대지 않는다.** 30 은 세 연산의 "모집단이 작아 상대지표 해석이 거칠어지는" 구간을 **같은 임계로 묶는 실용 선택**.
- **단일 임계(자산군·연산 무차별).** 자산군별/연산별 차등 임계는 임의성↑·검증성↓ → 기각. 모든 partition 모집단·universe-상대 op 에 같은 30 적용.
- 상수로 export(테스트·UI 공유). **임계값 변경은 표시 정책 변경일 뿐 값 불변(D4) — `result_hash`/`DISTRIBUTION_POLICY_VERSION` 무영향**, changelog 명기로 추적.

### D2. 디스클로저 메커니즘 — N/A 강등 아님, `sample_size` 동반 + `small_sample` 플래그

- `n < 30` 의 percentile/zscore 를 **N/A 로 강등하지 않는다**(값은 §2.7 사실). 대신:
  - provider 의 percentile/zscore 반환을 **값 + `sample_size`(=n) 동반** 형태로 확장(또는 호출자가 조회 가능한 부수 정보). `FieldDistribution.n` 을 표면으로 노출.
  - `n < SMALL_SAMPLE_THRESHOLD` → `small_sample=True` 플래그. UI 가 이를 받아 **디스클로저** 표시("모집단 N개 — 소표본, 통계적 해석 주의").
- **N/A 와의 경계(기존 코드 확정 — 미해결 아님)**: 
  - `n==0` → 값 None(전 종목 N/A, 기존).
  - `n==1` → **zscore N/A(stddev None)** / **min_max_scale N/A(max==min → span 0; stddev 미사용)** — 두 N/A 의 코드 원인이 다름(기존). **percentile 은 코드상 결정적으로 `100`** — `percentile`(`db_universe_distribution.py:365-390`)이 `(bisect_right(sorted_values, value) / n) * 100` 이고 자기 자신이 모집단에 포함되면 `(1/1)*100`. **"100 vs N/A 미해결"이 아니라 이미 100 으로 확정.** 이를 N/A 로 바꾸는 것은 tie-break/N/A 규칙 변경 = `DISTRIBUTION_POLICY_VERSION` bump 사건이므로 **값(100)은 보존하고 `small_sample` 디스클로저로 처리.** 
  - **n==1 → percentile 100 이 가장 날카로운 케이스**: "자기 혼자뿐인 분포의 100 percentile" 을 디스클로저 없이 보면 "최상위"로 오인(§2.5 위반 표본). small_sample(특히 n==1) 디스클로저가 이 오인을 막는 주목적.
  - `2 <= n < 30` → 값 산출 + `small_sample=True`. `n >= 30` → 값, 플래그 false.

### D3. 출력 표시 제약 (§2.2 No Advice / ADR-0007 D2.2) — 선언이 아니라 강제

- 디스클로저는 **사실 텍스트만**: "모집단 N개" + "소표본" 중립 라벨. **금지**: "신뢰할 수 없음/부정확/무시하라" 같은 가치·지시어, 등락색, 경고 적색. ADR-0007 D2.2 grayscale 정신 — 디스클로저도 neutral 톤.
- percentile 자체 표시(grayscale gradient, 색조 컬러맵 금지)는 ADR-0007/0022 D4 게이트 그대로. 소표본 디스클로저는 그 위에 **모집단 크기 사실**을 덧붙일 뿐.
- **강제(테스트 — 선언만으로 불충분):**
  1. **forbidden-words 통과 의무**: 디스클로저 텍스트는 `ForbiddenWordsGuardMiddleware`(ADR-0007 D4.4) `SYSTEM` scope 검사 대상. 라벨 어휘가 `shared/forbidden-words.json` 평가어와 충돌 없음을 보장("주의"·"모집단 N개"는 안전, "부정확/신뢰불가"는 D4.1 평가어 → 금지). i18n 라벨도 점검.
  2. **시각 게이트 테스트(chart-visual-gate 동형, T74 패턴)**: 디스클로저 컴포넌트가 등락색(`#dc2626`/`#2563eb`)·적색 경고·랭킹 배지를 **구조적으로 받을 수 없게** 설계 + 색 상수 부재 검증 테스트(`small-sample-disclosure-gate.test.tsx` 동형).
  3. **default sort 무관**: small_sample 은 정렬 키가 아님(percentile 값으로만 user-explicit 정렬, ADR-0022 D4 일관).

### D4. PIT / 재현성 (§2.10) — 두 객체를 분리

`sample_size`(n)와 `SMALL_SAMPLE_THRESHOLD`(30)는 재현성 지위가 **다르다**. ADR-0022 의 이분법("분포 정책은 freeze / 표시는 게이트")에 각각을 정확히 배치:

- **(a) `sample_size`(=n) — 분포 정책 측(freeze 대상, 이미 충족).** n 은 모집단의 결정적 함수 — 같은 as_of·batch_id·partition 규칙이면 byte 동일. 모집단 정의(N/A 제외·tie-break·security_type partition)는 이미 `DISTRIBUTION_POLICY_VERSION`("1.1", T78 반영)이 freeze 하고, 이 버전은 `snapshot_versions.collect_active_policy_versions` → `data_versions` → `result_hash` 입력에 들어가 있다(`screen_run.py:350-357`). 따라서 n 은 **기존 메커니즘으로 이미 freeze**, 추가 키 불요.
- **(b) `SMALL_SAMPLE_THRESHOLD`(30) — 표시 게이트 측(freeze 대상 아님).** 임계값은 ADR-0022 의 *"분포 정책 freeze"* 측이 **아니라** *"출력 게이트"* 측이다. percentile/zscore **반환 Decimal 값이 임계값을 참조조차 하지 않으므로**(`percentile`/`zscore` 코드가 임계 미개입) 임계값을 바꿔도 값·`result_codes` 불변 → `result_hash` 무관. **그러므로 임계값은 `DISTRIBUTION_POLICY_VERSION` 에 넣지 않는다**(과잉 freeze 회피, Alternative D). 변경 시 changelog 명기로만 추적.

- **(c) result_codes 불변식 (치명적 강제 — 선언 아님).** `small_sample`·`sample_size` 는 **표시 전용**이다. **screener 조건 평가(pass/fail)·`_apply_universe_relative`·`result_codes` 산출 경로에 절대 진입 금지.** 만약 D5 구현이 "n<30 → percentile N/A 처리"(Alternative A 의 유혹)로 미끄러지면 즉시 result_codes 가 변동해 (b)가 붕괴한다. → **CI 게이트로 분포 op 반환 경로(`percentile`/`zscore`/`_apply_universe_relative`)가 `SMALL_SAMPLE_THRESHOLD` 를 참조하지 않음을 정적 검증**(ADR-0007 D8.2 의 검색-게이트 *정신*을 AST-grep 로 강화한 **신설** 게이트 — D8.2 자체는 수동 review 중심. `snapshot_versions` 의 ast-walk CI 선례 동형). 디스클로저는 값을 만든 *뒤* 표시층에서만 부착.

### D5. 구현 범위 (본 ADR 확정 후 별도 작업)

- **backend — 조회 메서드 권고(반환 시그니처 확장 비권고).** 두 길의 파급이 전혀 다르다:
  - ❌ **반환 확장(값→`(Decimal, n)`)**: `factor_evaluator` 의 AST 합성 전 경로(`_visit_node`/`_apply_binary`/`weighted_sum`)가 tuple 을 풀어야 함 = **evaluator 코어 전면 수정**. 게다가 `weighted_sum(percentile(per), percentile(roe))` 처럼 합성 시 sample_size 의 전파/소멸 규칙이 필요 — 과대.
  - ✅ **조회 메서드(권고)**: provider 가 `bind_target_security_type` 후 보유한 분포 캐시에서 `sample_size(field) -> int`(=`FieldDistribution.n`) 를 **별도 노출**. percentile/zscore 반환 시그니처 **불변**(`Decimal | None`) → **evaluator 무수정**. 표시층이 값과 n 을 각각 조회. `SMALL_SAMPLE_THRESHOLD` 상수 + `small_sample` 판정은 표시층/헬퍼.
  - **composite 합성 시 sample_size 귀속**: composite score(weighted_sum 등)의 디스클로저는 그 식에 쓰인 **universe-상대 leaf 들의 `min(n)`** 으로 표시(가장 빈약한 모집단이 전체 신뢰도 상한). 정확한 귀속 규칙은 **D6 미해결**(아래)로 둔다.
- **frontend**: universe-상대 factor(Factor Lab composite percentile/zscore) 표시 셀에 소표본 디스클로저(neutral, 모집단 크기). 현재 universe-상대 op 표시 UI 가 Factor Lab evaluate 미리보기뿐이므로 거기 우선 적용. 우선주/리츠/ETF 뷰(AC-M2-F-05) 도입 시 동일 컴포넌트 재사용.

## Rationale

- **사실 + 한계가 Speculum 의 정직**(§2.1/§2.5/§2.7). N/A 강등은 사실(percentile 값)을 숨기고, 무경고는 한계(소표본)를 숨긴다 — 둘 다 부정직. 디스클로저가 유일한 정직.
- **n 은 이미 계산돼 있다**(`FieldDistribution.n`) — 노출만 하면 됨. 구현 비용 최소.
- **ADR-0022 이분법에 두 객체를 정확히 배치**(D4): `sample_size`(n)는 *분포 정책*(이미 `DISTRIBUTION_POLICY_VERSION` freeze), `SMALL_SAMPLE_THRESHOLD`(30)는 *표시 게이트*(freeze 아님, 값 불변→result_hash 무관). 임계값을 분포 정책 freeze 와 묶지 않는다.
- 30 은 정밀 주장이 아닌 디스클로저 트리거 — CLT 같은 통계 정설이 아니라 "모집단이 작다는 사실을 보이는" 실용 목적. percentile/min_max 의 소표본 문제는 분위/극값 해상도이지 CLT 아님(D1).

## Consequences

### Positive
- partition(ADR-0023 D5)이 연 소표본 구멍을 디스클로저로 막음 — §2.5 충족.
- n 노출로 우선주/리츠 등 작은 자산군의 percentile 해석 한계가 항상 visible(§2.1 Fidelity 확장).
- result_hash 무영향(표시 정책) — 재현성 비용 0.

### Negative / 비용
- provider 반환 시그니처 확장(값→값+sample_size) — factor_evaluator·호출부 파급(소).
- UI 디스클로저 컴포넌트 + 시각 게이트 테스트(neutral 톤 검증).

## "이대로 가면 깨지는 지점"
- **갭 A — 무경고 소표본 표시.** n 미노출 시 N=3 percentile 이 N=300 과 동일하게 보임 → §2.5 위반. D2 의 sample_size 동반이 닫음.
- **갭 B — 디스클로저의 가치어 변질.** "신뢰 불가" 같은 지시어가 들어가면 §2.2 위반(관측이 판단으로). D3 의 중립 텍스트 게이트가 닫음.
- **갭 C — 임계값을 hash 입력으로 오인.** 30 을 result_hash 나 DISTRIBUTION_POLICY_VERSION 에 넣으면 표시 정책 변경이 재현을 깬다(불필요). D4(b)가 "임계값=표시 게이트 ≠ 분포 정책 freeze" 로 차단.
- **갭 D — small_sample 이 result_codes 로 새는 경로.** D5 가 "n<30 → N/A 강등"으로 미끄러지면 screener pass/fail 이 변동해 재현 붕괴(D4 c). CI 정적 게이트(분포 op 가 임계 미참조)가 막음. **이게 본 ADR 의 가장 중요한 강제** — 디스클로저는 값 생성 *후* 표시층 전용.

## Alternatives 기각
- **A. n<임계 percentile/zscore 를 N/A 강등.** 단순. 기각 — percentile 값은 사실(§2.7), N/A 는 사실 은폐 + 작은 자산군에서 모든 상대지표 소멸(우선주 분석 불가). 디스클로저가 사실 보존 + 한계 명시 양립.
- **B. 무경고 표시(현 동작 유지).** 비용 0. 기각 — §2.5 Open Data Sufficiency 위반, N=3 을 N=300 처럼 오인.
- **C. 자산군별 차등 임계.** "정밀". 기각 — 임의성·검증성 저하. 단일 30 의 디스클로저 목적에 충분.
- **D. 임계값 정책 버전화(freeze).** "엄밀". 기각 — 값 불변이므로 재현 무관, 과잉 freeze.

## 미해결 (구현 시 결정)
- **(D6)** composite score 의 sample_size 귀속 규칙 — universe-상대 leaf 들의 `min(n)` 권고(D5)이나, 가중치·중첩 합성 시 정확한 전파 규칙 확정은 구현 시. (percentile `n==1`→100 은 코드 확정이므로 미해결 아님 — D2 에 명시.)
- 디스클로저 UI 형태(인라인 배지 vs tooltip) — 우선주/리츠 뷰 디자인과 함께.
- min_max_scale 의 소표본 동반 여부(현재 max==min N/A 외) — universe-상대 3종(percentile/zscore/min_max) 일관성.
