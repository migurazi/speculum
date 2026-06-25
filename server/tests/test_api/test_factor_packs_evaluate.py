"""POST /api/factor-packs/evaluate (M2 T74 Phase 1) — custom pack ad-hoc 평가.

Factor Lab editor 가 정의한 custom pack 을 저장 없이 즉시 평가(미리보기)하는
endpoint. 종목-국소 factor + 유니버스-상대 op(percentile, T72 분포 provider) 까지
실평가.

테스트 매트릭스:
1. 종목-국소 custom factor(4 분기 순이익 합) → 실값.
2. 유니버스-상대 op(percentile) 포함 custom factor → 분포 provider 로 실값
   (universe_distribution_required N/A 가 아닌 실 percentile).
3. invalid pack → valid:false + issues(평가 안 함, results 빈).
4. N/A 전파(결측 입력 종목).
5. 다중 code — 각 종목 독립 산출.
6. codes 상한 초과 방어(422).
7. No Advice 경계 — 응답에 랭킹/추천 키 0, 값/N-A 사실만.

net_income annual field(`net_income_attributable_consolidated_ifrs_annual`)는 단일
scalar(4 분기 strict 합)로 가격/시가총액 fetch 없이 결정적 산출 → 종목-국소 + 유니버스
-상대(scalar 분포) 평가 검증에 이상적(가격 입력 부재로 결정성 보장).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.fakes import FakeFinancialRepository, FakeMarketCapRepository
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    FinancialRecord,
    MarketCapRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository

_NI_ACCOUNT = "net_income_attributable_to_owners"
_NI_FIELD = "net_income_attributable_consolidated_ifrs_annual"
_CONSOLIDATED = "consolidated"
_CITATION = UUID("00000000-0000-0000-0000-0000000000ff")
_AS_OF = "2024-05-07"


# =============================================================================
# Fixtures / helpers
# =============================================================================

def _ni_quarters(code: str, per_quarter: str) -> list[FinancialRecord]:
    """code 의 4 분기 net_income consolidated — TTM 합 = per_quarter*4.

    effective_date 는 as_of(2024-05-07) 이전 고정 (PIT 통과). annual field 는
    4 분기 strict 합 scalar (db_field_provider financial_annual kind).

    **B1 (ROADMAP_v2 V1b)**: net_income 은 FLOW 계정이라 DART 는 회계연도 **누적
    (YTD)** 으로 보고. 분기단독(standalone)이 per_quarter 가 되도록 누적값
    [pq, 2*pq, 3*pq, 4*pq] 으로 seed → `_resolve_financial_series` 가 standalone
    [pq,pq,pq,pq] 로 복원, TTM 합 = per_quarter*4 (의도 유지).
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
            account=_NI_ACCOUNT,
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


def _mc(code: str, market_cap: str) -> MarketCapRecord:
    """단일 market_cap fact (effective_date <= as_of) — 시총 분포 모집단 입력.

    ADR-0024 다중 universe-상대 field 테스트 (min(n) 귀속) 에서 net_income 과 다른
    모집단 크기를 만들기 위한 보조 fact. effective_date 는 as_of(2024-05-07) 이전 고정.
    """
    return MarketCapRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=UUID(int=int(code)),
        effective_date=date(2024, 5, 1),
        market_cap=Decimal(market_cap),
        shares_outstanding=1_000_000,
        shares_treasury=None,
        citation_id=_CITATION,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


# pack 의 종목-국소 factor — 단일 scalar field(net_income annual = 4 분기 strict 합).
# db_field_provider 의 financial_annual resolver 가 해소.
def _ni_factor(canonical_id: str = "my:ni") -> dict[str, Any]:
    return {
        "canonical_id": canonical_id,
        "uuid": "20000000-0000-0000-0000-000000000001",
        "name": "내 순이익 TTM",
        "description": "최근 4 분기 지배주주순이익 합 (custom)",
        "formula": {
            "ast": {"field": _NI_FIELD},
            "inputs": [_NI_FIELD],
        },
        "unit": "krw",
    }


