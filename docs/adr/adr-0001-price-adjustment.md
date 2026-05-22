# ADR-0001: 가격 보정 정책 (수정주가 정의)

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-05-22 |
| **Deciders** | 사용자 |
| **Related** | `docs/CONCEPT.md §2.9 Temporal Continuity`, `docs/M0_PLAN.md T1`, [[adr-0009-corporate-action]] (예정), [[adr-0003-data-source-adapter]] (예정) |

## Context

한국 시장에는 가격 시계열을 끊는 사건이 다양하다. 같은 종목의 같은 날짜라도 도구마다 "수정주가" 값이 다르다 — 보정 정책이 다르기 때문.

| 도구 | 수정주가 정의 (관찰) |
|---|---|
| 네이버 금융 차트 | 권리락(액면분할·무상증자·주식배당)만 보정. 현금배당 미포함 |
| pykrx (`get_market_ohlcv(..., adjusted=True)`) | KRX 공식 — 권리락만 보정 |
| FinanceDataReader | 권리락 보정. 현금배당 별도 (FDR 자체 알고리즘) |
| Investing.com | 권리락 보정. 일부 시계열에서 현금배당 포함 (불일치) |
| Yahoo Finance 한국주식 | Total return 기반 (현금배당 포함) |

이로 인해 같은 "삼성전자 2020-01-02 종가" 가 도구마다 다르다 → 8 기둥 §2.1 Fidelity (왜곡 안 함) 와 §2.10 Reproducibility 직격.

해결해야 하는 결정:
1. 우리의 default 수정주가 정의 — 권리락만 vs total return
2. 유상증자의 권리락 보정 방식
3. Adapter 간 충돌 시 출처 우선순위
4. 보정 정책 자체의 버저닝 (corporate action 보정 정책이 바뀌면 모든 historical 가격이 바뀜 — Reproducibility 위반)

## Decision

### D1. 기본 수정주가 = "권리락만 보정, 현금배당 미포함"

라벨: `adjusted-rights-only` (이하 줄여서 `adjusted`)

**보정 대상 corporate action** (시계열을 곱셈비로 보정):

| Action | 보정 |
|---|---|
| 액면분할 (예: 1주 → 50주) | 과거 가격 / 분할비 |
| 액면병합 | 과거 가격 × 병합비 |
| 무상증자 | 과거 가격 / (1 + 무상증자비율) |
| 주식배당 | 과거 가격 / (1 + 주식배당비율) |
| 유상증자 (이론가격 — D2 참조) | 과거 가격 × (이론가 / 권리락 직전 종가) |

**보정 미대상**:
- **현금배당** — total return 측정 시 별도 추가
- **자사주 매입·소각** — 유통주식수만 영향, 단일 주가는 영향 없음
- **합병·분할 (회사 자체)** — 다른 종목이 됨 → 종목 lineage history (§2.6) 로 처리

### D2. 유상증자 = 이론가격 기준 보정

권리락 이론가:

```
P_theoretical = (P_prev * 기존주식수 + P_subscription * 신주발행수) / (기존주식수 + 신주발행수)
```

- `P_prev`: 권리락 직전일 종가
- `P_subscription`: 신주 발행가
- 보정비 = `P_theoretical / P_prev` (1.0 이하)
- 모든 과거 가격에 보정비 곱

이는 한국거래소 공식 권리락 가격 산출 방식과 동일. pykrx 의 default 동작과 일치.

**왜 이론가격인가:**
- 무상증자·주식배당의 보정과 일관된 logic.
- 실제 권리락 시초가는 시장 sentiment 가 반영된 결과 — 보정 logic 으로는 부적절.
- pykrx 와 정합 → §2.5 Open Data Sufficiency 가 단단.

### D3. Adapter 간 충돌 시 = pykrx 우선

가격 시계열의 1차 자료 우선순위:

