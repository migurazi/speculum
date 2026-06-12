# M9_PLAN — KOSIS 거시지표 도입

**테마**: 통계청 KOSIS(국가통계포털) 무료 공개 API로 거시경제 지표(소비자물가·고용·
산업생산·인구 등)를 도입 — 기존 ECOS(한국은행) 거시 인프라와 **대칭 신규 소스**. CONCEPT
§4.1 "KOSIS 월 M2+" 유보 실현. §2.5 Open Data 부합, 외부 blocker 없음.

**ADR**: ADR-0036(예정). **선행**: M0~M8([[project-speculum-m8]]). plan rev1
(Momus R1 REJECT → Critical/High/Medium 5건 흡수).

## §0 핵심 설계 — ECOS 인프라 재사용
KOSIS는 ECOS와 동일 거시지표 도메인이므로 **기존 인프라를 최대 재사용**, 신규는 adapter·
field 등록·freshness·배치뿐.

| 구분 | 재사용(무변경) | 신규 |
|------|------|------|
| ORM/테이블 | `macro_indicators`(indicator_id로 ECOS/KOSIS 구분) | — |
| Repository | `MacroIndicatorRepository`·SQL·Fake·Converter | — |
| field 해소 | `_resolve_macro_indicator`(generic, kind="macro_indicator") | `_RESOLUTIONS` KOSIS 항목 |
| SourceKind | `SourceKind.KOSIS`(이미 존재) | — |
| adapter | `EcosAdapter` 구조 템플릿 | `KosisAdapter` 신규 |
| freshness | `_assess_dart` 패턴 | `_assess_kosis`+`DataFreshness.kosis` |
| 배치 | `BatchRunORM.source="KOSIS"`(String, OK) | KOSIS 적재 배치(**`batch/dart_daily.py` 패턴** — ECOS 배치는 미구현이라 참조 대상 아님) |

## §1 범위 — 포함 / 유보

### 포함 (deliverable #1~#5)
KosisAdapter(통계자료 조회) · field/factor 노출 · 월단위 freshness · 적재 배치 · 테스트.

### 유보 (범위 외)
- **정확 공표일(vintage)**: KOSIS API는 공표일 미제공(ECOS 동일 한계). `vintage_date=ingested_at`
  근사 — 별도 공표일정 파싱은 범위 외(known-limit, ECOS 일관).
- **ECOS 기존 지표 전부**(Momus R1 Low — CPI 중복 정정): PPI(한국은행 소관)뿐 아니라 **ECOS에
  이미 있는 모든 지표**(예 `ecos_cpi` 소비자물가지수 — `db_field_provider.py:314`)는 KOSIS에서
  재노출 안 함. ECOS가 1차 SoT. KOSIS는 **ECOS 미보유 통계청 고유 지표만**(실업률·고용률·
  산업생산지수·경기선행/동행지수·인구). 동일 거시개념 이중 소스 = §2.8 SoT 위반 회피.
- **forward/예측 거시지표**: §2.7 Observation — 실측 통계만. 전망치 0.
- **objL 복합 통계표**: 분류 다단계(objL2~8) 복합 표는 범위 외. **단일 분류축(objL1=특정값
  또는 ALL) 단순 시계열 지표만** → adapter objL 재시도 로직 불필요(아래 §2.3), indicator_id
  길이 64자 내 보장(§2.2).

## §2 load-bearing 제약 (Explore/librarian 확정)

1. **vintage 미제공 → ingested_at 근사 (ECOS 패턴 동일, LST_CHN_DE 감지 제거)**: KOSIS 응답에
   공표일 필드 없음(`LST_CHN_DE`는 DB 갱신일, `PRD_DE`는 기준시점). `MacroIndicatorORM.vintage_date`를
   **수집일시(observed_date 주입)**로 채움 — `ecos_adapter.py` `observed_date` 패턴 동일.
   **revision 처리(Momus R1 Medium 정정)**: ECOS와 동일하게 LST_CHN_DE 감지 로직 **없이** —
   잠정→확정 갱신값은 **다음 수집일(다른 vintage_date)에 새 row로 자연 누적**(UNIQUE
   `(indicator_id, reference_date, vintage_date)` 충족). 같은 날 재수집은 UNIQUE로 idempotent
   skip(같은 vintage_date). LST_CHN_DE는 details/메타로만 기록(vintage 트리거 아님 — 같은 날
   vintage 충돌 회피). KOSIS 수집을 공표 예정일 근처 스케줄링하면 ingested_at이 실 공표일 근접.
