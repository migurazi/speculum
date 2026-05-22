# ADR-0009: Corporate Action 분류 + 보정 정책

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-05-22 |
| **Deciders** | 사용자 |
| **Related** | `docs/CONCEPT.md §2.9 Temporal Continuity`, `§2.6 KRX-Native`, `docs/M0_PLAN.md T9, T20`, [[adr-0001-price-adjustment]], [[adr-0002-factor-fact-model]], [[adr-0003-data-source-adapter]] |

## Context

[[adr-0001-price-adjustment]] 는 가격 시계열의 수정주가 정의에 집중. 본 ADR 은 **corporate action 전체** 의 분류·발효일·보정·UI 표시 정책 — Speculum 의 §2.9 Temporal Continuity 기둥의 implementation.

한국 시장의 corporate action 은 회계처리·법적 효력·시장 영향이 종류별로 다르고, DART 공시 양식·KRX 발표가 모두 source. 일관된 모델이 필요.

다루는 사건:
1. 액면분할 / 액면병합
2. 무상증자 (Stock Dividend / Bonus Issue)
3. 유상증자 (Rights Issue / Public Offering)
4. 주식배당
5. 현금배당
6. 자사주 매입 / 자사주 소각
7. 합병 (피합병 → 합병회사)
8. 분할 (인적·물적)
9. 액면가 변경 (분할·병합과 별도 — 액면가 자체 변경)
10. 종목코드 변경 (재상장, 합병 후 신규)
11. 정정공시

## Decision

### D1. Corporate Action 데이터 모델

`corporate_actions` 테이블은 모든 사건의 단일 입구.

```sql
CREATE TABLE corporate_actions (
  id              UUID PRIMARY KEY,
  code            TEXT NOT NULL,            -- 사건 대상 종목코드 (사건 발생 시점의 code)
  code_lineage_id UUID NOT NULL,            -- stocks_master 의 lineage (D6)
  action_type     TEXT NOT NULL,            -- enum, D2 참조
  announced_date  DATE NOT NULL,            -- 공시일 (DART rcept_dt)
  effective_date  DATE NOT NULL,            -- 권리락일 / 효력 발생일
  payment_date    DATE,                     -- 배당 지급일 (해당 시)
  ratio           NUMERIC,                  -- 분할비, 무상증자비 등
  subscription_price NUMERIC,               -- 유상증자 발행가
  cash_amount     NUMERIC,                  -- 현금배당 금액 (주당)
  details         JSONB NOT NULL,           -- 사건별 추가 정보 (D2 참조)
  citation_id     UUID NOT NULL REFERENCES source_citations(id),
  superseded_by   UUID,                     -- 정정공시 시 새 row 가 가리킴 (D5)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ca_effective ON corporate_actions(code_lineage_id, effective_date);
CREATE INDEX idx_ca_action_type ON corporate_actions(action_type, effective_date);
```

### D2. Action Type enum + 보정 정책

| action_type | 가격 보정 (`adjusted` 시계열) | 시가총액 영향 | 발행주식수 변경 | M0 처리 |
|---|---|---|---|---|
| `split` (액면분할) | 과거가격 / 분할비 | 변경 없음 (보정 후) | ✓ 증가 | ✓ |
| `reverse_split` (액면병합) | 과거가격 × 병합비 | 변경 없음 | ✓ 감소 | ✓ |
| `bonus_issue` (무상증자) | 과거가격 / (1 + 비율) | 변경 없음 | ✓ 증가 | ✓ |
| `stock_dividend` (주식배당) | 무상증자와 동일 | 변경 없음 | ✓ 증가 | ✓ |
| `rights_issue` (유상증자) | 권리락 이론가 비율 ([[adr-0001-price-adjustment]] D2) | 증가 | ✓ 증가 | ✓ |
| `cash_dividend` (현금배당) | **보정 안 함** ([[adr-0001-price-adjustment]] D1) | 감소 (배당락) | 변경 없음 | ✓ 정보만 |
| `treasury_purchase` (자사주 매입) | 보정 안 함 | 자사주 증가 → ex_treasury 시총 감소 | 변경 없음 (자사주 분리) | ✓ |
| `treasury_cancellation` (자사주 소각) | 보정 안 함 | 변경 없음 | ✓ 감소 (총 발행) | ✓ |
| `merger` (합병 — 피합병) | 종목 자체 변경, 상장폐지 처리 | N/A | N/A | ✓ |
| `merger_absorption` (합병 — 흡수) | 발행주식수 증가 | 증가 | ✓ 증가 | M1 |
| `spin_off_personal` (인적분할) | 비례 분리 | 분리 | 종목 lineage | M1 |
| `spin_off_business` (물적분할) | 변경 없음 (자회사가 됨) | 변경 없음 | 변경 없음 | M1 |
| `par_value_change` (액면가 변경) | 분할/병합과 별도 — 보정 X (액면가는 표시 정보) | 변경 없음 | 변경 없음 | ✓ |
| `code_change` (종목코드 변경) | 가격 X — lineage 만 | 변경 없음 | 변경 없음 | ✓ |