```
1순위 (canonical)  pykrx.get_market_ohlcv(adjusted=True)
                   — KRX 공식 wrapper, 권리락만 보정
2순위 (verify)     FinanceDataReader.DataReader
                   — 교차검증용. 충돌 시 alert
3순위 (보강)       DART corporate_action 공시
                   — 보정비 계산을 위한 원자료
```

**충돌 처리:**
- 일배치에서 pykrx 값과 FDR 값이 `|diff| / max > 0.1%` 차이 → `data_quality_alerts` 테이블에 기록 + Sentry alert
- 사용자에게는 pykrx 값을 표시 + UI 에 작은 ⚠ 아이콘 ("출처 충돌 감지, pykrx 기준 표시. 자세히 보기 →")
- 자동 fall-back 금지 — 사용자에게 항상 visible

### D4. Raw + Adjusted 둘 다 저장

`prices_daily` 테이블에 두 컬럼 모두 보존:

```sql
CREATE TABLE prices_daily (
  code             TEXT NOT NULL,
  date             DATE NOT NULL,
  -- raw (보정 안 됨, 그날의 실제 거래 가격)
  open_raw         NUMERIC NOT NULL,
  high_raw         NUMERIC NOT NULL,
  low_raw          NUMERIC NOT NULL,
  close_raw        NUMERIC NOT NULL,
  volume           BIGINT  NOT NULL,
  -- adjusted (D1, D2 정책 v1.0 적용)
  open_adjusted    NUMERIC NOT NULL,
  high_adjusted    NUMERIC NOT NULL,
  low_adjusted     NUMERIC NOT NULL,
  close_adjusted   NUMERIC NOT NULL,
  adj_policy       TEXT NOT NULL,   -- "v1.0:rights-only,theoretical"
  citation_id      UUID NOT NULL REFERENCES source_citations(id),
  PRIMARY KEY (code, date)
)
PARTITION BY RANGE (date);
```

`adj_policy` 컬럼이 정책 버전을 보존 — D5 의 핵심.

### D5. 보정 정책의 버저닝

보정 정책은 그 자체로 versioned artifact (Norma 의 Run snapshot freeze 패턴, [[adr-0002-factor-fact-model]] D4 와 동형):

```
price-adjustment-policy v1.0   sha256:...
  - rights-only (현금배당 미포함)
  - 유상증자 = 이론가격
  - 액면병합·자사주는 보정 미대상
  - 적용 시점: 2026-08-01 ~ (M0 release)
```

**원칙:**
- 보정 정책이 변경되면 (예: 현금배당 포함하기로 결정) → 새 정책 버전 발행 (`v2.0`), 모든 가격을 새로 계산 후 별도 컬럼/테이블에 저장. 옛 정책의 값은 영구 보존.
- Factor 계산은 `as_of` + `adj_policy` 의 결합으로만 결정적 — Screen Run snapshot (§2.10) freeze 입력.

### D6. UI 표시 — 사용자 visible 토글

기본 = `adjusted` (D1). 사용자가 토글 가능:

```
[Adjusted ▾]   ←  default
  • Raw                    — 그날의 실제 거래 가격
  • Adjusted (default)    — 권리락만 보정 (현금배당 미포함)
  • Adjusted + Dividend   — total return (M2+ — 별도 ADR)
```

- 차트 hover tooltip 에 "Adjusted: v1.0 rights-only" 표기 (§2.1 Fidelity).
- "Raw" 토글 시 corporate action 일자에 시각적 표시 (▾ 마커).

## Rationale

