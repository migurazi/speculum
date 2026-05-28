# M0 Conformance Review — Momus 라운드 1 보고서

| | |
|---|---|
| **검토자** | Momus (Opus 4.7 [1M], read-only) |
| **일자** | 2026-05-28 |
| **상위 work-order** | [m0-conformance-review.md](m0-conformance-review.md) |
| **결론** | **REJECT (Critical 2 개 해결 필요)** |

---

## 검토 범위

- ADR-0001~0011 (12 ADR) 전체.
- 핵심 service 5 종 (`pit_enforcer`, `price_adjuster`, `forbidden_words`, `krx_calendar`, `source_citation`).
- Adapter 4 종 (`base`, `fdr`, `pykrx`, `dart` + `dart_account_mapper`).
- Batch 2 종 (`krx_daily`, `dart_daily`) — effective_date 영구화 path 추적.
- Client: 4 뷰 + layout + Consent/Disclaimer + NavBar + AsOfDatePicker + SourceAttribution + SaveRunButton.
- CI 게이트 5 종 workflow + factor pack JSON + `shared/forbidden-words.json` SoT.

## 분류 요약

| 등급 | 개수 | 처리 상태 |
|---|---|---|
| **Critical** | 2 | 0 / 2 |
| **High** | 4 | 0 / 4 |
| **Medium** | 5 | 0 / 5 |
| **Low** | 3 | 0 / 3 |
| **합** | 14 | 0 / 14 |

---

## V1. DART `effective_date` 가 rcept_dt 가 아닌 분기말로 채워짐 — silent look-ahead bias (Critical)

**위치**:
- `server/app/adapters/dart_adapter.py:392, 524-532` (`_fiscal_quarter_end`)
- `server/app/adapters/dart_adapter.py:227-232` (citation 의 effective_date 도 분기말)
- `server/batch/dart_daily.py:344` (`FinancialRecord.effective_date = row.effective_date` 로 분기말 영구화)
- `server/app/db/orm/financials.py:55` 댓글이 ADR 본문 정의를 임의 재해석
- `server/app/repositories/pit_protocols.py:96` 댓글이 "DART rcept_dt **가 아닌** 회계기간 등" 으로 ADR 정면 부정

**표준**:
- ADR-0002 D3 line 102: `effective_date # 그 데이터의 발효일 (DART rcept_dt, KRX trd_dt)`
- ADR-0003 line 75, 97 의 예제 코드: `effective_date=date.fromisoformat(response["rcept_dt"])`
- `docs/ARCHITECTURE.md` line 184: "DART 보고서 접수일 (`rcept_dt`) 을 `effective_date` 로 사용"
- CONCEPT §2.4 line 123, 137: "분기 공시 lag — 1Q 결산은 1Q 종료 후 45일 이내 공시... Speculum 은 모든 재무지표에 effective_date (그 데이터가 공시된 날짜) 를 일급 필드로 보존"
- work-order §2 H 우선순위: "DART rcept_dt PIT 일관성"

**문제**:
- DART `_fiscal_quarter_end(2024, 1) = 2024-03-31` 가 effective_date 로 영구화되나, 실제 DART 보고서 접수일은 분기말 + 45~60 일.
- PIT Enforcer `filter_records_at_or_before(records, as_of)` 의 비교가 `record.effective_date <= as_of` 이므로 as_of=2024-04-15 같은 시점이 1Q 데이터를 사용할 수 있음 → CONCEPT §2.4 의 "그 시점에 알 수 없던 데이터를 사용하지 않는다" 정면 위반.
- M0_PLAN §3 의 명시 리스크 "Look-ahead bias 의 silent regression — 8기둥 §2.4 위반" 이 이미 발생.
- adapter 가 `estimated_fields=frozenset({"effective_date"})` 로 marker 를 달았지만, `dart_daily.py` 의 `_convert_to_financial_records` (line 336-355) 는 이를 무시하고 분기말 그대로 영구화. PIT enforcer 는 estimated_fields 를 인식하지 않음.
- AC-P-04 (PIT) 가 "raw query path 0" 만 검증 — record-level 의미론 위반은 미검출.
- 코드 코멘트 (dart_adapter line 246-250) 가 "본 cycle backlog" 라 인정. 그러나 work-order §1.2 의 intended 한계 목록에 **명시되어 있지 않음**. 이 항목은 review 대상이며, 위 ADR/standard 의 위반 정도가 silent → AC-P-04 + AC-D-05 둘 다 실질 미통과.

