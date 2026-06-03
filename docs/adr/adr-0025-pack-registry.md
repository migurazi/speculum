# ADR-0025: PackRegistry — custom/reference pack screen 실행 + 재현 freeze

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-06-02 |
| **Deciders** | 사용자 |
| **Related** | [[adr-0022-composite-factor-operators]] D9.4(PackRegistry 별도 ADR 예약), [[adr-0021-multiuser-transition]] D4(익명 screen 함정)·D5(재현 무해), [[adr-0023-universe-expansion]] D6(pack version freeze), `docs/CONCEPT.md §2.10`(Reproducibility); oracle 분석(2026-06-02) |

## Context

custom pack 영속화(ADR-0022 D9)는 Phase 1(저장/목록/불러오기/evaluate)까지만 했고, **저장 custom pack 으로 screen 실행 + 재현 freeze 는 deferred**(D9.4). 이유: `snapshot_versions.collect_active_policy_versions` 가 `DEFAULT_PACK`(빌트인 1.0.0) 하드코딩 무인자 순수함수라, custom pack 으로 screen 을 실행하면 그 pack 의 (slug, version, content_hash)를 freeze 해야 하고 `reproduce` 가 그 version 을 재로드해야 한다 — snapshot 재설계 + reproduce 재로드라는 재현성(§2.10) 위험.

**현재 reproduce 의 잠재 결함(oracle)**: `reproduce_run_endpoint`(`runs.py:229`)가 frozen `factor_pack_content_hash` 를 무시하고 무조건 `ActivePackDep`(=DEFAULT_PACK)으로 재실행한다(`reproduce.py:140-142` docstring "호출자 책임" 자인). **빌트인 pack 이 1.0.0 하나뿐이라 우연히 정합**할 뿐 — 빌트인 1.0.1 또는 custom screen 도입 시 frozen hash 를 무시하고 현재 pack 으로 silently 잘못 재실행한다. PackRegistry 는 이 결함을 닫는 작업이기도 하다.

## Decision

### D1. PackRegistry — (slug, version) → LoadedPack resolve
신규 `server/app/services/pack_registry.py`. 3 소스 통합 + content_hash 검증:
- **빌트인**: `load_builtin_pack(version)`, slug=`speculum-builtin`. 프로세스 수명 캐시(immutable).
- **reference**: `load_reference_packs()`, slug=reference pack slug. 프로세스 수명 캐시.
- **저장 custom**: `custom_pack_repository.get_by_slug_version(slug, version, *, user_id)` → body → `validate_hash` 재검증 → LoadedPack. **request-scoped**(user_id 필요, 전역 캐시 금지 — user 격리·메모리 누수 방지).
- **content_hash 검증 진입점**: custom pack 을 DB body 로 로드할 때 `validate_hash` 재검증(`HashMismatch` 면 변조 탐지, ADR-0022 D9.2 실현).
- **단방향 의존 보존**: `snapshot_versions` 는 PackRegistry 를 import 하지 않는다(registry 가 custom_pack_repository=DB 를 import). pack 은 파라미터로만 전달, resolve 는 route 가 수행.

### D2. snapshot_versions pack-aware 확장 — default 인자 byte 불변
`collect_active_policy_versions(pack: LoadedPack = DEFAULT_PACK)` + `collect_run_data_versions(as_of, session, pack=DEFAULT_PACK)`. line 118-130 의 `DEFAULT_PACK.computed_hash/pack_slug/version` 을 `pack.computed_hash/...` 로 치환하되 **default 가 DEFAULT_PACK 이므로 무인자 호출 결과 byte 동일**(기존 호출처·기존 신규-빌트인-run hash 불변). custom 경로만 `pack=<선택>` 명시. **`SNAPSHOT_SCHEMA_VERSION` bump 불요**(키 set 불변, factor_pack_* 값만 pack 에서 옴). "시그니처 변경 금지" 주석은 호출처 무영향(default 인자)이므로 위반 아님 — 주석 갱신.

