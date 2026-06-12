"""TreasurySharesORM — ADR-0002 D5 + ADR-0009 D5 의 정정공시 chain.

`app/repositories/pit_protocols.py` 의 `TreasurySharesRecord` 의 DB 표현.
financials.py 패턴 미러 — DART `stockTotqySttus.json` (주식의 총수 현황) 의
보통주 자기주식수를 fiscal_period 단위로 영구화. 정정공시 (재무제표 정정) 시
같은 (code, fiscal_period) 의 새 row 추가 + 옛 row 의 `superseded_by` set.

핵심 결정 (financials.py 와 동일):
    - `id` = surrogate UUID PK — 정정공시 chain (`superseded_by`) 의 self-FK.
      자연키 (code, fiscal_period) 는 정정 시 같은 자연키의 새 row 추가되므로
      PK 후보 X.
    - `superseded_by` = nullable self-FK. 새 정정 row 추가 시 옛 row 의 본
      컬럼 = 새 row.id. PITEnforcer 가 chain 해소 (latest_active_by_key).
    - **이중 hot path 인덱스**:
        - (code, effective_date) — `fetch_latest_active` 의 PIT scan.
        - superseded_by — supersede chain 추적.
    - `shares_treasury` = BigInteger — 한국 주식 자사주 ~수억주 (BigInteger
      안전). market_caps.shares_outstanding 과 동일 type.

append-only 트리거 (ADR-0020):
    - financials / corporate_actions 와 동일하게 정정 chain (superseded_by) 을
      가지므로 조건부 BEFORE UPDATE 트리거 (NULL→set 단일 전이만 허용) 대상.
      Alembic migration 에서 트리거 추가 (PG 전용, SQLite 는 repository layer
      검증이 방어선 — ADR-0020 D5).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class TreasurySharesORM(Base):
    """treasury_shares row — 보통주 자기주식수 (fiscal_period 단위, 정정 chain)."""

    __tablename__ = "treasury_shares"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    code_lineage_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False,
    )
    # `effective_date` = financials 와 동일 (ADR-0012 D6) — stockTotqySttus.json
    # 응답의 rcept_no 앞 8자리 (YYYYMMDD = 접수일자) 에서 직접 도출한 정밀 공시일.
    # 도출 실패 시 자본시장법 제160조 신고기한 보수값 fallback (ADR-0012 D1):
    # Q1~Q3 = 분기 종료 + 45일, Q4 = 사업연도 종료 + 90일.
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    # DART 일배치 표준 = `f"{year}Q{quarter}"` (예: "2024Q1") — financials 동일.
    fiscal_period: Mapped[str] = mapped_column(String(16), nullable=False)
    # 보통주 자기주식수 (stockTotqySttus.json 의 tesstk_co). 한국 주식 자사주
    # ~수억주 (BigInteger 안전). market_caps.shares_outstanding 과 동일 type.
    shares_treasury: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # ADR-0012 D6 — effective_date 가 rcept_no 도출 실 공시일이면 True, 신고기한
    # 보수값 fallback 이면 False (financials 와 동일). server-side default false.
    effective_date_precise: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false",
    )
    citation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_citations.id"),
        nullable=False,
    )
    # ADR-0009 D5 — 정정공시 chain. self-FK nullable (financials 와 동일).
    superseded_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("treasury_shares.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False,
    )

    __table_args__ = (
        # PIT scan 의 hot path — code 별 effective_date 범위 (fetch_latest_active).
        Index(
            "ix_treasury_shares_code_date",
            "code",
            "effective_date",
        ),
        # supersede chain 추적 — 정정공시 발생 시 옛 row 찾기.
        Index("ix_treasury_shares_superseded_by", "superseded_by"),
        # lineage 단위 시계열 scan hot path — financials.ix_financials_lineage_date
        # 와 일관성 (종목코드 변경 lineage 의 historical fetch).
        Index(
            "ix_treasury_shares_lineage_date",
            "code_lineage_id",
            "effective_date",
        ),
    )
