# Speculum — M0 v0.1.0 Plan

> **Project**: Speculum — 한국 주식 시장의 검경 (Korean Equity Market Quantitative Inspector)
> **Sisters**: Tessera (DICOM 표준 준수), Norma (Cephalometric 분석법 빌더)
> **Milestone**: M0 v0.1.0 — MVP 4 뷰 + 1차 자료 파이프라인
> **Created**: 2026-05-22
> **Status**: planning — ADR 결정 대기

---

## Summary

한국 KOSPI/KOSDAQ 보통주를 정량 지표로 비추는 데이터 탐색기 웹앱의 첫 마일스톤. MVP 4 뷰 (Screener / Stock Detail / Compare / Watchlist) + DART/pykrx/FDR 1차 자료 파이프라인 + 10 기둥의 핵심 절반 구현.

**핵심 원칙**: 추천하지 않는다. 사용자가 정의한 조건의 필터링 결과만 표시. 모든 값에 식·출처·기준일 동반. PIT correctness + corporate action 보정 + Screen Run snapshot 으로 시계열 무결성 + 재현 가능성 보장.

## Acceptance Criteria

1. **기능 (8)**: 4 뷰 작동 + Google OAuth + 동의 모달 + 종목 검색 + 조건셋 저장 + Screen Run 저장
2. **데이터 (6)**: KOSPI/KOSDAQ ~2,500 보통주 + 분기 재무제표 4 분기 이상 + 5 년 가격 시계열 + 휴장일 정확 + effective_date 보존 + 정정공시 history
3. **10 기둥 conformance (10)**: Fidelity / No Advice / Active Inspection / PIT / Open Data / KRX-Native / Observation / Conformance / Temporal Continuity / Reproducibility — 각 항목 검증 가능
4. **법적 (4)**: 동의 모달 + footer + 법률 자문 반영 + 데이터 라이선스 + 개인정보처리방침
5. **운영 (3)**: 일배치 7 일 무결 + Sentry alert + adapter 충돌 감지

## Implementation Steps

### Phase 0 — ADR (T0~T12)
- T0: 프로젝트 scaffold (Next.js + FastAPI + Postgres + Docker compose)
- T1~T12: 12 개 H 우선순위 ADR 작성
  - T1 가격 보정 정책
  - T2 Factor/Fact 3-layer 데이터 모델
  - T3 Data Source Adapter 패턴
  - T4 시가총액·EPS·PER 산출 정의
  - T5 K-IFRS 연결/별도
  - T6 법률 자문 (유사투자자문업 / 라이선스 / 개인정보)
  - T7 디폴트 UI 규약
  - T8 As-of date picker
  - T9 Corporate Action 보정
  - T10 홈 화면 정체성
  - T11 Watchlist scope
  - T12 Conformance review work-order 템플릿

### Phase 1 — Data Layer (T13~T23)
- T13 Postgres schema
- T14 FDR adapter
- T15 pykrx adapter
- T16 DART adapter + dart_account_mapper
- T17 KRX 영업일 캘린더
- T18 KRX 일배치 (16:30 KST)
- T19 DART 일배치 (03:00 KST)
- T20 Corporate Action 보정 엔진
- T21 PIT Enforcer
- T22 Factor Definition + 빌트인 ~30 factor pack
- T23 Source Citation 의무 강제

### Phase 2 — Backend API (T24~T30)
- T24 FastAPI + NextAuth JWT
- T25 /api/stocks/* endpoint
- T26 /api/screen endpoint
- T27 /api/compare endpoint
- T28 /api/watchlist + /api/screener_sets
- T29 금지 어휘 검사 + CI 게이트
- T30 Screen Run snapshot DB schema

### Phase 3 — Frontend MVP 4 뷰 (T31~T40)
- T31 Next.js scaffold + Tailwind + shadcn/ui + NextAuth
- T32 동의 모달 + footer + ESLint rule
- T33 Source Attribution component
- T34 As-of date picker
- T35 홈 화면 (ADR-010)
- T36 Screener 뷰
- T37 Stock Detail 뷰
- T38 Compare 뷰
- T39 Watchlist 뷰
- T40 Save Run 버튼

### Phase 4 — Polish + Release (T41~T47)
- T41 5 CI 게이트
- T42 Playwright E2E
- T43 백엔드 통합 테스트
- T44 OSS infra (README ko+en, LICENSE, CHANGELOG, CONTRIBUTING, SECURITY)
- T45 M0 conformance review (Momus)
- T46 Conformance fix 반영
- T47 Squash merge develop + tag v0.1.0

## Critical Files

- `docs/CONCEPT.md` — 10 기둥 + 한 줄 정의 + 차별화 포지셔닝
- `docs/ROADMAP.md` — M0~M3+ timeline
- `docs/ARCHITECTURE.md` — 기술 스택 + 데이터 모델 개요
- `docs/M0_PLAN.md` — 본 plan 의 origin (Task 분해 + Acceptance 상세)
- `docs/adr/adr-*.md` — Phase 0 산출물
- `server/app/services/pit_enforcer.py` — 8기둥 §2.4 의 핵심
- `server/app/adapters/` — FDR / pykrx / DART / KRX adapter
- `server/app/services/forbidden_words.py` — 8기둥 §2.2 강제
- `server/builtin-packs/factors/v1.0.json` — 빌트인 ~30 factor

## Risks

- **법률 자문 결과 큰 변경**: T6 을 Phase 0 에 배치, 코드 작성 전 결정
- **DART rate limit**: throttling + backoff + 키 rotation
- **DART 양식 회사별 차이**: dart_account_mapper + override + alert
- **pykrx/FDR 충돌**: 출처 우선순위 ADR + adapter 별 citation
- **Look-ahead bias silent regression**: PIT Enforcer + CI 게이트
- **Active Inspection vs 빈 화면**: ADR-010 단단 결정

## Verification

- Acceptance §2.1~§2.5 모든 항목 통과
- Momus M0 conformance review (KRX/K-IFRS/DART/자본시장법) 통과
- 7 일 운영 통계 무결
- 첫 사용자 ~5 명 feedback 수집

## Notes

- 자매 프로젝트 Tessera 의 ADR 문화 + work-order 패턴 + 마일스톤 종료 시 Momus conformance review 의무를 그대로 차용
- Norma 의 Definition/Instance/Run 3-layer 와 self-identifying report 패턴을 Factor Definition / Stock Snapshot / Screen Run snapshot 으로 매핑
- 10 기둥은 v0.1 초안 8 기둥에서 Metis 사전 검토 결과 §2.9 Temporal Continuity + §2.10 Reproducibility 추가
- 차후 마일스톤 (M1: Sector/Market Overview + PIT 토글 + ECOS, M2: Factor Lab + Notes + ETF/우선주/리츠) 은 별도 plan 문서
