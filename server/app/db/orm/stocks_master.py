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
    - `security_type` = ADR-0023 D2 — 자산군 분류 (보통주/우선주/ETF/리츠).
      String(16) NOT NULL, server_default "common". 허용값은 모듈 상수 참조.

ADR-0009 D7 (survivorship 보존) — `delisting_date` 가 채워진 row 도 row 삭제 X.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Final, Literal
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, Date, Index, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# =============================================================================
# security_type 도메인 상수 — ADR-0023 D2
# =============================================================================
# 자산군 분류 허용값. lineage entity 의 정체(identity) — PIT 시계열 불변(immutable).
# 우선주→보통주 전환은 발행주식수 감소 + lineage 폐지(delisting_date) 로 처리하며
# security_type 변경이 아님 (ADR-0009 D10, ADR-0023 D2).
# 분포 partition(T78 D5) / screener 필터(T79 D7) 는 별도 사이클 — 여기서 구현 X.
#
# 향후 확장 여지:
#   "spac" (기업인수목적회사), "closed_end_fund" (폐쇄형 펀드) 등이 필요하면
#   ADR-0023 amendment + 본 상수 + migration add CHECK constraint 변경.

SECURITY_TYPE_COMMON: Final[str] = "common"       # 보통주 (KOSPI/KOSDAQ 현행 universe)
SECURITY_TYPE_PREFERRED: Final[str] = "preferred"  # 우선주
SECURITY_TYPE_ETF: Final[str] = "etf"             # 상장지수펀드
SECURITY_TYPE_REIT: Final[str] = "reit"           # 부동산투자신탁 (리츠)

# Literal 타입 — 정적 분석기가 허용값 외의 문자열을 거부.
SecurityTypeLiteral = Literal["common", "preferred", "etf", "reit"]

# 허용값 집합 — 검증 헬퍼용.
_VALID_SECURITY_TYPES: Final[frozenset[str]] = frozenset(
    [SECURITY_TYPE_COMMON, SECURITY_TYPE_PREFERRED, SECURITY_TYPE_ETF, SECURITY_TYPE_REIT]
)

# DB CHECK constraint SQL (V-M2-4 — m2-conformance-review). application 의
# `_VALID_SECURITY_TYPES`(SoT)와 동일 집합을 DB 레벨에서 이중 강제 — 배치 직접
# INSERT/수동 SQL 의 미지원 값을 DB 가 거부(defense-in-depth). sorted 로 결정적
# SQL 문자열(IN 의 값 순서는 집합 의미상 무관하나 재현성 위해 고정). 마이그레이션
# `20260602_0015_security_type_check.py` 의 constraint 와 동일.
_SECURITY_TYPE_CHECK_SQL: Final[str] = "security_type IN (" + ", ".join(
    f"'{v}'" for v in sorted(_VALID_SECURITY_TYPES)
) + ")"


def validate_security_type(value: str) -> str:
    """security_type 허용값 검증. 미지원 값이면 ValueError raise.

    ORM insert / Record 생성 전 방어적 검증에 사용.
    허용값: "common" | "preferred" | "etf" | "reit" (ADR-0023 D2).
    """
    if value not in _VALID_SECURITY_TYPES:
        raise ValueError(
            f"security_type 허용값 외: {value!r}. "
            f"허용값: {sorted(_VALID_SECURITY_TYPES)}"
        )
    return value


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
    # ADR-0023 D2 — 자산군 분류 (보통주/우선주/ETF/리츠). lineage 시계열 불변.
    # 우선주→보통주 전환은 lineage 폐지(delisting_date)로 처리 — security_type 변경 X.
    # 기존 전 종목 backfill = "common" (KOSPI/KOSDAQ 현행 universe = 보통주).
    # 분포 partition(T78 D5) / screener 필터(T79 D7) 는 별도 사이클.
    # server_default="common" — 신규 INSERT 시 값 미지정이면 자동 "common".
    security_type: Mapped[str] = mapped_column(
        String(16), nullable=False,
        default=SECURITY_TYPE_COMMON,
        server_default=SECURITY_TYPE_COMMON,
    )

    __table_args__ = (
        # current_code 의 단순 인덱스 — partial 은 PG 전용이므로 운영 migration
        # 에서 별도 검토. 폐지 종목 (NULL) 이 KOSPI/KOSDAQ ~2,500 종목 중 소수.
        Index("ix_stocks_master_current_code", "current_code"),
        Index("ix_stocks_master_market", "market"),
        # ADR-0023 D2 / V-M2-4 — security_type 허용값 DB CHECK. application
        # validate_security_type 와 이중 강제(create_all DB 에 적용 — 마이그레이션
        # 0015 는 운영 기존 DB 용, 본 constraint 는 신규 생성 DB 용으로 동일 효과).
        CheckConstraint(
            _SECURITY_TYPE_CHECK_SQL, name="ck_stocks_master_security_type",
        ),
    )
