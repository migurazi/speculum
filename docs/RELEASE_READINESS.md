# Speculum 출시 준비 상태 (M0~M9 종합, 2026-06-08)

코드 마일스톤 M0~M9 완료(develop ahead 7 미푸시). 본 문서는 출시 전 해결할
release blocker, 운영 cycle TODO, 영구 수용 known-limit을 종합한다.

## (A) Release Blocker — 코드 외 (자문/외부, 운영 노출 전 필수)

| ID | 항목 | 근거 | 의존 마일스톤 |
|----|------|------|------|
| B-1a | 변호사 자문(유사투자자문업·개인정보·KRX 라이선스) | ADR-0006 D9 | M0~M3 운영·M4·M6 |
| B-1b | 세무사+변호사 자문(세금계산기·양도세) | ADR-0030 D6 | M3 #5·M7 세후 |
| B-1c | LLM 운영 노출(B-1a 후 env opt-in) | ADR-0031 D6 | M3 #6 AI 추출 |
| B-1d | M4 제3자 factor 콘텐츠 자문 재발행 | ADR-0032 D7 | M4 community pack |
| B-1e | M6 publisher namespace 자문 판정(조건부) | ADR-0034 D8 | M6 v2 pack |
| B-2 | Survivorship backfill 실데이터 수집 | ADR-0027·`batch/survivorship_backfill.py` | M3 #1·M5 backtest |
| B-3 | Google OAuth 실 credential 발급 | ADR-0021·M2 T68 | M2 NextAuth |

**핵심**: B-1a~B-1e는 변호사·세무사 1~2회 자문 묶음으로 동시 해소(유사투자자문업
경계가 다수 기능의 운영 노출 게이트). 코드는 게이트·디스클레이머·스키마로 방어 완료.

## (B) 운영 확정 TODO — 코드 완료, 운영 데이터/설정/검증 필요

