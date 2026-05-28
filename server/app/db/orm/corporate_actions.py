"""CorporateActionORM — ADR-0009 D1 의 이중 PIT entity.

`app/repositories/pit_protocols.py` 의 `CorporateActionRecord` 의 DB 표현.

핵심 결정 (ADR-0009):
    - **이중 PIT** — `announced_date` (정보 가용성) + `effective_date` (효력 발생,
      권리락일 등). PIT 쿼리는 보통 effective_date 기준이지만, "공시된 시점에 알
      수 있었던 사건" 이 필요할 때 announced 기준도 사용.
    - **action_type** = "split" | "stock_dividend" | "rights_offering" | "merger"
      | "spinoff" | "treasury_buy" | "treasury_sell" | ... (ADR-0009 D2 enum).
      String 컬럼 + 호출자 검증.
    - **details** = JSONB — action_type 별 추가 필드 (ratio, cash_amount 외).
    - `superseded_by` = 정정공시 chain.
    - **인덱스**: (code, announced_date), (code, effective_date), superseded_by.

ratio/cash_amount 는 nullable — action_type 별로 사용 여부 다름:
    - split 은 ratio 만 사용 (1:N 분할 비율).
    - cash dividend (M1) 는 cash_amount 만.
    - rights_offering 은 ratio + cash_amount 모두.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

_CA_NUMERIC = Numeric(precision=20, scale=8)


class CorporateActionORM(Base):
    """corporate_actions row — ADR-0009 D1 이중 PIT entity."""

    __tablename__ = "corporate_actions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    code_lineage_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    announced_date: Mapped[date] = mapped_column(Date, nullable=False)
    # effective_date 는 ADR-0009 D5 의 "권리락일" 등 — 사건 효력 발생일.
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ratio: Mapped[Decimal | None] = mapped_column(_CA_NUMERIC, nullable=True)
    cash_amount: Mapped[Decimal | None] = mapped_column(
        _CA_NUMERIC, nullable=True,
    )
    # action_type 별 가변 필드. PostgreSQL JSONB, SQLite JSON.
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    citation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_citations.id"),
        nullable=False,
    )
    superseded_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("corporate_actions.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False,
    )

    __table_args__ = (
        # announced 기준 PIT (정보 가용성 시점).
        Index(
            "ix_corporate_actions_code_announced",
            "code",
            "announced_date",
        ),
        # effective 기준 PIT (사건 효력 발생).
        Index(
            "ix_corporate_actions_code_effective",
            "code",
            "effective_date",
        ),
        # supersede chain 추적.
        Index("ix_corporate_actions_superseded_by", "superseded_by"),
    )
