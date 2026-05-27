# ADR-0008: As-of Date Picker — 일급 UI Element

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-05-22 |
| **Deciders** | 사용자 |
| **Related** | `docs/CONCEPT.md §2.4 PIT`, `§2.10 Reproducibility`, `docs/M0_PLAN.md T8, T21, T34`, [[adr-0002-factor-fact-model]], [[adr-0009-corporate-action]] |

## Context

CONCEPT §2.4 (Point-in-Time Correctness) 의 약속 — "과거 시점 분석 시, 그 시점에 알 수 없던 데이터를 사용하지 않는다" — 가 실현되려면 모든 데이터 표시·필터 화면이 **as-of date** 를 인식해야 한다.

Metis 검토 B1, G6 의 핵심 발견:
- "이 PER 은 언제 데이터인가?" 가 사용자에게 항상 명확해야 함 — §2.1 Fidelity
- "as-of 2024-10-01 에 PER<10 종목" 이 그 시점에 진짜 가능했는지 검증 가능해야 함 — §2.4 PIT

본 ADR 은 As-of date 의 UI / state / API 레이어를 결정.

## Decision

### D1. As-of Date 는 전역 state — 모든 화면이 의식

#### D1.1 Header 영역의 globl picker

```
┌──────────────────────────────────────────────────────────────┐
│  Speculum  [🔍 종목 검색]               📅 2026-05-22 ▾  👤  │
│                                                              │
│  Screener  Stocks  Compare  Watchlist                       │
└──────────────────────────────────────────────────────────────┘
```

- Header 우측 상단에 As-of picker. 항상 보임.
- 기본값 = **가장 최근 영업일** (오늘이 영업일이면 오늘, 아니면 직전 영업일)
- 클릭 시 calendar UI — 영업일만 선택 가능 (휴장일은 grayed out)
- 변경 시 모든 화면의 데이터가 reload

#### D1.2 Zustand global store

```typescript
// client/state/as-of-store.ts
type AsOfState = {
  asOf: Date;                       // 항상 영업일
  isToday: boolean;
  setAsOf: (date: Date) => void;
};
```

- localStorage 에 last selected 보존 (세션 간 일관)
- 단, **로그인 직후 default 는 항상 최근 영업일 (저장된 값 무시)** — 사용자가 옛 날짜에서 working 하다가 sign-in 한 후 confusion 방지

### D2. 영업일 처리 — KRX 캘린더 의존

- KRX 영업일 캘린더 (M0_PLAN T17) 가 영구 진실 source.
- pykrx + KRX 공식 발표 + 수동 검증.
- Picker 가 휴장일 선택 시도 → 가장 가까운 이전 영업일로 snap (사용자에게 toast 알림).
- Half-day (반장) 도 영업일로 처리. 단, M2 의 장중-장후 구분은 별도 ADR.

### D3. As-of Date 의 영향 범위

#### D3.1 데이터에 영향

| 화면 / 데이터 | as-of 영향 |
|---|---|
| Stock Detail — 가격 차트 | as-of 까지 표시 (이후는 잘림) |
| Stock Detail — 지표 카드 | as-of 기준 latest 분기/일 |
| Stock Detail — 재무 시계열 | as-of 시점의 `effective_date` 까지 |
| Screener 조건 | 모든 조건이 as-of 기준 ([[adr-0002-factor-fact-model]] D5 의 `stock_snapshots`) |
| Compare | as-of 기준 표시 |
| Watchlist (관심 종목 리스트) | as-of 시점에 active 인 종목 (상폐 제외) |
| Market Overview (M1) | as-of 일자의 지수·거래대금 등 |

#### D3.2 데이터에 영향 안 함

| 데이터 | 이유 |
|---|---|
| 사용자 자신의 Watchlist 정의 | 사용자가 어제 추가한 종목이 어제 데이터로 보이는 것이 자연 — Watchlist 는 사용자 의도 |
| 조건셋 (저장된 Screener query) | 정의 자체. 결과만 as-of 영향 |
| 사용자 설정 (Settings) | as-of 무관 |
| Factor Definition (Pack) | immutable, version 으로 freeze |

### D4. UI Affordance — 사용자가 as-of 를 명확히 인지

#### D4.1 "Today" vs "Past" 시각 차이

