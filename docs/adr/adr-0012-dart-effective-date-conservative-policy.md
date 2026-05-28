# ADR-0012: DART effective_date 의 보수적 신고기한 정책

| | |
|---|---|
| **Status** | ACCEPTED (M0 v0.1.0, 변호사 자문 ADR-0019 와 함께 release 전 재검토) |
| **Date** | 2026-05-28 |
| **Deciders** | 사용자 |
| **Related** | [[adr-0002-factor-fact-model]] D3, [[adr-0003-data-source-adapter]] D2, `docs/CONCEPT.md §2.4 PIT`, `docs/work-orders/m0-conformance-review-rev1.md` V1 |

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

### D3. estimated_fields marker 보존

`FetchResult.estimated_fields = frozenset({"effective_date"})` 는 그대로 유지.
이유:
- 실제 rcept_dt (= 회사가 일찍 공시한 경우) 와 신고기한 사이의 lag 가 잔여 추정.
- M1+ 에서 list.json endpoint fetch 도입 시 marker 가 자연스럽게 제거됨 (정확한 값).
- ConflictDetector / 운영 alerting 의 추정 신호 channel 보존.

### D4. PIT Enforcer 변경 없음

기존 PIT Enforcer 의 `effective_date <= as_of` 의미론은 그대로. 단지 dart_adapter
가 채우는 effective_date 값이 보수적 신고기한 → look-ahead bias 0 보장. PIT
Enforcer 의 schema 확장 (estimated_fields 인식) 은 M1+ backlog.

### D5. 사용자 시각 한계 명시

`/disclaimer` 페이지 §3 (Point-in-Time 의 한계) 에 "분기 재무 데이터의
effective_date 는 자본시장법 신고기한 기준 보수적 근사" 명시 — T46 V2 fix 의
disclaimer 페이지에 이미 포함됨.

### D6. M1+ 정확 fetch 후속 cycle

`docs/work-orders/dart-rcept-dt-precision.md` (신규 work-order, M1 우선순위):
- DART list.json endpoint 통해 정확한 rcept_dt fetch.
- batch (dart_daily) 통합 — rate limit 고려한 효율 fetch.
- 본 ADR 의 보수 정책은 fallback 으로 유지 (list.json 실패 시).
- estimated_fields 가 empty 인 경우 (정확한 fetch 성공) 와 frozen({"effective_date"})
  의 경우 (보수 정책 적용) 의 분기 명시.

### D7. ADR-0002 D3 의 docstring 정정

`server/app/repositories/pit_protocols.py:96` 의 docstring "DART rcept_dt 가 아닌
회계기간 등" 은 ADR-0002 D3 ("effective_date # 그 데이터의 발효일 (DART rcept_dt,
KRX trd_dt)") 와 정면 모순. 본 ADR 의 D1 (신고기한 보수) 이 적용된 후 정확한
docstring:

> `effective_date` 의 의미:
> - KRX (가격): trade_date (정확).
> - DART (재무제표): 본 record 가 시장에 100% 가용해진 시점. M0 v0.1.0 은
>   자본시장법 신고기한 (Q1~Q3 = +45일, Q4 = +90일) 으로 보수 산출
>   (ADR-0012 D1). 실 rcept_dt 의 정확한 값은 estimated_fields marker 보존 +
>   M1+ list.json fetch 합류 시 갱신.

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

- **Silent look-ahead bias 제거** — Momus V1 Critical 해결.
- **Minimal fix** — `_fiscal_quarter_end` 1 함수 + test 갱신만.
- **법적 명확성** — 자본시장법 제160조 의 단단한 인용.
- **estimated_fields marker 보존** — M1+ 정확 fetch 합류 시 자연 갱신.

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

- **A. DART list.json endpoint fetch** — 정확한 rcept_dt. M0 scope 큼 (호출
  +1, rate limit 영향). M1+ work-order 로 분리. **거부 (현 cycle)**.
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
