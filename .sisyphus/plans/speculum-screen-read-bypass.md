# Speculum — Screen read-bypass (ⓓ) 실행 work plan

> stock_snapshots precompute lookup 으로 `screen_active_codes` 의 종목별 live factor
> 평가를 대체. market_overview read-bypass(slice 2b, `market.py
> _serve_overview_from_snapshot`)의 검증된 패턴을 screen 의 **조건-부분집합 +
> AND short-circuit + 임계비교 + artifact byte-재현** 컨텍스트로 이식.

## Context

### Original request
screen read-bypass 의 **Slice 1+ 실행 work plan** 작성. 모든 게이팅 결정 확정 →
interview 재진행 없이 바로 플랜. 산출물: 본 파일.

### 확정 결정 (재질문 불요)
- **Q1/Slice 0 측정 완료 → 조건부 GO**: `scripts/bench_screen_eval.py` 실측. screen
  CPU 의 **~80-94% 가 factor 평가**(`evaluator.evaluate`), prime/bulk ~2-3%(ⓐⓑ 이미
  캐싱). per-code eval ~570µs(financial-only 2-factor 하한; price factor 는
  PriceAdjuster 로 더 큼). N=2500 wall ~615ms. oracle M2("ⓐⓑ 가 이미 비용 제거")
  **부분 반증** — read-bypass 이득 천장 높음. 상세:
  `server/docs/screen_read_bypass_slice0_findings.md`.
- **Q2**: 측정 게이트(Slice 0) 통과 → Slice 1 진행.
- **Q3/coherency = 더 엄격한 규칙**: `result_codes` 는 저장·byte-재현 artifact 라
  market_overview(live observation)보다 엄격. **live serve 경로만** read-bypass +
  `data_versions` equality + `computed_at` vs `collect_batch_versions` max
  `started_at` 가드(best-effort).

### Research findings (코드베이스 grounding — 인프라 전부 실재)
- **precompute producer**: `server/batch/snapshot_daily.py` — screen 라이브 경로와
  **동일 evaluator·동일 DbFieldProvider·동일 caching bulk prime** 를 미러 →
  byte-동일 producer(파일 docstring L5-7, L144-146). cutoff=None live precompute(L31-35),
  UPSERT(L36-39), `collect_run_data_versions(observed_date, session, pack)` 로
  data_versions freeze(L26-30). consumer 계약을 producer docstring 이 명시(L229-236):
  **batch_run.status='success' 로 완전성 추론 금지 — per-(code,factor) miss/stale/
  reproduce 시 live fallback 필수**(oracle M3).
- **scheduler 순서**: `server/batch/scheduler.py` — `all` = corp_code→ecos→kosis→
  dart→**snapshot**(L77-81, L460-462). snapshot 이 raw 적재 **후** 실행 → as_of=T 의
  EOD-확정 데이터로 precompute → snapshot 의 `data_versions` 가 그 시점 krx/dart
  batch_id 를 pinned(slice 1a 가설의 근거).
- **repo**: `StockSnapshotRepository.fetch_snapshots_for_code(code, *, as_of,
  factor_uuids: frozenset[UUID] | None = None)` 실재
  (`pit_protocols.py:810`) — **조건-부분집합 fetch 지원**(screen 은 전 factor 아닌
  조건 factor 만 필요).
- **record**: `StockSnapshotRecord`(`pit_protocols.py:343`) — `value: Decimal | None`
  (None=미산정), `factor_uuid`, `data_versions: Mapping[str,str]`, `computed_at:
  datetime`.
- **ORM 정밀도**: `stock_snapshots.value` 는 `String(nullable)` — `str(Decimal)` 저장
  으로 **byte-동일 round-trip 보장**(`stock_snapshots.py:16-20`, 64-65). 고정 Numeric
  scale 의 정밀도 손실 회피. → 임계비교 byte-동일성의 근거.
- **market_overview 템플릿(slice 2b)**: `market.py:223 _serve_overview_from_snapshot`
  — per-code all-or-nothing serve; predicate = 전 needed factor uuid 존재 +
  `dict(snap.data_versions) == current_data_versions`; 아니면 None → live fallback.
  `current_dv` = `collect_run_data_versions(as_of, session, pack)`, session None(Fake)
  이면 None → 전부 live. **wiring 은 호출부 분기**(provider wrap 아님).
