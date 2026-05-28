"""FinancialORM — ADR-0002 D5 + ADR-0009 D5 의 정정공시 chain.

`app/repositories/pit_protocols.py` 의 `FinancialRecord` 의 DB 표현.

핵심 결정:
    - `id` = surrogate UUID PK — 정정공시 chain (`superseded_by`) 의 self-FK 가
      가능해야 함. 자연키 (code, fiscal_period, account, ifrs_type) 는 정정공시
      시 같은 자연키의 새 row 가 추가되므로 PK 후보 X.
    - `superseded_by` = nullable self-FK. 새 정정공시 row 가 추가될 때 옛 row 의
      이 컬럼 = 새 row.id. PITEnforcer 가 `superseded_by IS NULL OR (chain 끝)`
      해소.
    - **이중 hot path 인덱스**:
        - (code, account, effective_date) — `fetch_financials` 의 PIT scan.
        - superseded_by — supersede chain 추적.
    - `value` = Numeric(38, 4) — 한국 대기업 자산총계 수십조원 (12 자리 정수부
      + 4 자리 소수부 충분).
    - `unit` = "krw" / "ratio" / "percent" 등 (TextChoice 없이 String).
    - `ifrs_type` = "consolidated" | "separate" — ADR-0005.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

# 한국 대기업 자산총계 = 수십조원 (~10^13). precision 38 = PG NUMERIC 한도 호환
# + 안전 마진. scale 4 = 비율 지표 (ROE, 부채비율) 도 같은 컬럼에서 표현.
_FINANCIAL_NUMERIC = Numeric(precision=38, scale=4)


class FinancialORM(Base):
    """financials row — 재무제표 단일 account."""

    __tablename__ = "financials"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    code_lineage_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False,
    )
    # `effective_date` = M0 v0.1.0 의 자본시장법 제160조 신고기한 보수 정책
    # (ADR-0012 D1): Q1~Q3 = 분기 종료 + 45일, Q4 = 사업연도 종료 + 90일.
    # 실 DART rcept_dt 의 정확한 값은 estimated_fields marker 보존 + M1+
    # list.json fetch 합류 시 갱신 (ADR-0012 D6).
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(16), nullable=False)
    # "net_income_consolidated_ifrs" 등 dart_account_mapper 정규화된 account 키.
    account: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[Decimal] = mapped_column(_FINANCIAL_NUMERIC, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    # "consolidated" | "separate" — ADR-0005.
    ifrs_type: Mapped[str] = mapped_column(String(16), nullable=False)
    citation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_citations.id"),
        nullable=False,
    )
    # ADR-0009 D5 — 정정공시 chain. self-FK nullable.
    superseded_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("financials.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False,
    )

    __table_args__ = (
        # PIT scan 의 hot path — (code, account) 별 effective_date 범위.
        Index(
            "ix_financials_code_account_date",
            "code",
            "account",
            "effective_date",
        ),
        # supersede chain 추적 — 정정공시 발생 시 옛 row 찾기.
        Index("ix_financials_superseded_by", "superseded_by"),
        # lineage 단위 시계열 scan 의 hot path — Momus M0 review W4 fix.
        # 종목코드 변경 (재상장 / 합병 후 신규 코드) lineage 의 historical
        # financials fetch 시 PostgreSQL 의 sequential scan 회피.
        # prices_daily 의 `ix_prices_daily_lineage_date` 와 일관성.
        Index(
            "ix_financials_lineage_date",
            "code_lineage_id",
            "effective_date",
        ),
    )
