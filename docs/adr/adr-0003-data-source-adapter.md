# ADR-0003: Data Source Adapter 패턴

| | |
|---|---|
| **Status** | ACCEPTED |
| **Date** | 2026-05-22 |
| **Deciders** | 사용자 |
| **Related** | `docs/CONCEPT.md §2.5 Open Data Sufficiency`, `§2.8 Conformance`, `docs/ARCHITECTURE.md §3.2`, `docs/M0_PLAN.md T3, T14~T19`, [[adr-0001-price-adjustment]] D3, [[adr-0002-factor-fact-model]] D3, Norma `CONCEPT §2.6 vendor bridge` |

## Context

Speculum 의 데이터는 여러 외부 출처에서 옴 — FDR, pykrx, DART OpenAPI, KRX 정보데이터시스템, 한국은행 ECOS (M1+). 각 출처는:

- 데이터 schema 가 다름 (DART XBRL/HTML, KRX CSV·JSON, FDR pandas DataFrame)
- 라이브러리 인터페이스가 다름 (FDR `DataReader()`, pykrx 함수형, DART REST)
- 에러 모드가 다름 (rate limit, 일시 장애, 데이터 결측)
- 버전 변화 — 라이브러리 업데이트 시 동작 변경 가능
- 라이선스가 다름

이걸 service / batch / API 어디서나 직접 호출하면 다음 문제가 발생:

1. **§2.1 Fidelity 위반** — Source Citation 7 필드 (ADR-0002 D3) 가 매번 다르게 채워짐. 누락 가능.
2. **§2.10 Reproducibility 위반** — 외부 라이브러리 버전이 silent 하게 결과를 바꿔도 추적 안 됨.
3. **테스트 어려움** — 외부 의존이 서비스 코드에 박힘.
4. **충돌 처리 산발** — ADR-0001 D3 의 pykrx vs FDR 가격 충돌 처리가 코드 곳곳에.

자매 프로젝트 Norma 의 §2.6 Vendor Bridge (Dolphin / OnyxCeph / WebCeph 어댑터 패턴) 가 같은 종류의 문제를 푼다. Speculum 도 같은 결로.

## Decision

### D1. Adapter 인터페이스 — Python ABC

각 출처별로 `*Adapter` 클래스. 공통 인터페이스:

```python
# server/app/adapters/base.py
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import TypeVar, Generic, Sequence
from uuid import UUID

T = TypeVar("T")

class FetchResult(Generic[T]):
    data: T
    citations: list[SourceCitation]   # ADR-0002 D3 의 7-tuple
    warnings: list[str]               # 비치명적 데이터 이슈

class DataSourceAdapter(ABC):
    SOURCE_KIND: str                  # "DART" / "KRX" / "FDR" / "PYKRX" / "ECOS"
    ADAPTER_VERSION: str              # semver, 코드 변경 시 bump

    @abstractmethod
    async def health_check(self) -> bool:
        """외부 출처 도달 가능한가."""

    # 각 adapter 가 자기 도메인의 fetch 메서드 정의
    # 예: PykrxAdapter.fetch_ohlcv(code, from_date, to_date) -> FetchResult[OHLCVFrame]
```

**비동기 (`async def`)**:
- FastAPI · SQLAlchemy 2 async 와 정합.
- pykrx / FDR 은 sync — 내부에서 `asyncio.to_thread()` wrapper.
- DART · ECOS · KRX 는 HTTP — `httpx.AsyncClient` 직접.

### D2. Canonical schema 로 정규화

Adapter 의 출력은 항상 canonical schema. service / repository 는 canonical 만 안다.

| 도메인 | Canonical schema (Python TypedDict / Pydantic) | M0 adapter |
|---|---|---|
| **OHLCV** | `OHLCVRow(code, date, open, high, low, close, volume, value, adjusted: dict)` | pykrx (primary), FDR (verify) |
| **시가총액** | `MarketCapRow(code, date, market_cap, shares_outstanding, shares_treasury)` | pykrx |
| **종목 마스터** | `StockMaster(code, name, market, listing_date, delisting_date, sector_krx, fiscal_month, ...)` | pykrx (primary), FDR (verify) |
| **재무제표** | `FinancialStatement(code, fiscal_year, fiscal_quarter, ifrs_type, accounts: dict[str, decimal], rcept_no, rcept_dt)` | DART |
| **Corporate Action** | `CorporateAction(code, action_type, ex_date, ratio, ...)` | DART (1차 자료) |
| **휴장일** | `KrxCalendar(date, is_trading_day, session)` | pykrx |
| **거시지표** | `MacroIndicator(indicator_id, reference_date, value, unit, vintage_date)` | ECOS (M1+) |

Adapter 가 raw 응답을 canonical 로 변환. 변환 logic 은 adapter 코드 + 단위 테스트로 동결.

