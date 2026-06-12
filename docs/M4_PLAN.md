# M4_PLAN — Open Format / Community Pack 생태계 (v2.x)

> ROADMAP §7 비전("블로거·연구자가 Factor Lab pack 을 git 에 올리고 다른 사람이 import 해서
> 재현")의 코드 실현. M2(ADR-0022 Factor Lab·ADR-0025 PackRegistry)·M3(ADR-0028 pack
> community) 위에 **pack 을 진짜 portable open-format** 으로 만든다.
>
> 기획 결정(2026-06-04): 사용자 테마 선택 = "Open Format / Community pack 생태계".
> URL import 방식 = **client-fetch**(브라우저가 fetch, 백엔드 SSRF surface 0). pre-planning
> 분석 = explore(현황 매핑) + Metis(적대적 리스크). 위험한 ROADMAP M4+ 후보(백테스트
> 최적화=§2.2 advice-인접, MyData=ADR-0029 기각)는 채택 안 함 — 비전 §7 의 open/community/
> reproducibility 가 8기둥에 자연스럽게 부합.

## 0. 현황 — M2+M3 가 이미 만든 것 (재구현 금지)

| 기능 | 구현 위치 | 상태 |
|------|----------|------|
| Pack JSON export (content_hash 봉인, self-identifying `speculum-factor-pack-export-v1`) | `routes/factor_packs.py:522` `/api/factor-packs/export` | 완료 |
| Import (로컬 파일/paste, 2-pass import-check→import, content_hash 검증, 충돌 resolve) | `routes/factor_packs.py:389,437` | 완료 |
| In-DB community 공유 (visibility public/private, 중립 목록 큐레이션0) | `routes/custom_packs.py:182` + `factor_packs.py:598` `list_public()` | 완료 |
| PackRegistry freeze + reproduce (pack 은 export body 로 self-contained 재로드) | `services/pack_registry.py`, `services/reproduce.py:239` | 완료 |
| factor-pack-v1 JSON schema (기계용) | `shared/schemas/factor-pack-v1.json` | 완료 |
| 3-tier identity(canonical/community/user) + JCS+SHA256 hash | `services/factor_pack_identity.py`, `services/_jcs.py`, `services/factor_pack.py:161` | 완료 |
| `publisher`·`license` 필드 | schema:18,37 (required) | **존재하나 미사용**(display/식별 미연결) |

## 1. 범위 확정

| # | 항목 | M4 포함 | 충돌 무게중심 | ADR |
|---|------|---------|--------------|-----|
| 1 | **JCS 재현 recipe lock** | ✅ | §2.10 Reproducibility (load-bearing) | **Critical** ADR-0032 |
| 2 | **open-format spec 문서 + validator tool** | ✅ | §2.8 Conformance / SoT 일관 | ADR-0032 |
| 3 | **client-fetch URL import** | ✅ | §2.1 Fidelity / 보안(client-fetch라 SSRF 0) | ADR-0032 |
| 4 | **provenance (hash-제외 metadata)** | ✅ | §2.1 Fidelity / ADR-0020 immutability | ADR-0032 |
| 5 | **외부 pack forbidden-words 게이트 시점** | ✅ | §2.2 No Advice (노출점) | ADR-0032 |
| 6 | **license/citation 표시** | ✅ | §2.1 Fidelity / ADR-0006 D5 | ADR-0032 |
| — | namespace/identity 변경(fully-qualified) | ❌ **유보** | hash 가 slug 포함 → frozen run 재현 파손(v2 schema 마이그레이션, Critical 호환성). 중앙 registry 없이 글로벌 유일성 불가 | (M5+) |
| — | cross-instance discovery index | ❌ **유보** | 중앙 호스팅 인프라 의존 — 코드 범위 밖 | (인프라) |
| — | cross-instance run 재현 | ❌ **유보** | batch_id 가 타 인스턴스 DB 부재 → `matches=False`. ADR-0025 D5 self-contained 은 pack 한정. **known limit 으로 문서화** | (인프라) |
| — | auto-sync/subscribe to URL | ❌ **유보** | 배경 egress + change-detection + curation 신호(§2.2 위험) | (영구 검토) |

## 2. 구현 순서 (의존성 위상정렬 — Metis dependency topology)

```
① JCS recipe lock (#1) ──→ ② spec 문서+validator (#2)   [recipe 가 spec 의 전제]
③ client-fetch URL import (#3)   [독립 — #1/#2 무관, 기존 import 파이프라인 재사용]
⑤ forbidden-words 게이트 시점 (#5) ──→ ③ (import UI 노출 전 게이트 선행)
④ provenance metadata (#4) ──→ ⑥ license 표시 (#6)   [import→save 레코드 확장, body 무관]
```
- **#1 이 최우선**: "재현" 약속이 깨지면 open-format 전제가 무너짐(Metis Critical). #2 spec 은 #1 recipe 를 normative 로 인용.
- **#3·#5 직렬**: 외부 pack name 을 게이트 전에 렌더하면 §2.2 hole — #5(게이트 시점) 선행.
- **#4·#6**: `custom_packs` row 확장(body 무관, hash 불변식 보존) → license 표시와 함께.

