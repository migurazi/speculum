# Speculum — Data Model (M0 v0.1.0)

> **Status**: M0 T13 산출물. ADR-0002 D5 + ADR-0009 D6 의 5 core 테이블 구현.
> **Migration**: `server/alembic/versions/20260527_0001_initial_schema.py`
> **선행 ADR**: [ADR-0002 Factor/Fact](adr/adr-0002-factor-fact-model.md), [ADR-0009 Corporate Action](adr/adr-0009-corporate-action.md), [ADR-0001 가격 보정](adr/adr-0001-price-adjustment.md), [ADR-0005 K-IFRS 연결/별도](adr/adr-0005-ifrs-consolidated.md)

---

## 1. 5 Core 테이블 — M0 scope

| 테이블 | 역할 | ADR | T |
|---|---|---|---|
| `source_citations` | Layer 3 — 모든 fact 의 7-tuple 출처 (append-only) | ADR-0002 D3 | T13 |
| `stocks_master` | 종목 lineage entity — code 변경/재상장 history 보존 | ADR-0009 D6 | T13 |
| `prices_daily` | KRX 일별 OHLC + adjusted close + 거래량 | ADR-0001 | T13 |
| `financials` | 재무제표 단일 account row + 정정공시 chain | ADR-0002 D5 / ADR-0009 D5 | T13 |
| `corporate_actions` | 액면분할·무상증자 등 이중 PIT (announced/effective) | ADR-0009 D1 | T13 |

**Out-of-scope (T13 Phase B 또는 별도 사이클)**:
- `users` / `watchlists` / `watchlist_items` / `screener_sets` — ADR-0011
- `screen_runs` — ADR-0014 (별도)
- `factor_definitions` / `factor_packs` — 현재 in-memory pack loader 충분
- `stock_snapshots` — factor 결과 precomputed (일배치 합류 후)

---

## 2. ER 다이어그램 (Mermaid)

```mermaid
erDiagram
    source_citations ||--o{ prices_daily       : "citation_id"
    source_citations ||--o{ financials         : "citation_id"
    source_citations ||--o{ corporate_actions  : "citation_id"

    financials       ||--o{ financials         : "superseded_by"
    corporate_actions||--o{ corporate_actions  : "superseded_by"

    stocks_master {
        UUID    id PK
        STRING  current_code   "nullable - NULL = delisted"
        STRING  current_name
        STRING  market         "KOSPI | KOSDAQ | KONEX"
        DATE    listing_date
        DATE    delisting_date "nullable"
        INT     fiscal_month
        JSON    code_history   "list of (code, valid_from, valid_to, reason)"
        STRING  ifrs_preference_default "AUTO | CONSOLIDATED | SEPARATE"
    }

    source_citations {
        UUID     id PK
        STRING   source         "DART | KRX | FDR | PYKRX | ECOS | KOSIS | USER_INPUT"
        STRING   identifier
        DATETIME retrieved_at   "UTC tz-aware"
        DATE     effective_date "KST date"
        STRING   adapter_version "semver"
        UUID     batch_id       "indexed"
        STRING   url            "nullable, http(s):// only"
        DATETIME created_at     "UTC, append-only"
    }

    prices_daily {
        UUID     id
        STRING   code              PK
        UUID     code_lineage_id
        DATE     effective_date    PK "= trade_date"
        NUMERIC  open_raw
        NUMERIC  high_raw
        NUMERIC  low_raw
        NUMERIC  close_raw
        BIGINT   volume
        NUMERIC  close_adjusted    "ADR-0001"
        UUID     citation_id       FK
        DATETIME created_at
    }

    financials {
        UUID     id PK
        STRING   code
        UUID     code_lineage_id
        DATE     effective_date    "공시 효력일"
        STRING   fiscal_period     "2024Q1 | 2024-FY"
        STRING   account           "net_income_consolidated_ifrs ..."
        NUMERIC  value
        STRING   unit              "krw | ratio | percent"
        STRING   ifrs_type         "consolidated | separate"
        UUID     citation_id       FK
        UUID     superseded_by     FK "self, nullable"
        DATETIME created_at
    }

    corporate_actions {
        UUID     id PK
        STRING   code
        UUID     code_lineage_id
        STRING   action_type       "split | rights_offering | merger ..."
        DATE     announced_date    "정보 가용성 시점"
        DATE     effective_date    "효력 발생일 (권리락일 등)"
        DATE     payment_date      "nullable"
        NUMERIC  ratio             "nullable"
        NUMERIC  cash_amount       "nullable"
        JSON     details           "action_type 별 가변 필드"
        UUID     citation_id       FK
        UUID     superseded_by     FK "self, nullable"
        DATETIME created_at
    }
```

> `stocks_master` 와 fact 테이블 (prices_daily/financials/corporate_actions) 의
> 연결은 **soft reference** (`code_lineage_id` UUID). FK 강제 안 함 — lineage 보다
> daily-batch 생성 순서 자유도가 우선 (T18/T19 일배치가 atomic 보장).

---

## 3. 인덱스 전략 (PostgreSQL 16 기준)

| 테이블 | 인덱스 | 목적 |
|---|---|---|
| `source_citations` | `ix_source_citations_batch_id` | 재현성·rollback (batch 단위) |
| `source_citations` | `ix_source_citations_effective_date` | PIT query 의 effective_date scan |
| `stocks_master` | `ix_stocks_master_current_code` | 종목 검색·조회 hot path |
| `stocks_master` | `ix_stocks_master_market` | Screener universe filter (KOSPI/KOSDAQ) |
| `prices_daily` | `pk_prices_daily (code, effective_date)` | KRX 가격 PK + range scan |
| `prices_daily` | `ix_prices_daily_lineage_date` | lineage 단위 시계열 fetch |
| `financials` | `ix_financials_code_account_date` | `(code, account)` PIT scan |
| `financials` | `ix_financials_superseded_by` | supersede chain 추적 |
| `corporate_actions` | `ix_corporate_actions_code_announced` | 이중 PIT announced 기준 |
| `corporate_actions` | `ix_corporate_actions_code_effective` | 이중 PIT effective 기준 |
| `corporate_actions` | `ix_corporate_actions_superseded_by` | supersede chain 추적 |

