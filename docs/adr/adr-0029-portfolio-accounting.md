# ADR-0029: Portfolio 회계 — 거래내역 기반 사실 회계, 평가/제안 분리

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-06-02 |
| **Deciders** | 사용자 |
| **Related** | CONCEPT §0(정체성 — 추천 않는 거울)·§2.2(No Advice)·§2.9(Temporal Continuity)·§10(한계 — Portfolio 회계 유보), [[adr-0011-watchlist-scope]] D1.2(수익률 추적 명시 미포함), [[adr-0009-corporate-action]](보정정책 — 거래 레벨 적용), [[adr-0007-default-ui-rules]] D2(등락색)·D2.2(grayscale)·D4.5(USER_PRIVATE scope), [[adr-0020-append-only-invariant]]; oracle 분석(2026-06-02, "정체성-민감 ADR") M3_PLAN #4 |

## Context

CONCEPT §0 은 Speculum 을 "정량 데이터를 왜곡 없이 비추는 도구 — 추천하지 않는다"로 정의하고,
§10 은 "포트폴리오 회계 없음 — 가격 추적 vs 회계는 다른 영역, M3+ 검토"로 유보했다. ADR-0011
D1.2 가 "수익률 추적(보유 수량/평단)"을 명시 미포함. #4 는 이 경계를 처음 넘는다.

위험: (A) 손익률 표시 → "더 사야 하나"의 행동 유도(§2.2), 평단 대비 등락색(적/녹) = ADR-0007 D2
정면 위반. (B) 실현손익은 corporate action 보정(액면분할 후 평단 조정)을 정확히 해야 함(§2.9).
(C) 증권사 계좌 연동 = 실시간 매매 유도·개인정보·자본시장법 폭증.

## Decision

### D1. PortfolioTransaction entity — append-only 수동 입력 거래내역
신규 `portfolio_transactions`(append-only, ADR-0020): `user_id`, `code_lineage_id`(종목 lineage,
ADR-0009 D6), `side`(`'buy'`|`'sell'`), `quantity`(정수), `unit_price`(Decimal), `trade_date`,
`fee`(Decimal, 수수료+세금 합산 입력값), `created_at`. **사용자 수동 입력만** — 증권사 계좌 연동 0(D4).
거래 정정은 append(역분개)로(append-only 정신).

### D2. Position 계산 service — 거래내역 → 사실 회계
거래내역에서 종목별 **보유수량·평단(이동평균법)·원가(취득가액 합)** 산출. 매도 시 실현손익 =
(매도가 − 평단)×수량 − fee. **순수 사실 계산**(stateless, 결정적). corporate action 보정(D5)을
거래 레벨에 적용.

### D3. 회계 ≠ 평가 분리 (정체성 핵심)
- **사실 표시**: 보유수량·평단·원가·(현재가 입력 시)평가금액·평가손익. 이는 산술 사실(§2.1).
- **금지**(ADR-0007 D2/D2.2): 손익률 headline 대형 강조, 등락색(녹수익/적손실), "수익 중"/"손실 중"/
  "양호" 평가 라벨, 종목 손익 순위/정렬 default, "더 담기"/"비중 조절" 등 행동 유도. **grayscale
  중립 톤만**. 손익은 사실 숫자로만 표시하고 판단·강조·색분기 0.
- 평가금액은 현재가(사용자 조회 시점 사실)×수량 — "성과 평가"가 아닌 산술. 추천 변질 통로 차단.

### D4. USER_PRIVATE scope — 공유 금지, 수동 입력만
Portfolio 는 본인 자산 → `USER_PRIVATE`(forbidden-words 검사 skip, Notes 동형). **공유 불가**
(visibility 없음, user 격리 — 공유 시 USER_SHARED 추천 변질). 계좌 연동 0 — 거래내역 수동 입력 장부.

### D5. corporate action 보정 — 거래 레벨
액면분할·무상증자 등(ADR-0009) 발생 시 보유수량·평단을 보정(분할비율 적용). `price_adjuster` 의
보정계수 산출 로직 재사용 정신 — 거래일 이후 발생한 CA 를 position 계산에 반영(§2.9 시계열 정합).
merger/spin_off 는 M0 미보정(보고만, price_adjuster 일관).

### D6. 세금 미결합 — 세전(税前)만
손익 표시는 세전만. 세금 계산(#5, ADR-0030)은 거래내역 위에서 별도. 결합 시 세무자문 경계 진입.

## Rationale
- Watchlist(ADR-0011) 의 code_lineage_id 재사용 + 거래내역 entity 추가. 거래내역은 #5 세금의 선행.
- "회계 한정 + 평가/제안 0"이 §0 정체성과 §2.2 를 동시 보존 — 사실 산술은 거울의 본분, 판단·강조만 차단.
- append-only + 수동 입력으로 자본시장법·개인정보 리스크 최소화.

## Consequences
- **Positive**: 사용자 자기 거래의 사실 회계. #5 세금 거래내역 토대.
- **Negative**: §0 정체성 경계 확장 — 출력 게이트(D3) 회귀 테스트 필수. entity/migration.
- **Neutral/Unknown**: "회계 도구화"가 §0 와 긴장 — 평가/제안 0 으로 "사실 거울" 위치 유지. 손익률
  강조가 추천으로 보이면 §2.2 재검토. ADR-0006 변호사 자문에 Portfolio 손익 표시 포함 권고.

## Alternatives Considered
- **A. WatchlistItem 에 수량/평단 컬럼 추가** — 단일 평단만, 거래내역 부재로 실현손익·세금(#5) 불가. 기각.
- **B. 증권사 계좌 연동(MyData)** — 실시간 매매 유도·개인정보·자본시장법. 기각(D4 수동 입력만).
- **C. 손익률 등락색 표시(네이버/증권사 관행)** — ADR-0007 D2 정면 위반. 기각(D3 grayscale).

## References
- CONCEPT §0/§2.2/§2.9/§10, ADR-0011 D1.2, ADR-0009, ADR-0007 D2/D2.2/D4.5, ADR-0020
- 단계: migration(portfolio_transactions) → repository → position 계산 service(CA 보정) → route → client(`Portfolio/` + 출력 게이트 test)