### env / API Key (prod 설정)
`ANTHROPIC_API_KEY`+`SPECULUM_ENABLE_LLM_FACT_EXTRACTION`(M3#6, 미설정=fail-closed)·
`DART_API_KEY`·`ECOS_API_KEY`·`KOSIS_API_KEY`·`FSC_API_KEY`(공공데이터 serviceKey)·
`AUTH_SECRET`(prod gate 구현됨)·`SPECULUM_DATABASE_URL`·`SPECULUM_ENV=prod`.

### provisional / 미검증 식별자
- KOSIS itmId/objL1 잠정값(`kosis_daily.py`·`db_field_provider.py` — 3계층 ⚠ 명시) + 반기/분기 PRD_DE 형식 → 운영 KOSIS_API_KEY 메타·실 응답 확정.
- FSC 종목→crno 매핑 + 배당 배치(M7 #2 후속, `fsc_dividend_adapter.py`).

### 미구현/미연결 배치
- ECOS 일배치(`ecos_daily.py` 미구현 — T64, adapter/repo는 완성).
- KOSIS/KRX/DART 배치 scheduler 미통합(CLI manual 호출). DART 정정공시 chain 미구현.

### 실데이터 운영 cycle
ETF NAV 실데이터(M2, KRX endpoint 차단)·ECOS/KOSIS/DART 실 수집·KRX 영업일 캘린더 갱신.

## (C) Known-limit — 구조적/영구 한계 (설계 수용, 문서화 완료)

### 재현성
- Corporate action(배당 포함) batch cutoff freeze 불가(ADR-0033 D4·ADR-0035 D8) — 정정 시 reproduce 한계, 디스클로저 처리.
- Cross-instance run 재현 불가(ADR-0032 D6) — batch_id 타 인스턴스 부재.
- ECOS/KOSIS vintage 미제공(ADR-0036 D3) — ingested_at 근사.
- KRX 영업일 캘린더 단년 범위 — calendar-days 보수 fallback.

### 범위 영구 제외 (8기둥 의사결정)
- 세후 total return·양도세(세무 자문)·forward 예측(§2.7)·가중 최적화/리스크 패리티(§2.2)·MyData 계좌연동(자본시장법)·cross-instance 중앙 registry(인프라)·v1→v2 자동 마이그레이션(content_hash 파손)·1:1 챗봇/개별 자문(ADR-0006 D2)·pack 자동 순위/합산 점수(§2.2).

## (D) Git 상태
develop ahead 7 미푸시(M3~M9: d19e850·ca7fb99·c46b84c·81fc55d·d3339d3·aaabc0b·510b99e).
push는 사용자 명시 지시 시.

## (E) Viability 냉정 평가 (oracle 심층 리뷰 2026-06-08)

**현 상태 = "8기둥 게이트·엔지니어링 골격은 실재하나, 실데이터 파이프라인 마지막
1마일이 미완이라 실데이터 적재 후 동작이 증명된 정교한 골격"** — "코드 완료"(M0~M9)와
"데이터 흐르는 제품" 사이 간극이 핵심.

### 실데이터 1마일 gap (켜면 화면이 빈다 — 치명)
- **ECOS 일배치 부재**: `server/batch/ecos_daily.py` 파일 자체 없음(adapter/repo만) → 거시지표 빈값.
- **DART corp_code 빈 매핑**: `corp_code_mapping.py` 운영 source(corpCode.xml fetch+cache) 미구현 → 재무 배치 실행해도 종목 미발견 → **PER/PBR/ROE 등 재무 factor 전부 N/A**.
- **scheduler 전무**: krx/dart/kosis 배치 모두 manual CLI 호출(APScheduler 미통합) → 무인 운영 불가.
- **실 검증 경로 = 일배치(`batch/`) 실데이터 적재 후 운영 DB 기준**("정확성 보증 아님" 코드 명시).

### 테스트 green ≠ 실 동작
- 실 API 테스트(`test_*_real.py`)는 전부 `@pytest.mark.integration` → **기본 deselect**, key/network 없으면 skip. server ~2339 green이 실 DART/KRX/ECOS/KOSIS를 **한 번도 호출 안 함** → pykrx 컬럼·DART account·KOSIS itmId schema drift 무방비(green인 채 운영서 깨질 수 있음).

### 핵심 흐름 완결성
- 종목 상세: seed 완결, 실데이터는 재무 N/A로 반쪽. Screen: N+1 자인(`screen.py` — 실 universe ~2500종목 성능 미검증). Factor Lab: 상대적 완성도 높음. Backtest: survivorship 미수집으로 핵심 가치(PIT 신뢰성) 미실현. 거시: ECOS 배치 부재로 빈값. 홈 Watchlist: placeholder.

### 강점 (진짜 구현)
8기둥 코드화 실재(citation FK 강제·ForbiddenWords 미들웨어 실설치·PIT 런타임 게이트)·SQL e2e 테스트(FK·SAVEPOINT 실 제약)·재현성/파생필드 단일출처 규율·법적/철학 일관.

### viability 우선 보완 3 (제품화 조건)
1. **실데이터 1마일**: DART corpCode.xml fetch+cache(corp_code) + `ecos_daily.py` + 최소 scheduler(cron). 없으면 "제품" 아님.
2. **실 API nightly CI**: `-m integration` 실 키 1회 강제 → schema drift를 green과 연동.
3. **Screen precompute(stock_snapshots)**: 실 universe 스크리닝 N+1 제거.

## 출시 우선순위
1. **반드시(blocker)**: ADR-0006+ADR-0030 자문 묶음 · survivorship backfill 실수집 · OAuth credential.
2. **제품화(viability)**: 실데이터 1마일(corp_code·ecos_daily·scheduler) · 실 API CI · Screen precompute.
3. **운영 cycle**: env/API key 설정 · FSC 배치 · KOSIS provisional 확정 · 캘린더 갱신.
4. **영구 수용**: 재현성 known-limit(디스클로저) · 범위 제외(설계).
