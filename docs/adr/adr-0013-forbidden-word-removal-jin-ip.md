# ADR-0013: Forbidden word `진입` 단독 제거 — `진입 시점` / `진입시점` phrase 만 유지

| | |
|---|---|
| **Status** | ACCEPTED (M0 v0.1.0) |
| **Date** | 2026-05-28 |
| **Deciders** | 사용자 |
| **Procedure** | [[adr-0007-default-ui-rules]] D9.2 — 어휘 제거의 무거움 절차 |
| **Related** | [[adr-0007-default-ui-rules]] D4.1, D4.6, D9.2; [[adr-0006-legal-review]] D8; `docs/work-orders/m0-conformance-review-rev1.md` V4 |

## Context

Momus M0 conformance review V4 (High) 발견:

> `shared/forbidden-words.json:54` 의 `"진입"` 단독은 매우 흔한 일반 동사 —
> "시장 진입", "조건 진입 단계", "Stock Detail 진입" 등 SYSTEM scope 의 정상
> UI 라벨에 substring 매치. 한국어 패턴이 substring 이므로 단어 경계 보호
> 불가. 화이트리스트에 `진입` 의 일반 사용 보호 collocation 0.

원인:
- `진입` 단독은 매매 행위 시사 (예: "매매 진입 시점") 도 있지만, 비-매매
  맥락 (페이지 진입, 데이터 진입, 시장 진입, 단계 진입 등) 이 더 광범위.
- 한국어 substring 매치의 본질적 한계 — 영어의 `\b` 단어 경계 보호 불가.
- 결과: SYSTEM scope 의 정상 UI 라벨이 silent fail. 운영 시 false-positive
  폭증 위험.

이미 SoT JSON 에는 의도 명확 collocation 이 별도로 등재:
- `"진입 시점"` (line 26)
- `"진입시점"` (line 27)

즉 `진입` 단독 제거 후에도 매매 행위 시사 collocation 은 100% 차단.

## Decision

### D1. SoT 에서 `"진입"` 단독 항목 제거

`shared/forbidden-words.json` 의 `ko_absolute` 배열에서 `"진입"` (line 54) 삭제.
`"진입 시점"` + `"진입시점"` 항목은 유지.

### D2. 대체 메커니즘 — phrase 만 차단

| 어휘 | 차단? | 사용 가능 맥락 |
|---|---|---|
| `진입 시점` | 차단 (SoT line 26) | (매매 진입 시점) — 매매 행위 시사 |
| `진입시점` | 차단 (SoT line 27) | 동일 |
| `진입` 단독 | **허용** | 페이지 진입, 시장 진입, 데이터 진입, 조건 진입, 단계 진입 |

### D3. ADR-0007 D9.2 의 의무 명시

ADR-0007 D9.2 의 어휘 제거 ADR 의 필수 명시 항목:

**[법적 근거]**
- 자본시장법 제101조 의 "조언" 의 정의 = "특정 종목의 매매·종목·시기에 대한
  자문". `진입` 단독은 매매 행위 시사 불충분 — 일반 동사 의미 우세.
- 변호사 직접 자문은 ADR-0019 와 통합 검토 (release 전).

**[false positive 누적 통계]**
- M0 cycle 의 SYSTEM scope 텍스트 정상 사용 사례 (시장 진입 분석, 조건 진입,
  Stock Detail 진입 등) 모두 본 어휘로 silent fail.
- 운영 audit log 누적 통계는 M0 release 후 보강 (M1 D9.1 의 audit-based
  강화).

**[보호 약화 방지 — 대체 메커니즘]**
- `진입 시점` / `진입시점` (phrase) 가 이미 SoT 에 별도 등재. 매매 행위 시사
  collocation 은 100% 보존.
- substring 매치 특성상 `진입 시점` 은 `매매 진입 시점`, `진입 시점 분석` 등
  모든 변형을 cover.
- 추가 collocation 발견 시 D9.1 (어휘 추가 = 가벼움) 절차로 즉시 보강 가능.

### D4. M0 release 전 어휘 제거 의 원칙적 금지 예외 근거

ADR-0007 D9.2 마지막 문장: "M0 release 전 어휘 제거는 원칙적으로 금지 (보호
약화 방향)."

본 결정의 예외 사유:
- **보호 약화 0** — phrase 등재로 매매 시사 collocation 모두 차단 유지.
- **운영 안정성** — false-positive 폭증으로 SYSTEM scope 정상 텍스트의 silent
  fail 이 더 큰 운영 risk (개발 cycle 멈춤, 회피용 부적절 어휘 선택).
- **substring 매치의 본질적 한계** — 영어 `\b` 부재 환경에서 일반 동사 단독
  등재는 false-positive 의 구조적 위험.

## Rationale

1. **§2.2 No Advice 의 보호 약화 0** — phrase 가 매매 시사 collocation 모두
   cover.
2. **운영 신뢰성** — SYSTEM scope 정상 텍스트의 silent fail 차단.
3. **substring 매치 한계의 명시적 인정** — 한국어 패턴 설계의 본질적 trade-off.
4. **ADR-0007 D9.2 절차 준수** — 어휘 제거의 무거움 절차 형식 충족.

## Consequences

### Positive

- **false-positive 폭증 차단** — SYSTEM scope 의 일반 동사 사용 안전.
- **운영 신뢰성** — CI 게이트의 false alarm 0.
- **보호 약화 0** — phrase 등재로 매매 시사 cover 유지.

### Negative

- **단어 경계 모호한 collocation** 에는 phrase 등재 의무 — 운영 audit 누적 시
  새 collocation 마다 D9.1 추가 필요.
- **이상적 단어 boundary 매치 불가** — 한국어 NLP morpheme 분석 도입은 별도
  cycle (M2+).

### Neutral / Unknown

- **`진입` 의 매매 외 단독 사용 사례 발생 시** — D9.1 의 audit-based 추가로
  대응. 본 ADR 의 phrase 정책은 변경 X.

## Alternatives Considered

- **A. `진입` 단독 유지 + allowed_phrases 확장** — `Stock Detail 진입`, `시장
  진입`, `데이터 진입` 등 collocation 별도 등재. 매번 collocation 발견 시
  추가 — 운영 부담. 일반 동사의 본질 변경 X. **거부**.
- **B. 한국어 morpheme 매처 도입 (KoNLPy 등)** — 단어 boundary 정확 인식.
  운영 환경 의존성 추가 + 빌드 시간 증가. M2+ 평가. **거부 (현 cycle)**.
- **C. 매매 시사 collocation 만 phrase 등재 + `진입` 제거 (본 ADR D1)** —
  현 SoT 의 `진입 시점` / `진입시점` 활용 + 제거. **채택**.

## References

- ADR-0007 D4.1, D4.6, D9.2 — 어휘 정책 + 제거 절차
- ADR-0006 D8 — 자본시장법 회피의 코드 수준 implementation
- Momus M0 review V4 — `docs/work-orders/m0-conformance-review-rev1.md`
- 자본시장법 제101조 — [국가법령정보센터](https://www.law.go.kr/lsInfoP.do?lsiSeq=105908)
