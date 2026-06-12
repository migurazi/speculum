"""보유수익률 corporate-action 보정 정합성 회귀 테스트 (BACKTEST_ENGINE_VERSION 1.1).

버그(1.0): `_portfolio_return` 이 close@t 를 as_of=t, close@t_next 를 as_of=t_next
로 **서로 다른 보정 기준**으로 조회 → 보유구간 (t, t_next] 안에 price-adjust
corporate action(분할/병합/유상증자)이 발효되면 분모·분자 보정이 어긋나 허위 ±점프
수익률이 equity_curve 에 주입됐다(2:1 분할 → 허위 -50%).

수정(1.1): `_adjusted_closes_at` 이 **단일 as_of=t_next 보정 시계열**에서 두 시점
종가를 함께 추출 → 보정 기준 통일 → 구간 내 분할 발효 시에도 비율 왜곡 0.

테스트 매트릭스:
    1. 보유구간 내 2:1 분할 → 실제 가치 불변이므로 수익률 ≈ 0 (수정 검증).
       + 두-기준 불일치(as_of=t vs as_of=t_next)가 실제로 존재함을 직접 증명.
    2. reverse_split(1:2 병합) → 허위 +점프 없이 수익률 ≈ 0.
    3. corporate action 없는 순수 가격 변화 → 수익률 정확(무액션 회귀 가드).
    4. 보유구간 내 폐지(시계열 조기 종료) → 폐지일까지 보유수익률 보존.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakePriceRepository,
)
from app.repositories.pit_protocols import CorporateActionRecord, PriceRecord
from app.services.backtest_engine import _adjusted_closes_at, _portfolio_return
from app.services.price_adjuster import PriceAdjuster

_LINEAGE = UUID("00000000-0000-0000-0000-0000000000aa")
_CITATION = UUID("00000000-0000-0000-0000-00000000ffff")
_CODE = "000001"


def _price(*, d: date, close: int) -> PriceRecord:
    p = Decimal(close)
    return PriceRecord(
        id=uuid4(), code=_CODE, code_lineage_id=_LINEAGE,
        effective_date=d,
        open_raw=p, high_raw=p, low_raw=p, close_raw=p,
        volume=1_000_000, trading_value=p * Decimal(1_000_000),
        close_adjusted=p,
        citation_id=_CITATION,
        created_at=datetime(d.year, d.month, d.day, 17, 0, tzinfo=UTC),
    )


def _action(
    *, action_type: str, announced: date, effective: date,
    details: dict | None = None,
) -> CorporateActionRecord:
    return CorporateActionRecord(
        id=uuid4(), code=_CODE, code_lineage_id=_LINEAGE,
        action_type=action_type,
        announced_date=announced, effective_date=effective,
        payment_date=None, ratio=None, cash_amount=None,
        details=details or {},
        citation_id=_CITATION, superseded_by=None,
        created_at=datetime(announced.year, announced.month, announced.day,
                            9, 0, tzinfo=UTC),
    )


_T = date(2024, 4, 1)
_T_NEXT = date(2024, 5, 1)
_START = date(2024, 1, 1)


# =============================================================================
# 1. 보유구간 내 2:1 분할 — 수익률 ≈ 0 (허위 -50% 미주입)
# =============================================================================

def test_split_during_holding_no_spurious_return() -> None:
    """[t, t_next] 안 2:1 분할 — 실제 가치 불변이므로 보유수익률 ≈ 0.

    분할 전 100000 → 분할(04-15, split_ratio=2)로 명목 반토막 → 분할 후 50000.
    가치는 불변(주식수 2배). 올바른 보유수익률 = 0%.
    """
    prices = [
        _price(d=_T, close=100_000),         # 분할 전 명목가
        _price(d=date(2024, 4, 15), close=50_000),  # 분할 발효일 (반토막)
        _price(d=_T_NEXT, close=50_000),     # 분할 후
    ]
    actions = [_action(
        action_type="split", announced=date(2024, 3, 1),
        effective=date(2024, 4, 15), details={"split_ratio": 2},
    )]
    price_repo = FakePriceRepository(prices)
    ca_repo = FakeCorporateActionRepository(actions)

    ret = _portfolio_return(
        holdings=(_CODE,), t=_T, t_next=_T_NEXT, fetch_start=_START,
        price_repo=price_repo, corporate_action_repo=ca_repo,
        adjuster=PriceAdjuster(),
    )
    # 단일 t_next 보정 기준 → close@t = 100000×0.5 = 50000, close@t_next = 50000
    # → 수익률 0 (허위 -50% 미발생).
    assert ret == Decimal(0)

    # 버그의 근원을 직접 증명 — 두 보정 기준이 실제로 다른 close@t 를 낸다.
    by_tnext = _adjusted_closes_at(
        _CODE, as_of=_T_NEXT, start=_START, pick_dates=(_T, _T_NEXT),
        price_repo=price_repo, corporate_action_repo=ca_repo,
        adjuster=PriceAdjuster(),
    )
    by_t = _adjusted_closes_at(
        _CODE, as_of=_T, start=_START, pick_dates=(_T,),
        price_repo=price_repo, corporate_action_repo=ca_repo,
        adjuster=PriceAdjuster(),
    )
    assert by_tnext is not None and by_t is not None
    # t_next 기준: 분할이 back-adjust 됨 → close@t = 50000.
    assert by_tnext[_T] == Decimal(50_000)
    # t 기준(옛 버그 경로): 분할 미적용(effective>as_of=t) → close@t = 100000.
    assert by_t[_T] == Decimal(100_000)
    # 옛 코드는 close@t_next(50000)/close@t(100000) = 허위 -50% 였음을 확인.
    assert by_tnext[_T_NEXT] / by_t[_T] - Decimal(1) == Decimal("-0.5")


# =============================================================================
# 2. reverse_split — 허위 +점프 미주입
# =============================================================================

def test_reverse_split_during_holding_no_spurious_gain() -> None:
    """[t, t_next] 안 1:2 병합(reverse_split) — 명목가 2배여도 수익률 ≈ 0."""
    prices = [
        _price(d=_T, close=10_000),          # 병합 전 명목가
        _price(d=date(2024, 4, 15), close=20_000),  # 병합 발효 (명목 2배)
        _price(d=_T_NEXT, close=20_000),
    ]
    # reverse_split: 발효 후 명목가 ×merge_ratio. 과거가격은 그 역수로 back-adjust.
    actions = [_action(
        action_type="reverse_split", announced=date(2024, 3, 1),
        effective=date(2024, 4, 15), details={"merge_ratio": 2},
    )]
    ret = _portfolio_return(
        holdings=(_CODE,), t=_T, t_next=_T_NEXT, fetch_start=_START,
        price_repo=FakePriceRepository(prices),
        corporate_action_repo=FakeCorporateActionRepository(actions),
        adjuster=PriceAdjuster(),
    )
    # 단일 기준 → close@t = 10000×2 = 20000, close@t_next = 20000 → 0% (허위 +100% 없음).
    assert ret == Decimal(0)


# =============================================================================
# 3. corporate action 없는 순수 가격 변화 — 수정이 무액션 케이스 불변
# =============================================================================

def test_plain_holding_return_unchanged_without_actions() -> None:
    """액션 없으면 단순 close@t_next/close@t - 1 (보정 통일 변경의 무액션 회귀 가드)."""
    prices = [
        _price(d=_T, close=10_000),
        _price(d=_T_NEXT, close=11_000),
    ]
    ret = _portfolio_return(
        holdings=(_CODE,), t=_T, t_next=_T_NEXT, fetch_start=_START,
        price_repo=FakePriceRepository(prices),
        corporate_action_repo=FakeCorporateActionRepository([]),
        adjuster=PriceAdjuster(),
    )
    assert ret == Decimal("0.1")  # 11000/10000 - 1


# =============================================================================
# 4. 보유구간 내 폐지 — 폐지일까지 보유수익률 보존
# =============================================================================

def test_delisting_during_holding_uses_last_close() -> None:
    """t_next 이전 시계열 종료(폐지) → close@t_next = 폐지일 종가(현금 동결 의미 보존)."""
    prices = [
        _price(d=_T, close=10_000),
        _price(d=date(2024, 4, 20), close=12_000),  # 폐지일 (마지막 종가)
        # t_next(05-01) 이전에 종료 — 이후 가격 없음.
    ]
    ret = _portfolio_return(
        holdings=(_CODE,), t=_T, t_next=_T_NEXT, fetch_start=_START,
        price_repo=FakePriceRepository(prices),
        corporate_action_repo=FakeCorporateActionRepository([]),
        adjuster=PriceAdjuster(),
    )
    # close@t = 10000, close@t_next = 폐지일 최신 12000 → 폐지일까지 +20% 보존.
    assert ret == Decimal("0.2")


# =============================================================================
# 5. 구간 내 **공시**된 분할 (late disclosure) — 단일 t_next 기준 일관 적용
# =============================================================================

def test_split_announced_within_window_consistent_basis() -> None:
    """announced·effective 모두 (t, t_next] 안인 분할 — 단일 as_of=t_next 기준이라
    두 leg(close@t, close@t_next)에 일관 적용 → 왜곡 0 (oracle M2 커버리지).

    옛 코드는 close@t 를 as_of=t 로 fetch/보정해 t 시점엔 이 분할이 announced 도
    안 된 것으로 보여(invisible) close@t 미보정 → 허위 점프. 단일 t_next 기준이면
    announced<=t_next 라 visible + effective<=t_next 라 back-adjust 적용.
    """
    prices = [
        _price(d=_T, close=100_000),                  # 분할 전
        _price(d=date(2024, 4, 20), close=50_000),    # 분할 발효 (반토막)
        _price(d=_T_NEXT, close=50_000),
    ]
    # announced 가 t(04-01) **이후** — 보유구간 내 늦은 공시.
    actions = [_action(
        action_type="split", announced=date(2024, 4, 10),
        effective=date(2024, 4, 20), details={"split_ratio": 2},
    )]
    ret = _portfolio_return(
        holdings=(_CODE,), t=_T, t_next=_T_NEXT, fetch_start=_START,
        price_repo=FakePriceRepository(prices),
        corporate_action_repo=FakeCorporateActionRepository(actions),
        adjuster=PriceAdjuster(),
    )
    # close@t = 100000×0.5 = 50000, close@t_next = 50000 → 0% (허위 -50% 미발생).
    assert ret == Decimal(0)


# =============================================================================
# 6. holdings bulk prime (ⓒ) — per-code N+1 제거 + 결과 불변
# =============================================================================

class _CountingPrice:
    """FakePriceRepository 위임 + bulk vs per-code 호출 카운트(ⓒ 검증)."""

    def __init__(self, records: list[PriceRecord]) -> None:
        self._fake = FakePriceRepository(records)
        self.bulk_calls = 0
        self.single_calls = 0

    def fetch_prices(self, code, *, as_of, start, batch_cutoff=None):
        self.single_calls += 1
        return self._fake.fetch_prices(
            code, as_of=as_of, start=start, batch_cutoff=batch_cutoff,
        )

    def fetch_prices_bulk(self, codes, *, as_of, start, batch_cutoff=None):
        self.bulk_calls += 1
        return self._fake.fetch_prices_bulk(
            codes, as_of=as_of, start=start, batch_cutoff=batch_cutoff,
        )


class _CountingCA:
    """FakeCorporateActionRepository 위임 + bulk vs per-code 호출 카운트."""

    def __init__(self, records: list[CorporateActionRecord]) -> None:
        self._fake = FakeCorporateActionRepository(records)
        self.bulk_calls = 0
        self.single_calls = 0

    def fetch_actions(self, code, *, as_of, action_types=None):
        self.single_calls += 1
        return self._fake.fetch_actions(
            code, as_of=as_of, action_types=action_types,
        )

    def fetch_all_actions_bulk(self, codes):
        self.bulk_calls += 1
        return self._fake.fetch_all_actions_bulk(codes)


def test_portfolio_return_bulk_primes_holdings_no_n_plus_1() -> None:
    """holdings 다종목 → price/CA 가 루프 전 1회 bulk prime, per-code single 0(ⓒ).

    결과(수익률)는 raw 경로와 동일해야 함(bulk == 단건 byte-동일).
    """
    codes = ("000001", "000002", "000003")
    prices: list[PriceRecord] = []
    for c in codes:
        for d, close in ((_T, 10_000), (_T_NEXT, 11_000)):
            prices.append(PriceRecord(
                id=uuid4(), code=c, code_lineage_id=_LINEAGE,
                effective_date=d,
                open_raw=Decimal(close), high_raw=Decimal(close),
                low_raw=Decimal(close), close_raw=Decimal(close),
                volume=1_000_000, trading_value=Decimal(close) * Decimal(1_000_000),
                close_adjusted=Decimal(close), citation_id=_CITATION,
                created_at=datetime(d.year, d.month, d.day, 17, 0, tzinfo=UTC),
            ))
    price_repo = _CountingPrice(prices)
    ca_repo = _CountingCA([])

    ret = _portfolio_return(
        holdings=codes, t=_T, t_next=_T_NEXT, fetch_start=_START,
        price_repo=price_repo, corporate_action_repo=ca_repo,
        adjuster=PriceAdjuster(),
    )
    # 전 종목 +10% 동일 → 동일가중 평균 +10%.
    assert ret == Decimal("0.1")
    # 루프 전 1회 bulk prime → per-code single fetch 0 (N+1 제거).
    assert price_repo.bulk_calls == 1
    assert price_repo.single_calls == 0
    assert ca_repo.bulk_calls == 1
    assert ca_repo.single_calls == 0


def test_portfolio_return_bulk_split_in_window_matches_single_path() -> None:
    """split 이 보유구간 (t,t_next] 내 발효 — bulk-primed 캐시 경로가 raw 단건 경로와
    **동일** 수익률(§2.10 seam 직접 검증, oracle L2). 보정 정합성(1.1)이 bulk 경로
    에서도 보존돼 허위 -50% 미발생.
    """
    prices = [
        _price(d=_T, close=100_000),
        _price(d=date(2024, 4, 15), close=50_000),  # 2:1 분할 발효.
        _price(d=_T_NEXT, close=50_000),
    ]
    actions = [_action(
        action_type="split", announced=date(2024, 3, 1),
        effective=date(2024, 4, 15), details={"split_ratio": 2},
    )]
    common = dict(
        holdings=(_CODE,), t=_T, t_next=_T_NEXT, fetch_start=_START,
        adjuster=PriceAdjuster(),
    )
    # bulk-primed 캐시 경로 (counting wrapper → CachingPrice/CA 내부 사용).
    ret_bulk = _portfolio_return(
        price_repo=_CountingPrice(list(prices)),
        corporate_action_repo=_CountingCA(list(actions)),
        **common,  # type: ignore[arg-type]
    )
    # raw 단건 경로 (Fake 직접 — CachingPrice/CA 가 isinstance 아님 → 새 wrap+prime,
    # 그러나 결과는 단건 fetch 와 byte-동일해야 함).
    ret_raw = _portfolio_return(
        price_repo=FakePriceRepository(list(prices)),
        corporate_action_repo=FakeCorporateActionRepository(list(actions)),
        **common,  # type: ignore[arg-type]
    )
    assert ret_bulk == ret_raw == Decimal(0)  # 분할 왜곡 0(보정 정합성 보존).
