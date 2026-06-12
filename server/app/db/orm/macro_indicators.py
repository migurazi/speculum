"""MacroIndicatorORM — ECOS 거시지표의 vintage 이중 시간축 스키마.

`app/repositories/pit_protocols.py` 의 `MacroIndicatorRecord` 의 DB 표현.
한국은행 ECOS 에서 공표하는 거시지표(기준금리, GDP 성장률, CPI 등)를 vintage
이중 시간축(reference_date × vintage_date)으로 append-only 영구화.

핵심 설계 결정:

**왜 이중 시간축이 필요한가 (PIT 정합성 P0)**:
    ECOS 매크로 지표는 사후 개정된다 — 잠정치가 먼저 공표되고, 이후 같은 기간의
    확정치로 값이 바뀐다(예: GDP 성장률 속보치 → 잠정치 → 확정치). 하나의 시간축
    (reference_date 만)으로는 "언제 알 수 있었던 값인가"를 구분할 수 없어 look-ahead
    bias 가 발생한다. 이를 차단하기 위해:
        - `reference_date`: 지표가 가리키는 기준 시점(예: 2024-01 기준금리 →
          2024-01-01).
        - `vintage_date`: 한국은행이 그 값을 공표/개정한 시점(PIT 의 두 번째 축).
    같은 `(indicator_id, reference_date)` 에 대해 `vintage_date` 가 다른 여러 row
    가 append-only 로 누적된다(잠정→확정).

**왜 superseded_by 가 없는가**:
    financials / treasury_shares 의 정정공시는 사람이 명시적으로 정정 공시를
    올리는 이벤트이므로 superseded_by chain 으로 old row 를 inactive 처리한다.
    반면 ECOS 매크로 개정은 "새 vintage_date 를 가진 새 row" 의 자연 누적으로
    표현된다 — 어떤 UPDATE 도 없고, 개정은 새 vintage row 의 INSERT 다.
    PIT 조회 시 `WHERE indicator_id=? AND vintage_date <= as_of` 중 각
    reference_date 별 `max(vintage_date)` 의 value 를 선택하면 as_of 시점에 알
    수 있었던 vintage 만(look-ahead 0)을 얻는다. 이 조회 규약은 T64
    MacroIndicatorRepository 에서 구현 예정.

**UNIQUE(indicator_id, reference_date, vintage_date) 이유**:
    같은 기준일·같은 공표일의 중복 insert 를 차단(idempotent batch). 세 컬럼 조합이
    자연키다. id surrogate UUID 를 PK 로 두되 이 UNIQUE 로 중복을 차단.

append-only 트리거 (PG 전용):
    macro_indicators 는 superseded_by 가 없어 financials 의 "조건부(superseded_by
    NULL→set 허용)" 트리거가 아닌 **모든 UPDATE + DELETE 를 무조건 차단**하는
    트리거를 사용. Alembic 0011 migration 에서 적용. SQLite(테스트) 는 plpgsql
    미지원으로 dialect 분기 생략 — repository layer 의 INSERT-only 불변식 검증이
    방어선 (ADR-0020 D5 정신).

관련:
    - ADR-0003 D2 (MacroIndicator canonical schema + vintage 이중 시간축 명세)
    - `app/repositories/pit_protocols.py` MacroIndicatorRecord
    - Alembic 0011 (본 ORM 의 migration)
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
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

# 거시지표 값의 Numeric precision/scale.
# ECOS 지표는 금리(소수 2자리), 지수(소수 2자리), GDP(조원 단위 큰 금액) 등 다양.
# Numeric(28, 8) 은 금리 소수점 충분 + 큰 금액 안전 마진.
_VALUE_NUMERIC = Numeric(precision=28, scale=8)


class MacroIndicatorORM(Base):
    """macro_indicators row — ECOS 거시지표의 vintage 이중 시간축 단일 관측값.

    같은 (indicator_id, reference_date) 에 대해 vintage_date 가 다른 여러 row 가
    append-only 로 누적. PIT 조회는 T64 MacroIndicatorRepository 에서 구현.
    """

    __tablename__ = "macro_indicators"

    # surrogate UUID PK — MacroIndicatorRecord.id (PITRecord 호환).
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    # ECOS 통계 식별자 — 통계표·항목 코드 조합 (예: "722Y001/0101000").
    # 실제 ECOS API 코드 체계는 T63 EcosAdapter 에서 확정.
    indicator_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 지표가 가리키는 기준 시점 — 월별 지표는 해당 월의 1일(2024-01-01 = 2024-01).
    # 분기별 지표는 분기 시작일(2024-01-01 = 2024Q1). ECOS 의 TIME 필드를 date 로 정규화.
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)
    # 지표 값 — 금리(%), 지수(pt), 금액(억원·조원) 등 단위는 `unit` 컬럼으로 구분.
    value: Mapped[Decimal] = mapped_column(_VALUE_NUMERIC, nullable=False)
    # 값의 단위 — "percent" / "index" / "krw_100m" / "krw_1t" 등. T63 에서 확정.
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    # 한국은행이 이 값을 공표/개정한 시점 — PIT 의 두 번째 축.
    # `vintage_date <= as_of` 중 max(vintage_date) 가 as_of 시점의 최신 발표값.
    vintage_date: Mapped[date] = mapped_column(Date, nullable=False)
    # ECOS fetch 의 source citation FK — Fidelity (ADR-0002 D3).
    citation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_citations.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False,
    )

    __table_args__ = (
        # 자연키 중복 차단 — 같은 기준일·같은 공표일의 idempotent batch insert 보장.
        # superseded_by 없이 vintage 누적이므로 세 컬럼 조합이 고유해야 함.
        UniqueConstraint(
            "indicator_id",
            "reference_date",
            "vintage_date",
            name="uq_macro_indicators_indicator_ref_vintage",
        ),
        # PIT 조회 hot path:
        #   WHERE indicator_id=? AND vintage_date<=as_of → reference_date 별 max(vintage_date).
        # composite (indicator_id, vintage_date, reference_date) 로 양쪽 조건을 cover.
        Index(
            "ix_macro_indicators_indicator_vintage_ref",
            "indicator_id",
            "vintage_date",
            "reference_date",
        ),
        # reference_date 범위 scan (시계열 표시용) hot path.
        Index(
            "ix_macro_indicators_indicator_ref",
            "indicator_id",
            "reference_date",
        ),
    )
