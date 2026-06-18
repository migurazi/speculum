# Speculum — Roadmap

> **본 도구는 정보 제공 목적의 정량 데이터 탐색기이며 투자 자문이 아닙니다.** 자세한
> disclaimer 는 [CONCEPT §5](CONCEPT.md#5-법적-위치--정직한-설명) 참조.

본 문서는 M0 → M1 → M2 → M3+ 의 통합 timeline 단일 view. 각 milestone 의 상세 task 분해는 별도 `M*_PLAN.md` 참조.

---

## 1. Timeline 한눈에 보기

```
2026-Q3    2026-Q4~2027-Q1    2027-Q2~Q3        2027-Q4+
v0.1.0     v0.5.0             v1.0.0            v1.x~2.x
M0         M1                 M2                M3+
[MVP]      [확장 universe]    [Factor Lab]      [outlook]
   │           │                  │                   │
   ▼           ▼                  ▼                   ▼
4 뷰        Sector / Market    Factor Lab         Backtest
DART/KRX   PIT 토글            Notes              Pack share
일배치      ECOS 거시지표       ETF/우선주/리츠     공시 metadata
```

| Milestone | Version | 시점(추정) | 추정 기간 | 상태 |
|---|---|---|---|---|
| **M0** | `0.1.0` | 2026-08~10 | 3~5 개월 | 코드 구현 완료 (v0.1.0 태그 없이 v1.0.0 통합, release blocker: 자문) |
| **M1** | `0.5.0` | M0 + 2~4 개월 | 2~4 개월 | 코드 구현 완료 (잔여: T53/T54 backfill) |
| **M2** | `1.0.0` | M1 + 4~6 개월 | 4~6 개월 | 코드 구현 완료 (tag v1.0.0 부착, ETF 데이터 deferred) |
| **M3** | `1.x` | M2 + ? | ? | **구현 완료 (release blocker: 자문)** |

> **현황 종합은 §8 (실측 검증 2026-06-18) 참조.** M0~M9 전 마일스톤이 squash `dbeee96` 계열로 develop 에 통합·`v1.0.0` 태그 부착됨. 위 "추정 기간/시점" 은 최초 기획 시 추정이며 실제 진행과 무관.

---

## 2. M0 v0.1.0 — MVP 4 뷰 + 1차 자료 파이프라인

**한 줄 정의**: 4 핵심 뷰(Screener / Stock Detail / Compare / Watchlist) 위에서 KOSPI/KOSDAQ 보통주의 정량 지표 ~30 개를 정직하게 비춰 본다. DART/KRX 1차 자료 파이프라인이 단단히 작동.

### 2.1 주요 deliverable

- **데이터 파이프라인**:
  - 종목 마스터 (KRX) 일배치 — 신규 상장·폐지·종목코드 변경 history 보존
  - 가격·시가총액·거래량 (pykrx + FinanceDataReader) 일배치
  - 분기 재무제표 (DART OpenAPI) 일배치 — `effective_date` 보존
  - 휴장일 캘린더 (pykrx)
- **지표 ~30 개**: PER, PBR, PSR, ROE, ROA, 부채비율, 시가총액, 거래량, 거래대금, 거래대금 회전율, 이동평균 (5/20/60/120일), RSI(14), MACD, 52주 신고/신저가 대비, BPS, EPS, 배당수익률(전년기준), 매출액·영업이익 (YoY 분기), 영업이익률, 부채비율 — DART/KRX 1차 자료에서 산출
- **MVP 4 뷰**:
  - Screener: 조건 빌더 + 결과 테이블 (가상화)
  - Stock Detail: 지표 카드 + 가격 차트 + 재무 시계열 표
  - Compare: 2~6 종목 나란히 + 차트 오버레이
  - Watchlist: 폴더링 + 메모 기본
- **Auth**: Google OAuth (NextAuth)
- **법적·UX**: 첫 진입 동의 모달 + 모든 화면 footer disclaimer + 금지 어휘 lint
- **8 기둥 준수**:
  - §2.1 Fidelity — 모든 값 hover → 식·출처·기준일
  - §2.2 No Advice — 금지 어휘 자동 검사
  - §2.5 Open Data — 무료 데이터만
  - §2.6 KRX-Native — KOSPI/KOSDAQ 보통주, K-IFRS 연결/별도 선택
  - §2.8 Conformance — KRX·DART 양식 1차 자료

### 2.2 Acceptance 요약

자세한 항목은 `M0_ACCEPTANCE.md` (작성 예정).

- **기능 (8)**: 4 뷰 각각 기본 사용 + 종목 검색 + Watchlist CRUD + 조건셋 저장 + 차트 시간축 조정
- **데이터 (6)**: KOSPI/KOSDAQ 전 보통주 마스터 ✓, 분기 재무제표 4 분기 이상 ✓, 가격 시계열 5 년 이상 ✓, 휴장일 정확 ✓, `effective_date` 보존 ✓, 정정공시 새 record 추가 (history 보존)
- **기둥 (5)**: 모든 값에 출처·식 표시 ✓, 금지 어휘 0 ✓, 무료 데이터만 ✓, K-IFRS 연결/별도 명시 ✓, DART/KRX 1차 자료 ✓
- **법적 (3)**: 동의 모달 ✓, footer disclaimer ✓, 유사투자자문업 회피 검토 ✓
- **UX (3)**: 4 뷰 모바일 반응형 ✓, 종목 검색 250ms 이내 ✓, 스크리닝 1만 종목 결과 60fps ✓

### 2.3 Plan 문서

[M0_PLAN.md](M0_PLAN.md) — T0~T?? task 분해

---

## 3. M1 v0.5.0 — 확장 뷰 + PIT + 거시지표

**한 줄 정의**: M0 의 1차 자료 위에 Sector·Market Overview 두 뷰가 더해지고, Point-in-Time 토글(과거 시점 스크리닝)이 8기둥 §2.4 의 약속을 실현. 외인·기관 매매동향 + ECOS 거시지표 도입.

### 3.1 주요 deliverable

- **Sector / Industry 뷰**: 업종별 평균 지표 히트맵, 구성 종목, KRX 업종분류 1차 + KSIC 보조
- **Market Overview 뷰**: 지수, 시장 폭(상승/하락 비율), 거래대금, 외인/기관 순매수
- **PIT 토글**: "as of <date>" — 과거 시점 스크리닝. `effective_date` 인덱스 기반.
- **ECOS 통합**: 환율 (USD/KRW), 금리 (기준금리, 국고채), 거시지표
- **정정공시 history**: M0 의 "새 record 추가" 위에 UI 표시 추가
- **사용자 데이터 export**: Watchlist + 조건셋 JSON export/import

### 3.2 신규 기둥 영역

- §2.4 Point-in-Time Correctness 의 본격 실현
- §2.6 KRX-Native 의 corporate action 보정 (액면분할·무상증자 권리락) — 시계열 정합성

---

## 4. M2 v1.0.0 — Factor Lab + Notes + 확장 universe

**한 줄 정의**: Norma 의 "분석법 빌더" 패턴이 주식에 적용. 사용자가 정량 팩터를 정의·저장·공유. ETF·우선주·리츠 별도 모듈로 universe 확장.

### 4.1 주요 deliverable

- **Factor Lab**:
  - Primary 지표 (재무제표 원문 필드) → Derived (PER, ROE 등) → Composite (multi-factor score) 의 DAG (Norma §2.1 패턴)
  - JSON pack 으로 export — community 공유 가능
  - Identity 3-tier: canonical (~50 빌트인) / community (공유 pack) / custom
- **Notes**: 종목별 사용자 메모 (Markdown)
- **ETF / 우선주 / 리츠 별도 모듈**:
  - ETF: 기초자산, NAV, 괴리율, AUM
  - 우선주: 보통주 대비 할인율, 배당수익률
  - 리츠: 임대수익률, NAV 대비 가격
- **Self-identifying export**: Screener 결과 = {조건 hash, 데이터 hash, 기준일} JSON. 재현 가능
- **Pack version 관리**: Factor pack v1.0/v1.1, immutable hash (Norma 패턴)

### 4.2 신규 기둥 영역

- Norma §2.14 self-identifying report 의 주식 응용
- 9번째 기둥 후보: Reproducibility freeze (Run snapshot 동등물)

---

## 5. M3 v1.x — outlook 6항목 구현 완료 (release blocker: 자문)

ROADMAP §5 의 6개 후보를 **단일 M3** 로 확정·구현 완료(2026-06-02). 상세 task 분해 + 가드레일은
[M3_PLAN.md](M3_PLAN.md). 각 항목은 ADR 로 8기둥 경계를 명문화하고 visual-gate/디스클레이머
게이트로 강제했다(기존 ForbiddenWordsGuard·PackRegistry freeze 인프라 재사용).

| # | 항목 | ADR | 상태 |
|---|------|-----|------|
| 2 | **공시 metadata 표시** | ADR-0026 | 완료 — 제목·시각·DART링크만, 본문 0, on-demand 1종목 |
| 1 | **백테스트 모듈** | ADR-0027 | 완료 — PIT rebalance 순회·거래비용 강제·survivorship 디스클로저·grayscale 출력게이트·freeze |
| 3 | **Factor pack community** | ADR-0028 | 완료 — visibility 공유·USER_SHARED 게이트·중립정렬·import content_hash fail-loud |
| 4 | **Portfolio 회계** | ADR-0029 | 완료 — 거래내역 사실 회계·회계≠평가 분리·등락색0·세전·수동입력 |
| 5 | **세금 계산** | ADR-0030 | 완료(증권거래세) — 단순 산식·디스클레이머 게이트. 양도세는 자문 후 유보 |
| 6 | **AI 통합** | ADR-0031 | 완료 — 구조화 사실추출(요약 아님)·LLM 출력 게이트·LLM adapter 추상화 |

### release blocker (운영 노출 전 필수)
1. **세무사+변호사 자문** (ADR-0030 D6 + ADR-0006 D9) — 세금(#5) 양도세·세율 정확성·Portfolio 손익 표시.
2. **LLM 운영 연동 + Momus §2.7 검토** (ADR-0031 D6) — AI(#6) 운영 LLM adapter·sentiment 어휘 보강.
3. **백테스트 survivorship 소급 backfill** (ADR-0027) — 폐지 종목 과거 OHLCV 데이터 운영 cycle.

### M4+ 향후 검토 (M3 범위 외)
- 백테스트 가중 최적화/리스크 패리티 (§2.2 — 영구 범위 외 가능성)
- 증권사 계좌 연동(MyData) — ADR-0029 D4 에서 기각, 재검토 시 자본시장법·개인정보 신중

---

## 6. 마일스톤 종료 시 의무 — Momus 표준 검토

자매 프로젝트 (Tessera) 정책 일관:

> 매 마일스톤 squash 직전 Momus 전방위 검토 자동 실행. 검토 항목:
> - 8 기둥 준수도 (특히 Fidelity, No Advice, Conformance)
> - 한국 자본시장법 준수 (유사투자자문업 등록 경계)
> - 데이터 무결성 (PIT, corporate action, survivorship)
> - 자매 프로젝트와의 패턴 일관성

---

## 7. 비전 — 5년 후의 Speculum

- 한국 정량 투자 커뮤니티의 "open format" 표준이 되어 있다
- 블로거·연구자가 자신의 팩터를 Factor Lab pack 으로 git 에 올리고 다른 사람이 import 해서 재현
- 백테스트 결과의 신뢰성이 PIT 데이터 layer 덕분에 환상이 아닌 것이 확인됨
- 누구도 "Speculum 이 추천한 종목" 이라 부르지 않음 — Speculum 은 추천하지 않으므로

---

## 8. 구현 현황 종합 (실측 검증 2026-06-18)

> 각 마일스톤 plan 문서에 **세부 체크리스트**를 추가하고, 코드 적대적 검증(문서의 "완료" 표기를 신뢰하지 않고 실제 코드 대조)으로 표기. 마일스톤별 상세는 각 `M*_PLAN.md` 의 "구현 현황 체크리스트" 섹션 참조.

| 마일스톤 | 테마 | 코드 상태 | 실 갭 (코드 없음/계획과 차이) |
|---|---|---|---|
| M0 | MVP 4뷰 + 1차 파이프라인 | 구현 완료 | AC-F-03 Screener 가상화 미구현, AC-O-02 SentryAlertHandler 미구현, 캘린더 2024 단년만 |
| M1 | 확장뷰 + PIT + ECOS | 구현 완료 | T53 list.json backfill 스크립트 부재(rcept_no 로 설계 대체), T54 기존 row 소급 정밀화 backfill 미구현 |
| M2 | Factor Lab + 멀티유저 + universe | 구현 완료 | ETF NAV/괴리율/AUM 데이터 어댑터 부재(R4 deferred) |
| M3 | outlook 6종(백테스트·Portfolio·세금·AI 등) | 구현 완료 | 없음(명칭 drift 1건: `list_public`) |
| M4 | Open Format / Community pack | 구현 완료 | #1 client(Node) cross-runtime JCS conformance fixture 부재(known-limit 문서화), `imported_at/from` 컬럼 미추가 |
| M5 | 재현성·신뢰성 검증 layer | 구현 완료 | `tools/verify_run.py` 미구현, 후보 어휘(초과수익/승률) 미추가(deferred) |
| M6 | Factor pack identity v2 (publisher namespace) | 구현 완료 | 없음 |
| M7 | Total Return + 배당 layer | **부분** | ⚠ #2 배당 수집이 DART→FSC 로 선회 + **배치 미배선**(실 배당 적재 경로 부재), #6 차트 total-return 3-변이 토글 미구현, `close_price_total_return` field 부재 |
| M8 | AI 운영 마일스톤 (ADR-0031) | 구현 완료 | 운영 LLM 키만 외부 blocker |
| M9 | KOSIS 거시지표 | 구현 완료 | `kosis_leading_index` field 부재, KOSIS 전용 vintage PIT 테스트 부재 |

### 정직한 결론
- **대부분의 마일스톤은 코드·테스트가 실재**한다. 단 메모리/문서의 "100% 완료" 는 **부정확** — 위 "실 갭" 들은 실제 코드에 없거나 계획과 다르다.
- **가장 큰 doc↔code 괴리 = M7**: 배당 데이터 실 적재 배치가 배선되지 않았고(adapter 코어만), 차트 total-return 토글이 미구현이다. "완료" 로 기록돼 있었으나 실제로는 미완.
- **외부 blocker(코드 무관)**: 변호사/세무사 자문(ADR-0006/0030), 운영 데이터 적재(DART/ECOS/KOSIS/FSC API 키), 운영 LLM 키(ADR-0031), survivorship 실데이터 backfill.
- **순수 코드 잔여 작업 후보**: M7 배당 배치 배선 + 차트 3-변이 토글, M5 `tools/verify_run.py`, M9 `kosis_leading_index`, M4 client JCS fixture, M0 Screener 가상화·SentryAlertHandler.
