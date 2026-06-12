"""SurvivorshipBackfillBatch 단위 테스트 — 폐지 종목 과거 OHLCV 소급 수집 (ADR-0027).

네트워크 0 — 실제 PykrxAdapter + mock pykrx module(다일 OHLCV) + Fake repository.

테스트 매트릭스:
    1. list_delisted_before — 폐지(<=cutoff)만, 활성 제외, 결정적 정렬
    2. happy path — 단일 윈도우 전기간 fetch → 저장 + citation
    3. 중복 회피 — 이미 있는 날짜 재저장 안 함(rows_skipped_existing)
    4. 빈 윈도우 관용 — pykrx 데이터 부재 → empty_windows, 종목은 성공
    5. market 필터 — KOSPI 만
    6. 활성 종목 제외 — delisting_date None 은 universe 미포함
    7. dry_run — DB write 0, 집계만
    8. failure isolation — 한 종목 실패가 다른 종목 막지 않음
    9. code_history 다중 윈도우 — code 변경 종목은 code 별 기간 fetch
    10. batch_run 영속화 — start/finalize(success)
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pandas as pd

from app.adapters.pykrx_adapter import PykrxAdapter
from app.repositories.batch_run_repository import FakeBatchRunRepository
from app.repositories.citation_repository import FakeCitationRepository
from app.repositories.fakes import FakePriceRepository
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    PriceRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from batch.survivorship_backfill import SurvivorshipBackfillBatch

# =============================================================================
# Fixtures — mock pykrx module (다일 OHLCV) + 종목 마스터
# =============================================================================

def _ohlcv_df(days: list[date], close: float = 1000.0) -> pd.DataFrame:
    """pykrx 한글 컬럼 OHLCV DataFrame — 여러 일."""
    n = len(days)
    return pd.DataFrame(
        {
            "시가": [close] * n,
            "고가": [close] * n,
            "저가": [close] * n,
            "종가": [close] * n,
            "거래량": [1000] * n,
            "거래대금": [close * 1000] * n,
        },
        index=pd.to_datetime(days),
    )


def _make_pykrx_mock(
    *,
    days_by_code: dict[str, list[date]],
    empty_codes: set[str] | None = None,
) -> Any:
    """code → 반환할 거래일 목록. empty_codes 는 빈 DataFrame(데이터 부재)."""
    empty_codes = empty_codes or set()

    def _ohlcv(fromdate: str, todate: str, code: str) -> pd.DataFrame:
        if code in empty_codes:
            return pd.DataFrame()
        days = days_by_code.get(code, [])
        lo = date.fromisoformat(f"{fromdate[:4]}-{fromdate[4:6]}-{fromdate[6:]}")
        hi = date.fromisoformat(f"{todate[:4]}-{todate[4:6]}-{todate[6:]}")
        in_range = [d for d in days if lo <= d <= hi]
        if not in_range:
            return pd.DataFrame()
        return _ohlcv_df(in_range)

    mock = MagicMock()
    mock.get_market_ohlcv = MagicMock(side_effect=_ohlcv)
    return mock


def _delisted_record(
    *,
    code: str,
    market: str = "KOSPI",
    listing: date,
    delisting: date,
    code_history: tuple[CodeHistoryEntry, ...] | None = None,
) -> StockMasterRecord:
    """폐지 종목 record — current_code=None, delisting_date 설정."""
    history = code_history or (
        CodeHistoryEntry(
            code=code, valid_from=listing, valid_to=None, reason="initial_listing",
        ),
    )
    return StockMasterRecord(
        id=uuid4(),
        current_code=None,  # 폐지 → 현재 활성 코드 없음
        current_name=f"폐지종목{code}",
        market=market,
        listing_date=listing,
        delisting_date=delisting,
        fiscal_month=12,
        code_history=history,
    )


def _active_record(*, code: str, listing: date) -> StockMasterRecord:
    return StockMasterRecord(
        id=uuid4(),
        current_code=code,
        current_name=f"활성종목{code}",
        market="KOSPI",
        listing_date=listing,
        delisting_date=None,
        fiscal_month=12,
        code_history=(
            CodeHistoryEntry(
                code=code, valid_from=listing, valid_to=None, reason="initial_listing",
            ),
        ),
    )


def _price_record(*, code: str, day: date) -> PriceRecord:
    """중복 회피 테스트용 기존 가격 row."""
    return PriceRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=uuid4(),
        effective_date=day,
        open_raw=Decimal("1000"),
        high_raw=Decimal("1000"),
        low_raw=Decimal("1000"),
        close_raw=Decimal("1000"),
        volume=1000,
        trading_value=Decimal("1000000"),
        close_adjusted=Decimal("1000"),
        citation_id=uuid4(),
        created_at=datetime.now(UTC),
    )


def _batch(
    *,
    masters: list[StockMasterRecord],
    days_by_code: dict[str, list[date]],
    empty_codes: set[str] | None = None,
    price_repo: FakePriceRepository | None = None,
    batch_run_repo: FakeBatchRunRepository | None = None,
) -> tuple[SurvivorshipBackfillBatch, FakePriceRepository, FakeCitationRepository]:
    price_repo = price_repo if price_repo is not None else FakePriceRepository([])
    citation_repo = FakeCitationRepository()
    batch = SurvivorshipBackfillBatch(
        primary_adapter=PykrxAdapter(
            pykrx_module=_make_pykrx_mock(
                days_by_code=days_by_code, empty_codes=empty_codes,
            )
        ),
        stocks_master_repo=FakeStocksMasterRepository(masters),
        citation_repo=citation_repo,
        price_repo=price_repo,
        throttle_seconds=0.0,  # test 빠르게
        batch_run_repo=batch_run_repo,
    )
    return batch, price_repo, citation_repo


_LISTING = date(2018, 1, 2)
_DELIST = date(2020, 6, 15)
_DAYS = [date(2020, 6, 10), date(2020, 6, 11), date(2020, 6, 12)]


# =============================================================================
# 1. list_delisted_before — repository contract
# =============================================================================

def test_list_delisted_before_filters_and_sorts() -> None:
    active = _active_record(code="005930", listing=_LISTING)
    d1 = _delisted_record(code="900110", listing=_LISTING, delisting=date(2020, 6, 15))
    d2 = _delisted_record(code="900120", listing=_LISTING, delisting=date(2019, 3, 1))
    future = _delisted_record(
        code="900130", listing=_LISTING, delisting=date(2021, 1, 1),
    )
    repo = FakeStocksMasterRepository([active, d1, d2, future])

    result = repo.list_delisted_before(cutoff=date(2020, 12, 31))
    # 활성 제외 + cutoff 이후 폐지(future) 제외. delisting_date asc 정렬.
    assert [r.delisting_date for r in result] == [date(2019, 3, 1), date(2020, 6, 15)]


# =============================================================================
# 2. happy path
# =============================================================================

def test_happy_path_saves_full_window() -> None:
    rec = _delisted_record(code="900110", listing=_LISTING, delisting=_DELIST)
    batch, price_repo, citation_repo = _batch(
        masters=[rec], days_by_code={"900110": _DAYS},
    )
    summary = batch.run(cutoff=date(2020, 12, 31))

    assert summary.delisted_universe_size == 1
    assert summary.success_count == 1
    assert summary.failure_count == 0
    assert summary.rows_saved == 3
    assert summary.rows_skipped_existing == 0
    saved = price_repo.fetch_prices("900110", as_of=_DELIST, start=_LISTING)
    assert {p.effective_date for p in saved} == set(_DAYS)
    assert len(citation_repo._by_id) >= 1  # citation 저장됨


# =============================================================================
# 3. 중복 회피
# =============================================================================

def test_dedup_skips_existing_dates() -> None:
    rec = _delisted_record(code="900110", listing=_LISTING, delisting=_DELIST)
    # 3일 중 1일은 이미 DB 에 존재.
    price_repo = FakePriceRepository([_price_record(code="900110", day=_DAYS[0])])
    batch, price_repo, _ = _batch(
        masters=[rec], days_by_code={"900110": _DAYS}, price_repo=price_repo,
    )
    summary = batch.run(cutoff=date(2020, 12, 31))

    assert summary.rows_saved == 2          # 신규 2일만
    assert summary.rows_skipped_existing == 1
    # 기존 1 + 신규 2 = 3일 모두 존재.
    saved = price_repo.fetch_prices("900110", as_of=_DELIST, start=_LISTING)
    assert {p.effective_date for p in saved} == set(_DAYS)


# =============================================================================
# 4. 빈 윈도우 관용
# =============================================================================

def test_empty_window_tolerated_not_failure() -> None:
    rec = _delisted_record(code="900110", listing=_LISTING, delisting=_DELIST)
    batch, price_repo, _ = _batch(
        masters=[rec], days_by_code={}, empty_codes={"900110"},
    )
    summary = batch.run(cutoff=date(2020, 12, 31))

    assert summary.success_count == 1   # 실패 아님
    assert summary.failure_count == 0
    assert summary.empty_windows == 1
    assert summary.rows_saved == 0
    assert price_repo.fetch_prices("900110", as_of=_DELIST, start=_LISTING) == []


# =============================================================================
# 5. market 필터
# =============================================================================

def test_market_filter() -> None:
    kospi = _delisted_record(
        code="900110", market="KOSPI", listing=_LISTING, delisting=_DELIST,
    )
    kosdaq = _delisted_record(
        code="900120", market="KOSDAQ", listing=_LISTING, delisting=_DELIST,
    )
    batch, price_repo, _ = _batch(
        masters=[kospi, kosdaq],
        days_by_code={"900110": _DAYS, "900120": _DAYS},
    )
    summary = batch.run(cutoff=date(2020, 12, 31), market="KOSPI")

    assert summary.delisted_universe_size == 1
    assert summary.rows_saved == 3
    # KOSDAQ 종목은 미수집.
    assert price_repo.fetch_prices("900120", as_of=_DELIST, start=_LISTING) == []


# =============================================================================
# 6. 활성 종목 제외
# =============================================================================

def test_active_record_excluded() -> None:
    active = _active_record(code="005930", listing=_LISTING)
    batch, _price, _cit = _batch(
        masters=[active], days_by_code={"005930": _DAYS},
    )
    summary = batch.run(cutoff=date(2020, 12, 31))
    assert summary.delisted_universe_size == 0
    assert summary.rows_saved == 0


# =============================================================================
# 7. dry_run
# =============================================================================

def test_dry_run_no_write() -> None:
    rec = _delisted_record(code="900110", listing=_LISTING, delisting=_DELIST)
    batch, price_repo, citation_repo = _batch(
        masters=[rec], days_by_code={"900110": _DAYS},
    )
    summary = batch.run(cutoff=date(2020, 12, 31), dry_run=True)

    assert summary.dry_run is True
    assert summary.rows_saved == 3          # 저장될 행 수는 집계
    # 실제 DB write 0.
    assert price_repo.fetch_prices("900110", as_of=_DELIST, start=_LISTING) == []
    assert len(citation_repo._by_id) == 0


# =============================================================================
# 8. failure isolation
# =============================================================================

def test_failure_isolation() -> None:
    # code_history 가 빈 종목 → _fetch_windows 빈 목록 → AdapterError(실패).
    bad = StockMasterRecord(
        id=uuid4(), current_code=None, current_name="bad", market="KOSPI",
        listing_date=_LISTING, delisting_date=_DELIST, fiscal_month=12,
        code_history=(),
    )
    good = _delisted_record(code="900110", listing=_LISTING, delisting=_DELIST)
    batch, price_repo, _ = _batch(
        masters=[bad, good], days_by_code={"900110": _DAYS},
    )
    summary = batch.run(cutoff=date(2020, 12, 31))

    assert summary.failure_count == 1
    assert summary.success_count == 1
    # 실패 종목이 있어도 정상 종목은 저장됨.
    assert summary.rows_saved == 3


# =============================================================================
# 9. code_history 다중 윈도우 (코드 변경)
# =============================================================================

def test_code_change_multi_window() -> None:
    change_day = date(2019, 1, 2)
    rec = _delisted_record(
        code="900110", listing=_LISTING, delisting=_DELIST,
        code_history=(
            CodeHistoryEntry(
                code="900110", valid_from=_LISTING, valid_to=change_day,
                reason="initial_listing",
            ),
            CodeHistoryEntry(
                code="900111", valid_from=change_day, valid_to=None,
                reason="code_change",
            ),
        ),
    )
    old_days = [date(2018, 6, 1), date(2018, 6, 2)]
    new_days = [date(2020, 6, 10), date(2020, 6, 11), date(2020, 6, 12)]
    batch, price_repo, _ = _batch(
        masters=[rec],
        days_by_code={"900110": old_days, "900111": new_days},
    )
    summary = batch.run(cutoff=date(2020, 12, 31))

    assert summary.rows_saved == len(old_days) + len(new_days)
    # 각 code 가 자기 기간 데이터로 저장됨.
    assert {p.effective_date for p in price_repo.fetch_prices(
        "900110", as_of=change_day, start=_LISTING)} == set(old_days)
    assert {p.effective_date for p in price_repo.fetch_prices(
        "900111", as_of=_DELIST, start=change_day)} == set(new_days)


# =============================================================================
# 10. batch_run 영속화
# =============================================================================

def test_batch_run_persisted() -> None:
    rec = _delisted_record(code="900110", listing=_LISTING, delisting=_DELIST)
    batch_run_repo = FakeBatchRunRepository()
    batch, _price, _cit = _batch(
        masters=[rec], days_by_code={"900110": _DAYS},
        batch_run_repo=batch_run_repo,
    )
    summary = batch.run(cutoff=date(2020, 12, 31))

    run = batch_run_repo.fetch_by_id(summary.batch_id)
    assert run is not None
    assert run.success_count == 1
