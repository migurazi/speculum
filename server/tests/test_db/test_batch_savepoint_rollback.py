"""T18/T19 M1 통합 테스트 — SQLite 위에서 종목/회사별 SAVEPOINT rollback.

oracle T18/T19 M1 — 일배치의 종목/회사 처리 중 DB write 실패 시 partial
commit 차단. session.begin_nested() SAVEPOINT 가 정확히 그 종목/회사의
write 만 rollback.

테스트 매트릭스:
    1. KrxDailyBatch — 종목 1 성공 + 종목 2 의 save 단계 실패 → 종목 1 만
       DB 에 영구화 (종목 2 의 in-flight citation 도 rollback).
    2. DartDailyBatch — 회사 1 성공 + 회사 2 의 financial save 실패 → 회사 1
       만 영구화.
    3. session=None (Fake mode) — savepoint 없이 기존 동작 (역호환).
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

import httpx
import pandas as pd
from sqlalchemy.orm import Session

from app.adapters.dart_adapter import DartAdapter
from app.adapters.pykrx_adapter import PykrxAdapter
from app.repositories.citation_repository import SqlCitationRepository
from app.repositories.sql_repositories import (
    SqlFinancialRepository,
    SqlPriceRepository,
)
from app.services.corp_code_mapping import CorpCodeMapping
from app.services.krx_calendar import DEFAULT_CALENDAR
from batch.dart_daily import DartDailyBatch
from batch.krx_daily import KrxDailyBatch

# =============================================================================
# T18 — KrxDailyBatch savepoint
# =============================================================================

def _ohlcv_df(*, day: date, close: float) -> pd.DataFrame:
    """pykrx OHLCV 한 row DataFrame."""
    return pd.DataFrame(
        {
            "시가": [close], "고가": [close], "저가": [close],
            "종가": [close], "거래량": [1_000_000],
            "거래대금": [close * 1_000_000],
        },
        index=pd.to_datetime([day]),
    )


def _market_cap_df(*, day: date) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "시가총액": [400_000_000_000_000],
            "거래량": [1_000_000],
            "거래대금": [400_000_000_000],
            "상장주식수": [5_000_000_000],
        },
        index=pd.to_datetime([day]),
    )


def test_krx_savepoint_rolls_back_failed_code_only(
    db_session: Session,
) -> None:
    """oracle T18 M1 — 종목 2 save 실패 시 종목 1 의 citation/price 는 보존.

    종목 2 는 fetch 까지 성공한 후 save_prices 가 IntegrityError. SAVEPOINT
    ROLLBACK 으로 종목 2 의 citation 도 rollback. 최종 citation_repo 에는
    종목 1 의 citation 만.
    """
    # pykrx mock — 두 종목 모두 fetch 성공.
    pykrx_mock = MagicMock()
    pykrx_mock.get_market_ticker_list = MagicMock(
        return_value=["005930", "000660"],
    )
    pykrx_mock.get_market_ohlcv = MagicMock(
        side_effect=lambda fromdate, todate, code: _ohlcv_df(
            day=date(2024, 5, 7), close=70000.0,
        ),
    )
    pykrx_mock.get_market_cap_by_date = MagicMock(
        side_effect=lambda fromdate, todate, code: _market_cap_df(
            day=date(2024, 5, 7),
        ),
    )

    citation_repo = SqlCitationRepository(db_session)
    price_repo = SqlPriceRepository(db_session)

    # save_prices 가 종목 000660 일 때만 raise — 종목 1 (005930) 성공 후 종목 2
    # 의 save 단계에서 실패.
    original_save = price_repo.save_prices

    def _failing_save(records: Any) -> None:
        if records and records[0].code == "000660":
            raise RuntimeError("simulated DB write failure on 000660")
        original_save(records)

    price_repo.save_prices = _failing_save  # type: ignore[method-assign]

    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=citation_repo,
        price_repo=price_repo,
        throttle_seconds=0,
        session=db_session,  # SAVEPOINT 활성.
    )
    summary = batch.run(as_of=date(2024, 5, 7), market="KOSPI")

    # 종목 1 만 성공.
    assert summary.success_count == 1
    assert summary.failure_count == 1
    assert summary.failures[0][0] == "000660"

    # 영구화된 price = 종목 005930 만.
    prices_005930 = price_repo.fetch_prices(
        "005930", as_of=date(2024, 5, 7), start=date(2024, 5, 7),
    )
    assert len(prices_005930) == 1
    prices_000660 = price_repo.fetch_prices(
        "000660", as_of=date(2024, 5, 7), start=date(2024, 5, 7),
    )
    assert len(prices_000660) == 0  # rollback 됨.

    # 영구화된 citation = universe 1 + 종목 005930 의 OHLCV+market_cap 2 = 3.
    # 종목 000660 의 in-flight citation 은 rollback (SAVEPOINT 효과).
    cited = citation_repo.fetch_by_batch(summary.batch_id)
    assert len(cited) == 3
    identifiers = {c.identifier for c in cited}
    assert any("005930" in i for i in identifiers)
    assert not any("000660" in i for i in identifiers)


# =============================================================================
# T19 — DartDailyBatch savepoint
# =============================================================================

def _dart_row(*, account_id: str = "ifrs-full_Assets", amount: str = "100") -> dict[str, Any]:
    return {
        "account_id": account_id,
        "account_nm": "자산총계",
        "thstrm_amount": amount,
        "rcept_no": "20240501000123",
        "currency": "KRW",
        "sj_div": "BS",
        "bsns_year": "2023",
        "reprt_code": "11011",
    }


def test_dart_savepoint_rolls_back_failed_company_only(
    db_session: Session,
) -> None:
    """oracle T19 M1 — 회사 2 의 financial save 실패 시 회사 1 의 citation/
    financial 만 보존.
    """
    def _handler(request: httpx.Request) -> httpx.Response:
        # 모든 회사 모든 IFRS type 정상 응답.
        return httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": [_dart_row()],
            },
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(_handler), timeout=5.0,
    )
    dart_adapter = DartAdapter(
        http_client=http_client, api_key="test_key",
    )
    citation_repo = SqlCitationRepository(db_session)
    financial_repo = SqlFinancialRepository(db_session)

    # oracle T19 M2 — code 기반 분기 (call_count 보다 견고). 회사 2 (000660) 의
    # FinancialRecord 가 save_financials 에 도달하면 raise. IFRS type 수 변경
    # 에도 안전.
    original_save = financial_repo.save_financials

    def _code_failing_save(records: Any) -> None:
        records_list = list(records)
        if records_list and records_list[0].code == "000660":
            raise RuntimeError("simulated save failure on company 2")
        original_save(records_list)

    financial_repo.save_financials = _code_failing_save  # type: ignore[method-assign]

    mapping = CorpCodeMapping.from_dict({
        "005930": "00126380",
        "000660": "00164779",
    })
    batch = DartDailyBatch(
        adapter=dart_adapter,
        corp_mapping=mapping,
        citation_repo=citation_repo,
        financial_repo=financial_repo,
        throttle_seconds=0,
        session=db_session,
    )
    summary = batch.run(
        stock_codes=["005930", "000660"],
        fiscal_year=2023, fiscal_quarter=4,
    )

    # 회사 1 성공 + 회사 2 실패.
    assert summary.success_count == 1
    assert summary.failure_count == 1
    assert summary.failures[0][0] == "000660"

    # 영구화된 citation — 회사 1 의 CFS+OFS citation 2 개만.
    cited = citation_repo.fetch_by_batch(summary.batch_id)
    assert len(cited) == 2

    # oracle T19 L2 — financial row 직접 검증. 회사 1 영구화 + 회사 2 rollback.
    # _dart_row 의 default account_id = "ifrs-full_Assets" → canonical
    # "total_assets". effective_date 는 rcept_no="20240501000123" 도출 정밀
    # 공시일 (ADR-0012 D6) = 2024-05-01. as_of=2024-05-15 으로 안전 마진.
    fin_005930 = financial_repo.fetch_financials(
        "005930", as_of=date(2024, 5, 15),
        account="total_assets",
    )
    assert len(fin_005930) > 0
    fin_000660 = financial_repo.fetch_financials(
        "000660", as_of=date(2024, 5, 15),
        account="total_assets",
    )
    assert fin_000660 == ()


# =============================================================================
# Backward compat — session=None (Fake mode)
# =============================================================================

def test_krx_session_none_uses_nullcontext() -> None:
    """session=None 일 때 savepoint 없이 기존 동작 유지."""
    from app.repositories.citation_repository import FakeCitationRepository
    from app.repositories.fakes import FakePriceRepository

    pykrx_mock = MagicMock()
    pykrx_mock.get_market_ticker_list = MagicMock(return_value=["005930"])
    pykrx_mock.get_market_ohlcv = MagicMock(
        return_value=_ohlcv_df(day=date(2024, 5, 7), close=70000.0),
    )
    pykrx_mock.get_market_cap_by_date = MagicMock(
        return_value=_market_cap_df(day=date(2024, 5, 7)),
    )

    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=FakeCitationRepository(),
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
        # session 미주입 — default None.
    )
    summary = batch.run(as_of=date(2024, 5, 7), market="KOSPI")
    assert summary.success_count == 1