**MacroIndicator 의 vintage 이중 시간축 (M1 T62 — PIT 정합성 P0)**: ECOS 거시지표는 잠정치가 먼저 공표된 뒤 같은 기간의 값이 확정치로 사후 개정된다(예: GDP 속보치 → 잠정치 → 확정치). 단일 `date` 필드로는 "언제 알 수 있었던 값인가"를 구분할 수 없어 look-ahead bias 가 발생한다. 이를 차단하기 위해 두 번째 시간축 `vintage_date`(한국은행이 그 값을 공표/개정한 시점)를 추가한다. 같은 `(indicator_id, reference_date)` 에 대해 `vintage_date` 가 다른 row 가 append-only 로 누적되며(잠정→확정 개정마다 새 row), PIT 조회 시 `vintage_date <= as_of` 중 각 `reference_date` 별 `max(vintage_date)` 를 선택하면 look-ahead 0 이 보장된다. financials 의 `superseded_by` chain 과 달리 개정 = 새 vintage row INSERT 로 표현하므로 어떤 UPDATE 도 없다(순수 append-only). 스키마/ORM/migration 선설계는 M1 T62, 실제 ECOS fetch adapter 는 T63, Repository 조회 구현은 T64.

### D3. Source Citation 7-tuple 의 자동 채움

Adapter 메서드가 `FetchResult` 를 반환할 때 `citations` 가 의무 — 빌드 게이트 (`check-source-attribution`) 가 누락 검사.

```python
# 예: DartAdapter.fetch_financial_statement
async def fetch_financial_statement(
    self, code: str, fiscal_year: int, fiscal_quarter: int
) -> FetchResult[FinancialStatement]:
    response = await self._client.get(...)  # DART API
    stmt = self._parse_xbrl(response)
    citation = SourceCitation(
        source="DART",
        identifier=response["rcept_no"],
        retrieved_at=datetime.now(UTC),
        effective_date=date.fromisoformat(response["rcept_dt"]),
        adapter_version=self.ADAPTER_VERSION,
        batch_id=self._current_batch_id,   # batch context 에서 주입
        url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={response['rcept_no']}",
    )
    return FetchResult(data=stmt, citations=[citation], warnings=[])
```

### D4. 출처 우선순위 + 충돌 처리

ADR-0001 D3 의 일반화. 도메인별 1차 / 검증 출처 표:

| 도메인 | 1차 출처 (canonical) | 검증 출처 | 충돌 시 |
|---|---|---|---|
| OHLCV (raw) | pykrx | FDR | alert + pykrx 표시 |
| OHLCV (adjusted) | pykrx | FDR | alert + pykrx 표시 (정책 [[adr-0001-price-adjustment]] D3) |
| 시가총액·발행주식수 | pykrx | DART (보강) | alert + pykrx |
| 종목 마스터 | pykrx | FDR | alert + 두 set 의 union 노출 |
| 신규 상장·폐지 | pykrx | KRX 직접 (M2 옵션) | KRX 우선 |
| 재무제표 | DART | (없음) | N/A |
| 업종 분류 | pykrx (KRX 표준) | KSIC (보조) | UI 토글 |
| Corporate Action | DART | pykrx (cross-check) | DART 1차 |

**충돌 감지:**

```python
# server/app/services/data_reconciliation.py
class ConflictDetector:
    """일배치에서 1차 vs 검증 출처를 비교, |diff|/max > threshold 면 alert."""

    THRESHOLDS = {
        "ohlcv.close": 0.001,        # 0.1%
        "market_cap":  0.005,        # 0.5%
        ...
    }

    def detect_and_log(self, primary: T, verify: T) -> list[Conflict]: ...
```

Alert → `data_quality_alerts` 테이블 + Sentry (P2).

### D5. Adapter 버전 (semver)

```python
class PykrxAdapter(DataSourceAdapter):
    SOURCE_KIND = "PYKRX"
    ADAPTER_VERSION = "1.0.0"   # 코드 변경 시 bump
```

**Bump 규칙:**
- **patch** (1.0.1) — 버그 수정. 같은 입력에 같은 출력.
- **minor** (1.1.0) — 새 메서드 추가. 기존 출력 불변.
- **major** (2.0.0) — 기존 출력 schema 변경 또는 logic 변경. **Reproducibility 영향** → Screen Run 들의 hash 재계산 필요.

Major bump 는 ADR 동반 (예: `adr-0050-pykrx-adapter-v2.md`).

### D6. 재시도 / Rate Limit 정책

| 출처 | Rate limit | 정책 |
|---|---|---|
| DART OpenAPI | 일 ~10,000 호출 (키당, 2026 기준 확인) | 분당 throttling + exponential backoff (1s → 30s, max 5 retry) |
| KRX 정보데이터시스템 | 공식 미명시 (관행 ~1 req/s) | 1.5s 간격 throttling |
| pykrx | KRX 페이지 크롤링 — KRX 정책 따름 | 위와 동일 |
| FDR | 외부 소스 (Yahoo, Naver) | 0.5s 간격, 외부 실패는 무시·로그 |
| ECOS | 일 100,000 호출 (M1+) | 충분 |

