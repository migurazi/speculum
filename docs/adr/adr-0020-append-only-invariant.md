# ADR-0020: financials / corporate_actions append-only 불변식 — `superseded_by` NULL→non-NULL 단일 전이만 허용

| | |
|---|---|
| **Status** | ACCEPTED (M1) |
| **Date** | 2026-05-29 |
| **Deciders** | 사용자 |
| **Related** | [[adr-0002-factor-fact-model]] D3/D5 (append-only, Source Citation), [[adr-0009-corporate-action]] D5 (정정공시 supersede chain); `docs/work-orders/m1-milestone.md` Phase M1-0 T49 |

## Context

M1 재현성(§2.10) 기반은 **입력 테이블의 append-only 불변식**에 의존한다. 저장된 Screen Run 을 frozen batch_id 로 재쿼리할 때 fact row 가 in-place 로 변경되면 byte-동일 재현이 조용히 깨진다.

기존 상태:
- `source_citations` 는 **무조건 차단** BEFORE UPDATE 트리거 보유 (Alembic `20260527_0001:76-95`, `RAISE EXCEPTION`, 컬럼 분기 없음). citation 은 7-tuple 이 불변이고 `superseded_by` 컬럼 자체가 없어 무조건 차단이 옳다 ([[adr-0002-factor-fact-model]] D3 Layer 3).
- `financials` / `corporate_actions` 는 **트리거 부재**. ORM 의 `superseded_by` 컬럼만 존재.

**자기모순 위험 (Momus C1):** [[adr-0009-corporate-action]] D5 의 정정 프로토콜은 "새 row INSERT → **옛 row 의 `superseded_by` UPDATE**" 를 명령한다. 정정 row 의 id 는 원 row INSERT 시점에 알 수 없으므로(정정공시는 수개월 후 임의 도착) INSERT-time 설정이 불가능하다 → 반드시 사후 UPDATE.

따라서 `source_citations` 의 무조건 차단 트리거를 financials / corporate_actions 에 **그대로 복사하면 정정 chain 이 DB 레벨에서 막혀** PIT / Temporal Continuity 가 깨진다. 두 테이블은 성격이 다르므로 별도의 **조건부** 불변식이 필요하다.

## Decision

### D1. financials / corporate_actions 의 허용 UPDATE = `superseded_by` 의 NULL→non-NULL 단일 전이뿐

두 테이블에 대해 다음만 허용한다:

> `superseded_by` 가 `OLD = NULL AND NEW != NULL` 이고, **그 외 모든 컬럼이 OLD 와 동일**한 UPDATE.

그 외 모든 경우(다른 컬럼 변경, `superseded_by` 의 non-NULL→다른 값 재변경, non-NULL→NULL 되돌리기, DELETE)는 **차단**한다.

- 이미 non-NULL 인 `superseded_by` 의 재변경 금지 → supersede chain 은 **1 회성**(한 row 는 한 번만 superseded).
- DELETE 무조건 차단 → historical 영구 보존 ([[adr-0009-corporate-action]] D5 line 3).

### D2. `source_citations` 는 무조건 차단 유지 (복사 금지)

`source_citations` 는 `superseded_by` 컬럼이 없고 7-tuple 이 완전 불변이므로 기존 무조건 차단 트리거([[adr-0002-factor-fact-model]] D3 Layer 3)를 그대로 둔다. **본 ADR 의 조건부 트리거를 citation 에 적용하지 않는다** — 성격이 다르다.

### D3. PostgreSQL 조건부 트리거 (Alembic migration 0007)

`financials` / `corporate_actions` 에 PG `BEFORE UPDATE` 트리거를 추가한다. plpgsql 함수가 OLD/NEW 의 **모든 컬럼을 명시 비교**하여 D1 의 단일 전이 외 변경을 `RAISE EXCEPTION` 으로 차단한다(테이블별로 컬럼 set 이 다르므로 별도 함수). `BEFORE DELETE` 트리거도 무조건 차단으로 추가한다.

운영 export 주의는 `20260527_0001` 와 동일 — PG dialect 로 SQL export 해야 트리거가 포함된다.

### D4. 유일 허용 UPDATE 경로 = `update_superseded_by(record_id, successor_id)`

`app/repositories/sql_repositories.py` 에 `SqlFinancialRepository.update_superseded_by` / `SqlCorporateActionRepository.update_superseded_by` 메서드를 신설한다. 정정공시 처리(별도 cycle)의 유일한 진입점이며, 다음을 강제한다:

