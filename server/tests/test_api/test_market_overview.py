"""GET /api/market-overview (M1 T60) — 유니버스 집계 통계.

두 layer:
1. **pure helper 단위 테스트** — `_percentile` / `aggregate_values` 의 분위수
   보간·평균·min/max·빈 입력 처리를 Decimal 로 직접 검증.
2. **end-to-end** — fully-wired create_app 에 fact-backed FinancialRepository +
   다중 시장 종목을 주입하고 GET /api/market-overview 로 집계 결과 검증.

검증 핵심:
- 집계만 노출 (종목 식별자·랭킹·추천 0 — R8, 응답 스키마가 구조적으로 보장).
- N/A 종목은 값으로 오인되지 않고 na_count 로만 집계 (Fidelity).
- factor 순서·market_breakdown 정렬이 결정적.

EPS factor (`eps:basic-ttm-consolidated-ifrs`) 는 단일 field 의 4 분기 strict 합
으로 가격/시가총액 fetch 없이 결정적 산출 → 집계 검증에 이상적. PER/PBR/ROE/
market-cap 은 가격·자본 입력 부재로 전 종목 N/A → na_count 검증.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes.market import _percentile, aggregate_values
from app.main import create_app
from app.repositories.fakes import FakeFinancialRepository, FakeMacroIndicatorRepository
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    FinancialRecord,
    MacroIndicatorRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository

_EPS_ACCOUNT = "basic_eps"
_EPS_FACTOR_ID = "eps:basic-ttm-consolidated-ifrs"
_CONSOLIDATED = "consolidated"
_CITATION = UUID("00000000-0000-0000-0000-0000000000ff")
_AS_OF = date(2024, 5, 7)


# =============================================================================
# Pure helper 단위 테스트 — _percentile / aggregate_values
# =============================================================================

def test_percentile_linear_interpolation_three_points() -> None:
    """3 점 [4000,8000,12000] — numpy type-7 linear interpolation."""
    vals = [Decimal("4000"), Decimal("8000"), Decimal("12000")]
    assert _percentile(vals, Decimal("0.5")) == Decimal("8000")   # median
    assert _percentile(vals, Decimal("0.25")) == Decimal("6000")  # p25 보간
    assert _percentile(vals, Decimal("0.75")) == Decimal("10000")  # p75 보간


def test_percentile_single_value() -> None:
    """n==1 → 그 값 (보간 불필요)."""
    assert _percentile([Decimal("42")], Decimal("0.25")) == Decimal("42")
    assert _percentile([Decimal("42")], Decimal("0.75")) == Decimal("42")


def test_percentile_even_count_median() -> None:
    """짝수 개 [10,20,30,40] — median = (n-1)*0.5=1.5 → 20+(30-20)*0.5=25."""
    vals = [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")]
    assert _percentile(vals, Decimal("0.5")) == Decimal("25")


def test_aggregate_values_empty_all_none() -> None:
    """관측값 0 (전 종목 N/A) → 통계 전부 None, na_count 보존."""
    out = aggregate_values([], na_count=7)
    assert out["count"] == 0
    assert out["na_count"] == 7
    for key in ("mean", "median", "p25", "p75", "min", "max"):
        assert out[key] is None


def test_aggregate_values_basic_stats() -> None:
    """[4000,8000,12000] — 평균/중앙값/사분위/최소·최대 검증 (정렬 무관)."""
    # 입력 순서를 섞어 정렬 의존성 검증.
    out = aggregate_values(
        [Decimal("12000"), Decimal("4000"), Decimal("8000")], na_count=2,
    )
    assert out["count"] == 3
    assert out["na_count"] == 2
    assert Decimal(out["mean"]) == Decimal("8000")    # type: ignore[arg-type]
    assert Decimal(out["median"]) == Decimal("8000")  # type: ignore[arg-type]
    assert Decimal(out["p25"]) == Decimal("6000")     # type: ignore[arg-type]
    assert Decimal(out["p75"]) == Decimal("10000")    # type: ignore[arg-type]
    assert Decimal(out["min"]) == Decimal("4000")     # type: ignore[arg-type]
    assert Decimal(out["max"]) == Decimal("12000")    # type: ignore[arg-type]


def test_aggregate_values_mean_non_integer() -> None:
    """평균이 정수가 아닌 경우 — 6 자리 quantize 표시."""
    out = aggregate_values([Decimal("10"), Decimal("11")], na_count=0)
    assert Decimal(out["mean"]) == Decimal("10.5")  # type: ignore[arg-type]


# =============================================================================
# End-to-end — fully-wired create_app + 다중 시장 + EPS 재무
# =============================================================================

def _eps_quarters(code: str, per_quarter: str) -> list[FinancialRecord]:
    """code 의 4 분기 basic_eps consolidated — TTM 합 = per_quarter*4.

    effective_date 는 as_of(2024-05-07) 이전 고정 일자 (분기 신고기한 보수값보다
    충분히 이전).

    **B1 (ROADMAP_v2 V1b)**: basic_eps 는 FLOW 계정 — DART 누적(YTD)으로 seed.
    분기단독(standalone)이 per_quarter 가 되도록 누적 [pq,2pq,3pq,4pq] seed →
    `_resolve_financial_series` 가 standalone [pq×4] 로 복원, TTM 합 = per_quarter*4.
    """
    periods = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    eff = [date(2023, 5, 15), date(2023, 8, 14), date(2023, 11, 14),
           date(2024, 3, 30)]
    out: list[FinancialRecord] = []
    pq = Decimal(per_quarter)
    for idx, (fp, e) in enumerate(zip(periods, eff, strict=True), start=1):
        out.append(FinancialRecord(
            id=uuid4(),
            code=code,
            code_lineage_id=UUID(int=hash(code) & ((1 << 128) - 1)),
            effective_date=e,
            fiscal_period=fp,
            account=_EPS_ACCOUNT,
            value=pq * idx,  # 누적(YTD): Q1=pq, Q2=2pq, Q3=3pq, Q4=4pq.
            unit="krw",
            ifrs_type=_CONSOLIDATED,
            citation_id=_CITATION,
            superseded_by=None,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        ))
    return out


def _stock(code: str, name: str, market: str) -> StockMasterRecord:
    return StockMasterRecord(
        id=UUID(int=int(code)),
        current_code=code, current_name=name, market=market,
        listing_date=date(2000, 1, 1), delisting_date=None, fiscal_month=12,
        code_history=(CodeHistoryEntry(code, date(2000, 1, 1), None,
                                       "initial_listing"),),
    )


def _financials(records: Sequence[FinancialRecord]) -> FakeFinancialRepository:
    return FakeFinancialRepository(records=list(records))


@pytest.fixture
def e2e_client() -> Iterator[TestClient]:
    """3 종목 — KOSPI 2 (EPS=4000, 12000), KOSDAQ 1 (EPS=8000). 매크로 없음."""
    stocks = FakeStocksMasterRepository(records=[
        _stock("005930", "삼성전자", "KOSPI"),
        _stock("000660", "SK하이닉스", "KOSPI"),
        _stock("035720", "카카오", "KOSDAQ"),
    ])
    financials = _financials(
        _eps_quarters("005930", "1000")    # TTM 4000
        + _eps_quarters("000660", "3000")  # TTM 12000
        + _eps_quarters("035720", "2000"),  # TTM 8000
    )
    # 매크로 빈 Fake — 기존 테스트는 macro_indicators 미검증 (빈 tuple 기대).
    macro = FakeMacroIndicatorRepository(records=[])
    app = create_app(
        stocks_repository=stocks,
        financial_repository=financials,
        macro_indicator_repository=macro,
    )
    with TestClient(app) as c:
        yield c


def _eps_factor(body: dict) -> dict:
    """응답 factors 에서 EPS factor 집계 dict 추출."""
    return next(f for f in body["factors"] if f["canonical_id"] == _EPS_FACTOR_ID)


def test_e2e_universe_count_and_market_breakdown(e2e_client: TestClient) -> None:
    """universe_count = 3, market_breakdown 시장명 오름차순 (KOSDAQ→KOSPI)."""
    res = e2e_client.get("/api/market-overview?as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    assert body["as_of"] == "2024-05-07"
    assert body["universe_count"] == 3
    assert body["market_breakdown"] == [
        {"market": "KOSDAQ", "count": 1},
        {"market": "KOSPI", "count": 2},
    ]


def test_e2e_eps_distribution(e2e_client: TestClient) -> None:
    """EPS 집계 — [4000,8000,12000]: count=3, mean=median=8000, p25=6000, p75=10000."""
    res = e2e_client.get("/api/market-overview?as_of=2024-05-07")
    eps = _eps_factor(res.json())
    assert eps["count"] == 3
    assert eps["na_count"] == 0
    assert Decimal(eps["mean"]) == Decimal("8000")
    assert Decimal(eps["median"]) == Decimal("8000")
    assert Decimal(eps["p25"]) == Decimal("6000")
    assert Decimal(eps["p75"]) == Decimal("10000")
    assert Decimal(eps["min"]) == Decimal("4000")
    assert Decimal(eps["max"]) == Decimal("12000")


def test_e2e_na_factors_have_zero_count(e2e_client: TestClient) -> None:
    """가격/자본 입력 부재 factor (PER/PBR/ROE/market-cap) → count=0, na_count=3.

    값 없는 종목이 분포에 0/임의값으로 섞이지 않음 (Fidelity §2.1).
    """
    res = e2e_client.get("/api/market-overview?as_of=2024-05-07")
    body = res.json()
    for f in body["factors"]:
        if f["canonical_id"] == _EPS_FACTOR_ID:
            continue
        assert f["count"] == 0, f"{f['canonical_id']} expected no values"
        assert f["na_count"] == 3
        assert f["mean"] is None
        assert f["min"] is None


def test_e2e_no_per_stock_data_leaked(e2e_client: TestClient) -> None:
    """응답에 종목 식별자·랭킹 키 0 (R8 — 집계만). 구조적 보장 회귀 가드."""
    res = e2e_client.get("/api/market-overview?as_of=2024-05-07")
    raw = res.text
    # 개별 종목코드가 응답에 노출되면 안 됨 (집계만).
    for code in ("005930", "000660", "035720"):
        assert code not in raw
    # 랭킹/추천성 키 부재. macro_indicators 는 값+기준일+단위만 (해석 0).
    body = res.json()
    assert set(body.keys()) == {
        "as_of", "universe_count", "market_breakdown", "factors", "macro_indicators",
    }


def test_e2e_deterministic_repeat(e2e_client: TestClient) -> None:
    """동일 as_of 반복 호출 → byte-동일 응답 (결정적 집계)."""
    res1 = e2e_client.get("/api/market-overview?as_of=2024-05-07")
    res2 = e2e_client.get("/api/market-overview?as_of=2024-05-07")
    assert res1.text == res2.text


def test_e2e_empty_universe_when_not_yet_listed() -> None:
    """active 종목 0 (전부 미래 상장) → universe_count=0, factor 통계 전부 None.

    as_of(2024-05-07) 기준 상장일이 미래(2025-01-01)인 종목만 존재 → list_active
    가 빈 결과. KRX 캘린더 커버리지(2024) 안의 as_of 를 사용해 정규화 회귀 회피.
    """
    future = StockMasterRecord(
        id=UUID(int=int("005930")),
        current_code="005930", current_name="미래상장", market="KOSPI",
        listing_date=date(2025, 1, 1), delisting_date=None, fiscal_month=12,
        code_history=(CodeHistoryEntry("005930", date(2025, 1, 1), None,
                                       "initial_listing"),),
    )
    stocks = FakeStocksMasterRepository(records=[future])
    app = create_app(
        stocks_repository=stocks,
        financial_repository=_financials([]),
        macro_indicator_repository=FakeMacroIndicatorRepository(records=[]),
    )
    with TestClient(app) as c:
        res = c.get("/api/market-overview?as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    assert body["universe_count"] == 0
    assert body["market_breakdown"] == []
    for f in body["factors"]:
        assert f["count"] == 0
        assert f["mean"] is None


# =============================================================================
# 매크로 지표 — FakeMacroIndicatorRepository 주입 테스트 (T64 Phase 2)
# =============================================================================

_MACRO_CITATION = UUID("00000000-0000-0000-0000-ec0500000001")
_MACRO_AS_OF = date(2024, 5, 7)


def _macro_record(
    indicator_id: str,
    reference_date: date,
    value: str,
    unit: str,
    vintage_date: date,
) -> MacroIndicatorRecord:
    """테스트용 MacroIndicatorRecord 생성 헬퍼."""
    return MacroIndicatorRecord(
        id=uuid4(),
        indicator_id=indicator_id,
        reference_date=reference_date,
        value=Decimal(value),
        unit=unit,
        vintage_date=vintage_date,
        citation_id=_MACRO_CITATION,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _macro_app(records: list[MacroIndicatorRecord]) -> TestClient:
    """매크로 지표 주입 앱 — 종목 없음 (집계 검증 외)."""
    stocks = FakeStocksMasterRepository(records=[])
    macro = FakeMacroIndicatorRepository(records=records)
    app = create_app(
        stocks_repository=stocks,
        financial_repository=_financials([]),
        macro_indicator_repository=macro,
    )
    return TestClient(app)


def test_macro_indicator_displayed_in_response() -> None:
    """매크로 indicator 가 응답 macro_indicators 에 포함 — 값/기준일/단위 확인.

    FakeMacroIndicatorRepository 에 기준금리 record 주입 → fetch_latest 가 해당 값
    반환 → MacroIndicatorOut 으로 직렬화 확인.
    vintage_date(2024-04-15) <= as_of(2024-05-07) — PIT 통과.
    """
    rec = _macro_record(
        "722Y001/0101000",
        date(2024, 4, 1),   # reference_date: 2024년 4월 기준금리
        "3.50",
        "percent",
        date(2024, 4, 15),  # vintage_date: as_of(2024-05-07) 이전 — PIT 통과
    )
    with _macro_app([rec]) as client:
        res = client.get(f"/api/market-overview?as_of={_MACRO_AS_OF}")
    assert res.status_code == 200
    body = res.json()
    macros = body["macro_indicators"]
    # 기준금리 indicator 가 포함돼야 함 (CPI 는 데이터 없음 → 제외).
    assert len(macros) == 1
    m = macros[0]
    assert m["indicator_id"] == "722Y001/0101000"
    assert m["name"] == "한국은행 기준금리"
    assert Decimal(m["value"]) == Decimal("3.50")
    assert m["unit"] == "percent"
    assert m["reference_date"] == "2024-04-01"
    assert m["vintage_date"] == "2024-04-15"


def test_macro_indicator_no_data_excluded() -> None:
    """데이터 없는 indicator 는 응답에서 제외 — 빈 항목 미표시.

    _MACRO_INDICATORS 에 등록된 indicator 가 2 개지만 FakeMacroIndicatorRepository
    에 records=[] (아무 데이터 없음) → macro_indicators 는 빈 tuple.
    """
    with _macro_app([]) as client:
        res = client.get(f"/api/market-overview?as_of={_MACRO_AS_OF}")
    assert res.status_code == 200
    assert res.json()["macro_indicators"] == []


def test_macro_indicator_pit_vintage_filter() -> None:
    """as_of PIT — vintage_date > as_of 인 record 는 제외.

    잠정(vintage 2024-04-01) + 확정(vintage 2024-06-01) 이 같은 reference_date 에
    존재. as_of=2024-05-07 → vintage_date <= as_of 조건에서 확정(2024-06-01) 제외 →
    잠정값(3.50) 반환.
    확정은 as_of > vintage 이므로 제외돼야 하고, 잠정값이 반환돼야 함.
    """
    prov = _macro_record(
        "722Y001/0101000", date(2024, 4, 1), "3.50", "percent",
        date(2024, 4, 15),  # 잠정 vintage — as_of(2024-05-07) 이전
    )
    final = _macro_record(
        "722Y001/0101000", date(2024, 4, 1), "3.25", "percent",
        date(2024, 6, 1),  # 확정 vintage — as_of(2024-05-07) 이후 → 제외
    )
    with _macro_app([prov, final]) as client:
        res = client.get(f"/api/market-overview?as_of={_MACRO_AS_OF}")
    assert res.status_code == 200
    macros = res.json()["macro_indicators"]
    assert len(macros) == 1
    # 잠정값 (확정이 미래라 as_of 기준에선 알 수 없음 — look-ahead 0).
    assert Decimal(macros[0]["value"]) == Decimal("3.50")
    assert macros[0]["vintage_date"] == "2024-04-15"


def test_macro_indicator_no_interpretation_text() -> None:
    """응답 macro_indicators 에 해석·판단·추천 텍스트 0 — 값/기준일/단위만 (ADR-0007 D5).

    MacroIndicatorOut 스키마가 구조적으로 보장: indicator_id / name / value /
    unit / reference_date / vintage_date 이외의 키 없음 (extra="forbid").
    """
    rec = _macro_record(
        "901Y009/0", date(2024, 4, 1), "113.56", "index",
        date(2024, 5, 2),
    )
    with _macro_app([rec]) as client:
        res = client.get(f"/api/market-overview?as_of={_MACRO_AS_OF}")
    assert res.status_code == 200
    macros = res.json()["macro_indicators"]
    assert len(macros) == 1
    m = macros[0]
    # 허용된 키만 — 해석/랭킹/추천 관련 키 없음.
    allowed_keys = {"indicator_id", "name", "value", "unit", "reference_date", "vintage_date"}
    assert set(m.keys()) == allowed_keys
    # 해석·추천성 텍스트 키 명시 부재 확인.
    for forbidden in ("signal", "rank", "advice", "recommendation", "interpretation"):
        assert forbidden not in m
