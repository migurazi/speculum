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
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from app.db.orm.corporate_actions import CorporateActionORM
from app.db.orm.custom_packs import CustomPackORM
from app.db.orm.financials import FinancialORM
from app.db.orm.macro_indicators import MacroIndicatorORM
from app.db.orm.market_caps import MarketCapDailyORM
from app.db.orm.notes import StockNoteORM
from app.db.orm.portfolio_transactions import PortfolioTransactionORM
from app.db.orm.prices_daily import PriceDailyORM
from app.db.orm.screen_runs import ScreenRunSnapshotORM
from app.db.orm.screener_sets import ScreenerSetORM
from app.db.orm.source_citations import SourceCitationORM
from app.db.orm.stock_snapshots import StockSnapshotORM
from app.db.orm.stocks_master import StocksMasterORM
from app.db.orm.treasury_shares import TreasurySharesORM
from app.db.orm.watchlists import WatchlistFolderORM, WatchlistItemORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.custom_pack_repository import (
    CustomPack,
    CustomPackDataError,
)
from app.repositories.notes_repository import Note
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    CorporateActionRecord,
    FinancialRecord,
    MacroIndicatorRecord,
    MarketCapRecord,
    PriceRecord,
    StockMasterRecord,
    StockSnapshotRecord,
    TreasurySharesRecord,
)
from app.repositories.portfolio_repository import PortfolioTransaction
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
    "macro_indicator_orm_to_record",
    "macro_indicator_record_to_orm",
    "pack_orm_to_record",
    "pack_record_to_orm",
    "market_cap_orm_to_record",
    "market_cap_record_to_orm",
    "note_orm_to_record",
    "note_record_to_orm",
    "portfolio_transaction_orm_to_record",
    "portfolio_transaction_record_to_orm",
    "price_orm_to_record",
    "price_record_to_orm",
    "screen_run_orm_to_record",
    "screen_run_record_to_orm",
    "screener_set_orm_to_record",
    "screener_set_record_to_orm",
    "stocks_master_orm_to_record",
    "stocks_master_record_to_orm",
    "treasury_shares_orm_to_record",
    "treasury_shares_record_to_orm",
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
        trading_value=record.trading_value,
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
        trading_value=orm.trading_value,
        close_adjusted=orm.close_adjusted,
        citation_id=orm.citation_id,
        created_at=orm.created_at,
    )


# =============================================================================
# MarketCap — KRX 시가총액, supersede 없음 (prices 패턴)
# =============================================================================

def market_cap_record_to_orm(record: MarketCapRecord) -> MarketCapDailyORM:
    """frozen dataclass → ORM."""
    return MarketCapDailyORM(
        id=record.id,
        code=record.code,
        code_lineage_id=record.code_lineage_id,
        effective_date=record.effective_date,
        market_cap=record.market_cap,
        shares_outstanding=record.shares_outstanding,
        shares_treasury=record.shares_treasury,
        citation_id=record.citation_id,
        created_at=record.created_at,
    )


def market_cap_orm_to_record(orm: MarketCapDailyORM) -> MarketCapRecord:
    """ORM → frozen dataclass."""
    return MarketCapRecord(
        id=orm.id,
        code=orm.code,
        code_lineage_id=orm.code_lineage_id,
        effective_date=orm.effective_date,
        market_cap=orm.market_cap,
        shares_outstanding=orm.shares_outstanding,
        shares_treasury=orm.shares_treasury,
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
        effective_date_precise=record.effective_date_precise,
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
        effective_date_precise=orm.effective_date_precise,
        citation_id=orm.citation_id,
        superseded_by=orm.superseded_by,
        created_at=orm.created_at,
    )


# =============================================================================
# TreasuryShares — 정정공시 chain (financials 패턴)
# =============================================================================

def treasury_shares_record_to_orm(
    record: TreasurySharesRecord,
) -> TreasurySharesORM:
    """frozen dataclass → ORM."""
    return TreasurySharesORM(
        id=record.id,
        code=record.code,
        code_lineage_id=record.code_lineage_id,
        effective_date=record.effective_date,
        fiscal_period=record.fiscal_period,
        shares_treasury=record.shares_treasury,
        effective_date_precise=record.effective_date_precise,
        citation_id=record.citation_id,
        superseded_by=record.superseded_by,
        created_at=record.created_at,
    )


def treasury_shares_orm_to_record(
    orm: TreasurySharesORM,
) -> TreasurySharesRecord:
    """ORM → frozen dataclass."""
    return TreasurySharesRecord(
        id=orm.id,
        code=orm.code,
        code_lineage_id=orm.code_lineage_id,
        effective_date=orm.effective_date,
        fiscal_period=orm.fiscal_period,
        shares_treasury=orm.shares_treasury,
        effective_date_precise=orm.effective_date_precise,
        citation_id=orm.citation_id,
        superseded_by=orm.superseded_by,
        created_at=orm.created_at,
    )


# =============================================================================
# MacroIndicator — ECOS 거시지표 vintage 이중 시간축, supersede 없음
# =============================================================================

