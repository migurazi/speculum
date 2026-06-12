"""screen_active_codes 평가-비용 측정 harness — ⓓ screen read-bypass Slice 0 게이트.

목적 (prometheus 플랜 Slice 0 — go/no-go 측정 게이트):
    screen read-bypass(precompute snapshot lookup 으로 종목별 live factor 평가 대체)
    의 **이득 천장(ceiling)** 을 실측한다. ⓐⓑ 가 이미 DB round-trip N+1 을 bulk
    prime 으로 제거했으므로, read-bypass 가 추가로 절감할 수 있는 것은 오직
    **evaluator.evaluate(종목별 factor 평가 CPU + provider serve-time resolution)**
    뿐이다. 따라서:

        이득 천장 ≈ cumtime(FactorEvaluator.evaluate) / 전체 screen wall-time

    이 비율이 작으면(예: prime/bulk-fetch 가 지배) read-bypass 는 위험(§2.10)·복잡도
    대비 이득이 작아 **no-go**. 크면 Slice 1+ 진행 가치.

측정 범위 (정직한 한계):
    본 harness 는 **financial-only factor(eps + roe)** 로 측정한다 — account 매핑이
    명확하고 합성 데이터가 가벼워서다. price 기반 factor(per/pbr/total-return)는
    PriceAdjuster.adjust(종목별 split 보정)를 evaluate 안에서 수행하므로 eval 비중을
    **더 키운다**. 즉 본 측정은 read-bypass 이득 천장의 **하한(lower bound)** 이다.
    하한이 이미 크면 go 근거가 강하고, 하한이 작아도 price 포함 시 더 클 수 있어
    보수적 해석 필요(주석에 명시).

실행:
    cd server && python -m scripts.bench_screen_eval
    (운영 코드 behavior 무변경 — 본 스크립트는 측정 전용, import side-effect 없음.)
"""

from __future__ import annotations

import cProfile
import io
import pstats
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.api.routes.screen import screen_active_codes
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeDividendRepository,
    FakeFinancialRepository,
    FakeMacroIndicatorRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    FinancialRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.schemas.screen import ConditionIn, OpEnum
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK

# db_field_provider _RESOLUTIONS 기준 account 매핑 (financial-only factor).
_NET_INCOME = "net_income_attributable_to_owners"
_EQUITY = "equity_attributable_to_owners"
_BASIC_EPS = "basic_eps"
_CONSOLIDATED = "consolidated"
_CITATION = UUID("00000000-0000-0000-0000-0000000000ff")
_LINEAGE_BASE = 0xA0000000

_AS_OF = date(2024, 5, 7)
# eps TTM = 최근 4 분기 basic_eps 합. roe = net_income TTM / avg equity(현+직전).
# 충분한 분기(8)를 합성해 TTM + period_begin 이 모두 해소되게 한다.
_QUARTERS: tuple[tuple[str, date], ...] = (
    ("2022Q1", date(2022, 5, 15)), ("2022Q2", date(2022, 8, 14)),
    ("2022Q3", date(2022, 11, 14)), ("2022Q4", date(2023, 3, 30)),
    ("2023Q1", date(2023, 5, 15)), ("2023Q2", date(2023, 8, 14)),
    ("2023Q3", date(2023, 11, 14)), ("2023Q4", date(2024, 3, 30)),
)


def _fin(
    *, code: str, fiscal_period: str, account: str, value: str, eff: date,
) -> FinancialRecord:
    return FinancialRecord(
        id=uuid4(), code=code, code_lineage_id=UUID(int=_LINEAGE_BASE),
        effective_date=eff, fiscal_period=fiscal_period, account=account,
        value=Decimal(value), unit="krw", ifrs_type=_CONSOLIDATED,
        citation_id=_CITATION, superseded_by=None,
        created_at=datetime(eff.year, eff.month, eff.day, 9, 0, tzinfo=UTC),
    )


def _gen_universe(
    n: int,
) -> tuple[list[StockMasterRecord], list[FinancialRecord]]:
    """N 종목 + 종목별 8 분기 financial(basic_eps/net_income/equity) 합성."""
    stocks: list[StockMasterRecord] = []
    fins: list[FinancialRecord] = []
    for i in range(n):
        code = f"{i + 1:06d}"
        stocks.append(StockMasterRecord(
            id=UUID(int=i + 1), current_code=code, current_name=f"bench{i}",
            market="KOSPI", listing_date=date(2000, 1, 1), delisting_date=None,
            fiscal_month=12,
            code_history=(CodeHistoryEntry(
                code, date(2000, 1, 1), None, "initial_listing",
            ),),
        ))
        for q, (fp, eff) in enumerate(_QUARTERS):
            # 분기별 약간 다른 값(평가가 상수 단축 안 되게).
            fins.append(_fin(
                code=code, fiscal_period=fp, account=_BASIC_EPS,
                value=str(1000 + q * 10 + i % 7), eff=eff,
            ))
            fins.append(_fin(
                code=code, fiscal_period=fp, account=_NET_INCOME,
                value=str(5_000_000_000 + q * 1_000_000 + i * 100), eff=eff,
            ))
            fins.append(_fin(
                code=code, fiscal_period=fp, account=_EQUITY,
                value=str(50_000_000_000 + q * 1_000_000 + i * 100), eff=eff,
            ))
    return stocks, fins