# 유니버스-상대 percentile factor — 같은 net_income field 의 모집단 분포 내 percentile.
def _ni_percentile_factor(
    canonical_id: str = "my:ni-pct",
) -> dict[str, Any]:
    return {
        "canonical_id": canonical_id,
        "uuid": "20000000-0000-0000-0000-000000000002",
        "name": "순이익 유니버스 percentile",
        "description": "순이익의 as_of 유니버스 분포 내 percentile (custom)",
        "formula": {
            "ast": {"op": "percentile", "field": _NI_FIELD},
            "inputs": [_NI_FIELD],
        },
        "unit": "ratio",
    }


def _pack(factors: list[dict[str, Any]]) -> dict[str, Any]:
    """schema/identity/acyclic/citation/forbidden_vocab 통과 custom pack."""
    return {
        "$schema": "https://speculum.dev/schemas/factor-pack-v1.json",
        "type": "factor-pack",
        "pack_slug": "user/testuser-evalpack",
        "version": "1.0.0",
        "publisher": "testuser",
        "license": "MIT",
        "created_at": "2026-01-01",
        "citation": {"title": "Test Eval Pack", "publisher": "testuser"},
        "factors": factors,
        "content_hash": "sha256:" + "a" * 64,
    }


@pytest.fixture
def client() -> Iterator[TestClient]:
    """3 종목 — 순이익 TTM: 005930=4000, 000660=12000, 035720=8000.

    유니버스(list_active) = 3 종목 → percentile 분포 모집단.
    """
    stocks = FakeStocksMasterRepository(records=[
        _stock("005930", "삼성전자", "KOSPI"),
        _stock("000660", "SK하이닉스", "KOSPI"),
        _stock("035720", "카카오", "KOSDAQ"),
    ])
    financials: Sequence[FinancialRecord] = (
        _ni_quarters("005930", "1000")    # TTM 4000
        + _ni_quarters("000660", "3000")  # TTM 12000
        + _ni_quarters("035720", "2000")  # TTM 8000
    )
    app = create_app(
        stocks_repository=stocks,
        financial_repository=FakeFinancialRepository(records=list(financials)),
    )
    with TestClient(app) as c:
        yield c


def _post(client: TestClient, body: Any) -> Any:
    return client.post("/api/factor-packs/evaluate", json=body)


def _factor_of(stock: dict, canonical_id: str) -> dict:
    return next(f for f in stock["factors"] if f["canonical_id"] == canonical_id)


# =============================================================================
# 1. 종목-국소 custom factor → 실값
# =============================================================================

