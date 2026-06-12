"""screen_active_codes ↔ CachingMacroIndicatorRepository 배선 통합 테스트.

N+1 완화의 배선을 입증한다 — screen 1회 실행이 macro_repo 를 단일
CachingMacroIndicatorRepository 로 감싸 **모든 종목의 provider 에 같은 인스턴스를
공유**시키는지(종목 간 캐시 공유 = N×M → M). 캐시 자체의 memoize 정확성은
test_repositories/test_caching_repositories.py 가 검증.

setup 최소화: conditions=[] 로 호출하면 _compile_conditions 가 빈 list →
_passes_all_conditions 가 즉시 True(통과) 이므로 fact fetch 없이 종목마다
_build_provider 만 호출된다. _build_provider 를 spy 하여 전달된 macro_repo 를
캡처.

주의: API schema(ScreenRequest)는 conditions 1~32개를 강제하므로 빈 conditions
는 endpoint 경계에서 발생하지 않는다. 본 테스트는 배선만 격리 검증하기 위해
screen_active_codes 를 직접 호출하며 schema 검증을 의도적으로 우회한다 — 빈
conditions 의 vacuous-true 통과는 함수 계약상 정상(설계 의도)이다.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.api.routes import screen as screen_module
from app.repositories.caching_repositories import CachingMacroIndicatorRepository
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
    """테스트용 active StockMasterRecord (보통주 — 기본 자산군 필터 통과)."""
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


def test_screen_shares_single_macro_cache_across_stocks(monkeypatch) -> None:
    """3 종목 screen → macro_repo 가 단일 CachingMacroIndicatorRepository 로 공유.

    종목마다 _build_provider 가 호출되지만, 전달되는 macro_repo 는 모두 **동일한**
    캐싱 래퍼 인스턴스여야 한다(종목 간 캐시 공유). 이로써 N 종목이 같은
    (indicator_id, as_of) 를 조회해도 inner fetch 가 1회로 축약된다.
    """
    captured: list[object] = []
    real_build = screen_module._build_provider

    def _spy(**kwargs: object) -> object:
        captured.append(kwargs["macro_repo"])
        return real_build(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(screen_module, "_build_provider", _spy)

    stocks = FakeStocksMasterRepository(records=[
        _stock("005930", "삼성전자"),
        _stock("000660", "SK하이닉스"),
        _stock("035420", "NAVER"),
    ])
    inner_macro = FakeMacroIndicatorRepository(records=[])

    # conditions=[] → 모든 종목 통과(빈 compiled). fact fetch 없이 provider 만 생성.
    result = screen_module.screen_active_codes(
        as_of=date(2024, 5, 7),
        conditions=[],
        stocks_repo=stocks,
        pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository(records=[]),
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=FakeMarketCapRepository(records=[]),
        treasury_repo=FakeTreasurySharesRepository(records=[]),
        macro_repo=inner_macro,
    )

    # 3 종목 모두 통과(조건 0개) → result 3개.
    assert len(result) == 3
    # 종목마다 _build_provider 호출 = 3회.
    assert len(captured) == 3
    # 전달된 macro_repo 가 모두 CachingMacroIndicatorRepository.
    assert all(
        isinstance(m, CachingMacroIndicatorRepository) for m in captured
    )
    # 핵심 — 3 종목이 **동일** 캐시 인스턴스 공유 (종목 간 N×M → M 배선).
    assert captured[0] is captured[1] is captured[2]
    # 원본 inner 는 직접 노출되지 않음 (캐싱 래퍼로만 접근).
    assert captured[0] is not inner_macro


def test_screen_without_macro_repo_keeps_none(monkeypatch) -> None:
    """macro_repo=None 이면 래핑하지 않음 — None 그대로 전달(하위호환)."""
    captured: list[object] = []
    real_build = screen_module._build_provider

    def _spy(**kwargs: object) -> object:
        captured.append(kwargs["macro_repo"])
        return real_build(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(screen_module, "_build_provider", _spy)

    stocks = FakeStocksMasterRepository(records=[_stock("005930", "삼성전자")])

    screen_module.screen_active_codes(
        as_of=date(2024, 5, 7),
        conditions=[],
        stocks_repo=stocks,
        pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository(records=[]),
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=FakeMarketCapRepository(records=[]),
        treasury_repo=FakeTreasurySharesRepository(records=[]),
        macro_repo=None,
    )

    assert captured == [None]


def test_screen_does_not_double_wrap_cache(monkeypatch) -> None:
    """이미 CachingMacroIndicatorRepository 인 macro_repo 는 재래핑하지 않음(idempotent).

    호출자가 캐싱 래퍼를 직접 넘긴 경우 screen_active_codes 가 한 번 더 감싸면
    무의미한 캐시 중첩이 발생한다 — isinstance 방어로 동일 인스턴스를 그대로 전달.
    """
    captured: list[object] = []
    real_build = screen_module._build_provider

    def _spy(**kwargs: object) -> object:
        captured.append(kwargs["macro_repo"])
        return real_build(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(screen_module, "_build_provider", _spy)

    stocks = FakeStocksMasterRepository(records=[_stock("005930", "삼성전자")])
    # 사전 래핑한 캐시를 직접 주입.
    pre_wrapped = CachingMacroIndicatorRepository(FakeMacroIndicatorRepository(records=[]))

    screen_module.screen_active_codes(
        as_of=date(2024, 5, 7),
        conditions=[],
        stocks_repo=stocks,
        pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository(records=[]),
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=FakeMarketCapRepository(records=[]),
        treasury_repo=FakeTreasurySharesRepository(records=[]),
        macro_repo=pre_wrapped,
    )

    # 재래핑 없이 동일 인스턴스 그대로 전달 (이중 래핑 방어).
    assert captured == [pre_wrapped]