`details` JSONB 구조 예 (D2 의 action_type 별):

```json
// split
{ "before_face_value": 5000, "after_face_value": 100, "split_ratio": 50 }

// rights_issue
{ "subscription_ratio": 0.2,
  "subscription_price": 15000,
  "theoretical_ex_price": 28500,
  "previous_close": 30000,
  "shares_issued": 10000000 }

// cash_dividend
{ "per_share": 1500, "type": "interim" | "annual" | "quarterly" }

// merger
{ "merging_into_code": "005930",
  "exchange_ratio": 0.5,
  "delisting_date": "2026-08-30" }
```

### D3. effective_date 의 의미

| Action | effective_date 정의 |
|---|---|
| split / reverse_split / bonus_issue / stock_dividend | **권리락일** (KRX 발표) — 이 날 시초가부터 보정 적용 |
| rights_issue | 권리락일 |
| cash_dividend | **배당락일** (KRX 발표) — 보정은 안 하지만 배당 추적용 |
| treasury_purchase | 매입 완료 공시일 또는 분기말 시점 (DART 공시 패턴) |
| treasury_cancellation | 소각일 (등기 효력일) |
| merger / merger_absorption | 합병기일 |
| code_change | 변경 효력일 |

권리락 / 배당락 일자는 **KRX 공식 발표** 가 1차 자료. DART 공시는 사전 공시. KRX 공식 일자가 다르면 KRX 우선.

### D4. UI 표시 — 차트 + 카드

#### D4.1 차트 (Stock Detail / Compare)

```
Adjusted 모드 (default):
  - corporate action 일자에 보정 적용 → 차트 매끄러움
  - 차트 하단에 작은 ▾ 마커 (월별 corporate action 집계)
  - hover → "2025-03-15 액면분할 (50:1)" 정보

Raw 모드:
  - corporate action 일자에 점프 visible
  - 점프 일자에 시각적 표시 (수직 점선 + 라벨)
  - "이 점프는 액면분할에 의한 것입니다" tooltip
```

#### D4.2 Stock Detail "Corporate Actions" 섹션

종목별 corporate action history table:

```
일자          종류          비율/금액        발효일        DART 공시
2025-03-01   액면분할       50:1            2025-03-15   →  [원문]
2025-05-12   현금배당       주당 1,500원    2025-05-14   →  [원문]
2025-08-30   자사주 매입    100억원         2025-08-30 ~ 2025-11-30  →  [원문]
```

### D5. 정정공시 처리

DART 정정공시 발생 시:

```
1. 새 corporate_action row insert (정정된 내용)
2. 옛 row 의 `superseded_by` = 새 row.id
3. 옛 row 는 historical 으로 영구 보존 (DELETE 금지)
4. 쿼리 default = active (superseded_by IS NULL) row 만
5. "정정 history" UI 옵션 → superseded row 도 표시
```

§2.10 Reproducibility — Screen Run snapshot 시점에 어느 row 가 active 였는지 freeze.

### D6. 종목 lineage (`code_lineage_id`)

`stocks_master` 의 row 자체는 **종목 lineage 단위**. 종목코드 변경·재상장·합병 시 새 row 아닌 history append:

```sql
CREATE TABLE stocks_master (
  id              UUID PRIMARY KEY,        -- lineage ID, 영구 보존
  current_code    TEXT,                    -- 현재 활성 코드 (NULL = 폐지)
  current_name    TEXT,
  market          TEXT,                    -- 'KOSPI' | 'KOSDAQ' | 'KONEX'
  listing_date    DATE NOT NULL,
  delisting_date  DATE,
  fiscal_month    INTEGER NOT NULL DEFAULT 12,
  code_history    JSONB NOT NULL,          -- [{code, valid_from, valid_to, reason}, ...]
  ifrs_preference_default TEXT DEFAULT 'AUTO',
  ...
);
```

`code_history` JSONB 예:

```json
[
  { "code": "035420", "valid_from": "1999-11-30", "valid_to": null, "reason": "initial_listing" },
  { "code": "035422", "valid_from": "2000-05-01", "valid_to": "2000-12-31", "reason": "merger_temporary" }
]
```

### D7. 합병·분할 — M1 본격

M0 에서는 이벤트 detect + 데이터 보존만 (보정·시계열 통합은 M1):

- **피합병 종목**: 합병 효력일에 자동 `delisting_date` 기록. 시계열은 그대로 보존 (survivorship bias 방지).
- **흡수합병한 종목**: 발행주식수 history 갱신. 가격은 자연스럽게 시장이 반영 → 별도 보정 없음.
- **인적분할**: 두 종목의 lineage 연결 (M1). M0 는 단순 새 종목 listing 으로 처리.
- **물적분할**: 자회사가 됨 — 모회사 시계열에 직접 영향 없음.

### D8. 보정 정책 자체의 버저닝

[[adr-0001-price-adjustment]] D5 의 정책 버저닝 확장 — 본 ADR 의 D2 보정 매트릭스 전체가 `corporate-action-policy v1.0` 의 일부:

```
corporate-action-policy v1.0
  - 위 D2 매트릭스 그대로
  - effective_date 정의: KRX 공식 우선 (D3)
  - 정정공시: superseded_by chain (D5)
  - 적용 시점: 2026-08-01 ~ (M0 release)
```

정책 변경 시 새 버전. Factor 계산은 (adj_policy, ca_policy) 두 버전의 결합 hash → Screen Run snapshot freeze 입력.

### D9. 누락 / 미감지 사건 처리

DART / KRX 공시를 일배치에서 100% catch 못 할 가능성:

- **알 수 없는 가격 점프** (>15% in 1 day) 감지 → `data_quality_alerts` 테이블 + Sentry alert
- 사용자에게 알림: 해당 일자 차트에 ⚠ 표시 + "확인 필요" tooltip
- 운영자 수동 검토 후 corporate_action row 추가 또는 "noise" 분류

### D10. 한국 시장 특수 케이스

| 케이스 | 처리 |
|---|---|
| 우선주 → 보통주 전환 청구 | 우선주 발행주식수 감소 + 보통주 증가. 두 종목의 corporate_action 각각 발생 |
| 신주인수권부사채(BW) 행사 | rights_issue 와 유사하게 처리. M2 (M0~M1 은 발행주식수 변화만 catch) |
| 전환사채(CB) 전환 | 위와 동일 |
| ETF 분배금 | ETF 별도 모듈 (M2) 에서 처리. M0 에서 ETF 제외 universe (CONCEPT §7.1) |
| 거래정지 / 관리종목 지정 | corporate_action 이 아닌 stock_status 시계열 (D11) |

### D11. 종목 상태 시계열 (`stock_status`) — corporate action 과 별도

Metis 검토 A3 항목 — 종목 상태는 시간에 따라 변함:

```sql
CREATE TABLE stock_status (
  code_lineage_id UUID NOT NULL REFERENCES stocks_master(id),
  effective_date  DATE NOT NULL,
  status          TEXT NOT NULL,    -- 'normal' | 'caution' | 'warning' | 'risk' | 'managed' | 'trading_halt' | 'liquidation_trading' | 'delisted'
  reason          TEXT,
  citation_id     UUID NOT NULL REFERENCES source_citations(id),
  PRIMARY KEY (code_lineage_id, effective_date)
);
```

