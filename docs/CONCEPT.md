# Speculum — 컨셉 문서

> **프로젝트명**: `Speculum` (라틴어 "거울·검경(檢鏡)" — 시장 내부를 들여다보는 도구라는 컨셉의 핵심)
> **자매 프로젝트**: Tessera (DICOM 표준 준수), Norma (Cephalometric 분석법 빌더) — 라틴어 어원 시리즈
> **부제**: *Korean Equity Market Quantitative Inspector*
> **작성일**: 2026-05-22
> **상태**: 브레인스토밍 종합 / 요구사항 초안 v0.1
> **목적**: 개인 학습·포트폴리오 + 정량 분석에 관심 있는 소규모 사용자(연구자·블로그 구독자·동료)를 위한 도구

---

## 0. 한 줄 정의

> 한국 주식 시장의 **검경(speculum)**. 정량 데이터를 왜곡 없이 비추어 사용자가 시장을 능동적으로 살피게 하는 도구. **추천하지 않는다.** 스크리닝은 그 한 가지 기능일 뿐이다.

**주 사용자**:
- **(1차)** 정량(quant) 관점으로 한국 시장을 들여다보고 싶은 개인 — 본인 포함
- **(2차)** 같은 관점을 공유하는 소규모 독자·동료 (Google OAuth, 수십~수백 명)
- **(부차적)** 학습자 — "이 PER 은 어떻게 계산되는가" 의 답을 식과 출처까지 보여주는 교육 자료로

기존 도구(네이버 금융, 키움 영웅문, ftrader, 퀀트킹 등)가 **결과만 보여주는 닫힌 도구**라면, Speculum 은 **출처·산출식·시점을 항상 동반 표시하는 정직한 거울**을 지향한다. 일상 종목 조회 80% 시나리오(네이버 금융 검색 한 번)에선 네이버가 더 빠르다 — 이걸 정직하게 인정. 비유하면:

- 네이버 금융 / 영웅문 = SPSS / Stata (강력하지만 식이 안 보이는 닫힌 도구)
- Speculum = **Jupyter + pandas** 의 한국주식 판 (자체로는 네이버만큼 빠르지 않으나, **출처·재현·확장** 가능)

---

## 1. 차별화 포지셔닝

### 1.1 솔직한 비교 — 기능 단위로는 새롭지 않음

| Speculum 이 "코어" 라 부르는 것 | 기존 도구 현황 |
|---|---|
| KOSPI/KOSDAQ 전 종목 정량 스크리닝 | 네이버 금융 종목스크리너, KRX 정보데이터시스템 (지표 제한적) |
| 재무지표 + 기술지표 조합 필터 | 퀀트킹, FnGuide DataGuide (유료) |
| 종목 비교 / Watchlist | 어느 도구든 기본 |
| 차트 + 재무 시계열 | 네이버, 영웅문, TradingView |

→ "스크리닝" 또는 "비교" 자체는 새롭지 않다. **거기서 끝났으면 그저 네이버의 못한 클론**.

### 1.2 진짜 차별점 — 4 가지 (기능이 아니라 **철학·아키텍처**)

1. **모든 값 옆에 식과 출처 (Provenance-first)**
   기존 도구의 "PER 12.3" 은 출처·산출식·기준일이 보이지 않음. Speculum 은 모든 값 옆에 `식 / 데이터 소스 / 기준일 / pack hash` 표시. 학술적·교육적 가치 결정적. Norma 의 self-identifying report 패턴을 주식에 적용.

2. **Point-in-Time Correctness (look-ahead bias 방지)**
   "2024년 10월 기준 PER<10 종목" 을 조회하면, 그 시점에 **공시되지 않은 분기 데이터로 계산된 PER 은 사용하지 않음**. 분기 공시 lag (분기 종료 후 45일~) 를 일급 모델링. 백테스트 가능한 데이터 layer.

3. **개방형 포맷 + 사용자 정의 팩터 (M1+)**
   기존 도구의 "조건검색" 은 그 도구 안에서만 작동. Speculum 은 팩터 정의·조건식·스크리닝 결과를 **JSON pack** 으로 export. git 에 올릴 수 있고 블로그 글 supplementary 로 첨부 가능. "tool" 이 아니라 **format** 의 차이.