### D3. reproduce frozen 재로드 — active pack 사용 금지
`reproduce_run` 이 frozen `data_versions` 에서 pack 재로드: `slug/version/content_hash = data_versions["factor_pack_slug/version/content_hash"]` → `registry.resolve(slug, version, ...)` → `assert pack.computed_hash == frozen_hash`(변조 탐지). `runs.py:229` 의 `pack: ActivePackDep` 주입 **제거**. 
- **하위호환**: 기존 빌트인 run frozen slug=`speculum-builtin`/version=`1.0.0` → registry 가 빌트인 1.0.0 반환 → result_codes byte 동일(AC-M2-C-05·ADR-0023 D6 성립).
- **삭제된 custom pack**(D9 delete): registry None → **matches=False + note**("frozen pack 삭제로 재현 불가"). batch_id 삭제의 `EXCLUDE_ALL_CUTOFF` 패턴 동형. **silently 다른 pack 재실행 금지** — 재현 실패가 정직.

### D4. custom screen 인증 — D4 함정 회피
ADR-0021 D4 "익명 screen, CurrentUserDep 추가 금지"와 custom pack(user 격리)은 양립 불가. **별도 endpoint `POST /api/screen/custom`**(`CurrentUserDep` + pack 식별자) 신설 — 기존 익명 screen 라우트 무변경(회귀 0). custom pack 조회 `registry.resolve(slug, version, user_id=current_user)` → 타 user 404. (Phase 2c)

### D5. 재현성 불변식 (명문화)
> **저장 run 은 frozen `factor_pack_*` 로만 pack 을 재로드한다. "현재 active/custom pack" 을 재현에 사용하는 것은 절대 금지.** result_hash 입력(data_versions)은 재계산하지 않고 저장값을 재주입. frozen pack 의 content_hash 가 재로드 pack 과 불일치하면 fail-loud(변조/삭제). 기존 빌트인 run 의 byte 재현 보존이 어떤 신기능보다 우선.

### D6. custom composite 출력 게이트
custom pack 은 composite op 허용(D9). screen conditions 는 boolean 필터(통과/탈락)라 composite "점수" 가 추천 순위로 노출되지 않음(빌트인 0 게이트 우회 아님). 단 custom composite factor **값**이 결과 표시되면 합산점수 노출 위험 → 사용자 명시 정의라 표시 허용하되 **자동 강조/순위 금지**(ADR-0007 D5.1 정신). universe-relative composite 는 ADR-0024 소표본 디스클로저 자동 적용. (Phase 2c/2d)

## Rationale

- **§2.10 Reproducibility 강화**: (A) result_hash 재계산 안 함 + (B) frozen pack 으로만 재로드 — 두 불변식이 기존 run byte 재현을 보존하고, 현 reproduce 의 "DEFAULT_PACK 무조건" 잠재 결함까지 닫는다.
- **default 인자 최소 침습**: snapshot 무인자 호출이 byte 불변이라 M1/M2 의 bump-하위호환 패턴과 동형.
- **§2.2 No Advice**: custom screen 은 boolean 필터라 추천 변질 통로 아님. composite 값 표시는 사용자 정의라 Fidelity 일관(자동 강조만 금지).

## Consequences

### Positive
- custom pack screen 실행 + 재현(AC-M2-F-02 완전 활용). 현 reproduce 잠재 결함 해소(빌트인 multi-version 안전).
- frozen pack 변조/삭제 탐지(content_hash 검증).

### Negative
- snapshot_versions/reproduce/runs 재현성 경로 수정(회귀 테스트 필수). custom screen 별도 endpoint. PackRegistry 신규.

### Neutral / Unknown
- custom composite 출력 게이트(D6)의 frontend 표시 정책은 Phase 2d 에서 구체화.

## Alternatives Considered
- **A. reference pack 만 evaluate, screen 미지원** — 안전하나 AC-F-02 미완. (A)/(B) 불변식으로 안전 보존 가능하므로 불필요(oracle).
- **B. reproduce 가 현재 active pack 사용** — frozen run 을 현재 pack 으로 재실행 → result_codes 붕괴. 재현성 폭탄. 기각.
- **C. 기존 screen 에 custom pack 옵션 + optional auth** — ADR-0021 D4 함정(익명 screen 에 auth 혼입) 위험. 별도 endpoint(D4) 권고.

## References
- ADR-0022 D9.4(PackRegistry 예약), ADR-0021 D4/D5, ADR-0023 D6, CONCEPT §2.10
- 단계: 2a(registry+reproduce 재로드, 회귀 그린 먼저) → 2b(repo get_by_slug_version) → 2c(custom screen) → 2d(frontend)
