# ADR-0034: Factor pack identity v2 — 인증된 publisher namespace

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-06-05 |
| **Deciders** | 사용자 |
| **Related** | CONCEPT §7(open format 비전)·§2.1(Fidelity)·§2.2(No Advice)·§2.7(Observation)·§2.8(Conformance)·§2.10(Reproducibility), [[adr-0002-factor-fact-model]] D4(versioning·hash)·[[adr-0022-composite-factor-operators]] D8(3-tier identity)·[[adr-0025-pack-registry]] D5(frozen 재현)·[[adr-0028-factor-pack-community]](공유)·[[adr-0032-open-format-pack-ecosystem]](open format)·[[adr-0006-legal-review]](자본시장법); M2 NextAuth(인증); `docs/M6_PLAN.md`(rev3, Momus R1→R2→R3 OKAY) |

## Context

M4(ADR-0032)가 pack 을 portable open-format 으로, M5(ADR-0033)가 그 재현을 검증
가능하게 만들었다. 그러나 pack identity 는 아직 `speculum-builtin`·`community/{...}`·
`user/{...}` prefix 로만 격리될 뿐, **서로 다른 저자의 같은 이름 pack 을 글로벌하게
구별·인증**하지 못한다. M4/M5 는 namespace/identity v2 를 "(M5+)" 로 명시 유보했다.

**유보의 load-bearing 근거(코드 확정)**: `compute_pack_hash`(`factor_pack.py:161`)는
`content_hash` 만 제외하고 **pack_slug·publisher 포함 body 전체**를 JCS+SHA-256 →
**slug 변경 = content_hash 변경**. `reproduce._resolve_frozen_pack` 이 frozen slug/
hash 로 pack 재로드+검증 → **기존 slug 를 바꾸면 frozen run 전부 "pack 변조" 오진·
재현 파손**(ADR-0025 D5 / §2.10 붕괴). 또한 `result_hash`(screen/backtest run snapshot,
`screen_run.py:350-357`·`backtest_snapshot.py:158-174`)는 `data_versions` 를 JCS 입력
으로 포함하므로, **data_versions 에 키를 더하면 신규 run 의 result_hash 가 drift**.

Momus 3라운드 적대 검토(R1·R2 REVISE → R3 OKAY)가 드러낸 추가 제약:
- v2 slug 가 `derive_tier`(prefix 판정)·reproduce(빌트인/custom 이분법)·`_VALIDATOR`
  (v1 단일 싱글톤)와 정합해야 한다.
- "글로벌 유일성" 은 인증 또는 중앙 registry 없이는 표시 라벨 수준에 머문다(M4 가
  이미 user_id 격리·UniqueConstraint 보유) → **publisher 인증**이 실효의 핵심.

## Decision

### D1. namespace 형식 = slug-embedded `@{publisher}/{slug}`
v2 pack 의 `pack_slug` 는 **`@{publisher}/{slug}`**(예 `@joel-greenblatt/magic-formula`).
`@` prefix 로 v1 pattern(`speculum-builtin|community/...|user/...`)과 **형식 disjoint**.
기존 `publisher` 필드는 **display-only**(identity 의 단일 출처는 slug — §2.8 SoT,
publisher 가 hash 에 중복 의미로 들어가는 충돌 회피). publisher·slug = `[a-z0-9]
[a-z0-9-]*`, 총 pack_slug 길이 ≤128(ORM `String(128)` 정합).

### D2. v1/v2 판별 = slug `@` 형식 (data_versions 절대 불변)
frozen run 의 v1/v2 구별은 **frozen slug 의 `@` 유무**로 한다. `data_versions` 에
신규 키를 **추가하지 않는다**(result_hash 입력 불변 — 기존 frozen run·신규 v1 run
모두 hash drift 0). 기존 frozen run 은 `@` 없는 v1 slug 라 자동 v1 경로. registry/
`_resolve_frozen_pack` 이 `@` → v2 분기, 그 외 → v1 경로 byte-불변.

### D3. v2 derive_tier = community 재사용
v2 pack(`@` prefix)은 `derive_tier` 에서 **community tier 로 매핑**(신 tier 신설 안
함). `TierName` Literal(canonical/community/custom)·`_tier_rank`(community=1) 변경 0.
v1 prefix 판정 코드 byte-불변. cross-pack 충돌 해소(`detect_cross_pack_conflicts`)는
community-community 동률 first-wins 동작 불변(회귀 T-M6-05c).

### D4. publisher 인증 = 신규 `publishers` 테이블 + 사칭 게이트
인증 user ↔ publisher handle **1:1**. 신규 `publishers`(`user_id` FK→users.id UNIQUE·
`handle` UNIQUE[인스턴스 유일]·`created_at`, append-only). v2 pack 생성/저장 route 가
`current_user.user_id → publishers.handle → v2 slug 의 @{publisher} 일치 강제`(불일치
403). 예약 handle(`community`/`user`/`speculum-builtin`/`speculum`) route 거부
(canonical 사칭 게이트 패턴 재사용). 미claim user → v2 발급 불가(v1 `user/` 경로 그대로).

