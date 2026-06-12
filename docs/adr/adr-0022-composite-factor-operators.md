# ADR-0022: Factor Lab Composite 연산자 집합 + No Advice 경계

| | |
|---|---|
| **Status** | PROPOSED (M2 선결 — T70b schema 확장 착수 전 확정 필요) |
| **Date** | 2026-06-01 |
| **Deciders** | 사용자 |
| **Related** | [[adr-0007-default-ui-rules]] D1/D2.2/D5/D8.2/D8.3 (랭킹·점수 금지, percentile 허용, 자동 게이트), [[adr-0002-factor-fact-model]] (factor pack), `docs/CONCEPT.md` §2.2 No Advice / §2.3 Active Inspection / §2.7 Observation / §2.10 Reproducibility; `docs/work-orders/m2-milestone.md` T70a/T70b/T73/T74; oracle 분석(2026-06-01) |

## Context

M2 Factor Lab 은 사용자가 Primary(재무 원문) → Derived(PER/ROE) → **Composite(multi-factor)** DAG 를 정의·저장·공유하게 한다. 그런데 Composite "score" 가 실제 의미하는 연산 — 이종 단위 factor(PER=ratio, ROE=percent, 시총=krw)의 **정규화(z-score/percentile) 후 가중합** — 을 표현할 어휘가 현 `factor-pack-v1.json` `exprOp` enum(`add/sub/mul/div/sum_last_n_quarters/avg/ratio_pct`)에 없다.

**핵심 함정(Momus M1 review C1):** Composite 를 표현 가능하게 만드는 연산자(rank/percentile/normalize)가 **바로 No Advice 경계의 회색지대**다. "누가 정의하느냐(사용자)"만 통제하면 부족하고 **"무엇을 정의 가능한가(연산자 집합)"**를 통제해야 한다. rank 를 schema 에 넣는 순간 ADR-0007 D5.2 가 금지한 "시스템 큐레이션 Top-N" 을 사용자 정의 factor 라는 우회로로 합법화하는 통로가 열린다.

oracle 분석의 핵심 구분선: **(1) 단조 변환 vs 유니버스-상대 변환, (2) 연속 값 출력 vs 이산 순위 라벨 출력.** `rank` 만이 D5.2 를 직접 위반(서수 = 가치 서사), percentile/zscore 는 §2.7 Observation 측에 안착 가능하되 **PIT 분포 freeze + 출력 표시 게이트** 두 조건 동반 필수.

## Decision

### D1. `exprOp` 확장 범위 — 허용/금지 명문화

**조건부 허용 (T70b 에서 schema 추가):**

| 연산자 | 출력 | 입력 의존 |
|--------|------|-----------|
| `weighted_sum` | unitless(정규화 입력 전제) | 종목-국소 |
| `zscore` | unitless(σ) | **유니버스-상대** |
| `percentile` | percent [0,100] | **유니버스-상대** |
| `min_max_scale` | unitless [0,1] | **유니버스-상대** |
| `winsorize` / `clip` | 입력 단위 보존(전처리) | 유니버스-상대/상수 경계 |

**절대 금지 (enum 에 넣지 않음 — 처음부터 부재가 안전. 추후 추가는 별도 ADR + ADR-0007 D9 보호 강화 절차):**

| 연산자 | 사유 |
|--------|------|
| `rank` / `top_n` / `bottom_n` | ADR-0007 D5.2 시스템 큐레이션 우회(서수 라벨 = "1등이 좋다" 가치 서사). community pack 공유 시 "정의자=사용자" 방어선 붕괴 |
| `sign` / `step` / 임계 라벨 | §2.2 Buy/Hold/Sell 라벨과 동형(연속값에 임의 임계선 → 이산 신호) |

**순위 기능 대체:** 사용자가 순위를 원하면 percentile factor 를 만든 뒤 `ResultsTable` 의 **user-driven column sort**(D1 허용 경로)로 본다. rank 를 factor 출력(데이터 구조)에 박는 것과 표시 시점 사용자 정렬은 No Advice 관점에서 질적으로 다르다. 기능 손실 0.

### D2. 유니버스-상대 vs 종목-국소 이분법

`zscore`/`percentile`/`min_max_scale`/`winsorize`(분위수 경계) 는 **유니버스 분포**에 의존 → 단일 종목 `DbFieldProvider`(현 구조)로 평가 불가. **유니버스-스코프 분포 provider 가 신규 필요**(T70b/T72 아키텍처 함의). 분포 계산 = 유니버스 × as_of factor 평가의 2차 집계(O(1)→O(N)) — 분포 사전계산/캐싱 레이어 요구.

