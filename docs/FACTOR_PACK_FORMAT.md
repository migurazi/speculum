# Factor Pack Open Format — v1 사양

> Speculum Factor Lab pack 의 **open format 사양**. 외부 저자(블로거·연구자)가 자신의
> factor pack 을 작성·검증하고 git 에 올려 공유하면, 다른 사람이 import 해서 **재현**할
> 수 있다(ROADMAP §7 비전, ADR-0032).
>
> **정규(normative) 정의는 JSON Schema** [`shared/schemas/factor-pack-v1.json`](../shared/schemas/factor-pack-v1.json)
> 이다. 본 문서는 그 schema 를 참조하며, **JSON Schema 로 표현할 수 없는 것**(content_hash
> 계산 recipe·식별 규약·예제)만 추가한다. 충돌 시 schema 가 우선한다.

## 1. 한눈에 — 최소 예제

[`examples/factor-packs/example-debt-ratio-v1.0.0.json`](../examples/factor-packs/example-debt-ratio-v1.0.0.json)
가 작성 가능한 최소 pack 이다. `tools/validate_pack.py` 로 검증한다:

```sh
python tools/validate_pack.py examples/factor-packs/example-debt-ratio-v1.0.0.json
# → valid — 봉인 정상 (sha256:...).
```

## 2. 최상위 구조 (schema `properties` 참조)

| 필드 | 필수 | 의미 |
|------|------|------|
| `$schema` | ✅ | schema URI(`https://speculum.dev/schemas/factor-pack-v1.json`). |
| `type` | ✅ | 상수 `"factor-pack"`. |
| `pack_slug` | ✅ | 3-tier 식별자(§3). |
| `version` | ✅ | semver(예: `"1.0.0"`). |
| `publisher` | ✅ | 저자 식별 문자열(자유 텍스트 — 신뢰 식별 아님, §3 주의). |
| `license` | ✅ | 라이선스(예: `"CC-BY-4.0"`). import 시 표시됨. |
| `created_at` | ✅ | 작성일. |
| `citation` | ✅ | pack 출처(`title`/`version`/`publisher`/`url`). |
| `factors` | ✅ | factor 정의 배열(§4). |
| `content_hash` | ✅ | `"sha256:<64-hex>"` — §5 recipe 로 계산한 봉인. |
| `description` | — | 설명. |

정확한 타입·길이·pattern·금지 키(`__proto__` 등)는 schema 가 강제한다.

## 3. 식별 — 3-tier `pack_slug`

`pack_slug` pattern(schema enforced): `^(speculum-builtin|community/[a-z0-9][a-z0-9-]*|user/[a-z0-9-]+)$`

- `speculum-builtin` — Speculum 빌트인(canonical). 외부 저자 사용 금지.
- `community/<slug>` — 공유 목적의 community pack.
- `user/<slug>` — 개인 custom pack.

각 factor 의 `canonical_id`(이름 역할)와 `uuid`(개체 식별, 충돌 검사 기준)는 **분리된 역할**
이다. import 시 기존 pack 과 `canonical_id`/`uuid` 충돌은 import-conflict 흐름이 해소한다.

> **주의(글로벌 유일성 없음)**: `publisher` 는 자유 텍스트이며 slug 의 글로벌 유일성을 보증
> 하지 않는다(서로 다른 저자가 같은 `community/foo` slug 를 쓸 수 있음). 중앙 registry 기반
> fully-qualified 식별은 본 v1 범위 밖(ADR-0032 D6 유보). import 시 로컬 충돌만 해소된다.

## 4. Factor 정의 (schema `factor` / `formula` / `expression` 참조)

각 factor: `canonical_id`·`uuid`·`name`·`unit`·`formula{ast, inputs}` (+ 선택 `description`/
`tags`/`alt_names`/`citation`). `formula.ast` 는 재귀 expression(`exprConst`/`exprField`/
`exprOp`).

**허용 연산자만**(schema `exprOp.op` enum): `add`·`sub`·`mul`·`div`·`ratio_pct`·
`sum_last_n_quarters`·`avg`·`weighted_sum`·`zscore`·`percentile`·`min_max_scale`·`winsorize`.
**`rank`/`top_n`/`bottom_n`/`sign`/`step` 등 서수·임계 신호 연산자는 의도적으로 부재**(§2.2
No Advice — 시스템 큐레이션·매매 신호 차단, ADR-0022 D1). factor `name`/`description` 의
추천·매매 어휘는 import 시 게이트로 차단된다.

### 4.1 상수는 decimal **문자열** (ADR-0032 D1) — 중요

`exprConst.const`, `weighted_sum.weights[]`, `winsorize.lower`/`upper` 는 JSON number 가
아니라 **decimal 문자열**이다(schema `decimalString`):

- pattern: `^-?(0|[1-9][0-9]*)(\.[0-9]+)?$` — 부호 + 정수부(선행 0 금지, `0` 단독 허용) +
  선택 소수부. **지수표기(e/E)·선행 0·`+` 금지**.