### D5. v1 절대 불변 + 자동 마이그레이션 영구 금지
M6 는 v1 **content_hash·result_hash·slug·data_versions·schema·body 를 절대 변경하지
않는다**. v1↔v2 자동 마이그레이션은 content_hash 파손이라 **영구 미지원** — v2 는
opt-in 신규, 기존 pack 에 namespace 를 소급 부여하지 않는다. v1 frozen run 은 영구 v1
재현. #5 회귀(golden hash anchor·기존 reproduce matches)가 이를 코드로 강제.

### D6. v1/v2 validator dispatch (저장/생성 경로)
`factor_pack.py` 의 `_VALIDATOR` 단일 싱글톤을 **`$schema`(또는 slug `@` 형식) 기준
v1/v2 validator dispatch** 로 확장. v2 는 `factor-pack-v2.json` validator. **v1 검증
경로 byte-불변**. dispatch 가 필요한 곳은 **저장/생성 경로**(`validate_schema`/
`load_pack`/`validate_custom_pack`)뿐 — reproduce(`load_pack_from_body`)는 hash-only
(`validate_schema` 미호출)라 schema dispatch 불필요.

### D7. 외부 권위 미보증 disclosure
publisher 인증은 **본 인스턴스의 OAuth 계정 기반 handle 까지**. handle 은 임의 claim
가능(예 `goldman-sachs`)하므로 외부 권위(실제 회사 등)를 보증하지 않는다. v2 identity
표시(#6)에 "인증된 publisher(이 인스턴스)" + 미보증 disclosure 명시(권위 오인 방지).

### D8. ADR-0006 재자문 판정 (조건부 release blocker)
namespace 가 제3자 publisher 콘텐츠를 이름으로 식별·호스팅하는 것을 강화 →
ADR-0006(유사투자자문업/제3자 투자 콘텐츠 유통 경계) **재자문 필요 여부를 본 ADR 이
운영 노출 전 판정**. 코드 작업은 진행 가능, 운영 노출만 조건부 게이트(M4 동일 패턴).

## Rationale

- **§2.10 Reproducibility(최우선)**: D2/D5 가 v1 content_hash·result_hash·data_versions
  를 불변으로 고정 → frozen run 파손 0. D1 의 `@` 형식 disjoint 가 v1/v2 를 hash 변경
  없이 구별. content_hash recipe(JCS·decimalString, ADR-0032 D1)는 v2 동일(cross-runtime).
- **§2.8 Conformance**: D1 identity = slug 단일 출처(publisher display-only). D6 validator
  dispatch 가 v1 경로 byte-불변(SoT).
- **§2.1 No impersonation**: D4 인증 publisher 가 인스턴스 내 사칭을 막는다(M4 대비
  실효). D7 disclosure 가 외부 권위 오인 방지.
- **§2.2 No Advice**: v2 외부 pack 도 forbidden-words·citation 게이트(ADR-0032 D4).

## Consequences

### Positive
- 인증 저자가 자기 이름으로 pack 발급, 사용자가 인스턴스 내 사칭 없이 식별.
- v1 frozen run·content_hash 완전 보존(§2.10) — 파손 0.
- v2 도입이 기존 인프라(derive_tier·reproduce·저장) byte-불변(점진적).

### Negative
- v2 pack 만 namespace(기존 v1 은 영구 namespace 없음 — 소급 0).
- 글로벌 유일성은 인스턴스 내까지(cross-instance 충돌은 인프라 — 유보).
- validator 단일 싱글톤 → 다중 dispatch 로 복잡도 증가(D6).

### Neutral / Unknown
- ADR-0006 재자문 결과가 운영 노출 blocker 가 될 수 있음(D8).
- publishers 테이블 migration 의 기존 user 무영향(append-only, FK only).

## Alternatives Considered

- **A. content_hash 입력에서 slug 제외** — 거부. 기존 v1 pack hash 전부 변경 →
  frozen run 파손(§2.10 붕괴).
- **B. data_versions 에 schema 버전 키 추가로 v1/v2 판별**(rev1) — 거부(Momus R2 C-1).
  data_versions 는 result_hash JCS 입력이라 신규 run hash drift.
- **C. 기존 slug 를 fully-qualified 로 일괄 변경 + 마이그레이션** — 거부. content_hash
  파손 + 중앙 registry 의존(글로벌 유일성).
- **D. namespace 만 도입(인증 없이)** — 거부(Momus R1 H2). 표시 라벨 수준 공허(M4 가
  이미 격리). 인증이 실효의 핵심.

## References
- `docs/M6_PLAN.md`(rev3) — deliverable #1~#6 + Momus 3라운드.
- `factor_pack.py:161`(content_hash)·`screen_run.py:350-357`·`backtest_snapshot.py:158-174`
  (result_hash 입력)·`reproduce.py:155-259`·`pack_registry.py:59-89,145-162`·
  `factor_pack_identity.py:50,148`(derive_tier·_tier_rank)·`db/orm/users.py`·
  `custom_packs.py:65,96`·`api/routes/custom_packs.py:96`(사칭 게이트)·
  `shared/schemas/factor-pack-v1.json:26-41`(v1 불변).