def macro_indicator_record_to_orm(
    record: MacroIndicatorRecord,
) -> MacroIndicatorORM:
    """frozen dataclass → ORM (insert 직전).

    vintage 이중 시간축(reference_date + vintage_date) 이 모두 그대로 전달됨.
    superseded_by 가 없으므로 단순 field 복사. round-trip 무손실.
    """
    return MacroIndicatorORM(
        id=record.id,
        indicator_id=record.indicator_id,
        reference_date=record.reference_date,
        value=record.value,
        unit=record.unit,
        vintage_date=record.vintage_date,
        citation_id=record.citation_id,
        created_at=record.created_at,
    )


def macro_indicator_orm_to_record(
    orm: MacroIndicatorORM,
) -> MacroIndicatorRecord:
    """ORM → frozen dataclass (fetch 직후).

    vintage 이중 시간축(reference_date + vintage_date) 이 그대로 복원됨.
    PIT 조회 규약(vintage_date <= as_of max)의 적용은 Repository 계층 책임.
    round-trip 무손실.
    """
    return MacroIndicatorRecord(
        id=orm.id,
        indicator_id=orm.indicator_id,
        reference_date=orm.reference_date,
        value=orm.value,
        unit=orm.unit,
        vintage_date=orm.vintage_date,
        citation_id=orm.citation_id,
        created_at=orm.created_at,
    )


# =============================================================================
# StockSnapshot — factor 결과 precomputed (as_of exact lookup, ADR-0002 D5)
# =============================================================================

