"""reproduce_run ↔ macro_repo 배선 회귀 테스트 (§2.10 대칭).

live save (save_run / execute_screen) 가 macro_repo 를 screen_active_codes 에
배선하므로, reproduce_run 도 동일 배선해야 macro field 조건이 silent N/A 가
아닌 같은 값으로 재현된다(byte-동일). 본 테스트는 reproduce_run 이 받은
macro_repo 를 screen_active_codes 에 그대로 forward 하는지 spy 로 검증한다 —
dividend_repo 선례(M7 #4)와 동형 배선의 회귀 방지.

배선 누락 시 macro 조건을 쓰는 저장 run 의 재현이 N/A 로 무너져 matches=False
유령 불일치가 발생하므로, forward 자체가 §2.10 정합성의 핵심이다.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import app.api.routes.screen as screen_module
from app.repositories.batch_run_repository import FakeBatchRunRepository
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeFinancialRepository,
    FakeMacroIndicatorRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK
from app.services.reproduce import reproduce_run
from app.services.screen_run import ScreenRunBuilder


def _snapshot():
    """최소 ScreenRunSnapshot — data_versions={} 로 fallback pack 경로, 빈 result."""
    return ScreenRunBuilder.build(
        run_id=uuid4(),
        user_id=uuid4(),
        conditions=[
            {"factor": "eps:basic-ttm-consolidated-ifrs", "op": ">", "value": "1"},
        ],
        selected_factors=["eps:basic-ttm-consolidated-ifrs"],
        security_types=("common",),
        as_of=date(2024, 5, 7),
        result_codes=(),
        data_versions={},
    )


def test_reproduce_run_forwards_macro_repo(monkeypatch) -> None:
    """reproduce_run 이 macro_repo 를 screen_active_codes 에 그대로 forward."""
    captured: dict[str, object] = {}

    def _spy(**kwargs: object) -> tuple[str, ...]:
        captured.update(kwargs)
        return ()  # 빈 result_codes → matches=True (snapshot.result_codes=())

    # reproduce_run 은 screen_active_codes 를 함수 내 지연 import
    # (`from app.api.routes.screen import screen_active_codes`) 한다. 지연 import 는
    # 호출 시점에 sys.modules["app.api.routes.screen"] 의 속성을 lookup 하므로,
    # screen_module(=그 모듈 객체) 의 속성을 교체하면 패치된 spy 가 잡힌다.
    # 지연 import 방식이 유지되는 한 본 패치가 유일한 정확한 타겟이다 (reproduce
    # 모듈 namespace 엔 screen_active_codes 이름이 없음).
    monkeypatch.setattr(screen_module, "screen_active_codes", _spy)

    macro = FakeMacroIndicatorRepository(records=[])
    result = reproduce_run(
        _snapshot(),
        batch_run_repo=FakeBatchRunRepository(),
        stocks_repo=FakeStocksMasterRepository(records=[]),
        pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository(records=[]),
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=FakeMarketCapRepository(records=[]),
        treasury_repo=FakeTreasurySharesRepository(records=[]),
        macro_repo=macro,
    )

    # macro_repo 가 screen_active_codes 에 forward 됐는지 (배선 핵심).
    assert captured.get("macro_repo") is macro
    # 빈 result 재현 — matches=True (snapshot.result_codes=()).
    assert result.matches is True


def test_reproduce_run_macro_repo_defaults_none(monkeypatch) -> None:
    """macro_repo 미지정(구 호출 경로) → None forward (하위호환)."""
    captured: dict[str, object] = {}

    def _spy(**kwargs: object) -> tuple[str, ...]:
        captured.update(kwargs)
        return ()

    monkeypatch.setattr(screen_module, "screen_active_codes", _spy)

    reproduce_run(
        _snapshot(),
        batch_run_repo=FakeBatchRunRepository(),
        stocks_repo=FakeStocksMasterRepository(records=[]),
        pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository(records=[]),
        financial_repo=FakeFinancialRepository(records=[]),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=FakeMarketCapRepository(records=[]),
        treasury_repo=FakeTreasurySharesRepository(records=[]),
        # macro_repo 생략 — default None.
    )

    assert captured.get("macro_repo") is None