- 예: `{"const": "0.001"}`, `"weights": ["0.6", "0.4"]`, `"lower": "0"`, `"upper": "1000"`.
- **이유**: content_hash(§5)의 cross-runtime 재현. JSON number(float)는 언어별 직렬화
  (지수표기·IEEE 754 shortest-repr)가 달라 hash 가 어긋난다. decimal 문자열은 어느 언어에서도
  동일 직렬화 → 동일 hash. (정수 인덱스 `n` 은 number 유지 — 정수는 직렬화 차이 없음.)

## 5. content_hash 계산 recipe (load-bearing — 외부 재현의 핵심)

`content_hash = "sha256:" + hex(SHA-256(JCS(body without content_hash)))`

1. **`content_hash` 필드를 제거**한 pack body 를 대상으로 한다(자기참조 회피).
2. **RFC 8785 JCS(JSON Canonicalization Scheme)** 로 정규화:
   - object 키는 **Unicode codepoint 오름차순 정렬**(server 구현 = Python
     `json.dumps(sort_keys=True)`). 본 포맷의 키는 모두 ASCII(§2 schema 의
     `propertyNames`/필드 pattern)로 제한되므로 codepoint 정렬은 RFC 8785 의
     UTF-16 code unit 정렬과 **동일**하다(둘은 비-BMP 키 U+10000+ 에서만 발산하며,
     그런 키는 schema 가 애초에 거부한다).
   - 모든 불필요 공백 제거(separator `,` 와 `:` 만).
   - 문자열은 JSON minimal escape. **non-ASCII(한글 등)는 escape 하지 않고 그대로**(UTF-8).
   - `NaN`/`Infinity` 금지.
3. UTF-8 로 encode → **SHA-256** → 소문자 hex → `"sha256:"` prefix.

§4.1 의 decimal-문자열 규약 덕분에 이 과정에 **runtime-의존 단계가 없다** — JCS + decimalString
만 따르면 어느 언어 구현이든 byte-for-byte 동일 canonical form → 동일 hash.

> **참고(구현 노트)**: 본 레퍼런스 구현에서 content_hash 는 **server(Python `_jcs.py`)
> 가 단독 계산**한다. 클라이언트(브라우저)는 hash 를 자체 계산하지 않고 server 의
> import-check 결과(봉인값·불일치 여부)를 사용한다. 따라서 cross-runtime 일치는
> **위 golden anchor 로 외부 구현(임의 언어)이 대조**하는 방식으로 보장된다(Python↔
> Node 동시 단위 테스트는 client 가 hash 비계산이라 현 아키텍처에서 불요 —
> known limit). client 가 향후 hash 를 자체 계산하게 되면 이 anchor 에 대한 대조
> 테스트를 양 런타임에 추가해야 한다.

### 5.1 conformance anchor (구현 대조용)

다음 fixture(decimal 문자열 4종 + 한글)의 canonical form 과 hash 는 고정이다. 외부 구현이
동일 값을 내면 recipe 를 올바로 구현한 것이다. (server 테스트 `tests/test_jcs_conformance.py`
의 `_GOLDEN_FIXTURE`/`_GOLDEN_HASH` 와 동일.)

```
content_hash(exclude content_hash) = sha256:39c17d9e834dfbac128ce3887dddc35451a709a8ea044ff7b59ed177eb668152
```

작은 검증 예(키 비정렬 입력 → canonical):

```
입력:  {"z":"1000000","b":"0.5","a":["-1.5","0.001"],"한":"값"}
canonical: {"a":["-1.5","0.001"],"b":"0.5","z":"1000000","한":"값"}
```

## 6. 권장 git 레이아웃

- pack 1개 = JSON 파일 1개(`<slug>-<version>.json`). 한 repo 에 여러 pack 가능.
- repo 에 `README` 로 pack 의 의도·출처·license 를 사람이 읽게 기술(citation.url 이 이 repo 를
  가리키게).
- 게시 전 `python tools/validate_pack.py <file>` 로 검증 + 봉인(미봉인이면 도구가 계산한
  content_hash 를 채운다).
- 다른 사람은 그 raw URL(예: GitHub raw) 또는 파일을 Speculum 의 import 로 가져와 재현한다.

## 7. 검증 도구

`tools/validate_pack.py` — server 의 `app.services.factor_pack` 검증을 그대로 위임(별도
재구현 없음 — 도구 판정 = Speculum 판정). schema·식별·순환·citation·금지어휘 검사 + content_hash
재계산. exit 0=valid, 1=이슈/봉인 파손, 2=입력 오류.

## 참조
- 정규: [`shared/schemas/factor-pack-v1.json`](../shared/schemas/factor-pack-v1.json)
- ADR-0032(open format)·ADR-0022(Factor Lab op)·ADR-0028(community)·ADR-0025(freeze/재현)
- RFC 8785 (JSON Canonicalization Scheme)
- 예제: [`examples/factor-packs/example-debt-ratio-v1.0.0.json`](../examples/factor-packs/example-debt-ratio-v1.0.0.json)