historical 스크리닝 시 `WHERE effective_date <= as_of` + 최신 row 를 적용 → look-ahead bias 방지 (§2.4 PIT).

## Rationale

1. **§2.9 Temporal Continuity 의 단단한 implementation** — 모든 corporate action 이 일급 entity. UI 가 의식 가능.
2. **§2.4 PIT** — 정정공시 chain + 종목 상태 시계열이 historical 쿼리의 정확성 보장.
3. **§2.10 Reproducibility** — 정책 버저닝 + freeze.
4. **§2.6 KRX-Native** — KRX 공식 일자 우선, K-IFRS 회사별 결산기 일급.
5. **자매 프로젝트 결** — Norma 의 calibration 4 모드 + 분기별 fallback 패턴이 본 ADR 의 corporate action type별 보정 매트릭스와 동형.

## Consequences

### Positive
- **모든 corporate action 추적** — UI 가 raw/adjusted 양쪽 모드에서 명시.
- **정정공시 chain** — historical 데이터의 무결성.
- **종목 lineage** — 합병·재상장·종목코드 변경 모두 영구 보존.
- **종목 상태 시계열** — survivorship bias / look-ahead bias 동시 방어.
- **정책 자체의 버저닝** — Screen Run snapshot 의 결정성.

### Negative
- **DART/KRX 파싱 부담** — corporate action 공시는 양식이 다양 (분할 공시 / 무상증자 결정 / 합병결의 등). dart_account_mapper 외에 `dart_ca_classifier` 모듈 추가 필요 (T20).
- **합병·분할의 M1 미루기** — M0 사용자는 합병한 종목의 historical 시계열에서 끊김 경험 가능. UI 에 명시.
- **자동 매뉴얼 검토 필요** — 알 수 없는 가격 점프 처리는 운영자 개입 필요 (D9). M0 사용자 ~10 명 수준에서는 감당 가능.
- **스키마 복잡 증가** — corporate_actions / stock_status / stocks_master.code_history 3 entity. 그러나 §2.6 의 요구사항.

### Neutral / Unknown
- **DART 공시 lag** — 일부 corporate action 의 공시·KRX 발표 간 lag. effective_date 의 정확성 검증 필요 (T20 의 fixture 테스트).
- **인적분할의 자동 lineage 연결** — M1. 한국 사례 다양성으로 자동화 어려운 부분 있을 수 있음 — manual 검증 자리.

## Alternatives Considered

- **A. 가격 시계열에 직접 보정만, corporate_actions 테이블 없음** — 단순. 그러나 UI 가 "왜 보정?" 표시 불가, 정정공시 처리 불가. **거부**.
- **B. 모든 사건을 corporate_actions 한 테이블에 합치지 않고 종류별 별도 테이블** — schema 깔끔하나 query 복잡. 단일 엔트리 + JSONB details 가 균형. **거부**.
- **C. 종목 lineage 없이 종목 변경 시 새 row** — 단순. 그러나 합병·재상장 종목의 historical 시계열 끊김 → 8 기둥 §2.6 + §2.9 위반. **거부**.
- **D. stock_status 를 stocks_master 의 단일 컬럼으로 (Metis A3 의 anti-pattern)** — historical screening 시 look-ahead bias. **거부**.

## References

- [한국거래소 — 권리락·배당락 가격 산출 기준](http://data.krx.co.kr/)
- [Open DART — 주요사항보고서 양식](https://opendart.fss.or.kr/guide/main.do) — 분할·무상증자·합병 공시 양식
- [자본시장법 — 합병·분할·자기주식 규정](https://www.law.go.kr/lsInfoP.do?lsiSeq=105908)
- [[adr-0001-price-adjustment]] — 가격 보정 정책 (D2 의 split/bonus/rights 보정식 일치)
- [[adr-0002-factor-fact-model]] D4 — 정책 버저닝 패턴
- Norma `docs/CONCEPT.md §2.5` Calibration 4 모드 — corporate action type 별 보정 매트릭스의 동형
- Norma `docs/CONCEPT.md §2.2` Definition / Instance 분리 — corporate_action (event) vs stocks_master (lineage) 의 동형
