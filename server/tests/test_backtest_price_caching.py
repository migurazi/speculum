"""_select_portfolio ↔ CachingPrice/CachingMarketCapRepository 배선 + survivorship.

backtest 의 rebalance 시점 universe 루프(per-code price/market_cap N+1)도 screen 과
동일하게 루프 전 1회 prime(bulk)으로 캐싱 래퍼를 공유시키는지(배선), 그리고 가격
존재 판정(survivorship missing_price_count)을 bulk 존재 쿼리로 대체해도 의미가
동일한지(line 338 정공법, oracle 설계검토 A2)를 검증.

캐시/SQL 정확성은 test_repositories/test_caching_price_marketcap.py ·
test_db/test_sql_bulk_fetch.py 가, equity_curve 불변(전체 백테스트 재현)은 기존
test_backtest_engine.py 가 커버. 본 테스트는 _select_portfolio 격리 검증.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import app.services.backtest_engine as backtest_engine
from app.repositories.caching_repositories import (
    CachingMarketCapRepository,
    CachingPriceRepository,
)
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeFinancialRepository,
    FakeMacroIndicatorRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    PriceRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK


def _stock(code: str) -> StockMasterRecord:
    return StockMasterRecord(
        id=UUID(int=int(code)),
        current_code=code,
        current_name=f"종목{code}",
        market="KOSPI",
        listing_date=date(2000, 1, 1),
        delisting_date=None,
        fiscal_month=12,
        code_history=(
            CodeHistoryEntry(code, date(2000, 1, 1), None, "initial_listing"),
        ),
    )


def _price(code: str, eff: date) -> PriceRecord:
    return PriceRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=UUID(int=int(code)),
        effective_date=eff,
        open_raw=Decimal(70000),
        high_raw=Decimal(70000),
        low_raw=Decimal(70000),
        close_raw=Decimal(70000),
        volume=1_000_000,
        trading_value=Decimal(70000) * Decimal(1_000_000),
        close_adjusted=Decimal(70000),
        citation_id=uuid4(),
        created_at=datetime(eff.year, eff.month, eff.day, 17, 0, tzinfo=UTC),
    )


def _select(monkeypatch, *, stocks, price_repo, market_cap_repo):
    """_select_portfolio 호출 + DbFieldProvider spy 로 repo 인자 캡처."""
    captured_price: list[object] = []
    captured_mc: list[object] = []
    real_provider = backtest_engine.DbFieldProvider

    def _spy(**kwargs: object) -> object:
        captured_price.append(kwargs["price_repo"])
        captured_mc.append(kwargs["market_cap_repo"])
        return real_provider(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(backtest_engine, "DbFieldProvider", _spy)

    out = backtest_engine._select_portfolio(
        as_of=date(2024, 5, 7),
        pack=DEFAULT_PACK,
        conditions=[],
        stocks_repo=stocks,
        evaluator=FactorEvaluator(),
        price_repo=price_repo,
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=market_cap_repo,
        treasury_repo=FakeTreasurySharesRepository(records=[]),
        macro_repo=FakeMacroIndicatorRepository(records=[]),
    )
    return out, captured_price, captured_mc


def test_select_portfolio_shares_single_price_cache(monkeypatch) -> None:
    """가격 있는 3종목 → price/market_cap 이 각각 단일 캐싱 래퍼로 공유(배선)."""
    stocks = FakeStocksMasterRepository(
        records=[_stock("000001"), _stock("000002"), _stock("000003")],
    )
    prices = [
        _price("000001", date(2024, 5, 1)),
        _price("000002", date(2024, 5, 1)),
        _price("000003", date(2024, 5, 1)),
    ]
    inner_price = FakePriceRepository(prices)
    inner_mc = FakeMarketCapRepository(records=[])
    (selected, universe_size, missing), cap_price, cap_mc = _select(
        monkeypatch, stocks=stocks, price_repo=inner_price, market_cap_repo=inner_mc,
    )

    assert universe_size == 3
    assert missing == 0  # 전 종목 가격 존재.
    assert selected == ("000001", "000002", "000003")  # 조건 0개 → 전 종목 선정.
    assert len(cap_price) == 3
    assert all(isinstance(p, CachingPriceRepository) for p in cap_price)
    assert all(isinstance(m, CachingMarketCapRepository) for m in cap_mc)
    # 핵심 — 3종목이 동일 캐시 인스턴스 공유(루프 전 1회 prime).
    assert cap_price[0] is cap_price[1] is cap_price[2]
    assert cap_mc[0] is cap_mc[1] is cap_mc[2]


def test_select_portfolio_survivorship_missing_price(monkeypatch) -> None:
    """가격 없는 종목은 missing_price_count 로 집계되고 provider 미생성(선정 제외).

    bulk 존재 판정(fetch_codes_with_prices)이 종목별 fetch_prices(date.min) 의 빈
    시계열 판정과 동일 의미임을 입증(survivorship 지표·선정 결과 불변, oracle A2).
    """
    stocks = FakeStocksMasterRepository(
        records=[_stock("000001"), _stock("000002"), _stock("000003")],
    )
    # 000002 만 가격 없음(소급 폐지 backfill 누락 모사).
    prices = [_price("000001", date(2024, 5, 1)), _price("000003", date(2024, 5, 1))]
    (selected, universe_size, missing), cap_price, _cap_mc = _select(
        monkeypatch,
        stocks=stocks,
        price_repo=FakePriceRepository(prices),
        market_cap_repo=FakeMarketCapRepository(records=[]),
    )

    assert universe_size == 3  # 모집단(보통주)은 3.
    assert missing == 1  # 000002 가격 누락.
    assert selected == ("000001", "000003")  # 가격 있는 종목만 선정.
    # provider 는 가격 있는 2종목만 생성(missing 종목은 continue).
    assert len(cap_price) == 2


def test_select_portfolio_does_not_double_wrap(monkeypatch) -> None:
    """이미 캐싱 래퍼인 price/market_cap 은 재래핑하지 않음(idempotent)."""
    stocks = FakeStocksMasterRepository(records=[_stock("000001")])
    pre_price = CachingPriceRepository(
        FakePriceRepository([_price("000001", date(2024, 5, 1))])
    )
    pre_mc = CachingMarketCapRepository(FakeMarketCapRepository(records=[]))
    _out, cap_price, cap_mc = _select(
        monkeypatch, stocks=stocks, price_repo=pre_price, market_cap_repo=pre_mc,
    )

    assert all(p is pre_price for p in cap_price)
    assert all(m is pre_mc for m in cap_mc)
