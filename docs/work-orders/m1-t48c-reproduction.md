# 작업 지시서: T48c — Screen Run 재현 필터 (Reproducibility)

> 작성: 2026-05-29 (Momus rev1 REJECT 반영 rev2). 상위: `docs/work-orders/m1-milestone.md` T48c.
> 선행 완료: T48(batch_runs+data_versions freeze), T49(append-only+supersede), T50(FieldProvider),
> market_cap/treasury 영구화, Screener 조건 매칭(`screen_active_codes`).

## 배경 / 목적

저장된 Screen Run snapshot 은 `data_versions` 에 `krx_batch_id`+`dart_batch_id` 를 freeze(T48b). **재현** = 그 frozen batch 기준 재실행 시 **byte-동일** result_codes/result_hash (AC-P-10/§2.10). 현재 screen 은 최신 데이터를 읽어, snapshot 이후 도착한 batch(정정 or backfill)가 factor 값을 바꿔 재현이 깨진다. T48c 는 fact fetch 를 **frozen batch 이하 batch 가 생산한 row 로 한정**한다.

## 핵심 정정 (Momus rev1)

1. **freeze 와 reproduce 의 batch 선택 기준은 대칭이어야 한다 (C#2).** freeze(`snapshot_versions.py:160-172`)는 `func.date(started_at)<=as_of` 후보 중 `ORDER BY started_at DESC, id DESC LIMIT 1` — **id 가 final tiebreaker**. 따라서 재현 cutoff 는 단순 `started_at <=` 가 아니라 **`(started_at, id)` lexicographic ≤ (frozen.started_at, frozen.id)** 여야 한다. 단순 started_at 비교는 started_at tie(동시/재시도 batch)에서 freeze 가 고르지 않은 batch 의 row 까지 통과시켜 재현을 깬다.
2. **price/market_cap 은 supersede 가 없다 (C#1).** `prices_daily`/`market_caps` 에 `superseded_by` 컬럼 없음(KRX 무정정). 따라서 "successor 필터 제외 → 보수 분기 active 복원" 메커니즘은 **financial/treasury 에만** 적용. KRX 의 cutoff 는 **backfill(과거 영업일 데이터를 늦은 batch 가 보충) row 제외** 가 목적 — `effective_date<=as_of` 와 직교. 별도 분석·테스트.
3. **record 는 citation_id 를 보유한다 (H#4).** `FinancialRecord`/`PriceRecord` 등 모두 `citation_id` 필드 보유(`pit_protocols.py:93,117,147,173`). Fake 는 별도 map 이 아니라 **record.citation_id 로 batch 직접 조회**.

## 변경 계획

### 1. batch cutoff 표현 — (started_at, id) 쌍
- `BatchRunRepository` 에 `get_run(batch_id) -> BatchRunRecord | None` 추가(Sql+Fake). frozen batch_id → `(started_at, id, source)`.
- cutoff = `BatchCutoff(started_at, id)` (frozen batch 의 쌍). 비교는 lexicographic: batch X 통과 ⟺ `(X.started_at, X.id) <= (cutoff.started_at, cutoff.id)`. **freeze 의 `started_at DESC, id DESC` 선택과 정확히 대칭** — frozen batch 자신 + 그 이전 batch 만 통과.

### 2. fetch 메서드 cutoff 필터 (4 repo, default None=무필터)
- 각 fetch 에 `batch_cutoff: BatchCutoff | None = None` 추가. **default None → 기존 동작 완전 보존(회귀 0).**
- **SQL**: cutoff 시 `JOIN source_citations sc ON fact.citation_id=sc.id JOIN batch_runs br ON sc.batch_id=br.id WHERE br.source = :source AND ((br.started_at < :cs) OR (br.started_at = :cs AND br.id <= :cid))`. `source` 필터 필수(H#3 — fact 종류↔source 정합 방어: KRX fetch→'KRX', DART fetch→'DART').
- **financial/treasury**: cutoff join 으로 row 제외 → 그 위에서 `latest_active_by_key` chain 해소. 이후 batch 의 successor 가 빠지면 원 row 가 `pit_enforcer:306-311` 보수 분기(`record_by_id.get(superseded_by) is None`→active=True)로 복원. **NoActiveRecordError 흡수(M#6)**: cutoff+as_of 곱집합이 빈 candidates 면 `latest_active_by_key` 가 raise — fetch 가 catch 하여 빈 결과(없는 fiscal_period)로 처리, 500 금지.
- **price/market_cap**: supersede 없음. cutoff 는 backfill row 제외만. `fetch_latest`(market_cap)는 `effective_date<=as_of` 중 max 에 cutoff join 추가 → frozen 이후 batch 가 생산한 row 제외 후 max.
- **Fake**: 생성자에 `citation_runs: Mapping[UUID, BatchCutoff(+source)] | None` (citation_id → batch (started_at,id,source)). cutoff 시 `record.citation_id` 로 조회, source 일치 + `(started_at,id) <= cutoff` 아닌 record 제외. map 부재 시 cutoff 무시(테스트가 명시 주입 — 경고). SQL join 과 동일 의미(2-hop 평탄화).

### 3. FieldProvider 재현 모드
- `DbFieldProvider.__init__` 에 `krx_batch_cutoff: BatchCutoff | None=None`, `dart_batch_cutoff: BatchCutoff | None=None`. price/market_cap→krx, financial/treasury→dart 전달.
- **wiring thread-through (L2)**: `screen.py` 의 `_build_provider` + `screen_active_codes` 도 cutoff 파라미터를 받아 DbFieldProvider 에 전달해야 함 (현재 미수용). reproduce_run 이 이 경로로 cutoff 주입.
- **get_run 은 full `started_at` timestamp 반환 (L3)** — `func.date` 금지. cutoff lexicographic 비교는 full timestamp 기준.

### 4. 재현 실행 (빈 batch_id 처리 — H#5)
- `reproduce_run(snapshot, *, batch_run_repo, repos..., pack, evaluator) -> ReproduceResult(result_codes, matches: bool)`:
  - snapshot.data_versions 의 krx/dart batch_id → `get_run` 로 BatchCutoff 해소.
  - **빈 batch_id("")** = snapshot 시점 그 source 성공 batch 없음 → cutoff 를 "무필터"가 아니라 **그 source fact 전부 제외**(빈 결과로 재현 — as_of 시점 데이터 없음 상태 정확 재현, look-ahead 누출 차단). 명시적 sentinel `BatchCutoff.EXCLUDE_ALL`.
  - cutoff 주입 FieldProvider 로 `screen_active_codes`(snapshot.query.conditions) 재실행 → result_codes.
  - `matches` = (normalize 후 result_codes == snapshot.result_codes).
- (선택) `GET /api/runs/{id}/reproduce` — ReproduceResult 반환. universe 재평가 비용 주석.

## 회귀 테스트 (필수 — Momus rev1 경계 포함)
- **financial supersede (load-bearing)**: 원 row(batch1) + 정정 row(batch2, started>batch1, supersede 원). frozen=batch1 → 재현 시 batch2 제외 → 원 row 보수 분기 active → 원 값 → byte-동일. 대조: cutoff 없으면 정정 값.
- **started_at tie (C#2)**: batch1·batch2 started_at 동일, freeze 가 id DESC 로 batch2(또는 batch1) 선택. frozen=선택된 것 → 재현이 freeze 와 **정확히 동일 batch 집합**만 통과 (id tiebreak 일치). 단순 started_at 비교면 실패하는 케이스.
- **KRX backfill (C#1)**: market_cap 과거 영업일 row 가 frozen 이후 batch 로 backfill → 재현 시 제외 (cutoff). supersede 무관 path 검증.
- **source mismatch 방어 (H#3)**: financial fetch 에 dart_cutoff 줬을 때 KRX batch 의 citation row 가 (오염 가정) 통과 안 됨(source 필터).
- **빈 batch_id (H#5)**: dart_batch_id="" snapshot → financial/treasury 전부 제외(빈), matches 정확.
- **NoActiveRecordError 흡수 (M#6)**: cutoff 로 candidates 빈 fiscal_period → 예외 아닌 N/A.
- **하위호환**: cutoff=None 기존 4 repo 테스트 회귀 0.
- SQL(in-memory SQLite 실 join) + Fake(citation_runs 주입) 양쪽 동일 시나리오.

## 제약 / 완료 기준
- cutoff default None → 회귀 0. PIT(`effective_date<=as_of`)와 cutoff 는 직교(둘 다).
- 재현 정확성 load-bearing 체인(T49 in-place superseded_by × cutoff × `pit_enforcer:306-311`)은 financial/treasury 한정 — 코드 주석 + 회귀 테스트 lock. price/market_cap 은 별도(backfill) 논리.
- [ ] BatchCutoff (started_at,id) lexicographic + get_run. [ ] 4 repo cutoff(source 필터 포함). [ ] FieldProvider 재현 모드. [ ] reproduce_run(빈 batch_id=전부 제외, matches). [ ] 회귀 6종(supersede/tie/backfill/source/empty/no-active) + 하위호환. [ ] ruff clean, 전체 회귀 0.

## 리스크
- R1(C#2): freeze/reproduce 비대칭 → lexicographic (started_at,id) 로 해소 + tie 테스트.
- R2(C#1): KRX supersede 부재 안전망 오적용 → financial/treasury 한정 명시 + backfill 별도 테스트.
- R3(H#5): 빈 batch_id look-ahead → EXCLUDE_ALL.
- R4(H#4): Fake parity → record.citation_id 직접 + 양쪽 테스트.
