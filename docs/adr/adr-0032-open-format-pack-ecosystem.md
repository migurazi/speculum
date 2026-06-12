# ADR-0032: Open Format / Community Pack 생태계 — portable factor pack

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-06-04 |
| **Deciders** | 사용자 |
| **Related** | CONCEPT §7(비전 — open format 표준)·§2.1(Fidelity)·§2.2(No Advice)·§2.7(Observation over Speculation)·§2.8(Conformance)·§2.10(Reproducibility), [[adr-0022-composite-factor-operators]](Factor Lab pack)·[[adr-0025-pack-registry]](freeze·재현)·[[adr-0028-factor-pack-community]](in-DB 공유)·[[adr-0020-append-only-invariant]](body immutable)·[[adr-0007-default-ui-rules]] D4(forbidden-words)·[[adr-0006-legal-review]] D2/D9(유사투자자문 경계·재검토 의무); Norma §2.14(self-identifying report); M4_PLAN, explore+Metis 분석(2026-06-04) |

## Context

ROADMAP §7 비전: "블로거·연구자가 Factor Lab pack 을 git 에 올리고 다른 사람이 import 해서
재현". M2(ADR-0022 Factor Lab·ADR-0025 PackRegistry)·M3(ADR-0028 community)가 pack export
(content_hash 봉인)·로컬 import·in-DB 공유·freeze 재현을 이미 구현했다. 그러나 pack 은 아직
**이 인스턴스 DB 안에서만** 공유된다 — git/외부로 portable 한 진짜 open format 이 아니다.

M4 는 pack 을 portable open-format 으로 만든다. pre-planning 적대적 분석(Metis)이 드러낸 제약:

- **JCS 재현이 load-bearing**: `_jcs.py` 가 Python↔Node 의 float·non-BMP 문자 직렬화 차이를
  이미 문서화. schema 가 임의 `number` 를 허용해 외부 저자가 content_hash 를 재현 못 할 수 있다
  → "재현" 약속(§2.10) 붕괴. open format 의 전제다.
- **content_hash 가 pack_slug·publisher 를 포함**: slug/identity 변경은 봉인을 깨고 frozen run
  재현(ADR-0025)을 파손 → namespace 변경은 v2 schema 마이그레이션(Critical 호환성).
- **외부 pack 은 신뢰 불가 입력**: 제3자 name/desc 가 추천 어휘(§2.2)를 담을 수 있고, 자본시장법
  경계(외부 투자 factor 콘텐츠 호스팅·표시)를 건드린다.
- **cross-instance 재현은 인프라 의존**: batch_id 가 타 인스턴스 DB 에 부재 → 데이터 layer 재현
  불가. 중앙 registry/discovery 도 호스팅 인프라 — 코드 범위 밖.

## Decision

### D1. JCS content_hash 재현 recipe lock — schema 레벨 결정성 강제
content_hash 를 외부 구현자(임의 언어)가 byte-for-byte 재현할 수 있도록 **비결정 입력을 schema
에서 봉쇄**한다(Metis 권장 a안 — RFC 8785 number 직렬화 규약 명시(b안)보다 단순·안전).
`const`/`weights`/`lower`/`upper` 등 numeric 필드는 **지수표기 금지 + bounded decimal**로 제약.
cross-runtime conformance fixture(float + 한글 name 포함)로 Python(`_jcs.py`) ↔ Node(client)
hash 일치를 강제 테스트. AST 재귀 depth cap 도 검증(adversarial nested AST stack 고갈 차단).

### D2. URL import = client-fetch (백엔드 SSRF surface 0)
"git 에 올린 pack 을 URL 로 import" 는 **브라우저가 fetch**(GitHub raw=CORS 허용)해 사용자
검토 후 **기존 검증된 import 파이프라인**(import-check→import, content_hash 검증·충돌 resolve)에
투입한다. **백엔드 신규 fetch 경로 0** — 서버 SSRF surface 미추가(`dart_adapter` httpx 재사용
금지). content_hash 불일치 = **fail-loud**(저자 봉인 파손 = 변조/손상 → silent reseal 아니라
명시 경고/차단). CORS 미허용 host 는 로컬 파일 다운로드 후 import(기존 경로) — known limit.

### D3. provenance = hash-제외 row metadata (body 불변)
import 출처(`source_url`/`imported_at`/`imported_from`)는 **pack body 에 넣지 않는다**
(content_hash 가 body 기준 → 봉인 파손 + ADR-0020 immutability 위반). `custom_packs` **row**
컬럼(visibility 옆)에 저장. `compute_pack_hash` 출력이 provenance 유무와 무관함을 테스트로 강제.

### D4. 외부 pack forbidden-words 게이트 — import 시점 USER_SHARED, 렌더 전 게이트
외부 import pack 의 `name`/`description` 은 import 시점에 **`assert_clean(USER_SHARED)`** 게이트
(공유 콘텐츠 취급). **게이트 통과 전 원본 name 을 사용자 화면에 렌더하지 않는다**(§2.2 hole 차단).
차단 시 중립 메시지(어휘 echo 0, forbidden_words B4). client+server 양쪽.

