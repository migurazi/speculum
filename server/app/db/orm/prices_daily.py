"""PriceDailyORM — ADR-0001 의 raw + adjusted 가격.

`app/repositories/pit_protocols.py` 의 `PriceRecord` 의 DB 표현.

핵심 결정:
    - **PK = (code, trade_date)** — KRX 가격은 정정 없음 (supersede 없음). 단,
      lineage `code_lineage_id` 도 함께 인덱스 (lineage 단위 시계열 fetch).
    - `effective_date` 가 곧 `trade_date` (KRX 가격의 PIT 의미는 거래일).
    - `close_adjusted` 는 ADR-0001 의 corporate action 보정 후 종가. raw 와 함께
      보유 — toggle 시 호출자 선택.
    - **citation_id FK** — Fidelity 의 implementation (T23). 모든 row 가 citation
      참조 의무.
    - `id` 는 surrogate UUID — PITRecord protocol 의 `id: UUID` 만족. (PK 가
      composite 이지만 도메인 record 는 단일 id 사용.)

부족 인덱스:
    - PostgreSQL 운영 시 `code_lineage_id` + `effective_date` 복합 인덱스가 lineage
      단위 시계열 query 의 hot path. M1 의 partition (월별) 합류 시점 검토.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


# OHLC + adjusted close 의 Numeric precision/scale.
# 한국 주식 가격 최대 ~9백만원 (LGenergy 부근) → 8자리 정수부 + 4자리 소수부
# (split 후 fractional share 미지원이나 향후 ADR ETF 대응).
_PRICE_NUMERIC = Numeric(precision=18, scale=4)


class PriceDailyORM(Base):
    """prices_daily row — KRX 일별 OHLC + adjusted close + 거래량."""

    __tablename__ = "prices_daily"

    # id 는 surrogate UUID — PIT protocol 호환.
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    code_lineage_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False,
    )
    # effective_date = trade_date (KRX 거래일).
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    open_raw: Mapped[Decimal] = mapped_column(_PRICE_NUMERIC, nullable=False)
    high_raw: Mapped[Decimal] = mapped_column(_PRICE_NUMERIC, nullable=False)
    low_raw: Mapped[Decimal] = mapped_column(_PRICE_NUMERIC, nullable=False)
    close_raw: Mapped[Decimal] = mapped_column(_PRICE_NUMERIC, nullable=False)
    # 거래량 — 한국 주식 일거래량 ~수억주 (BigInteger 안전).
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    close_adjusted: Mapped[Decimal] = mapped_column(_PRICE_NUMERIC, nullable=False)
    citation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_citations.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False,
    )

    __table_args__ = (
        # KRX 가격 PK — (code, trade_date). 같은 code/date 중복 insert 차단.
        PrimaryKeyConstraint("code", "effective_date", name="pk_prices_daily"),
        # oracle 리뷰 C2 — `id` surrogate UUID 가 PITRecord protocol 의 1차 키
        # 로 사용 (record_by_id dict + final tiebreaker). composite PK 와 별개로
        # UNIQUE 강제 — 중복 id 시 silent overwrite 차단.
        UniqueConstraint("id", name="uq_prices_daily_id"),
        # lineage 단위 시계열 fetch 의 hot path.
        Index(
            "ix_prices_daily_lineage_date",
            "code_lineage_id",
            "effective_date",
        ),
    )
