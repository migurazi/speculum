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
- **어휘 보강 — 완료(M8, 2026-06-07)**: `forbidden-words.json` `ko_sentiment`(호재/악재/긍정적/
  부정적) 보강 완료 + 런타임 LLM 출력 게이트가 `assert_clean(include_sentiment=True)`로 검사.
  scope 분리 구현 — repo 스캔(check_forbidden_words)·미들웨어·conformance CLI 는 absolute 만
  (`scan_text` 기본 `include_sentiment=False`), sentiment 는 LLM 출력 게이트에서만 opt-in 검사하므로
  server 주석/docstring 의 정당한 어휘 등장과 충돌 0(`_sentiment_note` + check_forbidden_words
  exclude). `en_sentiment` 는 유보(M8 — "positive cash flow"·"Material Adverse Change" 등 정상
  재무·법률 용어 충돌, en_absolute 가 Buy/Bullish 포괄). 테스트 `test_extract_facts_gate_blocks_sentiment`.

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

**코드 완성 — 완료(M8, 2026-06-07)**: `AnthropicFactExtractor`(strict tool use `tool_choice`
강제·`additionalProperties:False`·system prompt §2.7 금지) 전체 구현 + 단위 테스트(fake client
주입). **우발 활성 방지 게이트 추가**: 활성 조건 = pytest 아님 AND `SPECULUM_ENABLE_LLM_FACT_
EXTRACTION` opt-in AND `ANTHROPIC_API_KEY` AND SDK 설치 — **키 존재만으로는 활성 안 됨**(타 인프라
키 공유 시 우발 운영 노출 차단, AUTH_SECRET prod gate 동형). 하나라도 빠지면 None→route 503
fail-closed. **운영 노출(prod opt-in 활성)은 ADR-0006 법률 자문 후** — 코드 blocker 없음, 외부
자문만 잔여.

## Rationale
- "사실 추출 ≠ 요약"이 §2.7 핵심 회피 — 고정 스키마 슬롯 채우기는 본문 해석이 아닌 사실 전사.
- LLM 출력에 기존 ForbiddenWordsGuard(D4.4) 적용 = 가치판단·hallucination 1차 차단. 신규 발명 아님.
- 출처 강제(D3) + 원문 대조 가능 필드만 = §2.1 검증 가능성 보존. 디스클레이머로 법적 방어.
- adapter 추상화로 §2.7 경계를 코드 레벨에서 운영과 분리 — 자문 전 운영 노출 0.

## Consequences
- **Positive**: 사용자 요청 1공시의 구조화 사실(M3+ §2.7 예외 실현). #2 DART 인프라 활용.
- **Negative**: §2.7 경계 확장 — **Momus §2.7 검토 완료(M8, 2026-06-07 OKAY)**: 6축(의제설정·예측
  구조적 차단·trigger 단건·No Advice fail-closed 게이트·hallucination 출처/disclaimer·운영 활성
  게이트·슬롯 악용) 전부 PASS, 구조적 차단(strict tool use+필드 부재 스키마 3-layer)이 주 방어.
  LLM hallucination 잔존 위험(게이트+출처+디스클레이머로 완화).
- **Neutral/Unknown**: 사실 추출도 잘못된 필드 가능 → 출처 대조 강제. 추후 §2.7 재평가 트리거.

## 구현 상태 (M8, 2026-06-07)
**코드 deliverable 완료** — D1~D6 충실 이행, Momus §2.7 OKAY. server 2249 passed.
**잔여 = ADR-0006 법률 자문(코드외 blocker)** — 자문 완료 후 prod 에서
`SPECULUM_ENABLE_LLM_FACT_EXTRACTION=1` + `ANTHROPIC_API_KEY` 설정으로 운영 노출.

## Alternatives Considered
- **A. 자유생성 요약** — §2.1/§2.7 정면 위반. 기각(D1 구조화 사실만).
- **B. LLM 미사용 유지** — §10 유보 지속. 사용자가 M3 포함 결정 → 최대 게이트로 구현.
- **C. 출력 게이트 없이 LLM 직결** — hallucination/가치판단 무방비. 기각(D2 필수 게이트).

## References
- CONCEPT §2.7/§2.1/§2.2/§10, ADR-0026, ADR-0007 D4.4/D8.1, ADR-0006
- 단계: LlmFactExtractor protocol + 사실추출 스키마 → 출력 게이트(SYSTEM scope) → route(1공시 on-demand) → client(사실 필드 표시 + 출처 + 디스클레이머 게이트)
- **release blocker**: LLM 운영 연동 + Momus §2.7 검토 + ADR-0006 자문 — 완료 전 운영 노출 금지.