4. **1차 자료 우선 (DART-native)**
   기존 도구는 가공된 재무비율을 받음 (출처 추적 어려움, 회사별 표시 차이 흡수됨). Speculum 은 **DART OpenAPI 원문 재무제표를 직접 파싱**해서 산출. K-IFRS 연결/별도 선택을 사용자가 명시적으로 함. 자매 프로젝트 Tessera 의 "DICOM 표준 우선" 결과 동일.

### 1.3 솔직한 한계

- 일상 종목 조회 80% 시나리오는 "삼성전자 어제 얼마 했어?" — 이건 네이버가 빠르고 잘함. Speculum 은 그 80% 에선 우위 없음.
- 장중 실시간 시세 없음 — 무료 데이터 한계. 종가 기준 일배치만.
- 알고리즘 매매 연동 없음 — 정보 제공 도구의 경계 명시 (§5 법적 위치).
- 한국 시장 한정 — 미국·일본 등 해외 시장 미지원. 도메인을 좁혀 깊이 확보.

### 1.4 포트폴리오 관점

- 위 차별점 1, 2 번은 **금융 도메인 + 데이터 무결성을 다 알아야 설계 가능**. 보통 웹 개발자는 흉내 못 내고, 보통 금융인은 데이터 모델을 모름. **본인 위치에서만 설득력 있게 만들 수 있는 프로젝트**.
- 보여줄 수 있는 엔지니어링 영역: 데이터 파이프라인 (DART 파싱·캐시·증분 갱신), 시계열 무결성 (PIT correctness, corporate action 보정), 도메인 스키마 설계 (팩터·pack JSON), 인터랙티브 시각화 (차트·테이블 가상화), Web Crypto + OAuth.

---

## 2. 10 핵심 기둥

> 각 기둥은 컨셉의 한 축. M0/M1/M2 어느 마일스톤에 들어갈지는 §6 참고.
>
> **History**: v0.1 초안은 8 기둥이었으나 Metis 사전 검토 결과 §2.9 Temporal Continuity 와 §2.10 Reproducibility 두 기둥이 강력 권고 추가됨 — corporate action 보정과 Screen Run snapshot 은 자매 프로젝트 Norma 의 핵심 패턴(immutable Run + freeze)을 주식 도메인에 매핑한 것이며, 본 도구의 "검경"이라는 정체성과 직결.

### 2.1 — Fidelity (거울은 왜곡하지 않는다)

**모든 표시 값 옆에 출처·산출식·기준일을 동반 표시.** 가공된 2차 자료 대신 1차 자료(DART, KRX) 를 우선 사용. 가공이 필요할 때는 가공식을 명시.

**구체 정책:**
- 모든 측정값 hover/click → 식 텍스트 + 데이터 소스 + 기준일 표시 (Norma §2.14 패턴)
- 모든 화면의 footer 에 "데이터 기준일: YYYY-MM-DD HH:MM (UTC+9)" 표시
- 데이터가 stale 일 때 명시 (배치 실패·정정공시 미반영 등)

**Anti-pattern (금지):**
- 가공된 PER 을 그대로 표시 + 출처 미표시
- 다른 도구의 값과 다른 이유를 설명할 수 없음 (예: 연결 vs 별도 선택)

### 2.2 — No Advice (거울은 추천하지 않는다)

**Speculum 은 매수/매도/보유 신호를 생성하지 않는다.** 사용자가 정의한 조건의 필터링 결과만 표시.

**금지 단어**: "추천", "유망", "주목", "기대", "강력 매수", "비중 확대" 등 행위를 시사하는 어휘. 화면·문구·이메일·UTM 어디서도 사용 X.

**허용 표현**: "조건에 부합하는 종목", "필터 통과 항목", "지표 값이 X 인 종목".

**법적 함의**: 자본시장법 유사투자자문업 등록 회피의 핵심 — "투자 자문" 의 정의는 "특정 종목의 매매·종목·시기에 대한 자문" 인데, Speculum 은 사용자가 직접 조건을 입력하고 그 결과만 보여주므로 자문이 아닌 도구. 단, 디스클레이머·동의 모달 필수 (§5).

**Anti-pattern (금지):**
- "오늘의 추천 종목 10선" — 절대 금지
- 알고리즘 점수로 "Buy/Hold/Sell" 라벨 — 절대 금지
- 메인 화면 hero 영역의 "AI 추천" — 절대 금지

### 2.3 — Active Inspection (능동적 탐색)

**Speculum 은 의제를 정하지 않는다.** 사용자가 무엇을 볼지 정한다. 홈 화면의 default 가 "추천 종목" 이 되어선 안 됨.