- **screen wiring 지점**: `screen.py:507` — `_passes_all_conditions(provider, compiled,
  evaluator, as_of=as_of)` 호출부. `_passes_all_conditions`(L253) 가 condition loop
  에서 `evaluator.evaluate` 호출 + AND short-circuit.
- **SnapshotRepoDep**: `repositories.py:668` 실재 — market route 에 이미 주입됨. screen
  route 추가 주입만 필요.

---

## Work Objectives

### Core Objective
live screen serve 경로(`POST /api/screen` 의 `execute_screen`)에서, 조건 factor 가
precompute snapshot 에 hit 하고 freshness/coherency 가드를 통과하는 종목에 대해
factor 평가(`_passes_all_conditions` 의 per-condition `evaluator.evaluate`)를 snapshot
lookup 으로 대체하여 screen latency 를 절감한다. **result_codes 는 live 평가와
byte-동일**(§2.10 불변)해야 하고, **reproduce/save/miss/stale 는 무조건 live fallback**
(회귀 0)이다.

### Deliverables
1. **Slice 1a**: hit-rate cadence 측정 harness + 측정 결과 문서(실현 이득 = 천장 ×
   hit-rate 확정). behavior 무변경.
2. **Slice 1b**: screen route 의 read-bypass 구현 + serve predicate + reproduce/save
   bypass + 테스트 + oracle 3-게이트 통과.

### Definition of Done
- Slice 1a: as_of=today(EOD 확정 후) / 과거 일자 screen 의 snapshot hit-rate 를
  실측, "천장 × hit-rate = 실현 이득" 수치화. 측정 결과로 1b 의 실착수 정당성 확인.
- Slice 1b: hit 종목 result_codes == live result_codes(byte-동일 테스트 green) +
  reproduce/save/miss/stale → live(회귀 테스트 green) + `server` 전체 회귀 0 + ruff
  clean + forbidden-words clean + oracle 구현후 리뷰 Critical 0.

---

## Must Have / Must NOT Have (Guardrails)

### Must Have
- **live serve 경로 한정**: `batch_cutoff is None`(=non-reproduce) 일 때만 read-bypass.
- **all-or-nothing per code**: 한 종목의 **모든 조건 factor** uuid 가 hit + 전부
  data_versions 일치해야 serve. 하나라도 miss/stale → 그 종목은 통째로 live(부분
  serve 금지 — market_overview 패턴 동일, oracle 권고).
- **호출부 분기 wiring**: `_passes_all_conditions` 호출 직전/내부에서 snapshot serve
  시도 → 성공 시 평가 skip, 실패 시 기존 live 평가. **provider wrap 금지**(oracle).
- **byte-동일 임계비교**: snapshot value(str→Decimal)와 live evaluate 결과 Decimal 이
  `_OP_COMPARATORS` 비교에서 동일 결과. ORM String round-trip 이 근거.
- **회귀 0 fallback**: snapshot_repo 미주입 / current_dv None(Fake) / snapshot 미존재 /
  stale / reproduce(cutoff!=None) → 전부 live(기존 동작).
- **feature flag = snapshot_repo 미주입**: route 가 SnapshotRepoDep 미주입 또는 None
  이면 전부 live → 즉시 롤백 경로.

### Must NOT Have
- save_run / reproduce_run 경로의 read-bypass(§2.10 제약 1 — 저장 run = 사용자가 본
  screen, frozen 재실행은 cutoff 기반 live 여야 함).
- 부분 serve(일부 조건만 snapshot, 나머지 live) — all-or-nothing 만.
- batch_run.status='success' 로 snapshot 완전성 추론(producer docstring L229-236 금지).
- custom pack 특수 처리 — `factor_pack_content_hash` 불일치로 **자동 live**(설계상 안전).
- snapshot producer(`snapshot_daily.py`) 변경 — 본 cycle 은 **consumer(read 경로)만**.

---

## Task Flow and Dependencies

```
Slice 0 (완료, 게이트 통과)
   │
   ▼
Slice 1a (hit-rate cadence 측정 — 선행 권장, behavior 무변경)
   │  실현 이득 수치 확정 → 1b 착수 정당성
   ▼
Slice 1b (read-bypass 구현)
   ├─ oracle 설계검토 게이트 (구현 전)
   ├─ 구현
   └─ oracle 구현후 리뷰 게이트 (Critical 0 → 커밋)
```

