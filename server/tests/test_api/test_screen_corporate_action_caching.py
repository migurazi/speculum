"""screen_active_codes ↔ CachingCorporateActionRepository 배선 통합 테스트.

corporate_action 의 per-code N+1(adjusted-price factor 의 split 보정 fetch_actions)
완화 배선 입증 — screen 1회 실행이 루프 **이전** 1회 prime(bulk)으로 corporate_
action_repo 를 단일 캐싱 래퍼로 감싸 모든 종목 provider 에 공유. price/financial/
treasury 와 동형(ⓒ CachingCorporateActionRepository 는 byte-동일 검증 완료).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.api.routes import screen as screen_module
from app.repositories.caching_repositories import CachingCorporateActionRepository
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


def _run_screen(monkeypatch, *, corporate_action_repo):
    """screen_active_codes 를 3종목으로 호출하며 _build_provider 의 CA repo 인자 캡처."""
    captured_ca: list[object] = []
    real_build = screen_module._build_provider

    def _spy(**kwargs: object) -> object:
        captured_ca.append(kwargs["corporate_action_repo"])
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
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=corporate_action_repo,
        market_cap_repo=FakeMarketCapRepository(records=[]),
        treasury_repo=FakeTreasurySharesRepository(records=[]),
        macro_repo=FakeMacroIndicatorRepository(records=[]),
    )
    return result, captured_ca


def test_screen_single_ca_cache_across_stocks(monkeypatch) -> None:
    """3종목 screen → corporate_action 이 단일 캐싱 래퍼로 공유(종목 간 prefetch)."""
    inner_ca = FakeCorporateActionRepository(records=[])
    result, cap_ca = _run_screen(monkeypatch, corporate_action_repo=inner_ca)

    assert len(result) == 3  # 조건 0개 → 전 종목 통과.
    assert len(cap_ca) == 3
    assert all(isinstance(c, CachingCorporateActionRepository) for c in cap_ca)
    # 핵심 — 3종목이 동일 캐시 인스턴스 공유(루프 전 1회 prime).
    assert cap_ca[0] is cap_ca[1] is cap_ca[2]
    # 원본 inner 는 캐싱 래퍼로만 접근.
    assert cap_ca[0] is not inner_ca


def test_screen_does_not_double_wrap_ca_cache(monkeypatch) -> None:
    """이미 캐싱 래퍼면 재래핑하지 않음(idempotent, isinstance 방어)."""
    pre_ca = CachingCorporateActionRepository(
        FakeCorporateActionRepository(records=[])
    )
    _result, cap_ca = _run_screen(monkeypatch, corporate_action_repo=pre_ca)
    assert all(c is pre_ca for c in cap_ca)


def test_screen_none_ca_repo_stays_none(monkeypatch) -> None:
    """corporate_action_repo=None(선택적) → 래핑 없이 None 전달(하위호환)."""
    _result, cap_ca = _run_screen(monkeypatch, corporate_action_repo=None)
    assert all(c is None for c in cap_ca)
