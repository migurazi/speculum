"""Data Source Adapter layer — ADR-0003 의 외부 출처 abstraction.

본 패키지는 외부 데이터 출처 (pykrx / FDR / DART OpenAPI / KRX 직접 / ECOS)
의 access path 단일 source. service / repository / batch 가 직접 외부
라이브러리 호출하지 않도록 강제.

핵심 책임:
    1. **Canonical schema 정규화** — 출처마다 다른 raw 응답을 통일 dataclass
       (OHLCVRow / MarketCapRow / StockMaster / KrxCalendarDay) 로 변환.
    2. **Source Citation 7-tuple 자동 채움** — ADR-0002 D3 의 Fidelity 보장.
    3. **rate limit / 재시도** — adapter 의 호출자 (T18 일배치) 가 정책 적용.
    4. **fixture 기반 단위 테스트** — 외부 호출 없이 변환 logic 검증 (ADR-0003 D8).

T15 = pykrx primary. T14 = FDR verify. T16 = DART (재무제표 + corporate action).

설계 결정 (T15 cycle):
    - **sync API** — codebase 의 sync Repository 정책과 일관. pykrx 도 sync.
      ADR-0003 D1 의 async 권고는 ARCHITECTURE.md §3.1 의 "Async" 의도와 함께
      M1+ 별도 cycle backlog.
    - **adapter version semver** — `ADAPTER_VERSION` class var 가 cite 시 자동
      채움. 코드 변경 시 bump 의무 (D5).

관련 ADR:
- ADR-0003 — 본 패키지의 motivation + 인터페이스 결정
- ADR-0001 D3 — 출처 우선순위 (pykrx vs FDR)
- ADR-0002 D3 — Source Citation 7-tuple
"""

from __future__ import annotations

from app.adapters.base import (
    AdapterError,
    AdapterRetryError,
    DataSourceAdapter,
    FetchResult,
    FinancialStatementRow,
    IfrsType,
    KrxCalendarDay,
    MarketCapRow,
    OHLCVRow,
    StockMaster,
)
from app.adapters.dart_adapter import DartAdapter
from app.adapters.fdr_adapter import FdrAdapter
from app.adapters.pykrx_adapter import PykrxAdapter

__all__ = [
    "AdapterError",
    "AdapterRetryError",
    "DartAdapter",
    "DataSourceAdapter",
    "FdrAdapter",
    "FetchResult",
    "FinancialStatementRow",
    "IfrsType",
    "KrxCalendarDay",
    "MarketCapRow",
    "OHLCVRow",
    "PykrxAdapter",
    "StockMaster",
]