## 3. 각 항목 핵심 가드레일 + task

### #1 JCS 재현 recipe lock (Critical — 첫 착수)
- **문제(Metis)**: `_jcs.py:16-18` 가 Python↔Node 의 **non-BMP 문자·float(지수표기/큰수) 직렬화 차이**를 이미 문서화. schema 가 `const`/`weights`/`lower`/`upper` 를 임의 `number` 로 허용(`1e-7`·`0.1` 가 런타임별 canonicalize 상이 가능) → 외부 저자가 content_hash 재현 불가 → "재현" 전제 붕괴.
- **가드레일**: 둘 중 하나로 결정성 강제 —
  (a) **schema numerics 제약**: 지수표기 금지 + bounded decimal(예: 최대 소수 N자리, `pattern`/`multipleOf`) — 가장 안전, 외부 구현 단순.
  (b) **RFC 8785 §3.2.2.3 ECMAScript Number 직렬화 정확 명시** + 문자열 정규화(NFC? UTF-8 byte order) 규약.
  → **(a) 권장**(Metis): schema 레벨에서 비결정 입력을 봉쇄.
- **task**: ADR-0032(D1 recipe) → schema numerics 제약(`factor-pack-v1.json`) → `_jcs.py` 결정성 보강 → **cross-runtime conformance fixture**: float + 한글 `name` 포함 pack 의 content_hash 가 Python(`_jcs.py`) ↔ Node(`client/lib/factor/*`) **byte-for-byte 일치** 테스트(server pytest + client vitest 양쪽). + AST 재귀 depth cap 검증(adversarial nested AST 가 `validate_acyclic`/evaluator stack 고갈 불가).