**권장**:
- **단기 (M0 squash 차단 해제용 minimal fix)**: DART adapter 가 `fnlttSinglAcntAll` 응답의 `rcept_no` 로부터 별도 `list.json` endpoint 호출하여 정확한 `rcept_dt` 를 fetch. 또는 PIT 의미 위반을 **명시적 alert** 으로 사용자/운영자에게 노출 — `estimated_fields` 가 `effective_date` 인 record 는 PIT enforcer 가 `effective_date + DEFAULT_DISCLOSURE_LAG_DAYS` (예: 60 일) 를 적용 후 비교하는 보수 정책.
- **중기**: PIT Enforcer 가 `PITRecord` Protocol 에 `is_estimated_date: bool` 또는 `effective_date_confidence` 차원 추가. 현재 estimated 인 record 는 별도 path.
- **ADR-0012 신설** (또는 ADR-0002 D3 amendment): "DART effective_date 의 단기 보수 정책 — quarter_end + lag_days 또는 별도 fetch". 변호사 자문 ADR-0019 와 함께 release 전 결정.
- **work-order §1.2 의 intended 한계 목록에 추가** — 만약 사용자가 본 항목을 의도된 한계로 squash 통과시키려면, `docs/CONCEPT.md §2.4` 의 "솔직한 한계" 절에 명시 + ConsentModal 의 본문에 "분기 재무 데이터의 PIT 정확도는 분기말 단위 보수적 근사" 명시. **그러나 본 검토자의 판단으로는 이 정도가 M0 release 의 "정직한 거울" 정체성을 훼손 — minimal fix 권장**.

---

## V2. ConsentModal 본문이 ADR-0006 D7.1 의 4 항목 의무 중 [2~4] 누락 — 개인정보보호법 + 자본시장법 위험 (Critical)

**위치**:
- `client/components/ConsentModal.tsx:49-86` — 모달 본문
- `client/components/DisclaimerFooter.tsx:56-58` — "개인정보처리방침 · 이용약관 · 면책조항 — M0 release 직전 게시 예정" placeholder

**표준**:
- 개인정보보호법 제15조 (수집·이용 동의), 제22조 (별도 동의), 제22조의2 (만 14세), 제30조 (처리방침 의무 공개)
- ADR-0006 D6.1 (필수 고지 4 항목), D6.4 (만 14세 확인 체크박스 의무), D6.5 (국외 이전 동의), D7.1 (동의 모달 본문 4 절)
- AC-L-01 (동의 모달 + footer disclaimer 모든 화면), AC-L-04 (개인정보처리방침 + 이용약관 작성·노출)

**문제**:
- ConsentModal 은 ADR-0006 D7.1 의 4 절 중 [1] 본 서비스 성격 만 들어있음.
- 누락:
  - **[2] 개인정보 수집·이용 동의** 4 항목 (수집 항목 / 목적 / 보유 기간 / 동의 거부 권리) — 단일 체크박스 + 명시 동의 필요. 현재 모달은 단일 "동의하고 시작" button 으로 모든 사항 일괄 동의 — 제22조의 "별도 동의" 위반 위험.
  - **[2-2] 국외 이전 동의** (Vercel 미국 / Fly.io 미국 — ADR-0006 D6.5) — 별도 체크박스 의무.
  - **[3] 만 14세 이상 확인 체크박스** — 제22조의2.
  - **[4] 처리방침 / 이용약관 link**.
