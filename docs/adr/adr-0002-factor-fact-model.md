# ADR-0002: Factor / Fact 3-layer 데이터 모델

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-05-22 |
| **Deciders** | 사용자 |
| **Related** | `docs/CONCEPT.md §2.1 Fidelity`, `§2.8 Conformance`, `§2.10 Reproducibility`, `docs/M0_PLAN.md T2`, Norma `CONCEPT §2.1~§2.3 + §2.14`, Tessera ADR-0008 |

## Context

Speculum 의 모든 데이터·UI·API 가 의존하는 가장 기초적인 모델. 다음 요구를 동시에 만족해야 한다.

1. **Fidelity (§2.1)** — 모든 표시 값 옆에 식·출처·기준일 표기.
2. **Conformance (§2.8) — multi-id ambiguous indicators** — 같은 "PER" 이라도 산출 정의가 다르면 다른 ID. 자매 프로젝트 Norma 의 dual-definition (Gnathion 기하학 vs 해부학) 패턴 매핑.
3. **Reproducibility (§2.10)** — Screen Run snapshot 이 식·데이터·계산 시점을 freeze. 6 개월 후 같은 결과 재현.
4. **Factor Lab (M2)** — 사용자가 정의한 팩터를 JSON pack 으로 export, community 공유.

자매 프로젝트 Norma 가 동일한 종류의 문제 (분석법 정의 vs 환자 인스턴스 vs 측정 Run) 를 `Definition / Instance / Run` 3-layer 로 풀었다. Speculum 도 같은 결로 매핑.

핵심 결정 항목:
- Factor ID 네이밍 (canonical / community / custom 의 분리)
- Source Citation 의 필수 필드
- Pack 의 버저닝 + 무결성 메커니즘
- DB 와 JSON pack 의 분리

## Decision

### D1. 3-layer 모델

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1 — FACTOR DEFINITION (immutable, versioned)         │
│    Factor ID + 산출식 + 입력 필드 + 단위 + citation         │
│    예: per:trailing-12m-consolidated-ifrs v1.0.0            │
│        market_cap / sum(net_income_last_4q_cfs)             │
└────────────────────────┬────────────────────────────────────┘
                         │ references
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 2 — STOCK SNAPSHOT (instance, mutable per date)      │
│    (stock_code, as_of_date) 키로 Layer 1 의 입력 값들이      │
│    실제로 어떤 값이었는지를 저장. + 산출된 factor 결과       │
│    각 값에 Source Citation (Layer 3) 의무 참조               │
└────────────────────────┬────────────────────────────────────┘
                         │ provenance
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 3 — SOURCE CITATION (의무, append-only)              │
│    7-tuple: source / identifier / retrieved_at /            │
│              effective_date / adapter_version /             │
│              batch_id / url                                 │
└─────────────────────────────────────────────────────────────┘
```

별도 entity 로 Screen Run snapshot (§2.10) — 위 3-layer 의 read-only freeze.

### D2. Factor ID 네이밍 — Norma 패턴 동일

3-tier:

```
1. Canonical (빌트인, ~30~50개) — colon-separated slug
   per:krx-official
   per:trailing-12m-consolidated-ifrs
   per:trailing-12m-separate-ifrs
   pbr:krx-official
   roe:net-income-consolidated-ifrs-avg-equity
   ev-ebitda:trailing-12m-consolidated-ifrs

2. Community (선택, import 가능 확장 팩) — namespace 콜론 2단
   community/joel-greenblatt:magic-formula-roc
   community/piotroski:f-score-2000

3. User-defined (UUID, 로컬)
   uuid-018f9b...