**홈 화면 default 정책:**
- 사용자의 Watchlist (있으면) — 본인이 정한 의제
- 그 외 영역은 **시장 wide overview** (지수, 거래대금, 상승/하락 비율 등 의제 없는 fact)
- 화면 한 구석에 "조건 입력해 시장 탐색하기 →" 의 entry point

**Norma §2.8 의 GeoGebra-style editor 와 같은 결**: 도구가 "이걸 해보세요" 가 아니라 도구가 "당신이 원하는 것을 만들 도구를 제공" 한다.

**Anti-pattern (금지):**
- 홈에 "오늘의 인기 종목" — 인기 = 의제, 거울 아님
- "이 종목과 비슷한 종목" 추천 위젯 — 사용자가 명시적으로 요청한 게 아니면 X

### 2.4 — Point-in-Time Correctness (거울은 멀리 보지 못한다)

**과거 시점 분석 시, 그 시점에 알 수 없던 데이터를 사용하지 않는다.** Look-ahead bias 의 일급 방지.

**핵심 함의:**
1. **분기 공시 lag** — 1Q 결산은 1Q 종료 후 45일 이내 공시 (자본시장법). 즉 1Q (1-3월) 데이터는 5/15 이후에야 사용 가능. Speculum 은 모든 재무지표에 `effective_date` (그 데이터가 공시된 날짜) 를 일급 필드로 보존.
2. **정정공시** — 정정 전 값으로 계산된 historical 스크리닝은 정정 후에도 그대로 (역사 변경 X). 정정공시는 새 record 로 추가.
3. **시가총액·자사주** — 자사주 매입·소각 이벤트의 effective date 보존.
4. **종목 변경** — 합병상장·재상장·종목코드 변경 시 historical 연결.

**왜 중요한가**:
- 백테스트의 진위 결정 — look-ahead bias 가 있으면 모든 백테스트 결과가 환상.
- 학술·블로그 글의 신뢰성 — "2024년 10월에 이 조건으로 봤다면" 이 진짜 그 시점에 가능했는지.

**스코프:**
- M0: 일배치 데이터에 `effective_date` 필드 보존. UI 는 latest 만 표시.
- M1: "as of <date>" 토글 — 과거 시점 스크리닝 (PIT).
- M2: 정정공시 history view.

> ⚠ **솔직한 한계** — DART OpenAPI 가 제공하는 effective_date 가 보고서 접수일이지 실제 공시 시점(장중 vs 장후) 까진 정밀 아님. 분 단위 PIT 는 무리, 일 단위 PIT 가 현실적.

### 2.5 — Open Data Sufficiency (거울은 무료다)

**모든 핵심 기능은 공개·무료 데이터로 작동한다.** 유료 데이터 의존 금지.

**데이터 소스 정책 (§4 상세):**
- 1차 자료: DART OpenAPI (재무제표 원문), KRX 정보데이터시스템 (시세·시가총액), 한국은행 ECOS (거시지표, 환율)
- 라이브러리: FinanceDataReader (가격 시계열, 종목리스트), pykrx (KRX 공식 데이터 wrapper)
- **금지**: 네이버/다음 금융 크롤링 (약관 회색지대 — 자매 프로젝트의 "표준 우선" 원칙과 충돌), FnGuide/Quantiwise (유료)

**부산물**: 누구나 같은 데이터로 같은 결과를 재현할 수 있음 → 8기둥의 Fidelity 강화.

### 2.6 — KRX-Native (한국 시장을 비춘다)

**한국 시장에 특화. 미국·일본 등 해외 시장 미지원.** 도메인을 좁혀 깊이 확보.

**한국 시장 특수성 일급 처리:**
- **시장 분류**: KOSPI / KOSDAQ / KONEX. 보통주 / 우선주 / ETF / ETN / 리츠 / 스팩 — 카테고리별 별도 처리.
- **결산기 다양성**: 12월 결산이 대부분이지만 3월·6월·9월 결산 회사 존재. 회사별 결산월 일급 필드.
- **K-IFRS 연결/별도**: DART 재무제표 양식의 회사별 표시 차이. 사용자가 명시적으로 "연결 우선" 또는 "별도 우선" 선택.
- **공시 분류 (KIND)**: 정기공시 / 주요사항보고 / 발행공시 등.
- **휴장일·반일장**: 한국 거래소 휴장 캘린더 (`pykrx.get_market_close_dates`) 일급.
- **권리락·배당락**: corporate action 일자 보정 (§2.4 와 결합).
- **종목코드 변경**: 합병·분할·재상장 시 종목코드 변경 history 보존.