- DisclaimerFooter 의 "M0 release 직전 게시 예정" placeholder 는 AC-L-04 미달 상태로 release 가능성 시사 — 그러나 `/privacy` 페이지 자체 미존재 + 이용약관 미작성. 개인정보보호법 제30조 (처리방침 항시 공개) 의무 위반.
- ADR-0006 D2 의 자본시장법 회피 측면도 ConsentModal 의 "투자 자문 아님" 진술은 들어있으나, 사용자가 "동의하고 시작" 버튼만 클릭 — 명시적 자본시장법 disclaimer 동의 record 가 단일 button click 에 합쳐져 있어 향후 분쟁 시 동의 분리 입증 어려움.
- `/privacy`, `/terms`, `/disclaimer` 페이지가 `client/app/` 에 미존재 — Footer link 가 "게시 예정" 만.

**권장**:
- ConsentModal 을 ADR-0006 D7.1 의 4 절 모두 포함하도록 재작성 — 3 개 필수 체크박스 (개인정보 동의 / 국외 이전 동의 / 만 14세 확인). 사용자가 3 개 모두 체크해야 "동의하고 시작" 활성화. **공통 동의 button 하나로 다항 동의** 패턴은 제22조 별도 동의 위반.
- `client/app/privacy/page.tsx`, `client/app/terms/page.tsx`, `client/app/disclaimer/page.tsx` 신설 — ADR-0006 D6.2 의 처리방침 항목 + D7.2 의 footer disclaimer 전문 + 이용약관 초안. M0 release 전 의무.
- DisclaimerFooter line 56-58 의 "게시 예정" 문구를 실 link 로 교체. CI 게이트 (`disclaimer-coverage.yml`) 에 처리방침/이용약관 페이지 존재 검사 추가.
- 만약 정식 변호사 자문 (ADR-0006 D9 + M0_PLAN AC-L-02) 이전 release 시도면 squash 전 사용자 명시 동의 의무 — 본 검토자는 변호사 자문 + ConsentModal/처리방침/이용약관 모두 완성 후 release 권장.

---

## V3. 빌트인 factor pack 의 `volume-turnover:avg-20d` 가 `trading_value_20d_avg` 를 입력으로 가지나 KRX 일배치가 거래대금 미수집 (High)

**위치**:
- `server/builtin-packs/factors/speculum-builtin-v1.0.0.json` line 303-332
- `server/batch/krx_daily.py` 전체 — OHLCV + market_cap 만 수집

**표준**:
- ADR-0002 D3 + AC-D-05 / AC-P-08

**문제**:
- factor pack 정의가 `trading_value_20d_avg` field 를 input 으로 함. 그러나 일배치는 `fetch_ohlcv_by_date_range` + `fetch_market_cap_by_date_range` 만 호출. trading_value 별도 수집 path 없음.
- `OHLCVRow` 에 `value` (거래대금) 필드는 있으나, batch 가 별도 영구화하지 않고 PriceRecord schema 에도 거래대금 컬럼 없음.
- 결과: 본 factor 가 evaluator 호출 시 silent N/A 또는 KeyError. AC-P-08 (빌트인 indicator 정상 작동) 부분 미통과.

**권장**:
- PriceRecord schema 에 `trading_value` 컬럼 추가 (Alembic migration) + batch 가 OHLCVRow.value 영구화.
- 또는 factor pack 에서 본 factor 일시 제거 + M1 backlog 마킹.
- M0 release 전 `factor_evaluator` 의 dry-run test — 모든 빌트인 factor 가 실 데이터로 N/A 없이 계산되는지 통합 테스트 추가.

---

## V4. Forbidden words KO `진입` 이 일반 동사로도 매치 — false positive 폭증 위험 (High)

**위치**:
- `shared/forbidden-words.json` line 53 — `"진입"` (단독)
- `server/app/services/forbidden_words.py` line 122-130 — 한국어 substring 매치

**표준**: ADR-0007 D4.6 / D4.5 + 8 기둥 §2.2

