# ADR-0035 — Total Return + 배당 데이터 layer (M7)

- **Status**: ACCEPTED (2026-06-05)
- **Context milestone**: M7
- **선행**: ADR-0001(가격 보정·D6 total return 유보), ADR-0003(데이터 출처),
  ADR-0009(corporate action), ADR-0033(backtest reproduce·D4 corporate action cutoff
  한계), ADR-0030(세무 유보). plan=`docs/M7_PLAN.md` rev1(Momus R1 OKAY-조건부 →
  R2 OKAY 착수 가능).

## Context

ADR-0001 D6은 total return(배당 재투자 누적 시계열)을 "M2+ 별도 ADR"로 유보했다.
§2.9 Temporal Continuity는 raw/adjusted 두 변이를 보존하되 현금배당은 보정하지 않는
상태(`PriceAdjuster._POLICY_MATRIX` `cash_dividend="ignore"`)로 남아, 배당을 포함한
실질 수익 시계열이 부재했다. M7은 DART 현금배당 공시를 1차자료로 도입해 세전 total
return을 구현하고 이 빈 공간을 채운다.

핵심 제약: v1 frozen run(screen/backtest)의 result_hash·content_hash·data_versions를
한 바이트도 깨지 않으면서(§2.10) 신규 데이터 소스·factor·정책을 추가해야 한다.

## Decisions

### D1 — 별도 TotalReturnAdjuster 모듈 (POLICY_CONTENT_HASH 불변)
total return 계산을 `PriceAdjuster._POLICY_MATRIX`에 넣지 않고 신규
`services/total_return_adjuster.py`로 분리한다. `_POLICY_MATRIX` → `_build_policy_body`
→ `POLICY_CONTENT_HASH` → `snapshot_versions.price_adjustment_policy_hash`가 result_hash
입력이므로, 매트릭스를 건드리면 기존 adjusted 정책 hash가 drift한다. 분리로
`POLICY_CONTENT_HASH`를 byte-불변 유지. total return은 adjusted 위에 배당 재투자를 더한
**3번째 변이**(raw/adjusted/total-return 병존, §2.9 "두 변이 보존" 원칙 확장).
자체 `TOTAL_RETURN_POLICY_VERSION`으로 재투자 정책(배당락일 시점·세전·분수주 비율)을 봉인.

### D2 — corporate_actions 확장, 신규 테이블 0 (cash_dividend PIT)
`CorporateActionORM`은 `cash_dividend` action_type·`effective_date`(배당락일)·
`payment_date`·`cash_amount`·`details`(per_share/type) JSONB를 **이미 보유**(ADR-0009 D2).
신규 테이블 없이 `DividendRepository`(Protocol+Fake+SQL)가 `effective_date ≤ as_of` PIT
필터로 cash_dividend row를 쿼리한다.

**D2.1 — chain은 전체 corporate_action 집합에서 해소(oracle 리뷰 Critical)**: `fetch_dividends`
는 SQL WHERE를 `code`만으로 좁히고(action_type 필터 제거) `filter_active_records`로 **전체
action 집합**에서 supersede chain을 해소한 **후** cash_dividend를 필터한다. cash_dividend가
다른 action_type(예: reclassified)으로 superseded되는 cross-action chain에서 action_type을
먼저 좁히면 successor가 `record_by_id`에 부재 → 보수적 active 복원으로 superseded된 배당이
부활(§2.4) + `fetch_actions`의 corruption 검출과 비대칭(§2.10 hole)이 발생한다. 따라서
`fetch_dividends ≡ fetch_actions(action_types={"cash_dividend"})` + effective 필터(byte-동일).

**D2.2 — supersede는 insert 아닌 `update_superseded_by`(oracle 리뷰 High)**: `save_dividends`는
`superseded_by` non-NULL을 ValueError로 거부하고(항상 NULL insert) atomic 검증(전체 batch
검증 후 add) 한다. 정정공시 chain은 `update_superseded_by(record_id, successor_id)`로만
수행(financials/treasury/corporate_action 패턴 일관) — self-FK 순서 의존(successor-first
insert) 제거. `details`는 JSON-scalar(str/int/float/bool/None)만 — Decimal/date 금지
(Fake/SQL JSON round-trip 비대칭 방지), per_share는 문자열 저장.

