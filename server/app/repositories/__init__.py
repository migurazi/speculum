"""Repository layer — DB 추상화의 외곽 경계.

본 패키지는 Protocol 만 정의 (T21 standalone). 실제 SQLAlchemy 구현체는 T13
(DB schema + Alembic) 합류 후 별도 모듈로 추가. AC-P-04 (raw query path 0
보증) 의 type-level 1차 방어선 — 모든 method 시그니처가 `as_of: date` 를
keyword-only required 로 받는다.
"""

from app.repositories.pit_protocols import (
    CorporateActionRecord,
    CorporateActionRepository,
    FinancialRecord,
    FinancialRepository,
    PITRecord,
    PriceRecord,
    PriceRepository,
    StockSnapshotRecord,
    StockSnapshotRepository,
    SupersedableRecord,
)

__all__ = [
    "CorporateActionRecord",
    "CorporateActionRepository",
    "FinancialRecord",
    "FinancialRepository",
    "PITRecord",
    "PriceRecord",
    "PriceRepository",
    "StockSnapshotRecord",
    "StockSnapshotRepository",
    "SupersedableRecord",
]
