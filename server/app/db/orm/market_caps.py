"""MarketCapDailyORM — ADR-0003 D2 + ADR-0004 의 일별 시가총액 + 발행주식수.

`app/repositories/pit_protocols.py` 의 `MarketCapRecord` 의 DB 표현.

핵심 결정 (prices_daily.py 패턴 미러):
    - **PK = (code, effective_date)** — KRX 시가총액은 정정 없음 (supersede 없음).
      가격과 동일 PIT 의미 — `effective_date` 가 곧 기준일 (KRX 거래일). lineage
      `code_lineage_id` 도 함께 인덱스 (lineage 단위 시계열 fetch).
    - **citation_id FK** — Fidelity 의 implementation (T23). 모든 row 가 citation
      참조 의무. pykrx market_cap fetch 의 FetchResult citation 을 연결.
    - `id` 는 surrogate UUID — PITRecord protocol 의 `id: UUID` 만족. (PK 가
      composite 이지만 도메인 record 는 단일 id 사용.) prices_daily 와 동일하게
      UniqueConstraint("id") 로 silent overwrite 차단.
    - `shares_treasury` 는 **nullable** — pykrx 가 자사주를 제공하지 않을 때 None.
      절대 0 으로 가정하지 않음 (silent 오류 — DART 보강 별도 cycle, T48c).

append-only 무관:
    - prices_daily 와 동일하게 KRX 가격류 (정정 없음) — source_citations 의
      무조건 차단 트리거 (Alembic 0001) / financials·corporate_actions 의 조건부
      트리거 (ADR-0020, Alembic 0007) 와 무관. market_caps 는 supersede 컬럼이
      없으며 append-only 트리거 대상이 아님.
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

# 시가총액 (원) 의 Numeric precision/scale. 한국 주식 시가총액 max ~수백조원
# (삼성전자 ~5백조원) → Numeric(28, 4) 안전 마진. prices_daily.trading_value 와
# 동일 precision (거래대금류 큰 금액).
_MARKET_CAP_NUMERIC = Numeric(precision=28, scale=4)


class MarketCapDailyORM(Base):
    """market_caps row — KRX 일별 시가총액 + 발행주식수 + (옵션) 자사주."""

    __tablename__ = "market_caps"

    # id 는 surrogate UUID — PIT protocol 호환.
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    code_lineage_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False,
    )
    # effective_date = 기준일 (KRX 거래일) — prices_daily 와 동일 PIT 의미.
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    # 시가총액 (원).
    market_cap: Mapped[Decimal] = mapped_column(
        _MARKET_CAP_NUMERIC, nullable=False,
    )
    # 발행주식수 (자사주 포함) — 한국 주식 발행주식수 ~수십억주 (BigInteger 안전).
    shares_outstanding: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 자사주 수 — pykrx 미제공 시 None. 절대 0 으로 가정하지 않음 (DART 보강 별도 cycle).
    shares_treasury: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
    )
    citation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_citations.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False,
    )

    __table_args__ = (
        # KRX 시가총액 PK — (code, effective_date). 같은 code/date 중복 insert 차단.
        PrimaryKeyConstraint("code", "effective_date", name="pk_market_caps"),
        # prices_daily 와 동일 — `id` surrogate UUID 가 PITRecord protocol 의 1차
        # 키. composite PK 와 별개로 UNIQUE 강제 — 중복 id 시 silent overwrite 차단.
        UniqueConstraint("id", name="uq_market_caps_id"),
        # lineage 단위 시계열 fetch 의 hot path.
        Index(
            "ix_market_caps_lineage_date",
            "code_lineage_id",
            "effective_date",
        ),
    )