Slice 1a 와 1b 는 독립 착수 가능하나, 1a 가 "실현 이득이 hit-rate 로 무의미하지
않은지"를 먼저 확인하므로 **선행 권장**. 1a 결과가 hit-rate ~0 이면 1b NO-GO 재검토.

---

## Slice 0 (완료) — 측정 게이트

**상태**: 완료. `scripts/bench_screen_eval.py` + `server/docs/screen_read_bypass_
slice0_findings.md`. **조건부 GO** — 이득 천장 높음(평가가 screen CPU 80-94% 지배),
단 실현 이득 = 천장 × hit-rate 라 1a 가 hit-rate 를 확정.

---

## Slice 1a (선행 권장) — hit-rate cadence 측정

### 목표
precompute→KRX 일배치 주기에서 as_of=today(EOD 확정 후) / 과거 일자 screen 의
snapshot hit-rate 를 시뮬/측정하여 **실현 이득(=천장 × hit-rate)** 을 확정. behavior
무변경(측정 전용 harness).

### 가설 (Slice 0 findings M1 재평가)
precompute 가 KRX 일배치 **후** 실행(scheduler `all` = …→dart→snapshot)되면, as_of=T
의 EOD-확정 데이터로 precompute 된 snapshot 의 `data_versions` 가 as_of=T 의 krx/dart
batch_id 를 pinned → 그날 이후 as_of=T screen 이 **일관 hit**. intraday batch churn 이
없는 EOD-확정 cadence 에선 hit-rate 양호.

### 변경 파일
- `server/scripts/bench_screen_hitrate.py` (신규, 측정 전용 — 운영 코드 무변경)

### 측정 설계
1. **serve predicate 시뮬레이터**: Slice 1b 가 쓸 predicate 와 **동일 로직**(전 조건
   factor uuid 존재 + `dict(snap.data_versions) == current_dv` + computed_at 가드)을
   harness 안에서 재현(1b 의 predicate helper 가 아직 없으므로 harness 가 동형 구현,
   1b 에서 helper 추출 후 harness 가 재사용하도록 정리 — 중복 로직 drift 방지).
2. **시나리오**:
   - (a) as_of = 가장 최근 EOD-확정일, precompute 직후 — 기대 hit-rate 높음.
   - (b) as_of = 과거 일자(snapshot 존재) — 기대 hit-rate 높음(append-only batch).
   - (c) as_of = today 인데 precompute 미실행(snapshot 부재) — 기대 hit-rate 0(전부
     live, 회귀 0 경로).
   - (d) precompute 후 raw 배치(dart/krx) 재실행으로 batch_id 변경 — 기대 mismatch
     → 0 hit(stale → live, freshness 가드 동작 확인).
3. **측정 지표**: 종목별 all-condition-hit 비율(=serve 가능 종목 / universe), 대표
   조건셋(financial-only 2-factor, price-factor 포함 셋) 별로.
4. **이득 환산**: hit-rate × Slice 0 천장(per-code eval ~570µs+, N=2500) =
   예상 wall 절감.

### 테스트 / 검증
- harness 는 운영 코드 import 만(behavior 무변경). 실데이터/seed 양쪽에서 실행 가능.
- harness 자체 단위 테스트 불요(측정 도구), 단 predicate 시뮬 로직이 1b helper 와
  동형임을 1b 에서 교차검증(1b 가 helper 추출 후 harness 가 그 helper 호출로 전환).

### 리뷰 게이트
- **oracle-medium** 측정 설계 sanity check(시뮬 predicate 가 1b 와 동형인지, 시나리오
  d 의 stale 재현이 실제 freshness 가드를 대표하는지). 측정이므로 경량.

### §2.10 위험
- 낮음(behavior 무변경). 유일 위험: 시뮬 predicate 가 1b 실구현과 **divergent** 하면
  측정이 호도. 완화 = 1b 에서 predicate 를 단일 helper 로 추출 + harness 가 그 helper
  를 import(1b 완료 후 harness 리팩터, 또는 1a 에서 helper 를 먼저 정의하고 1b 가 채택).

### "다음 cycle 바로 착수" 구체성
- 신규 파일 1개(`bench_screen_hitrate.py`), 운영 코드 무변경, 측정 시나리오 4개 명시,
  기대 결과 명시. seed 데이터로 즉시 (c) 확인 가능, 실데이터 nightly 환경에서 (a)(b)(d)
  확인. **다음 cycle 즉시 착수 가능**.

---

## Slice 1b — read-bypass 구현