**문제**:
- "진입" 은 매매 행위 시사이지만 매우 흔한 일반 동사 — "시장 진입", "조건 진입 단계", "Stock Detail 진입" 등 SYSTEM scope 의 정상 UI 라벨 매치.
- 한국어 패턴이 substring 매치이므로 단어 경계 보호 X.
- 화이트리스트 (`allowed_phrases`) 에 일반 동사 "진입" 보호 어휘 0.
- ADR-0007 D9.2 의 "어휘 제거 / 화이트리스트 확장 — 무거움" 정책으로 어휘 제거가 어려움.

**권장**:
- `"진입"` 단독을 SoT 에서 제거하거나 `"진입 시점"` / `"매매 진입"` 같은 phrase 로 좁힘.
- `allowed_phrases` 에 정상 collocation 추가.
- `adr-NNNN-forbidden-word-removal-jin-ip.md` 신설 권장.

---

## V5. ADR-0007 D4.1 본문 어휘 list 가 `shared/forbidden-words.json` SoT 와 불일치 (High)

**위치**:
- `docs/adr/adr-0007-default-ui-rules.md` line 83-87 (D4.1 본문 24 어휘)
- `shared/forbidden-words.json` ko_absolute line 6-55 (47 어휘)

**표준**: ADR-0007 D4.6 / D9

**문제**:
- ADR 본문 D4.1 의 어휘 list (24) 와 SoT JSON 의 ko_absolute (47) 가 불일치.
- ADR-0007 D9.1 정책상 SoT 갱신은 OK 이지만, ADR 본문이 incomplete 상태로 misleading.
- ADR-0007 D4.6 자체가 "ADR 본문은 정책 근거만 명시, 구체 어휘 list 는 JSON 참조" 라 명시 — 그렇다면 D4.1 의 어휘 list 자체가 ADR 에 있어선 안 되거나 "예시 (full list 는 JSON)" 으로 명시 의무.

**권장**:
- ADR-0007 D4.1, D4.2 본문의 어휘 list 를 "(예시 — full list 는 `shared/forbidden-words.json` 참조)" 로 변경. 또는 본문 갱신을 CI 게이트 (`shared/forbidden-words.json` 변경 시 ADR-0007 갱신 PR 강제) 로 자동화.

---

## V6. KRX 라이선스 문의 답변 미수신 + ADR-0018 미작성 — release 차단 가능 (High)

**위치**:
- `docs/adr/adr-0006-legal-review.md` D5.2
- `docs/M0_PLAN.md` line 49 (ADR-0018 권장)
- `docs/adr/` — ADR-0018 / ADR-0019 미존재

**표준**: ADR-0006 D5.2 / D9 + AC-L-02

**문제**:
- ADR-0018 / ADR-0019 가 work-order §1.2 의 "M0 release 전 별도 처리" 항목으로 명시되어 본 review 범위 외이지만, **release 차단 조건** 으로서 squash 가능 여부 판단에 결정적.
- AC-L-02 가 미통과면 squash 후 tag v0.1.0 release 가능한가? — squash 판정과 release 판정을 분리 필요.

**권장**:
- **본 review 의 squash 판정은 V1, V2 만 해결되면 가능** — V6 는 release blocker 이지 squash blocker 아님 (squash 후 develop 에 머무는 동안 ADR-0018/0019 + AC-L-02 처리 가능).
- T47 (tag v0.1.0 release) 전 의무 list 명시화 — squash 가능 판정과 release 가능 판정 분리.

---

## V7. `volume` 컬럼이 raw / adjusted 분리 안 됨 — ADR-0001 D4 부분 위반 (Medium)

**위치**:
- `server/app/services/price_adjuster.py:435-455` (`AdjustedPriceRecord.volume: int` 단일)
- `server/app/services/price_adjuster.py:34-41` 본 모듈 docstring (코드 자체 인지)

**표준**: ADR-0001 D4 + 8 기둥 §2.9

**문제**:
- 액면분할 (50:1) 시 가격은 1/50 보정되나 volume 은 raw 보존 → 거래량 시계열의 의미 불연속.
- AC-P-09 는 가격만 covers — volume turnover factor (V3) 의 의미가 추가로 영향받음.

