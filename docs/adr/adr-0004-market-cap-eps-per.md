# ADR-0004: 시가총액·EPS·PER 산출 정의

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-05-22 |
| **Deciders** | 사용자 |
| **Related** | `docs/CONCEPT.md §2.1 Fidelity`, `§2.8 Conformance (multi-id)`, `docs/M0_PLAN.md T4, T22`, [[adr-0002-factor-fact-model]], [[adr-0005-ifrs-consolidated]] (예정) |

## Context

같은 "PER" 이라도 산출 정의가 도구마다 다르다. 가장 빈번한 충돌 원인 4 가지:

1. **시가총액 = 발행주식 × 가격? 자사주 차감?**
   - KRX 공식: 발행주식수 × 종가 (자사주 미차감)
   - 다수 학술 정의: (발행주식수 − 자사주) × 종가
   - 일부 도구: 우선주 별도 분리 후 보통주만

2. **EPS = 기본 EPS vs 희석 EPS?**
   - 한국 회사 다수: 두 값을 모두 공시. 큰 차이 없는 경우 많으나 전환사채·BW 보유 회사는 격차 큼.

3. **PER 분자 = 시가총액, 분모 = ?**
   - Trailing 12M: 최근 4 분기 net income 합 (TTM)
   - 최근 연간 (단일 사업보고서) — KRX 공식 PER 의 일부
   - Forward consensus — M3+

4. **ROE = net income / equity. equity 시점은?**
   - 기말 자본
   - 기초·기말 평균 자본 (학술 표준)
   - 평균이 더 정확하나 default 가 도구마다 다름

[[adr-0002-factor-fact-model]] §2.8 의 multi-id 정책은 "정의가 다르면 다른 canonical_id". 본 ADR 은 M0 빌트인 pack 에 들어갈 구체 정의 ~30 개를 확정.

## Decision

### D1. 자사주 처리 — `ex_treasury` default

시가총액의 default 정의:

```
market_cap_ex_treasury = (issued_shares - treasury_shares) * close_price_adjusted
market_cap_krx_official = issued_shares * close_price_adjusted
```

**default = `ex_treasury`** (자사주 차감).

**Rationale:**
- 자사주는 의결권 없음, 시장에 유통되지 않음 → 실효 시가총액은 차감이 정확.
- 학술적 표준 (Damodaran 등).
- PER 분모와 정합 — net income 도 자사주에 배당·EPS 귀속 안 됨, 분자도 자사주 제외가 일관.

**둘 다 빌트인으로 제공** (§2.8 multi-id):

| Factor canonical_id | 정의 |
|---|---|
| `market-cap:ex-treasury` (default) | (발행주식수 − 자사주) × 종가 |
| `market-cap:krx-official` | 발행주식수 × 종가 (자사주 포함) |

### D2. EPS — 기본 EPS default

```
basic_eps    = net_income_attributable_to_common / weighted_avg_basic_shares
diluted_eps  = net_income_attributable_to_common / weighted_avg_diluted_shares
```

**default = `basic`** (기본 EPS).

**Rationale:**
- 한국 회사 다수가 전환사채·BW 가 미미 → 기본 ≈ 희석. 일관된 default 로 충분.
- 도구·뉴스의 default 와 정합 (네이버 금융, KRX 공식).
- Diluted 가 더 보수적이지만, 차이가 큰 회사는 사용자가 토글로 확인.

둘 다 빌트인:

| canonical_id | 정의 |
|---|---|
| `eps:basic-ttm-consolidated-ifrs` (default) | 최근 4 분기 기본 EPS 합 |
| `eps:diluted-ttm-consolidated-ifrs` | 최근 4 분기 희석 EPS 합 |
| `eps:basic-annual-consolidated-ifrs` | 최근 사업연도 기본 EPS |
| `eps:basic-ttm-separate-ifrs` | 별도재무제표 기준 |

### D3. PER 분모 — TTM (최근 4 분기 합) default

```
per_ttm = market_cap_ex_treasury / sum(net_income_attributable_to_common_last_4q_cfs)
```

**default = TTM (Trailing 12 Months)**.

**Rationale:**
- 사업보고서만으로는 1 년에 1 번 갱신 → 분기 보고서 활용해야 신선도 ↑.
- 단, 분기 net income 의 계절성 (예: 4Q 만 큰 비중인 산업) 주의 필요 — TTM 이 이 문제 흡수.
- 결손 회사의 PER 은 음수 → UI 에 "N/A (적자)" 표시 (절대값·역수 변환 금지 — Fidelity).