**KSIC (한국표준산업분류) 매핑**: KRX 의 업종분류와 KSIC 사이의 매핑이 모호. Speculum 은 **KRX 업종분류를 1차** 로 사용하고 KSIC 은 보조 (Sector view 가 M2 도입 시 결정).

### 2.7 — Observation over Speculation (Speculate 가 아닌 Spec)

**정량성, 서사·추측·감정 배제.** 화면에 표시되는 것은 수치와 그 수치의 정의뿐. 뉴스·공시·리포트 텍스트는 표시하지 않음 (M0~M2 범위).

**왜?**
- 뉴스·리포트는 가치 판단을 내포. 추천을 회피하는 8기둥 §2.2 와 충돌.
- 자연어 처리 영역으로 확장하면 LLM 의 hallucination 위험 + 의제 설정 위험.
- 자매 프로젝트 Norma 의 "측정 = 수치, 텍스트 아님" 결과 일관.

**예외 — 사실적 메타데이터만:**
- 종목명, 업종, 결산월, 상장일, 시가총액 등 정량·정성 fact
- DART 공시 제목 + 링크 (텍스트 자체 표시 X, 클릭 시 DART 페이지로 이동)

**M3+ 검토**: 사용자가 명시적으로 요청한 종목 1개에 대한 공시 제목 list 표시 — 의제 설정 위험 낮음.

### 2.8 — Conformance to Standards (표준 준수)

**KRX 종목 분류, K-IFRS 회계기준, KSIC 업종 코드, DART 공시 양식을 1차 자료로 사용한다.** 가공된 2차 자료에 의존하지 않는다.

자매 프로젝트와 결을 일치시킴:
- **Tessera**: DICOM 표준 준수가 모든 결정의 1순위 판단 기준
- **Norma**: Cephalometric 분석법의 명시적 출처·인용 (Park 1989, SciRep 2024 등)
- **Speculum**: DART 양식·KRX 분류·K-IFRS 의 1차 자료 우선

**구체 정책:**
- 재무제표 항목은 K-IFRS 표준 계정과목명을 우선 사용 (예: "매출액" 이 아닌 "수익(매출액)").
- 시가총액·발행주식수는 KRX 발표값을 1차로 사용 (DART 발행주식 정보는 보조).
- 업종 분류는 KRX 표준 산업분류를 1차 (KSIC 보조).
- 회계기준 변경(K-IFRS → K-IFRS 신리스 기준 등) 의 효력 발효일을 보존 → 시계열 불연속 처리.

**Multi-ID for ambiguous indicators (Norma §2.3 dual-definition 패턴):**

같은 "PER" 이라도 정의가 다양함. 단일 ID 로 묶으면 §2.1 Fidelity 위반:

| Factor ID | 정의 |
|---|---|
| `per:krx-official` | KRX 가 발표하는 공식 PER (시가총액 / 최근 연결 당기순이익) |
| `per:trailing-12m-consolidated-ifrs` | 우리가 계산: 시가총액 / Σ(최근 4 분기 연결 당기순이익), 자사주 제외 |
| `per:forward-consensus` | (M3+) 컨센서스 EPS 기반 |

Factor 식별자 자체가 "(지표, 산출 정의, 회계기준, 자사주 처리)" 의 tuple. UI 에 항상 어느 정의의 PER 인지 명시.

> 📋 **마일스톤 종료 시 의무** — 매 마일스톤 squash 직전 Momus 전방위 표준 검토 자동 실행 (자매 프로젝트 정책 일관). Conformance review work-order 템플릿은 Tessera 의 `m{n}-dicom-conformance-review.md` 와 동형으로 운영.

### 2.9 — Temporal Continuity (거울은 시간을 끊지 않는다)

**Corporate action 으로 인한 시계열 불연속을 명시적·일관된 정책으로 보정한다.**

기존 도구의 "수정주가" 가 소스마다 다르고 그 정의가 보이지 않는 문제를 정면 해결.

**Corporate action 분류:**