```

각 Factor Definition = `factor_id` (있으면 canonical) + UUID (항상) + 정의 텍스트 + 산출식 + 대체 이름들. **식 안의 참조는 항상 UUID** (Norma §2.3 의 3 원칙 그대로 적용 — 자동 매칭은 canonical_id 일치 시에만).

**Multi-ID rationale** — 같은 "PER" 이라도 다음이 다 다른 정의이므로 다른 canonical_id:

| Canonical ID | 산출식 |
|---|---|
| `per:krx-official` | KRX 가 발표하는 공식 PER (시가총액 / 최근 연결 당기순이익) |
| `per:trailing-12m-consolidated-ifrs` | 우리 계산: 시가총액(자사주 제외) / Σ(최근 4 분기 연결 당기순이익) |
| `per:trailing-12m-separate-ifrs` | 위와 동일하나 별도재무제표 |
| `per:forward-consensus` (M3+) | 시가총액 / 컨센서스 예상 EPS |

UI 에 항상 어느 정의의 PER 인지 명시 (§2.1 Fidelity 의 implementation).

### D3. Source Citation — 7 필드 의무 세트

모든 fact 가 source_citation row 를 의무 참조. 누락 시 빌드 게이트 차단.

```python
class SourceCitation:
    id: UUID
    source: SourceKind            # DART / KRX / FDR / PYKRX / ECOS / KOSIS / USER_INPUT
    identifier: str               # DART = rcept_no, KRX = trd_dt+market, FDR = N/A
    retrieved_at: datetime        # adapter 가 데이터 fetch 한 시각 (UTC)
    effective_date: date          # 그 데이터의 발효일 (DART rcept_dt, KRX trd_dt)
    adapter_version: str          # adapter 코드 버전 (semver — 파싱 로직 변경 추적)
    batch_id: UUID                # 일배치 잡 식별자 (재현성)
    url: str | None               # 1차 자료 원문 링크 (DART 보고서 URL, KRX 페이지)
```

**왜 7 필드 모두인가:**

| 필드 | 역할 |
|---|---|
| source | 어느 출처 (멀티 어댑터 대응) |
| identifier | 그 출처 내 고유 ID (DART rcept_no 등) — 원문 재방문 가능 |
| retrieved_at | 우리가 받아온 시각 — DART 정정공시·재배포 추적 |
| effective_date | PIT 의 1차 키 (§2.4) — 그 데이터가 공시·발효된 시점 |
| adapter_version | 파싱 로직 변경이 결과 변경을 일으킬 수 있음 (Norma `adapter` 버전과 동형) |
| batch_id | 같은 batch 의 다른 데이터들과 함께 freeze 가능 (재현성) |
| url | 1 차 자료로 직접 이동. Fidelity 의 마지막 보루 |

#### Amendment (ⓓ stock_snapshots precompute) — `SourceKind.PRECOMPUTE`

`SourceKind` 에 `PRECOMPUTE` 추가. 기존 8 종(DART/KRX/FDR/PYKRX/ECOS/KOSIS/
USER_INPUT/FSC)은 모두 **1 차 raw 자료**의 출처지만, `stock_snapshots`(D5)는
factor 평가의 **derived(파생) 결과**라 외부 fetch 원문이 없다. 이를 1 차 출처로
위장(예: KRX citation 재사용)하면 Fidelity(§2.1)가 왜곡되므로 별도 kind 로 구분한다.

- precompute 일배치(`batch/snapshot_daily.py`)가 실행당 **단일 PRECOMPUTE
  citation** 1 개를 생성(`identifier = "SNAPSHOT_PRECOMPUTE|{as_of}|{batch_id}"`,
  url=None, batch_id = 그 배치 run). 모든 snapshot row 가 이를 공유.
- **per-input 1:1 citation 이 아니다** — 한 factor 가 price(KRX)+financial(DART)
  등 복수 입력을 쓸 수 있으나, snapshot 의 `citation_id` 는 "이 값이 어느 precompute
  run 의 산물인가"만 가리키는 대표 citation 이다. 전체 input provenance 는
  snapshot 의 `data_versions`(freeze fingerprint — batch_id·evaluator_version·
  policy hash, screen_runs 와 대칭) + `inputs`(산정 입력값)에 보존된다. per-input
  citation fan-out(join 테이블)은 M2+.
- DB `source_citations.source` 는 String(32)(enum/CHECK 없음)이라 value 추가가
  migration 을 요구하지 않으며 SourceCitation 의 identifier 검증도 source-무관
  (non-empty·printable·max-length)이라 PRECOMPUTE citation 이 그대로 통과한다.

### D4. Pack 버저닝 — Semver + immutable SHA-256 hash

Norma 패턴 동일.

```
factor-pack v1.0.0  sha256:a3f2b1...
factor-pack v1.0.1  sha256:c8d4e2...    ← 오타 보정도 patch
factor-pack v1.1.0  sha256:...           ← 새 factor 추가 (minor)
factor-pack v2.0.0  sha256:...           ← 기존 factor 식 변경 (major, breaking)
```

**원칙:**
- **모든 변경은 새 버전 발행** — 오타 보정도 patch. 기존 hash 영구 보존.
- **자동 마이그레이션 금지** — Screen Run snapshot 의 hash 검증이 깨지지 않도록.
- **Pack JSON 의 RFC 8785 JCS + SHA-256** — cross-runtime deterministic hash (Norma M0 패턴 차용).

### D5. DB 스키마 (M0 초안)

```sql
-- Layer 1
CREATE TABLE factor_definitions (
  id           UUID PRIMARY KEY,
  canonical_id TEXT,                 -- nullable (custom 은 NULL)
  uuid         UUID NOT NULL,        -- 항상 존재, 식 참조의 1차 키
  name         TEXT NOT NULL,
  description  TEXT NOT NULL,
  formula      JSONB NOT NULL,       -- AST 또는 텍스트 + 입력 필드 list
  unit         TEXT,                 -- "ratio", "krw", "percent", null
  pack_id      UUID NOT NULL REFERENCES factor_packs(id),
  citation     JSONB NOT NULL,       -- {author, year, doi, ...} — Pack 본문에 또 있지만 검색 용이성 위해
  created_at   TIMESTAMPTZ NOT NULL,
  UNIQUE (canonical_id, pack_id),    -- 같은 pack 내 canonical_id 중복 금지
  UNIQUE (uuid)                       -- 글로벌 unique
);