### #2 open-format spec 문서 + validator tool
- **가드레일(Metis SoT 일관)**: spec 문서·validator 가 **4번째 SoT 가 되면 안 됨**. validator 는 server `validate_custom_pack`/`validate_schema`(`factor_pack.py`)의 **thin CLI wrapper**. spec 문서는 `factor-pack-v1.json` 을 normative 로 인용하고 **JSON Schema 로 표현 불가한 것만**(JCS recipe #1 + worked example) 추가.
- **task**:
  - T-M4-02a `docs/FACTOR_PACK_FORMAT.md` — 사람용 사양: 필드 정의(schema 참조)·**JCS content_hash 계산 recipe(#1 결정성 포함)**·3-tier identity·예제 pack·권장 git repo 레이아웃(pack 1개 = 1 파일, `README` 권장).
  - T-M4-02b `tools/validate_pack.py` — `app.services.factor_pack` import 한 standalone CLI(server deps 최소, forbidden_words CLI 패턴). 입력 pack JSON → schema+pack 검증 + hash 재계산 표시. exit 0/1.
  - T-M4-02c 예제 pack `examples/factor-packs/*.json`(또는 `shared/`) + **CI parity 테스트**: `validate_pack.py` 출력 == server 검증 verdict, 그리고 doc 에 등장한 모든 필드명이 schema 에 존재.

### #3 client-fetch URL import
- **가드레일(Metis: client-fetch 라 백엔드 SSRF 0)**: 브라우저가 URL fetch(GitHub raw=CORS 허용) → 기존 import-check/import 파이프라인(검증·충돌 resolve)에 투입. **백엔드 신규 fetch 경로 0**(서버 SSRF surface 미추가). `dart_adapter` httpx client 절대 재사용 안 함(미하드닝).
  - **content_hash 불일치 = fail-loud**: 외부 pack 이 declared hash X, 재계산 Y 면 **저자 봉인 파손(변조/손상)** → silent reseal 아니라 명시 경고/차단(Metis edge 1 — paste 경로와 다른 의도). UI 가 사용자에게 "출처 hash 불일치" 표시.
  - **응답 가드(client)**: max bytes(예: 1 MiB, `_MAX_FACTORS=256` 기반) + JSON parse 전 크기 체크 + content-type 보조 확인. CORS 미허용 host 는 브라우저가 자연 차단(기능 한계로 문서화).
- **task**:
  - T-M4-03a client `PackIO.tsx`(또는 신규) URL input + fetch + 크기/JSON 가드 → 기존 import-check API 흐름 연결.
  - T-M4-03b content_hash 불일치 fail-loud UI(경고 + import 차단/명시 동의).
  - T-M4-03c **#5 게이트 선행** — 외부 pack name/desc 를 화면 렌더 전 검사(아래 #5).
  - T-M4-03d known limit 문서화: CORS 미허용 host 는 로컬 파일 다운로드 후 import(기존 경로) 안내.

### #5 외부 pack forbidden-words 게이트 시점 (#3 선행)
- **문제(Metis §2.2 hole)**: 현재 `USER_SHARED` 게이트는 **public 토글 시점만**(ADR-0028 D2). `EXTERNAL_QUOTE` 는 검사 skip(`forbidden_words.py:99-106`). 외부 import pack 의 `name`/`description` 이 어느 쪽도 아니어서, "추천주 Buy" 류 외부 pack 이름이 **게이트 전에 editor 에 렌더**되면 §2.2 위반.
- **가드레일**: import 시점에 외부 pack name/desc 를 **USER_SHARED scope 게이트**(공유 콘텐츠로 취급). 게이트 통과 전 **원본 name 을 사용자 화면에 렌더하지 않음**(차단 시 중립 메시지, 어휘 echo 금지 — forbidden_words B4). client(eslint·런타임) + server(import 응답 검사) 양쪽.
- **task**: T-M4-05a import-check/import route 가 외부 pack name/desc 를 `assert_clean(USER_SHARED)` → 위반 시 명시 거부(어휘 echo 0). T-M4-05b client 가 게이트 통과분만 렌더(미통과 시 중립 차단 UI).

### #4 provenance (hash-제외 metadata) + #6 license 표시
- **가드레일(Metis)**: provenance 를 **pack body 에 넣지 않는다**(`content_hash` 가 body 기준이라 봉인 파손 + ADR-0020 immutability 위반). `custom_packs` **row** 에 `source_url`/`imported_at`/`imported_from` 컬럼 추가(visibility 옆, body 무관). hash 불변식 보존: `compute_pack_hash` 출력이 provenance 유무와 무관함을 테스트로 강제.
  - **license/citation 표시**(ADR-0006 D5·§2.1): import·display 시점에 pack `license` + `citation`(외부 저자 기입) 노출 — "이 pack 의 출처·라이선스" 사용자 가시.
- **task**:
  - T-M4-04a migration: `custom_packs` 에 `source_url TEXT NULL` 등 provenance 컬럼(append-only 무관 — run 메타).
  - T-M4-04b import→save 흐름이 provenance 기록(client-fetch URL 을 save 시 전달).
  - T-M4-04c hash 불변식 회귀 테스트(provenance 유무 동일 hash).
  - T-M4-06a client pack 상세/라이브러리에 license+citation+source_url 표시(중립 — 큐레이션 신호 0 유지).

## 4. 마일스톤 종료 의무 (ROADMAP §6)
squash 직전 Momus 전방위 검토: 8기둥(특히 **§2.10 Reproducibility = JCS recipe cross-runtime 일치**, §2.2 No Advice = 외부 pack 게이트, §2.1 Fidelity = provenance/license, §2.8 Conformance = SoT 일관) + 자본시장법 경계(외부 factor 콘텐츠 호스팅·표시) + 자매 프로젝트(Norma §2.14 self-identifying / Tessera) 패턴 일관.

## 5. release blocker (운영 노출 전 필수)
1. **ADR-0006 변호사 자문 재발행** (Metis B / ADR-0006 D2·D9 "M1+ 기능 확장 시 재검토 의무") — **제3자(외부 저자) 투자 factor 콘텐츠를 fetch·표시·공유**하는 것은 material expansion. 유사투자자문업 경계(저자 pack 의 factor name/desc 가 사실상 추천이 될 가능성)·외부 콘텐츠 책임·license 표시 의무. 이전 전 마일스톤(M0 V6·M3 세무/변호사)과 동일 패턴의 자문 blocker.
2. (해당 시) 외부 host CORS 정책 변동 — client-fetch 한계이며 코드 blocker 아님(로컬 파일 fallback 존재).

## 6. 핵심 리스크 요약 (Metis) — plan 실행 시 상수
- **Critical**: JCS cross-runtime 재현(#1) — 미해결 시 open-format 전제 붕괴. namespace 변경(유보) — hash 가 slug 포함이라 frozen run 파손.
- **Medium**: 외부 pack §2.2 게이트 시점(#5), provenance hash 불변식(#4), validator SoT drift(#2), URL import content_hash fail-loud(#3).
- **known limit(문서화)**: cross-instance run 재현 불가(batch_id 타 DB 부재), CORS 미허용 host.

## 7. 착수 가능 순서 요약
1. ADR-0032 작성(D1 JCS recipe ~ Dn) — 본 plan 의 결정 명문화.
2. #1 JCS recipe lock + cross-runtime fixture (Critical 선행).
3. #2 spec 문서 + validator tool(#1 인용).
4. #5 게이트 시점 → #3 client-fetch URL import.
5. #4 provenance metadata → #6 license 표시.
6. Momus §6 종료 검토 → squash. (운영 노출은 release blocker 1 자문 후.)