| 종류 | 시계열 영향 | 보정 정책 |
|---|---|---|
| **액면분할 / 액면병합** | 가격·발행주식수 변경 | 분할/병합 비율로 과거 가격 보정. 발행주식수 history 보존 |
| **무상증자** | 권리락. 가격 점프 | 권리락 비율로 과거 가격 보정 |
| **유상증자** | 가격 희석 (이론가 변경) | 보정 정책 ADR 필요 (할인율·청약 등) |
| **주식배당** | 권리락 | 무상증자와 유사 처리 |
| **현금배당** | 배당락 점프 | **기본 보정 안 함** (현금 흐름은 별도 — total return 은 옵션) |
| **합병 / 분할** | 종목 자체 변경 | §2.6 종목 lineage history |
| **자사주 매입·소각** | 발행주식수 변경 | 발행주식수 history. 시가총액 정의 영향 |

**핵심 원칙:**
- 모든 corporate action 은 `corporate_actions` 테이블에 일급 row
- 가격 시계열은 `raw` 와 `adjusted` 두 변이 모두 보존, UI 토글
- 보정 정책 자체에 버전 (예: `v1: split+rights, no dividend`) → factor pack hash 에 포함

**왜 별도 기둥인가:**
- §2.1 Fidelity 와 §2.4 PIT 사이의 빈 공간. corporate action 보정은 시계열의 **연속성** 문제이지 출처 문제도 시점 문제도 아님.
- 자매 프로젝트 Norma 의 §2.5 calibration (등방성 픽셀, view transform 비파괴) 과 같은 결 — 입력의 일관성 보장.

### 2.10 — Reproducibility (거울은 어제의 자신과 같다)

**모든 사용자 query 는 snapshot 으로 freeze 가능하다.** 오늘 본 결과 = 6 개월 후 본 결과 = 영원히 동일.

Norma 의 Run snapshot (§2.14) 패턴을 주식 도메인에 매핑.

**Screen Run snapshot — 사용자가 스크리닝 실행 시 생성되는 불변 레코드:**

```
ScreenRun
  ├─ id: run_001
  ├─ query: { conditions: [...], universe: "kospi+kosdaq보통주" }
  ├─ as_of: 2026-05-22
  ├─ data_versions: {
  │     krx_market_data: "krx-2026-05-22",
  │     dart_financials:  "dart-2026-05-21",
  │     factor_pack:      "factors-v1.2-sha256:a3f2..."
  │   }
  ├─ factor_definitions_hash: "sha256:..."  ← 그 시점의 Factor 정의 freeze
  ├─ result_codes: ["005930", "000660", ...]
  ├─ result_count: 42
  ├─ computed_at: 2026-05-22T10:23:00+09:00
  └─ computed_by: user_abc
```

**구체 메커니즘:**
- 스크리닝 결과 옆에 "Save Run" 버튼 → 위 snapshot 생성
- Watchlist 에 Run 을 저장 → 그 Run 의 결과 종목들을 시점 freeze 한 상태로 보존
- 6 개월 후 같은 Run 을 재실행하면 같은 결과 (데이터 정정 무관)
- Export = JSON pack — Norma 의 self-identifying report 와 같은 결

**무엇을 freeze 하는가:**

| 대상 | 도입 | freeze 사유 |
|---|---|---|
| query (조건들) | M0 | 사용자 의도 |
| as_of date | M0 | PIT |
| factor definition hash | M0 | 식 변경이 결과 변경 |
| data version (KRX / DART batch ID) | M1 | 데이터 정정이 결과 변경 |
| 결과 종목 코드 list | M0 | 결정성 |
| 계산자 (user_id) + 시각 | M0 | provenance |

**왜 별도 기둥인가:**
- §2.1 Fidelity (출처) 의 **시간적 확장**. 출처를 명시한 값이 시간에 따라 의미가 변하지 않음을 보장.
- §2.4 PIT (look-ahead 방지) 의 **사용자 측 보완**. PIT 가 "과거 데이터 사용 정책" 이라면 Reproducibility 는 "사용자 query 의 시간 보존".
- 자매 프로젝트 Norma 의 §2.14 Run + self-identifying report 와 결 일치.

**스코프:**
- **M0**: Screen Run schema + DB 저장. UI 는 "Save Run" 버튼만.
- **M1**: 데이터 정정 시 옛 Run 의 재현 검증 + UI 표시.
- **M2**: JSON pack export (Factor Lab 과 동시).

---

## 3. 4 + 4 핵심 뷰

> MVP 4 + 확장 4. 모든 뷰가 같은 코어 위에서 작동.