### 목표
screen route 에 SnapshotRepoDep 주입 + `screen_active_codes` 에 `current_data_versions`
계산 + `_passes_all_conditions` 호출부 분기. serve predicate 통과 종목은 평가 skip,
나머지는 live. reproduce/save 무조건 live.

### 변경 파일
1. `server/app/api/routes/screen.py`
   - `execute_screen`: `snapshot_repo: SnapshotRepoDep` 파라미터 추가, `current_dv =
     collect_run_data_versions(as_of, session, pack) if session else None` 계산(market.py
     L133-137 패턴), `screen_active_codes(..., snapshot_repo=..., current_data_versions=...)`
     전달.
   - `screen_active_codes`: `snapshot_repo: StockSnapshotRepository | None = None`,
     `current_data_versions: Mapping[str,str] | None = None` 파라미터 추가(default None
     = 기존 동작 불변 — 하위호환). 루프 안 `_passes_all_conditions` 호출 직전 snapshot
     serve 시도.
   - 신규 helper `_passes_all_conditions_from_snapshot(...)` 또는
     `_serve_conditions_from_snapshot(...)` — market.py `_serve_overview_from_snapshot`
     의 조건-부분집합 버전. all-or-nothing per code + data_versions equality +
     computed_at 가드. **batch_cutoff is None 일 때만 호출**(reproduce 차단).
   - serve predicate helper 는 Slice 1a harness 와 공유 가능하게 순수 함수로.
2. `server/tests/test_api/test_screen_read_bypass.py` (신규)
3. (필요 시) `server/app/repositories/fakes.py` — FakeStockSnapshotRepository 가
   `fetch_snapshots_for_code` 의 `factor_uuids` 필터를 지원하는지 확인(`fakes.py:754`
   실재 — 시그니처 검증, 미지원 시 보강).

### serve predicate (조건-부분집합 + 더 엄격, Q3)
한 종목 `code` 가 다음을 **모두** 만족하면 평가 skip 하고 snapshot value 로 판정:
1. `snapshot_repo is not None` and `current_data_versions is not None`(미주입/Fake →
   None → live).
2. `batch_cutoff is None`(reproduce 아님 — krx/dart cutoff 둘 다 None 확인. 단
   reproduce 는 cutoff 주입하므로 `krx_batch_cutoff is None and dart_batch_cutoff is
   None` 게이트).
3. `compiled` 의 **전 조건 factor uuid** 가 `fetch_snapshots_for_code(code, as_of,
   factor_uuids=<조건 factor uuid set>)` 결과에 존재(하나라도 miss → live).
4. 각 snapshot 의 `dict(snap.data_versions) == current_data_versions`(하나라도 stale
   → live). market_overview 패턴 동일.
5. **(Q3 추가 가드)** 각 snapshot 의 `computed_at >= collect_batch_versions(as_of,
   session) 의 max started_at`(best-effort late-commit 차단 — 아래 위험 분석 참조).
   단순화: snapshot 의 data_versions 가 이미 batch_id 를 포함하므로 (4) 가 batch
   세대를 잡고, (5) 는 **out-of-order late-commit(started_at<=기존 max 라 batch_id
   불변)** 의 보조 best-effort. oracle 설계검토에서 (5) 의 비용/효과 확정(필요 시
   생략 가능 — artifact 강화 의도이나 근본 차단 불가, market_overview 와 동일 known-limit).

serve 성공 시: 각 조건에 대해 snapshot value 로 `_OP_COMPARATORS` 비교 + None →
조건 불충족(live `is_na` 와 동일 의미). AND short-circuit 동일 적용. 전 조건 통과 시
matched 에 추가.

### 핵심 정확성 논증 (gap 분석 — Metis 대체 자체 검증 완료)
> 아래는 market_overview(전-factor, 단순 집계) → screen(조건-부분집합, AND
> short-circuit, 임계비교, byte-재현 artifact) 이식에서 발생하는 위험을 코드 근거와
> 함께 정리. 각 항목을 1b 테스트로 가드.

