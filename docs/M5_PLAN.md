# M5_PLAN — 재현성·신뢰성 검증 layer (rev3, Momus 3라운드 반영)

> **rev3 변경(코드 실측)**: backtest 는 snapshot **영속화·export·repository 가
> 전무**(`backtest_runs` ORM 0, `backtest.py:213` 저장 0)하고 `FreezeOut`
> (`schemas/backtest.py:133-145`)이 재실행 입력(conditions·pack_slug/version·
> security_types·data_versions)을 **누락** → "screen reproduce 패턴 복제"는 재현
> *대상·입력·baseline* 부재로 바로 착수 불가. **#3a 를 3개 하위(#3a-0 입력 운반 ·
> #3a-1 baseline · #3a-2 cutoff 재실행)로 분해**. 또한 `CorporateActionRepository.
> fetch_actions`(`pit_protocols.py:576-588`)에 batch_cutoff 가 없어 corporate action
> 보정은 cutoff freeze 불가 → #3a 결정성을 "price/financial/treasury/market_cap
> 한정, corporate action 은 screen reproduce 와 동일 한계 상속"으로 정정(known limit).



> 마일스톤 plan / 작업 지시서. 상세 ADR(8기둥 경계·임계값)은 구현 착수 시
> **ADR-0033 (reproducibility-trust-verification)** 로 동반 작성.
>
> **rev2 변경(코드 실측 기반)**:
> - **전제 정정**: "신규 0" → "신규 *사용자 산출 기능* 0. 검증·게이트·표시·**재현
>   경로** 신규 코드는 허용(이것이 'layer' 의 정의)."
> - **#3 재구조화**: screen run 은 reproduce 응답이 이미 matches/note/result_hash 를
>   노출(`schemas/screen.py:204,222`·`runs.py`) → 실효는 pack-변조의 **구조화 필드
>   분리**. **backtest 는 reproduce 경로 자체가 부재**(`reproduce.py:239` 는 screen
>   전용) + `result_hash` 가 입력만 해시(`backtest_snapshot.py:158-174`, 결과 미해시)
>   → **backtest reproduce 경로 신설**이 M5 신뢰성의 핵심 격차.
> - **#1 정정**: SQL WHERE 가 이미 `effective_date <= as_of` 1차 강제 →
>   runtime assert 는 **반환 record list 사후 regression assert**(SQL 필터 대체 아님).
>   PIT 키 = `effective_date`(trade_date 아님, `pit_protocols.py:83`). distribution
>   경로 포함.
> - **#5 위상 정정**: 신규 출력 문자열은 #2/#3/#4/#6 가 생성한 뒤 존재 → #5 를
>   **#6 이후/병행** 검증으로 재배치.

## 0. 한 줄 정의 + 전제

M4 가 pack 을 **열린 포맷**으로 만들었다면, M5 는 그 위에서 산출되는 reproduce·
백테스트·PIT 결과가 **신뢰할 수 있음을 코드로 검증·고지**한다. 비전 §7 "백테스트
결과의 신뢰성이 PIT 데이터 layer 덕분에 환상이 아닌 것이 확인됨" 을 실증한다.

**전제(정정)**: M5 는 새로운 *사용자 산출 기능*(신규 factor·화면·지표)을 만들지
않는다. 그러나 **검증(verification)·게이트(gate)·표시(surface)·재현(reproduce)
경로의 신규 코드는 허용**한다 — 그것이 "신뢰성 layer" 의 실체다. 기존 측정·게이트
(`missing_price_ratio`·screen `reproduce.matches`·pack-hash 게이트·
`assert_no_lookahead`)를 **운영 경로에서 강제·노출**하고, 빠진 재현 경로(backtest)를
**기존 패턴 복제로 채운다**.

## 1. 스코프

### 포함 — deliverable #1~#6

| # | 항목 | 기둥 | 코드 현황(실측) | M5 가 추가 |
|---|------|------|----------------|-----------|
| 1 | **PIT look-ahead runtime 게이트** | §2.4 / §2.9 | SQL WHERE 1차 필터 O, `assert_no_lookahead` test-only | 반환 record 사후 regression assert(defense-in-depth) |
| 2 | **survivorship·비용 사실 고지** | §2.10 / §2.1 | `missing_price_ratio` 측정·응답 O, cost=0 명시노출 O(D2) | 사실 비율·비용가정 표시(판정 라벨 0) |
| 3 | **재현 무결성: backtest 경로 신설 + screen 노출 강화** | §2.10 / §2.8 | screen reproduce O(matches/note/hash 노출), **backtest reproduce 부재** | backtest reproduce 신설 + pack-변조 구조화 필드 |
| 4 | **데이터 신선도·정정 미반영 추적** | §2.1 | `diff_versions`(정책 diff만)·`batch_runs` O | 배치 실패 stale + 종목 supersede chain-diff(신규) |
| 5 | **No Advice 게이트의 신규 출력 경로 검증** | §2.2 / §2.8 | 성과 어휘 다수 기등록(`forbidden-words.json:55-91`) | #2~#4·#6 산출 문자열의 게이트 포함 검증 |
| 6 | **client 신뢰성 표시(중립 배지)** | §2.1 / §2.7 | freshness banner O | #2·#3·#4 신호의 grayscale 배지 |

### 유보 (M5 범위 외)

- **cross-instance run 재현** — batch_id 타 DB 부재(M4 유보 유지). 인프라.
- **운영 모니터링 외부 연동**(알림·대시보드) — 코드는 검증·플래그·로깅까지.
- **result_hash 입력 스키마 변경** — **절대 불변**(추가 시 기존 frozen run 전부 파손). screen·backtest 모두.
- **분기 공시 lag 정책 재정의** — 현 `effective_date` 모델 유지. M5 는 가용성 *검증*만.

### release blocker

- **신규 blocker 없음** — M5 는 §2.2 안전 영역(사실 고지·검증, 추천 0).
- 상위 blocker 유지: M3 자문(세무/변호사)·survivorship 실데이터 backfill 운영 cycle·M4 ADR-0006. M5 는 코드부 신뢰성 기반만 마련.

## 2. deliverable 위상정렬 (rev2 — #5 후행)

```
#1 PIT runtime assert ─┐
#2 survivorship/비용 고지 ─┤
#3 재현(backtest 신설+screen) ─┼─→ #6 client 표시 ─→ #5 게이트 적용 검증(신규 문자열 생성 후)
#4 신선도·정정 추적 ─┘                              (#6 산출 문자열을 #5 가 스캔)
```

- **#1·#2·#3·#4 병렬**: 각자 독립 server 검증/재현. 서로 의존 없음.
- **#3 내부 순서**: #3a-0(입력 운반 경로 신설) → #3a-1(baseline 정의) → #3a-2(cutoff 재실행·대조). #3b/#3c 는 #3a 와 독립.
- **#6**: server 신호(#2·#3·#4)를 use-시점 표시.
- **#5 후행**: 게이트가 검증할 신규 사용자 노출 문자열은 #2~#4·#6 가 만든 뒤에야 존재 → #6 산출 후(또는 병행 말미) 게이트 포함을 검증(rev1 의 선행 배치는 위상 역전이었음).

## 3. deliverable 상세 + task + acceptance

### #1 PIT look-ahead runtime 게이트 (§2.4 / §2.9)
- **현황(실측)**: SQL repository(`sql_repositories.py`)가 **이미 WHERE 절에서 `effective_date <= as_of` 1차 강제**. `assert_no_lookahead(records, as_of)`(`pit_enforcer.py`, `r.effective_date` 검사)는 **test 에서만** 호출. `db_field_provider` 는 scalar 만 반환(record 없음) → provider 에서는 게이트 불가.
- **task**:
  - T-M5-01a 게이트는 **fetch 반환 record list 에 대한 사후 regression assert**(SQL 1차 필터를 *대체*하지 않음 — defense-in-depth). 코드 변경으로 WHERE 회귀 시 fail-loud(`LookAheadError`). 대상·PIT 키(실측):
    - `fetch_financials` — `effective_date <= as_of` (+ supersede active-at-as_of).
    - `fetch_prices` — `effective_date`(= trade_date 의미, `pit_protocols.py:83`).
    - corporate action fetch — **`announced_date <= as_of` 축만**(공시 가용성). effective_date 는 권리락 등 **발효 예정일**이라 정상적으로 as_of 이후(미래) 가능 → assert 제외(거짓양성 방지·Critical). effective 적용 시점은 adjuster 책임. (구현 발견: pit_protocols.py:14 의 "effective_date<=as_of 이중 PIT" 는 fetch+adjust 전체 파이프라인 의미이며, `fetch_actions` 의 PIT 가용성 축은 announced 단일.)
    - market_cap / treasury fetch — 각 `effective_date`.
    - macro fetch — **이중 시간축**(`vintage_date`/`reference_date`) → 단순 effective_date 비교 불가, vintage 전용 assert.
  - T-M5-01b 적용 표면에 **universe distribution 경로**(`db_universe_distribution`, 모집단 각 종목 DbFieldProvider 경유)와 **backtest survivorship fetch**(`backtest_engine` 의 `fetch_prices(as_of, start=date.min)`)를 포함 — 둘 다 provider/repository 경유라 위 사후 assert 로 자동 커버되나, 회귀 표면으로 명시 테스트.
  - T-M5-01c 적대적 회귀: 누수 record 주입(Fake repo) → assert fail-loud. 경계(`<=` inclusive·tie-break by created_at) 정상 데이터 거짓양성 0.
- **acceptance**: 6종 fetch + distribution + backtest 가격 경로에서 누수 record 가 결과 도달 시 fail-loud. macro 이중 축 별도 검증. 정상 데이터 거짓양성 0(경계 회귀 테스트 green).
- **가드레일**: fail-loud. **거짓 양성이 운영 평가를 막는 가용성 사고**가 최대 리스크(§리스크 Critical) → 경계 정확성 load-bearing. assert 가 SQL 필터를 대체한다는 오구현 금지(둘 다 유지).

### #2 survivorship·비용 사실 고지 (§2.10 / §2.1) — **M3 ADR-0027 D2/D3 로 이미 완료**
- **현황(실측 — 구현 발견)**: server `missing_price_ratio`·`survivorship_complete` 측정·응답(`backtest_engine.py`) + cost_assumptions 명시 노출(D2, 0 입력 숨김 불가)뿐 아니라, **client 표시도 이미 구현됨** — `BacktestPanel.tsx:252-291` 가 survivorship_complete=false 시 경고(neutral 톤·red 금지)·missingRatio% + costAssumptions(commission/tax bps)를 항상 표시. `backtest-visual-gate.test.tsx` 가 판정 라벨 0·neutral 톤 검증.
- **판정**: T-M5-02a(사실 비율·판정 라벨 0)·T-M5-02b(비용 가정 표시)·T-M5-02c(회귀)가 **M3 에서 전부 충족됨 → M5 신규 작업 없음**. heuristic 한계("빈 가격=폐지 추정", 결손≠폐지)는 ADR-0033 에 문서화하고, 등급화(insufficient 라벨 등)는 **하지 않는다**(폐지≠결손 구분 불가 + §2.2/§2.7 — 사실 비율로 충분).
- **잔여(있다면)**: #6 표시 통합 시 survivorship/cost 배지의 forbidden-words 게이트 포함만 #5 에서 확인.

### #3 재현 무결성: backtest 경로 신설 + screen 노출 강화 (§2.10 / §2.8)
- **현황(실측 — rev2 핵심)**:
  - **screen run**: `reproduce_run`(`reproduce.py:239`)이 frozen pack/batch 재로드 + result_codes 재실행 비교(`matches`) + pack-hash 게이트(`reproduce.py:225`). `ReproduceOut`(`schemas/screen.py:204,222`·`runs.py`)이 **이미 matches·note·result_hash 노출**. → 잔여는 미세.
  - **backtest**: `BacktestSnapshot.result_hash` 는 **입력만 해시**(pack/batch_id/conditions/기간/비용/engine_version, `backtest_snapshot.py:158-174`) — equity_curve·CAGR **결과 미해시**. 그리고 **backtest reproduce 경로 자체가 없다**(grep `reproduce_backtest` 0). 게다가 `_adjusted_close_at`·`_select_portfolio` 가 `fetch_prices` 에 **batch_cutoff 미전달**(screen reproduce 는 cutoff 주입). → frozen backtest 를 재실행해 같은 결과가 나오는지 **검증 불가** = M5 신뢰성의 진짜 격차.
- **task** — backtest reproduce 는 **3개 선행 인프라가 부재**(snapshot 영속화·재실행 입력 운반·비교 baseline)하므로 하위 분해. "screen 패턴 복제"는 #3a-2 에만 적용:
  - **T-M5-03a-0 재현 입력 운반 경로 신설**: 현 backtest 는 `FreezeOut`(result_hash·pack_content_hash·batch_id·기간·비용만)만 응답하고 snapshot 을 저장하지 않는다. 둘 중 택1(ADR-0033 결정) — (A) **영속화**: `backtest_runs` ORM + repository(screen `screen_run_repository` 패턴) 신설, 또는 (B) **export 자기완결 JSON**(screen `ScreenRunSnapshotOut` 패턴). 어느 쪽이든 재실행 입력(conditions·pack_slug/version·security_types·data_versions 맵)을 `FreezeOut`/export 에 **추가**(현재 누락 — 재현 불가의 직접 원인).
  - **T-M5-03a-1 비교 baseline 정의**: 재실행 결과를 대조할 frozen 결과가 없다(equity_curve 는 result_hash 미포함·미영속). equity_curve(+통계)를 #3a-0 의 영속/export 에 **baseline 으로 포함**하되 **result_hash 입력에는 넣지 않는다**(입력 스키마 불변 — §1 유보). 즉 result_hash 는 입력 식별자로 유지하고, `matches` 는 별도 저장/round-trip 한 equity_curve 와 재실행 결과의 대조로 정의.
  - **T-M5-03a-2 cutoff 주입 재실행**: #3a-0 의 입력으로 (1) pack 재로드 + hash 게이트, (2) frozen batch_id → `BatchCutoff` 해소 후 **가격/재무 fetch 에 cutoff 주입**, (3) 재실행 equity_curve/통계 → #3a-1 baseline 과 대조(`matches`). screen `reproduce_run` 구조 복제.
  - T-M5-03b screen pack-변조 **구조화 bool 필드**: free-text `note` 에 fold 된 pack-hash mismatch 를 `pack_tampered: bool` 로 분리(client 명확 구분, #6). result_codes `matches` 와 구분.
  - T-M5-03c(Low) offline 저장 무결성 CLI `tools/verify_run.py`(thin wrapper — `_jcs`/`screen_run` 재사용): stored result_hash 가 stored inputs 재유도값과 일치. **부분 변조·직렬화 drift 만 탐지**(전체 행 동시 변조 불가 — 한계 명시).
  - T-M5-03d 회귀: backtest reproduce 정상 round-trip `matches=True`, 가격 변조 `matches=False`, screen pack 변조 `pack_tampered=True`.
- **acceptance**: backtest 가 #3a-0 입력 + #3a-1 baseline 으로 재실행돼 `matches` 산출. screen pack-변조 구조화 노출. **기존 frozen run/snapshot 의 result_hash 가 새 코드로 재유도해도 byte-동일**(입력 스키마 불변 — equity_curve 는 hash 밖).
- **가드레일**: §2.8 — 단일 `_jcs` recipe. **result_hash 입력 스키마 절대 불변**(equity_curve baseline 은 hash 밖 별도 저장). #3a 결정성은 **price/financial/treasury/market_cap cutoff 범위 한정** — **corporate action 보정은 `fetch_actions` 에 batch_cutoff 가 없어 freeze 불가**(screen reproduce 와 동일 상속 한계, known limit). #3c 탐지 범위(부분 변조) 과대 주장 금지.

### #4 데이터 신선도·정정 미반영 추적 (§2.1)
- **현황(실측)**: `diff_versions`(frozen vs current **정책/배치/pack diff 만**)·`assert_not_stale`(calendar) 존재. batch_runs 실패→stale 명시, 종목 supersede chain-diff 부재(`diff_versions` 재사용 불가 → 신규 로직).
- **task**:
  - T-M5-04a 신선도 진단: 최신 성공 `batch_runs`(krx/dart) 시각·실패 여부 → "데이터 기준일 + stale(임계 N일, ADR-0033)". 기존 footer disclaimer 소스.
  - T-M5-04b 정정 미반영 진단(신규 chain-diff): 입력 = frozen run result_codes 종목, 비교 = 해당 종목·회계기간의 현재 supersede chain 에 frozen 이후 successor 존재 여부, 산출 = "N건 정정 후행" 사실(종목 목록). **known limit**: 필터 탈락했으나 정정으로 편입될 종목(result_codes *밖*)은 탐지 못함(구조적 false-negative) — 명시.
- **acceptance**: 배치 실패 시 stale 신호, 정정 후행 종목 수 사실 노출, false-negative 한계 문서화.
- **가드레일**: 사실(시각·임계·건수)만.

### #5 No Advice 게이트의 신규 출력 경로 검증 (§2.2 / §2.8) — #6 후행
- **현황(실측)**: 성과·우열 어휘 다수 기등록(`forbidden-words.json`: "수익률 상위"·"검증된 전략"·"고수익 전략"·"아웃퍼폼/언더퍼폼"·en `Outperform/Underperform/Overweight/Underweight/Long/Short` 등). **주 작업은 어휘 추가가 아니다.**
- **task**:
  - T-M5-05a(주) **게이트 적용 범위 검증**: #2~#4·#6 가 생성한 신규 사용자 노출 문자열(survivorship 고지·재현 note·신선도 배지·i18n)이 `check_forbidden_words.py` 스캔 대상에 포함되고 CI green. SoT — 신규 문자열이 게이트 우회 0.
  - T-M5-05b(부) 실측 누락분만: 등록 목록 대조 후 진짜 빠진 후보를 **plan/ADR-0033 에서 확정**(예: "초과수익"·"승률" 한글 — "롱숏" 은 `Long/Short` 기등록이나 한글 substring 오탐 위험 사전 판정). 각 추가 후보의 allowed_phrases 충돌(CAGR·누적수익률·변동성·turnover 사실 라벨) 동반.
- **acceptance**: 신규 출력 문자열 전부 게이트 스캔 포함·CI green. 추가 어휘는 확정 목록 한정 + allowed_phrases 충돌 0.
- **가드레일**: 사실 통계 라벨 허용, 우열·행위 시사만 차단. substring 매치 오탐 → allowed_phrases 필수.

### #6 client 신뢰성 표시 — 중립 배지 (§2.1 / §2.7)
- **task**:
  - T-M5-06a 대상 컴포넌트(확정): **backtest 결과 화면**(survivorship 비율 #2·비용 가정 #2b·재현 matches #3a), **run/reproduce 결과 화면**(result_codes matches·`pack_tampered` #3b), **freshness banner 인접**(stale·정정 후행 #4). 기존 freshness banner 보강(중복 아님).
  - T-M5-06b grayscale 중립 배지(판단색 0·순위 0 — ADR-0028 D3) + i18n(한국어) + 컴포넌트 테스트(렌더·중립 톤·forbidden 0).
- **acceptance**: 세 화면에 사실 배지, 판단색·우열 어휘 0, i18n 누락 0.
- **가드레일**: §2.7 — 사실만. 해석("위험"·"부정확")·행위 유도 금지.

## 4. 8기둥·자본시장법 가드레일

- **§2.2 No Advice**: 모든 경고는 데이터 사실 고지(추천 아님). 판정 라벨 금지(#2). 성과 어휘 게이트가 신규 표면 전부 적용(#5).
- **§2.10 / §2.8**: hash 는 단일 `_jcs` 재사용. **result_hash 입력 스키마 절대 불변**(screen·backtest frozen 호환). backtest reproduce 는 screen 패턴 복제(새 정규화 금지).
- **§2.4 / §2.9**: runtime assert 는 effective_date 모델을 *강제*할 뿐 정책 불변(재현 호환). macro 이중 축 등 경로별 키 정확.
- **§2.1 Fidelity**: survivorship·비용·stale 비대칭 없이 모두 고지(#2b).
- **자본시장법**: 검증·고지는 유사투자자문 경계 무관. 신규 자문 blocker 없음.

## 5. Momus 검토 계획 (squash 직전)

전방위 적대적 검토. 중점:
- §2.10 — backtest reproduce 신설(#3a)이 cutoff 주입으로 결정적인가? screen·backtest result_hash 입력 불변(byte-동일) 유지? "변조 탐지" 과대 주장 0(#3c 한계 명시)?
- §2.4 — #1 assert 가 6종 fetch + distribution + backtest 가격 전 경로를 덮나, SQL 필터를 대체하지 않나(둘 다 유지)? macro 이중 축? 거짓 양성 0·hot-path 성능?
- §2.2 — #2 사실 비율(판정 라벨 0)? #5 성과 어휘가 사실 라벨 오탐 0?
- §2.8 — verify CLI thin wrapper? 신규 출력 문자열이 게이트에 전부 포함?

## 6. ADR

- **ADR-0033 (reproducibility-trust-verification)** 착수 시 작성 — runtime PIT 게이트 경로별 키·비용/정확성, survivorship 사실 고지 vs 등급화 경계(폐지≠결손), backtest reproduce 의 cutoff 주입 결정성, #3c 저장 무결성 한계, stale·정정 임계값, 성과 어휘 추가분·allowed_phrases 명문화.

## 리스크 등급

- **Critical**: #1 runtime assert **거짓 양성**(정상 데이터 누수 오판 → 운영 평가/백테스트 차단 = 가용성 사고). 경계(`<=` inclusive·tie-break·macro 이중 축) 정확성 load-bearing.
- **High**: #3a backtest reproduce 의 **3개 선행 인프라**(snapshot 영속화/export·재실행 입력 운반·equity_curve baseline)가 부재 → 가장 큰 신규 작업이자 #3 의 load-bearing. corporate action cutoff 부재(상속 한계, known limit). #1 일부 경로 누락 시 누수 구멍. #5 substring 오탐(백테스트 출력 파손).
- **Medium**: #2 heuristic(결손≠폐지)을 등급에 쓰면 오분류. #4 chain-diff 비용·false-negative. #3c 탐지 범위 과대 표현.
- **Low**: #6 배지 톤, #4 stale 임계 튜닝.

## known limit (문서화)

- cross-instance run 재현 불가(batch_id 타 DB 부재) — M4 유지.
- #3c 저장 무결성은 부분 변조·직렬화 drift 만 탐지(전체 행 동시 변조 불가) — 진짜 재현 보증은 `matches`(재실행).
- **#3a backtest reproduce 결정성은 price/financial/treasury/market_cap cutoff 범위 한정** — corporate action 보정은 `CorporateActionRepository.fetch_actions` 에 batch_cutoff 가 없어 freeze 불가(screen reproduce 와 동일 상속 한계). 재실행 시점 추가된 정정 corporate action 이 보정종가에 반영될 수 있음. 완전 freeze 는 별도 작업(범위 외).
- #4b 정정 미반영 진단은 result_codes *내* 종목 한정(필터 탈락 후 정정 편입 종목은 false-negative).
- result_hash 재검증은 on-demand/offline — 성능.
- 운영 알림/대시보드는 코드 범위 밖.

## 참조 (코드 근거)

- `server/app/services/screen_run.py:348-357` / `schemas/screen.py:204,222` — screen result_hash 입력 종속·ReproduceOut 노출(#3 근거).
- `server/app/services/reproduce.py:225,239,350` — pack-hash 게이트·screen 전용·matches(#3).
- `server/app/services/backtest_snapshot.py:158-174` — backtest result_hash 입력만 해시(결과 미해시, #3 근거).
- `server/app/services/backtest_engine.py:249,317,469` — 가격 fetch cutoff 미전달·survivorship heuristic·cost=0 명시노출.
- `server/app/repositories/pit_protocols.py:50,83` — effective_date PIT 키(#1 키 정정).
- `shared/forbidden-words.json:55-91` — 성과 어휘 기등록(#5 재정의).
- 재현 byte-동일 불변식 = ADR-0025 D5 / `docs/work-orders/m1-t48c-reproduction.md`(ADR-0020=append-only·ADR-0032=provenance 와 구분).

---

## 7. 구현 현황 체크리스트 (실측 검증 2026-06-18)

> 코드 적대적 검증. 범례: `[x]` 코드 확인 / `[~]` 부분·계획과 차이 / `[ ]` 코드 없음 / `[blocked]` 외부 의존.

### #1 PIT look-ahead runtime 게이트 (§2.4/§2.9)
- [x] `assert_no_lookahead`(+ macro 용 `assert_no_vintage_lookahead`) — `services/pit_enforcer.py:169,202`
- [x] **운영 serve 경로에 실제 배선**(test-only 아님) — `sql_repositories.py` prices/financials/market_cap/treasury/corporate_action(announced 축만)/macro(vintage) 6종 + `caching_repositories.py` serve-time assert
- [x] 적대적 회귀(stub 주입 fail-loud) — `tests/test_db/test_pit_runtime_gate_integration.py`
- [~] distribution 경로 — repo 경유로 transitive 커버(`test_distribution_input_is_pit`), 전용 stub-assert 테스트는 없음
- [x] backtest 가격 경로 — 동일 `SqlPriceRepository` 배선

### #2 survivorship·비용 사실 고지 (M3 상속)
- [x] server `missing_price_ratio`/`survivorship_complete`/`cost_assumptions`(숨김 불가) — `backtest_engine.py:187`
- [x] client survivorship 경고 + 비용 표시 + `backtest-visual-gate.test.tsx`

### #3 재현 무결성: backtest 신설 + screen 강화 (계획상 핵심 격차)
- [x] **`reproduce_backtest` 신설(존재·동작)** — `services/reproduce.py:495` + endpoint `api/routes/backtest.py:282`
- [x] FreezeOut 재현 입력 운반(conditions/data_versions/pack_slug/version/engine_version) — `schemas/backtest.py:135`
- [x] baseline equity_curve 별도 운반(result_hash 입력 불변) — `BacktestReproduceIn.equity_curve`
- [x] cutoff 주입 재실행(`_resolve_cutoff` krx/dart) — `reproduce.py:603`
- [x] `pack_tampered` bool 분리(backtest+screen) — `schemas/backtest.py:252`·`schemas/screen.py:432`
- [x] 회귀: round-trip matches=True / pack-hash mismatch matches=False — `test_backtest_engine.py:451,570`
- [ ] **T-M5-03c `tools/verify_run.py` offline CLI — 없음**(계획에 thin wrapper 로 명시됐으나 미구현)
- [~] screen 측 `pack_tampered=True` 산출 전용 적대적 테스트 미확인(필드·매핑은 존재)

### #4 데이터 신선도·정정 미반영 추적
- [x] 신선도(배치 실패→stale, KRX/DART/KOSIS 임계) — `services/data_freshness.py:113`
- [x] 정정 chain-diff(restatement_lag, financial-only known-limit) — `services/restatement_lag.py:72` + endpoint `runs.py:439` + 테스트

### #5 No Advice 게이트 신규 출력 경로 검증
- [x] `tools/check_forbidden_words.py` 가 신규 출력 문자열(packTampered·freshness i18n) 스캔(제외 목록에 없음)
- [~] 후보 어휘 "초과수익"/"승률" — **`shared/forbidden-words.json` 에 미추가**(계획상 ADR-0033 확정 후행 deferred). 기존 어휘로 신규 출력은 사실 문자열이라 위반 0

### #6 client 신뢰성 표시(중립 배지)
- [x] backtest matches/pack_tampered 배지 + DataFreshnessPanel(grayscale) + `reproduce-trust-gate.test.tsx` + i18n
- [~] screen run/reproduce 화면의 pack_tampered 배지 렌더 전용 테스트 미확인(매핑 `lib/api/runs.ts` 는 존재)

**요약(2026-06-18)**: 계획이 "핵심 격차"로 지목한 **backtest reproduce·PIT runtime 게이트는 완전 구현·테스트됨**. **실 갭 2건**: (1) `tools/verify_run.py` 미구현, (2) 후보 어휘 초과수익/승률 미추가(의도적 deferred). 부분: distribution 전용 PIT 테스트·screen pack_tampered 전용 테스트는 transitive 커버만.
