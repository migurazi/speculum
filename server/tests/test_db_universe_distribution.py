"""db_universe_distribution 단위 테스트 — ADR-0022 D2/D3 유니버스-상대 분포.

테스트 매트릭스:
1. zscore — 평균/모표준편차 정확성
2. percentile — 백분위 + tie-break (<= cumulative, 동값 동률)
3. min_max_scale — (v-min)/(max-min)
4. PIT 모집단 — as_of active (이후 폐지 포함 / as_of 이전 폐지·이후 상장 제외)
5. PIT 분포 입력 — effective_date <= as_of fact 만
6. N/A 종목 모집단 제외 — N/A 종목이 분모에서 빠짐
7. 경계 — stddev=0→None, max==min→None, 모집단 1 종목, 빈 모집단
8. field별 분포 캐싱 — 같은 field 재요청 시 list_active 1 회
9. tie-break 결정성 — 동값 percentile 재현
10. evaluator 통합 — 유니버스-상대 op 이 provider 주입 시 실값
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.repositories.fakes import (
    FakeFinancialRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
)
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    MarketCapRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.services.db_field_provider import DbFieldProvider
from app.services.db_universe_distribution import (
    _OFF_UNIVERSE_TARGET,
    DISTRIBUTION_POLICY_VERSION,
    SMALL_SAMPLE_THRESHOLD,
    DbUniverseDistributionProvider,
    FieldDistribution,
    _build_field_distribution,
)
from app.services.factor_evaluator import FactorEvaluator, MalformedFormulaError

# financial_repo 는 market_cap field 해소에 미사용이나 생성자 필수 — 빈 Fake.
_EMPTY_FIN = FakeFinancialRepository([])

# 분포 산출에 쓰는 field — market_cap_krx_official 은 FakeMarketCapRepository 의
# 단일 scalar fetch (effective_date <= as_of 중 최신). 분포 계산 경로의 결정성을
# 단순 fixture 로 검증하기 충분 (파생 field 캐싱·evaluator 경로는 별도 통합 테스트).
_FIELD = "market_cap_krx_official"
_AS_OF = date(2024, 5, 15)
_CITATION = UUID("00000000-0000-0000-0000-0000000000cc")


# =============================================================================
# Builders
# =============================================================================

def _stock(
    *,
    code: str,
    listing_date: date = date(2020, 1, 1),
    delisting_date: date | None = None,
    market: str = "KOSPI",
    security_type: str = "common",
) -> StockMasterRecord:
    return StockMasterRecord(
        id=uuid4(),
        current_code=None if delisting_date is not None
        and delisting_date <= _AS_OF else code,
        current_name=f"종목{code}",
        market=market,
        listing_date=listing_date,
        delisting_date=delisting_date,
        fiscal_month=12,
        code_history=(
            CodeHistoryEntry(
                code=code, valid_from=listing_date,
                valid_to=delisting_date, reason="initial_listing",
            ),
        ),
        security_type=security_type,
    )


def _mc(
    *,
    code: str,
    market_cap: str,
    effective_date: date = date(2024, 5, 14),
    shares_outstanding: int = 1_000_000,
) -> MarketCapRecord:
    return MarketCapRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=uuid4(),
        effective_date=effective_date,
        market_cap=Decimal(market_cap),
        shares_outstanding=shares_outstanding,
        shares_treasury=None,
        citation_id=_CITATION,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _provider(
    *,
    stocks: list[StockMasterRecord],
    market_caps: list[MarketCapRecord],
    as_of: date = _AS_OF,
) -> DbUniverseDistributionProvider:
    """as_of active universe 의 market_cap 분포 provider — 가격은 빈 repo."""
    return DbUniverseDistributionProvider(
        as_of=as_of,
        stocks_repo=FakeStocksMasterRepository(stocks),
        pack=None,  # type: ignore[arg-type]  # market_cap 는 파생 아님 → pack 불필요
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository([]),
        financial_repo=_EMPTY_FIN,
        market_cap_repo=FakeMarketCapRepository(market_caps),
    )


# =============================================================================
# 1. zscore — 평균 / 모표준편차 정확성
# =============================================================================

def test_zscore_basic() -> None:
    """zscore = (v - μ) / σ (모표준편차 ddof=0).

    값 [10, 20, 30] → μ=20, σ=sqrt(((10-20)²+(20-20)²+(30-20)²)/3)=sqrt(200/3).
    """
    stocks = [_stock(code="000001"), _stock(code="000002"), _stock(code="000003")]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    sigma = (Decimal(200) / Decimal(3)).sqrt()
    # 평균값 → z=0.
    assert prov.zscore(_FIELD, Decimal("20")) == Decimal(0)
    # 최대값 → (30-20)/σ.
    assert prov.zscore(_FIELD, Decimal("30")) == Decimal(10) / sigma
    assert prov.zscore(_FIELD, Decimal("10")) == Decimal(-10) / sigma


def test_zscore_stddev_zero_returns_none() -> None:
    """전 종목 동값 → σ=0 → zscore None (정식 N/A)."""
    stocks = [_stock(code="000001"), _stock(code="000002")]
    mcs = [_mc(code="000001", market_cap="50"), _mc(code="000002", market_cap="50")]
    prov = _provider(stocks=stocks, market_caps=mcs)
    assert prov.zscore(_FIELD, Decimal("50")) is None


def test_zscore_single_population_returns_none() -> None:
    """모집단 1 종목 → 표준편차 정의 불가 → None."""
    stocks = [_stock(code="000001")]
    mcs = [_mc(code="000001", market_cap="50")]
    prov = _provider(stocks=stocks, market_caps=mcs)
    assert prov.zscore(_FIELD, Decimal("50")) is None


# =============================================================================
# 2. percentile — 백분위 + tie-break
# =============================================================================

def test_percentile_basic() -> None:
    """percentile = count(v <= value) / n × 100. 값 [10,20,30,40]."""
    stocks = [_stock(code=f"00000{i}") for i in range(1, 5)]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
        _mc(code="000004", market_cap="40"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    # 10 이하 1 개 / 4 = 25%.
    assert prov.percentile(_FIELD, Decimal("10")) == Decimal("25")
    # 30 이하 3 개 / 4 = 75%.
    assert prov.percentile(_FIELD, Decimal("30")) == Decimal("75")
    # 최대값 40 → 100%.
    assert prov.percentile(_FIELD, Decimal("40")) == Decimal("100")


def test_percentile_tie_same_value() -> None:
    """동값 (tie) 은 모두 '이하' 로 함께 카운트 → 같은 percentile (결정성).

    값 [10, 20, 20, 40] → value=20 은 count(v<=20)=3 → 75% (두 동값 종목이 같은
    percentile, 임의 순위 부여 없음).
    """
    stocks = [_stock(code=f"00000{i}") for i in range(1, 5)]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="20"),
        _mc(code="000004", market_cap="40"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    assert prov.percentile(_FIELD, Decimal("20")) == Decimal("75")


def test_percentile_tie_break_deterministic() -> None:
    """동값 percentile 재현 — 같은 입력 (순서만 다른) 같은 결과."""
    stocks_a = [_stock(code=f"00000{i}") for i in range(1, 5)]
    stocks_b = list(reversed(stocks_a))
    mcs = [
        _mc(code="000001", market_cap="20"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="10"),
        _mc(code="000004", market_cap="40"),
    ]
    prov_a = _provider(stocks=stocks_a, market_caps=mcs)
    prov_b = _provider(stocks=stocks_b, market_caps=list(reversed(mcs)))
    assert (prov_a.percentile(_FIELD, Decimal("20"))
            == prov_b.percentile(_FIELD, Decimal("20")))


def test_percentile_empty_population_returns_none() -> None:
    """valid 모집단 0 → percentile None."""
    stocks = [_stock(code="000001")]
    prov = _provider(stocks=stocks, market_caps=[])  # market_cap record 없음 → N/A
    assert prov.percentile(_FIELD, Decimal("10")) is None


# =============================================================================
# 3. min_max_scale
# =============================================================================

def test_min_max_scale_basic() -> None:
    """(v - min) / (max - min). 값 [10, 30, 50] → min=10, max=50, span=40."""
    stocks = [_stock(code=f"00000{i}") for i in range(1, 4)]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="30"),
        _mc(code="000003", market_cap="50"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    assert prov.min_max_scale(_FIELD, Decimal("10")) == Decimal("0")
    assert prov.min_max_scale(_FIELD, Decimal("50")) == Decimal("1")
    assert prov.min_max_scale(_FIELD, Decimal("30")) == Decimal("0.5")


def test_min_max_scale_max_equals_min_returns_none() -> None:
    """전 종목 동값 → max==min → 분모 0 → None."""
    stocks = [_stock(code="000001"), _stock(code="000002")]
    mcs = [_mc(code="000001", market_cap="7"), _mc(code="000002", market_cap="7")]
    prov = _provider(stocks=stocks, market_caps=mcs)
    assert prov.min_max_scale(_FIELD, Decimal("7")) is None


def test_min_max_scale_single_population_returns_none() -> None:
    """모집단 1 종목 → max==min → None."""
    stocks = [_stock(code="000001")]
    mcs = [_mc(code="000001", market_cap="7")]
    prov = _provider(stocks=stocks, market_caps=mcs)
    assert prov.min_max_scale(_FIELD, Decimal("7")) is None


# =============================================================================
# 4. PIT 모집단 — as_of active universe (ADR-0022 D3)
# =============================================================================

def test_population_excludes_not_yet_listed() -> None:
    """as_of 이후 상장 종목은 모집단 제외 (미상장)."""
    stocks = [
        _stock(code="000001"),
        _stock(code="000002"),
        # as_of (2024-05-15) 이후 상장 — 모집단 제외.
        _stock(code="000003", listing_date=date(2024, 6, 1)),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="30"),
        _mc(code="000003", market_cap="1000"),  # 산입되면 분포 왜곡.
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    dist = prov._get_distribution(_FIELD)
    # 미상장 000003 제외 → 모집단 2 종목 [10, 30].
    assert dist.n == 2
    assert dist.sorted_values == (Decimal("10"), Decimal("30"))


def test_population_includes_future_delisted_not_past_delisted() -> None:
    """as_of 이후 폐지 예정 종목은 포함 (survivorship bias 방지), as_of 이전 폐지
    종목은 제외 (ADR-0022 D3 모집단 정의)."""
    stocks = [
        _stock(code="000001"),
        # as_of 이후 폐지 예정 — as_of 시점엔 active → 포함.
        _stock(code="000002", delisting_date=date(2024, 12, 1)),
        # as_of 이전 폐지 — 제외.
        _stock(code="000003", delisting_date=date(2024, 1, 1)),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="999"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    dist = prov._get_distribution(_FIELD)
    # 000001 + 000002 (future delisted 포함), 000003 (past delisted) 제외.
    assert dist.n == 2
    assert dist.sorted_values == (Decimal("10"), Decimal("20"))


def test_distribution_input_is_pit() -> None:
    """분포 입력값은 effective_date <= as_of fact 만 (look-ahead 0)."""
    stocks = [_stock(code="000001"), _stock(code="000002")]
    mcs = [
        _mc(code="000001", market_cap="10", effective_date=date(2024, 5, 10)),
        # as_of (2024-05-15) 이후 effective — provider 가 미사용 → 000002 는 N/A.
        _mc(code="000002", market_cap="20", effective_date=date(2024, 6, 1)),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    dist = prov._get_distribution(_FIELD)
    # 000002 의 미래 fact 제외 → 모집단 1 종목 [10].
    assert dist.n == 1
    assert dist.sorted_values == (Decimal("10"),)


# =============================================================================
# 5. N/A 종목 모집단 제외 (ADR-0022 D3)
# =============================================================================

def test_na_stock_excluded_from_population() -> None:
    """N/A 종목 (market_cap record 없음) 은 분모에서 제외."""
    stocks = [_stock(code="000001"), _stock(code="000002"), _stock(code="000003")]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000003", market_cap="30"),
        # 000002 는 record 없음 → get_scalar None → 모집단 제외.
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    dist = prov._get_distribution(_FIELD)
    assert dist.n == 2  # 000002 제외.
    assert dist.sorted_values == (Decimal("10"), Decimal("30"))
    # percentile 분모도 valid 2 종목 기준 — 30 이하 2 개 / 2 = 100%.
    assert prov.percentile(_FIELD, Decimal("30")) == Decimal("100")


# =============================================================================
# 6. field별 분포 캐싱 — list_active 1 회 (O(N) 보장)
# =============================================================================

class _CountingStocksRepo(FakeStocksMasterRepository):
    """list_active 호출 횟수 카운트 — 캐싱 검증."""

    def __init__(self, records: list[StockMasterRecord]) -> None:
        super().__init__(records)
        self.list_active_calls = 0

    def list_active(self, *, as_of: date):  # type: ignore[no-untyped-def]
        self.list_active_calls += 1
        return super().list_active(as_of=as_of)


def test_distribution_cached_per_field() -> None:
    """같은 field 의 zscore/percentile/min_max 반복 호출 → list_active 1 회."""
    stocks = [_stock(code="000001"), _stock(code="000002"), _stock(code="000003")]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
    ]
    repo = _CountingStocksRepo(stocks)
    prov = DbUniverseDistributionProvider(
        as_of=_AS_OF,
        stocks_repo=repo,
        pack=None,  # type: ignore[arg-type]
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository([]),
        financial_repo=_EMPTY_FIN,
        market_cap_repo=FakeMarketCapRepository(mcs),
    )
    prov.zscore(_FIELD, Decimal("20"))
    prov.percentile(_FIELD, Decimal("20"))
    prov.min_max_scale(_FIELD, Decimal("20"))
    # 분포 1 회만 계산 (field 캐시 hit) → active universe 도 1 회만 조회.
    assert repo.list_active_calls == 1


# =============================================================================
# 7. _build_field_distribution 직접 — 통계 단위
# =============================================================================

def test_build_field_distribution_empty() -> None:
    dist = _build_field_distribution([])
    assert dist.n == 0
    assert dist.mean is None
    assert dist.stddev is None
    assert dist.min_value is None
    assert dist.max_value is None


def test_build_field_distribution_stats() -> None:
    dist = _build_field_distribution([Decimal("30"), Decimal("10"), Decimal("20")])
    assert isinstance(dist, FieldDistribution)
    assert dist.n == 3
    assert dist.sorted_values == (Decimal("10"), Decimal("20"), Decimal("30"))
    assert dist.mean == Decimal("20")
    assert dist.min_value == Decimal("10")
    assert dist.max_value == Decimal("30")
    assert dist.stddev == (Decimal(200) / Decimal(3)).sqrt()


# =============================================================================
# 8. evaluator 통합 — 유니버스-상대 op 이 provider 주입 시 실값
# =============================================================================

def test_evaluator_integration_percentile_real_value() -> None:
    """ADR-0022 D2 — 유니버스-상대 op 이 분포 provider 주입 시 실값 (이전엔
    universe_distribution_required N/A).

    한 종목의 factor (op=percentile, field=market_cap_krx_official) 를 평가 —
    DbFieldProvider 로 종목값 fetch + DbUniverseDistributionProvider 로 분포 산출.
    """
    code = "000002"
    stocks = [_stock(code="000001"), _stock(code=code), _stock(code="000003")]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code=code, market_cap="20"),
        _mc(code="000003", market_cap="30"),
    ]
    evaluator = FactorEvaluator()
    market_cap_repo = FakeMarketCapRepository(mcs)
    dist_provider = DbUniverseDistributionProvider(
        as_of=_AS_OF,
        stocks_repo=FakeStocksMasterRepository(stocks),
        pack=None,  # type: ignore[arg-type]
        evaluator=evaluator,
        price_repo=FakePriceRepository([]),
        financial_repo=_EMPTY_FIN,
        market_cap_repo=market_cap_repo,
    )
    # 평가 대상 종목 (000002) 의 DbFieldProvider.
    target_provider = DbFieldProvider(
        code=code,
        as_of=_AS_OF,
        price_repo=FakePriceRepository([]),
        financial_repo=_EMPTY_FIN,
        market_cap_repo=market_cap_repo,
    )
    factor = {
        "canonical_id": "test:percentile-mcap",
        "uuid": "00000000-0000-0000-0000-000000000099",
        "formula": {
            "ast": {"op": "percentile", "field": _FIELD},
            "inputs": [_FIELD],
        },
    }
    result = evaluator.evaluate(
        factor, target_provider, as_of=_AS_OF,
        universe_distribution=dist_provider,
    )
    # 000002 값 20 → 20 이하 2 개 (10, 20) / 3 = 66.66...%.
    assert not result.is_na
    assert result.value == (Decimal(2) / Decimal(3)) * Decimal(100)


def test_evaluator_integration_as_of_invariant() -> None:
    """provider.as_of != evaluate as_of → MalformedFormulaError (PIT invariant)."""
    stocks = [_stock(code="000001")]
    mcs = [_mc(code="000001", market_cap="10")]
    market_cap_repo = FakeMarketCapRepository(mcs)
    evaluator = FactorEvaluator()
    dist_provider = DbUniverseDistributionProvider(
        as_of=date(2024, 6, 1),  # mismatch
        stocks_repo=FakeStocksMasterRepository(stocks),
        pack=None,  # type: ignore[arg-type]
        evaluator=evaluator,
        price_repo=FakePriceRepository([]),
        financial_repo=_EMPTY_FIN,
        market_cap_repo=market_cap_repo,
    )
    target_provider = DbFieldProvider(
        code="000001", as_of=_AS_OF,
        price_repo=FakePriceRepository([]),
        financial_repo=_EMPTY_FIN,
        market_cap_repo=market_cap_repo,
    )
    factor = {
        "canonical_id": "test:x",
        "uuid": "00000000-0000-0000-0000-000000000098",
        "formula": {"ast": {"op": "zscore", "field": _FIELD}, "inputs": [_FIELD]},
    }
    with pytest.raises(MalformedFormulaError, match="PIT invariant"):
        evaluator.evaluate(
            factor, target_provider, as_of=_AS_OF,
            universe_distribution=dist_provider,
        )


# =============================================================================
# 9. freeze — 정책 버전
# =============================================================================

def test_distribution_policy_version_is_str() -> None:
    assert isinstance(DISTRIBUTION_POLICY_VERSION, str)
    # ADR-0023 D6 — security_type partition (D5) 도입으로 "1.0" → "1.1" bump.
    assert DISTRIBUTION_POLICY_VERSION == "1.1"


# =============================================================================
# 10. ADR-0023 D5 — 분포 모집단 security_type partition
# =============================================================================

def test_partition_preferred_population_excludes_common() -> None:
    """우선주 종목의 market_cap percentile 은 우선주 모집단 기준 (보통주 혼합 X).

    보통주 [10, 20, 30] + 우선주 [40, 50]. 우선주 종목 (값 40) 의 percentile 은
    우선주 모집단 (40, 50) 기준 = 1/2 = 50% — 보통주 포함 전체 (5 종목) 기준이면
    40 이하 4 개 / 5 = 80% 가 되어야 하나, partition 으로 50% 여야 함 (D5).
    """
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="common"),
        _stock(code="000003", security_type="common"),
        _stock(code="000004", security_type="preferred"),
        _stock(code="000005", security_type="preferred"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
        _mc(code="000004", market_cap="40"),
        _mc(code="000005", market_cap="50"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    # 우선주 종목 (000004) 평가 — partition 컨텍스트 바인딩.
    resolved = prov.bind_target_security_type("000004")
    assert resolved == "preferred"
    # 우선주 모집단 (40, 50) 기준 — 40 이하 1 개 / 2 = 50% (보통주 혼합 아님).
    assert prov.percentile(_FIELD, Decimal("40")) == Decimal("50")
    # 우선주 모집단 분포 (n=2): max=50, min=40 → min_max_scale(40)=0.
    assert prov.min_max_scale(_FIELD, Decimal("40")) == Decimal("0")


def test_partition_common_population_excludes_preferred() -> None:
    """보통주 종목의 percentile 은 보통주 모집단 기준 (우선주 혼합 X)."""
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="common"),
        _stock(code="000003", security_type="common"),
        _stock(code="000004", security_type="preferred"),
        _stock(code="000005", security_type="preferred"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
        _mc(code="000004", market_cap="40"),
        _mc(code="000005", market_cap="50"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    prov.bind_target_security_type("000003")  # 보통주.
    # 보통주 모집단 (10, 20, 30) 기준 — 30 이하 3 개 / 3 = 100%.
    assert prov.percentile(_FIELD, Decimal("30")) == Decimal("100")


def test_partition_same_value_different_population_different_percentile() -> None:
    """같은 값이라도 모집단 (자산군) 다르면 percentile 다름 (D5 핵심).

    값 30 을 두 모집단에서 평가:
    - 보통주 모집단 (10, 20, 30): 30 이하 3 개 / 3 = 100%.
    - 우선주 모집단 (30, 40, 50): 30 이하 1 개 / 3 = 33.33%.
    같은 value=30 이 자산군 partition 에 따라 다른 percentile.
    """
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="common"),
        _stock(code="000003", security_type="common"),
        _stock(code="000004", security_type="preferred"),
        _stock(code="000005", security_type="preferred"),
        _stock(code="000006", security_type="preferred"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
        _mc(code="000004", market_cap="30"),
        _mc(code="000005", market_cap="40"),
        _mc(code="000006", market_cap="50"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)

    prov.bind_target_security_type("000003")  # 보통주.
    common_pctile = prov.percentile(_FIELD, Decimal("30"))
    assert common_pctile == Decimal("100")

    prov.bind_target_security_type("000004")  # 우선주.
    preferred_pctile = prov.percentile(_FIELD, Decimal("30"))
    assert preferred_pctile == (Decimal(1) / Decimal(3)) * Decimal(100)

    assert common_pctile != preferred_pctile


def test_partition_default_is_common_when_unbound() -> None:
    """bind 미호출 시 default 모집단 = 보통주 (기존 common-only 동작 불변).

    보통주 [10, 20, 30] + 우선주 [1000] 혼재. bind 안 하면 보통주 모집단으로
    산출 — 30 이하 3 개 / 3 = 100% (우선주 1000 미산입).
    """
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="common"),
        _stock(code="000003", security_type="common"),
        _stock(code="000004", security_type="preferred"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
        _mc(code="000004", market_cap="1000"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    # bind 미호출 — default common.
    assert prov.percentile(_FIELD, Decimal("30")) == Decimal("100")


def test_partition_bind_off_universe_yields_empty_population() -> None:
    """active 아닌 code bind → off-universe sentinel → universe-상대 N/A (V-M2-2).

    이전엔 default "common" 으로 귀속돼 common 모집단 percentile(모집단 불일치
    값)을 산출했으나(m2-conformance-review V-M2-2), off-universe code 는 어느
    모집단에도 없으므로 분포가 빈 모집단 → percentile/zscore/min_max None +
    sample_size 0 (틀린 값 대신 정직한 N/A, §2.1 Fidelity).
    """
    stocks = [_stock(code="000001", security_type="common")]
    mcs = [_mc(code="000001", market_cap="10")]
    prov = _provider(stocks=stocks, market_caps=mcs)
    resolved = prov.bind_target_security_type("999999")  # active 아님 → off-universe.
    # default "common" 으로 귀속하지 않음 — sentinel(빈 모집단).
    assert resolved == _OFF_UNIVERSE_TARGET
    assert resolved != "common"
    # 빈 모집단 → universe-상대 op 전부 N/A (모집단 불일치 값 산출 0).
    assert prov.percentile(_FIELD, Decimal("10")) is None
    assert prov.zscore(_FIELD, Decimal("10")) is None
    assert prov.min_max_scale(_FIELD, Decimal("10")) is None
    # sample_size 0 → 표시층이 "모집단 없음" 으로 처리(common n 오보 0).
    assert prov.sample_size(_FIELD) == 0


def test_partition_distribution_cache_keyed_by_security_type() -> None:
    """분포 캐시가 (security_type, field) 키 — 자산군별 분포 분리 (list_active 1 회)."""
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="preferred"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
    ]
    repo = _CountingStocksRepo(stocks)
    prov = DbUniverseDistributionProvider(
        as_of=_AS_OF,
        stocks_repo=repo,
        pack=None,  # type: ignore[arg-type]
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository([]),
        financial_repo=_EMPTY_FIN,
        market_cap_repo=FakeMarketCapRepository(mcs),
    )
    prov.bind_target_security_type("000001")
    prov.percentile(_FIELD, Decimal("10"))  # common 분포.
    prov.bind_target_security_type("000002")
    prov.percentile(_FIELD, Decimal("20"))  # preferred 분포 (별도 캐시 키).
    # partition 별 분포 2 개 캐시.
    assert len(prov._distribution_cache) == 2
    assert ("common", _FIELD) in prov._distribution_cache
    assert ("preferred", _FIELD) in prov._distribution_cache
    # list_active 는 partition map 1 회만 (bind + percentile 모두 캐시 map 공유).
    assert repo.list_active_calls == 1


# =============================================================================
# 11. ADR-0024 D5 — sample_size(field) 조회 메서드 (소표본 디스클로저 표면)
# =============================================================================

def test_sample_size_returns_population_n() -> None:
    """sample_size(field) = 바인딩된 자산군 모집단의 valid n (FieldDistribution.n)."""
    stocks = [_stock(code="000001"), _stock(code="000002"), _stock(code="000003")]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    assert prov.sample_size(_FIELD) == 3


def test_sample_size_reflects_partition() -> None:
    """우선주 bind 시 우선주 모집단 n (partition 반영, 보통주 혼합 X)."""
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="common"),
        _stock(code="000003", security_type="common"),
        _stock(code="000004", security_type="preferred"),
        _stock(code="000005", security_type="preferred"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
        _mc(code="000004", market_cap="40"),
        _mc(code="000005", market_cap="50"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    # 우선주 bind → 우선주 모집단 n=2.
    prov.bind_target_security_type("000004")
    assert prov.sample_size(_FIELD) == 2
    # 보통주 bind → 보통주 모집단 n=3.
    prov.bind_target_security_type("000003")
    assert prov.sample_size(_FIELD) == 3


def test_sample_size_excludes_na_population() -> None:
    """N/A 종목은 모집단 제외 — sample_size 가 valid n 만 센다 (분포 일관)."""
    stocks = [_stock(code="000001"), _stock(code="000002"), _stock(code="000003")]
    # 000003 은 market_cap fact 없음 → N/A → 모집단 제외.
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    assert prov.sample_size(_FIELD) == 2


def test_sample_size_single_population_is_one() -> None:
    """모집단 1 종목 → sample_size=1 (n==1 percentile=100 케이스의 표면, ADR-0024 D2).

    percentile 은 값 100 을 보존하고 (N/A 아님), sample_size=1 로 표시층이
    small_sample 디스클로저를 붙인다.
    """
    stocks = [_stock(code="000001")]
    mcs = [_mc(code="000001", market_cap="50")]
    prov = _provider(stocks=stocks, market_caps=mcs)
    # 값은 보존 (percentile = 1/1*100 = 100).
    assert prov.percentile(_FIELD, Decimal("50")) == Decimal("100")
    # sample_size = 1 → 표시층 small_sample 트리거 (1 < 30).
    assert prov.sample_size(_FIELD) == 1
    assert prov.sample_size(_FIELD) < SMALL_SAMPLE_THRESHOLD


def test_sample_size_empty_population_is_zero() -> None:
    """빈 모집단 (전 종목 N/A) → sample_size=0."""
    stocks = [_stock(code="000001")]
    prov = _provider(stocks=stocks, market_caps=[])  # market_cap fact 없음.
    assert prov.sample_size(_FIELD) == 0


def test_sample_size_uses_distribution_cache() -> None:
    """sample_size 는 분포 캐시 진입점 — percentile 후 호출 시 재계산 없음.

    universe-상대 op 평가 직후 표시층이 sample_size 를 조회하는 통상 경로는
    cache hit (같은 (security_type, field) 분포 공유) — 추가 O(N) 없음.
    """
    stocks = [_stock(code="000001"), _stock(code="000002")]
    mcs = [_mc(code="000001", market_cap="10"), _mc(code="000002", market_cap="20")]
    prov = _provider(stocks=stocks, market_caps=mcs)
    prov.percentile(_FIELD, Decimal("10"))  # 분포 1 회 계산 + 캐시.
    cache_len_after_pct = len(prov._distribution_cache)
    assert prov.sample_size(_FIELD) == 2
    # sample_size 가 새 캐시 엔트리를 만들지 않음 (cache hit).
    assert len(prov._distribution_cache) == cache_len_after_pct


def test_small_sample_threshold_value() -> None:
    """ADR-0024 D1 — 소표본 임계값 = 30 (단일 디스클로저 트리거)."""
    assert SMALL_SAMPLE_THRESHOLD == 30


# =============================================================================
# 12. M2 T79 리츠 — ADR-0023 D5 3-way partition + ADR-0024 리츠 소표본 (회귀 가드)
# =============================================================================
#
# T78 이 partition/소표본을 security_type-generic 으로 구현했고 `reit` 가 이미
# enum 에 있으므로 리츠는 자동 동작한다. 본 섹션은 (1) 리츠 모집단이 common/
# preferred 와 *분리*됨(D5)과 (2) 리츠 모집단이 작아 small_sample 이 자동 부착됨
# (ADR-0024 — 리츠는 십수 개로 거의 항상 소표본)을 회귀로 박는다. partition 을
# 자산군 특수화하는 미래 변경이 리츠를 깨면 여기서 감지된다.

def test_partition_reit_population_excludes_common_and_preferred() -> None:
    """리츠 종목의 percentile 은 리츠 모집단 기준 — common/preferred 혼합 X (D5).

    보통주 [10, 20] + 우선주 [100] + 리츠 [30, 40, 50]. 리츠 종목(값 30)의
    percentile 은 리츠 모집단 (30, 40, 50) 기준 = 1/3 = 33.33%. 전체 6 종목 혼합
    이면 30 이하 3 개 / 6 = 50% 가 되어야 하나, 3-way partition 으로 분리됨.
    """
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="common"),
        _stock(code="000003", security_type="preferred"),
        _stock(code="000004", security_type="reit"),
        _stock(code="000005", security_type="reit"),
        _stock(code="000006", security_type="reit"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="100"),
        _mc(code="000004", market_cap="30"),
        _mc(code="000005", market_cap="40"),
        _mc(code="000006", market_cap="50"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    # 리츠 종목(000004) 평가 — partition 컨텍스트 바인딩.
    resolved = prov.bind_target_security_type("000004")
    assert resolved == "reit"
    # 리츠 모집단 (30, 40, 50) 기준 — 30 이하 1 개 / 3 = 33.33% (혼합 아님).
    assert prov.percentile(_FIELD, Decimal("30")) == (
        Decimal(1) / Decimal(3) * Decimal(100)
    )
    # 리츠 모집단 min=30, max=50 → min_max_scale(30)=0 (혼합이면 다른 값).
    assert prov.min_max_scale(_FIELD, Decimal("30")) == Decimal("0")


def test_partition_three_way_caches_separate_distributions() -> None:
    """common/preferred/reit 3 모집단이 각자 (security_type, field) 캐시 키로 분리."""
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="preferred"),
        _stock(code="000003", security_type="reit"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
    ]
    repo = _CountingStocksRepo(stocks)
    prov = DbUniverseDistributionProvider(
        as_of=_AS_OF,
        stocks_repo=repo,
        pack=None,  # type: ignore[arg-type]
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository([]),
        financial_repo=_EMPTY_FIN,
        market_cap_repo=FakeMarketCapRepository(mcs),
    )
    for code, st in (("000001", "common"), ("000002", "preferred"),
                     ("000003", "reit")):
        prov.bind_target_security_type(code)
        prov.percentile(_FIELD, Decimal("10"))
        assert (st, _FIELD) in prov._distribution_cache
    # 3 자산군 분포 각각 1 개씩 캐시.
    assert len(prov._distribution_cache) == 3
    # list_active 는 partition map 1 회만 (전 bind/percentile 공유).
    assert repo.list_active_calls == 1


def test_reit_small_sample_disclosure_triggered() -> None:
    """리츠 모집단은 작아 sample_size < SMALL_SAMPLE_THRESHOLD → 소표본 디스클로저.

    리츠 5 종목 (실제 KOSPI 리츠도 수십 개 수준) → partition 후 n=5 < 30 →
    표시층 small_sample 트리거. 값(percentile)은 보존 — 숨김 0 (ADR-0024 D2).
    """
    stocks = [
        # 보통주 다수 (충분한 모집단) — 리츠와 대비.
        *[_stock(code=f"00010{i}", security_type="common") for i in range(5)],
        # 리츠 소수.
        *[_stock(code=f"00020{i}", security_type="reit") for i in range(5)],
    ]
    mcs = [
        *[_mc(code=f"00010{i}", market_cap=str((i + 1) * 10)) for i in range(5)],
        *[_mc(code=f"00020{i}", market_cap=str((i + 1) * 10)) for i in range(5)],
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    # 리츠 bind → 리츠 모집단 n=5.
    prov.bind_target_security_type("000200")
    assert prov.sample_size(_FIELD) == 5
    assert prov.sample_size(_FIELD) < SMALL_SAMPLE_THRESHOLD  # 소표본 트리거.
    # 소표본이어도 값은 보존 — 최소값 10 → percentile = 1/5 = 20% (N/A 아님).
    assert prov.percentile(_FIELD, Decimal("10")) == Decimal("20")


def test_partition_default_common_unaffected_by_reit() -> None:
    """리츠 합류 후에도 bind 미호출 시 default = common 모집단 (기본 동작 불변).

    보통주 [10, 20, 30] + 리츠 [1000] 혼재. bind 안 하면 보통주 모집단 산출 —
    리츠 1000 미산입 → 30 이하 3 개 / 3 = 100%.
    """
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="common"),
        _stock(code="000003", security_type="common"),
        _stock(code="000004", security_type="reit"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
        _mc(code="000004", market_cap="1000"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    # bind 미호출 — default common (리츠 1000 미산입).
    assert prov.percentile(_FIELD, Decimal("30")) == Decimal("100")


# =============================================================================
# 13. M2 T77 ETF — ADR-0023 D3-a/D5 시총 partition ("구멍은 N/A 아닌 field")
# =============================================================================
#
# ETF 는 재무 factor 가 자연 N/A 라 PER 분포에서 자동 제외(ADR-0022 D3-3)되지만,
# **시총·가격 field 는 N/A 가 아니라 값이 있어** 보통주 모집단에 섞이면 §2.7 의미
# 가 붕괴된다("이 보통주 시총이 ETF 포함 전체에서 몇 %"는 무의미). D5 의 핵심
# 문장 "구멍은 N/A 가 아닌 field" 의 정확한 ETF 케이스 — market_cap percentile 이
# ETF 와 보통주를 partition 으로 분리함을 회귀로 박는다.

def test_partition_etf_marketcap_excludes_common() -> None:
    """ETF 종목의 market_cap percentile 은 ETF 모집단 기준 — 보통주 혼합 X (D5).

    보통주 [10, 20, 30] + ETF [40, 50]. ETF 종목(값 40)의 percentile 은 ETF
    모집단 (40, 50) 기준 = 1/2 = 50%. 전체 5 종목 혼합이면 40 이하 4 개 / 5 = 80%
    가 되어 ETF 시총이 보통주 분포를 오염시킨다 — partition 으로 차단(50%).
    시총은 ETF 도 값이 있으므로(재무 N/A 와 달리) 이 분리가 필수다.
    """
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="common"),
        _stock(code="000003", security_type="common"),
        _stock(code="000004", security_type="etf"),
        _stock(code="000005", security_type="etf"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
        _mc(code="000004", market_cap="40"),
        _mc(code="000005", market_cap="50"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    # ETF 종목(000004) 평가 — partition 컨텍스트 바인딩.
    resolved = prov.bind_target_security_type("000004")
    assert resolved == "etf"
    # ETF 모집단 (40, 50) 기준 — 40 이하 1 개 / 2 = 50% (보통주 시총 미혼입).
    assert prov.percentile(_FIELD, Decimal("40")) == Decimal("50")
    # ETF 모집단 min=40, max=50 → min_max_scale(40)=0.
    assert prov.min_max_scale(_FIELD, Decimal("40")) == Decimal("0")
    # ETF 모집단 sample_size=2 (보통주 3 과 분리).
    assert prov.sample_size(_FIELD) == 2


def test_partition_etf_does_not_pollute_common_marketcap() -> None:
    """보통주 종목의 market_cap percentile 은 ETF 거대 시총에 오염되지 않음 (D5).

    보통주 [10, 20, 30] + ETF [9999](거대 ETF 시총). 보통주 종목(30) 평가 시
    보통주 모집단 (10, 20, 30) 기준 100% — ETF 9999 가 섞이면 75% 로 깎였을 것.
    """
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="common"),
        _stock(code="000003", security_type="common"),
        _stock(code="000004", security_type="etf"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
        _mc(code="000004", market_cap="9999"),  # 거대 ETF — 혼입 시 보통주 분포 왜곡.
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    prov.bind_target_security_type("000003")  # 보통주.
    # 보통주 모집단 (10, 20, 30) 기준 — 30 이하 3 개 / 3 = 100% (ETF 9999 미산입).
    assert prov.percentile(_FIELD, Decimal("30")) == Decimal("100")


def test_partition_four_way_all_security_types_separated() -> None:
    """common/preferred/etf/reit 4 자산군이 각자 (security_type, field) 캐시로 분리.

    universe 확장 트랙(T77~79) 완결 — 4 자산군 모두 독립 모집단.
    """
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="preferred"),
        _stock(code="000003", security_type="etf"),
        _stock(code="000004", security_type="reit"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="30"),
        _mc(code="000004", market_cap="40"),
    ]
    repo = _CountingStocksRepo(stocks)
    prov = DbUniverseDistributionProvider(
        as_of=_AS_OF,
        stocks_repo=repo,
        pack=None,  # type: ignore[arg-type]
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository([]),
        financial_repo=_EMPTY_FIN,
        market_cap_repo=FakeMarketCapRepository(mcs),
    )
    for code, st in (("000001", "common"), ("000002", "preferred"),
                     ("000003", "etf"), ("000004", "reit")):
        prov.bind_target_security_type(code)
        prov.percentile(_FIELD, Decimal("10"))
        assert (st, _FIELD) in prov._distribution_cache
    # 4 자산군 분포 각각 1 개씩 캐시 — universe 확장 트랙 완결.
    assert len(prov._distribution_cache) == 4
    assert repo.list_active_calls == 1


def test_partition_default_common_unaffected_by_etf() -> None:
    """ETF 합류 후에도 bind 미호출 시 default = common 모집단 (기본 동작 불변)."""
    stocks = [
        _stock(code="000001", security_type="common"),
        _stock(code="000002", security_type="common"),
        _stock(code="000003", security_type="etf"),
    ]
    mcs = [
        _mc(code="000001", market_cap="10"),
        _mc(code="000002", market_cap="20"),
        _mc(code="000003", market_cap="9999"),
    ]
    prov = _provider(stocks=stocks, market_caps=mcs)
    # bind 미호출 — default common (ETF 9999 미산입). 20 이하 2 개 / 2 = 100%.
    assert prov.percentile(_FIELD, Decimal("20")) == Decimal("100")