CREATE TABLE factor_packs (
  id           UUID PRIMARY KEY,
  pack_slug    TEXT NOT NULL,        -- "speculum-builtin", "joel-greenblatt-magic-formula"
  version      TEXT NOT NULL,        -- "1.0.0"
  hash         TEXT NOT NULL,        -- SHA-256 of canonical JSON (JCS)
  json_body    JSONB NOT NULL,
  imported_at  TIMESTAMPTZ NOT NULL,
  UNIQUE (pack_slug, version),
  UNIQUE (hash)
);

-- Layer 2
CREATE TABLE stock_snapshots (
  stock_code      TEXT NOT NULL,
  as_of_date      DATE NOT NULL,
  factor_uuid     UUID NOT NULL REFERENCES factor_definitions(uuid),
  value           NUMERIC,
  value_unit      TEXT,
  inputs          JSONB NOT NULL,     -- 그 시점에 어느 입력 값으로 계산했는지
  citation_id     UUID NOT NULL REFERENCES source_citations(id),
  computed_at     TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (stock_code, as_of_date, factor_uuid)
);

-- Layer 3
CREATE TABLE source_citations (
  id              UUID PRIMARY KEY,
  source          TEXT NOT NULL,       -- "DART" / "KRX" / "FDR" / "PYKRX" / "ECOS"
  identifier      TEXT NOT NULL,
  retrieved_at    TIMESTAMPTZ NOT NULL,
  effective_date  DATE NOT NULL,
  adapter_version TEXT NOT NULL,
  batch_id        UUID NOT NULL,
  url             TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  -- append-only — UPDATE 금지 (트리거로 강제)
);

