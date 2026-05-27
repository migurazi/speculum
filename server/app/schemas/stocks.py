"""Stocks endpoint wire schemas — `/api/stocks/{code}` + `/api/stocks/search`.

설계 (oracle T25 자문 P0):
- Pydantic v2 strict + extra="forbid" + frozen=True
- Decimal → str wire (JSON number drift 회피, Risk-C4)
- `from_domain(domain) -> SchemaOut` factory (단방향)
- StockStatus enum (결정 6 — 404 vs 200 분리)
- multi-id ambiguous indicators 노출 (AC-P-08) — `_DEFAULT_DISPLAY_FACTORS`
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.repositories.pit_protocols import StockMasterRecord
from app.services.factor_evaluator import EvaluationResult

__all__ = [
    "DEFAULT_DISPLAY_FACTORS",
    "CodeHistoryItemOut",
    "FactorValueOut",
    "StockDetailOut",
    "StockSearchPageOut",
    "StockStatus",
    "StockSummaryOut",
]

# 본 사이클의 Stock Detail 지표 카드 default factor list (oracle 결정 4).
# multi-id ambiguous (PER 의 여러 정의) 의 1 차 selection — primary 정의만.
# 사용자가 `?include_alternates=true` 또는 별도 endpoint 로 전체 보기 가능 (별도 사이클).
DEFAULT_DISPLAY_FACTORS: Final[tuple[str, ...]] = (
    "market-cap:ex-treasury",
    "per:ttm-consolidated-ifrs",
    "pbr:consolidated-ifrs",
    "roe:ttm-avg-equity-consolidated-ifrs",
    "eps:basic-ttm-consolidated-ifrs",
)

_STRICT_MODEL_CONFIG = ConfigDict(strict=True, extra="forbid", frozen=True)


class StockStatus(str, Enum):
    """as_of 시점의 종목 상태 — endpoint 가 200 + status 로 분기 (oracle 결정 6).

    경계 의미 (oracle 2 차 C1 — 반열린 구간):
        `active`: listing_date <= as_of AND (delisting_date is None OR delisting_date > as_of).
            즉 상장 첫날 = active, 폐지 당일 = delisted.
        `not_yet_listed`: as_of < listing_date. lineage 는 존재.
        `delisted`: delisting_date <= as_of. 시계열 보존 (ADR-0009 D7).

    KRX 의미: 정리매매 마지막 영업일이 delisting_date — 그 날까지 거래 가능.
    그러나 본 정책은 PIT-strict — delisting_date 당일은 거래 종료 후 freeze
    시점으로 간주 → DELISTED 분류.
    """

    ACTIVE = "active"
    NOT_YET_LISTED = "not_yet_listed"
    DELISTED = "delisted"


class CodeHistoryItemOut(BaseModel):
    """code_history JSONB 의 단일 entry — wire."""

    model_config = _STRICT_MODEL_CONFIG

    code: str
    valid_from: date
    valid_to: date | None
    reason: str


class StockSummaryOut(BaseModel):
    """검색 결과 / list view 용 요약 — Stock Detail 외 화면."""

    model_config = _STRICT_MODEL_CONFIG

    id: UUID
    code: str
    name: str
    market: str
    listing_date: date
    delisting_date: date | None
    status: StockStatus

    @classmethod
    def from_master(
        cls, master: StockMasterRecord, *, as_of: date,
    ) -> "StockSummaryOut":
        """도메인 → wire 단방향 factory."""
        return cls(
            id=master.id,
            code=master.current_code or "",
            name=master.current_name,
            market=master.market,
            listing_date=master.listing_date,
            delisting_date=master.delisting_date,
            status=_compute_status(master, as_of),
        )


class FactorValueOut(BaseModel):
    """단일 factor 산출값 — Stock Detail 의 지표 카드 1 개."""

    model_config = _STRICT_MODEL_CONFIG

    canonical_id: str
    name: str
    unit: str
    # Decimal → str wire — IEEE 754 drift 회피 (oracle Risk-C4). frontend 가
    # BigNumber/decimal.js 로 처리.
    value: str | None
    is_na: bool
    na_reason: str | None
    # Provenance (Fidelity, §2.1) — 본 사이클은 factor 정의 식별만, citation 은
    # 별도 endpoint 또는 후속 사이클 (T18 합류 후 진짜 citation_id 가 의미).
    evaluator_version: str

    @classmethod
    def from_evaluation(
        cls,
        result: EvaluationResult,
        *,
        factor_name: str,
        factor_unit: str,
    ) -> "FactorValueOut":
        return cls(
            canonical_id=result.factor_canonical_id,
            name=factor_name,
            unit=factor_unit,
            value=(str(result.value) if result.value is not None else None),
            is_na=result.is_na,
            na_reason=result.na_reason,
            evaluator_version=result.evaluator_version,
        )


class StockDetailOut(BaseModel):
    """Stock Detail view 의 wire schema — 지표 카드 + 메타데이터."""

    model_config = _STRICT_MODEL_CONFIG

    id: UUID
    code: str
    name: str
    market: str
    listing_date: date
    delisting_date: date | None
    fiscal_month: int
    ifrs_preference: str
    status: StockStatus
    code_history: tuple[CodeHistoryItemOut, ...]
    factors: tuple[FactorValueOut, ...]

    @classmethod
    def from_master_and_factors(
        cls,
        master: StockMasterRecord,
        factors: tuple[FactorValueOut, ...],
        *,
        as_of: date,
    ) -> "StockDetailOut":
        return cls(
            id=master.id,
            code=master.current_code or "",
            name=master.current_name,
            market=master.market,
            listing_date=master.listing_date,
            delisting_date=master.delisting_date,
            fiscal_month=master.fiscal_month,
            ifrs_preference=master.ifrs_preference_default,
            status=_compute_status(master, as_of),
            code_history=tuple(
                CodeHistoryItemOut(
                    code=e.code, valid_from=e.valid_from,
                    valid_to=e.valid_to, reason=e.reason,
                )
                for e in master.code_history
            ),
            factors=factors,
        )


class StockSearchPageOut(BaseModel):
    """검색 결과 페이지 — cursor-aware schema (M0 구현은 offset, oracle 결정 5).

    `next_cursor` 는 M0 = None 항상. M1+ cursor 도입 시 schema 무변경 — frontend
    의 infinite scroll 패턴이 first-write 그대로 작동.
    """

    model_config = _STRICT_MODEL_CONFIG

    items: tuple[StockSummaryOut, ...]
    total: int | None
    next_cursor: str | None


# =============================================================================
# Helpers
# =============================================================================

def _compute_status(master: StockMasterRecord, as_of: date) -> StockStatus:
    """as_of 시점의 종목 상태 산출 — oracle 결정 6."""
    if master.listing_date > as_of:
        return StockStatus.NOT_YET_LISTED
    if master.delisting_date is not None and master.delisting_date <= as_of:
        return StockStatus.DELISTED
    return StockStatus.ACTIVE