def test_stock_local_factor_real_value(client: TestClient) -> None:
    """단일 종목 순이익 TTM — 4 분기 합 실값(4000)."""
    res = _post(client, {
        "pack": _pack([_ni_factor()]),
        "as_of": _AS_OF,
        "codes": ["005930"],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    assert body["issues"] == []
    assert len(body["results"]) == 1
    stock = body["results"][0]
    assert stock["code"] == "005930"
    ni = _factor_of(stock, "my:ni")
    assert ni["is_na"] is False
    assert Decimal(ni["value"]) == Decimal("4000")
    assert ni["unit"] == "krw"
    assert ni["na_reason"] is None


# =============================================================================
# 2. 유니버스-상대 op(percentile) → 분포 provider 로 실값
# =============================================================================

def test_universe_relative_percentile_real_value(client: TestClient) -> None:
    """percentile — 분포 provider 주입으로 실값(N/A 아님).

    유니버스 순이익 분포 = {4000, 8000, 12000}. percentile = count(<=value)/n*100.
    - 005930 (순이익 4000, 최소): count_le=1 → 1/3*100 = 33.33...
    - 000660 (순이익 12000, 최대): count_le=3 → 100.
    """
    res = _post(client, {
        "pack": _pack([_ni_percentile_factor()]),
        "as_of": _AS_OF,
        "codes": ["005930", "000660"],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True

    by_code = {s["code"]: s for s in body["results"]}
    pct_min = _factor_of(by_code["005930"], "my:ni-pct")
    pct_max = _factor_of(by_code["000660"], "my:ni-pct")

    # 실값 — universe_distribution_required N/A 가 아님.
    assert pct_min["is_na"] is False
    assert pct_min["na_reason"] is None
    assert pct_max["is_na"] is False
    # 최소값 percentile = 1/3*100, 최대값 = 100.
    assert Decimal(pct_min["value"]) == (Decimal(1) / Decimal(3)) * Decimal(100)
    assert Decimal(pct_max["value"]) == Decimal("100")


def test_universe_relative_not_na_reason(client: TestClient) -> None:
    """percentile 결과의 na_reason 이 universe_distribution_required 가 아님.

    분포 provider 가 주입됐으므로 T70b 의 미주입 N/A 사유가 발생하면 안 됨.
    """
    res = _post(client, {
        "pack": _pack([_ni_percentile_factor()]),
        "as_of": _AS_OF,
        "codes": ["035720"],
    })
    body = res.json()
    pct = _factor_of(body["results"][0], "my:ni-pct")
    assert pct["na_reason"] != "universe_distribution_required"
    # 중앙값 순이익(8000) percentile = 2/3*100.
    assert Decimal(pct["value"]) == (Decimal(2) / Decimal(3)) * Decimal(100)


# =============================================================================
# 3. invalid pack → valid:false + issues (평가 안 함)
# =============================================================================

def test_invalid_pack_returns_issues_no_eval(client: TestClient) -> None:
    """schema 위반(factors 누락) → valid:false + issues, results 빈."""
    bad_pack = _pack([_ni_factor()])
    del bad_pack["factors"]
    res = _post(client, {
        "pack": bad_pack,
        "as_of": _AS_OF,
        "codes": ["005930"],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is False
    assert body["results"] == []
    assert any(i["stage"] == "schema" for i in body["issues"])


def test_invalid_pack_identity_dup(client: TestClient) -> None:
    """canonical_id 중복(identity) → valid:false, 평가 안 함."""
    a = _ni_factor("my:ni")
    b = _ni_factor("my:ni")
    b["uuid"] = "20000000-0000-0000-0000-0000000000bb"
    res = _post(client, {
        "pack": _pack([a, b]),
        "as_of": _AS_OF,
        "codes": ["005930"],
    })
    body = res.json()
    assert body["valid"] is False
    assert body["results"] == []
    assert any(i["stage"] == "identity" for i in body["issues"])


# =============================================================================
# 4. N/A 전파 (결측 입력)
# =============================================================================

def test_na_propagation_missing_input(client: TestClient) -> None:
    """재무 결측 종목 → 순이익 factor N/A (값 None + na_reason)."""
    res = _post(client, {
        "pack": _pack([_ni_factor()]),
        "as_of": _AS_OF,
        "codes": ["999999"],  # 재무 데이터 없음
    })
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    ni = _factor_of(body["results"][0], "my:ni")
    assert ni["is_na"] is True
    assert ni["value"] is None
    assert ni["na_reason"] is not None


# =============================================================================
# 5. 다중 code — 각 종목 독립 산출
# =============================================================================

def test_multiple_codes_independent_values(client: TestClient) -> None:
    """3 종목 동시 평가 — 각 순이익 TTM 독립값, 요청 순서 보존."""
    res = _post(client, {
        "pack": _pack([_ni_factor()]),
        "as_of": _AS_OF,
        "codes": ["005930", "000660", "035720"],
    })
    assert res.status_code == 200
    body = res.json()
    assert [s["code"] for s in body["results"]] == ["005930", "000660", "035720"]
    expected = {"005930": "4000", "000660": "12000", "035720": "8000"}
    for stock in body["results"]:
        ni = _factor_of(stock, "my:ni")
        assert Decimal(ni["value"]) == Decimal(expected[stock["code"]])


# =============================================================================
# 6. codes 상한 초과 방어
# =============================================================================

def test_codes_limit_exceeded_returns_422(client: TestClient) -> None:
    """codes 21 개(> 20) → 422."""
    codes = [str(100000 + i) for i in range(21)]
    res = _post(client, {
        "pack": _pack([_ni_factor()]),
        "as_of": _AS_OF,
        "codes": codes,
    })
    assert res.status_code == 422


def test_empty_codes_returns_400(client: TestClient) -> None:
    """codes 빈 목록 → 400."""
    res = _post(client, {
        "pack": _pack([_ni_factor()]),
        "as_of": _AS_OF,
        "codes": [],
    })
    assert res.status_code == 400


def test_invalid_code_format_returns_400(client: TestClient) -> None:
    """비숫자 code → 400."""
    res = _post(client, {
        "pack": _pack([_ni_factor()]),
        "as_of": _AS_OF,
        "codes": ["abc"],
    })
    assert res.status_code == 400


def test_missing_as_of_returns_400(client: TestClient) -> None:
    """as_of 누락 → 400."""
    res = _post(client, {
        "pack": _pack([_ni_factor()]),
        "codes": ["005930"],
    })
    assert res.status_code == 400


# =============================================================================
# 7. No Advice 경계 — 값/N-A 사실만, 랭킹/추천 키 0
# =============================================================================

def test_no_advice_response_keys(client: TestClient) -> None:
    """응답 키는 valid/issues/results 만. factor 키는 사실 필드만 (랭킹/추천 0)."""
    res = _post(client, {
        "pack": _pack([_ni_factor(), _ni_percentile_factor()]),
        "as_of": _AS_OF,
        "codes": ["005930"],
    })
    body = res.json()
    assert set(body.keys()) == {"valid", "issues", "results"}
    stock = body["results"][0]
    assert set(stock.keys()) == {"code", "factors"}
    for f in stock["factors"]:
        # 값/N-A 사실 필드 + 소표본 디스클로저 (ADR-0024 D2 — 모집단 크기 사실).
        # 랭킹/정렬/점수 라벨 키 부재. sample_size/small_sample 은 사실 (모집단
        # 크기 + 소표본 표식) 이지 가치·지시어가 아님 (No Advice 경계 유지).
        assert set(f.keys()) == {
            "canonical_id", "name", "unit", "value", "is_na", "na_reason",
            "sample_size", "small_sample",
        }
        for forbidden in ("rank", "score", "advice", "recommendation", "signal"):
            assert forbidden not in f


# =============================================================================
# 8. ADR-0024 — 소표본 디스클로저 (sample_size + small_sample, 표시 전용)
# =============================================================================

def test_universe_relative_exposes_sample_size_and_small_sample(
    client: TestClient,
) -> None:
    """percentile factor → sample_size(=모집단 n) + small_sample 노출 (ADR-0024 D2).

    fixture 유니버스 = 3 종목 (n=3 < 30) → small_sample=True. 값은 보존
    (N/A 강등 아님 — percentile 실값 + 디스클로저 플래그).
    """
    res = _post(client, {
        "pack": _pack([_ni_percentile_factor()]),
        "as_of": _AS_OF,
        "codes": ["005930"],
    })
    assert res.status_code == 200
    pct = _factor_of(res.json()["results"][0], "my:ni-pct")
    # 값 보존 — 소표본이어도 percentile 산출 (사실 + 한계).
    assert pct["is_na"] is False
    assert pct["value"] is not None
    # 모집단 크기 사실 + 소표본 표식 (n=3 < 30).
    assert pct["sample_size"] == 3
    assert pct["small_sample"] is True


def test_stock_local_factor_no_sample_size(client: TestClient) -> None:
    """universe-상대 op 없는 종목-국소 factor → sample_size None, small_sample False.

    모집단 개념이 없는 종목-국소 산출에는 소표본 디스클로저가 붙지 않는다
    (ADR-0024 D5).
    """
    res = _post(client, {
        "pack": _pack([_ni_factor()]),
        "as_of": _AS_OF,
        "codes": ["005930"],
    })
    ni = _factor_of(res.json()["results"][0], "my:ni")
    assert ni["sample_size"] is None
    assert ni["small_sample"] is False


def test_small_sample_n1_percentile_100_preserved() -> None:
    """n==1 → percentile=100 값 보존 + small_sample=True (ADR-0024 D2 — 가장 날카로운 케이스).

    "자기 혼자뿐인 분포의 100 percentile" 을 N/A 로 강등하지 않고 (값 100 보존),
    sample_size=1 + small_sample=True 디스클로저로 오인을 막는다.
    """
    stocks = FakeStocksMasterRepository(records=[
        _stock("005930", "삼성전자", "KOSPI"),
    ])
    financials = _ni_quarters("005930", "1000")  # TTM 4000, 유일 종목.
    app = create_app(
        stocks_repository=stocks,
        financial_repository=FakeFinancialRepository(records=list(financials)),
    )
    with TestClient(app) as c:
        res = _post(c, {
            "pack": _pack([_ni_percentile_factor()]),
            "as_of": _AS_OF,
            "codes": ["005930"],
        })
        assert res.status_code == 200
        pct = _factor_of(res.json()["results"][0], "my:ni-pct")
        # 값 100 보존 (N/A 아님 — bisect_right(1)/1*100).
        assert pct["is_na"] is False
        assert Decimal(pct["value"]) == Decimal("100")
        # 모집단 1 → 소표본 (가장 날카로운 케이스).
        assert pct["sample_size"] == 1
        assert pct["small_sample"] is True


def test_multi_universe_field_factor_uses_min_n() -> None:
    """다중 universe-상대 field factor → min(n) 귀속 (ADR-0024 D5).

    가장 빈약한 모집단이 전체 신뢰도 상한. weighted_sum(percentile(net_income),
    percentile(market_cap)) 에서 net_income 모집단 n=3, market_cap 모집단 n=2
    (한 종목 market_cap 결측) → factor sample_size = min(3, 2) = 2.
    """
    _MC_FIELD = "market_cap_krx_official"
    composite_factor: dict[str, Any] = {
        "canonical_id": "my:composite",
        "uuid": "20000000-0000-0000-0000-0000000000c0",
        "name": "복합 percentile",
        "description": "순이익·시총 percentile 가중합 (custom)",
        "formula": {
            "ast": {
                "op": "weighted_sum",
                "args": [
                    {"op": "percentile", "field": _NI_FIELD},
                    {"op": "percentile", "field": _MC_FIELD},
                ],
                "weights": ["0.5", "0.5"],  # ADR-0032 D1 — decimal 문자열.
            },
            "inputs": [_NI_FIELD, _MC_FIELD],
        },
        "unit": "ratio",
    }
    stocks = FakeStocksMasterRepository(records=[
        _stock("005930", "삼성전자", "KOSPI"),
        _stock("000660", "SK하이닉스", "KOSPI"),
        _stock("035720", "카카오", "KOSDAQ"),
    ])
    financials: Sequence[FinancialRecord] = (
        _ni_quarters("005930", "1000")
        + _ni_quarters("000660", "3000")
        + _ni_quarters("035720", "2000")
    )
    # market_cap 은 2 종목만 (035720 결측) → market_cap 모집단 n=2 < net_income n=3.
    market_caps = [
        _mc("005930", "100000"),
        _mc("000660", "200000"),
    ]
    app = create_app(
        stocks_repository=stocks,
        financial_repository=FakeFinancialRepository(records=list(financials)),
        market_cap_repository=FakeMarketCapRepository(market_caps),
    )
    with TestClient(app) as c:
        res = _post(c, {
            "pack": _pack([composite_factor]),
            "as_of": _AS_OF,
            "codes": ["005930"],
        })
        assert res.status_code == 200
        body = res.json()
        assert body["valid"] is True, body["issues"]
        comp = _factor_of(body["results"][0], "my:composite")
        # min(net_income n=3, market_cap n=2) = 2 (ADR-0024 D5 귀속).
        assert comp["sample_size"] == 2
        assert comp["small_sample"] is True


def test_deterministic_repeat(client: TestClient) -> None:
    """동일 요청 반복 → byte-동일 응답 (결정적 평가)."""
    payload = {
        "pack": _pack([_ni_factor(), _ni_percentile_factor()]),
        "as_of": _AS_OF,
        "codes": ["005930", "035720"],
    }
    r1 = _post(client, payload)
    r2 = _post(client, payload)
    assert r1.text == r2.text