### 3.1 MVP 뷰 (M0)

| 뷰 | 설명 | 핵심 인터랙션 |
|---|---|---|
| **Screener** | 정량 조건 빌더 + 결과 테이블 | 조건 추가/제거, 결과 정렬·페이지네이션, 조건셋 저장 |
| **Stock Detail** | 개별 종목: 지표 카드 + 가격 차트 + 재무 시계열 | 차트 시간축 조정, 지표 hover → 식 표시 |
| **Compare** | 2~6 종목 나란히 비교 | 종목 추가/제거, 지표 선택, 차트 오버레이 |
| **Watchlist** | 관심종목 폴더링 | 폴더 추가/이름변경, 종목 드래그, 메모 |

### 3.2 v0.2~v0.3 뷰 (M1+)

| 뷰 | 마일스톤 | 설명 |
|---|---|---|
| **Sector / Industry** | M1 | 업종별 평균 지표 히트맵, 구성 종목 |
| **Market Overview** | M1 | 지수, 시장 폭(상승/하락), 거래대금, 외인/기관 매매동향 |
| **Factor Lab** | M2 | 사용자가 정의한 팩터(=정량식)를 저장·재사용. Norma 의 "분석법" 에 대응 |
| **Notes** | M2 | 종목별 사용자 메모 (왜 봤는지). Markdown 지원 |

---

## 4. 데이터 소스 정책

### 4.1 1차 자료 우선

| 소스 | 역할 | 빈도 | M0 도입 |
|---|---|---|---|
| **DART OpenAPI** | 분기·연간 재무제표 원문 | 분기 + 정정 | ✓ |
| **KRX 정보데이터시스템** (`pykrx`) | 시세, 시가총액, 거래량, 발행주식수, 업종 | 일 | ✓ |
| **FinanceDataReader** | 가격 시계열 보조, 종목 마스터 | 일 | ✓ |
| **한국은행 ECOS** | 환율, 금리, 거시지표 | 일 | M1 |
| **KOSIS** | 통계청 거시지표 | 월 | M2+ |

### 4.2 사용하지 않는 소스

| 소스 | 거부 사유 |
|---|---|
| 네이버 금융 / 다음 금융 크롤링 | 약관 회색지대. 8기둥 §2.8 (표준 준수) 와 충돌 |
| FnGuide DataGuide | 유료. 8기둥 §2.5 (Open Data Sufficiency) 와 충돌 |
| Quantiwise | 유료 |
| 비공식 텔레그램/오픈채팅 봇 | 출처 불명, Fidelity 위반 |

### 4.3 데이터 freshness 정책

- **장 종료 후 (~16:30 KST)**: pykrx + FinanceDataReader 일배치 → 종가, 시가총액, 거래량 갱신
- **새벽 (~03:00 KST)**: DART OpenAPI 일배치 → 신규 공시 수집, 재무제표 변경 반영
- **요청 시**: 사용자가 종목 detail 진입 시 해당 종목의 latest 공시 lazy fetch (rate limit 고려)

### 4.4 캐싱 전략

- 시세 시계열: PostgreSQL 파티션 (월별)
- 재무제표: PostgreSQL JSONB (회사·분기 단위), `effective_date` 필드 보존
- 스크리닝 인덱스: 자주 쓰는 조건의 사전 계산 결과 (M1+ 도입)
- 사용자 데이터 (Watchlist, Notes, Factor): PostgreSQL row-level (user_id 격리)

---

## 5. 법적 위치 — 정직한 설명

### 5.1 자본시장법

- **유사투자자문업 미등록.** 등록 의무가 없는 형태로 운영 (개별 종목·시기·매매에 대한 자문 X, 사용자 정의 조건의 필터링 결과만).
- **첫 진입 시 동의 모달**: "본 도구는 정보 제공 목적이며 투자 자문이 아닙니다. 표시되는 데이터·지표는 1차 자료에서 산출되었으나 정확성을 보증하지 않습니다. 모든 투자 판단·손익은 사용자 본인의 책임입니다."
- **모든 화면 footer 디스클레이머**: 위 모달의 압축 버전.
- **이메일·알림 금지 어휘**: "추천", "유망", "기대" 등 (§2.2).

### 5.2 자매 프로젝트와의 비교

