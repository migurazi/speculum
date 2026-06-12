"""screen_active_codes ↔ CachingDividendRepository 배선 통합 테스트.

dividend 의 per-code N+1(total_return field 의 배당 재투자 fetch_dividends) 완화
배선 입증 — screen 1회 실행이 루프 **이전** 1회 prime(bulk)으로 dividend_repo 를 단일
캐싱 래퍼로 감싸 모든 종목 provider 에 공유. corporate_action 과 동형(byte-동일 검증은
test_repositories/test_caching_dividend.py·test_db 가 담당). dividend_repo 는 **Optional**
이라 None 가드 + both-or-neither(dividend None → adjuster None) 보존을 함께 검증.

test_screen_corporate_action_caching.py 미러.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.api.routes import screen as screen_module
from app.repositories.caching_repositories import CachingDividendRepository
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeDividendRepository,
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


def _run_screen(monkeypatch, *, dividend_repo):
    """screen_active_codes 를 3종목으로 호출하며 _build_provider 의 dividend repo·
    adjuster 인자 캡처."""
    captured_div: list[object] = []
    captured_adj: list[object] = []
    real_build = screen_module._build_provider

    def _spy(**kwargs: object) -> object:
        captured_div.append(kwargs["dividend_repo"])
        # _build_provider 내부에서 adjuster 를 구성하므로 결과 provider 에서 읽는다.
        provider = real_build(**kwargs)  # type: ignore[arg-type]
        captured_adj.append(provider._total_return_adjuster)
        return provider

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
        price_repo=FakePriceRepository(records=[]),
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=FakeMarketCapRepository(records=[]),
        treasury_repo=FakeTreasurySharesRepository(records=[]),
        macro_repo=FakeMacroIndicatorRepository(records=[]),
        dividend_repo=dividend_repo,
    )
    return result, captured_div, captured_adj


def test_screen_single_dividend_cache_across_stocks(monkeypatch) -> None:
    """3종목 screen → dividend 가 단일 캐싱 래퍼로 공유(종목 간 prefetch)."""
    inner_div = FakeDividendRepository(records=[])
    result, cap_div, cap_adj = _run_screen(monkeypatch, dividend_repo=inner_div)

    assert len(result) == 3  # 조건 0개 → 전 종목 통과.
    assert len(cap_div) == 3
    assert all(isinstance(c, CachingDividendRepository) for c in cap_div)
    # 핵심 — 3종목이 동일 캐시 인스턴스 공유(루프 전 1회 prime).
    assert cap_div[0] is cap_div[1] is cap_div[2]
    # 원본 inner 는 캐싱 래퍼로만 접근.
    assert cap_div[0] is not inner_div
    # both-or-neither — dividend non-None 이면 adjuster 도 non-None.
    assert all(a is not None for a in cap_adj)


def test_screen_does_not_double_wrap_dividend_cache(monkeypatch) -> None:
    """이미 캐싱 래퍼면 재래핑하지 않음(idempotent, isinstance 방어)."""
    pre_div = CachingDividendRepository(FakeDividendRepository(records=[]))
    _result, cap_div, _cap_adj = _run_screen(monkeypatch, dividend_repo=pre_div)
    assert all(c is pre_div for c in cap_div)


def test_screen_none_dividend_repo_stays_none(monkeypatch) -> None:
    """dividend_repo=None(선택적) → 래핑 없이 None 전달 + adjuster 도 None(both-or-neither)."""
    _result, cap_div, cap_adj = _run_screen(monkeypatch, dividend_repo=None)
    assert all(c is None for c in cap_div)
    assert all(a is None for a in cap_adj)