1. **[Critical] AND short-circuit vs snapshot up-front fetch**: live
   `_passes_all_conditions`(L270-280)는 첫 실패/N/A 조건에서 short-circuit → live 가
   **더 적은** factor 만 평가할 수 있다. snapshot serve 는 판정을 위해 **전 조건
   factor 를 up-front fetch** 해야 한다.
   - **byte-동일성 영향 없음**: result_codes 는 "통과 종목 코드의 정렬 집합"이지
     평가 횟수가 아니다. snapshot 의 condition-by-condition 판정도 동일 순서로 AND
     short-circuit 적용하면 **최종 통과/탈락 결정이 live 와 동일**. snapshot value
     None → 조건 불충족(live `is_na or value is None` 과 동일, L274) → 탈락. **위험:
     up-front fetch 했는데 조건 factor 중 일부만 snapshot 에 있으면(나머지 미산정)
     all-or-nothing 가드(predicate 3)가 live 로 보냄** — short-circuit 으로 live 가
     그 factor 를 평가 안 했을 수도 있으나, 안전 측(live)으로 빠지므로 정확성 보존.
   - **테스트**: 첫 조건 탈락 종목이 두번째 조건 factor snapshot 없어도 결과 동일
     (snapshot serve 와 live 모두 탈락) / 첫 조건 통과 + 두번째 탈락 종목 동일.
2. **[Critical] 임계비교 정밀도 round-trip**: live 는 갓 평가한 Decimal, snapshot 은
   String→Decimal. `stock_snapshots.value` = `String` + `str(Decimal)` 저장
   (`stock_snapshots.py:16-20`)이라 **scale 보존 byte-동일 round-trip**. producer 가
   동일 evaluator·Decimal context 사용(`snapshot_daily.py` L144-146) → 동일 입력엔
   동일 Decimal 산출. `_OP_COMPARATORS` 는 Decimal 비교라 scale 차이 무관(`Decimal("1.0")
   == Decimal("1.00")` 은 True). **테스트**: 동일 종목 live vs snapshot serve 의 조건
   판정(>=, <, == 경계값 포함) byte-동일.
3. **[High] 조건 factor 가 precompute 대상이 아닐 수 있음**: precompute(`snapshot_
   daily.py`)는 `pack.body["factors"]` 전체를 평가. active pack == builtin 이고 조건이
   builtin factor 면 hit. **custom pack 은 content_hash 불일치 → predicate 4 가
   자동 live**(안전). builtin 인데 신규 factor 추가 후 precompute 미실행 → miss →
   live(안전). **위험 없음 — 전부 안전 측 fallback**. 단 **테스트로 명시 가드**:
   custom pack screen → 전부 live, snapshot 미존재 factor 조건 → live.
4. **[Medium] computed_at 가드의 timezone/clock-skew**: `computed_at` = UTC
   tz-aware(`stock_snapshots.py:76` UTCDateTime), `started_at` = UTC tz-aware
   (`collect_batch_versions`), as_of = KST date. 비교는 **UTC datetime 간** 이라 tz
   일관(date 절단 금지). 실패 모드(`computed_at < max started_at`) → live(안전).
   clock-skew 로 false-stale 시 단지 live(성능만 손해, 정확성 무해). **oracle 설계검토
   에서 (5) 채택 여부 최종 결정** — market_overview 가 (5) 없이도 안전하다고 판단했고
   근본 차단 불가라, artifact 강화 목적의 best-effort 로만. 비용 = 종목당 추가 datetime
   비교(무시 가능).
5. **[Medium] security_types 사전 필터 보존**: screen 은 universe 를
   security_type 으로 사전 필터 후 평가(L405-415). snapshot lookup 은 사전 필터 **후**
   universe_records 루프 안에서 일어나므로 필터 보존(snapshot 은 종목 단위 lookup —
   security_type 무관, 필터된 종목은 애초 루프에 없음). **위험 없음**. 테스트로
   security_types 지정 screen 의 snapshot serve 결과가 live 와 동일 모집단 확인.
6. **[High] reproduce/save bypass 누락 위험**: `screen_active_codes` 는 `execute_screen`
   (live)·`runs.py save_run`·`reproduce.py reproduce_run` 가 호출. **save/reproduce 는
   snapshot_repo/current_dv 를 주입하지 않으면 default None → 전부 live**. 따라서
   **명시적 안전장치 = save_run/reproduce_run 호출부에 snapshot_repo 미전달**(또는
   reproduce 의 cutoff!=None 이 predicate 2 에서 차단). 이중 안전: (a) save/reproduce 가
   snapshot_repo 미주입(default None), (b) reproduce 의 cutoff 주입이 predicate 2 차단.
   **테스트**: reproduce_run 이 cutoff 주입 시 snapshot_repo 주입돼도 live(byte-동일
   재현) / save_run 이 live 평가(저장 run = 사용자가 본 screen).
