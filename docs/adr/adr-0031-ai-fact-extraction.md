# ADR-0031: AI 공시 사실추출 — 요약 아닌 구조화 사실 + LLM 출력 게이트

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-06-02 |
| **Deciders** | 사용자 |
| **Related** | CONCEPT §2.7(Observation over Speculation — LLM 경계)·§2.1(Fidelity)·§2.2(No Advice)·§10(AI/LLM 미사용 유보), [[adr-0026-disclosure-metadata]](공시 fetch 인프라·EXTERNAL_QUOTE)·[[adr-0007-default-ui-rules]] D4.4(ForbiddenWordsGuardMiddleware)·D8.1(디스클레이머 게이트), [[adr-0006-legal-review]](유사투자자문 경계); oracle 분석(2026-06-02, "철학적 최우선 ADR") M3_PLAN #6 |

## Context

CONCEPT §2.7 은 LLM 을 명시적으로 경계한다: "자연어 처리로 확장하면 LLM 의 hallucination 위험
+ 의제 설정 위험". §10 은 "AI/LLM 미사용 — §2.7 와 충돌 위험"으로 유보했다. #6 은 Speculum 의
9년치 경계를 처음 넘는 결정이다 — 따라서 6항목 중 가장 무거운 게이트가 필요하다.

충돌: (A) 공시 **요약**은 본문 텍스트의 해석/압축 — §2.7 의 "텍스트 표시 X"를 LLM 이 우회하는
통로. (B) LLM 요약은 출처 추적 불가·hallucination 가능 — §2.1 "왜곡 없는 거울"의 정반대.
(C) 요약이 "호재/악재/긍정적" 가치 판단 생성 시 추천 변질(§2.2).

## Decision

### D1. "요약" 아닌 구조화 사실 추출 — 자유 서술 금지
LLM 의 출력을 **자유생성 요약이 아니라 구조화된 사실 필드 추출**로 재정의. 공시에서 사실 필드
(공시유형·금액·일자·당사자·수량 등)만 **structured output(고정 스키마)**으로 추출. 자유 서술·
해석·압축 문장 0. "이 공시는 유상증자, 규모 X억, 청약일 Y" 같은 **사실 필드 슬롯 채우기**만.
스키마 외 자유 텍스트 필드 없음.

### D2. LLM 출력 게이트 — ForbiddenWordsGuardMiddleware SYSTEM scope
LLM 이 추출한 모든 텍스트는 `forbidden_words.assert_clean(scope=SYSTEM)` 통과 필수(ADR-0007 D4.4
미들웨어가 LLM 응답도 SYSTEM scope 검사). "유망/매수/추천" 등 생성 시 **BLOCK**. 이것이
hallucination·가치판단의 1차 방어선. 게이트 실패 시 추출 결과 미표시(fail-closed).
- **어휘 보강 미결(Momus 검토 항목)**: 현 `forbidden-words.json` 에 "유망/매수/추천/Buy"는
  있으나 sentiment 어휘 "호재/악재/긍정적/부정적"은 **부재** — 게이트가 이들을 차단하지 못한다.
  이 어휘들은 server 주석/docstring 에 이미 등장해 단순 추가 시 conformance CLI 충돌 위험이
  있으므로, release blocker 의 Momus §2.7 검토에서 scope 분리(생성 텍스트만 검사)와 함께 보강한다.

### D3. 출처 강제 + AI 디스클레이머 게이트
모든 추출 사실 옆 **DART 원문 인용 + 링크**(ADR-0026 EXTERNAL_QUOTE). **"AI 가 추출한 사실이며
원문 확인이 필수입니다. hallucination 가능성이 있습니다"** 디스클레이머 게이트 — 없이 렌더 차단
(ADR-0007 D8.1). 추출 사실은 원문 대조 가능한 필드만(검증 가능성).

### D4. trigger = 사용자 명시 1종목·1공시
사용자가 명시 요청한 **1종목·1공시**만(CONCEPT §2.7/§10 "요청 종목 1개" 문구). 자동 추출·feed·
일괄 처리·홈/Watchlist push 0(§2.3). on-demand only.

### D5. 미래 전망/평가 절대 0 — 과거 공시 사실만
미래 전망·매매 시사·투자 판단 생성 절대 금지(§2.2). 과거에 접수된 공시의 사실만. 전망/추천
필드를 스키마에 두지 않는다(구조적 차단).

### D6. LLM adapter 추상화 — 운영 연동은 release blocker
`LlmFactExtractor` protocol 로 추상화 — 구현체(Anthropic SDK 등)는 주입. 테스트는 fake. **실제
LLM 운영 연동은 release blocker**(§2.7 경계 + ADR-0006 자문 + API 비용/키). 코드는 게이트·스키마·
디스클레이머로 방어해 구현하되, 운영 노출은 자문 후. structured output 강제(자유생성 차단).

## Rationale
- "사실 추출 ≠ 요약"이 §2.7 핵심 회피 — 고정 스키마 슬롯 채우기는 본문 해석이 아닌 사실 전사.
- LLM 출력에 기존 ForbiddenWordsGuard(D4.4) 적용 = 가치판단·hallucination 1차 차단. 신규 발명 아님.
- 출처 강제(D3) + 원문 대조 가능 필드만 = §2.1 검증 가능성 보존. 디스클레이머로 법적 방어.
- adapter 추상화로 §2.7 경계를 코드 레벨에서 운영과 분리 — 자문 전 운영 노출 0.

## Consequences
- **Positive**: 사용자 요청 1공시의 구조화 사실(M3+ §2.7 예외 실현). #2 DART 인프라 활용.
- **Negative**: §2.7 경계 확장 — Momus 검토 권고(release 전). LLM hallucination 잔존 위험(게이트+출처+디스클레이머로 완화). 운영 LLM 연동 blocker.
- **Neutral/Unknown**: 사실 추출도 잘못된 필드 가능 → 출처 대조 강제. 추후 §2.7 재평가 트리거.

## Alternatives Considered
- **A. 자유생성 요약** — §2.1/§2.7 정면 위반. 기각(D1 구조화 사실만).
- **B. LLM 미사용 유지** — §10 유보 지속. 사용자가 M3 포함 결정 → 최대 게이트로 구현.
- **C. 출력 게이트 없이 LLM 직결** — hallucination/가치판단 무방비. 기각(D2 필수 게이트).

## References
- CONCEPT §2.7/§2.1/§2.2/§10, ADR-0026, ADR-0007 D4.4/D8.1, ADR-0006
- 단계: LlmFactExtractor protocol + 사실추출 스키마 → 출력 게이트(SYSTEM scope) → route(1공시 on-demand) → client(사실 필드 표시 + 출처 + 디스클레이머 게이트)
- **release blocker**: LLM 운영 연동 + Momus §2.7 검토 + ADR-0006 자문 — 완료 전 운영 노출 금지.
