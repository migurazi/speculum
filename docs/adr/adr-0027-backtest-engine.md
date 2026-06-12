# ADR-0027: 백테스트 모듈 — PIT rebalance 순회 + freeze + 출력 게이트

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-06-02 |
| **Deciders** | 사용자 |
| **Related** | CONCEPT §2.4(PIT)·§2.10(Reproducibility)·§2.2(No Advice)·§2.1(Fidelity)·§7.3(survivorship), [[adr-0025-pack-registry]] D5(재현 불변식 — 시계열 확장), [[adr-0024-asset-class-statistical-limits]](사실+한계 디스클로저 게이트), [[adr-0009-corporate-action]] D7(survivorship 보존), [[adr-0007-default-ui-rules]] D2(등락색)·D8.1(디스클레이머 게이트); oracle 분석(2026-06-02) M3_PLAN #1 |

## Context

백테스트는 PIT 데이터 layer 의 첫 대량 소비자다. `pit_enforcer`(stateless, 임의 as_of 반복),
`price_adjuster`(read-time 수정주가), `factor_evaluator`+`DbFieldProvider`(as_of 주입 종목별 평가),
`ScreenRunSnapshot` freeze 가 모두 즉시 재사용 가능하다. 그러나 두 위험:

1. **survivorship(§7.3)**: `stocks_master.delisting_date` 보존 + `list_active(as_of)` 로 과거 시점
   universe 복원하는 **설계는 완비**(ADR-0009 D7). 그러나 서비스 개시 이전 폐지 종목의 과거
   OHLCV **소급 backfill 은 운영적으로 미충족**(`krx_daily.fetch_universe` 는 현재 활성 종목만 수집).
   소급 universe 에서 이미 폐지된 종목이 누락되면 survivorship bias 로 결과가 환상.
2. **§2.2 No Advice**: "수익률 N%" headline·pack 간 자동 순위·등락색이 "이 pack 이 좋다" 추천 변질 통로.

## Decision

### D1. Backtest engine — as_of rebalance 그리드 순회
`server/app/services/backtest_engine.py`. 입력: pack(PackRegistry resolve), rebalance 주기
(분기말/월말), 기간[start, end], universe 정의. 각 rebalance 시점 t 마다:
- `list_active(as_of=t)` + `security_type=="common"` 필터 → t 시점 universe.
- 각 종목 `DbFieldProvider(code, as_of=t)` + `factor_evaluator` → pack conditions 통과 종목 = 포트폴리오.
- **동일가중**(equal-weight) — 가중 최적화는 "최적 전략 제안"(§2.2 위반)이라 금지. 사실 계산만.
- t→t+1 보유수익률 = 수정주가(`price_adjuster.adjust(as_of=t+1)`) 변화. 폐지 종목은 폐지일까지.
- 거래비용(D2) 차감 후 누적.
**stateless engine — 외부 부수효과 0, 동일 입력 byte 동일 출력**(재현성).

### D2. 거래비용 default 강제 노출
거래비용 0 백테스트는 환상 자체. 매매회전 시 **증권거래세(매도) + 위탁수수료** default 가정을
강제 적용·노출(§2.1). 사용자가 비율 조정 가능하나 0 으로 숨길 수 없음. 적용 비율을 결과에 명시.

### D3. survivorship 디스클로저 — fail-loud (ADR-0024 패턴)
engine 은 `list_active(as_of=t)` 로 PIT-correct universe 를 구성하되, **그 시점 universe 의 종목 중
가격 시계열이 DB 에 부재한 비율(=backfill 누락 추정)을 측정**해 `survivorship_complete: bool` +
`missing_price_ratio` 를 결과에 포함. 누락이 있으면 결과 표면에 **"survivorship bias 가능 — 소급
폐지 종목 데이터 미완비"** 경고를 강제 표시(디스클레이머 게이트, ADR-0024 소표본 디스클로저 동형).
**숨기지 않고 정직하게 한계 노출** — §2.1 Fidelity.

### D4. 출력 게이트 (ADR-0025 D6 직계 확장)
백테스트 결과 표시:
- **grayscale equity curve + 중립 통계표만**. 금지: (a) "수익률 N%" headline 강조, (b) 등락색
  (녹수익/적손실, ADR-0007 D2), (c) pack 간 자동 순위/정렬 default, (d) "최고 성과 pack" 큐레이션.
- **과거성과 디스클레이머 게이트**: "과거 성과는 미래 수익을 보장하지 않으며 거래비용·세금·슬리피지·
  체결 가능성을 단순화한 가정"을 게이트로 부착 — disclaimer 없는 백테스트 렌더 차단(ADR-0007 D8.1).
- `backtest-visual-gate.test.tsx` 로 강제(`chart-visual-gate`/`custom-screen-visual-gate` 동형).
- forbidden-words: 성과 맥락 KO collocation("수익률 상위"/"아웃퍼폼"/"검증된 전략") 추가(ADR-0007 D9.1).

### D5. freeze artifact — ADR-0025 D5 시계열 확장
백테스트 결과는 `ScreenRunSnapshot` 동형 freeze: **pack content_hash + krx/dart batch_id +
rebalance 정책 버전 + 거래비용 가정 + 기간/주기 + result_hash(SHA-256 JCS)**. frozen artifact 로
재현(`reproduce` 동형). frozen pack 으로만 재로드(현재 active pack 사용 금지, ADR-0025 D5 불변식).

### D6. 결과 = 사실 통계만, 평가 0
CAGR·누적수익률·MDD·변동성·turnover 등 **표준 사실 통계만 중립 표기**. "우수"/"양호" 등 평가
라벨 0, 등급 0, 별점 0. 통계는 산출식·기간·가정 hover 명시(§2.1).

## Rationale
- 인프라 80% 재사용 — engine 은 기존 PIT/evaluate/adjust/freeze 를 시점 순회로 조립. 신규 발명 최소.
- survivorship 을 숨기지 않고 측정·노출(D3)하는 것이 §2.1 정직. backfill 완비 전에도 "한계 있는 사실"로 제공 가능.
- 추천 변질(§2.2)은 D4 출력 게이트 + D6 평가 0 으로 닫음. 기존 visual-gate 패턴 직계.

## Consequences
- **Positive**: AC-M2-F-02 성과 차원 완성. PIT 재현 인프라 실전 검증. Pack community(#3) 성과 차원 선행.
- **Negative**: survivorship backfill 미완 시 결과에 항상 디스클로저(데이터 운영 cycle 까지). engine 복잡도.
- **Neutral**: 가중 최적화/리스크 패리티 등 "전략 제안"은 영구 범위 외(§2.2). 동일가중 고정.

## Alternatives Considered
- **A. survivorship backfill 완료 후 engine** — 외부 데이터 조달(코드 범위 밖)·장기. 기각 — D3 디스클로저로 "한계 있는 사실" 선제공.
- **B. 거래비용 0 옵션 허용** — 환상 자체. 기각(D2 강제 노출).
- **C. pack 간 성과 비교 순위 위젯** — §2.2/§2.3 정면 위반. 기각(D4-c).

## References
- CONCEPT §2.4/§2.10/§2.2/§2.1/§7.3, ADR-0025 D5, ADR-0024, ADR-0009 D7, ADR-0007 D2/D8.1/D9.1
- 단계: engine(서비스) → freeze schema → route → client(`Backtest/` + visual-gate) → forbidden-words 확장
- **release blocker**: survivorship 소급 backfill(별도 운영 cycle) — 완비 전 D3 디스클로저 필수.
