"""StocksMasterORM — ADR-0009 D6 의 lineage entity.

`app/repositories/pit_protocols.py` 의 `StockMasterRecord` + `CodeHistoryEntry`
의 DB 표현. lineage 단위 row — 종목코드 변경·재상장·합병 시 새 row 가 아닌
`code_history` JSONB append.

핵심 결정:
    - `id` = lineage UUID (영구 보존, 종목코드 변경 무관).
    - `current_code` = 현재 활성 코드. `NULL` = 폐지. 운영 검색의 hot path 이므로
      인덱스. PostgreSQL 에서는 partial index (`WHERE current_code IS NOT NULL`)
      이상적이지만 SQLite 호환 위해 단순 nullable index.
    - `code_history` = JSON 컬럼. PostgreSQL = JSONB, SQLite = JSON (text). 본
      필드는 ADR-0009 D6 의 `[(code, valid_from, valid_to, reason), ...]` list.
    - `market` = "KOSPI" | "KOSDAQ" | "KONEX" (String).
    - `ifrs_preference_default` = ADR-0005 — "AUTO" | "CONSOLIDATED" | "SEPARATE".

ADR-0009 D7 (survivorship 보존) — `delisting_date` 가 채워진 row 도 row 삭제 X.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Date, Index, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StocksMasterORM(Base):
    """stocks_master row — lineage entity."""

    __tablename__ = "stocks_master"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    # `current_code` — 현재 활성 코드. 폐지된 lineage 는 NULL.
    current_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    current_name: Mapped[str] = mapped_column(String(200), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    listing_date: Mapped[date] = mapped_column(Date, nullable=False)
    delisting_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fiscal_month: Mapped[int] = mapped_column(Integer, nullable=False)
    # ADR-0009 D6 — code 변경 history. PostgreSQL = JSONB, SQLite = JSON.
    # value = list of {"code": str, "valid_from": "YYYY-MM-DD",
    #                  "valid_to": "YYYY-MM-DD" | None, "reason": str}.
    code_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    ifrs_preference_default: Mapped[str] = mapped_column(
        String(16), nullable=False, default="AUTO",
    )

    __table_args__ = (
        # current_code 의 단순 인덱스 — partial 은 PG 전용이므로 운영 migration
        # 에서 별도 검토. 폐지 종목 (NULL) 이 KOSPI/KOSDAQ ~2,500 종목 중 소수.
        Index("ix_stocks_master_current_code", "current_code"),
        Index("ix_stocks_master_market", "market"),
    )
