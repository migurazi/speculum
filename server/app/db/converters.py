"""ORM row ↔ frozen dataclass Record 변환.

본 모듈은 SQL repository 가 ORM row 를 도메인 Record (frozen dataclass) 로
반환하기 위한 변환 layer. codebase 의 모든 운영 코드는 dataclass Record 와
상호작용하며 ORM 클래스는 본 모듈과 SQL repository 내부에만 노출.

설계 결정:
    - **dataclass → ORM** — adapter / repository.save 가 사용. 모든 invariant
      (UTC tz, semver, append-only) 는 dataclass `__post_init__` 가 이미 검증 →
      ORM 생성은 단순 field 복사.
    - **ORM → dataclass** — repository.fetch_* 가 사용. ORM 의 nullable 필드를
      dataclass 의 `| None` 으로 그대로 매핑. JSONB 의 list[dict] 는
      `CodeHistoryEntry` 로 복원.
    - **JSON 컬럼 round-trip** — SQLite/PG 모두 list/dict 그대로 stored.
      `code_history` 는 list[dict] → tuple[CodeHistoryEntry, ...] 변환.

Note:
    `SourceCitation` 의 `source` 필드는 `SourceKind` enum. ORM 컬럼은 String —
    변환 시 `SourceKind(orm.source)` 로 enum 복원, `dataclass.source.value` 로
    str 저장.
"""

from __future__ import annotations

from datetime import date
from types import MappingProxyType
from typing import Any

from app.db.orm.corporate_actions import CorporateActionORM
from app.db.orm.financials import FinancialORM
from app.db.orm.prices_daily import PriceDailyORM
from app.db.orm.screen_runs import ScreenRunSnapshotORM
from app.db.orm.screener_sets import ScreenerSetORM
from app.db.orm.source_citations import SourceCitationORM
from app.db.orm.stocks_master import StocksMasterORM
from app.db.orm.watchlists import WatchlistFolderORM, WatchlistItemORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    CorporateActionRecord,
    FinancialRecord,
    PriceRecord,
    StockMasterRecord,
)
from app.repositories.watchlist_repository import (
    ScreenerSet,
    WatchlistFolder,
    WatchlistItem,
)
from app.services.screen_run import ScreenRunQuery, ScreenRunSnapshot

__all__ = [
    "citation_orm_to_record",
    "citation_record_to_orm",
    "corporate_action_orm_to_record",
    "corporate_action_record_to_orm",
    "financial_orm_to_record",
    "financial_record_to_orm",
    "price_orm_to_record",
    "price_record_to_orm",
    "screen_run_orm_to_record",
    "screen_run_record_to_orm",
    "screener_set_orm_to_record",
    "screener_set_record_to_orm",
    "stocks_master_orm_to_record",
    "stocks_master_record_to_orm",
    "watchlist_folder_orm_to_record",
    "watchlist_folder_record_to_orm",
    "watchlist_item_orm_to_record",
    "watchlist_item_record_to_orm",
]


# =============================================================================
# SourceCitation
# =============================================================================

def citation_record_to_orm(record: SourceCitation) -> SourceCitationORM:
    """frozen dataclass → ORM (insert 직전)."""
    return SourceCitationORM(
        id=record.id,
        source=record.source.value,
        identifier=record.identifier,
        retrieved_at=record.retrieved_at,
        effective_date=record.effective_date,
        adapter_version=record.adapter_version,
        batch_id=record.batch_id,
        url=record.url,
        created_at=record.created_at,
    )


def citation_orm_to_record(orm: SourceCitationORM) -> SourceCitation:
    """ORM → frozen dataclass (fetch 직후). invariant 재검증 (defense-in-depth)."""
    return SourceCitation(
        id=orm.id,
        source=SourceKind(orm.source),
        identifier=orm.identifier,
        retrieved_at=orm.retrieved_at,
        effective_date=orm.effective_date,
        adapter_version=orm.adapter_version,
        batch_id=orm.batch_id,
        url=orm.url,
        created_at=orm.created_at,
    )


# =============================================================================
# Price
# =============================================================================

def price_record_to_orm(record: PriceRecord) -> PriceDailyORM:
    """frozen dataclass → ORM."""
    return PriceDailyORM(
        id=record.id,
        code=record.code,
        code_lineage_id=record.code_lineage_id,
        effective_date=record.effective_date,
        open_raw=record.open_raw,
        high_raw=record.high_raw,
        low_raw=record.low_raw,
        close_raw=record.close_raw,
        volume=record.volume,
        close_adjusted=record.close_adjusted,
        citation_id=record.citation_id,
        created_at=record.created_at,
    )


def price_orm_to_record(orm: PriceDailyORM) -> PriceRecord:
    """ORM → frozen dataclass."""
    return PriceRecord(
        id=orm.id,
        code=orm.code,
        code_lineage_id=orm.code_lineage_id,
        effective_date=orm.effective_date,
        open_raw=orm.open_raw,
        high_raw=orm.high_raw,
        low_raw=orm.low_raw,
        close_raw=orm.close_raw,
        volume=orm.volume,
        close_adjusted=orm.close_adjusted,
        citation_id=orm.citation_id,
        created_at=orm.created_at,
    )


