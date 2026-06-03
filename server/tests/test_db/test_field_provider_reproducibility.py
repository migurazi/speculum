"""T52 — DbFieldProvider 재현성·정확성 AC (factor 값 byte-동일).

m1-milestone.md T52 / AC-M1-P-01: "factor 값이 동일 (as_of + batch_id) 에서
byte-동일". test_reproduction.py 는 repository / screen_run (result_hash) 레벨의
재현을 검증하나, 본 모듈은 **DbFieldProvider get_scalar / FactorEvaluator
evaluate 레벨** 의 factor 값 byte-동일을 직접 검증한다 (AC-M1-P-01 직접 대응).

"byte-동일" 의 의미 — 단순 `==` (Decimal 의 수치 동일, 100 == 100.00) 가 아니라
`Decimal.as_tuple()` (sign, digits, exponent 까지) 동일. AC-M1-P-01 의 "factor 값
byte-동일" 은 Stock Detail 표시값 (예: PER 12.34) 의 재현성을 요구한다 — 재현마다
12.34 / 12.340 으로 scale 이 흔들리면 표시 / 직렬화가 비결정적. 따라서 수치 동일을
넘어 scale (exponent) 까지 결정적이어야 한다 (PER/PBR 나눗셈의 quantize scale
재현성 포함). screen_run 의 result_hash (result_codes 기반) 와는 별개 층위의
재현성으로, factor 값을 비교 연산 (>, <) 의 입력으로 쓰는 screen 의 결정성도 이
값 결정성에 의존한다.

검증 매트릭스:
1. determinism — 동일 데이터 + 동일 batch_cutoff (None) 로 두 독립 provider →
   EPS / PER / PBR / trading_value 의 값이 as_tuple() 동일 (Decimal 연산 결정성).
2. frozen cutoff 가 factor 값을 결정 — 정정 (batch2) 을 frozen=batch1 cutoff 로
   제외 → EPS factor 원값 byte-동일 복원. cutoff=None (라이브) 은 정정값. 두 값이
   다르며, 그 차이는 batch_id (data_versions) 에 명시 귀속 (AC-M1-P-02 factor 레벨).
3. frozen cutoff 재현도 결정적 — frozen cutoff 로도 두 독립 provider 가 byte-동일.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

from app.repositories.batch_run_repository import BatchCutoff, CitationBatch
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeFinancialRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    FinancialRecord,
    MarketCapRecord,
    PriceRecord,
    TreasurySharesRecord,
)
from app.services.db_field_provider import DbFieldProvider
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK
from app.services.krx_calendar import DEFAULT_CALENDAR

_CODE: Final[str] = "005930"
_LINEAGE: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000aa")
_NET_INCOME_ACCOUNT: Final[str] = "net_income_attributable_to_owners"
_EQUITY_ACCOUNT: Final[str] = "equity_attributable_to_owners"
_EPS_ACCOUNT: Final[str] = "basic_eps"

# DART 정정 시나리오의 두 batch — batch1 = 원 공시, batch2 = 정정 (frozen 이후).
_DART_BATCH1: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000b1")
_DART_BATCH2: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000b2")
_CIT1: Final[UUID] = UUID("00000000-0000-0000-0000-0000000001c1")
_CIT2: Final[UUID] = UUID("00000000-0000-0000-0000-0000000001c2")
# 단일 batch (정정 없음) 시나리오의 citation.
_CIT_BASE: Final[UUID] = UUID("00000000-0000-0000-0000-0000000001c0")

_CITATION_RUNS: Final[dict[UUID, CitationBatch]] = {
    _CIT_BASE: CitationBatch(
        started_at=datetime(2024, 4, 1, 9, tzinfo=UTC),
        batch_id=_DART_BATCH1, source="DART",
    ),
    _CIT1: CitationBatch(
        started_at=datetime(2024, 4, 1, 9, tzinfo=UTC),
        batch_id=_DART_BATCH1, source="DART",
    ),
    _CIT2: CitationBatch(
        started_at=datetime(2024, 5, 5, 9, tzinfo=UTC),
        batch_id=_DART_BATCH2, source="DART",
    ),
}

# frozen=batch1 cutoff — 정정 (batch2) 제외, 원 공시만 통과.
_FROZEN_CUTOFF: Final[BatchCutoff] = BatchCutoff(
    started_at=datetime(2024, 4, 1, 9, tzinfo=UTC), id=_DART_BATCH1,
)


# =============================================================================
# Builders
# =============================================================================

def _fin(
    *,
    account: str,
    fiscal_period: str,
    value: str,
    effective_date: date,
    citation_id: UUID = _CIT_BASE,
    ifrs_type: str = "consolidated",
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
    created_at: datetime | None = None,
) -> FinancialRecord:
    return FinancialRecord(
        id=record_id or uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        account=account,
        value=Decimal(value),
        unit="krw",
        ifrs_type=ifrs_type,
        citation_id=citation_id,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


def _price(*, d: date, close: str, trading_value: str = "1000000") -> PriceRecord:
    return PriceRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=d,
        open_raw=Decimal(close),
        high_raw=Decimal(close),
        low_raw=Decimal(close),
        close_raw=Decimal(close),
        volume=1000,
        trading_value=Decimal(trading_value),
        close_adjusted=Decimal(close),
        citation_id=_CIT_BASE,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _mc(*, d: date, market_cap: str, shares_outstanding: int) -> MarketCapRecord:
    return MarketCapRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=d,
        market_cap=Decimal(market_cap),
        shares_outstanding=shares_outstanding,
        shares_treasury=None,
        citation_id=_CIT_BASE,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _treasury(
    *, fiscal_period: str, effective_date: date, shares_treasury: int,
) -> TreasurySharesRecord:
    return TreasurySharesRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=_LINEAGE,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        shares_treasury=shares_treasury,
        citation_id=_CIT_BASE,
        superseded_by=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _build_provider(
    *,
    as_of: date,
    financials: list[FinancialRecord],
    prices: list[PriceRecord],
    market_caps: list[MarketCapRecord],
    treasury: list[TreasurySharesRecord],
    evaluator: FactorEvaluator,
    dart_batch_cutoff: BatchCutoff | None = None,
    krx_batch_cutoff: BatchCutoff | None = None,
) -> DbFieldProvider:
    """재현성 검증용 provider — Fake repo (citation_runs 주입) + pack/evaluator.

    배치 cutoff 를 지원하기 위해 모든 Fake repo 에 `_CITATION_RUNS` 를 주입한다
    (cutoff 가 None 이면 무관, 정정 시나리오에서만 의미). 매 호출이 새 repo /
    provider 인스턴스를 생성하므로, 두 번 호출하면 완전히 독립된 평가 경로가
    되어 byte-동일성이 캐시 공유가 아닌 연산 결정성에서 비롯됨을 보장한다.
    """
    return DbFieldProvider(
        code=_CODE,
        as_of=as_of,
        price_repo=FakePriceRepository(
            records=prices, citation_runs=_CITATION_RUNS,
        ),
        financial_repo=FakeFinancialRepository(
            records=financials, citation_runs=_CITATION_RUNS,
        ),
        corporate_action_repo=FakeCorporateActionRepository(records=[]),
        market_cap_repo=FakeMarketCapRepository(
            records=market_caps, citation_runs=_CITATION_RUNS,
        ),
        treasury_repo=FakeTreasurySharesRepository(
            records=treasury, citation_runs=_CITATION_RUNS,
        ),
        factor_pack=DEFAULT_PACK,
        evaluator=evaluator,
        dart_batch_cutoff=dart_batch_cutoff,
        krx_batch_cutoff=krx_batch_cutoff,
    )


def _factor(canonical_id: str) -> dict:
    return next(
        f for f in DEFAULT_PACK.body["factors"]
        if f["canonical_id"] == canonical_id
    )


def _last_n_business_days(as_of: date, n: int) -> list[date]:
    """as_of 이하 최근 n KRX 영업일 (오름차순) — resolver 와 동일 산출."""
    cursor = DEFAULT_CALENDAR.latest_business_day(as_of)
    days = [cursor]
    for _ in range(n - 1):
        cursor = DEFAULT_CALENDAR.previous_business_day(cursor)
        days.append(cursor)
    return sorted(days)


# 정정 없는 full 데이터 (PER/PBR/EPS 산출 가능) — 4 분기 net_income / equity /
# 시총 / 자사주 / 종가. 모든 record 가 batch1 (frozen 이전).
_FULL_AS_OF: Final[date] = date(2024, 5, 2)


def _full_financials() -> list[FinancialRecord]:
    eff = [date(2023, 5, 15), date(2023, 8, 14),
           date(2023, 11, 14), date(2024, 3, 30)]
    out: list[FinancialRecord] = []
    for fp, e in zip(
        ("2023Q1", "2023Q2", "2023Q3", "2023Q4"), eff, strict=True,
    ):
        out.append(_fin(account=_NET_INCOME_ACCOUNT, fiscal_period=fp,
                        value="1000000000", effective_date=e))
        out.append(_fin(account=_EPS_ACCOUNT, fiscal_period=fp,
                        value="700", effective_date=e))
    out.append(_fin(account=_EQUITY_ACCOUNT, fiscal_period="2023Q4",
                    value="30000000000", effective_date=date(2024, 3, 30)))
    return out


# =============================================================================
# 1. determinism — 동일 입력 + 동일 cutoff → 모든 factor 값 byte-동일
# =============================================================================

def test_factor_values_byte_identical_across_independent_providers() -> None:
    """동일 데이터 + 동일 cutoff 로 두 독립 provider → factor 값 as_tuple() 동일.

    EPS (sum), PER / PBR (나눗셈 quantize), market-cap:ex-treasury (곱),
    trading_value (나눗셈) 각각에 대해 `Decimal.as_tuple()` 까지 동일 — 수치
    동일을 넘어 scale 동일 (result_hash byte-동일의 전제).
    """
    shares = 1_000_000
    treasury_shares = 100_000
    close = "50000"
    days = _last_n_business_days(_FULL_AS_OF, 20)

    def _make() -> DbFieldProvider:
        prices = [
            _price(d=d, close=close, trading_value=str((i + 1) * 100))
            for i, d in enumerate(days)
        ]
        return _build_provider(
            as_of=_FULL_AS_OF,
            financials=_full_financials(),
            prices=prices,
            market_caps=[_mc(d=_FULL_AS_OF, market_cap="410000000000000",
                             shares_outstanding=shares)],
            treasury=[_treasury(fiscal_period="2023Q4",
                                effective_date=date(2024, 3, 30),
                                shares_treasury=treasury_shares)],
            evaluator=FactorEvaluator(),
        )

    provider_a = _make()
    provider_b = _make()
    evaluator = FactorEvaluator()

    for canonical_id in (
        "eps:basic-ttm-consolidated-ifrs",
        "per:ttm-consolidated-ifrs",
        "pbr:consolidated-ifrs",
        "market-cap:ex-treasury",
    ):
        factor = _factor(canonical_id)
        value_a = evaluator.evaluate(factor, provider_a, as_of=_FULL_AS_OF).value
        value_b = evaluator.evaluate(factor, provider_b, as_of=_FULL_AS_OF).value
        assert value_a is not None, f"{canonical_id} 예상 실값"
        assert value_b is not None
        # byte-동일 — 수치뿐 아니라 scale (exponent) 까지.
        assert value_a.as_tuple() == value_b.as_tuple(), (
            f"{canonical_id} 값 byte-불일치: {value_a!r} != {value_b!r}"
        )

    # trading_value_20d_avg 도 byte-동일 (나눗셈 scale 재현).
    tv_a = provider_a.get_scalar("trading_value_20d_avg")
    tv_b = provider_b.get_scalar("trading_value_20d_avg")
    assert tv_a is not None and tv_b is not None
    assert tv_a.as_tuple() == tv_b.as_tuple()


# =============================================================================
# 2. frozen cutoff 가 factor 값을 결정 — 정정 제외 → 원값 byte-동일 복원
# =============================================================================

def _supersede_scenario_financials() -> list[FinancialRecord]:
    """4 분기 EPS — 2023Q4 가 batch1 원 공시 (800) 에서 batch2 정정 (100) 으로
    supersede. frozen=batch1 이면 원값 (TTM 합 = 500+600+700+800 = 2600),
    라이브 (cutoff 없음) 면 정정값 (500+600+700+100 = 1900).
    """
    periods = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    values = ["500", "600", "700", "800"]
    eff = [date(2023, 5, 15), date(2023, 8, 14),
           date(2023, 11, 14), date(2024, 3, 30)]
    q4_orig_id = UUID("00000000-0000-0000-0000-0000000000e4")
    correction_id = UUID("00000000-0000-0000-0000-0000000000f4")

    records: list[FinancialRecord] = []
    for fp, val, e in zip(periods, values, eff, strict=True):
        is_q4 = fp == "2023Q4"
        records.append(_fin(
            account=_EPS_ACCOUNT, fiscal_period=fp, value=val,
            effective_date=e, citation_id=_CIT1,
            record_id=q4_orig_id if is_q4 else None,
            superseded_by=correction_id if is_q4 else None,
        ))
    # batch2 정정 — 2023Q4 를 100 으로 하향 (원 row supersede).
    records.append(_fin(
        account=_EPS_ACCOUNT, fiscal_period="2023Q4", value="100",
        effective_date=date(2024, 3, 30), citation_id=_CIT2,
        record_id=correction_id,
        created_at=datetime(2024, 5, 5, tzinfo=UTC),
    ))
    return records


_EPS_FACTOR_ID: Final[str] = "eps:basic-ttm-consolidated-ifrs"
_SUPERSEDE_AS_OF: Final[date] = date(2024, 5, 7)


def test_frozen_cutoff_reproduces_original_factor_value() -> None:
    """frozen=batch1 cutoff → 정정 (batch2) 제외 → EPS 원값 byte-동일 복원.

    라이브 (cutoff=None) 는 정정값. 두 값이 다르며, 그 차이는 batch_id (frozen
    DART batch) 에 명시 귀속 — factor 레벨의 AC-M1-P-02.
    """
    evaluator = FactorEvaluator()
    factor = _factor(_EPS_FACTOR_ID)
    common = dict(
        as_of=_SUPERSEDE_AS_OF,
        financials=_supersede_scenario_financials(),
        prices=[],
        market_caps=[],
        treasury=[],
        evaluator=evaluator,
    )

    # 재현 (frozen=batch1) — 원 EPS TTM 합 = 500+600+700+800 = 2600.
    frozen_provider = _build_provider(**common, dart_batch_cutoff=_FROZEN_CUTOFF)
    frozen_value = evaluator.evaluate(
        factor, frozen_provider, as_of=_SUPERSEDE_AS_OF,
    ).value
    assert frozen_value == Decimal("2600")

    # 라이브 (cutoff 없음) — 정정 EPS TTM 합 = 500+600+700+100 = 1900.
    live_provider = _build_provider(**common, dart_batch_cutoff=None)
    live_value = evaluator.evaluate(
        factor, live_provider, as_of=_SUPERSEDE_AS_OF,
    ).value
    assert live_value == Decimal("1900")

    # batch_id 가 factor 값을 결정 — 재현 ≠ 라이브.
    assert frozen_value != live_value


def test_frozen_cutoff_factor_value_byte_identical_across_instances() -> None:
    """frozen cutoff 재현도 결정적 — 두 독립 provider 가 byte-동일 (as_tuple).

    재현 (batch_cutoff 적용) 경로 자체가 결정적이어야 저장된 run 의 result_hash
    가 재실행마다 byte-동일 (AC-M1-P-02). frozen cutoff 로 두 독립 provider 를
    만들어 EPS 값의 as_tuple() 동일을 검증.
    """
    evaluator = FactorEvaluator()
    factor = _factor(_EPS_FACTOR_ID)

    def _frozen_value() -> Decimal | None:
        provider = _build_provider(
            as_of=_SUPERSEDE_AS_OF,
            financials=_supersede_scenario_financials(),
            prices=[], market_caps=[], treasury=[],
            evaluator=FactorEvaluator(),
            dart_batch_cutoff=_FROZEN_CUTOFF,
        )
        return evaluator.evaluate(
            factor, provider, as_of=_SUPERSEDE_AS_OF,
        ).value

    value_a = _frozen_value()
    value_b = _frozen_value()
    assert value_a is not None and value_b is not None
    assert value_a.as_tuple() == value_b.as_tuple()
