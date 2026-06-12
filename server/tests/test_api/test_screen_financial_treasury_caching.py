"""screen_active_codes ↔ CachingFinancial/CachingTreasurySharesRepository 배선 통합.

per-code(financial·treasury) N+1 완화의 배선을 입증 — screen 1회 실행이 루프
**이전** 1회 prime(bulk)으로 financial_repo / treasury_repo 를 단일 캐싱 래퍼로 감싸
**모든 종목의 provider 에 같은 인스턴스를 공유**시키는지(종목 간 prefetch 공유).
캐시 정확성은 test_repositories/test_caching_financial_treasury.py 가, bulk+serve==
single 동등성은 test_db/test_sql_bulk_financial_treasury.py 가 검증.

setup 최소화: conditions=[] → 모든 종목 통과(빈 compiled), _build_provider 만 호출.
_build_provider 를 spy 하여 전달된 financial/treasury_repo 캡처(test_screen_price_
caching.py 와 동일 격리 기법).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.api.routes import screen as screen_module
from app.repositories.caching_repositories import (
    CachingFinancialRepository,
    CachingTreasurySharesRepository,
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


def _run_screen(monkeypatch, *, financial_repo, treasury_repo):
    """screen_active_codes 를 3종목으로 호출하며 _build_provider 의 repo 인자 캡처."""
    captured_fin: list[object] = []
    captured_tr: list[object] = []
    real_build = screen_module._build_provider

    def _spy(**kwargs: object) -> object:
        captured_fin.append(kwargs["financial_repo"])
        captured_tr.append(kwargs["treasury_repo"])
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
        price_repo=FakePriceRepository(records=[]),
        financial_repo=financial_repo,
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=FakeMarketCapRepository(records=[]),
        treasury_repo=treasury_repo,
        macro_repo=FakeMacroIndicatorRepository(records=[]),
    )
    return result, captured_fin, captured_tr


def test_screen_shares_single_financial_treasury_cache_across_stocks(
    monkeypatch,
) -> None:
    """3종목 screen → financial/treasury 가 각각 단일 캐싱 래퍼로 공유(종목 간 prefetch)."""
    inner_fin = FakeFinancialRepository(records=[])
    inner_tr = FakeTreasurySharesRepository(records=[])
    result, cap_fin, cap_tr = _run_screen(
        monkeypatch, financial_repo=inner_fin, treasury_repo=inner_tr,
    )

    assert len(result) == 3  # 조건 0개 → 전 종목 통과.
    assert len(cap_fin) == 3
    assert all(isinstance(f, CachingFinancialRepository) for f in cap_fin)
    assert all(isinstance(t, CachingTreasurySharesRepository) for t in cap_tr)
    # 핵심 — 3종목이 **동일** 캐시 인스턴스 공유(루프 전 1회 prime, 종목 간 공유).
    assert cap_fin[0] is cap_fin[1] is cap_fin[2]
    assert cap_tr[0] is cap_tr[1] is cap_tr[2]
    # 원본 inner 는 캐싱 래퍼로만 접근.
    assert cap_fin[0] is not inner_fin
    assert cap_tr[0] is not inner_tr


def test_screen_does_not_double_wrap_financial_treasury_cache(monkeypatch) -> None:
    """이미 캐싱 래퍼면 재래핑하지 않음(idempotent, isinstance 방어)."""
    pre_fin = CachingFinancialRepository(FakeFinancialRepository(records=[]))
    pre_tr = CachingTreasurySharesRepository(FakeTreasurySharesRepository(records=[]))
    _result, cap_fin, cap_tr = _run_screen(
        monkeypatch, financial_repo=pre_fin, treasury_repo=pre_tr,
    )
    assert all(f is pre_fin for f in cap_fin)
    assert all(t is pre_tr for t in cap_tr)


def test_screen_none_treasury_repo_stays_none(monkeypatch) -> None:
    """treasury_repo=None(선택적) → 래핑 없이 None 전달(하위호환)."""
    _result, _cap_fin, cap_tr = _run_screen(
        monkeypatch,
        financial_repo=FakeFinancialRepository(records=[]),
        treasury_repo=None,
    )
    assert all(t is None for t in cap_tr)