### D3 — 금융위 공공 배당 API 1차자료 + 배당락일 역산 (§2.5/§2.8, ADR-0003 D4 확장)
**조사 정정(librarian/OpenDART 공식 확인)**: 당초 가정한 DART `alotMatter.json`은
주당배당금(per_share)은 주지만 **배당락일·배당기준일을 안정적으로 제공하지 않는다**
(`stlm_dt`는 결산기준일 12/31, 2023 제도변경으로 역산 오류). 따라서 배당 1차자료를
**금융위원회 주식배당정보 API**(공공데이터포털 15043284,
`GetStocDiviInfoService/getDiviInfo`)로 변경한다 — KOSPI/KOSDAQ 전 상장사·우선주 포함,
한국예탁결제원(KSD) 원천, §2.5 open data 공식 1차.
- **수집 필드**: `stckGnrlDvdnAmt`(주당 현금배당금→cash_amount)·`dvdnBasDt`(배당기준일)·
  `cshDvdnPayDt`(지급일→payment_date)·`stckKndNm`(주식종류 보통주/우선주)·`dvdnRcdNm`
  (배당종류 결산/중간/분기).
- **effective_date(배당락일) = `dvdnBasDt` − 1영업일**(KRX 영업일 캘린더 역산 — 한국
  T+2 결제 규칙, 배당기준일 직전 영업일이 배당락일). 캘린더는 기존 거래일 데이터 사용.
- **종목→crno 매핑**: getDiviInfo는 종목코드 조회 불가(`crno` 법인등록번호 필요). 매핑은
  배치 상위(KRX상장종목정보 API 15094775 또는 마스터 확장) — adapter는 crno를 받는다.
- **DART `alotMatter.json`은 cross-check 보조**(per_share 교차검증, §2.1 불일치 고지) —
  배당락일 출처 아님. pykrx 미사용(배당 데이터 미지원).
- 신규 `FscDividendAdapter`(`SOURCE_KIND` 신규, `ADAPTER_VERSION` 1.0.0). ADR-0003 D4의
  "corporate action = DART 1차"를 **배당 한정 금융위 공공 API 1차**로 확장.
- **착수 검증**: getDiviInfo `resultCode`·필드 실재성 1종목 smoke(운영 serviceKey).

### D4 — 빌트인 pack v1.1.0 + v1.0.0 영구 보존 + active bump
빌트인 pack에 `price-return:total-annual` factor 1개 신규 추가 →
`speculum-builtin-v1.1.0.json`. `dividend-yield:trailing-annual`은 v1.0.0 기존 factor의
N/A(`unsupported_m0`) field resolver를 실데이터 연결(formula 불변). pack body에 신규
factor 합류 → `factor_pack_content_hash` 변경. **`v1.0.0.json` 영구 보존** — frozen run
reproduce 시 `_resolve_frozen_pack`이 frozen `factor_pack_version`별로 `load_builtin_pack`
(`pack_registry.py:145` version 인자 분리 로드) → 구버전 정본 byte-재현. 운영 active pack
노출 위해 `factor_pack.py:807 DEFAULT_PACK`을 `load_builtin_pack("1.1.0")`으로 bump.

