# M7_PLAN — Total Return + 배당 데이터 layer

**테마**: 현금배당 이력을 일급 데이터로 도입해, 수정주가(권리락 보정) 위에 배당
재투자를 더한 **Total Return 시계열**을 구현하고 §2.9 Temporal Continuity의 마지막
빈 공간(ADR-0001 D6 명시 유보 "total return M2+ 별도 ADR")을 채운다.

**ADR**: ADR-0035(예정). **선행 완료**: M0~M6([[project-speculum-m6]]). plan rev1
(Momus R1 OKAY-조건부 → 보강 4건 흡수).

---

## §0 용어 구분 (혼동 방지 — M6 §0 패턴)

- **raw price**: 무보정 종가. corporate action 보정 0.
- **adjusted price**: 권리락(액면분할·유무상증자) 보정 종가. **현금배당은 보정 안 함**
  (CONCEPT §2.9, `PriceAdjuster._POLICY_MATRIX` `cash_dividend="ignore"`). M0~M6 기존.
- **total return**: adjusted 위에 **현금배당을 배당락일 시점에 재투자**한 누적 시계열.
  M7 신규. adjusted 와 별개 변이 — raw/adjusted/total-return **3변이 병존**(§2.9 "두
  변이 모두 보존" 원칙의 확장).
- **세전(pre-tax / gross)**: 배당소득세·양도세 차감 0. M7은 **세전만**. 세후는 세무
  자문(ADR-0030 양도세 유보) 의존 → 영구 유보 후보(§1).

## §1 범위 — 포함 / 유보

### 포함 (deliverable #1~#6)
현금배당 PIT 데이터 layer · DART 배당결정 공시 수집(1차자료) · 세전 Total Return
엔진(별도 모듈) · field/factor 등록(빌트인 pack version bump + 구버전 보존) · §2.10
격리(dividend_batch_id + schema 1.3) · client 토글 표시.

### 유보 (범위 외 — 사유 명시)
- **세후(net) total return**: 배당소득세 15.4%·양도세 차등 → 세무 자문 필요(ADR-0030
  양도세 유보와 동일 blocker). M7은 세전만. **자문 후 별도 마일스톤**.
- **현물배당(stock dividend) 재투자**: 무상증자와 회계 구분 복잡, 사례 희소. 현금배당
  (`cash_dividend`)만 M7. 현물은 §2.9 known-limit 문서화.
- **forward/예상 배당**: §2.7 Observation over Speculation — 추정치 표시 금지. **과거
  실현 배당(effective_date ≤ as_of)만**. 예상 배당수익률 영구 범위 외.
- **배당 재투자 분수주 처리 정밀화**: 재투자 시 분수주는 누적 비율(factor)로 처리
  (실제 분수주 매매 시뮬레이션 X) — 표준 total-return index 관행.

### known-limit (구조적 한계 — Momus R1 축3 명문화)
- **배당 정정 시 total-return reproduce 한계**: `corporate_action`(배당 포함)은 batch
  cutoff freeze **불가**(ADR-0033 D4 상속 — `reproduce.py:402-404`). frozen 이후 배당
  row가 정정(ADR-0009 D5 `superseded_by`)되면 재실행 시 정정분이 보정에 반영되어 frozen
  total-return run 재현이 깨질 수 있다. M5 `restatement_lag` 진단은 **financial-only**라
  배당 정정 미탐지(false-negative). → §2.10 total-return reproduce byte-불변은 **"배당
  정정 미발생 가정 하"로 한정**. #5 회귀 anchor도 이 가정 하의 불변을 검증(무조건 보장 X).
  배당 정정 탐지 확장은 후속 마일스톤.

## §2 load-bearing 제약 (Explore 코드 확정 — 설계 핵심)

1. **POLICY_CONTENT_HASH 봉인**: `price_adjuster.py:324-421` `_POLICY_MATRIX` → JCS+SHA-256
   = `POLICY_CONTENT_HASH` → `snapshot_versions.py:57` `price_adjustment_policy_hash`
   키(data_versions/result_hash 입력). **매트릭스 수정 = 기존 run 과 hash 다른 신규
   run**. → **별도 `TotalReturnAdjuster` 모듈로 분리, _POLICY_MATRIX 불변**(D1).
2. **빌트인 pack content_hash**: `factor_pack.py` `compute_pack_hash`가 slug 포함 body
   전체 hash. 빌트인 pack에 factor 추가 = `factor_pack_content_hash` 변경 → frozen run
   reproduce 시 `_resolve_frozen_pack`이 **frozen slug+hash로 구버전 정본**을 찾아야 함.
   → **구버전 pack JSON(`speculum-builtin-v1.0.0.json`)을 `builtin-packs/`에 영구 보존**,
   신규는 `v1.1.0` 별도 파일(D4). PackRegistry version별 로드.
3. **data_versions = result_hash JCS 입력**(`screen_run.py:350-357`·`backtest_snapshot.py:158-174`):
   신규 키 추가 시 기존 frozen run의 result_hash는 freeze 약속으로 불변, `diff_versions`
   `get(k,"")` fallback(`snapshot_versions.py:267` 기존 구현)이 하위호환. M1(batch_id
   1.0→1.1)·M2(distribution 1.1→1.2) 반복 패턴 → M7 **1.2→1.3**(D5).
4. **PIT 기준**: `corporate_actions`의 `cash_dividend` row는 `effective_date`(배당락일,
   KRX 공식)가 PIT 기준. `announced_date`(공시일)는 look-ahead, `payment_date`(지급일)는
   배당락 점프 미설명 → 둘 다 부적격. `PITEnforcer.filter_active_records` 패턴 적용(D6).

## §3 deliverable

### #1 현금배당 PIT 데이터 layer
- `CorporateActionORM`(`db/orm/corporate_actions.py`) **확장 불필요** — `cash_dividend`
  action_type·`effective_date`·`payment_date`·`cash_amount`·`details`(per_share/type)
  JSONB 이미 존재(ADR-0009 D2). **신규 테이블 0**.
- 신규 `DividendRepository`(Protocol+Fake+SQL): `get_cash_dividends(code, as_of)` —
  `cash_dividend` + `effective_date ≤ as_of` PIT 필터. 기존 corporate action repo 패턴 재사용.
- 회귀: PIT 경계(effective_date == as_of 포함, > as_of 제외), look-ahead 차단.

### #2 DART 배당결정 공시 수집 (1차자료, §2.8)
- `DartAdapter.fetch_cash_dividends(corp_code, year)` 신규 — DART OpenAPI **"배당에 관한
  사항"** 공식 endpoint **`alotMatter.json`**(정기보고서 배당 항목). 응답 추출 필드:
  주당 현금배당금(`thstrm` 당기 / `se` 항목 구분 = "주당 현금배당금(원)"), 회계연도,
  배당 종류(중간/기말). 크롤링·pykrx 배당금액 의존 금지(pykrx는 per_share 미지원).
  `ADAPTER_VERSION` minor bump(출력 추가, 기존 불변 — ADR-0003 D5).
- **착수 1단계 = endpoint 실재성 smoke**(Momus R1 축4): `alotMatter.json`이 per_share를
  실제 제공하는지 1종목 smoke 확인. 미제공/스키마 상이 시 대체 endpoint 탐색 또는 해당
  데이터 N/A 고지(낙관 금지). 배당락일은 `alotMatter`에 직접 없을 수 있어 KRX/공시
  본문 보강 — smoke로 확정.
- **pykrx cross-check**(ADR-0003 D4): 배당락일 KRX 공식과 대조, 불일치 시 KRX 우선
  + 불일치 fact 고지(§2.1 — 조용한 채택 금지). per_share는 DART 단일 출처(pykrx 미지원
  한계 명시).
- 배당 수집 배치(`dart_dividend` cycle) — 기존 `dart_daily` 배치 패턴. batch_id 발급.

**구현 진화 (2026-06)**: 배당 출처가 DART `alotMatter` → **금융위 공공데이터
GetStocDiviInfoService_V2**(ADR-0035 D3)로 변경. `FscDividendAdapter` 가 crno(법인
등록번호)를 배당 조회 키로 받는다.

**crno 매핑 인프라 — ✅ Slice 1 구현 완료 (2026-06-19)**:
- `DartAdapter.fetch_company_info(corp_code)`(`app/adapters/dart_adapter.py`) +
  `CompanyInfo` dataclass — DART `company.json` 의 `jurir_no`(=crno) fetch. 비숫자
  제거 후 13자리 검증, 위반 시 빈 문자열(호출자 skip, §2.1). 기존 DART 키 사용.
- `CrnoMapping`(`app/services/crno_mapping.py`) — 종목코드→crno 단방향 immutable.
  **N:1 허용**(보통주·우선주 같은 법인 crno 공유 — CorpCodeMapping 1:1 과의 차이).
- `CrnoBootstrap`(`batch/crno_bootstrap.py`) — CorpCodeMapping 의 corp_code 별
  company.json 호출 → stock→crno dict + JSON 디스크 캐시(TTL 30일, crno 사실상
  불변). per-stock AdapterError skip+카운트, AdapterRetryError re-raise(부분 캐시
  방지). 캐시 format 위반 시 degrade 재빌드. test +26(adapter 8·mapping 7·boot 11).
- oracle-medium SHIP(Critical 0). **잔여 Slice 2 = `dividend_daily.py` orchestrator**
  (CrnoMapping + FscDividendAdapter + DividendRepository.save_dividends) + scheduler
  "dividend" job 등록 + `all` 순서 합류.

### #3 TotalReturnAdjuster (세전, 별도 모듈 — D1)
- 신규 `services/total_return_adjuster.py` — `_POLICY_MATRIX` **미접촉**(POLICY_CONTENT_HASH
  불변). adjusted 종가 + 배당락일 시점 배당 재투자 → 누적 비율(factor). 세전.
- 입력: adjusted price 시계열(기존 PriceAdjuster 출력) + cash_dividend PIT row.
- 출력: `total_return` 시계열(raw/adjusted와 별개 3번째 변이). 자체 버전
  `TOTAL_RETURN_POLICY_VERSION`(재투자 시점=배당락일·세전 정책 봉인).
- §2.1: 재투자 시점(배당락일)·세전·분수주(비율) 정책을 정책 hash로 명시 봉인.

### #4 field/factor 등록 (빌트인 pack version bump + 구버전 보존 — D4)
- `db_field_provider.py` `_RESOLUTIONS`에 `close_price_total_return` field 추가
  (TotalReturnAdjuster 연결). `dividend_per_share_trailing_annual`(현 `unsupported_m0`)
  → 실데이터 연결.
- 신규 빌트인 pack `speculum-builtin-v1.1.0.json`: **진짜 신규 factor =
  `price-return:total-annual` 1개**(Momus R1 축5 정정). `dividend-yield:trailing-annual`은
  v1.0.0에 **이미 존재**(`speculum-builtin-v1.0.0.json:273-302`, 현 `unsupported_m0` N/A)
  → v1.1.0에서 **실데이터 연결**(formula 불변, field resolver만 연결). content_hash 변경
  원인 = 신규 factor 1개 합류. **`v1.0.0.json` 영구 보존**(frozen run reproduce 정본).
- **active pack bump(Momus R1 축1 누락 보강)**: `factor_pack.py:807`
  `DEFAULT_PACK = load_builtin_pack("1.0.0")` → `load_builtin_pack("1.1.0")` bump해야
  신규 total-return factor가 운영 active pack에 노출. PackRegistry는 frozen
  `factor_pack_version`별 로드(`pack_registry.py:145`·`reproduce.py:185-253` 검증됨)라
  구버전 run은 v1.0.0 정본 재현. bump 후 기존 v1.0.0-active run byte-불변은 #5가 검증.
- §2.2: total return factor 표시는 성과 순위 아님 — ADR-0022 D4(user-explicit 정렬만).

### #5 §2.10 격리 + v1 불변 회귀 가드 (D5)
- `snapshot_versions.py`: `collect_batch_versions`에 `dividend_batch_id` 키 추가 +
  `SNAPSHOT_SCHEMA_VERSION` `"1.2"→"1.3"`. `TOTAL_RETURN_POLICY_VERSION`도
  `collect_active_policy_versions`에 추가.
- **회귀 가드(Critical)**: 기존 "1.2" frozen run의 result_hash byte-불변(golden hash
  anchor — hex 리터럴과 byte 동일 명시 검증), `diff_versions` fallback으로 신규 키
  `("", new)` 표시, 구버전 pack reproduce matches 유지. M1/M2 schema bump 테스트 패턴 재사용.
- **DEFAULT_PACK bump 직접 검증(Momus R1 축1)**: `DEFAULT_PACK`→1.1.0 bump 후에도
  **v1.0.0을 active로 freeze한 기존 screen/backtest run의 result_hash가 hex golden과
  byte-동일**임을 anchor로 못박는다(active pack 교체가 과거 run을 깨지 않음 증명).
- 단, byte-불변은 §1 known-limit("배당 정정 미발생 가정 하")로 한정 — 무조건 보장 아님.

### #6 client 표시 (UI 토글 + 세전 disclosure)
- 가격 차트/시계열에 **변이 토글**: "원주가 / 권리락 보정 / 권리락+배당재투자(Total
  Return)". grayscale 중립.
