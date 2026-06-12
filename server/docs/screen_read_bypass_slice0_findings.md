# screen read-bypass — Slice 0 측정 게이트 결과

ⓓ screen read-bypass(stock_snapshots precompute lookup 으로 `screen_active_codes`
종목별 live factor 평가 대체) 진행 여부를 **실측으로 게이트**하기 위한 Slice 0.
oracle 가 단발 구현을 NO-GO 하고 prometheus 전략기획을 권고했으며, 사용자가
"가치 근거를 추정 대신 실측으로 결정" + "측정 슬라이스 먼저"를 선택했다.

측정 harness: `scripts/bench_screen_eval.py` (운영 코드 behavior 무변경, 측정 전용).
financial-only factor(eps + roe)로 합성 universe(N=200~2500)에 `screen_active_codes`
를 돌려 cProfile 로 시간을 분해. **price 기반 factor(per/pbr/total-return)는
PriceAdjuster 를 평가 안에서 수행하므로 본 측정은 이득 천장의 하한(lower bound)**.

## 측정 결과 (financial-only, 2 condition)

| N | wall(실측) | evaluate(profile %) | prime/bulk(%) | per-code eval | eval/prime |
|---|---|---|---|---|---|
| 200 | 22.7 ms | ~92% | 3% | 547 µs | 35× |
| 500 | 136.9 ms | ~94% | 2% | 639 µs | 56× |
| 1000 | 264.7 ms | ~78% | 2% | 590 µs | 36× |
| 2500 | 615.0 ms | ~92% | 3% | 570 µs | 27× |

(wall 은 un-profiled 실측 latency; evaluate/prime % 는 cProfile 오버헤드 회피 위해
profile-내부 비율 = cumtime/Σtottime.)

## 핵심 발견 (M2 가정 반증)

oracle 의 NO-GO 근거 중 **M2**("ⓐⓑ 가 이미 N+1 을 제거해 read-bypass 의 추가 이득은
작음")는 **부분적으로 반증된다**:

- ⓐⓑ 가 제거한 것은 **DB round-trip**(prime/bulk = 현재 screen CPU 의 **~2-3%**).
- 그러나 screen CPU 의 **~80-94% 는 factor 평가**(AST walk + Decimal 산술 + 종목별
  supersede chain 해소 + serve-time resolution)이고, **read-bypass 가 정확히 이 부분을
  snapshot lookup 으로 대체**한다. 즉 read-bypass 의 이득 천장은 **높다**.
- per-code 평가 ~550-640 µs(financial-only 2 factor 하한). 실제 screen(더 많은
  condition + price 기반 factor)은 PriceAdjuster 로 종목당 평가가 더 무거워져
  천장이 더 커진다. N=2500 financial-only 2-factor 만으로도 wall ~615 ms.

## go/no-go: **조건부 GO** (Slice 1 진행 가치 있음)

이득 천장이 높아(평가가 screen CPU 지배) read-bypass 는 hit 시 screen latency 를
대략 **~90% 절감**할 수 있다. 단 **실현 이득 = 천장 × hit-rate** 이며, hit-rate(M1)가
관건이다:

- **M1 재평가**: snapshot.data_versions 의 krx/dart batch_id 가 freshness 축. precompute
  배치가 KRX 일배치 **후** 실행되면(scheduler "all" 순서 …→dart→snapshot), as_of=T
  의 데이터가 EOD 확정된 뒤 precompute 된 snapshot 은 as_of=T 의 krx_batch_id 가
  pinned 되어 **그날 이후 as_of=T screen 이 일관 hit**. M1 의 "today-screen worst
  hit"는 intraday batch churn 을 가정한 worst-case 로, EOD-확정 cadence 에선 hit-rate
  가 양호하다(다음 측정 슬라이스에서 cadence 재현 검증 권장).
- 따라서 천장(높음) × hit-rate(cadence 적절 시 양호) → 실현 이득 유의미.

## Slice 1+ §2.10 제약 (구현 시 필수 — 사용자 Q3 "더 엄격한 규칙")

`result_codes` 는 market_overview(live observation)와 달리 **저장·byte-재현되는
artifact**(reproduce_run/save_run)다. 따라서:

1. **reproduce/save 무조건 bypass**: `batch_cutoff != None`(reproduce) 이면 snapshot
   미사용 — snapshot 은 cutoff 없이 live 계산되므로 frozen 재실행과 다름. save_run
   도 live 평가(저장 run = 사용자가 본 screen 의 §2.10 대칭). **live screen serve
   경로에만** read-bypass 적용.
2. **serve predicate(더 엄격)**: market_overview 의 data_versions equality + 추가
   가드(computed_at vs collect_batch_versions max started_at — out-of-order
   late-commit best-effort 차단; oracle 가 근본 차단 불가라 했으나 artifact 라
   best-effort 강화). custom pack 은 content_hash 불일치로 자동 live.
3. **byte-동일 논증**: snapshot serve == live evaluate(동일 FactorEvaluator·동일
   Decimal context + str(Decimal) round-trip — market_overview 에서 검증). hit 종목의
   result_codes 가 live 와 동일해야 §2.10 불변.
4. **회귀 0 fallback**: snapshot 미존재/stale/reproduce → live(기존 동작).
5. **wiring**: `_passes_all_conditions` 의 evaluate 호출부 분기(provider wrap 금지,
   oracle 권고). all-or-nothing per code(부분 serve 복잡도 회피).

## Slice 1b 구현 + 실현-이득 측정 (완료)

Slice 1b(read-bypass 구현) 완료(commit `f019f98`, oracle SHIP). plan `.sisyphus/
plans/speculum-screen-read-bypass.md` + e2e 가드(`test_db/test_screen_read_bypass_e2e.py`).

### Slice 1a — realized speedup 실측 (`scripts/bench_screen_serve.py`)

serve(hit) vs live(재평가) wall-time 실측 — financial-only(eps+roe) 하한:

| N | live | serve | realized speedup (hit 100%) |
|---|---|---|---|
| 200 | 20.4 ms | 1.2 ms | 94% |
| 500 | 50.0 ms | 5.8 ms | 88% |
| 1000 | 99.4 ms | 5.9 ms | 94% |
| 2500 | 252.8 ms | 16.6 ms | **93%** |

serve==live result_codes byte-동일(harness assert + e2e 테스트 보증).

**결론**: serve hit 시 실현 speedup **~90%** — Slice 0 천장(eval CPU ~90%)을 온전히
실현(read-bypass 가 평가를 정확히 skip). 실 production 이득 = ~90% × hit-rate.
hit-rate: precompute 가 KRX 일배치 후 실행되면 EOD-확정 as_of 의 krx_batch_id pinned
→ 그날 이후 as_of=T screen 일관 hit(분석). 따라서 read-bypass 의 실현 가치 확정 —
GO 정당화 완료. price 기반 factor screen 은 PriceAdjuster 로 serve 절감이 더 큼(상한).