2. **macro_indicators 테이블 재사용 + indicator_id 길이(Momus R1 High)**: ECOS row와 동일 테이블
   공존, `indicator_id` prefix namespace 분리 — 규약 `"kosis/{orgId}/{tblId}/{itmId}"`(단일 objL
   범위라 objL 미포함; ECOS는 `"{stat}/{item}"`). **String(64) 충분성 검증** — 대표 지표 최장
   예: `kosis/101/DT_1DA7001S/T20`(~24자), `kosis/101/DT_1C65_03E/T1`(~22자) 모두 64자 내(여유).
   복합 objL 통계표는 §1 유보라 itmId만으로 식별 충분 → **신규 ORM/Alembic 0** 유지. 착수 시
   대표 지표 5종의 실 orgId/tblId/itmId 최장 길이 표로 재확인.
3. **식별자 단순화(Momus R1 Medium — objL 재시도 제거)**: §1 유보로 **단일 분류축(objL1=특정값
   또는 ALL) 지표만** → adapter는 호출자가 준 objL을 그대로 사용, **objL ALL 순차 재시도 로직
   없음**(결정적). objL 불일치(KOSIS err 20)는 명시 AdapterError(통계표 ID 오류로 처리). 복합
   다단계 통계표 미지원.
4. **PIT 재현**: `vintage_date ≤ as_of AND reference_date ≤ as_of` 기존 SQL 무변경. ECOS와
   동일 fetch_latest. KOSIS row 자동 적용. look-ahead 0(ECOS 대칭, Momus R1 축2 검증).
5. **거시 factor 입력 경계(Momus R1 Medium — ADR-0007 D5)**: ECOS field가 이미 `_RESOLUTIONS`에
   연산용 등록(`db_field_provider.py:303`, M2 T81 표시용→연산용 해제). KOSIS도 **ECOS 선례 동일**
   취급 — 표시(`_MACRO_INDICATORS`) + factor field(`_RESOLUTIONS`) 둘 다 등록. ADR-0036에
   ADR-0007 D5 현 효력(거시 factor 입력 허용, ECOS M2 선례) 명시.

## §3 deliverable

### #1 KosisAdapter (EcosAdapter 템플릿)
신규 `app/adapters/kosis_adapter.py` — `SOURCE_KIND="KOSIS"`(SourceKind.KOSIS.value),
`ADAPTER_VERSION="1.0.0"`. `__init__(http_client, api_key[KOSIS_API_KEY env], timeout)`.
`fetch_statistic(*, org_id, tbl_id, itm_id, obj_l, prd_se, start_prd, end_prd, batch_id,
observed_date) -> FetchResult[tuple[MacroIndicatorRow, ...]]`:
- GET `https://kosis.kr/openapi/statisticsData.do?method=getList` query param(apiKey·orgId·
  tblId·itmId·objL1[단일]·prdSe·startPrdDe·endPrdDe·format=json·jsonVD=Y).
- 응답 파싱: `PRD_DE`→reference_date(YYYYMM→월1일, YYYY→연1/1), `DT`→value(Decimal), `UNIT_NM`→unit,
  `ITM_NM`/`TBL_NM`→indicator 메타. `vintage_date=observed_date`(공표일 미제공 근사).
- indicator_id = `"kosis/{org_id}/{tbl_id}/{itm_id}"`(objL 단일 범위라 미포함).
- 에러 분기: KOSIS err 코드(httpx status + JSON err 필드) — rate/일시→AdapterRetryError, 인증/
  영구→AdapterError. **objL 불일치(err 20)→명시 AdapterError**(통계표 ID 오류, 재시도 없음 —
  단일 objL 범위). 무자료→빈 결과(에러 아님).
- `_make_citation`: KOSIS 통계표 뷰어 URL(`statHtml.do?orgId=&tblId=`), apiKey URL 미포함(보안 —
  ecos_adapter `_make_citation` 패턴).