### D5 — §2.10 격리 (schema 1.3 + 신규 키, 기존 run 불변)
`collect_batch_versions`에 `dividend_batch_id`, `collect_active_policy_versions`에
`TOTAL_RETURN_POLICY_VERSION` 추가 + `SNAPSHOT_SCHEMA_VERSION` `"1.2"→"1.3"`. 기존 frozen
run의 저장 data_versions에는 신규 키가 없으므로 result_hash 재계산 시 byte-동일(freeze
약속). `diff_versions` `get(k,"")` fallback(기존 구현)이 신규 키를 `("", new)` 표시 —
M1(1.0→1.1)·M2(1.1→1.2) 반복 패턴. DEFAULT_PACK bump 후에도 v1.0.0-active 기존 run
result_hash가 golden hex와 byte-동일임을 회귀 anchor로 못박는다.
- **tradeoff(보수적 설계)**: `total_return_policy_hash`/`total_return_policy_version`은
  `price_adjustment_policy_hash`와 동일하게 **모든** run의 active policy에 포함된다
  (total-return factor 미사용 run도). 정책-only set(`collect_active_policy_versions`)은
  pack/conditions 무관 무인자 산출이므로 — 어떤 정책 변화든 freshness diff로 표시하는
  보수적 선택이며, price 정책 키 패턴과 일관(과대 freshness < 누락 freshness).

### D6 — PIT 기준 = 배당락일(effective_date), announced_date = effective_date
total return의 배당 반영 시점은 `effective_date`(배당락일 — 시장 가격 조정 발생일).
`payment_date`(지급일)는 배당락 점프 미설명 → 부적격.
- **announced_date = effective_date(배당락일)로 설정**: 금융위 배당 API(D3)는 공시일을
  제공하지 않는다. 배당락일은 배당 정보가 시장 가격에 반영되는 시점이자 **look-ahead
  안전 하한**이다 — 실제 DART 배당결정 공시는 배당락일보다 수 주 이르지만, 그 공시일을
  쓰지 않고 배당락일까지 배당을 미반영하는 것은 **보수적**(미래 정보 누출 0). #1
  `DividendRepository`의 이중 PIT 축(announced/effective)은 배당에서 배당락일로 수렴 —
  `announced ≤ as_of`와 `effective ≤ as_of`가 동일 날짜 기준이 되어 정합.
- `PITEnforcer.filter_active_records` 패턴 적용(#1 구현 완료).

### D7 — 세전(pre-tax)만, 세후 유보 + disclosure (§2.1/§2.7)
M7 total return은 **세전(gross)**. 배당소득세·양도세 차감 0. 세후는 세무 자문 의존
(ADR-0030 양도세 유보와 동일 blocker) → 별도 마일스톤. client는 "세전 기준 — 배당소득세·
양도세 미반영, 실제 수익과 상이" 중립 disclosure 표시. 가격 변이 토글(원주가/권리락 보정/
권리락+배당재투자)은 grayscale, 성과 순위 아님(ADR-0022 D4 user-explicit 정렬).

### D8 — 배당 정정 reproduce known-limit (구조적 한계)
`corporate_action`(배당 포함)은 batch cutoff freeze **불가**(ADR-0033 D4 상속 —
`reproduce.py:402-404`). frozen 이후 배당 row 정정(ADR-0009 D5 `superseded_by`) 시 재실행
정정분이 보정에 반영되어 total-return frozen run 재현이 깨질 수 있다. M5 `restatement_lag`
진단은 financial-only라 배당 정정 미탐지(false-negative). → §2.10 total-return byte-불변은
**"배당 정정 미발생 가정 하"로 한정**. 배당 정정 탐지 확장은 후속 마일스톤.

## Consequences

- §2.9 Temporal Continuity의 마지막 미완성 빈 공간(total return) 충족.
- v1 frozen run(screen/backtest) 절대 불변 유지 — 빌트인 pack version별 로드 + schema
  1.3 격리 + 기존 run data_versions 불변.
- 세후 수익·forward 배당·현물배당 재투자는 범위 외(plan §1 유보).
- **release blocker(코드외)**: 세후 total return = 세무 자문 의존. 세전 M7은 자문 무관
  (공시 배당 사실 기반).

## 유보 (범위 외)
세후 total return(세무 자문)·현물배당 재투자·forward 예상 배당(§2.7)·배당 재투자 분수주
매매 시뮬레이션·배당 정정 탐지(D8 known-limit).