빌트인:

| canonical_id | 분자 | 분모 |
|---|---|---|
| `per:ttm-consolidated-ifrs` (default) | market_cap_ex_treasury | TTM 연결 NI |
| `per:annual-consolidated-ifrs` | market_cap_ex_treasury | 최근 사업연도 연결 NI |
| `per:krx-official` | market_cap_krx_official | KRX 가 사용하는 정의 |
| `per:ttm-separate-ifrs` | market_cap_ex_treasury | TTM 별도 NI |

### D4. PBR — 시가총액 / 자기자본

```
pbr_consolidated = market_cap_ex_treasury / equity_attributable_to_common_period_end
```

- 분모 = 지배기업소유주지분 자본 (자사주 차감 후)
- 시점 = 최근 분기말 자기자본 (period-end). 평균이 아닌 시점값 (시가총액도 시점값).

빌트인:

| canonical_id | 정의 |
|---|---|
| `pbr:consolidated-ifrs` (default) | market_cap_ex_treasury / equity_attr_common_q_end_cfs |
| `pbr:separate-ifrs` | 별도 |
| `pbr:krx-official` | KRX 공식 정의 |

### D5. ROE — 평균 자기자본 default

```
roe = ni_attributable_to_common_ttm / avg(equity_attr_common_q_begin, equity_attr_common_q_end)
```

**default = 평균 자기자본** (학술 표준).

**Rationale:**
- 신주 발행·자사주 매입 등으로 자본이 기중 크게 변하면 기말 자본만으로는 왜곡.
- 평균이 더 정확하나 계산이 약간 복잡 — adapter 가 자동.

빌트인:

| canonical_id | 분자 | 분모 |
|---|---|---|
| `roe:ttm-avg-equity-consolidated-ifrs` (default) | TTM 연결 NI | (분기초 + 분기말) / 2 연결 자본 |
| `roe:annual-end-equity-consolidated-ifrs` | 연간 NI | 기말 자본 |
| `roe:ttm-end-equity-consolidated-ifrs` | TTM NI | 기말 자본 |

### D6. M0 빌트인 Factor list (~30)

`server/builtin-packs/factors/speculum-builtin-v1.0.0.json` 의 factor:

**Valuation (8)**:
- `per:ttm-consolidated-ifrs`, `per:annual-consolidated-ifrs`, `per:krx-official`, `per:ttm-separate-ifrs`
- `pbr:consolidated-ifrs`, `pbr:krx-official`
- `psr:ttm-consolidated-ifrs`
- `ev-ebitda:ttm-consolidated-ifrs`

**Profitability (6)**:
- `roe:ttm-avg-equity-consolidated-ifrs`, `roe:annual-end-equity-consolidated-ifrs`
- `roa:ttm-avg-assets-consolidated-ifrs`
- `operating-margin:ttm-consolidated-ifrs`
- `net-margin:ttm-consolidated-ifrs`
- `gross-margin:ttm-consolidated-ifrs`

**Growth (4)**:
- `revenue-growth:yoy-quarterly-consolidated-ifrs`
- `operating-income-growth:yoy-quarterly-consolidated-ifrs`
- `net-income-growth:yoy-quarterly-consolidated-ifrs`
- `eps-growth:yoy-annual-consolidated-ifrs`

**Financial Health (3)**:
- `debt-ratio:consolidated-ifrs`
- `current-ratio:consolidated-ifrs`
- `interest-coverage:ttm-consolidated-ifrs`

**Market (5)**:
- `market-cap:ex-treasury`, `market-cap:krx-official`
- `volume-turnover:20d-avg`
- `price-relative-52w-high`, `price-relative-52w-low`

**Technical (4)**:
- `ma:5d`, `ma:20d`, `ma:60d`, `ma:120d`
- `rsi:14d`
- (선택) `macd:12-26-9`

**Dividend (2)**:
- `dividend-yield:trailing-annual`
- `payout-ratio:trailing-annual-consolidated-ifrs`

→ 누계 **약 32 개**. 한 사용자가 보고 싶을 핵심을 거의 다 cover.

### D7. UI 표시 정책

- Stock Detail / Compare 화면에 default factor (`*:ex-treasury`, `*:ttm-consolidated-ifrs`) 가 카드로 노출.
- 카드 우상단에 ⓘ 호버 → Factor canonical_id + 산출식 + Source Citation 7 필드 표시 (§2.1 Fidelity).
- 같은 종류의 factor 토글 (예: PER 의 TTM ↔ Annual ↔ KRX 공식) — Stock Detail 의 "Variants" 메뉴.