def _serialize_input_value(value: object) -> object:
    """inputs 의 단일 값을 JSON-safe 로 직렬화 (Decimal→str, 재귀).

    `EvaluationResult.inputs_used` 는 `Decimal | tuple[Decimal, ...]` 를 낸다
    (factor_evaluator.py). stdlib json 은 Decimal 직렬화 불가(TypeError)이고
    float 강제는 §2.1 Fidelity 정밀도를 깬다. value 컬럼과 동일하게 Decimal 을
    str 로 보존하여 무손실 직렬화 (oracle 리뷰 C2). tuple/list 는 재귀.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_serialize_input_value(v) for v in value]
    return value


def stock_snapshot_record_to_orm(
    record: StockSnapshotRecord,
) -> StockSnapshotORM:
    """frozen dataclass → ORM (insert 직전).

    value(Decimal | None)는 str 로 직렬화(정밀도 보존, §2.10 round-trip). None 은
    그대로 NULL(미산정). inputs 는 Decimal→str 재귀 직렬화(`_serialize_input_value`,
    JSON-safe·무손실). data_versions Mapping → dict. id 는 합성 uuid5(record.id) 그대로.
    """
    return StockSnapshotORM(
        id=record.id,
        stock_code=record.stock_code,
        as_of_date=record.as_of_date,
        factor_uuid=record.factor_uuid,
        # Decimal → str (None 보존). read 시 Decimal 로 정확히 복원.
        value=None if record.value is None else str(record.value),
        value_unit=record.value_unit,
        # inputs 는 audit 용(Fidelity) — Decimal 을 str 로 직렬화 보존. read 시
        # 그대로 반환(str/list). 평가 입력이라기보다 산정 근거 기록.
        inputs={k: _serialize_input_value(v) for k, v in record.inputs.items()},
        citation_id=record.citation_id,
        computed_at=record.computed_at,
        data_versions=dict(record.data_versions),
    )


def stock_snapshot_orm_to_record(
    orm: StockSnapshotORM,
) -> StockSnapshotRecord:
    """ORM → frozen dataclass (fetch 직후).

    value str → Decimal(None 보존) — byte-동일 round-trip. inputs 는 직렬화된 형태
    (Decimal→str)로 반환(audit). data_versions dict 복원. id 는 __post_init__ 이
    (stock_code, as_of_date, factor_uuid)로 재계산(orm.id 와 동일 — deterministic).
    """
    return StockSnapshotRecord(
        stock_code=orm.stock_code,
        as_of_date=orm.as_of_date,
        factor_uuid=orm.factor_uuid,
        value=None if orm.value is None else Decimal(orm.value),
        value_unit=orm.value_unit,
        inputs=dict(orm.inputs),
        citation_id=orm.citation_id,
        computed_at=orm.computed_at,
        data_versions=dict(orm.data_versions),
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
        # ADR-0023 D2 — 자산군 분류 round-trip.
        security_type=record.security_type,
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
        # ADR-0023 D2 — 자산군 분류 round-trip.
        # ORM row 에 컬럼 없는 경우(구 migration 이전 테스트 환경) 는 default "common".
        security_type=orm.security_type if orm.security_type is not None else "common",
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
# StockNote — T76 종목별 사용자 메모 (USER_PRIVATE, mutable CRUD)
# =============================================================================
#
# watchlist_item 과 동형의 단순 field 복사. append-only 아님(screen_runs 와 다름).
# Note 생성 경로는 인증 route + repository 뿐 — converter 는 그 경로의 직렬화만
# 담당하며 시스템 생성 경로를 만들지 않는다(T76 시스템 생성 Notes 0).


def note_record_to_orm(record: Note) -> StockNoteORM:
    """frozen dataclass → ORM."""
    return StockNoteORM(
        id=record.id,
        user_id=record.user_id,
        code_lineage_id=record.code_lineage_id,
        body=record.body,
        scope=record.scope,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def note_orm_to_record(orm: StockNoteORM) -> Note:
    """ORM → frozen dataclass."""
    return Note(
        id=orm.id,
        user_id=orm.user_id,
        code_lineage_id=orm.code_lineage_id,
        body=orm.body,
        scope=orm.scope,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


# =============================================================================
# PortfolioTransaction — M3 #4 거래내역 (ADR-0029 D1, append-only)
# =============================================================================
#
# stock_note 와 동형의 단순 field 복사. append-only(updated_at 없음 — screen_runs
# 정신). 거래 생성 경로는 인증 route + repository 뿐(수동 입력) — converter 는 그
# 경로의 직렬화만 담당하며 시스템 생성 경로를 만들지 않는다(시스템 생성 거래 0).


def portfolio_transaction_record_to_orm(
    record: PortfolioTransaction,
) -> PortfolioTransactionORM:
    """frozen dataclass → ORM."""
    return PortfolioTransactionORM(
        id=record.id,
        user_id=record.user_id,
        code_lineage_id=record.code_lineage_id,
        side=record.side,
        quantity=record.quantity,
        unit_price=record.unit_price,
        trade_date=record.trade_date,
        fee=record.fee,
        created_at=record.created_at,
    )


def portfolio_transaction_orm_to_record(
    orm: PortfolioTransactionORM,
) -> PortfolioTransaction:
    """ORM → frozen dataclass."""
    return PortfolioTransaction(
        id=orm.id,
        user_id=orm.user_id,
        code_lineage_id=orm.code_lineage_id,
        side=orm.side,
        quantity=orm.quantity,
        unit_price=orm.unit_price,
        trade_date=orm.trade_date,
        fee=orm.fee,
        created_at=orm.created_at,
    )


# =============================================================================
# CustomPack — ADR-0022 D9 사용자 정의 pack (append-only immutable)
# =============================================================================
#
# screener_set 과 동형의 JSON body 복사 + load 시 content_hash 재검증(note
# converter 의 invariant 재검증 선례). pack 생성 경로는 인증 route + repository
# 뿐 — converter 는 그 경로의 직렬화 + 변조 탐지만 담당(시스템 생성 경로 없음).


def pack_record_to_orm(record: CustomPack) -> CustomPackORM:
    """frozen dataclass → ORM. body 는 JSON 컬럼 그대로 저장."""
    return CustomPackORM(
        id=record.id,
        user_id=record.user_id,
        pack_slug=record.pack_slug,
        version=record.version,
        content_hash=record.content_hash,
        body=record.body,
        factor_count=record.factor_count,
        created_at=record.created_at,
        # ADR-0028 D1 — 공유 visibility round-trip. default 'private'.
        visibility=record.visibility,
        # ADR-0032 D3 — import provenance round-trip. body 가 아닌 row 메타라
        # content_hash 와 무관(봉인 불변). editor 생성 pack 은 None.
        source_url=record.source_url,
    )


def pack_orm_to_record(orm: CustomPackORM) -> CustomPack:
    """ORM → frozen dataclass + load 시 content_hash 재검증(변조 탐지).

    저장된 body 가 DB 직접 변조 등으로 봉인 hash 와 불일치하면 무결성 위반 —
    fail-loud (note converter 의 invariant 재검증 선례). validate_hash 는 body
    내부의 content_hash 와 재계산값을 비교하므로, orm.content_hash(컬럼)와 body
    의 content_hash 정합성도 함께 강제한다.
    """
    # 지연 import — converters 는 service layer 보다 하위. 순환 방지.
    from app.services.factor_pack import HashMismatch, validate_hash

    body = orm.body
    try:
        validate_hash(body)
    except HashMismatch as exc:
        raise CustomPackDataError(
            f"저장된 pack '{orm.pack_slug}' v{orm.version} 의 content_hash 가 "
            f"body 와 불일치 — 무결성 위반(변조 의심): {exc}"
        ) from exc
    if body.get("content_hash") != orm.content_hash:
        raise CustomPackDataError(
            f"저장된 pack '{orm.pack_slug}' v{orm.version} 의 컬럼 content_hash 가 "
            f"body content_hash 와 불일치 — 무결성 위반."
        )
    return CustomPack(
        id=orm.id,
        user_id=orm.user_id,
        pack_slug=orm.pack_slug,
        version=orm.version,
        content_hash=orm.content_hash,
        body=body,
        factor_count=orm.factor_count,
        created_at=orm.created_at,
        # ADR-0028 D1 — 공유 visibility round-trip. 구 row(컬럼 부재 테스트 환경)
        # 는 default 'private'(공유 의도 없는 비공개가 안전 default).
        visibility=orm.visibility if orm.visibility is not None else "private",
        # ADR-0032 D3 — import provenance round-trip. getattr 방어(구 ORM/테스트
        # mock 에 컬럼 부재 시 None). content_hash 와 무관 — 재검증 대상 아님.
        source_url=getattr(orm, "source_url", None),
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