- 대상 row 의 현재 `superseded_by` 가 NULL 이어야 함(이미 superseded 면 거부 — chain 1 회성).
- `successor_id` 의 row 가 같은 테이블에 존재해야 함(self-FK 무결성).
- `superseded_by` **외 컬럼 미변경**.

`dart_daily.py` 의 `superseded_by=None` 하드코딩은 본 cycle 에서 변경하지 않는다(신규 INSERT 는 항상 active head 로 시작). 정정공시 처리(정정 row INSERT + 원 row `update_superseded_by`)는 별도 cycle 이며 본 메서드를 그 진입점으로 사용한다.

### D5. SQLite(테스트) = repository layer 불변식 검증

SQLite 는 plpgsql 트리거를 지원하지 않는다. 따라서 테스트는 **repository layer** 에서 동일 불변식을 검증한다:

- `update_superseded_by` 가 비-NULL→재변경 / successor 부재 / NULL 되돌리기를 거부.
- non-`superseded_by` UPDATE 와 DELETE 는 repository 에 해당 경로 자체가 없음(method 부재) — append-only 의도가 type-level 로 표현됨.

이 한계는 트리거 migration 과 repository 코드 주석에 명시한다(운영 PG 가 최종 방어선, SQLite repository 검증이 개발 시 방어).

## Rationale

1. **§2.10 Reproducibility** — fact row in-place 변경 0 → frozen batch_id 재쿼리의 byte-동일 보장.
2. **§2.4 PIT / Temporal Continuity** — 정정은 새 row + supersede chain 으로만 표현, 원 row 영구 보존.
3. **[[adr-0009-corporate-action]] D5 와 정합** — D5 의 "옛 row `superseded_by` UPDATE" 를 DB 레벨에서 정확히 그 한 가지 전이만 허용하여 enforcement.
4. **load-bearing 의존 (T48c 재현 정확성)** — 본 불변식(① 정정 시 원 row `superseded_by` 를 NULL→non-NULL 로 set)은 T48c 의 frozen batch_id 필터(② successor row 제외) + `pit_enforcer.py` 의 보수적 분기(③ `successor is None → active=True`)와 함께 byte-동일 재현을 보장한다. 이 chain 을 깨면 재현이 조용히 깨진다.

## Consequences

### Positive

- 정정 chain(D5)을 허용하면서 임의 in-place 변경을 DB 레벨에서 차단.
- `update_superseded_by` 단일 경로로 정정 로직의 audit / 검증 집중.
- 미완료 / 잘못된 UPDATE 가 운영 PG 에서 즉시 `RAISE EXCEPTION`.

### Negative

- 테이블별 컬럼 명시 비교 plpgsql 함수 유지 부담 — 컬럼 추가 시(예: T54 `effective_date_precise`) 트리거 함수도 갱신 필요.
- SQLite 테스트와 PG 운영의 enforcement 위치 차이(repository vs 트리거) — 이중 검증 필요.

### Neutral / Unknown

- T54 의 `effective_date_precise BOOLEAN` 컬럼 추가 시 본 트리거의 "그 외 컬럼 불변" 비교에 신규 컬럼이 자동 포함되어야 함(별도 migration 에서 트리거 함수 재정의).

## Alternatives Considered

- **A. source_citations 무조건 차단 트리거 복사** — 정정 chain 의 사후 `superseded_by` UPDATE 가 막혀 [[adr-0009-corporate-action]] D5 위반. **거부**.
- **B. 트리거 없이 repository 검증만** — 운영 PG 에서 raw SQL / 다른 코드 경로의 in-place UPDATE 를 막지 못함. 재현성의 최종 방어선 부재. **거부**.
- **C. 조건부 BEFORE UPDATE + BEFORE DELETE 트리거 + repository `update_superseded_by` 단일 경로 (본 ADR)** — DB 최종 방어 + 코드 단일 진입점 + SQLite repository 검증. **채택**.

## References

- [[adr-0002-factor-fact-model]] D3/D5 — append-only, Source Citation Layer 3
- [[adr-0009-corporate-action]] D5 — 정정공시 supersede chain
- `docs/work-orders/m1-milestone.md` Phase M1-0 T49 (Momus C1 해소)
- Alembic `20260527_0001` — source_citations 무조건 차단 트리거 (성격 비교)
- 8+2 기둥 §2.10 Reproducibility, §2.4 PIT