CREATE INDEX idx_citations_batch ON source_citations(batch_id);
CREATE INDEX idx_citations_effective ON source_citations(effective_date);
```

`screen_runs` 테이블은 별도 ADR (ADR-0014 — M0~M1 경계) 에서 결정.

### D6. Pack JSON schema (M0 초안)

`server/builtin-packs/factors/speculum-builtin-v1.0.0.json`:

```json
{
  "$schema": "https://speculum.dev/schemas/factor-pack-v1.json",
  "pack_slug": "speculum-builtin",
  "version": "1.0.0",
  "publisher": "speculum",
  "license": "MIT",
  "created_at": "2026-08-01",
  "citation": {
    "title": "Speculum Built-in Factor Pack",
    "version": "1.0.0"
  },
  "factors": [
    {
      "canonical_id": "per:trailing-12m-consolidated-ifrs",
      "uuid": "018f9b00-1234-...",
      "name": "PER (TTM, 연결, K-IFRS)",
      "description": "주가수익비율. 시가총액 / 최근 4분기 연결 당기순이익 합. 자사주 차감.",
      "formula": {
        "ast": {
          "op": "div",
          "left":  { "field": "market_cap_ex_treasury" },
          "right": { "op": "sum_last_n_quarters",
                     "n": 4,
                     "field": "net_income_consolidated_ifrs" }
        },
        "inputs": ["market_cap_ex_treasury", "net_income_consolidated_ifrs"]
      },
      "unit": "ratio",
      "alt_names": ["PER", "P/E (TTM, 연결)"],
      "tags": ["valuation", "earnings"]
    },
    ...
  ]
}
```

## Rationale

1. **자매 프로젝트 결 일관** — Norma `Definition / Instance / Run` 3-layer 가 dual-definition / per-population norm / Run snapshot / pack export 모두를 깨끗이 푼 검증된 패턴. Speculum 의 PER/PBR multi-definition / 사용자 팩터 공유 / 재현성 요구가 같은 종류의 문제.
2. **§2.1 Fidelity 의 implementation** — Source Citation 7 필드가 "출처 항상 표시" 의 진짜 구현. 단순 텍스트 "DART" 만으로는 불충분 (어느 보고서? 언제 받았나? 어느 adapter 로?).
3. **§2.8 Conformance 의 multi-id** — Factor ID 자체를 산출 정의의 tuple 로 다룸. "PER 이 KRX 와 우리 값이 다른 이유" 가 시스템에서 명시.
4. **§2.10 Reproducibility** — Pack hash + Source Citation batch_id + Factor uuid 의 조합이 freeze 의 단위.
5. **M2 Factor Lab 의 prerequisite** — 사용자 팩터 정의·공유는 본 3-layer 위에서만 깨끗. M0 부터 박지 않으면 후일 깨짐.

## Consequences

### Positive
- **모든 값에 출처 7 필드 자동 동반** — Fidelity 의 형식적 보장.
- **Multi-id 로 같은 이름 다른 정의 충돌 방지** — KRX 공식 PER 과 우리 PER 이 다르면 사용자가 즉시 알 수 있음.
- **Pack hash freeze** — Reproducibility 의 기초. Norma 의 self-identifying report 패턴 차용.
- **Adapter 변경 추적** — `adapter_version` 으로 파싱 로직 변경이 결과에 미친 영향 추적.
- **자매 프로젝트와 결 일관** — 향후 ADR/검토 작업의 인지 부담 감소.

### Negative
- **DB 부담** — Source Citation 이 fact 마다 row → 수백만 row. 인덱스·파티셔닝 필요 (M1+).
- **adapter 코드 부담** — adapter 가 매 fetch 마다 citation 채워야 함. 누락 방지 게이트 (T23) 필요.
- **사용자 인지 부담** — "왜 같은 PER 이 두 개?" 질문 → 도움말·tooltip 필요.
- **Pack JSON 의 RFC 8785 JCS 구현** — 결정적 직렬화 라이브러리 (Python `jcs`, Node `canonicalize`) 도입.

### Neutral / Unknown
- **Formula AST vs 단순 텍스트** — M0 는 ~30 빌트인 factor 하드코딩 + JSON AST 자리만. 사용자 정의 식 파서는 M2 Factor Lab 진입 시 결정.
- **`stock_snapshots` 의 폭증** — 종목 ~2500 × factor ~30 × 일자 1300 (5 년) ≈ 1억 row. PG partition + 압축 필요. M1 ADR.
- **JSONB vs 정규화 컬럼** — `formula` 와 `inputs` 를 JSONB 로 두는 trade-off. 검색 빈도 낮으면 OK.

## Alternatives Considered

- **A. 모두 UUID, namespace 없음** — 검색·관리 단순하지만 multi-id 의도 표현 불가. CONCEPT §2.8 가 풍자됨. **거부**.
- **B. Hash only (semver 없음)** — Git 스타일. 사람 가독성 떨어지고 release notes 작성 어려움. **거부**.
- **C. Source Citation 3 필드 minimum** — source + identifier + retrieved_at. 가볍지만 adapter_version / batch_id 가 없어 재현성 위반. PIT 정정공시 추적 어려움. **거부**.
- **D. Polymorphic citation (DART/KRX 별 다른 필드)** — 출처별로 정확하지만 query·schema 복잡. 7-tuple flat 으로 모두 표현 가능. **거부**.
- **E. Definition / Instance 만 (Run 별도)** — Run 을 별도 entity 로 미루면 Reproducibility (§2.10) 가 M2+ 로 밀림. M0 에 schema 자리만 비우는 게 비용 거의 0. **거부**.

## References

- Norma `docs/CONCEPT.md §2.1` (Primary/Derived/Measurement DAG) — Speculum 의 Factor 식 평가에 매핑
- Norma `docs/CONCEPT.md §2.2` (Definition / Instance) — Speculum 의 Layer 1 / Layer 2 와 동형
- Norma `docs/CONCEPT.md §2.3` (Identity 3-tier) — Factor ID 네이밍에 그대로 차용
- Norma `docs/CONCEPT.md §2.14` (Run + self-identifying report) — Speculum 의 ADR-0014 (Screen Run snapshot) 에서 본격 매핑
- Tessera `docs/adr/0008-dicom-storage-policy.md` — immutable + 버전 관리 패턴 참조
- [RFC 8785 — JSON Canonicalization Scheme](https://datatracker.ietf.org/doc/html/rfc8785)
- [Open DART API 가이드](https://opendart.fss.or.kr/guide/main.do) — `rcept_no` / `rcept_dt` 의 의미
