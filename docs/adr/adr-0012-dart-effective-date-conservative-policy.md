# ADR-0012: DART effective_date 의 보수적 신고기한 정책

| | |
|---|---|
| **Status** | ACCEPTED (M0 v0.1.0; M1 minor revision 2026-05-29 — D6 정밀화: rcept_no 직접 도출, list.json 폐기) |
| **Date** | 2026-05-28 (rev. 2026-05-29) |
| **Deciders** | 사용자 |
| **Related** | [[adr-0002-factor-fact-model]] D3, [[adr-0003-data-source-adapter]] D2, `docs/CONCEPT.md §2.4 PIT`, `docs/work-orders/m0-conformance-review-rev1.md` V1, `docs/work-orders/m1-milestone.md` T53/T54 |

## Context

Momus M0 conformance review V1 (Critical) 발견:

> `dart_adapter.py:392, 524-532` 가 `_fiscal_quarter_end` 로 effective_date 를
> 분기말 (3-31, 6-30, 9-30, 12-31) 로 영구화. 실제 DART 보고서 접수일 (rcept_dt)
> 은 분기말 + 45~60 일. PIT Enforcer 의 `record.effective_date <= as_of` 비교가
> as_of=2024-04-15 같은 시점에 1Q 데이터를 사용할 수 있게 함 → CONCEPT §2.4
> "그 시점에 알 수 없던 데이터를 사용하지 않는다" **정면 위반**.

원인:
- 기존 `_fiscal_quarter_end` docstring 이 "PIT 의미상 가장 보수적" 이라고 자칭
  했지만, 정확히는 **가장 비보수적** — 분기말 시점에는 보고서가 미공시 상태.
- 정확한 `rcept_dt` 는 DART `list.json` endpoint 의 별도 fetch 가 필요. M0
  scope 외.

해결책 선택지:
1. **DART list.json endpoint 추가 fetch** — 정확하나 호출 수 2x + cycle 큼.
2. **신고기한 (deadline) 으로 보수 lag 적용** — 정확하지 않으나 false-negative
   (실제 사용 가능했던 데이터를 못 본 것으로) 만 발생. silent look-ahead bias
   (false-positive) 0. Speculum 의 "정직한 거울" 정체성에 부합.
3. **PIT Enforcer 에 estimated_fields 인식 + lag 적용** — schema 변경 필요
   (SourceCitation/Record). M0+ backlog.

**M0 결정**: **#2 (신고기한 lag) — minimal fix + 정직성 보장**.

## Decision

### D1. effective_date 산출 = 분기 종료 + 신고기한 (lag days)

자본시장법 제160조의 신고기한을 effective_date 산출에 적용:

| fiscal_quarter | 보고서 종류 | 신고기한 | effective_date 산출 (캘린더 산술) |
|---|---|---|---|
| Q1 | 1분기 보고서 | 분기 종료 + 45일 | year-03-31 + timedelta(45) = year-05-15 |
| Q2 | 반기 보고서 | 분기 종료 + 45일 | year-06-30 + timedelta(45) = year-08-14 |
| Q3 | 3분기 보고서 | 분기 종료 + 45일 | year-09-30 + timedelta(45) = year-11-14 |
| Q4 | 사업 보고서 | 사업연도 종료 + 90일 | year-12-31 + timedelta(90) ≈ (year+1)-03-31 (윤년 시 -03-30) |

근거 — 자본시장법 제160조 (분기·반기·사업 보고서 제출):
- 분기보고서·반기보고서: 사업연도의 각 분기 종료일부터 45일 이내
- 사업보고서: 사업연도 종료일부터 90일 이내

신고기한이면 100% 공시 완료 보장 — silent look-ahead bias 0.

### D2. 산출 함수 명명 변경

`_fiscal_quarter_end` → `_disclosure_deadline`. 의미가 "분기말" 이 아닌 "신고기한
완료 시점" 임을 명확화. 함수 시그니처는 동일 (`year: int, quarter: int → date`).

### D3. estimated_fields marker (M0) → effective_date_precise 컬럼 (M1)

**M0 v0.1.0 (구)**: `FetchResult.estimated_fields = frozenset({"effective_date"})`
를 항상 보존 (effective_date 가 항상 신고기한 추정값이었음).

**M1 (현)**: effective_date 가 rcept_no 도출 정밀 공시일 (D6) 인지, 신고기한
보수 추정값인지에 따라 분기:
- 정밀 (rcept_no 도출 성공) → `estimated_fields = frozenset()` (marker 불요).
- 보수 fallback (도출 실패) → `estimated_fields = frozenset({"effective_date"})`.

