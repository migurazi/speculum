# ADR-0005: K-IFRS 연결 vs 별도 기본 선택

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-05-22 |
| **Deciders** | 사용자 |
| **Related** | `docs/CONCEPT.md §2.6 KRX-Native`, `§2.8 Conformance`, `docs/M0_PLAN.md T5`, [[adr-0002-factor-fact-model]], [[adr-0004-market-cap-eps-per]] |

## Context

K-IFRS (한국채택국제회계기준) 는 회사가 다음 두 종류의 재무제표를 동시에 작성·공시한다:

| 종류 | K-IFRS 약어 | 내용 |
|---|---|---|
| **연결재무제표** | CFS (Consolidated Financial Statements) | 지배회사 + 자회사 합산. 경제적 단위로서의 그룹 전체 |
| **별도재무제표** | OFS (Separate / Stand-alone Financial Statements) | 지배회사 자체의 재무제표 (자회사 미합산) |

DART 공시 양식에 두 종류 모두 들어있음. 같은 회사의 같은 분기에 대해 두 PER 값이 다를 수 있음 — net income 이 다르고, equity 도 다름.

**도구별 default 차이:**
- 네이버 금융 / 다음 금융 / KRX 공식 PER: 연결 우선
- 일부 학술 도구: 별도 (단순성)
- 지주회사·금융지주: 연결의 의미 모호 (자회사가 본 사업) → 별도가 더 의미 있는 경우

[[adr-0002-factor-fact-model]] D2 의 multi-id 정책은 "연결" / "별도" 를 다른 canonical_id 로 분리. 본 ADR 은 **default 선택** 정책.

## Decision

### D1. Default = **연결재무제표 우선, 별도재무제표 fallback**

```
1순위: 연결재무제표 (CFS)
   → 회사가 연결재무제표를 제출했으면 그것 사용
2순위: 별도재무제표 (OFS)
   → 자회사가 없거나 연결 제외 사유 회사
```

**구체 로직** (adapter level):

```python
def select_ifrs_statement(stmt_cfs: Statement | None, stmt_ofs: Statement | None,
                         user_pref: IfrsPreference = IfrsPreference.AUTO) -> Statement:
    if user_pref == IfrsPreference.SEPARATE_ONLY:
        return require(stmt_ofs, "OFS required but not available")
    if user_pref == IfrsPreference.CONSOLIDATED_ONLY:
        return require(stmt_cfs, "CFS required but not available")
    # AUTO = CFS 우선
    return stmt_cfs or stmt_ofs
```

### D2. 두 정의 모두 빌트인 (multi-id)

[[adr-0004-market-cap-eps-per]] D6 의 빌트인 factor 가 ifrs_type 별 variant 보유:

```
per:ttm-consolidated-ifrs   (default, CFS)
per:ttm-separate-ifrs       (OFS)
roe:ttm-avg-equity-consolidated-ifrs   (default)
roe:ttm-avg-equity-separate-ifrs
...
```

사용자가 Stock Detail 에서 "Variants" 메뉴로 토글 가능.

### D3. UI 표시 의무 — 항상 명시

§2.1 Fidelity 의 직접적 implementation:

- 모든 지표 옆 또는 카드에 작은 라벨 "연결" 또는 "별도".
- 차트·테이블 의 컬럼 헤더에 (연결) 표시.
- 토글 변경 시 명시적 UI 피드백 ("별도재무제표 기준으로 전환됨").
- 두 값이 크게 다른 경우 (>20% 차이) 카드에 작은 ⚠ 아이콘 → "연결 ≠ 별도. 어느 정의로 볼지 선택" 안내.

### D4. 회사별 override 정책 — `fiscal_preference` 필드

`stocks_master` 테이블에 `ifrs_preference_default` 컬럼:

```sql
ALTER TABLE stocks_master
ADD COLUMN ifrs_preference_default TEXT NOT NULL DEFAULT 'AUTO';
-- 'AUTO' | 'CONSOLIDATED' | 'SEPARATE'
```

**override 사례** (M1+ 진입 시 결정, M0 는 모두 AUTO):
- 지주회사 (예: SK, LG) — `SEPARATE` 가 더 의미 (자회사는 별도 종목으로 상장된 경우)
- 금융지주 (예: KB금융지주) — 자회사가 본 사업, `CONSOLIDATED` 유지
- 외부 감사 의견 거절·한정 회사 — UI 에 추가 경고

M0 에서 일률 AUTO. 회사별 fine-tuning 은 M1+ 의 별도 작업 (override pack).

### D5. 사용자 글로벌 preference — Settings

사용자 설정에 글로벌 default 토글:

```
[설정] 
├─ K-IFRS 재무제표 우선순위
│    ◉ 자동 (연결 우선, 별도 fallback)         ← default
│    ○ 연결재무제표만
│    ○ 별도재무제표만
```

이 설정은 모든 화면의 default 를 변경. 단, Stock Detail 의 Variants 메뉴는 화면 단위 override 허용.

### D6. 결손·자본잠식 시 fallback

별도재무제표는 정상이나 연결재무제표는 결손인 경우 (또는 vice versa):

- `per:ttm-consolidated-ifrs` 가 `N/A (적자)` 라도 자동으로 별도로 fallback 하지 **않음**. 명시적 N/A 유지.
- 사용자가 Variants 토글로 별도 값 확인.
- 자동 fallback 은 §2.1 Fidelity (왜곡 안 함) 위반 → 명시적 사용자 선택만.

### D7. 보고서 / Export 시 명시

Screen Run snapshot (§2.10) 의 freeze 입력에 `ifrs_preference` 포함:

```json
{
  "query": {
    "conditions": [...],
    "universe": "...",
    "ifrs_preference": "AUTO"
  },
  ...
}
```

같은 query 라도 ifrs_preference 가 다르면 다른 Run hash → 재현성 보장.

## Rationale

1. **사용자 친숙도** — 네이버 / 다음 / KRX 공식 PER 이 모두 연결 우선 → default 일치 시 인지 부담 ↓.
2. **K-IFRS 표준 의도** — 연결재무제표가 기업집단의 경제적 실질을 반영. 별도는 법적 단위 정보.
3. **§2.1 Fidelity** — 자동 fallback 금지. 어느 재무제표인지 항상 visible.
4. **§2.8 Conformance multi-id** — 연결 / 별도가 다른 canonical_id. 두 값을 명시적 비교 가능.
5. **§2.10 Reproducibility** — Screen Run hash 입력 포함.

## Consequences

### Positive
- **명확한 default** — 한 사용자가 한 화면에 보는 PER 이 하나.
- **사용자 글로벌·화면별 토글** — 능동적 탐색 (§2.3) 의 implementation.
- **자동 fallback 금지** — Fidelity 의 직접 보장.
- **회사별 override 자리** (D4) — 지주회사 fine-tuning 준비.

### Negative
- **사용자 인지 부담** — "왜 같은 PER 이 두 종류?" 도움말·tooltip 필요.
- **DART adapter 부담** — 회사가 연결 제출 X 인 경우 (자회사 없음·연결 제외 사유) 처리.
- **저장 부담** — 두 종류 statement 모두 저장 → financials 테이블 row 2배. 사용 빈도 적은 별도는 lazy load 도 검토.

### Neutral / Unknown
- **지주회사 default override 결정 시점** — M1 의 별도 작업. 한국 지주회사 list 가 명확하지 않음 (공시 의존).
- **K-IFRS 신리스 기준 등 회계기준 변경** — 시계열 불연속. [[adr-0009-corporate-action]] 의 회계기준 변경 처리와 연계 필요.

## Alternatives Considered

- **A. 별도재무제표 default** — 단순. 그러나 KRX 공식·네이버 default 와 거리, 학술 표준과도 거리. **거부**.
- **B. 회사별 자동 선택 (지주·금융지주는 별도, 나머지는 연결)** — 정확하나 한국 지주회사 list 의 회사별 분류가 명확하지 않아 ad-hoc 결정. M1+ override pack 으로 처리 (D4). **M0 거부**.
- **C. 항상 두 값 동시 표시** — Fidelity ↑ 이지만 화면 폭증 + 사용자 혼란. Variants 메뉴로 명시적 토글이 균형. **거부**.
- **D. 자동 fallback 허용 (연결 결손이면 별도로)** — 사용성 ↑ 그러나 §2.1 Fidelity 위반. **거부**.

## References

- [한국채택국제회계기준 (K-IFRS) 1110 — 연결재무제표](https://www.kasb.or.kr/) — 1차 표준
- [한국채택국제회계기준 (K-IFRS) 1027 — 별도재무제표](https://www.kasb.or.kr/)
- [KRX 정보데이터시스템 — PER 산출 정의](http://data.krx.co.kr/) — 연결 우선 관행
- [Open DART — 재무제표 구조](https://opendart.fss.or.kr/guide/main.do)
- [[adr-0002-factor-fact-model]] D2 — multi-id 정책
- [[adr-0004-market-cap-eps-per]] D6 — 빌트인 factor list 의 ifrs_type 별 variant
- Norma `docs/CONCEPT.md §2.3` Dual-definition landmark — 같은 라벨 다른 정의 분리 패턴 동형