7. **[Medium] inputs/citation 미사용**: read-bypass 는 value 만 사용. snapshot 의
   inputs/citation 은 serve 에 불요(result_codes 는 코드 집합). market_overview 와 동일.

### 테스트 (`test_screen_read_bypass.py`)
- **hit → eval skip**: snapshot 존재 + data_versions 일치 → `evaluator.evaluate` 호출
  안 됨(spy/mock 으로 호출 0 확인) + result_codes 정확.
- **byte-동일**: 동일 universe·조건에서 live screen result_codes == snapshot serve
  result_codes(경계 임계값 포함, >=/</== 연산자별).
- **miss → live**: snapshot 없는 종목은 live 평가(spy 호출 확인).
- **stale → live**: snapshot.data_versions != current_dv(batch_id 변경) → live.
- **reproduce → live**: cutoff!=None 이면 snapshot_repo 주입돼도 전부 live + byte-동일
  재현.
- **save → live**: save_run 경로 live 평가(§2.10 대칭).
- **custom pack → live**: content_hash mismatch → 전부 live.
- **Fake(session None) → live**: current_dv None → 전부 live(회귀 0).
- **partial factor → live**: 조건 factor 중 일부만 snapshot 존재 → all-or-nothing →
  live.
- **None value → 조건 불충족**: snapshot value None → 그 조건 탈락(live is_na 동일).
- **security_types → 모집단 동일**: 자산군 필터 + snapshot serve 결과 = live 모집단.

### 리뷰 게이트
1. **oracle 설계검토(구현 전)**: predicate 설계(특히 (5) computed_at 가드 채택 여부,
   reproduce/save 이중 안전장치, all-or-nothing 경계), byte-동일 논증 sanity check.
2. **구현**.
3. **oracle 구현후 리뷰**: Critical 0 확인 → 커밋. §2.10 byte-재현 테스트가 가드인지,
   회귀 0 fallback 전 경로 커버되는지 검증.

### §2.10 위험 + 롤백
- **위반 감지**: reproduce byte-동일 테스트 + save live 테스트가 가드. 통과 = §2.10
  불변 유지.
- **즉시 롤백**: feature flag = snapshot_repo 미주입 → 전부 live(기존 동작). route 의
  SnapshotRepoDep 제거 또는 None 전달로 코드 변경 없이 disable.

### "다음 cycle 바로 착수" 구체성
- 변경 파일 명시(screen.py 정확한 함수·라인 컨텍스트, 신규 테스트 파일), serve
  predicate 5조건 명시, 11개 테스트 케이스 명시, market.py 이식 template 명시. oracle
  게이트 3개 명시. **다음 cycle 즉시 착수 가능**.

---

## Commit Strategy
- 사용자 정책(memory): **develop 직접 커밋, feature 브랜치 금지, push 는 명시 키워드
  ("푸시"/"올려") 있을 때만**.
- Slice 1a: 측정 harness + findings 문서 → 단일 커밋(`perf: screen read-bypass
  hit-rate cadence 측정 — slice 1a`). behavior 무변경.
- Slice 1b: 구현 + 테스트 → oracle 구현후 리뷰 Critical 0 후 단일 커밋(`perf: screen
  read-bypass — snapshot lookup 으로 live eval 대체 (slice 1b)`).
- 각 커밋 전: `server` 전체 회귀 0 + ruff clean + forbidden-words clean.

---

## Success Criteria
- Slice 1a: hit-rate 4 시나리오 실측, 실현 이득 수치화, 1b 착수 정당성 확인(또는
  hit-rate ~0 시 NO-GO 재검토).
- Slice 1b: byte-동일 + 회귀 0 fallback 전 테스트 green + `server` 전체 회귀 0 + ruff/
  forbidden clean + oracle Critical 0. live screen latency 가 hit 시 Slice 0 천장만큼
  절감(평가 CPU 80-94% → snapshot lookup).

---

## oracle 설계검토 확정 (Slice 1b 구현 즉시 가능 사양)

oracle 조건부 GO(한 cycle 구현 가능). 아래 4개를 GO 조건으로 박는다.