- mock 테스트(httpx.MockTransport, ecos_adapter test 패턴).

### #2 field/factor 노출
`db_field_provider.py` `_RESOLUTIONS`에 KOSIS field 추가(**ECOS 미보유 통계청 고유만** —
`kosis_unemployment_rate`·`kosis_employment_rate`·`kosis_industrial_production`·`kosis_leading_index`
등, `FieldResolution(field=..., kind="macro_indicator", indicator_id="kosis/101/...")`).
**CPI 제외**(`ecos_cpi` 이미 존재 — ECOS 1차 SoT, Momus R1 Low). `_resolve_macro_indicator`
**무수정**(generic). Market Overview `_MACRO_INDICATORS`에 KOSIS 지표 표시(ECOS 패턴).
표시 + factor field 둘 다 등록(거시 factor 입력 ECOS 선례 — ADR-0007 D5 현 효력, §2.5번 제약).

### #3 월단위 freshness
`data_freshness.py`에 `_SOURCE_KOSIS="KOSIS"`·`_KOSIS_STALE_CALENDAR_DAYS=45`(월간 공표+lag)
+ `_assess_kosis()`(`_assess_dart` calendar-days 패턴) + `DataFreshness.kosis: SourceFreshness`
필드. `latest_successful("KOSIS")` 기준.

### #4 KOSIS 적재 배치 (실재 `batch/dart_daily.py` 패턴 — Momus R1 Critical 정정)
KOSIS 거시지표 수집 배치 — **`batch/dart_daily.py`/`krx_daily.py` 일배치 패턴**(ECOS 배치는
미구현이라 참조 대상 아님). batch_id 발급(BatchRunORM source="KOSIS")·per-indicator fetch-then-save·
SAVEPOINT failure isolation·rate limit(KOSIS 분당 1000회)·citation→record FK 순서. fetch→
`macro_indicator_record_to_orm`→`SqlMacroIndicatorRepository` save. observed_date=수집일.
idempotent(UNIQUE 제약 — 같은 날 재수집 skip). 실 수집은 운영 cycle(KOSIS_API_KEY, 무료 키 —
외부 blocker 아님). 단위 테스트는 adapter mock + repo, 실 배치 smoke는 운영.

### #5 테스트
- KosisAdapter mock(정상 파싱·objL 재시도·err 분기·citation·무자료·PRD_DE/DT 파싱).
- MacroIndicatorRepository KOSIS indicator_id 시계열·vintage PIT(ECOS test 패턴 복제).
- field resolve(kosis field→값)·freshness(_assess_kosis stale 경계).

## §4 리스크
- **High**: vintage 미제공 → revision 시 PIT 재현 한계. `vintage_date=ingested_at` 근사,
  갱신값은 **다음 수집일 새 vintage row로 자연 누적**(§2.1 — LST_CHN_DE 감지 로직 없음).
  ECOS와 동일 known-limit(신규 위험 아님).
- **High**: KOSIS API key·운영(KOSIS_API_KEY env). 코드는 mock 테스트, 실 수집은 운영 cycle
  (외부 blocker 아님 — 무료 키, 자문 무관).
- **Medium**: orgId/tblId/itmId 식별자 복잡 — indicator_id 규약(`kosis/{org}/{tbl}/{itm}`,
  단일 objL)으로 추상화. objL 불일치(err 20)·잘못된 통계표 ID → 명시 AdapterError(재시도 없음,
  §2.3). 무자료 → 빈 결과.
- **Medium**: KOSIS 지표 중복(동일 지표 여러 orgId 수록) — 원천 기관 orgId 고정(통계청 101).
  ECOS 기존 지표(CPI·PPI 등)는 §1 유보로 KOSIS 제외(ECOS 1차 SoT).
- **참조 경로**: 배치 템플릿은 `server/batch/dart_daily.py`(`server/app/batch/` 아님).

## §5 완료 기준
deliverable #1~#5 구현 + KOSIS 거시지표 fetch→적재→field/표시 + vintage PIT(ECOS 일관) +
월단위 freshness + Momus 전방위 OKAY + 전체 게이트(server pytest·ruff·forbidden / client
관련) green. 외부 blocker 없음(KOSIS 무료 공개).