### D8. 결손 회사 / 분모 0 / 분모 음수 처리

| 상황 | 표시 |
|---|---|
| NI < 0 (적자) | `N/A (적자)` — PER 음수 표시 금지 |
| Equity < 0 (자본잠식) | `N/A (자본잠식)` — PBR 표시 금지 |
| 분모 ≈ 0 | `N/A (분모 미달)` |
| Trailing 4 분기 미달 (신규 상장) | `N/A (데이터 부족, {n}/4Q)` |

UI 에 명시적 N/A — 0 으로 fall-back 금지 (§2.1 Fidelity).

## Rationale

1. **§2.8 Conformance multi-id** — 같은 "PER" 이 정의 차이로 다른 값이면 다른 ID. KRX 공식 PER 도 함께 빌트인.
2. **§2.1 Fidelity** — 결손 회사의 PER 같은 무의미한 값을 0 또는 절대값으로 대체 금지. N/A 명시.
3. **자사주 차감 default** — 학술 표준 + 자사주의 의결권·배당권 없음 → 실효 시가총액 정의.
4. **TTM default** — 분기 보고서 활용으로 신선도 ↑. 계절성은 TTM 자체로 흡수.
5. **평균 자기자본 default** — 신주 발행·자사주 매입의 영향 정확 반영.
6. **두 정의 모두 빌트인** — 사용자가 KRX 공식 값과 우리 값이 다른 이유를 즉시 확인.

## Consequences

### Positive
- **명확한 default** — 한 화면에 같은 종목의 PER 값이 1 개 (혼란 0).
- **Multi-id 로 변형 비교 가능** — KRX 공식 vs 우리 vs 별도재무제표 vs 연간.
- **결손 회사 N/A 처리** — Fidelity 의 직접적 implementation.
- **TTM + 평균 자본 사용** — 학술적·실무적 정합.

### Negative
- **계산 부담** — ROE 의 평균 자본은 분기마다 자본 데이터 필요 → DART 가 분기 자본 공시.
- **UI 복잡** — 같은 PER 의 4 종 variant 를 사용자가 인지해야 함. 도움말·tooltip 필요.
- **자사주 데이터 의존** — KRX 의 자사주 발표값을 매일 갱신 필요. pykrx 가 cover 하는지 검증 (T15).

### Neutral / Unknown
- **TTM 계산 시 분기 결산기 비표준 회사** — 12 월 결산 대부분이지만 3·6·9 월 결산 회사 존재. [[adr-0005-ifrs-consolidated]] 와 함께 추후 fiscal_month aware 계산.
- **EV/EBITDA 의 EV 정의** — Enterprise Value = 시가총액 + 순부채. 순부채 산식 회사별 차이 있을 수 있음. M0 에 단순 정의 사용, 정밀화는 M2+.

## Alternatives Considered

- **A. KRX 공식 PER 만 default** — 사용자 친숙. 그러나 자사주 미차감 + 단일 사업연도 만이라 신선도 ↓ + 학술 표준과 거리. **거부**.
- **B. 희석 EPS default** — 보수적. 그러나 한국 회사 다수의 기본·희석 차이 미미, 도구·뉴스 default 와 거리. **거부**.
- **C. Multi-id 없이 단일 PER** — 단순. 그러나 8 기둥 §2.8 Conformance 위반. **거부**.
- **D. 빌트인 ~10 개만, 나머지는 v0.2** — MVP 가벼움. 그러나 Screener 가 의미를 가지려면 valuation + profitability + growth 가 동시에 필요. ~30 개가 적정. **거부**.
- **E. 외부 도구 (FnGuide) 값을 그대로** — Open Data Sufficiency 위반. **거부**.

## References

- 자본시장과 금융투자업에 관한 법률 — 발행주식수·자사주 정의
- 한국채택국제회계기준 (K-IFRS) 1033 — 주당이익 (Earnings per Share)
- 한국채택국제회계기준 1110 — 연결재무제표
- Damodaran, A. — Investment Valuation, Wiley — equity value 의 자사주 차감 default 학술 표준
- [KRX 정보데이터시스템 — PER/PBR 산출식](http://data.krx.co.kr/) — KRX 공식 정의
- [[adr-0002-factor-fact-model]] §2.8 multi-id 정책
- Norma `docs/CONCEPT.md §2.3 보강 §4 dual-definition landmark` — 같은 이름 다른 정의 분리의 동형
