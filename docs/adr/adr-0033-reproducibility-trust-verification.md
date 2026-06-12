# ADR-0033: 재현성·신뢰성 검증 layer — runtime PIT 게이트 + backtest reproduce

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-06-04 |
| **Deciders** | 사용자 |
| **Related** | CONCEPT §7(비전 — 백테스트 신뢰성 PIT 실증)·§2.1(Fidelity)·§2.2(No Advice)·§2.4(PIT)·§2.7(Observation)·§2.8(Conformance)·§2.9(Temporal Continuity)·§2.10(Reproducibility), [[adr-0008-as-of-date]](PIT Enforcer)·[[adr-0025-pack-registry]](freeze·reproduce)·[[adr-0027-backtest-engine]](백테스트·survivorship)·[[adr-0009-corporate-action]] D5(이중 PIT)·[[adr-0032-open-format-pack-ecosystem]](self-contained export); `docs/M5_PLAN.md`(rev3, Momus 3라운드) |

## Context

ROADMAP §7 비전: "백테스트 결과의 신뢰성이 PIT 데이터 layer 덕분에 환상이 아닌
것이 확인됨". M3(ADR-0027 백테스트·ADR-0025 PackRegistry/reproduce)·M4(ADR-0032
open format)가 측정·재현 인프라를 이미 구축했으나, 신뢰성을 **코드로 강제·검증**
하는 layer 가 비어 있다. M5_PLAN(rev3, Momus 3라운드 검토)이 코드 실측으로 드러낸
격차:

- **PIT assert 가 test-only**: `PITEnforcer.assert_no_lookahead` 가 운영 평가
  경로에서 호출되지 않아, SQL WHERE 회귀가 조용히 look-ahead 를 통과시킬 수 있다.
- **backtest 는 reproduce 경로가 없다**: `reproduce.reproduce_run` 은 screen 전용.
  backtest `result_hash`(`backtest_snapshot.py`)는 **입력만 해시**(equity_curve 결과
  미해시)하고 snapshot 을 **영속화·export 하지 않으며**(`backtest_runs` ORM 0,
  `FreezeOut` 이 conditions·pack_slug·security_types·data_versions 누락), 가격 fetch
  에 batch_cutoff 를 주입하지 않는다 → frozen backtest 를 재실행해 같은 결과가
  나오는지 **검증 불가**. 이것이 M5 신뢰성의 진짜 격차.
- **survivorship·비용 고지는 이미 완료**(M3 ADR-0027 D2/D3, `BacktestPanel.tsx`).

## Decision

### D1. PIT look-ahead runtime 게이트 = fetch 반환 record 사후 regression assert
SQL repository fetch 의 반환 record 에 `assert_no_lookahead` 를 **사후 호출**한다
(SQL WHERE 1차 필터를 *대체하지 않는* defense-in-depth). PIT 키는 도메인별:
price/financial/treasury/market_cap = `effective_date`, **corporate action =
`announced_date` 단일**(effective_date 는 권리락 등 발효 예정일이라 정상적으로
as_of 이후 가능 — assert 제외), macro = `reference_date` **및** `vintage_date`
이중 축(`assert_no_vintage_lookahead`). **구현 완료(M5 #1).**

### D2. backtest reproduce 입력 운반 = **export 자기완결 JSON** (영속화 아님)
backtest snapshot 을 재현하기 위한 입력은 `backtest_runs` DB 영속화가 아니라
**self-contained export JSON**(screen `ScreenRunSnapshotOut`/export-reproduce
패턴 복제)으로 운반한다. export 는 재실행에 필요한 전 입력을 담는다:
conditions(canonical)·pack_slug/version·pack_content_hash·security_types·
data_versions 맵(krx/dart batch_id·정책 버전)·cost_assumptions·기간·rebalance.
client 가 이 JSON 을 보관하고 reproduce 요청 시 그대로 전송한다.

### D3. 비교 baseline = equity_curve(+통계), **result_hash 입력에는 미포함**
재실행 결과를 대조할 baseline(equity_curve·통계)을 D2 export 에 포함하되
**`result_hash` 입력 스키마에는 절대 넣지 않는다**(입력 불변 — 기존 frozen
snapshot 의 hash 가 byte-동일 유지, frozen run 재현 호환). `matches` = 재실행
equity_curve == export baseline.

### D4. cutoff 주입 범위 = price/financial/treasury/market_cap (corporate action 제외)
reproduce 재실행 시 frozen batch_id → `BatchCutoff` 해소 후 가격·재무·자사주·
시총 fetch 에 cutoff 주입(정정공시 freeze). **corporate action 보정은
`CorporateActionRepository.fetch_actions` 에 batch_cutoff 파라미터가 없어 freeze
불가** — screen reproduce 와 동일한 상속 한계(known limit). 완전 freeze 는 별도
작업(범위 외).

### D5. survivorship = 사실 비율만, 등급화 안 함
M3 ADR-0027 D2/D3 가 이미 missing_price_ratio·survivorship_complete 측정·표시
(neutral 톤·판정 라벨 0)·비용 가정 노출을 구현. M5 는 **신규 작업 없음**.
"insufficient/신뢰불가" 같은 **등급 라벨을 붙이지 않는다** — survivorship 측정은
"빈 가격 = 폐지 추정" heuristic 이라 결손(미수집·거래정지)과 폐지를 구분 못 하며,
사실 비율 고지로 충분(§2.7).

### D6. 데이터 신선도 임계 (stale 판정)
- KRX(가격·시총·종목): 최신 성공 batch 가 **3 영업일** 초과 경과 시 stale 신호.
- DART(재무): 분기 공시 주기상 **분기 + 60 일** 초과 시 stale 신호.
- 임계는 사실 비교(배치 시각 vs 임계)일 뿐 판정 아님 — 표시는 "데이터 기준일 +
  stale 여부" 사실. 정정 미반영 진단은 result_codes *내* 종목 한정(필터 탈락 후
  정정 편입 종목은 false-negative — known limit).