**권장**:
- ADR-0001 D4 amendment + schema `volume_raw` / `volume_adjusted` 분리. M0 release 전 fix 권장이나 minor revision 으로 **Medium**.

---

## V8. 정정공시 supersede chain 처리 미구현 — AC-D-06 부분 위반 (Medium)

**위치**:
- `server/batch/dart_daily.py:36` ("정정공시 supersede chain 자동 처리 X")
- `server/batch/dart_daily.py:351` (`superseded_by=None` 강제)

**표준**: AC-D-06 + ADR-0002 D3 + ADR-0009 D5 + CONCEPT §2.4

**문제**:
- 정정공시 발생 시 같은 자연키의 row 가 두 번 들어가나, 옛 row 의 `superseded_by` 가 새 row.id 로 갱신 안 됨.
- PIT enforcer 의 supersede chain 알고리즘은 무결성 의무화하나, dart_daily 가 chain 빌드 안 하면 두 row 모두 active.
- 운영상: 정정공시 발생 시 historical 재현성 (§2.10) 이 silent 깨짐.

**권장**:
- `dart_daily._process_company` 가 같은 자연키 row 가 있으면 `superseded_by` 를 새 row.id 로 업데이트.
- ADR-0009 D5 의 batch 구현은 후속 cycle — work-order §1.2 의 intended 한계에 명시 추가.

---

## V9. `close_adjusted` 가 batch 에서 raw 와 동일 placeholder (Medium → intended: Low)

**위치**:
- `server/batch/krx_daily.py:407-409, 426-428`
- work-order §1.2 line 42 — "T20 batch 통합" intended

**문제**:
- T18 batch 가 `close_adjusted = close_raw` 로 저장. PriceAdjuster 가 별개로 구현되어 있지만 batch integrate 안 됨.
- 결과: 분할/무상증자 발생한 종목의 historical 가격이 모두 raw. UI 는 adjusted 모드 default 이나 실 데이터는 raw.

**권장**:
- work-order §1.2 명시대로 review 대상 외 — **Low** (intended).
- UI 에 "현재 표시 = raw 가격 (보정 정책 v1.0 batch 통합 후속 사이클)" 명시 권장.

---

## V10. KRX 캘린더 verified 범위 = 2024 단년 — AC-D-04 미통과 (Medium → intended: Low)

**위치**:
- `server/app/services/krx_calendar.py:38`
- work-order §1.2 line 40 — intended

**문제**:
- AC-D-04 가 "지난 3 년 검증" 의무이나 calendar verified 범위 = 2024 단년.
- 가격 시계열 5 년 이상 fetch 시 calendar 범위 밖 일자에 대한 영업일 검증이 `CalendarRangeError`.

**권장**:
- intended — **Low**. AC-D-04 미통과 + work-order intended 와 모순만 기록.

---

## V11. ConflictDetector 가 `estimated_fields` 를 OHLCV cross-check 에서만 사용 (Medium)

**위치**:
- `server/app/services/conflict_detector.py:11-15`
- `server/app/adapters/dart_adapter.py:255` (`estimated_fields=frozenset({"effective_date"})`)
- DART daily batch 는 ConflictDetector 미사용

**표준**: ADR-0003 D4 + ADR-0002 D3

**문제**:
- `estimated_fields` metadata 가 ConflictDetector (OHLCV pykrx vs FDR) 에서만 활용. DART adapter 의 effective_date estimated marker 가 어디서도 사용 안 됨.
- 결과: V1 의 silent look-ahead bias 가 marker 만 달고 운영상 무력.

**권장**:
- SourceCitation 7-tuple 에 `effective_date_confidence` 추가. V1 fix 와 함께.
- 또는 dart_daily 가 batch 후 `data_quality_alerts` 테이블 (ADR-0009 D9) 에 alert 발송.

---

## V12. `assert_clean` ValueError 메시지가 검출 어휘를 echo (Medium)

**위치**:
- `server/app/services/forbidden_words.py:296-323`
- ADR-0007 D4.4.2 의 응답 본문 echo 정책

