"""StockSnapshotORM — ADR-0002 D5 의 stock_snapshots (factor 결과 precomputed).

`app/repositories/pit_protocols.py` 의 `StockSnapshotRecord` 의 DB 표현. 일배치가
universe × factor 를 미리 평가해 저장 → screen/market_overview 의 종목별 실시간
재계산(N+1)을 lookup 으로 대체하기 위한 precompute layer (read 경로 배선은 §2.10
reproduce 통합 설계가 필요한 별도 cycle — 본 cycle 은 **영속 layer** 만).

핵심 결정:
    - **PK = (stock_code, as_of_date, factor_uuid)** — snapshot 은 PIT semantics
      라기보다 `as_of_date == as_of` exact lookup (StockSnapshotRecord docstring).
      같은 (code, 일자, factor) 중복 insert 차단. 정정공시 후 재계산 overwrite 는
      호출자(일배치) 책임 — save_prices/save_financials 와 동일 INSERT-only 정책.
    - **id 는 합성 UUID5** — `uuid5(code|as_of|factor)` 결정적(StockSnapshotRecord.
      __post_init__). composite PK 와 별개로 UniqueConstraint("id") 로 PITRecord
      protocol 의 단일 id 키 + silent overwrite 차단(market_caps 패턴 미러).
    - **value 는 String(nullable)** — factor 값은 ratio·시총 등 scale 이 제각각이라
      고정 Numeric scale 로는 정밀도 손실/round-trip 불일치 위험. 코드베이스의
      Decimal-as-str 직렬화 관례(StatsOut·EquityPointOut wire)와 동일하게 Decimal
      을 str 로 저장해 **byte-동일 round-trip 보장**(§2.10 — 후속 read 경로가
      live 평가값과 정확히 일치해야 함). None = 미산정(분모 0 등, 절대 0 가정 금지).
    - **inputs 는 JSONB(PG)/JSON(SQLite)** — 산정 시 사용한 입력값(Fidelity §2.1).
      corporate_actions.details 와 동일 variant 패턴.
    - **citation_id FK** — 결과의 대표 citation(Fidelity). 모든 row 가 citation 참조.

append-only 무관:
    snapshot 은 derived cache — supersede 컬럼 없음. ADR-0020 조건부 트리거(financials
    /corporate_actions) 대상이 아님. 정정 시 일배치가 재계산 overwrite(M0 한계 —
    snapshot 자체 supersede chain 은 M2+, StockSnapshotRecord docstring).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Date,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class StockSnapshotORM(Base):
    """stock_snapshots row — factor 결과 precomputed snapshot (as_of exact lookup)."""

    __tablename__ = "stock_snapshots"

    # 합성 id — uuid5(code|as_of|factor). PITRecord protocol 의 단일 id 키.
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    factor_uuid: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    # factor 값 — Decimal 을 str 로(정밀도 보존, §2.10 round-trip). None = 미산정.
    value: Mapped[str | None] = mapped_column(String, nullable=True)
    value_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    # 산정 입력값 (Fidelity). PostgreSQL JSONB, SQLite JSON.
    inputs: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False,
    )
    citation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_citations.id"),
        nullable=False,
    )
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    # 산정 당시 freeze fingerprint — screen_runs.data_versions 와 대칭(batch_id·
    # evaluator_version·policy hash). 후속 read-bypass 의 serve-vs-recompute 결정
    # 축(§2.10, oracle C1). JSON/JSONB(str→str map).
    data_versions: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False,
    )

    __table_args__ = (
        # snapshot PK — (stock_code, as_of_date, factor_uuid). exact lookup 키 +
        # 같은 (code, 일자, factor) 중복 insert 차단(재계산 overwrite 는 호출자 책임).
        PrimaryKeyConstraint(
            "stock_code", "as_of_date", "factor_uuid", name="pk_stock_snapshots",
        ),
        # 합성 id surrogate UNIQUE — PITRecord protocol 단일 키 + silent overwrite
        # 차단(market_caps 패턴). id 가 PK 컬럼들의 deterministic 함수라 PK 와 1:1.
        UniqueConstraint("id", name="uq_stock_snapshots_id"),
        # fetch_snapshots_for_code((code, as_of) 의 여러 factor) hot path.
        Index("ix_stock_snapshots_code_asof", "stock_code", "as_of_date"),
    )