### D7. No Advice 게이트 = 신규 출력 문자열 포함 검증 (어휘 추가 아님)
성과·우열 어휘 다수가 `forbidden-words.json` 에 기등록. M5 의 주 작업은 #3/#4/#6
가 생성하는 **신규 사용자 노출 문자열이 게이트 스캔에 포함**되는지 검증(CI green).
실측 누락 어휘만 보강하되 사실 통계 라벨(CAGR·누적수익률·변동성·turnover) 오탐
방지를 위해 allowed_phrases 동반.

## Rationale

- **§2.10 Reproducibility**: D2 export 자기완결은 M4(ADR-0032) open-format 정신과
  일관 — pack 처럼 backtest 도 git/외부로 portable 한 self-identifying 산출물.
  D3 의 result_hash 입력 불변은 frozen run 재현 호환(ADR-0025 D5)을 보존.
- **§2.4/§2.9 PIT**: D1 사후 assert 는 effective_date 모델을 *강제*만 할 뿐 정책
  불변(재현 호환). corporate action 의 announced 단일 축은 거짓양성(정상 미래
  권리락 차단) 방지 — M5 Critical 리스크 회피.
- **§2.8 Conformance**: D2/D3 은 단일 `_jcs` recipe·screen reproduce 구조를 복제
  (새 정규화 SoT 금지). verify CLI 는 thin wrapper.
- **§2.2 No Advice**: D5 사실 비율(등급화 거부)·D7 성과 어휘 게이트 — 신뢰성 고지가
  투자 판단으로 미끄러지지 않게. 신규 자문 blocker 없음.

## Consequences

### Positive
- backtest 가 frozen export 로 재실행 검증(`matches`) 가능 — 비전 §7 실증.
- look-ahead 회귀가 운영 경로에서 fail-loud(§2.4 강제).
- DB 스키마 무변경(D2 export 택 — migration·운영 부담 0).

### Negative
- backtest export 보관은 client 책임(영속화 아님 — screen 과 동일 trade-off).
- corporate action 보정은 reproduce 시 완전 freeze 안 됨(D4 상속 한계).
- D1 사후 assert 가 hot-path 에 record 당 1 비교 추가(미미하나 전수).

### Neutral / Unknown
- D6 임계값(3 영업일·분기+60일→DART 100 calendar days 근사)은 운영 데이터로 재튜닝 가능.
- **D6 KRX 영업일 임계의 캘린더 범위 한계**: `business_days_between` 은 verified KRX
  캘린더(현재 단년 데이터) 범위 내에서만 영업일 수를 계산한다. batch 종료일/now 가
  범위 밖이면(운영 시각이 캘린더 갱신보다 앞서면) 영업일 산정 불가 → silent 추정
  대신 **calendar days(elapsed_days)로 보수 stale 판정**(§2.1 정직 — 범위 밖일 만큼
  오래됨은 사실). 운영 캘린더를 갱신하면 영업일 임계가 정확해진다(known limit).
- backtest reproduce 의 corporate action 완전 freeze 는 `fetch_actions` cutoff
  파라미터 추가가 선행돼야 하는 별도 마일스톤 후보.

## Alternatives Considered

- **A. backtest snapshot DB 영속화(`backtest_runs` 테이블 + repository)** — 거부.
  screen run 영속화 선례가 있으나, (1) migration·운영 테이블 증가, (2) M4 open
  format 정신은 self-contained export 지향, (3) reproduce 검증에 영속화가 필수
  아님(client round-trip 으로 충분). export 가 더 가볍고 portable.
- **B. survivorship 등급화(insufficient 라벨)** — 거부(D5). 폐지≠결손 구분 불가 +
  §2.2/§2.7 판정 라벨 위험. 사실 비율로 충분.
- **C. result_hash 입력에 equity_curve 추가(결과 해시)** — 거부(D3). 기존 frozen
  snapshot hash 전부 파손(입력 스키마 불변 위반, namespace 변경과 동일 위험).

## References
- `docs/M5_PLAN.md`(rev3) — deliverable #1~#6 + Momus 3라운드 검토.
- `server/app/services/pit_enforcer.py`(D1 구현), `backtest_snapshot.py`·
  `schemas/backtest.py`(D2/D3 대상), `reproduce.py`(screen 패턴 — D2 복제 원본).