**표준**: ADR-0007 D4.4.2 + ADR-0006 D2

**문제**:
- ValueError 메시지가 `f"Forbidden words detected (scope=...): {details}"` — 검출 어휘 echo.
- 코드 docstring 이 인지: "FastAPI exception handler 가 generic 500 으로 squash 의무" 그러나 main.py 의 handler 명시 미확인.
- Silent regression 위험.

**권장**:
- `server/app/main.py` 에 ValueError handler 명시 등록.
- 또는 ValueError 자체에 어휘 echo 안 함 — 별도 attribute 로 service-side 만 접근.
- 통합 test 추가.

---

## V13. ADR-0007 D8.3 차트 색상 정책 — M0 차트 미구현 (Low)

**위치**: `docs/adr/adr-0007-default-ui-rules.md` D8.3 / work-order §1.2 line 47

**문제**: lightweight-charts 가 M0 scope 외 — 의도된 한계.

**권장**: M1+ lightweight-charts 합류 시 D8.3 implementation 검증 의무 명시.

---

## V14. NavBar 검색창 disabled — AC-F-02 (250ms 응답) 미통과 (Low)

**위치**: `client/components/NavBar.tsx:57-63` (disabled input)

**문제**:
- NavBar 의 종목 검색 input 이 disabled. backend `/api/stocks/search` 구현되어 있으나 frontend wiring 안 됨.
- AC-F-02 미통과. work-order §1.2 의 intended 한계에 명시되지 않음.

**권장**:
- M0 release 전 frontend wiring. 사용자 우선순위 결정 후 Medium 격상 가능.

---

## 결론

**Squash 가능 판정**: **REJECT (Critical 2 개 해결 필요)**

### Squash 차단 (Critical) — T46 의무

- **V1** — DART effective_date silent look-ahead bias. M0 의 "검경" 정체성 + "정직한 거울" 약속의 가장 큰 risk surface. 반드시 코드 fix 또는 ADR amendment + 사용자 명시 동의 후 squash 가능.
- **V2** — ConsentModal 의 개인정보보호법 제22조 별도 동의 누락 + 처리방침/이용약관 페이지 미존재. AC-L-01 + AC-L-04 미통과.

### Release 차단 (High) — T47 전 의무

- V3 (factor pack volume-turnover input 부재) · V4 (forbidden words `진입` false-positive) · V5 (ADR-0007 본문 SoT 불일치) · V6 (ADR-0018 / ADR-0019 / AC-L-02).

### M1 backlog (Medium / Low)

- V7~V14. work-order §1.2 의 intended 한계와 일관성 검토 필요.

### 정체성 측면의 가장 큰 risk

**V1 (DART PIT) 가 가장 위중**. Speculum 의 차별점 #2 PIT 가 정의: "look-ahead bias 가 있으면 모든 백테스트 결과가 환상". DART effective_date 분기말 영구화 → "검경" 정체성 자체 무력.

코드 코멘트가 "backlog" 인정한 항목이 work-order §1.2 의 intended 에 **명시되어 있지 않음** — 본 review 가 처음 발견. 사용자가 intended 로 인정하려면 ConsentModal + CONCEPT §2.4 + DisclaimerFooter 모두에 명시 의무.

본 검토자는 **minimal fix (PIT enforcer 가 disclosure_lag 60일 보수 적용 + UI 에 estimated marker)** 또는 **DART list endpoint 추가 fetch** 둘 중 하나가 release 의무라 판단. ADR-0012 (또는 ADR-0002 D3 amendment) 신설 필수.

### 검토 외 영역 (라운드 2 권장)

screener_sets.py / runs.py / Alembic 0001~0003 schema 정합성 / `_jcs.py` / `factor_evaluator.py` / `snapshot_versions.py` / `as_of_policy.py` / `pykrx_adapter.py` / `fdr_adapter.py` 의 citation 생성 정확성 / E2E tests / shared schemas. 라운드 2 에서 Critical 2 fix 검증 + 본 영역 검토 필요.