### D3. PIT 분포 규칙 (재현성 §2.10 — M1 foundation 연장)

유니버스-상대 연산은 분포에 의존하므로 분포를 PIT-freeze 하지 않으면 같은 Run 이 시점마다 다른 percentile 을 내 재현성이 붕괴한다. 규칙:

1. **모집단 = as_of 시점 active universe** (상장폐지 종목 포함 — survivorship bias 방지, 미상장 제외).
2. **분포 입력값 PIT** — 유니버스 각 종목 factor 를 각자 as_of active fact 로 계산.
3. **N/A 종목 모집단 제외** — percentile 은 valid 모집단 기준(factor_evaluator N/A propagation 일관).
4. **tie-breaking 결정성 고정** — 동값 처리(평균/최소) 명시. 비결정 정렬 금지.
5. **분포 정책 버전 + 통계량 hash 를 Screen Run snapshot `data_versions` 에 freeze** (`EVALUATOR_POLICY_VERSION` 동형). 저장 run 재실행 시 동일 percentile 재현 보장.

### D4. 출력 표시 제약 (M1 chart-visual-gate 패턴 복제 — T74)

연산은 사실을 만들고, **표시 게이트가 그 사실이 가치 서사로 변질되는 것을 막는다.** 3중 강제:

1. **전용 렌더러 격리** — composite score 셀 컴포넌트가 등락색(`#dc2626`/`#2563eb`)·녹색·랭킹 배지를 **구조적으로 받을 수 없게** 설계. 색 상수 export + 게이트 테스트.
2. **시각 게이트 테스트(T74)** — chart-visual-gate 동형: score 출력에 등락색 부재 / "1위·Top·Best·순위 배지" 부재 / 자동 하이라이트 부재 / percentile 은 **grayscale gradient 만**(ADR-0007 D2.2 명시, 색조 컬러맵 금지).
3. **default sort 금지** — composite score 컬럼은 default 정렬이 되어선 안 됨(D1: 시가총액 default). user-explicit 클릭 정렬만. CI 게이트로 "default sort ≠ score 컬럼" 검증.

### D5. 빌트인 0 게이트 (ADR-0007 D5.1 자동화 — T73)

빌트인 pack(`pack_slug == "speculum-builtin"`)에 유니버스-상대 op 또는 `weighted_sum` 포함 factor **0개**. CI 게이트가 빌트인 pack AST 를 정적 검사(ADR-0007 D8.2 `<RecommendedStocks>` 검색 게이트 패턴). silent regression("퀄리티 점수" 빌트인 추가) 차단.

### D6. Fidelity 확장 (§2.1)

composite 의 **가중치·정규화 모집단·연산식이 UI 에 항상 visible.** `SourceAttribution`(ADR-0007 D8.1) 의무를 composite 에 확장 — 사용자가 score 의 산출 과정을 항상 추적 가능.

### D7. ADR-0007 D5 개정 연동

- D5.1 에 "사용자 정의 composite 의 허용 연산자 = ADR-0022 D1 확정 집합" 명시.
- D5.2 에 "rank 연산자 부재 — 순위는 표시 시점 user sort 로만" 추가.
(별도 ADR-0007 minor revision, T73/T74 작업 시 동반.)

### D8. Pack 간 identity 충돌 해소 — community/custom import (T75, 2026-06-02)

D1~D7 이 "무엇을 연산 가능한가"(No Advice 경계)를 다뤘다면, D8 은 identity 3-tier 의 **운영 무결성** 을 다룬다. community/custom pack 을 수동 JSON 으로 import 할 때 같은 `canonical_id` 가 서로 다른 정의를 주장하는 충돌(R3)을 **silent override 없이** 해소한다 (Norma §2.3 "조용한 덮어쓰기 금지" 의 인스턴스 — No Advice 가 아니라 identity 무결성 원칙).

- **D8.1 — identity 3축 분리.** factor 정체성을 세 축으로 분리해 충돌을 정밀 판정한다:
  - **`canonical_id` = 이름** — 같으면 충돌 *후보*(곧바로 충돌 아님).
  - **per-factor content hash = 정의** — factor dict 를 JCS 정규화·SHA-256 하되 **`uuid` 를 hash 입력에서 제외**(`compute_content_hash(factor, exclude_key="uuid")`). uuid 를 포함하면 *동일 정의* 를 두 사용자가 각자 만들어도 다른 정의로 오판되어 거짓 충돌이 폭증한다. 같은 canonical_id + 같은 hash = 동일 정의(충돌 아님, idempotent re-import). 같은 canonical_id + 다른 hash = **진짜 정의 충돌**.
  - **`uuid` = 개체 식별자** — 같은 uuid + 다른 canonical_id/정의 = uuid 재사용 위반(거부 — uuid 는 불변 개체 ID, pack 내부 중복 검사 `validate_identity` 의 pack-간 확장).