- as-of 가 오늘이면 picker = 회색 텍스트 "📅 오늘 (2026-05-22)"
- as-of 가 과거이면 picker = 강조 색상 "📅 과거 시점 (2024-10-01) ▾" + 작은 ⏰ 아이콘
- 모든 page 의 헤더 아래에 작은 banner: "*과거 시점 분석 — 2024-10-01 기준 데이터 표시 중. 그 시점 이후의 데이터는 사용되지 않습니다.*"

#### D4.2 모든 metric card 옆에 `as_of` 표시

```
[ PER (TTM, 연결) ]
  12.3
  ⓘ 2026-05-22 기준  source: DART rcept_no=20260515...
```

§2.1 Fidelity 의 implementation.

### D5. PIT Enforcer Service — Backend 강제

[[adr-0002-factor-fact-model]] D5 의 `stock_snapshots` 조회는 항상 as-of 기준.

```python
# server/app/services/pit_enforcer.py
class PITEnforcer:
    def filter_factors(self, query, as_of: date):
        return query.where(
            StockSnapshot.as_of_date <= as_of
        )

    def filter_financials(self, query, as_of: date):
        return query.where(
            Financial.effective_date <= as_of   # ADR-0002 D3 의 effective_date
        ).order_by(
            Financial.effective_date.desc()
        )
```

**Raw query path 차단 (CI 게이트)** — repository 가 PITEnforcer 우회 시 빌드 실패. T21 의 핵심.

### D6. API contract — `as_of` query param 의무

```
GET  /api/screen?conditions=...&as_of=2024-10-01
GET  /api/stocks/005930?as_of=2024-10-01
GET  /api/compare?codes=005930,000660&as_of=2024-10-01
```

- `as_of` 누락 시 서버가 today (영업일) 채움 + response header `X-AsOf-Defaulted: true` 로 알림.
- `as_of` 가 미래 날짜이면 400 error.
- `as_of` 가 영업일 아니면 가장 가까운 이전 영업일로 snap + warning.

### D7. Screen Run snapshot 의 as_of freeze

§2.10 Reproducibility 의 직접 implementation. ADR-0002 D5 의 `screen_runs` schema (M0 자리만):

```sql
CREATE TABLE screen_runs (
  id           UUID PRIMARY KEY,
  user_id      UUID NOT NULL,
  query        JSONB NOT NULL,
  as_of        DATE NOT NULL,            -- freeze
  result_codes TEXT[] NOT NULL,
  result_hash  TEXT NOT NULL,            -- SHA-256 of (query, as_of, result_codes, data_versions) — D7-bis
  data_versions JSONB NOT NULL,           -- T30 의 정책 hash + version aggregator + M1 KRX/DART batch_id
  computed_at  TIMESTAMPTZ NOT NULL,
  ...
);
```

같은 query 라도 as_of 가 다르면 다른 Run hash.

#### D7-bis. `result_hash` 입력 확장 (T30 보완, 2026-05-27)

원안 (위 schema 의 코멘트가 명시했던) "SHA-256 of (query, as_of, result_codes)" 는
§2.10 Reproducibility 와 모순. factor_pack v1.0 → v1.0.1 (오타 보정) 변경이 같은
query/as_of/result_codes 라도 산출 logic 이 달라졌는데 `result_hash` 가 동일 →
6 개월 후 재현 시 다른 결과 가능성 silent.

**확장 정의 (T30 으로 implementation)**:
```
result_hash = JCS-SHA256({
  "query": canonical_query,           # 정렬·dedup 후 conditions + selected_factors
  "as_of": ISO 8601 date,
  "result_codes": sorted 6-digit codes,
  "data_versions": {…}                # snapshot_versions.collect_active_policy_versions()
})
```

`data_versions` 의 freeze 가 hash 의 self-contained witness. 외부 export (M2
community pack 의 JSON 직렬화) 시 row 컬럼이 사라져도 hash 만으로 모든 정책
context 가 재현 가능. ADR-0002 D4 의 "Pack hash" 패턴과 동형.