### 결정 1 — computed_at 가드(predicate 5): **미채택**
data_versions 의 krx/dart/dividend batch_id 가 freshness 내장(새 배치→batch_id 변경
→equality mismatch). computed_at 가 잡는 잔여(out-of-order late-commit backfill,
started_at<=기존 max)는 `collect_batch_versions` ORDER BY started_at DESC 가 그
backfill 을 max 로 선택 안 하므로 **computed_at 도 동일하게 못 잡음**(근본 한계,
market_overview 동등). artifact 라는 점은 가드 효과 불변(가드는 value 동일성이 아닌
시각만 검사). → **predicate (1)~(4)만, (5) 제거.**

### 결정 2 — serve predicate helper: **공유 순수 helper 신규 추출**
market_overview `_serve_overview_from_snapshot` 의 `factor_uuids` 일반화 금지(반환
계약 다름: market=value dict, screen=조건 통과 bool; 모듈 결합 위험). 대신:
`app/services/snapshot_serve.py` 신규 — `try_serve_snapshot_values(code, *, as_of,
factor_uuids, snapshot_repo, current_data_versions) -> dict[UUID, Decimal|None] | None`
(predicate 1~4, 전 uuid hit + data_versions 일치면 `{uuid: value}`, 아니면 None).
- market_overview helper 는 이 공유 helper 에 위임 후 uuid→fid 재매핑(drift 0).
- screen `_passes_all_conditions_from_snapshot(compiled, served)`: 공유 helper 의
  `{uuid: value}` 로 각 조건 비교(None→False, 아니면 `_OP_COMPARATORS`) AND
  short-circuit. None(serve 불가)→호출자가 기존 live `_passes_all_conditions` fallback.
- 1a harness 도 `try_serve_snapshot_values` 만 import(drift 0).

### [Critical] C1 — predicate current_dv 는 **pack-aware**, 응답 freeze 와 분리
predicate (4) current_dv = `collect_run_data_versions(as_of, session, pack)` (**pack
인자 필수** — producer snapshot_daily.py:262 3-arg 와 factor_pack_* 일치). **응답 freeze
(screen.py:136, 2-arg)는 손대지 말 것**(바꾸면 저장 result_hash 변경=§2.10). pack 미전달
시 custom pack screen 이 builtin snapshot serve→§2.10+정확성 위반. **테스트**: custom
pack screen→전부 live.

### [Critical] C2-a — dividend-의존 factor 조건은 serve 제외(live)
dividend 정정은 dividend_batch_id(FSC) 가 현재 항상 ""(FSC 배치 미존재)라 equality 가
못 잡음→stale serve→snapshot result_codes ≠ 동시점 live(§2.10 위반·save-live 정합성
붕괴). CA 는 DART ingest 라 dart_batch_id 가 포착→안전. 따라서 dividend-의존 input 을
가진 factor 가 조건에 하나라도 있으면 screen serve 비활성(전부 live):
`_DIVIDEND_DEPENDENT_INPUTS = frozenset({"dividend_per_share_trailing_annual",
"total_return_trailing_1y"})` — 조건 factor formula.inputs 와 교집합이면 ineligible
(builtin: dividend-yield:trailing-annual, price-return:total-annual). **테스트**:
dividend factor 조건 screen→전부 live.

### [Critical 확인] EvaluationResult `is_na ⟹ value None`
producer 가 value 만 저장(is_na flag 미저장)→live 의 `is_na=True & value!=None` 케이스
있으면 serve(value 통과판정) vs live(is_na 탈락) 갈림. **factor_evaluator 에서 is_na 시
value=None 불변 코드 확인**(아니면 양쪽 보정).

### Medium
- **M1**: `_CompiledCondition` 에 factor_uuid slot(`_compile_conditions` 에서 UUID 1회
  파싱, 핫패스 재파싱 회피).
- **M3**: **1b 먼저** `try_serve_snapshot_values` 추출 → 1a harness 가 재사용(1a 선행
  시 predicate 과대평가). 플랜의 1a→1b 순서를 1b(helper 추출)→1a 로 조정.

### reproduce/save 안전 (확인)
reproduce_run: snapshot_repo 미주입 + cutoff!=None 이중 차단. save_run: 미주입→live;
serve==live byte-동일(C1+C2-a 전제)이라 "본 screen==저장 run" 불변 유지. **C2-a 가
save-live 정합성 전제.**

### GO 조건 요약
C1(pack-aware current_dv 분리)·C2-a(dividend factor 제외)·is_na⟹None 확인·predicate(5)
미채택·공유 helper(snapshot_serve.py)·M1(factor_uuid slot). market_overview 템플릿
재사용으로 추가 분할 불요.