**Circuit breaker** — 5 분 동안 50% 이상 실패 시 해당 adapter 일시 정지 + alert. Service 는 그 동안 캐시 fallback.

### D7. 디렉토리 구조

```
server/app/adapters/
  __init__.py
  base.py                     # ABC + FetchResult + 공통 예외
  pykrx_adapter.py            # canonical: OHLCV, market_cap, master, calendar
  fdr_adapter.py              # verify: OHLCV, master
  dart_adapter.py             # canonical: financial_statement, corporate_action
  dart_account_mapper.py      # K-IFRS 표준 계정과목 → canonical key
  ecos_adapter.py             # M1+
  krx_adapter.py              # M2+ (KRX 직접 API)
  tests/
    test_pykrx_adapter.py
    test_dart_adapter.py
    fixtures/                 # 응답 sample (version pin)
```

### D8. 빌트인 fixture 기반 테스트

Adapter 의 정합성은 외부 호출 없이 검증 가능해야 함:

- 각 adapter 에 `fixtures/` — 실제 응답을 캡처한 JSON/CSV/XML 파일
- 테스트는 fixture 를 mock response 로 주입 → adapter 출력이 canonical schema 와 일치하는지 검증
- 외부 호출은 `integration/` 별도 디렉토리 (CI 에서 nightly 만)

## Rationale

1. **Norma vendor adapter 동형** — 검증된 패턴. service / repository 가 외부 의존을 모름.
2. **§2.1 Fidelity** — Source Citation 7 필드가 adapter level 에서 강제 → 누락 0.
3. **§2.10 Reproducibility** — `ADAPTER_VERSION` 보존 + fixture 테스트 → 외부 라이브러리 업데이트가 결과 변경하면 즉시 감지.
4. **§2.5 Open Data Sufficiency** — 무료 출처에 의존하면서도 충돌 처리가 명시.
5. **§2.8 Conformance** — DART/KRX 1차 자료 우선 + 검증 출처 분리.
6. **테스트 가능성** — fixture 기반 → CI 빠름, 외부 의존 없음.

## Consequences

### Positive
- **외부 의존이 adapter 안에만** — service 단순.
- **Source Citation 강제** — Fidelity 약속의 코드 수준 implementation.
- **충돌 감지 자동화** — pykrx vs FDR 가격 차이가 silent regression 으로 묻히지 않음.
- **외부 라이브러리 업데이트 추적** — `ADAPTER_VERSION` bump 의무.
- **adapter 단위 단위 테스트** — fixture 기반, 외부 호출 없이.

### Negative
- **boilerplate** — 새 출처 추가 시 adapter 클래스 + 테스트 + fixture.
- **canonical schema 의 evolution** — schema 변경 시 모든 adapter 영향.
- **fixture 유지** — 외부 응답이 미세하게 변경되면 fixture 와 차이 → 정기 업데이트 필요.
- **DART 양식 다양성** — `dart_account_mapper` 가 회사별·연도별 override 필요할 수 있음. M0 에서 단단히 구축 (T16).

### Neutral / Unknown
- **pykrx 의 안정성** — community 라이브러리, KRX 페이지 구조 변경 시 깨질 수 있음. `health_check` + alert.
- **FDR 의 외부 소스 변경** — Yahoo Finance 한국 데이터 정책이 변경되면 FDR 도 영향. 1 차 출처를 pykrx 로 두는 결정의 보강 이유.

## Alternatives Considered

- **A. Adapter 없이 service 에서 직접 호출** — 단순하지만 Fidelity / Reproducibility 모두 위반. **거부**.
- **B. 단일 통합 adapter (모든 출처 합침)** — 클래스 폭증은 피하나 출처별 책임 분리가 흐려짐. **거부**.
- **C. ORM 으로 외부 출처 추상화** — SQLAlchemy 로 외부 API 도 dialect 처럼? 부적절·과도. **거부**.
- **D. 동기 (sync) adapter** — Python 의 GIL + FastAPI async 정합 떨어짐. pykrx/FDR 만 to_thread, 나머지 async 가 균형. **거부**.
- **E. Fixture 없이 외부 호출만 테스트** — CI 느림 + 외부 출처 정전 시 CI 전체 멈춤. **거부**.

## References

- Norma `docs/CONCEPT.md §2.6` (Vendor Bridge — Dolphin/OnyxCeph/WebCeph adapter)
- Tessera `docs/adr/0012-store-receive-outbox-pattern.md` — 외부 의존 분리 패턴 참조
- [pykrx GitHub](https://github.com/sharebook-kr/pykrx)
- [FinanceDataReader GitHub](https://github.com/FinanceData/FinanceDataReader)
- [Open DART OpenAPI 가이드](https://opendart.fss.or.kr/guide/main.do)
- [한국은행 ECOS API](https://ecos.bok.or.kr/api/)
- [[adr-0001-price-adjustment]] D3 — 충돌 처리의 가격 도메인 instance
- [[adr-0002-factor-fact-model]] D3 — Source Citation 7-tuple
