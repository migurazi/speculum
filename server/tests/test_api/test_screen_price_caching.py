"""screen_active_codes ↔ CachingPrice/CachingMarketCapRepository 배선 통합 테스트.

per-code(price·market_cap) N+1 완화의 배선을 입증한다 — screen 1회 실행이 루프
**이전** 1회 prime(bulk)으로 price_repo / market_cap_repo 를 단일 캐싱 래퍼로 감싸
**모든 종목의 provider 에 같은 인스턴스를 공유**시키는지(종목 간 prefetch 공유).
캐시 자체의 정확성은 test_repositories/test_caching_price_marketcap.py 가,
bulk==single 동등성은 test_db/test_sql_bulk_fetch.py 가 검증.

setup 최소화: conditions=[] → 모든 종목 통과(빈 compiled), fact fetch 없이 종목마다
_build_provider 만 호출. _build_provider 를 spy 하여 전달된 price/market_cap_repo
캡처. (test_screen_macro_caching.py 와 동일 격리 기법 — 빈 conditions 의 vacuous-true
통과는 함수 계약상 정상이며 schema 검증을 의도적으로 우회.)
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.api.routes import screen as screen_module
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
from app.repositories.pit_protocols import CodeHistoryEntry, StockMasterRecord
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK


def _stock(code: str, name: str) -> StockMasterRecord:
    return StockMasterRecord(
        id=UUID(int=int(code)),
        current_code=code,
        current_name=name,
        market="KOSPI",
        listing_date=date(2000, 1, 1),
        delisting_date=None,
        fiscal_month=12,
        code_history=(
            CodeHistoryEntry(code, date(2000, 1, 1), None, "initial_listing"),
        ),
    )


def _run_screen(monkeypatch, *, price_repo, market_cap_repo):
    """screen_active_codes 를 3종목으로 호출하며 _build_provider 의 repo 인자 캡처."""
    captured_price: list[object] = []
    captured_mc: list[object] = []
    real_build = screen_module._build_provider

    def _spy(**kwargs: object) -> object:
        captured_price.append(kwargs["price_repo"])
        captured_mc.append(kwargs["market_cap_repo"])
        return real_build(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(screen_module, "_build_provider", _spy)

    stocks = FakeStocksMasterRepository(records=[
        _stock("005930", "삼성전자"),
        _stock("000660", "SK하이닉스"),
        _stock("035420", "NAVER"),
    ])
    result = screen_module.screen_active_codes(
        as_of=date(2024, 5, 7),
        conditions=[],
        stocks_repo=stocks,
        pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        price_repo=price_repo,
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=market_cap_repo,
        treasury_repo=FakeTreasurySharesRepository(records=[]),
        macro_repo=FakeMacroIndicatorRepository(records=[]),
    )
    return result, captured_price, captured_mc


def test_screen_shares_single_price_cache_across_stocks(monkeypatch) -> None:
    """3종목 screen → price/market_cap 이 각각 단일 캐싱 래퍼로 공유(종목 간 prefetch)."""
    inner_price = FakePriceRepository(records=[])
    inner_mc = FakeMarketCapRepository(records=[])
    result, cap_price, cap_mc = _run_screen(
        monkeypatch, price_repo=inner_price, market_cap_repo=inner_mc,
    )

    assert len(result) == 3  # 조건 0개 → 전 종목 통과(가격 없어도 screen 은 무관).
    assert len(cap_price) == 3
    # 전달된 price/market_cap 가 모두 캐싱 래퍼.
    assert all(isinstance(p, CachingPriceRepository) for p in cap_price)
    assert all(isinstance(m, CachingMarketCapRepository) for m in cap_mc)
    # 핵심 — 3종목이 **동일** 캐시 인스턴스 공유(루프 전 1회 prime, 종목 간 공유).
    assert cap_price[0] is cap_price[1] is cap_price[2]
    assert cap_mc[0] is cap_mc[1] is cap_mc[2]
    # 원본 inner 는 직접 노출되지 않음 (캐싱 래퍼로만 접근).
    assert cap_price[0] is not inner_price
    assert cap_mc[0] is not inner_mc


def test_screen_does_not_double_wrap_price_cache(monkeypatch) -> None:
    """이미 캐싱 래퍼인 price/market_cap 은 재래핑하지 않음(idempotent, isinstance 방어)."""
    pre_price = CachingPriceRepository(FakePriceRepository(records=[]))
    pre_mc = CachingMarketCapRepository(FakeMarketCapRepository(records=[]))
    _result, cap_price, cap_mc = _run_screen(
        monkeypatch, price_repo=pre_price, market_cap_repo=pre_mc,
    )

    # 재래핑 없이 동일 인스턴스 그대로 전달(이중 래핑 방어 — 재prime 만 수행).
    assert all(p is pre_price for p in cap_price)
    assert all(m is pre_mc for m in cap_mc)
