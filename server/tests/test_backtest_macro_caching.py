"""run_backtest ↔ CachingMacroIndicatorRepository 배선 회귀 테스트.

backtest 는 rebalance 시점 t 마다 universe N 종목을 평가하며 종목마다
macro fetch(indicator_id, t) 를 반복 → 한 시점 내 N×M 동일 쿼리(전체 N×R×M).
run_backtest 진입부가 macro_repo 를 request-scoped CachingMacroIndicatorRepository
로 1회 감싸 시점 내 중복을 제거한다(screen 과 동일 — caching_repositories).

배선만 격리 검증: _run_backtest_inner 를 spy 로 가로채 전달된 macro_repo 가
캐싱 래퍼인지 확인(실제 rebalance 실행은 무관). 캐시 memoize 정확성은
test_repositories/test_caching_repositories.py 가 검증.
"""

from __future__ import annotations

from datetime import date

import app.services.backtest_engine as backtest_engine
from app.repositories.caching_repositories import CachingMacroIndicatorRepository
from app.repositories.fakes import (
    FakeFinancialRepository,
    FakeMacroIndicatorRepository,
    FakePriceRepository,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.services.factor_pack import DEFAULT_PACK


def _run(monkeypatch, macro_repo: object) -> dict[str, object]:
    """run_backtest 를 호출하되 _run_backtest_inner 를 spy 로 가로채 인자만 캡처.

    배선 격리 검증 전용 — run_backtest 의 BacktestResult 반환 계약은 real rebalance
    를 실행하는 test_backtest_engine.py 가 커버한다. 본 헬퍼는 wrapping 만 본다.

    spy 가 macro_repo 를 keyword-only 로 명시 바인딩하므로 run_backtest →
    _run_backtest_inner 호출의 파라미터 이름이 바뀌면 TypeError 로 즉시 실패한다
    (kwargs[...] 오타 누락 방지). monkeypatch 는 backtest_engine 모듈의
    _run_backtest_inner 속성을 교체 — run_backtest 가 동일 모듈의 global lookup 으로
    그 심볼을 호출하므로 patch 가 적용된다(지연/직접 import 무관, 같은 모듈).
    """
    captured: dict[str, object] = {}

    def _spy(*, macro_repo: object, **kwargs: object) -> None:
        captured["macro_repo"] = macro_repo
        return None  # 실제 rebalance 실행 skip (배선만 검증).

    monkeypatch.setattr(backtest_engine, "_run_backtest_inner", _spy)

    backtest_engine.run_backtest(
        pack=DEFAULT_PACK,
        conditions=[],
        start=date(2023, 1, 1),
        end=date(2023, 12, 31),
        frequency="quarterly",
        stocks_repo=FakeStocksMasterRepository(records=[]),
        price_repo=FakePriceRepository(records=[]),
        financial_repo=FakeFinancialRepository(records=[]),
        macro_repo=macro_repo,  # type: ignore[arg-type]
    )
    return captured


def test_run_backtest_wraps_macro_repo(monkeypatch) -> None:
    """주입 macro_repo 가 CachingMacroIndicatorRepository 로 wrapping 되어 전달."""
    macro = FakeMacroIndicatorRepository(records=[])
    captured = _run(monkeypatch, macro)
    assert isinstance(captured["macro_repo"], CachingMacroIndicatorRepository)


def test_run_backtest_does_not_double_wrap(monkeypatch) -> None:
    """이미 캐싱 래퍼면 재래핑하지 않음(idempotent)."""
    pre = CachingMacroIndicatorRepository(FakeMacroIndicatorRepository(records=[]))
    captured = _run(monkeypatch, pre)
    assert captured["macro_repo"] is pre


def test_run_backtest_macro_none_passthrough(monkeypatch) -> None:
    """macro_repo=None 이면 래핑하지 않음 — None 그대로(하위호환)."""
    captured = _run(monkeypatch, None)
    assert captured["macro_repo"] is None
