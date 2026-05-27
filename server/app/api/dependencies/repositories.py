"""Repository dependency injection — environment 별 구현체 swap point.

oracle T25 자문 Risk-C8 — Fake/Sql 분기를 본 layer 에 집중. T13 SQLAlchemy
합류 후 본 모듈만 변경하면 swap. endpoint 가 직접 Fake import 하지 않도록 강제.

M0 = always Fake (DB 미완). 환경변수 또는 settings 으로 swap 은 별도 사이클.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.repositories.stocks_master_repository import (
    FakeStocksMasterRepository,
    StocksMasterRepository,
)
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK, LoadedPack


def get_stocks_repository(request: Request) -> StocksMasterRepository:
    """현재 active StocksMasterRepository — `request.app.state.stocks_repo` 에 박힘.

    `main.create_app()` 가 app.state 에 등록. 테스트는 `app.state.stocks_repo =
    MyFake()` 로 override. T13 합류 시 본 함수가 SQLAlchemy session-bound 구현체
    반환.
    """
    repo = getattr(request.app.state, "stocks_repo", None)
    if repo is None:
        # M0 default — 빈 Fake. test 가 fixture 로 채워야.
        return FakeStocksMasterRepository(records=())
    return repo


def get_factor_evaluator() -> FactorEvaluator:
    """Factor evaluator — state-less, default config."""
    return FactorEvaluator()


def get_active_pack() -> LoadedPack:
    """M0 single-pack 가정 — DEFAULT_PACK singleton 반환 (T22 / T30 패턴 일관)."""
    return DEFAULT_PACK


StocksRepoDep = Annotated[StocksMasterRepository, Depends(get_stocks_repository)]
"""Endpoint type-level dependency hint."""

FactorEvaluatorDep = Annotated[FactorEvaluator, Depends(get_factor_evaluator)]

ActivePackDep = Annotated[LoadedPack, Depends(get_active_pack)]