이 정밀 여부는 in-memory marker 를 넘어 `financials.effective_date_precise`
+ `treasury_shares.effective_date_precise` BOOLEAN 컬럼 (Alembic 0010) 으로
영속화 — record/ORM round-trip. ConflictDetector / 운영 alerting 의 추정 신호
channel 은 fallback 시 estimated_fields 로 그대로 보존.

### D4. PIT Enforcer 변경 없음

기존 PIT Enforcer 의 `effective_date <= as_of` 의미론은 그대로. 단지 dart_adapter
가 채우는 effective_date 값이 보수적 신고기한 → look-ahead bias 0 보장. PIT
Enforcer 의 schema 확장 (estimated_fields 인식) 은 M1+ backlog.

### D5. 사용자 시각 한계 명시

`/disclaimer` 페이지 §3 (Point-in-Time 의 한계) 에 "분기 재무 데이터의
effective_date 는 자본시장법 신고기한 기준 보수적 근사" 명시 — T46 V2 fix 의
disclaimer 페이지에 이미 포함됨.

### D6. M1 정밀화 — rcept_no 직접 도출 (list.json 폐기)

**M0 (구 계획)**: DART `list.json` endpoint 를 별도 fetch 하여 정확한 rcept_dt
취득 (work order T53/T54). 호출 +1, rate limit 영향.

**M1 (현 결정)**: **list.json fetch 불필요 — rcept_no 에서 직접 도출**.

조사 결과:
- `fetch_financial_statement` 의 응답 (fnlttSinglAcntAll.json) 각 row 에
  `rcept_no` (접수번호) 가 **필수 필드**로 이미 존재 (없으면 AdapterError).
  코드가 이미 citation identifier + DART 뷰어 URL (`rcpNo={rcept_no}`) 로 사용.
- DART `rcept_no` = 14자리 = **앞 8자리 YYYYMMDD = 접수일자 (공시일)** + 6자리
  일련번호. 즉 정확한 공시일이 이미 응답에 포함.
- treasury (stockTotqySttus.json) 도 `rcept_no` 보유 (`_parse_treasury_response`
  추출).

→ `dart_adapter._rcept_date(rcept_no: str) -> date | None` helper 가 rcept_no
앞 8자리에서 정밀 공시일을 직접 도출 (별도 fetch 0, rate-limit 무관, 더 정확).

**방어 fallback** (schema 예상 외 시 look-ahead 0 유지):
- rcept_no 가 14자리 숫자가 아니거나 (placeholder/빈값/drift 포함) 앞 8자리가
  유효 date (YYYYMMDD, 연도 2000~2100) 가 아니면 `_rcept_date` 가 None →
  D1 의 자본시장법 신고기한 보수값 + `effective_date_precise = False` +
  `estimated_fields = {"effective_date"}` 로 안전 fallback.
- 도출 성공 시 `effective_date = 실 공시일`, `effective_date_precise = True`,
  `estimated_fields = frozenset()`.

**list.json 접근 폐기 사유**: rcept_no 가 이미 정확한 공시일을 담고 있어 별도
endpoint 호출이 중복·비효율. work order T53 의 list.json fetch / rate-limit
스크립트는 구현하지 않음 (moot).

**기존 estimate row backfill 제외**: 신규 fetch 는 처음부터 precise 이므로 moot.
정정공시 batch 처리 (supersede-chain 정밀화) 는 별도 cycle (M3 deferred).

**지각 공시 (precise > 신고기한) 거동** (T66 Momus M1 review 명문화 권고): 회사가
자본시장법 신고기한을 넘겨 지각 공시한 경우 rcept_no 의 실 공시일(precise)이 D1 의
신고기한 보수값보다 **늦다**. 이때도 도출 성공이면 정밀 공시일을 그대로 채택한다
(`effective_date_precise = True`). 이는 look-ahead 0 을 더 강하게 보장(실제 시장
가용 시점이 보수 추정보다 늦으므로 PIT 안전)하나, M0 의 "보수값만" 거동 대비
`effective_date` 가 **늦어질 수 있다** — 정상 동작이며 회귀 아님. early disclosure
불변식 `precise ≤ deadline` 은 **기한 내 정상 공시에만** 적용되며(테스트
`test_dart_adapter.py::test_precise_disclosure_not_later_than_deadline` 가 그 케이스를
검증), 지각 공시는 그 늦은 가용 시점을 사실대로 반영하는 것이 PIT 정합이다.

### D7. ADR-0002 D3 의 docstring 정정