- **Tessera (DICOM)** — 의료기기 SaMD 회색지대를 정직한 disclaimer 로 회피
- **Norma (Cephalometric)** — "진단 보조 의료기기 아님" 명시
- **Speculum** — "투자 자문 아님" 명시. 패턴 동일.

### 5.3 개인정보

- Google OAuth 로그인 (이메일·프로필 사진만). 별도 회원가입 폼 X.
- 사용자 데이터 (Watchlist, Notes): user_id 격리. 본인만 접근.
- 익명 통계 (집계만): 사용자 수, MAU 등. 개별 사용자 데이터 분석 X.

---

## 6. 마일스톤 (Roadmap 요약, 상세는 ROADMAP.md)

| Milestone | Version | 한 줄 정의 | 추정 |
|---|---|---|---|
| **M0** | `0.1.0` | MVP 4 뷰 + DART/pykrx 데이터 파이프라인 + 8 기둥의 핵심 절반 (Fidelity, No Advice, KRX-Native, Open Data) | 3~5 개월 |
| **M1** | `0.5.0` | Sector / Market Overview + PIT 토글 + 외인·기관 매매동향 + ECOS 거시지표 | M0 + 2~4 개월 |
| **M2** | `1.0.0` | Factor Lab + Notes + ETF/우선주/리츠 별도 모듈 + 정정공시 history | M1 + 4~6 개월 |
| **M3+** | `1.x~2.x` | 사용자 팩터 export/import + 백테스트 모듈 (PIT 위에서) + 공시 metadata 표시 | outlook |

---

## 7. 종목 유니버스 정책

### 7.1 기본 universe (M0)

- **포함**: KOSPI 보통주 + KOSDAQ 보통주
- **제외**: 스팩 (정량분석 무의미), KONEX (거래량 너무 적음), ETF/ETN/리츠/우선주 (별도 모듈 — M2)
- **필터로 노출**: 관리종목, 거래정지, 투자주의/경고/위험 종목

### 7.2 확장 universe (M2)

- 별도 모듈로 ETF·우선주·리츠 도입. 각각의 정량 지표 정의가 다름:
  - **ETF**: 기초자산, NAV, 괴리율, AUM
  - **우선주**: 보통주 대비 할인율, 배당수익률
  - **리츠**: 임대수익률, NAV 대비 가격

### 7.3 종목 변경 history

- **합병상장 / 재상장 / 종목코드 변경**: 종목 마스터에 history 필드. 과거 시점 스크리닝 시 그 시점의 종목코드로 매핑.
- **상장폐지**: 폐지일 보존. 과거 시점 스크리닝에 포함 가능 (survivorship bias 방지).

> ⚠ **Survivorship bias 의 함정**: "현재 상장 종목" 만 historical 스크리닝하면 과거 우상향 결과가 환상이 됨 (상장폐지된 부실 종목 누락). Speculum 은 폐지 종목도 historical universe 에 포함.

---

## 8. 기술 스택 (요약, 상세는 ARCHITECTURE.md)

### 8.1 Frontend

- **Next.js 14** (App Router) + TypeScript
- **Tailwind CSS** + **shadcn/ui** (Radix UI 기반)
- **TanStack Query** (서버 상태 관리)
- **Lightweight Charts** (TradingView 오픈소스 — 차트)
- **TanStack Table** (대용량 테이블 가상화)
- **Zustand** (클라이언트 로컬 상태)

### 8.2 Backend

- **Python 3.12** + **FastAPI** + **SQLAlchemy 2**
- **PostgreSQL 16** (JSONB + 파티셔닝)
- **Redis** (캐시·rate limiting — M1+)
- **APScheduler** 또는 **Celery** (배치 잡 — M1+)

### 8.3 Data Layer

- **FinanceDataReader** — 가격 시계열, 종목 마스터
- **pykrx** — KRX 공식 지표
- **OpenDartReader** 또는 직접 DART API — 재무제표 원문 (M0 결정)

### 8.4 Auth

- **NextAuth.js** + Google OAuth

### 8.5 배포

- **Frontend**: Vercel
- **Backend**: Fly.io 또는 Railway (Postgres 동봉)
- **모니터링**: Sentry (에러), Plausible (analytics, GA 미사용 — 개인정보 우려)

---

## 9. 자매 프로젝트와의 패턴 매핑

> Norma 의 14 기둥 / Tessera 의 표준 우선 / 양쪽의 self-identifying report 패턴을 Speculum 에 어떻게 매핑하는가.

