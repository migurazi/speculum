"""screen read-bypass 실현-이득 측정 — Slice 1a (Slice 1b 구현 후 realized speedup).

Slice 0(`bench_screen_eval.py`)은 이득 **천장**(eval CPU 가 screen wall 의 ~90%)을
측정했다. 본 harness 는 Slice 1b(serve 구현) 후 **실제 serve vs live wall-time 차이**
= **realized speedup** 을 측정한다 — 즉 천장 × (serve hit 시 절감률)의 실측치.

방법:
    1. bench_screen_eval 의 합성 universe(N 종목 + eps/roe financial) 재사용.
    2. 조건 factor(eps/roe)를 종목별로 1회 live 평가해 그 값으로 Fake snapshot 적재
       (producer 가 쓴 것과 동치 — serve==live byte-동일은 test_screen_read_bypass_e2e
       가 보증, 여기선 timing 만).
    3. screen_active_codes 를 **serve 경로**(snapshot_repo 적재 + current_dv 일치)와
       **live 경로**(snapshot_repo None) 양쪽 실행해 wall-time 비교.
    4. realized speedup = (T_live - T_serve) / T_live. result_codes 동일 확인(가드).

§2.10/behavior 무관(측정 전용). financial-only(eps/roe)라 PriceAdjuster 미포함 →
price 기반 factor screen 은 serve 절감이 더 큼(본 측정은 하한).

실행: cd server && python -m scripts.bench_screen_serve
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from uuid import UUID

from app.api.routes.screen import screen_active_codes
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeDividendRepository,
    FakeFinancialRepository,
    FakeMacroIndicatorRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeStockSnapshotRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import StockSnapshotRecord
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.schemas.screen import ConditionIn, OpEnum
from app.services.db_field_provider import DbFieldProvider
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK
from scripts.bench_screen_eval import _gen_universe

_AS_OF = date(2024, 5, 7)
_CITATION = UUID("00000000-0000-0000-0000-0000000000ff")
_EPS_ID = "eps:basic-ttm-consolidated-ifrs"
_ROE_ID = "roe:ttm-avg-equity-consolidated-ifrs"
_FACTORS_BY_ID = {f["canonical_id"]: f for f in DEFAULT_PACK.body["factors"]}
# producer freeze 대표 — serve current_dv 와 동일 주입(Fake 라 키 set 무관, equality 만).
_DV = {"factor_pack_content_hash": "sha256:builtin", "krx_batch_id": "k1"}

_CONDS = [
    ConditionIn(factor=_EPS_ID, op=OpEnum.GT, value="0"),
    ConditionIn(factor=_ROE_ID, op=OpEnum.GT, value="-9999"),
]


def _build_snapshots(stocks, fins) -> list[StockSnapshotRecord]:
    """조건 factor(eps/roe)를 종목별 1회 live 평가 → Fake snapshot 적재(producer 동치)."""
    fin_repo = FakeFinancialRepository(records=fins)
    evaluator = FactorEvaluator()
    snaps: list[StockSnapshotRecord] = []
    for stock in stocks:
        code = stock.current_code
        provider = DbFieldProvider(
            code=code, as_of=_AS_OF,
            price_repo=FakePriceRepository(records=()),
            financial_repo=fin_repo,
            corporate_action_repo=FakeCorporateActionRepository(records=()),
            market_cap_repo=FakeMarketCapRepository(records=()),
            treasury_repo=FakeTreasurySharesRepository(records=()),
            macro_repo=FakeMacroIndicatorRepository(records=()),
            factor_pack=DEFAULT_PACK, evaluator=evaluator,
        )
        for fid in (_EPS_ID, _ROE_ID):
            f = _FACTORS_BY_ID[fid]
            result = evaluator.evaluate(f, provider, as_of=_AS_OF)
            snaps.append(StockSnapshotRecord(
                stock_code=code, as_of_date=_AS_OF,
                factor_uuid=UUID(f["uuid"]), value=result.value,
                value_unit=f["unit"], inputs={}, citation_id=_CITATION,
                computed_at=datetime(2024, 5, 7, 18, 0, tzinfo=UTC),
                data_versions=dict(_DV),
            ))
    return snaps


def _kwargs(stocks, fins):  # noqa: ANN001, ANN202
    return dict(
        as_of=_AS_OF, conditions=_CONDS,
        stocks_repo=FakeStocksMasterRepository(records=stocks),
        pack=DEFAULT_PACK, evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository(records=()),
        financial_repo=FakeFinancialRepository(records=fins),
        corporate_action_repo=FakeCorporateActionRepository(records=()),
        market_cap_repo=FakeMarketCapRepository(records=()),
        treasury_repo=FakeTreasurySharesRepository(records=()),
        dividend_repo=FakeDividendRepository(records=()),
    )


def _bench(n: int) -> None:
    stocks, fins = _gen_universe(n)
    snaps = _build_snapshots(stocks, fins)
    snap_repo = FakeStockSnapshotRepository(snaps)

    # warm-up.
    live0 = screen_active_codes(**_kwargs(stocks, fins))
    serve0 = screen_active_codes(
        **_kwargs(stocks, fins), snapshot_repo=snap_repo,
        current_data_versions=dict(_DV),
    )
    assert live0 == serve0, "serve != live result_codes — byte-동일 위반!"

    t0 = time.perf_counter()
    screen_active_codes(**_kwargs(stocks, fins))  # live
    t_live = time.perf_counter() - t0

    t0 = time.perf_counter()
    screen_active_codes(
        **_kwargs(stocks, fins), snapshot_repo=snap_repo,
        current_data_versions=dict(_DV),
    )  # serve
    t_serve = time.perf_counter() - t0

    speedup = (t_live - t_serve) / t_live * 100 if t_live > 0 else 0.0
    print(f"\n=== N={n} (eps + roe 조건, serve 전 종목 hit) ===")
    print(f"  matched      : {len(live0)} (serve==live: {live0 == serve0})")
    print(f"  live  wall   : {t_live * 1000:.1f} ms")
    print(f"  serve wall   : {t_serve * 1000:.1f} ms")
    print(f"  → realized speedup: {speedup:.0f}% (hit 100% 가정 상한; 실 hit-rate 만큼 감산)")


def main() -> None:
    print("screen read-bypass realized speedup (serve vs live wall, financial-only 하한)")
    for n in (200, 500, 1000, 2500):
        _bench(n)


if __name__ == "__main__":
    main()