`server/app/repositories/pit_protocols.py:96` 의 docstring "DART rcept_dt 가 아닌
회계기간 등" 은 ADR-0002 D3 ("effective_date # 그 데이터의 발효일 (DART rcept_dt,
KRX trd_dt)") 와 정면 모순. 본 ADR 의 D1 (신고기한 보수) 이 적용된 후 정확한
docstring:

> `effective_date` 의 의미:
> - KRX (가격): trade_date (정확).
> - DART (재무제표): 본 record 가 시장에 100% 가용해진 시점. M1 정밀화 —
>   DART 응답 rcept_no (14자리) 앞 8자리 (YYYYMMDD = 접수일자 = 공시일) 에서
>   직접 도출 (ADR-0012 D6, `effective_date_precise = True`). 도출 실패 시
>   자본시장법 신고기한 (Q1~Q3 = +45일, Q4 = +90일) 보수값 fallback
>   (ADR-0012 D1, `effective_date_precise = False`). 어느 경우든 look-ahead 0.

## Rationale

1. **8 기둥 §2.4 PIT 정직성 회복** — silent look-ahead bias 0. false-negative
   (실제 사용 가능했으나 못 본 것) 은 안전 측면.
2. **Schema 변경 0** — Alembic migration 불필요. M0 minimal fix.
3. **자본시장법 제160조 의 기준** — 모든 회사 무조건 공시 완료 시점. 보수적
   하한.
4. **"정직한 거울" 정체성 부합** — over-conservative > silent over-claim.
   사용자가 백테스트 결과를 환상으로 받지 않음.

## Consequences

### Positive

- **Silent look-ahead bias 제거** — Momus V1 Critical 해결 (M0).
- **M1 정밀화 (D6)** — rcept_no 직접 도출로 신규 fetch 가 정확한 공시일 사용.
  별도 list.json fetch 0 (rate-limit 무관). 도출 실패 시 보수 fallback 으로
  look-ahead 0 유지.
- **법적 명확성** — 자본시장법 제160조 의 단단한 인용 (fallback 근거).
- **effective_date_precise 영속화** — 정밀/보수 구분이 in-memory marker 를
  넘어 컬럼 (Alembic 0010) 으로 보존 → 재현·감사 가능.

### Negative

- **False-negative**: 실제 일찍 공시한 종목의 데이터 사용 못 함 → as_of 와 실
  공시일 사이의 lag 동안 데이터 미존재로 표시. 보수적 측면이라 정직성 가치
  > UX 가치 — 사용자에게 "이 시점에는 데이터 미공개" 명시.
- **백테스트의 ~45일 지연** — 백테스트 (M1+ 도입) 의 trigger 시점이 +45~90일.
  보수적 측면.

### Neutral / Unknown

- **DART 정정공시 lag**: 정정공시는 신고기한 이후 발생 가능. supersede chain
  (ADR-0009 D5) 의 batch 구현 후속 cycle 에서 함께 처리.
- **분기 lag 의 사용자 인식**: `/disclaimer` 페이지 §3 명시되어 있으나 UI 가
  "현재 시점 가용 데이터" 의 의미를 강조 필요. M1 backlog.

## Alternatives Considered

- **A. DART list.json endpoint fetch** — 정확한 rcept_dt. M0 에서 M1 work-order
  로 분리했으나, M1 조사 결과 **rcept_no 가 이미 응답 필수 필드로 정확한 공시일
  (앞 8자리) 을 담고 있어 list.json 호출이 중복·비효율 → 영구 폐기** (D6).
  rcept_no 직접 도출 (호출 +0) 채택. **거부 (list.json), 채택 (rcept_no 도출)**.
- **B. PIT Enforcer 가 estimated_fields 인식 + lag 적용** — schema 변경 (Source
  Citation 7-tuple 에 confidence 필드 추가). M0 minimal fix 정신 위반. **거부**.
- **C. 분기말 그대로 + ConsentModal 에 한계 명시** — silent look-ahead bias 가
  Speculum 의 "검경" 정체성 자체 무력. ConsentModal 명시만으로는 부족. **거부**.
- **D. 사용자별 lag 정책 설정** — over-engineering. M0 scope 외. **거부**.

## References

- 자본시장법 제160조 — [국가법령정보센터](https://www.law.go.kr/lsInfoP.do?lsiSeq=105908)
- DART OpenAPI `list.json` (M1+ work-order) — [opendart.fss.or.kr](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018)
- Momus M0 review V1 — `docs/work-orders/m0-conformance-review-rev1.md`
- ADR-0002 D3 effective_date 정의
- ADR-0003 D2 canonical schema
- CONCEPT §2.4 PIT
- 자매 프로젝트: Norma `docs/CONCEPT.md §2.4` — calibration 의 보수적 측면 일관
