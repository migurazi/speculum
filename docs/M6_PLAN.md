# M6_PLAN — Factor pack identity v2 (인증된 publisher namespace) (rev3, Momus R3 OKAY)

> 마일스톤 plan / 작업 지시서. 상세 ADR = **ADR-0034 (factor-pack-identity-v2)**.
>
> **Momus R3 판정 = OKAY(착수 가능)** — R2 차단 C-1/H-1 둘 다 해소 확인. 잔존
> 권고 4건을 rev3 로 흡수(blocker 아님, 착수 병행 가능):
> - **H-2(#4 validator dispatch)**: `factor_pack.py` 의 `_VALIDATOR` 가 v1 단일
>   싱글톤 → v2 slug 를 v1 validator 가 거부. #4 T-M6-04a 에 v1/v2 validator dispatch
>   (`$schema`/slug `@` 기준, v1 byte-불변) 명시 task 추가.
> - **#2 정정**: reproduce(`load_pack_from_body`)는 hash-only(`validate_schema`
>   미호출) → v2 reproduce 는 schema dispatch 불필요. dispatch 는 저장경로(#4)뿐.
> - **M-b**: route 경로 `api/routes/custom_packs.py` 로 정정.
> - **M-a**: #5 에 v2=community tier cross-pack identity 회귀 anchor(T-M6-05c) 추가.
>
> **rev2 변경(Momus R2 — C-1/H-1 해소)**:
> - **C-1 해소(C2 재설계)**: rev1 의 "schema 버전 키를 data_versions(hash 비대상)에
>   추가" 는 **코드 반증** — `data_versions` 는 result_hash 의 JCS 입력
>   (`screen_run.py:350-357` `JCS(query, as_of, result_codes, data_versions)`,
>   `backtest_snapshot.py:158-174` 동일)이라 키 추가 시 신규 run hash drift. →
>   **frozen run 의 v1/v2 판별을 frozen slug 형식(`@` prefix 유무)으로** 전환.
>   data_versions 신규 키 0 → **hash 입력 0 변경**(기존 frozen run·신규 v1 run 모두
>   hash 불변). `content_hash`(pack body, data_versions 무관)와 `result_hash`(run
>   snapshot, data_versions 포함) 를 본 문서에서 명확히 **구분**.
> - **H-1 해소(#3 인증 모델 확정)**: publisher 저장 = **신규 `publishers` 테이블**
>   (`user_id` FK UNIQUE[user 1:1] + `handle` UNIQUE[인스턴스 유일]). 사칭 차단
>   조회 경로 = route 가 `current_user.user_id → publishers.handle → v2 slug 의
>   @{publisher} 일치` 강제.
> - **M-1**: v2 derive_tier tier = **community 재사용**(`@` prefix → community) —
>   `TierName` Literal·`_tier_rank`(`{canonical:0, community:1, custom:2}`) 변경 0.
> - **M-3**: 예약 prefix 금지 = route validate(canonical 사칭 게이트 패턴 재사용).

## 0. 한 줄 정의 + load-bearing 제약 + hash 용어

M6 는 pack 에 **인증된 publisher namespace**를 주어, 인증 저자만 자기 이름으로 pack
을 발급하고 사용자가 사칭 없이 식별하게 한다.

**load-bearing 제약(코드 확정)**: `compute_pack_hash`(`factor_pack.py:161`)가
`content_hash` 만 제외하고 **pack_slug·publisher 포함 body 전체**를 JCS+SHA-256 →
**slug 변경 = content_hash 변경**. `reproduce._resolve_frozen_pack`(`reproduce.py:
155`)이 frozen slug/hash 로 재로드+검증 → **기존 slug 변경 시 frozen run 변조 오진**.

**hash 용어 구분(rev2 — C-1 혼동 제거)**:
- **`content_hash`**: pack **body** 의 hash(`compute_pack_hash`). slug/publisher 포함,
  data_versions 무관. v1 pack 의 content_hash 는 **절대 불변**(M6 가 v1 body 미변경).
- **`result_hash`**: screen run / backtest snapshot 의 hash(`screen_run.py:357`·
  `backtest_snapshot.py:174`). 입력 = `JCS(query/as_of/result_codes/**data_versions**)`.
  **data_versions 가 입력**이므로 data_versions 에 키를 더하면 신규 run 의 result_hash
  가 바뀐다 → **M6 는 data_versions 에 신규 키를 넣지 않는다**(C2 를 slug 형식 판별로).

→ **절대 원칙**: v1 content_hash·result_hash·slug·frozen run **절대 불변**. namespace
v2 는 신규 pack opt-in. v1↔v2 **자동 마이그레이션 영구 금지**.

## 1. 스코프

### 포함 — deliverable #1~#6

| # | 항목 | 기둥 | 핵심 |
|---|------|------|------|
| 1 | **v2 namespace schema** (`factor-pack-v2.json`) | §2.8 | `@{publisher}/{slug}`, publisher 필드 display-only, 예약 prefix route 거부, ≤128 |
| 2 | **v1/v2 dual-load registry** | §2.10 | v1 resolve byte-불변. **frozen v1/v2 판별 = slug `@` 형식**(data_versions 키 0) |
| 3 | **publisher 인증 발급** (신규 `publishers` 테이블) | §2.1 | user↔publisher 1:1, handle 인스턴스 유일, 타 publisher 사칭 차단 |
| 4 | **v2 pack 생성·검증·import·저장** | §2.1 / §2.2 | derive_tier(`@`→community)·forbidden-words·content_hash(동일 recipe) |
| 5 | **v1 불변 회귀 가드** | §2.10 | v1 content_hash·result_hash byte-동일·기존 reproduce matches 유지 |
| 6 | **인증 identity 표시** | §2.1 / §2.7 | "인증된 publisher" + 외부 권위 미보증 disclosure, grayscale 중립 |

### 유보 (M6 범위 외)

- **중앙 registry 글로벌 유일성** — handle 의 인스턴스-간 충돌은 중앙 호스팅 필요
  (M4 유보 유지). M6 는 **본 인스턴스 내 handle 유일성**만. cross-instance known-limit.
- **v1→v2 자동 마이그레이션** — content_hash 파손이라 영구 미지원.
- **OAuth 외 publisher 외부 권위 증명**(도메인·verified badge) — handle 은 임의 claim
  가능(예 `goldman-sachs`), #6 disclosure 로 미보증 명시.

### release blocker

- **조건부 blocker(M-2)**: namespace 가 제3자 publisher 콘텐츠 식별·호스팅 강화 →
  ADR-0006 변호사 자문 재자문 필요 여부를 ADR-0034 판정. 자문 결과에 따라 운영 노출
  blocker 가능(M4 동일 패턴). **코드 작업은 진행 가능, 운영 노출만 게이트.**

## 2. deliverable 위상정렬

```
#1 v2 schema(@형식 확정·예약 prefix) ─→ #3 publisher 인증(publishers 테이블) ─→ #4 v2 pack ─┐
        │                                                                                     ├→ #6 표시
#2 dual-load registry(v1 불변·v2 분기·slug @ 판별) ──────────────────────────────────────────┘
        │
#5 v1 불변 회귀(전 단계 가드 — #1~#4 이 v1 content_hash/result_hash/frozen 미변경)
```

- **#1 선행**: `@{publisher}/{slug}` 형식 확정(H1)이 #2·#3·#4 입력. 순환 0.
- **#3→#4**: 인증 publisher 발급이 v2 생성(publisher 일치 게이트) 전제. 단방향.
- **#5 가드**: #1~#4 이 v1 hash/frozen 을 건드리면 즉시 fail.

## 3. deliverable 상세 + task

### #1 v2 namespace schema (§2.8)
- **task**:
  - T-M6-01a `factor-pack-v2.json`(신규 `$id`): `pack_slug` = **`@{publisher}/{slug}`**
    (`@` prefix 로 v1 pattern(`speculum-builtin|community/...|user/...`)과 형식
    **disjoint** — C1 충돌 0). publisher·slug = `[a-z0-9][a-z0-9-]*`. **총길이 ≤128**
    (ORM `String(128)` 정합 — M3). `publisher` 필드는 **display-only 명시**(identity
    단일 출처 = slug, SoT — H1). content_hash recipe(JCS·decimalString) v1 동일.
  - T-M6-01b **예약 prefix 금지(C1·M3)**: publisher handle ∈ {`community`,`user`,
    `speculum-builtin`,`speculum`} → **route validate 거부**(`api/routes/custom_packs.py:
    96` canonical 사칭 게이트 패턴 재사용). v1 tier·canonical 사칭 차단.
  - T-M6-01c `derive_tier`(`factor_pack_identity.py:50`)에 **`@` prefix → community
    분기** 추가(M-1: 신 tier 신설 안 함 — `TierName` Literal·`_tier_rank` 변경 0,
    v1 prefix 판정 코드 byte-불변). v2 pack 은 community tier(공유 pack 의미 일관).
- **acceptance**: v2 slug `@{publisher}/{slug}` 강제·예약 prefix 거부·≤128. v1 pack/
  derive_tier 무영향(회귀 green). content_hash recipe 동일.
- **가드레일**: §2.8 — identity = slug 단일 출처(publisher 필드 display-only). v1 schema 불변.

### #2 v1/v2 dual-load registry (§2.10) — slug `@` 형식 판별(C2 재설계)
- **현황(실측)**: `pack_registry.resolve`·`reproduce._resolve_frozen_pack` 이
  `slug == BUILTIN_PACK_SLUG` 이분법. **frozen run 의 slug 문자열이 그대로 보존**되어
  있으므로(기존 frozen 은 `@` 없는 v1 slug), **slug 형식으로 v1/v2 판별 가능**(신규
  data_versions 키 불필요 → result_hash 입력 0 변경).
- **task**:
  - T-M6-02a registry/`_resolve_frozen_pack`: slug 가 `@` 로 시작 → v2 resolve 분기,
    `@` 없으면 → **기존 v1 resolve 경로 byte-불변**. frozen v1 run 은 v1 slug(`@`
    없음)라 자동 v1 경로(파손 0). **정정(Momus R3)**: reproduce 의 `load_pack_from_body`
    (`pack_registry.py:59-89`)는 **`validate_hash` 만 호출·`validate_schema` 미호출**
    (hash-only) → v2 frozen reproduce 는 **schema dispatch 불필요**(content_hash 가
    이미 봉인됨, 재계산은 _jcs recipe 동일). schema 버전 dispatch 가 필요한 곳은
    **저장/생성 경로(#4 T-M6-04a)** 이지 reproduce 가 아니다.
  - T-M6-02b v2 reproduce 는 custom export-body 자기완결 경로 재사용(v2 도 export
    body 봉인 — ADR-0025 D5). hash 검증 로직 불변. **data_versions 에 신규 키 0**.
- **acceptance**: v1 frozen run reproduce 100% 동일(matches·result_hash·byte). v2
  frozen run 이 v2 schema 로 재로드. v1/v2 slug·tier 충돌 0(`@` disjoint).
- **가드레일**: §2.10 — v1 resolve·hash 경로 절대 불변. **data_versions 미변경**
  (result_hash 입력 불변 — C-1 회피의 핵심).

### #3 publisher 인증 발급 (§2.1) — publishers 테이블(H-1)
- **현황(실측)**: M2 NextAuth `users`(`id`·`google_sub` UNIQUE·`email`, `users.py`).
  publisher handle 컬럼 부재. `custom_packs.user_id` FK.
- **task**:
  - T-M6-03a **신규 `publishers` 테이블**(alembic migration): `id` PK·`user_id` FK→
    users.id **UNIQUE**(user 1:1)·`handle` String UNIQUE(**인스턴스 유일**)·`created_at`.
    append-only. 인증 user 가 handle 1회 claim.
  - T-M6-03b **사칭 차단 조회 경로**: v2 pack 생성/저장 route 가 `current_user.user_id
    → publishers.handle` 조회 → v2 slug 의 `@{publisher}` 와 **일치 강제**(불일치 →
    403). 미claim user → v2 발급 불가(v1 `user/` 경로는 그대로). 예약 handle(#1b) 거부.
  - T-M6-03c claim 시점: user 가 첫 v2 발급 전 publisher handle 등록(client UI).
- **acceptance**: 인증 user 만 자기 handle namespace 로 v2 발급. 타 handle/예약 handle
  사칭 → 403/422. 미claim → v2 불가. handle 인스턴스 유일(UNIQUE).
- **가드레일**: §2.1 — namespace = 인증 식별자(인스턴스 내 사칭 0). 외부 권위 미보증(#6).

### #4 v2 pack 생성·검증·import·저장 (§2.1 / §2.2)
- **현황(실측 — Momus R3 H-2)**: `factor_pack.py:51-153` 의 `_SCHEMA`/`_VALIDATOR` 는
  **factor-pack-v1.json 단일 하드코딩 싱글톤**. `validate_schema`(`:238`)·
  `validate_custom_pack`(`:663`)·`load_pack`(`:718`) 가 이 단일 validator 만 사용 →
  v2 slug `@{publisher}/{slug}` 는 **v1 pattern 불일치로 v1 validator 가 거부**.
  "v1 파이프라인 재사용=자동 통과" 는 코드 반증.
- **task**:
  - **T-M6-04a v1/v2 validator dispatch(H-2)**: `validate_schema`/`load_pack`/
    `validate_custom_pack` 를 **`$schema`(또는 slug `@` 형식) 기준 v1/v2 validator
    선택**으로 확장. v2 는 `factor-pack-v2.json` validator. **v1 경로 byte-불변**(기존
    pack 은 v1 validator 그대로 — #5 회귀로 강제). 단일 싱글톤 → 버전별 validator map.
  - T-M6-04b derive_tier(`@`→community)·forbidden-words(USER_SHARED, ADR-0032 D4)·
    content_hash 봉인·citation 게이트 v2 적용(검증 후 파이프라인 재사용). 저장
    `UniqueConstraint(user_id, pack_slug, version)` v2 slug 수용(≤128). community 공유
    (ADR-0028) v2. client Factor Lab v2 생성(publisher 는 #3 claim 에서 자동 — 폼 단순).
- **acceptance**: v2 pack 이 v2 validator 로 검증·생성·import·저장·공유 통과(v1
  validator 가 v2 를 거부하지 않게 dispatch). v1 검증 경로 byte-불변. forbidden-words/
  citation 게이트 v2 적용.
- **가드레일**: §2.2 — v2 외부 pack forbidden-words·citation 게이트.

### #5 v1 불변 보존 — 회귀 가드 (§2.10)
- **task**:
  - T-M6-05a v1 **content_hash** byte-동일(golden anchor `tests/test_jcs_conformance.py`)
    — M6 가 v1 pack body·schema 미변경 확인.
  - T-M6-05b v1 **result_hash** + 기존 frozen run reproduce matches 불변 — anchor:
    `tests/test_db/test_reproduce_pack_reload.py`·`test_api/test_screen_run_export_
    reproduce.py`·`test_api/test_backtest.py`. **data_versions 미변경**이므로 신규 v1
    run result_hash 도 불변(C-1 회피 검증). 적대적: v2 도입이 v1 깨면 fail.
  - **T-M6-05c cross-pack identity 회귀(Momus R3 M-a)**: v2=community tier 재사용이
    `detect_cross_pack_conflicts`/`build_existing_index`(`factor_pack_identity.py:148,
    199-232`)의 community-community 동률 first-wins(`_tier_rank` community=1) 동작을
    바꾸지 않음 anchor — v2 pack 과 기존 reference(community tier) 가 같은 rank 라도
    `by_cid` first-wins 가 v2 도입 전후 동일(기존 충돌 해소 동작 회귀 0).
- **acceptance**: v1 content_hash·result_hash·frozen run 재현 불변(§2.10 코드 증명).
- **가드레일**: M6 안전 핵심.

### #6 인증 identity 표시 (§2.1 / §2.7)
- **task**:
  - T-M6-06a v2 `@{publisher}/{slug}` use-시점 표시(M4 #6 PackAttribution 연장,
    grayscale 중립). **"인증된 publisher(이 인스턴스)"** 명시 + **disclosure(M-1)**:
    publisher 는 OAuth 계정 기반 handle 이며 외부 권위(실제 회사 등)를 보증하지 않음
    (`goldman-sachs` handle 임의 claim 가능 — 권위 오인 방지).
  - T-M6-06b i18n + 컴포넌트 테스트.
- **가드레일**: §2.7 — 사실 식별자. 큐레이션·순위·권위 신호 0.

## 4. 10기둥·자본시장법 가드레일

- **§2.10(최우선)**: v1 content_hash·result_hash·slug·frozen run 절대 불변. **M6 는
  data_versions·v1 schema·v1 body 미변경**(C-1 회피). v1/v2 판별 slug 형식만.
- **§2.8**: v2 schema SoT. identity = slug 단일 출처(publisher display-only).
- **§2.1 / §2.2**: v2 forbidden-words·citation 게이트. 인증 publisher(인스턴스 내 사칭
  0)지만 외부 권위 미보증 disclosure(#6).
- **자본시장법**: 제3자 콘텐츠 식별 강화 → ADR-0006 재자문 ADR-0034 판정(조건부 blocker).

## 5. Momus 검토 계획 (squash 직전)

전방위. 중점: §2.10 v1 content_hash/result_hash 불변(#5 — data_versions 미변경 확인)·
dual-load v1 byte-불변·slug `@` 판별이 derive_tier/reproduce/저장 전 경로 정합(C1/C2)·
publisher 인증 사칭 차단(#3 handle 일치 게이트·예약 handle)·표시 권위 오인 방지(M-1)·
자본시장법(M-2).

## 6. ADR

- **ADR-0034 (factor-pack-identity-v2)** — namespace 형식(`@{publisher}/{slug}` 확정)·
  v2=community tier·v1/v2 slug `@` 판별(data_versions 불변)·publisher 인증 모델
  (publishers 테이블·user 1:1·handle 유일·사칭 게이트)·외부 권위 disclosure·ADR-0006
  재자문 판정·v1 불변/자동마이그레이션 금지 근거·content_hash vs result_hash 구분.

## 리스크 등급

- **Critical**: v1 frozen run 재현 파손(#1~#4 가 v1 content_hash/result_hash/slug/
  resolve·**data_versions** 우발 변경 시 §2.10 붕괴). #5 회귀가 load-bearing —
  특히 **data_versions 에 어떤 키도 추가 금지**(result_hash drift) 가 코드 강제 대상.
- **High**: slug `@` 판별이 derive_tier/reproduce/registry/저장 전 경로 정합(C1/C2 —
  한 곳 누락 시 v2 오분류·resolve 실패). publisher 사칭 차단 누수(handle 일치 게이트
  우회·예약 handle 허용).
- **Medium**: 자본시장법 재자문 blocker(M-2). publisher 권위 오인(M-1 disclosure).
  publishers 테이블 migration 의 기존 user 무영향.
- **Low**: #6 톤, publisher claim UX.

## known limit (문서화)

- 중앙 registry 글로벌 유일성 미지원(인스턴스 내 handle 유일성만, cross-instance 인프라).
- v1→v2 자동 마이그레이션 영구 미지원(content_hash 파손). 기존 pack 소급 namespace 0.
- publisher 인증 = OAuth 계정 기반 handle — 외부 권위(실제 회사) 미검증(임의 claim,
  #6 disclosure).

## 참조 (Explore + Momus R1/R2 코드 근거)

- `factor_pack.py:161` content_hash 가 slug 포함(load-bearing).
- `screen_run.py:350-357` / `backtest_snapshot.py:158-174` — **result_hash 입력 =
  data_versions 포함**(C-1: data_versions 키 추가 금지 근거).
- `_jcs.py:84-99` exclude_key 는 top-level 단일 키만(data_versions 내부 제외 불가).
- `factor_pack_identity.py:41,50,148` TierName Literal·derive_tier·_tier_rank(M-1: community 재사용).
- `reproduce.py:197,210` is_builtin 이분법(C2 slug `@` 분기).
- `pack_registry.py:145` resolve(dual-load 확장).
- `db/orm/users.py` users(google_sub·email, publisher 부재 — #3 publishers 테이블).
- `custom_packs.py:65,96` pack_slug String(128)·UniqueConstraint(M3).
- `api/routes/custom_packs.py:96` canonical 사칭 게이트(C1 예약 handle).
- `shared/schemas/factor-pack-v1.json:26-41` v1 pack_slug pattern·publisher(불변).
- `tests/test_jcs_conformance.py`·`test_reproduce_pack_reload.py`(#5 회귀 anchor).
- M2 NextAuth·ADR-0002 D4·ADR-0025 D5·ADR-0032 D1·ADR-0006.