| Norma / Tessera 패턴 | Speculum 의 대응 |
|---|---|
| Norma §2.1 Primary / Derived / Measurement DAG | Factor Lab (M2) — Primary 지표 (재무제표 원문 필드) → Derived (PER = price / EPS) → Composite (multi-factor score). DAG invalidation 동일. |
| Norma §2.2 Definition / Instance 분리 | Factor Definition (immutable) / Stock × Date 인스턴스 (mutable 가격 + 분기 재무) 분리 |
| Norma §2.3 Identity 3-tier (canonical / community / custom) | 지표 Identity — canonical (PER/PBR/ROE 등 ~50개 빌트인) / community (사용자 공유 팩터 pack) / custom (개인 정의) |
| Norma §2.14 self-identifying report | Screener 결과 export = `{조건, 결과 종목, 데이터 hash, 기준일}` JSON. 5년 후 같은 hash 검증 가능 |
| Norma immutable + 버전 관리 | 팩터 pack v1.0 / v1.1 / v2.0 — fork, hash 검증 |
| Tessera DICOM 표준 우선 | DART 양식 + KRX 분류 + K-IFRS 우선 (§2.8) |
| Norma §2.5 calibration | 통화·단위 일관성 — KRW 기본, USD 환산은 ECOS 환율 사용 |
| Tessera Momus 마일스톤 검토 | 매 마일스톤 squash 직전 표준 검토 (자매 프로젝트 정책 일관) |

---

## 10. 솔직한 limitations (잠정)

- **장중 실시간 시세 없음** — 무료 데이터 한계
- **해외 시장 미지원** — 도메인 좁힘
- **알고리즘 매매 연동 없음** — 정보 제공 도구의 경계
- **세금 계산 없음** — 양도소득세·증권거래세 등 (M3+ 검토)
- **포트폴리오 회계 없음** — 보유 수량·평단·실현/미실현 손익 — Watchlist 의 단순 확장이지만 가격 추적 vs 회계는 다른 영역, M3+ 검토
- **백테스트 본격 모듈 없음 (M0~M2)** — PIT 데이터 layer 는 M1 에 준비되지만 본격 backtest engine 은 M3+
- **AI/LLM 미사용 (M0~M2)** — 8기둥 §2.7 (Observation over Speculation) 와 충돌 위험. M3+ 에 사용자 요청 종목 1개에 대한 공시 요약 정도만 검토

---

## 11. 핵심 ADR 목록 (M0 진입 전 결정 필요)

Metis 사전 검토 결과, M0 진입 전 별도 ADR 문서로 결정해야 할 항목들. 상세는 `M0_PLAN.md` 의 T1~T?? task 참조.

| ADR | 결정 항목 | 우선순위 |
|---|---|---|
| ADR-001 | 가격 보정 정책 (수정주가 정의, 배당 미포함 default) | H |
| ADR-002 | Factor / Fact 데이터 모델 (Definition / Snapshot / Source Citation 3-layer) | H |
| ADR-003 | Data Source Adapter 패턴 (FDR / pykrx / DART / KRX canonical schema) | H |
| ADR-004 | 시가총액·EPS·PER 산출 정의 (자사주 처리, 우선주 별도) | H |
| ADR-005 | K-IFRS 연결 vs 별도 기본 선택 (연결 우선 + 사용자 토글) | H |
| ADR-006 | 법률 검토 (유사투자자문업 경계, 데이터 라이선스, 개인정보보호) | H |
| ADR-007 | 디폴트 UI 규약 (디폴트 정렬·하이라이트·푸시 정책) | H |
| ADR-008 | As-of date picker 가 일급 UI element | H |
| ADR-009 | Corporate Action 분류 + 보정 정책 (§2.9 구체화) | H |
| ADR-010 | 홈 화면 정체성 (Active Inspection vs 사용성 trade-off) | H |
| ADR-011 | Watchlist scope (알림 기능 포함 여부) | H |
| ADR-012 | KRX/K-IFRS Conformance Review work-order 템플릿 (Tessera 동형) | M |
| ADR-013 | ETF/우선주 v0.2 분리의 통계적 한계 명시 (UI 디스클로저) | M |
| ADR-014 | Screen Run snapshot DB schema (§2.10 구체화) | M (M0~M1 경계) |

각 ADR 은 `docs/adr/` 디렉토리에 `adr-NNN-{slug}.md` 파일로 작성. Tessera 의 ADR 문화 일관.
