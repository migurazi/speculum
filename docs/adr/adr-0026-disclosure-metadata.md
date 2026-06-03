# ADR-0026: 공시 metadata 표시 — 제목·시각·링크만 (§2.7 예외 공식화)

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-06-02 |
| **Deciders** | 사용자 |
| **Related** | CONCEPT §2.7(Observation over Speculation, M3+ 예외 명시), §2.3(Active Inspection), [[adr-0011-watchlist-scope]] D1.2(공시 알림 미포함), [[adr-0007-default-ui-rules]] D4.5(EXTERNAL_QUOTE scope)·D8.1(디스클레이머 게이트), [[adr-0012-dart-effective-date-conservative-policy]] D6(`_rcept_date` 정밀 공시일); oracle 분석(2026-06-02) M3_PLAN #2 |

## Context

CONCEPT §2.7 은 LLM/자연어 처리의 의제 설정 위험을 경계하면서도 **명시적 예외**를 터놓았다:
"M3+ 검토: 사용자가 명시적으로 요청한 종목 1개에 대한 공시 제목 list 표시 — 의제 설정 위험
낮음." M3 #2 는 이 예외를 처음 구현한다. 현재 `dart_adapter.py` 는 재무제표
(`fnlttSinglAcntAll.json`)·자사주(`stockTotqySttus.json`)만 fetch 하고 공시 목록
(`list.json`)은 미구현 — 신규 추가가 필요하다.

위험: (A) 공시 **본문/요약** 표시로 미끄러지면 가치 판단 내포(§2.7 위반), (B) "주요 공시"
시스템 큐레이션 = 의제 설정, (C) 요청 안 한 종목에 공시 feed push = §2.3 Active Inspection 위반.

## Decision

### D1. 표시 범위 하드 제약 — 제목·접수시각·DART 원문링크만
공시 list 는 **제목(`report_nm`) + 접수일자(`rcept_dt`) + DART 원문 viewer URL(`rcept_no`)**
3 필드만. 본문·요약·자체 분류라벨 0. 클릭 시 DART 페이지로 이탈(텍스트 자체 표시 X, §2.7).

### D2. scope = EXTERNAL_QUOTE
공시 제목은 회사가 낸 외부 사실 → `forbidden_words` 검사 skip(ADR-0007 D4.5). 제목에 금지
어휘가 들어가도(회사 명명) redact/block 하지 않는다. 단 Speculum 이 **생성하는** 어떤 라벨·
배지·요약도 추가하지 않으므로 SYSTEM scope 생성 텍스트는 0.

### D3. trigger = 사용자 명시 요청 1종목만
Stock Detail 진입한 그 1종목의 on-demand fetch. batch 수집 X(영속화 불요 — 항상 DART 실시간).
홈/Watchlist/Screener 에 공시 feed push 금지(ADR-0011 D1.2 일관). "중요공시" 필터·배지 금지.
최신순(`rcept_dt` 역순) 정렬은 사실이라 허용(ADR-0007 D1 "거래대금↓=사실" 동형).

### D4. PIT 정합 — as_of 이후 공시 숨김
historical view(as_of < today)에서는 그 시점 이후 접수 공시 비표시. `_rcept_date`
(rcept_no→정밀 공시일) 재사용해 `rcept_date <= as_of` 필터. look-ahead 0.

## Rationale
- §2.7 이 설계 시점에 길을 터놓은 유일한 항목 — 본문 미표시 + 시스템 큐레이션 0 으로 의제
  설정 위험을 닫으면 경계 안.
- on-demand(영속화 X)라 batch/ORM 불요 — 6항목 중 최저 침습. DART 원문 링크 이탈로 저작권·
  2차가공 리스크도 회피(ADR-0006 D5.1 출처 표시만).

## Consequences
- **Positive**: AC 빠른 가치. #6(AI 사실추출)의 DART 공시 fetch 인프라 씨앗.
- **Negative**: on-demand DART 호출 → rate-limit 노출(요청 1종목 한정이라 경미). corp_code 매핑 필요.
- **Neutral**: 본문 표시는 영구 금지(§2.7 경계). #6 의 사실추출도 본문 표시가 아닌 구조화 필드만.

## Alternatives Considered
- **A. batch 수집 + 영속화** — 모든 종목 공시 push = §2.3 위반 + 의제 설정. 기각. on-demand 1종목.
- **B. 공시 본문 inline 표시** — §2.7 정면 위반. 기각. DART 링크 이탈.

## References
- CONCEPT §2.7/§2.3, ADR-0007 D4.5/D8.1, ADR-0011 D1.2, ADR-0012 D6
- 단계: adapter `fetch_disclosure_list` → route `GET /api/stocks/{code}/disclosures` → client `DisclosurePanel`
