# ADR-0036 — KOSIS 거시지표 도입 (M9)

- **Status**: ACCEPTED (2026-06-07)
- **Context milestone**: M9
- **선행**: ADR-0003(데이터 출처), ADR-0007 D5(거시지표 factor 입력 경계), CONCEPT §4.1
  (KOSIS M2+ 유보)·§2.5(Open Data)·§2.8(Conformance). plan=`docs/M9_PLAN.md` rev1
  (Momus R1 REJECT → 5건 흡수 → R2 OKAY 착수 가능).

## Context

CONCEPT §4.1은 통계청 KOSIS 거시지표를 "월 M2+"로 유보했다. M0~M8에서 거시 소스는 ECOS
(한국은행: 금리·통화·환율)만 구현됐다. M9는 KOSIS(통계청: 물가·고용·산업·인구)를 ECOS와
**대칭 신규 소스**로 도입한다 — 기존 macro 인프라(테이블·repository·field resolver) 최대 재사용.

## Decisions

### D1 — ECOS 거시 인프라 재사용 (신규 ORM/Alembic 0)
`macro_indicators` 테이블·`MacroIndicatorRepository`(SQL/Fake)·`_resolve_macro_indicator`
(generic, kind="macro_indicator")·`SourceKind.KOSIS`(이미 존재)를 **무변경 재사용**. ECOS row와
동일 테이블에 `indicator_id` prefix로 namespace 분리 — KOSIS 규약 `"kosis/{orgId}/{tblId}/{itmId}"`
(ECOS는 `"{stat}/{item}"`). String(64) 충분(대표 지표 ~24자). UNIQUE
`(indicator_id, reference_date, vintage_date)` 그대로.

### D2 — KosisAdapter 신규 (EcosAdapter 구조 템플릿)
`app/adapters/kosis_adapter.py` — `SOURCE_KIND="KOSIS"`·`ADAPTER_VERSION="1.0.0"`.
`fetch_statistic(org_id, tbl_id, itm_id, obj_l, prd_se, start_prd, end_prd, batch_id,
observed_date)` → `FetchResult[MacroIndicatorRow]`. KOSIS `statisticsData.do?method=getList`
query param. `PRD_DE`→reference_date·`DT`→value·`UNIT_NM`→unit. apiKey URL 미포함(보안).

### D3 — vintage 미제공 → ingested_at 근사 (ECOS 패턴 동일)
KOSIS API는 공표일 미제공(`LST_CHN_DE`=DB갱신일, `PRD_DE`=기준시점). `vintage_date=observed_date`
(수집일시 주입) — `ecos_adapter.py` observed_date 패턴 동일. revision은 LST_CHN_DE 감지 로직
**없이** 다음 수집일 새 vintage row로 자연 누적(잠정→확정 이중축 PIT). 같은 날 재수집은 UNIQUE
idempotent skip. `vintage_date ≤ as_of AND reference_date ≤ as_of` fetch_latest 무변경 — look-ahead 0.

### D4 — 단일 objL 범위 (objL 재시도 없음)
단일 분류축(objL1=특정값 또는 ALL) 단순 시계열 지표만. 복합 objL(2~8) 다단계 통계표 범위 외 —
adapter objL ALL 순차 재시도 로직 없음(결정적). objL 불일치(KOSIS err 20) → 명시 AdapterError.
이로써 indicator_id에 objL 미포함(itmId 식별 충분) + String(64) 길이 보장.

### D5 — KOSIS 고유 지표만 (ECOS 중복 제외, §2.8 SoT)
ECOS에 이미 있는 지표(CPI `ecos_cpi`·PPI 등)는 KOSIS에서 재노출 안 함 — ECOS 1차 SoT.
KOSIS는 **ECOS 미보유 통계청 고유**(실업률·고용률·산업생산지수·경기선행/동행지수·인구)만.
동일 거시개념 이중 소스 = §2.8 위반 회피.

### D6 — 거시 factor 입력 경계 (ADR-0007 D5 현 효력)
ADR-0007 D5의 거시지표 factor 입력 경계는 ECOS가 M2(T81)에서 표시용→연산용으로 등록되며
해제됨(`db_field_provider.py` ecos field `_RESOLUTIONS` 연산 등록). KOSIS도 **ECOS 선례 동일** —
표시(`_MACRO_INDICATORS`) + factor field(`_RESOLUTIONS`) 둘 다 등록. 거시지표 표시·연산은
사실 수치이며 해석·전망 0(§2.2 No Advice·§2.7 Observation 경계 안).

### D7 — 월단위 freshness
`data_freshness.py` `_assess_kosis()`(`_assess_dart` calendar-days 패턴) +
`DataFreshness.kosis` 필드. `_KOSIS_STALE_CALENDAR_DAYS=45`(월간 공표+lag). `latest_successful("KOSIS")`.

### D8 — 적재 배치 (server/batch/dart_daily.py 패턴)
KOSIS 수집 배치는 **실재 `server/batch/dart_daily.py`** 패턴(ECOS 배치 미구현이라 참조 대상
아님) — batch_id(source="KOSIS")·per-indicator fetch-then-save·SAVEPOINT·rate limit(분당 1000)·
citation→record FK 순서. 실 수집은 운영 cycle(KOSIS_API_KEY 무료, 외부 blocker 아님).

## Consequences
- CONCEPT §4.1 KOSIS 유보 실현 — 거시 커버리지 확장(통계청 물가·고용·산업·인구).
- ECOS 인프라 재사용으로 신규 ORM/Alembic 0 — macro_indicators 테이블 공유.
- vintage 미제공 known-limit(ECOS 일관) — ingested_at 근사, 정확 공표일정 파싱은 범위 외.
- **외부 blocker 없음** — KOSIS 무료 공개, 자문 무관.

## 유보 (범위 외)
정확 공표일(vintage) 파싱·ECOS 기존 지표(CPI/PPI) KOSIS 재노출·forward 예측 거시·objL 복합
다단계 통계표·실 운영 수집 cycle(KOSIS_API_KEY 운영 설정).