**M1+ 검토 backlog**:
- `prices_daily` 월별 partition (PostgreSQL declarative partitioning)
- `stocks_master.current_code` partial unique index (PG-only)
- `code_history` JSONB GIN index (M2 ETF universe 합류 시)

---

## 4. 무결성 invariant

### 4.1 Source Citation — append-only

ADR-0002 D5 line 190 의 "UPDATE 금지" 를 강제:

```sql
-- PostgreSQL only (Alembic migration 0001 의 dialect 분기)
CREATE OR REPLACE FUNCTION speculum_source_citations_no_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'source_citations is append-only (ADR-0002 D3/D5)';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER speculum_source_citations_no_update_trg
BEFORE UPDATE ON source_citations
FOR EACH ROW
EXECUTE FUNCTION speculum_source_citations_no_update();
```

SQLite (test) 는 trigger 미적용 — Repository layer 의 `SqlCitationRepository.
save()` 의 id 중복 검사가 부분 방어.

### 4.2 정정공시 chain — supersede 자기참조

`financials.superseded_by` 와 `corporate_actions.superseded_by` 는 같은 테이블의
nullable self-FK. ADR-0009 D5 의 chain 해소 알고리즘은 `PITEnforcer` 가 단일
source (`server/app/services/pit_enforcer.py`). SQL/Fake 양쪽 repository 가 같은
알고리즘 (`latest_active_by_key` / `filter_active_records`) 위임.

**Chain 무결성 검증**:
- cycle 검출 → `PITDataCorruptionError`
- depth > 100 → `PITDataCorruptionError`

### 4.3 PIT (Point-in-Time)

- **Price**: `trade_date <= as_of` (정정 없음)
- **Financial**: `effective_date <= as_of` + active-at-as_of (supersede chain)
- **CorporateAction**: `announced_date <= as_of` + chain 해소 (이중 PIT)

모든 Repository method 가 `as_of: date` keyword-only required — type-level
강제 (ADR-0008 D5).

### 4.4 출처 의무

모든 fact (`prices_daily`, `financials`, `corporate_actions`) 의 `citation_id`
는 `source_citations.id` 의 FK NOT NULL. 빌드 게이트 (T23) 가 citation 누락
record 생성 차단.

---

## 5. 운영 vs 테스트 DB 매핑

| 환경 | URL | Driver | 특이사항 |
|---|---|---|---|
| Production | `postgresql+psycopg://...` | psycopg3 | JSONB 활성, append-only trigger 활성 |
| Local dev | `postgresql+psycopg://localhost:5432/speculum` | psycopg3 | 운영 동일 |
| Unit test | `sqlite:///:memory:` (built-in) | sqlite3 | JSON 사용, trigger 미적용, UTCDateTime type 으로 tz-aware 복원 보강 |

**SQLite 단위 테스트 우회**:
- `UTCDateTime` type (`server/app/db/types.py`) 이 SQLite tz-naive 복원 시 UTC
  부착 — 도메인 invariant 일관.
- `with_variant(JSONB(), "postgresql")` 로 JSON 컬럼 dialect 분기.
- append-only trigger 는 `if dialect.name == "postgresql"` 분기로 SQLite 생략.

---

## 6. Alembic 명령어 cheat-sheet

```bash
# 운영 — PostgreSQL.
SPECULUM_DATABASE_URL="postgresql+psycopg://speculum:***@host:5432/speculum" \
  python -m alembic upgrade head

# 로컬 dev — SQLite (운영 image 와 schema 동일성 검증).
SPECULUM_DATABASE_URL="sqlite:///local.sqlite" \
  python -m alembic upgrade head

# 새 migration 추가 (autogenerate).
SPECULUM_DATABASE_URL="postgresql+psycopg://..." \
  python -m alembic revision --autogenerate -m "T?? <slug>"

# Offline SQL export (DBA review).
SPECULUM_DATABASE_URL="postgresql+psycopg://..." \
  python -m alembic upgrade head --sql > upgrade.sql

# Downgrade (개발 환경 한정).
python -m alembic downgrade base
```

---

## 7. M1+ 확장 계획 (참고)

- **users / OAuth** — M0 T0 에서 NextAuth 연동 시 추가.
- **watchlists / screener_sets / screen_runs** — Phase 3 합류 (T13 Phase B).
- **factor_definitions / factor_packs** — M2 Factor Lab 본격화 시.
- **stock_snapshots** — 일배치 정상화 (T18/T19) 후 factor 결과 precompute.
- **partition** — `prices_daily` 월별 (PG declarative).
- **JSONB GIN** — `code_history` 검색 가속 (M2 ETF 합류 시).

---

## 8. 검증 체크리스트 (T13 acceptance)

- [x] 5 core 테이블 ORM 모델 (`server/app/db/orm/*.py`)
- [x] Alembic config + initial migration (`server/alembic/`)
- [x] 5 SQL repository 구현체 (Fake 와 동일 contract)
- [x] ORM ↔ Record dataclass 변환 (`server/app/db/converters.py`)
- [x] SQLite in-memory contract test (15 tests passing)
- [x] `UTCDateTime` type 으로 tz-aware 일관성
- [x] PostgreSQL append-only trigger (source_citations)
- [x] ER 다이어그램 (본 문서)
