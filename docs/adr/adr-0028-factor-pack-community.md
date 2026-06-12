# ADR-0028: Factor pack community — 공유 visibility + USER_SHARED scope + import 명시 매핑

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-06-02 |
| **Deciders** | 사용자 |
| **Related** | [[adr-0025-pack-registry]](custom pack resolve/hash 검증), [[adr-0021-multiuser-transition]](user 격리), [[adr-0022-composite-factor-operators]] D9(custom pack 영속화), [[adr-0007-default-ui-rules]] D4.5(USER_SHARED scope 예약·dead-code), [[adr-0020-append-only-invariant]](body immutable), Norma §2.3(identity 3-tier); oracle 분석(2026-06-02) M3_PLAN #3 |

## Context

custom pack 영속화(ADR-0022 D9)·PackRegistry(ADR-0025)로 저장/resolve/hash 검증/screen/백테스트까지
완성됐으나 **사용자 간 공유는 부재**. ADR-0007 D4.5 가 `USER_SHARED` scope 를 enum 으로 예약하고
dead-code 로 봉쇄("공유 서버는 M3+")한 상태다. #1 백테스트(ADR-0027)로 pack 의 "성과 차원"이
생겼으므로, 공유 surface 가 처음 열린다.

위험: (A) 공유 pack 이 "X 님의 고수익 pack"으로 유통 → 추천 효과(§2.2), (B) import 시 factor
정의 1:1 매핑 미명시 → "남의 PER 정의"가 silent 하게 섞임(§2.8 Multi-ID dual-definition 위반),
(C) "인기 pack"/다운로드 순위 = 시스템 큐레이션(§2.3 위반), (D) 공유 pack content_hash 변조.

## Decision

### D1. visibility 메타 컬럼 — body immutable 보존
`custom_packs` 에 `visibility` 컬럼 추가(`'private'`(default) | `'public'`). **body·content_hash 는
ADR-0020 append-only 불변 유지** — visibility 만 사용자 의도 메타로 mutable UPDATE(공유 토글).
content_hash 가 불변이라 재현성(reproduce/freeze)에 영향 0. alembic migration 으로 기존 row =
`'private'` backfill. repository 에 `list_public()`(community 목록) + `set_visibility(pack_id, user_id, visibility)` 추가.

### D2. USER_SHARED scope 활성화 — private→public 게이트
`'public'` 전환 시점에 pack 의 **name + description 을 `forbidden_words.assert_clean(scope=USER_SHARED)`**
검사(ADR-0007 D4.5 dead-code 해제). 금지어휘 포함 시 공유 거부(422). private 저장 시점은
USER_PRIVATE(검사 skip, 본인만) 유지 — 공유 surface 진입 시에만 게이트. **공유가 추천 변질
통로가 되는 지점을 정확히 차단**.

### D3. community 목록 route — 중립 정렬, 큐레이션 0
`GET /api/factor-packs/community` — `list_public()` 반환. 정렬은 **created_at 역순 또는 slug
사전순(사실)만**. 금지: "인기"/다운로드 수/별점/순위/Top-N/추천 배지(§2.3, ADR-0027 D4-c 동형).
다운로드 카운트 자체를 집계·노출하지 않는다(큐레이션 신호 0). 인증 불요(공개 목록).

### D4. import 명시 매핑 — silent merge 금지 + content_hash fail-loud
community pack import 는 **기존 `factor_packs` import-check/import 흐름 재사용**(Norma §2.3 identity).
import 시 factor canonical_id 충돌을 사용자에게 명시 resolve 요구(silent merge 금지 — 기존
import-conflict 흐름). content_hash 재검증 실패 시 fail-loud(ADR-0025 D1 `validate_hash`). import 된
pack 은 importer 의 **새 custom pack(private)으로 복제** — 원본과 독립(user 격리).

### D5. composite 출력 게이트 상속
공유 pack 의 composite 값도 ADR-0025 D6(자동 강조/순위 금지)·ADR-0024(소표본 디스클로저)·
ADR-0027 D4(백테스트 출력 게이트) 자동 적용. community 라고 게이트 우회 없음.

## Rationale
- 기존 인프라 재사용: PackRegistry resolve·validate_hash·import-check·USER_SHARED scope enum 이
  이미 존재 — 신규는 visibility 컬럼 + community 목록 + 공유 게이트뿐.
- body immutable + visibility mutable 분리로 append-only(ADR-0020)와 공유 토글 양립.
- §2.2/§2.3: Speculum 은 format 호스팅이지 큐레이터가 아니다 — 시스템이 pack 을 추천/순위화 0.

## Consequences
- **Positive**: 사용자 간 pack 공유(비전 §7 "open format"). USER_SHARED dead-code 해제.
- **Negative**: 공유 = user 격리 해제 surface — visibility 게이트·권한 회귀 테스트 필수. migration.
- **Neutral/Unknown**: 공유 pack 이 "투자 전략 배포"로 보이면 §2.2 경계 — ADR-0006(불특정 다수
  조언) 재검토 트리거. 큐레이션 0 + 시스템 추천 0 으로 "호스팅" 위치 유지.

## Alternatives Considered
- **A. 공유 = community/ slug 로 새 pack 복제(visibility 컬럼 없이)** — body 중복·hash 분기·원본
  추적 불가. 기각 — visibility 메타가 단순(body 1개, 메타 토글).
- **B. 다운로드 순위/인기 정렬** — §2.3 큐레이션·의제 설정. 기각(D3 중립 정렬).
- **C. import 시 silent factor merge** — §2.8 dual-definition 위반. 기각(D4 명시 매핑).

## References
- ADR-0025 D1, ADR-0021, ADR-0007 D4.5, ADR-0020, ADR-0024, ADR-0027 D4, Norma §2.3
- 단계: migration(visibility) → repository(list_public/set_visibility) → 공유 게이트 route(USER_SHARED) → community 목록 route → client(CommunityPackBrowser)
