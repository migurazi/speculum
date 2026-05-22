# ADR-0011: Watchlist Scope — 단순 즐겨찾기 + 메모 (M0 알림 미구현)

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-05-22 |
| **Deciders** | 사용자 |
| **Related** | `docs/CONCEPT.md §3.1 MVP 4 뷰`, `§2.2 No Advice`, `docs/M0_PLAN.md T11, T28, T39, T40`, [[adr-0006-legal-review]], [[adr-0007-default-ui-rules]], [[adr-0010-home-screen]] |

## Context

Watchlist 는 MVP 4 뷰의 한 축. CONCEPT §3.1 에서 "관심종목 폴더링 + 메모" 를 핵심 인터랙션으로 정의했으나, 알림 기능 포함 여부는 미정.

Metis 검토 E3 의 핵심 발견:
- "관심종목의 PER 이 X 이하 도달 시 알림" — 매수 신호와 구분 불가
- "가격 알림" 의 한국 자본시장법 회색지대 ([[adr-0006-legal-review]] D2)

[[adr-0007-default-ui-rules]] D3 의 결정 — M0 푸시 알림 미구현 — 의 구체 implementation 이 본 ADR.

## Decision

### D1. M0 Watchlist Scope = 단순 즐겨찾기 + 메모 + Screen Run 저장

#### D1.1 포함된 기능

| 기능 | 설명 |
|---|---|
| 종목 추가 / 제거 | 검색·Stock Detail 에서 ★ 클릭 |
| 폴더링 | 트리 구조 (최대 2 단 depth — M0). Drag & drop reorder |
| 종목별 메모 | 짧은 텍스트 (~280 자 — M0). Markdown M2+ |
| Screen Run 저장 | 스크리닝 결과 종목들을 폴더로 한 번에 추가 |
| Note: as-of date | Watchlist 표시 데이터는 [[adr-0008-as-of-date]] 의 as-of 기준 |

#### D1.2 명시적으로 미포함 (M0)

| 기능 | 거부 사유 |
|---|---|
| **가격 알림** ("PER X 이하 도달 시 푸시") | [[adr-0006-legal-review]] D2 — 매매 시점 조언으로 해석 위험 |
| **공시 알림** ("이 종목의 새 DART 공시 발생") | 사실 전달이나 알림 빈도가 사용자 행동 유도. M1 ADR 로 재검토 |
| **목표가 설정** | "이 가격에 사고 싶다" — 명백한 매매 의도 |
| **수익률 추적** (보유 수량 / 평단 입력) | 포트폴리오 회계 영역 — M3+ |
| **공유** (Watchlist URL 공유) | 사용자 간 영향력 — "X 님의 관심 종목" → 추천 효과. M2+ ADR |
| **태그·라벨** ("관심 종목 → 매수 검토") | 행위 시사 단어 위험 ([[adr-0007-default-ui-rules]] D4) |

### D2. 데이터 모델

```sql
CREATE TABLE watchlists (
  id          UUID PRIMARY KEY,
  user_id     UUID NOT NULL REFERENCES users(id),
  parent_id   UUID REFERENCES watchlists(id),   -- 폴더링 (depth ≤ 2)
  name        TEXT NOT NULL,
  display_order INTEGER NOT NULL DEFAULT 0,
  is_default  BOOLEAN NOT NULL DEFAULT false,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE watchlist_items (
  id              UUID PRIMARY KEY,
  watchlist_id    UUID NOT NULL REFERENCES watchlists(id),
  code_lineage_id UUID NOT NULL REFERENCES stocks_master(id),
  note            TEXT,                          -- ≤ 280 chars (M0)
  display_order   INTEGER NOT NULL DEFAULT 0,
  added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (watchlist_id, code_lineage_id)
);

CREATE INDEX idx_watchlist_items_lineage ON watchlist_items(code_lineage_id);
```

`code_lineage_id` 참조 ([[adr-0009-corporate-action]] D6) — 종목코드 변경·합병 후에도 Watchlist 의 정체성 유지.

### D3. UI 디자인

#### D3.1 좌측 트리

```
┌─────────────────────────────┐
│  관심 종목                   │
│                              │
│  ▾ ★ 핵심  (3)              │
│      삼성전자                │
│      SK하이닉스              │
│      카카오                  │
│                              │
│  ▶ 헬스케어 (12)             │
│  ▶ 2차전지   (8)             │
│  ▶ Screen Run: PER<10 (15)   │
│                              │
│  + 새 폴더                   │
└─────────────────────────────┘
```

- 최대 depth 2 (폴더 안의 폴더 X — M0)
- "Screen Run:" prefix = Screener 결과 저장 폴더 (D4)
- 드래그로 reorder

#### D3.2 메인 영역

선택된 폴더의 종목 list — 테이블:

```
종목명         코드     시장    종가      PER    ROE     메모          [···]
삼성전자       005930   KOSPI   72,500   12.3   9.8%   ...           [···]
SK하이닉스     000660   KOSPI  168,000   N/A   -2.1%   적자 회복?    [···]
```

- 컬럼 customize (M1+)
- 클릭 → Stock Detail
- "···" 메뉴: 폴더 이동 / 메모 편집 / 제거

#### D3.3 종목 추가

- 검색 결과 / Stock Detail / Compare 의 "★" 아이콘 클릭 → 폴더 선택 모달
- Screener 결과 테이블의 row 별 ★

### D4. Screen Run 저장

[[adr-0008-as-of-date]] D7 + CONCEPT §2.10 Reproducibility 의 구체 implementation.

Screener 결과 테이블 우상단 "💾 Save Run" 버튼:

```
[Save Run]

Run 이름: PER < 10, ROE > 15%, 시가총액 ≥ 1조

이 결과를 어떻게 저장하시겠어요?
○ Watchlist 폴더로 저장 (종목 list 만)
◉ Screen Run 으로 저장 (재현 가능 query + 결과)
○ 둘 다

폴더 이름: PER<10_ROE>15_2026-05-22

[취소]  [저장]
```

- "Screen Run 으로 저장" = `screen_runs` row 생성. 6 개월 후 재실행하면 같은 결과 (PIT freeze).
- "Watchlist 폴더로 저장" = 결과 종목들을 새 폴더에 즐겨찾기 추가.

### D5. as-of date 와 Watchlist

[[adr-0008-as-of-date]] D9:

- Watchlist 의 종목 list = 사용자가 추가한 시점에 fix. as-of 무관.
- 표시 데이터 (PER, 종가 등) = as-of 기준.
- 상장폐지된 종목은 표시되 데이터 N/A + "{date} 에 상장폐지됨" 명시.

### D6. M1+ 검토 항목

| 항목 | M1 결정 ADR |
|---|---|
| 공시 알림 (이메일 only — 푸시 X) | 새 ADR. [[adr-0006-legal-review]] D2 의 "사실 전달 vs 시그널" 경계 재검토 |
| 컬럼 customize | 단순 UI 결정, 별도 ADR 불필요 |
| Markdown 메모 | XSS 방어 검토. 별도 ADR |
| 메모 280 자 → 더 길게 | 사용자 피드백 후 |
| Watchlist 공유 | M2+. 공유 URL 의 영향력 신중 검토 |
| 포트폴리오 회계 | M3+. 별도 모듈 |

### D7. 빌트인 폴더 — "내 관심 종목" only

신규 사용자의 default 폴더 = "내 관심 종목" 1개 (is_default=true). 그 외 빌트인 폴더 (예: "추천 종목", "유망주") 절대 없음.

### D8. 빈 Watchlist UX

[[adr-0010-home-screen]] D3 일관:

```
관심 종목이 비어있습니다.

Speculum 은 종목을 추천하지 않습니다.
대신 조건을 입력해 시장을 직접 살펴보세요.

[ 종목 검색 → ]   [ Screener 시작 → ]
```

## Rationale

1. **[[adr-0006-legal-review]] D2 의 implementation** — 매매 시점 조언으로 해석될 기능 (가격 알림·목표가) 자체 미구현.
2. **§2.2 No Advice 의 단단함** — 알림이 silent 한 추천이 되는 가능성 차단.
3. **§2.3 Active Inspection** — Watchlist 는 사용자가 정한 의제. 시스템이 새 종목 제안 X.
4. **§2.10 Reproducibility** — Screen Run 저장이 Watchlist 와 연계 (D4).
5. **Metis E3 발견 대응** — 가격 알림의 회색지대 직접 회피.
6. **MVP 단순성** — 빌더 vs 알림·포트폴리오·공유 의 기능 폭발 회피.

## Consequences

### Positive
- **법적 안전** — 매매 시그널 회피.
- **단순한 MVP** — 4 뷰의 다른 부분에 집중.
- **사용자 의제 일급** — Watchlist 가 §2.3 의 implementation.
- **Screen Run 연계** — 재현성과 관심종목이 자연 통합.

### Negative
- **사용자 일부 기대치 결여** — "왜 알림이 없나?" 피드백 예상. 도움말로 안내.
- **공유·포트폴리오 미지원** — 사용자가 다른 도구와 병행해야 할 수 있음. M2+ 검토.
- **메모 280 자 한계** — 짧은 사고 메모는 OK 이나 깊은 분석 메모는 부족. Markdown / 더 긴 메모는 M1+.

### Neutral / Unknown
- **공시 알림의 M1 결정** — librarian D-3 의 "사실 전달은 OK" 해석을 기반으로 가능할 수 있음. 그러나 알림 빈도가 사용자 행동 유도 → 신중.
- **Watchlist 의 sharing 미지원** — 블로그·논문에서 "이 폴더의 종목들" 인용하려면 Screen Run JSON export 로 우회 가능. M2 Factor Lab 과 함께 본격.

## Alternatives Considered

- **A. 가격 알림 포함** — 사용자 친숙. 그러나 매매 시그널 회색지대 정면 진입. **거부**.
- **B. 공시 알림만 포함** — 사실 전달로 librarian D-3 해석상 OK. 그러나 알림 frequency · UI 가 추천 효과 가능. M1 ADR 로 보류. **M0 거부**.
- **C. Watchlist 자체 없음 (Browser 즐겨찾기 사용)** — Speculum 의 핵심 기능 누락. **거부**.
- **D. 포트폴리오 회계 포함** — 가치 큰 기능이나 M0 범위 폭발. 정보 제공 vs 회계 도구 경계도 복잡. M3+. **거부**.
- **E. Watchlist 공유 (URL)** — 사용자 영향력 발생. 블로그·SNS 에서 "X 의 관심 종목" 형태 → 추천 효과. M2+ 신중. **거부**.

## References

- [[adr-0006-legal-review]] D2 — 매매 시점 조언 회피
- [[adr-0007-default-ui-rules]] D3 — 푸시 알림 미구현
- [[adr-0008-as-of-date]] D7, D9 — Screen Run freeze + 상호작용
- [[adr-0009-corporate-action]] D6 — code_lineage_id 참조
- [[adr-0010-home-screen]] D1 — Watchlist 가 홈 화면의 상단
- Metis 검토 E3 — Watchlist 의 알림 미끄러짐
- Norma `docs/CONCEPT.md §2.10` 환자/케이스 관리 — 다중 시점 비교 동형 (Watchlist 는 다중 종목 관심의 결)