### D5. open-format spec 문서 + validator tool — SoT 단일 유지
`docs/FACTOR_PACK_FORMAT.md`(사람용 사양)는 `shared/schemas/factor-pack-v1.json` 을 normative 로
인용하고 **JSON Schema 로 표현 불가한 것만**(D1 JCS recipe + worked example + 권장 git 레이아웃)
추가. `tools/validate_pack.py` 는 server `app.services.factor_pack` 검증을 import 한 **thin CLI
wrapper**(4번째 SoT 금지). CI 가 validator==server verdict + doc 필드==schema 필드 parity 강제.

### D6. namespace/identity·cross-instance 는 M4 범위 외 (유보)
- **namespace/identity 변경 유보**: content_hash 가 pack_slug 포함 → 변경은 frozen run 재현
  파손(v2 schema 마이그레이션, Critical). 중앙 registry 없이 글로벌 유일성 불가. M4 는 `publisher`
  를 **advisory 표시**만(식별 강제 안 함). slug 패턴 불변.
- **cross-instance discovery index 유보**: 중앙 호스팅 인프라 — 코드 범위 밖.
- **cross-instance run 재현 유보**: batch_id 타 DB 부재 → `matches=False`. **known limit 문서화**
  (pack 정의 자체는 ADR-0025 D5 self-contained 재로드 가능, 데이터 layer 는 불가).
- **auto-sync/subscribe to URL 유보**: 배경 egress + curation 신호(§2.2 위험).

### D7. release blocker — ADR-0006 변호사 자문 재발행
**제3자(외부 저자) 투자 factor 콘텐츠를 fetch·표시·공유**하는 것은 material expansion
(ADR-0006 D2/D9 "M1+ 기능 확장 시 재검토 의무"). 유사투자자문업 경계(저자 pack 의 factor
name/desc 가 사실상 추천이 될 가능성·외부 콘텐츠 책임·license 표시 의무) 자문 후 운영 노출.
이전 전 마일스톤(M0 V6·M3 세무/변호사)과 동일 패턴.

## Rationale

- **§7 비전 실현 + §2.10 Reproducibility**: D1 JCS lock 이 "재현" 약속을 코드로 보증 — open
  format 의 핵심. Norma §2.14 self-identifying 패턴의 cross-instance 확장.
- **§2.1 Fidelity**: D3 provenance + license 표시 = "이 pack 이 어디서 왔는가" 정직.
- **§2.2 No Advice**: D4 가 외부 pack 의 추천 어휘를 렌더 전 차단 — 경계 보존.
- **§2.8 Conformance / SoT**: D5 가 spec/validator 를 단일 SoT 의 얇은 인용/wrapper 로 묶어 drift
  차단(forbidden-words 3-언어 SoT 패턴 일관).
- **호환성 보존**: D6 가 hash-slug 결합의 파손 반경을 인지해 namespace 변경을 유보 — frozen run
  재현(ADR-0025) 불변.
- **§2.7·자본시장법**: D7 이 외부 콘텐츠 호스팅의 법적 확장을 자문 blocker 로 게이팅.

## Consequences

### Positive
- pack 이 git portable open format 이 됨 — 비전 §7 의 커뮤니티 생태계 코드 기반.
- 외부 저자가 spec+validator 로 valid pack 을 독립 생성·재현 가능(D1/D5).
- 기존 export/import/freeze 인프라 재사용 — 신규 발명 최소.

### Negative
- D7 변호사 자문 release blocker(운영 노출 전). JCS 결정성 제약(D1)이 일부 numeric 표현 자유 축소.
- cross-instance 데이터 재현은 여전히 불가(D6 known limit).

### Neutral / Unknown
- 외부 pack 품질·정확성은 Speculum 이 보증 안 함(저자 책임·license 표시). discovery 가 in-DB
  한정이라 "다른 사람의 pack 발견"은 사용자가 URL 을 이미 아는 경우로 제한(중앙 index 유보).

## Alternatives Considered

- **A. server-fetch URL import** — 임의 host fetch 가능하나 SSRF 하드닝(사설IP/metadata/redirect/
  크기) 전면 필요 + 보안-critical. 기각 — client-fetch(D2)가 GitHub raw 핵심 케이스를 SSRF 0 으로
  cover(Metis "everything branches from it"). 사용자 결정 = client-fetch.
- **B. provenance 를 pack body 에 포함** — content_hash 봉인 파손 + ADR-0020 위반. 기각(D3 row 분리).
- **C. namespace/identity 즉시 강제(fully-qualified slug)** — hash-slug 결합으로 frozen run 재현
  파손(v2 마이그레이션). 기각·유보(D6).
- **D. RFC 8785 number 직렬화 규약 명시(b안)** — 외부 구현 부담 큼. 기각 — schema numerics 제약
  (D1 a안)이 단순·안전.
- **E. 백테스트 최적화/MyData(ROADMAP M4+ 후보)** — 최적화=§2.2 advice-인접, MyData=ADR-0029
  기각. 기각 — 비전 §7 의 open/community 가 8기둥에 자연 부합.

## References

- CONCEPT §7(비전)·§2.1/§2.2/§2.7/§2.8/§2.10, ADR-0022/0025/0028/0020/0007/0006
- Norma §2.14 self-identifying report
- RFC 8785 (JSON Canonicalization Scheme), `shared/schemas/factor-pack-v1.json`, `server/app/services/_jcs.py`
- `docs/M4_PLAN.md`
