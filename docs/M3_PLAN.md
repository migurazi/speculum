# M3_PLAN — v1.x outlook 항목 6종 정식 task 분해

> ROADMAP §5 "M3+ outlook" 6개 후보를 **단일 M3(v1.x)** 로 확정. (사용자 결정 2026-06-02)
> 핵심 통찰(oracle): 기존 인프라(`ForbiddenWordsGuardMiddleware`·`*-visual-gate.test.tsx`·
> PackRegistry freeze)가 가드레일의 80%를 이미 보유 — 신규 발명이 아니라 **기존 패턴의
> 새 surface 적용**. 충돌 무게중심은 항목마다 다름(백테스트=§2.10/§2.4, Portfolio/세금=§0
> 정체성+세무사법, AI=§2.7 정면).

## 1. 범위 확정

| # | 항목 | M3 포함 | 충돌 무게중심 | ADR |
|---|------|---------|--------------|-----|
| 2 | 공시 metadata 표시 | ✅ | §2.7(예외 이미 허용) §2.3 | 경량 ADR-0026 |
| 1 | 백테스트 모듈 | ✅ | §2.10 Reproducibility / §2.4 PIT | **무거움** ADR-0027 |
| 3 | Factor pack community | ✅ | §2.2 / §2.8 / §2.10 | ADR-0028 |
| 4 | Portfolio 회계 | ✅ | §0 정체성 / §2.2 / §2.9 | **정체성** ADR-0029 |
| 5 | 세금 계산 | ✅ | 세무사법 / §2.1 | **법적1순위** ADR-0030 |
| 6 | AI 통합 (공시 사실추출) | ✅ | §2.7 정면 / §2.1 / §2.2 | **철학1순위** ADR-0031 |

## 2. 구현 순서 (의존성 위상정렬 + 충돌 강도 + 법적 blocker 회피)

```
① 공시 metadata (#2)  ──→  ⑥ AI 사실추출 (#6)      [DART 공시 인프라 공유, 직렬]
② 백테스트 (#1)        ──→  ③ Pack community (#3)    [성과 차원 선행, 직렬]
④ Portfolio (#4)       ──→  ⑤ 세금 (#5)              [거래내역 선행, 직렬]
```
- **#1·#3** 은 **#2** 와 독립 → 병렬 가능.
- **#5·#6** 은 후반 배치: 세금=변호사+세무사 자문 blocker(ADR-0006 D9 미실시와 묶음),
  AI=출력 게이트가 5개로 단련된 뒤 마지막.

## 3. 각 항목 핵심 가드레일 + task

### #2 공시 metadata 표시 (최저 난이도, 무의존 — **첫 착수**)
- **가드레일**: 제목·접수시각·DART원문링크만. 본문/요약/분류라벨 0. `scope=EXTERNAL_QUOTE`
  (forbidden-words skip — 회사가 낸 사실). trigger=Stock Detail 진입 1종목만(홈/Watchlist
  push 금지). 최신순 정렬은 사실, "중요공시" 배지/필터 금지.
- **task**:
  - T-M3-01 `dart_adapter.fetch_disclosure_list(corp_code, 기간)` — DART `list.json`
    (`opendart.fss.or.kr/api/list.json`). on-demand(batch X). PIT: `_rcept_date` 재사용.
  - T-M3-02 route `GET /api/stocks/{code}/disclosures` — corp_code 매핑 + as_of PIT 필터.
  - T-M3-03 client `StockDetail/DisclosurePanel.tsx` + `lib/api/disclosures.ts`.
  - T-M3-04 `disclosure-panel-gate.test.tsx`(본문/배지 0 회귀) + adapter/route 테스트.

### #1 백테스트 모듈
- **가드레일**: grayscale equity curve + 중립 통계표만. "수익률 N%" headline 강조·등락색·
  pack간 자동 순위/정렬 default·"최고성과" 큐레이션 금지. 거래비용(수수료+증권거래세) default
  가정 강제 노출. "과거 성과는 미래 보장 X" 디스클레이머 게이트(없으면 렌더 차단). 결과 =
  freeze artifact(pack content_hash + batch_id + rebalance 정책 ver — ADR-0025 D5 시계열 확장).
  survivorship: 상장폐지 종목 historical universe 선검증.
- **task**: ADR-0027 선행 → backtest engine(as_of 그리드 순회 + PIT 횡단면) → freeze schema →
  route → client(`Backtest/` + `backtest-visual-gate.test.tsx`) → forbidden-words KO collocation 추가.

### #3 Factor pack community
- **가드레일**: `USER_SHARED` scope 활성화(ADR-0007 D4.5 dead-code 해제 — pack name/desc
  forbidden-words 검사). import 명시 매핑(silent merge 금지, Norma §2.3 identity 3-tier).
  시스템 큐레이션("인기 pack"/다운로드 순위) 0. content_hash fail-loud.
- **task**: ADR-0028 → `custom_pack_repository.list_for_community()` + visibility 모델 →
  공유/import route → client `Lab/CommunityPackBrowser`.

### #4 Portfolio 회계
- **가드레일**: 보유수량·평단·원가까지 **사실만**. 손익률 headline 강조·등락색·"수익중" 라벨 금지
  (grayscale, ADR-0007 D2.2). `USER_PRIVATE` scope(공유 금지). **수동 입력만**(증권사 계좌연동 0).
  세금 미결합(세전만). corporate action 보정(ADR-0009) 사용자 거래 레벨 적용.
- **task**: ADR-0029(§0 정체성 결정 — Momus 검토) → `PortfolioPosition` entity(Watchlist 확장
  또는 1:1) → route → client `Portfolio/`.

### #5 세금 계산
- **가드레일**: 공개 산식 단순적용만(증권거래세 우선 → 양도세는 상황의존이라 신중). 개별 상황
  세무판단(대주주 판정·손익통산) 0. "세무자문 아님" 디스클레이머 게이트. 세율 효력일 freeze
  (§2.8 패턴). 금투세 등 미확정 제도 미표시. **세무사+변호사 자문 필수**(release blocker).
- **task**: ADR-0030(법적) → 거래세 계산기(단순 산식) → 디스클레이머 게이트 → (양도세는 자문 후).

### #6 AI 통합 (공시 사실추출)
- **가드레일**: "요약" 아닌 **구조화 사실 필드 추출**(공시유형·금액·일자). 자유생성 요약 금지
  (§2.1/§2.7). LLM 출력에 `ForbiddenWordsGuardMiddleware` SYSTEM scope 적용("호재/유망"
  BLOCK). 출처+원문링크 강제. trigger=1종목·1공시. 미래 전망/매매시사 0.
- **task**: ADR-0031(철학적 — Prometheus/Momus) → #2 DART 인프라 위 사실추출 파이프라인 →
  LLM 출력 게이트 삽입 → client.

## 4. 마일스톤 종료 의무 (ROADMAP §6)
squash 직전 Momus 전방위 검토: 8기둥(Fidelity/No Advice/Conformance) + 자본시장법·세무사법
경계 + PIT/corporate action 무결성 + 자매 프로젝트 패턴 일관.

## 5. release blocker (직렬)
1. ADR-0006 D9 변호사 자문(M2 잔여) — 세금(#5) 세무사 자문과 묶어 일괄 해소.
2. 백테스트 survivorship universe 데이터 레벨 선검증.