# =============================================================================
# Financial
# =============================================================================

def financial_record_to_orm(record: FinancialRecord) -> FinancialORM:
    """frozen dataclass → ORM."""
    return FinancialORM(
        id=record.id,
        code=record.code,
        code_lineage_id=record.code_lineage_id,
        effective_date=record.effective_date,
        fiscal_period=record.fiscal_period,
        account=record.account,
        value=record.value,
        unit=record.unit,
        ifrs_type=record.ifrs_type,
        citation_id=record.citation_id,
        superseded_by=record.superseded_by,
        created_at=record.created_at,
    )


def financial_orm_to_record(orm: FinancialORM) -> FinancialRecord:
    """ORM → frozen dataclass."""
    return FinancialRecord(
        id=orm.id,
        code=orm.code,
        code_lineage_id=orm.code_lineage_id,
        effective_date=orm.effective_date,
        fiscal_period=orm.fiscal_period,
        account=orm.account,
        value=orm.value,
        unit=orm.unit,
        ifrs_type=orm.ifrs_type,
        citation_id=orm.citation_id,
        superseded_by=orm.superseded_by,
        created_at=orm.created_at,
    )


# =============================================================================
# CorporateAction
# =============================================================================

def corporate_action_record_to_orm(
    record: CorporateActionRecord,
) -> CorporateActionORM:
    """frozen dataclass → ORM. `details` Mapping → dict 변환."""
    return CorporateActionORM(
        id=record.id,
        code=record.code,
        code_lineage_id=record.code_lineage_id,
        action_type=record.action_type,
        announced_date=record.announced_date,
        effective_date=record.effective_date,
        payment_date=record.payment_date,
        ratio=record.ratio,
        cash_amount=record.cash_amount,
        details=dict(record.details),
        citation_id=record.citation_id,
        superseded_by=record.superseded_by,
        created_at=record.created_at,
    )


def corporate_action_orm_to_record(
    orm: CorporateActionORM,
) -> CorporateActionRecord:
    """ORM → frozen dataclass. `details` dict 그대로 (immutable view 는 호출자)."""
    return CorporateActionRecord(
        id=orm.id,
        code=orm.code,
        code_lineage_id=orm.code_lineage_id,
        action_type=orm.action_type,
        announced_date=orm.announced_date,
        effective_date=orm.effective_date,
        payment_date=orm.payment_date,
        ratio=orm.ratio,
        cash_amount=orm.cash_amount,
        details=dict(orm.details),
        citation_id=orm.citation_id,
        superseded_by=orm.superseded_by,
        created_at=orm.created_at,
    )


# =============================================================================
# StocksMaster — code_history JSONB ↔ tuple[CodeHistoryEntry, ...]
# =============================================================================

def stocks_master_record_to_orm(record: StockMasterRecord) -> StocksMasterORM:
    """frozen dataclass → ORM. `code_history` tuple → list[dict]."""
    return StocksMasterORM(
        id=record.id,
        current_code=record.current_code,
        current_name=record.current_name,
        market=record.market,
        listing_date=record.listing_date,
        delisting_date=record.delisting_date,
        fiscal_month=record.fiscal_month,
        code_history=[_code_history_entry_to_dict(e) for e in record.code_history],
        ifrs_preference_default=record.ifrs_preference_default,
    )


def stocks_master_orm_to_record(orm: StocksMasterORM) -> StockMasterRecord:
    """ORM → frozen dataclass. `code_history` list[dict] → tuple[CodeHistoryEntry, ...]."""
    history = tuple(
        _dict_to_code_history_entry(e) for e in (orm.code_history or [])
    )
    return StockMasterRecord(
        id=orm.id,
        current_code=orm.current_code,
        current_name=orm.current_name,
        market=orm.market,
        listing_date=orm.listing_date,
        delisting_date=orm.delisting_date,
        fiscal_month=orm.fiscal_month,
        code_history=history,
        ifrs_preference_default=orm.ifrs_preference_default,
    )


def _code_history_entry_to_dict(entry: CodeHistoryEntry) -> dict[str, Any]:
    return {
        "code": entry.code,
        "valid_from": entry.valid_from.isoformat(),
        "valid_to": entry.valid_to.isoformat() if entry.valid_to else None,
        "reason": entry.reason,
    }


def _dict_to_code_history_entry(entry: dict[str, Any]) -> CodeHistoryEntry:
    valid_to_raw = entry.get("valid_to")
    return CodeHistoryEntry(
        code=entry["code"],
        valid_from=date.fromisoformat(entry["valid_from"]),
        valid_to=date.fromisoformat(valid_to_raw) if valid_to_raw else None,
        reason=entry["reason"],
    )


# =============================================================================
# WatchlistFolder / WatchlistItem — T13 Phase B
# =============================================================================