- **D8.2 — canonical tier 불가침.** import factor 가 빌트인 canonical(`pack_slug=="speculum-builtin"`)의 `canonical_id` 를 다른 정의로 주장하면 **빌트인 정의가 이긴다 + import factor 는 점유 불가**. 허용 resolution = `skip` / `rename`(새 `user/`·`community/` namespace canonical_id 부여)뿐. **`replace` 금지**(`CanonicalOverrideForbidden`) — 빌트인 정의를 사용자 import 로 덮으면 그 이후 모든 분석이 오염된 정의를 쓰므로 코드상 도달 불가여야 한다.
- **D8.3 — community/custom 충돌은 명시 resolution.** 두 사용자 정의가 같은 canonical_id 를 다툴 때 `skip`(import 제외) / `rename`(namespace 치환) / `replace`(명시적 override)를 사용자가 **각 충돌마다 명시 선택**. 자동 진행 없음.
- **D8.4 — 미해결 = 거부(fail-loud).** import 는 2-pass: ① `import-check`(dry-run — 충돌 리스트만 반환, 자동 적용 0) → ② `import`(각 충돌의 resolution 동반). resolution 이 누락된 충돌이 하나라도 있으면 **422, import 전체 거부**. "미해결 = 진행" 이 아니라 "미해결 = 거부" 가 default 여야 silent override 가 구조적으로 불가능하다.
- **D8.5 — self-identifying export.** custom/community pack export 는 marker wrapper(`speculum-factor-pack-export-v1`) + tier(pack_slug 도출) + **`content_hash` 봉인**(`compute_pack_hash` 로 채움). hash 없는 export 는 수입측이 "다른 정의" 를 판정할 권위 기준을 잃으므로 봉인 필수. 빌트인 export 는 무의미(repo 에 이미 존재) — `tier in {community, user}` 만.
- **범위 한정.** custom pack 은 아직 영속화되지 않으므로(in-memory loader) import = "검증·충돌해소된 JSON 을 editor 상태로 수용" 하는 **stateless** 경로다. user_id scoping·DB 저장은 별도 작업(M3+). 공유 서버 아님(수동 JSON 까지, work-order §8 정합). T68(OAuth) 무관.

(구현: `server/app/services/factor_pack_identity.py` 신규 + `factor_packs.py` import-check/import/export route. 회귀 가드 = pack-간 충돌 negative test.)

### D9. Custom pack 영속화 — immutable append-only user-scoped 저장 (M3 부분 당김, 2026-06-02)

stateless export/import(D8)로 갈음했던 "저장"(AC-M2-F-02)을 user-scoped DB 영속으로 구현. T68 인증 골격 + T69 user 격리 위에서 Factor Lab custom pack 을 저장한다. **immutable hash(D4)와 재현성 coupling 때문에 Notes 의 mutable CRUD 가 아니라 append-only version 저장**을 택한다.