- **세전 disclosure**(§2.1/§2.7): "세전 기준 — 배당소득세·양도세 미반영, 실제 수익과
  상이" 중립 고지. i18n 한국어.
- multi-ID 표시(`price-return:total-annual` 정의 노출, M4 PackAttribution 패턴). 권위·순위 0.

## §4 리스크

- **Critical**: 빌트인 pack hash 변경 → 구버전 frozen run reproduce 파손. **#4 구버전
  JSON 영구 보존 + version별 PackRegistry 로드**가 load-bearing 가드. #5 회귀 anchor 필수.
- **High**: DART `announced_date` look-ahead(#2 — effective_date만 PIT). `_POLICY_MATRIX`
  오수정 시 price_adjustment_policy_hash drift(#3 별도 모듈로 구조적 회피).
- **High**: DART 주요사항보고서 파싱 — 배당 endpoint 스키마 정확도. pykrx cross-check로
  배당락일 검증, per_share는 DART 단일 출처라 파싱 견고성 + 실패 시 N/A 고지.
- **Medium**: 세전/세후 혼동 → 사용자 오인. #6 disclosure 필수. 세후는 §1 유보 명시.

## §5 완료 기준
deliverable #1~#6 구현 + 세전 total return 시계열 산출 + 기존 frozen run(screen/backtest)
result_hash byte-불변 회귀 green + DART 1차자료(크롤링 0) + 세전 disclosure 표시 +
Momus 전방위 OKAY + 전체 게이트(server pytest·ruff·alembic head·forbidden-words /
client typecheck·lint·vitest·build·check:i18n) green.

**release blocker(코드외)**: 세후 total return은 세무 자문 의존(§1 유보). 세전 M7은
자문 무관 — 사실(공시 배당) 기반.