1. **사용자 친숙도** — 네이버 금융·pykrx·KRX 공식이 모두 `rights-only` — 같은 기본값으로 두면 사용자 가 다른 도구의 PER·차트와 일관된 값을 본다. §2.5 Open Data Sufficiency 가 단단.
2. **§2.1 Fidelity** — `raw` 보존 + `adj_policy` 버전 명시 → 어떤 보정이 적용되었는지 항상 추적 가능.
3. **§2.9 Temporal Continuity** — 코어 약속. 보정 정책 자체가 일급 entity.
4. **§2.10 Reproducibility** — Factor 계산 hash 입력에 `adj_policy` 포함 → 정책 변경이 silent regression 안 됨.
5. **Total return 의 학술적 정확성 vs 사용자 혼란** — 장기 시계열 평가에서는 total return 이 정답이지만, 일상 PER·차트 비교에서 사용자가 다른 도구와 다른 숫자를 보면 신뢰 손상. M2+ 에서 "Total Return" 토글로 별도 추가 (D6).
6. **유상증자 이론가** — KRX 공식 = pykrx default. 우리 추가 logic 없이 1 차 자료 그대로 사용 (§2.8 Conformance).
7. **pykrx 우선** — KRX 공식 wrapper. §2.8 Conformance 의 implementation.

## Consequences

### Positive
- **네이버/pykrx 와 같은 PER·차트 값** — 사용자 인지 부담 최소.
- **`raw + adjusted` 양쪽 보존** — 어떤 분석에도 대응. 정책 변경 시에도 raw 는 영원히 유효.
- **`adj_policy` 버전 freeze** — Screen Run snapshot 의 결정성 (§2.10).
- **pykrx-canonical** — 충돌 처리가 명확.
- **유상증자 이론가** — KRX 공식 logic 그대로 → 별도 검증 부담 적음.

### Negative
- **현금배당 분석 제한** — total return 비교는 M2+ 까지 대기.
- **저장량 ~2배** — raw + adjusted 동시 보존 (BIGINT volume 제외 8 컬럼). PG partition 으로 흡수 가능.
- **충돌 alert 처리 부담** — 매일 일배치에서 pykrx vs FDR 비교 + 충돌 alert 정의. 운영 부담.
- **Adapter 가 v1.0 정책에 종속** — pykrx 가 미래에 보정 logic 을 바꾸면 우리 정책과 어긋날 수 있음. `adapter_version` 추적 (ADR-0002 의 Source Citation) 으로 감지.

### Neutral / Unknown
- **pykrx 의 유상증자 보정이 항상 정확한가** — pykrx 가 모든 유상증자 corporate action 을 cover 하는지 검증 필요 (T15 task).
- **Stock dividend 와 무상증자의 한국 시장 정의 차이** — 한국에선 "주식배당" 과 "무상증자" 가 회계 처리는 다르지만 가격 보정 효과는 동일. 동일 보정 logic 적용.

## Alternatives Considered

- **A. Total return default** — Yahoo Finance 한국주식 스타일. 학술적 정확성 ↑ 이지만 다른 도구와 PER 값이 달라 사용자 혼란. M2+ 옵션으로 유보. **거부**.
- **B. 보정 안 함 (raw only)** — 단순하지만 권리락 일자에 차트가 점프 → §2.9 Temporal Continuity 직격 위반. **거부**.
- **C. FDR 우선** — Tiingo·Yahoo 같은 멀티 소스 교차검증 이점 있으나 외부 의존 ↑ + KRX 공식과 거리. **거부**.
- **D. 사용자가 매번 선택** — 능동적 탐색 (§2.3) 의 극단. 그러나 default 가 없으면 사용성 ↓. D6 에서 토글만 노출. **거부**.
- **E. Adjusted 만 저장 (raw 미보존)** — 저장 절약. 그러나 정책 변경 시 historical 가격이 영구 손실. **거부**.

## References

- [pykrx GitHub — get_market_ohlcv 의 adjusted 옵션](https://github.com/sharebook-kr/pykrx)
- [KRX 정보데이터시스템 — 권리락 가격 산출](http://data.krx.co.kr/) — 유상증자 이론가 산식
- [FinanceDataReader GitHub](https://github.com/FinanceData/FinanceDataReader) — 보정 알고리즘 소스 코드 참조
- 네이버 금융 차트 — 사용자 친숙도 ground truth
- [[adr-0002-factor-fact-model]] D4 — Pack 버저닝 패턴과 동형
- Norma `docs/CONCEPT.md §2.5 Calibration` — "원본 + 조정 파라미터" 비파괴 원칙 동형