def _run_screen(stocks, fins):  # noqa: ANN001, ANN202
    """1회 screen_active_codes — financial-only 조건(eps + roe), 대부분 통과."""
    return screen_active_codes(
        as_of=_AS_OF,
        conditions=[
            ConditionIn(factor="eps:basic-ttm-consolidated-ifrs",
                        op=OpEnum.GT, value="0"),
            ConditionIn(factor="roe:ttm-avg-equity-consolidated-ifrs",
                        op=OpEnum.GT, value="-9999"),
        ],
        stocks_repo=FakeStocksMasterRepository(records=stocks),
        pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository(records=[]),
        financial_repo=FakeFinancialRepository(records=fins),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=FakeMarketCapRepository(records=[]),
        treasury_repo=FakeTreasurySharesRepository(records=[]),
        macro_repo=FakeMacroIndicatorRepository(records=[]),
        dividend_repo=FakeDividendRepository(records=[]),
    )


def _cum_by_substr(stats: pstats.Stats, substr: str) -> float:
    """함수명에 substr 포함하는 항목들의 cumulative time 합(중복 호출 경로 합산)."""
    total = 0.0
    for (_file, _line, func), (_cc, _nc, _tt, ct, _callers) in stats.stats.items():  # type: ignore[attr-defined]
        if substr in func:
            total += ct
    return total


def _bench(n: int) -> None:
    stocks, fins = _gen_universe(n)
    # warm-up (import/JIT-less 지만 첫 호출의 lazy 초기화 제거).
    matched = _run_screen(stocks, fins)

    t0 = time.perf_counter()
    _run_screen(stocks, fins)
    wall = time.perf_counter() - t0

    prof = cProfile.Profile()
    prof.enable()
    _run_screen(stocks, fins)
    prof.disable()
    stats = pstats.Stats(prof, stream=io.StringIO())

    # 전체 프로파일 시간(루트 cumtime 근사 = 모든 tottime 합).
    total_tt = sum(v[2] for v in stats.stats.values())  # type: ignore[attr-defined]
    eval_ct = _cum_by_substr(stats, "evaluate")
    adjust_ct = _cum_by_substr(stats, "adjust")  # PriceAdjuster(현 0 — price 없음)
    prime_ct = _cum_by_substr(stats, "prime") + _cum_by_substr(stats, "_bulk")
    list_active_ct = _cum_by_substr(stats, "list_active")

    # 이득 천장 = evaluate 가 screen CPU 에서 차지하는 비율. profile cumtime 을
    # 비프로파일 wall 로 나누면 cProfile 오버헤드로 >100% 가 나오므로(아티팩트),
    # **프로파일-내부** 비율(eval cumtime / profile 전체 tottime 합)로 산출한다.
    eval_share = eval_ct / total_tt if total_tt > 0 else 0.0
    prime_share = prime_ct / total_tt if total_tt > 0 else 0.0
    print(f"\n=== N={n} 종목 (financial-only: eps + roe) ===")
    print(f"  matched          : {len(matched)} 종목")
    print(f"  wall time(실측)  : {wall * 1000:.1f} ms (un-profiled — 실제 latency)")
    print(f"  evaluate         : {eval_ct * 1000:.1f} ms = profile 의 {eval_share * 100:.0f}%"
          f"  (read-bypass 대체 가능 = 이득 천장 하한)")
    print(f"    └ adjust(price): {adjust_ct * 1000:.1f} ms (현 0 — price factor 없음)")
    print(f"  prime/bulk       : {prime_ct * 1000:.1f} ms = profile 의 {prime_share * 100:.0f}%"
          f"  (대체 불가 — 이미 캐싱됨)")
    print(f"  list_active      : {list_active_ct * 1000:.1f} ms")
    print(f"  → 이득 천장(하한): screen CPU 의 ~{eval_share * 100:.0f}% 가 평가 "
          f"(per-code {eval_ct / n * 1e6:.0f} µs). prime 은 {prime_share * 100:.0f}% "
          f"(eval/prime ≈ {eval_ct / prime_ct:.0f}×).")


def main() -> None:
    print("screen read-bypass Slice 0 측정 — evaluator.evaluate 비중 = 이득 천장")
    print("(financial-only=하한; price factor 포함 시 PriceAdjuster 로 eval 비중 ↑)")
    for n in (200, 500, 1000, 2500):
        _bench(n)


if __name__ == "__main__":
    main()