- **D9.1 immutable append-only.** 같은 `(user_id, pack_slug, version)` 의 "수정"=새 version row **append**(UPDATE 없음). `content_hash`=pack identity(D4) + `snapshot_versions` 의 `factor_pack_content_hash/slug/version` freeze coupling 때문에, in-place UPDATE 는 "hash=identity" 원칙과 §2.10 재현성(저장 run 이 가리킨 version/hash 조합 소멸)을 깬다. 같은 (slug,version) **다른 정의 → 409**("version 을 올려 저장"), 같은 정의 → idempotent. `UNIQUE(user_id, pack_slug, version)`. content_hash unique 미적용(D8.1 거짓충돌 논리 — 두 사용자 동일 정의 가능).
- **D9.2 save/load 무결성.** save: `validate_custom_pack` + `compute_pack_hash` 재봉인(클라이언트 hash 불신). load: `validate_hash` 재검증(DB 변조 탐지, defense-in-depth). **canonical tier(`speculum-builtin`) slug 저장 거부**(빌트인 사칭 차단, export route 의 canonical 거부 패턴 복제). M2 는 `user/` tier 만(community 공유 서버 M3+, D8 범위한정 일관).
- **D9.3 identity 충돌 합류 = user 격리 우선.** import-check 의 `existing_index` 는 **빌트인 canonical 만 유지**(타 user 저장 pack 비가시). `build_existing_index` 가 일반화돼 합류는 trivial 하나, 타 user pack 을 비교 대상에 넣으면 user B 가 user A 의 pack 정의를 추론(IDOR/정보 누출, ADR-0021 D2 위반). **user 격리 > 전역 identity 무결성**. 본인 pack 합류(같은 컨텍스트 충돌 안내)는 user_id 필터 동반 Phase 1.5 deferred.
- **D9.4 screen 실행·재현 freeze = Phase 2 deferred.** Phase 1 = 저장/목록/불러오기 + evaluate(저장 pack 을 editor 로 불러와 기존 stateless evaluate 에 전달). 저장 pack 으로 **screen 실행**하면 `snapshot_versions`(현재 DEFAULT_PACK 하드코딩 순수함수)가 custom pack (slug,hash,version)을 freeze 해야 하고 `reproduce` 가 재로드해야 하는 **PackRegistry 재설계**(별도 ADR)가 필요 — 재현성 폭탄이므로 본 작업에서 분리. **snapshot_versions DEFAULT_PACK freeze 불변 유지.**
- **D9.5 No Advice / 시스템 생성 0.** seeder/migration/builtin 어떤 경로도 custom_packs row insert 금지(생성=인증 route 뿐). owner-check(user_id keyword-only, IDOR 404). factor name/description 은 `validate_custom_pack` 의 forbidden_vocab(SYSTEM scope)로 저장 전 검사(공유 가능성 — Notes USER_PRIVATE 와 달리 검사).

(구현: `custom_packs` ORM/migration 0017/repository(append-only)/route(save/list/get/delete) — notes 패턴 + export 의 validate+seal. PackRegistry(D9.4 Phase 2)는 별도 ADR.)

## Rationale

- **percentile 은 사실이다.** "PER 이 유니버스 하위 20%" 는 "종가 > 시가"(차트 등락색 허용 근거, D8.3)와 동형 관측이며, ADR-0007 D2.2 가 이미 "Percentile 표시(분포 내 위치, 가치 무관)" 를 허용한다. 분포 규칙만 고정되면 누구나 재현 가능(§2.5).
- **판단은 연산이 아니라 라벨링에서 발생한다.** percentile 값 `18.3` 은 사실, "저평가 구간" 라벨은 §2.2 위반(D4 금지어). → 연산 허용 + 출력 게이트.
- **rank 는 불필요하며 위험하다.** percentile 이 같은 정보를 가치 서사 없이 제공. rank → 명시정렬 = D5.2 Top-N 우회. "코드베이스에 없는 게 안전"(ADR-0007 Alternative C).
- **학술 관행과의 절충:** Speculum 은 계산 도구(z-score/percentile/가중합)는 제공하되 끝단의 선택·라벨링(rank→Top-N→Buy)은 거부. "pandas 는 `df.rank()` 를 제공하지만 '사라'고 하지 않는다"(CONCEPT §1.1 정체성).

## Consequences

### Positive
- Factor Lab 이 학술 multi-factor 분석(정규화+가중합)을 표현 가능 — M2 정체성 충족.
- No Advice 경계가 **연산자 집합 레벨**에서 못 박혀 우회로 차단(rank 부재).
- PIT 분포 freeze 로 composite 도 §2.10 재현성 유지.

### Negative / 비용
- 유니버스-스코프 분포 provider 신규(O(N) 평가 + 캐싱) — 아키텍처 부담(T70b/T72).
- 분포 정책 버전·통계량 hash 를 snapshot 에 추가 — schema 1.1→1.2 가능.
- 출력 게이트 2종(T73/T74) 추가 구현.

## Alternatives 기각
- **A. rank 포함 + 출력만 제약** — rank 출력 자체가 서수 라벨이라 출력 제약으로 가치 서사 제거 불가. community pack 우회로 잔존. 기각.
- **B. Composite 전면 금지(Derived 까지만)** — M2 Factor Lab 정체성(multi-factor) 포기. ROADMAP §4 위반. 기각.
- **C. 정규화 없이 raw 가중합만** — 이종 단위 합산은 수치적 무의미. 기각.

## 미해결 (M2 착수 시 결정)
- 분포 캐싱 아키텍처 상세(사전계산 배치 vs 요청시 계산).
- `weighted_sum` 가중치 합 정규화(합=1 강제 여부).
- winsorize 경계 default(1%/99% 등).