def watchlist_folder_record_to_orm(
    record: WatchlistFolder,
) -> WatchlistFolderORM:
    """frozen dataclass → ORM."""
    return WatchlistFolderORM(
        id=record.id,
        user_id=record.user_id,
        parent_id=record.parent_id,
        name=record.name,
        display_order=record.display_order,
        is_default=record.is_default,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def watchlist_folder_orm_to_record(
    orm: WatchlistFolderORM,
) -> WatchlistFolder:
    """ORM → frozen dataclass."""
    return WatchlistFolder(
        id=orm.id,
        user_id=orm.user_id,
        parent_id=orm.parent_id,
        name=orm.name,
        display_order=orm.display_order,
        is_default=orm.is_default,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def watchlist_item_record_to_orm(record: WatchlistItem) -> WatchlistItemORM:
    """frozen dataclass → ORM."""
    return WatchlistItemORM(
        id=record.id,
        watchlist_id=record.watchlist_id,
        code_lineage_id=record.code_lineage_id,
        note=record.note,
        display_order=record.display_order,
        added_at=record.added_at,
    )


def watchlist_item_orm_to_record(orm: WatchlistItemORM) -> WatchlistItem:
    """ORM → frozen dataclass."""
    return WatchlistItem(
        id=orm.id,
        watchlist_id=orm.watchlist_id,
        code_lineage_id=orm.code_lineage_id,
        note=orm.note,
        display_order=orm.display_order,
        added_at=orm.added_at,
    )


# =============================================================================
# ScreenerSet — T13 Phase B
# =============================================================================

def screener_set_record_to_orm(record: ScreenerSet) -> ScreenerSetORM:
    """frozen dataclass → ORM. conditions/selected_factors tuple → list."""
    return ScreenerSetORM(
        id=record.id,
        user_id=record.user_id,
        name=record.name,
        conditions=[dict(c) for c in record.conditions],
        selected_factors=list(record.selected_factors),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def screener_set_orm_to_record(orm: ScreenerSetORM) -> ScreenerSet:
    """ORM → frozen dataclass. conditions list → tuple of dict."""
    return ScreenerSet(
        id=orm.id,
        user_id=orm.user_id,
        name=orm.name,
        conditions=tuple(
            dict(c) for c in (orm.conditions or [])
        ),
        selected_factors=tuple(orm.selected_factors or []),
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


# =============================================================================
# ScreenRunSnapshot — T13 Phase C
# =============================================================================
#
# nested query 직렬화 — ScreenRunQuery 의 conditions/selected_factors/
# presentation_order 가 단일 JSON object 로 묶임. MappingProxyType (immutable
# Mapping) 의 round-trip 은 fetch 시 새 view 로 wrap.


def screen_run_record_to_orm(record: ScreenRunSnapshot) -> ScreenRunSnapshotORM:
    """frozen dataclass → ORM. nested query / result_codes / data_versions 직렬화.

    Notes:
        ScreenRunQuery.conditions 의 각 Mapping 은 dict 로 변환 (MappingProxyType
        은 JSON 직렬화 불가). presentation_order tuple 은 list. data_versions
        Mapping (MappingProxyType 가능) 도 dict.
    """
    query_json: dict[str, Any] = {
        "conditions": [dict(c) for c in record.query.conditions],
        "selected_factors": list(record.query.selected_factors),
        # presentation_order None 도 JSON null 로 round-trip.
        "presentation_order": (
            list(record.query.presentation_order)
            if record.query.presentation_order is not None
            else None
        ),
    }
    return ScreenRunSnapshotORM(
        id=record.id,
        user_id=record.user_id,
        query=query_json,
        as_of=record.as_of,
        result_codes=list(record.result_codes),
        result_hash=record.result_hash,
        data_versions=dict(record.data_versions),
        computed_at=record.computed_at,
    )


def screen_run_orm_to_record(orm: ScreenRunSnapshotORM) -> ScreenRunSnapshot:
    """ORM → frozen dataclass. JSON → nested dataclass + MappingProxyType view.

    Notes:
        conditions 의 각 dict 는 `MappingProxyType` 로 wrap 하여 read-only view
        — service / endpoint 에서 mutation 차단 (ScreenRunQuery 의 frozen 의미
        보존).
    """
    raw_query = orm.query or {}
    presentation_raw = raw_query.get("presentation_order")
    # oracle 리뷰 M2 — SQLite 의 JSON 컬럼이 숫자를 float 로 반환할 수 있음
    # (numeric 친화성). presentation_order 의 type 선언 `tuple[int, ...]` 보장
    # 위해 명시 int cast. Pydantic 응답 직렬화 시점의 type drift 차단.
    query = ScreenRunQuery(
        conditions=tuple(
            MappingProxyType(dict(c)) for c in raw_query.get("conditions", [])
        ),
        selected_factors=tuple(raw_query.get("selected_factors", [])),
        presentation_order=(
            tuple(int(x) for x in presentation_raw)
            if presentation_raw is not None
            else None
        ),
    )
    return ScreenRunSnapshot(
        id=orm.id,
        user_id=orm.user_id,
        query=query,
        as_of=orm.as_of,
        result_codes=tuple(orm.result_codes or []),
        result_hash=orm.result_hash,
        # data_versions 도 read-only view 로 wrap (frozen 의미 보존).
        data_versions=MappingProxyType(dict(orm.data_versions or {})),
        computed_at=orm.computed_at,
    )