`snapshot_versions.collect_active_policy_versions()` 의 키 set (snapshot schema
v1.0 — 11 키): `factor_pack_content_hash`, `factor_pack_slug`, `factor_pack_version`,
`calendar_content_hash`, `calendar_version`, `pit_policy_version`,
`price_adjustment_policy_hash`, `adjustment_policy_version`, `ca_policy_version`,
`evaluator_version`, `snapshot_schema_version`.

M1 추가 예정: `krx_batch_id`, `dart_batch_id` (데이터 정정 추적).

### D8. M0 한계 — 일 단위 PIT

- DART 의 정확한 공시 시각 (장중 vs 장후) 까지는 M0 에서 처리 X. 일 단위 PIT 만.
- 한국 시장의 공시 시점 데이터 (장중 vs 종가 후) 까지 정밀히 보려면 M2+ ADR.

M0 PIT 의 명시적 한계 (CONCEPT §2.4 의 limitation 과 일관):

> "Speculum 의 PIT 는 일 단위 (effective_date). 분 단위 공시 시점 까지의 정밀도는 보장하지 않음."

### D9. Watchlist 와 as-of 의 상호작용

- Watchlist 의 종목 list 자체는 사용자가 추가한 시점에 fix. as-of 무관.
- 그러나 종목별 표시 데이터 (PER, 가격 등) 는 as-of 기준.
- 상장폐지된 종목이 as-of 이후라면 Watchlist 에 표시되 데이터가 N/A — UI 가 "이 종목은 {date} 에 상장폐지되었습니다" 명시.

## Rationale

1. **§2.4 PIT 의 implementation** — UI / state / API / DB layer 일관.
2. **§2.1 Fidelity** — 모든 값이 어느 시점인지 명시.
3. **§2.10 Reproducibility** — Screen Run snapshot 의 freeze key.
4. **§2.3 Active Inspection** — 사용자가 의도적으로 과거 시점에서 working — 능동적 시간 탐색.
5. **Metis B1, G6 발견 대응** — "이 PER 은 언제 데이터인가" 가 항상 visible.

## Consequences

### Positive
- **PIT 약속이 시스템 전반에 박힘** — backdating · look-ahead bias 의 silent regression 차단.
- **헤더의 globl picker** — 사용자가 항상 인지.
- **API contract** — 외부 통합 시에도 PIT 강제.
- **CI 게이트** — Repository 가 PITEnforcer 우회 시 차단.

### Negative
- **모든 API 에 as_of param 의무** — boilerplate 증가. 그러나 contract 명확.
- **사용자 인지 부담** — "왜 picker 가 항상 있나?" 도움말 필요.
- **localStorage default 안 함** — 첫 진입 시 항상 today 로 reset. 약간의 사용성 비용이지만 confusion 방지 이점.
- **장중 vs 장후 정밀도 미지원** — M0 한계 명시 (D8).

### Neutral / Unknown
- **다국어 — Picker 의 일자 포맷** — 한국어 "2026년 5월 22일 (목)" 표시 vs ISO. M0 한국어 우선.
- **Calendar widget 의 KRX 휴장일 표시** — shadcn/ui Calendar + custom disabled dates. 구현 단순.

## Alternatives Considered

- **A. As-of picker 없음 — 항상 today** — 단순. 그러나 §2.4 PIT 가 사실상 미구현. **거부**.
- **B. 페이지별 picker (전역 아님)** — Stock Detail 에만 picker 두기. 그러나 Screener·Compare 의 as-of 부재 → 일관성 깨짐. **거부**.
- **C. Picker default = localStorage last** — 사용성 ↑ 그러나 사용자가 옛 날짜 사용 후 다음 세션 confusion. **거부**.
- **D. As_of 없는 API + repository 가 self-PIT** — 단순. 그러나 외부 통합 시 PIT 우회 가능. **거부**.

## References

- [[adr-0002-factor-fact-model]] D3 (Source Citation effective_date) — 본 ADR 의 backbone
- [[adr-0009-corporate-action]] D5 (정정공시 chain) — as_of 시점의 정정 history
- Metis 검토 B1 — 공시 lag, B3 — 자사주·시가총액 모호성, G6 — freshness visibility
- Norma `docs/CONCEPT.md §2.14` Run snapshot freeze 정책표 — as_of 가 본 ADR 의 freeze key 와 동형
- [shadcn/ui Calendar](https://ui.shadcn.com/docs/components/calendar)
