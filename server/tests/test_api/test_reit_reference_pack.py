"""리츠(REIT) reference custom pack — ffo-multiple:reit / dividend-yield:reit.

ADR-0023 D8 (2026-06-02 재현성 경로) 결정대로 **빌트인 pack 무변경** — 리츠 factor
는 별도 reference custom pack (`community/speculum-reit-reference`) 으로 제공하고,
db_field_provider 의 신규 reference field (`depreciation_expense_annual`,
`dividends_paid_annual`) 로 해소한다. 본 테스트는 POST /api/factor-packs/evaluate
로 reference pack body 를 stateless 평가해 리츠 factor 의 실값 / 자연 N/A 를 검증한다.

테스트 매트릭스:
1. 리츠 재무 (net_income + depreciation_expense + dividends_paid_annual) 완비 →
   ffo-multiple:reit / dividend-yield:reit **실값**.
2. 감가상각 미주입 리츠 → ffo-multiple:reit N/A (FFO 구성 결손) + dividend-yield:reit
   는 실값 (배당 계정 존재).
3. 보통주 (리츠 계정 전무) → 리츠 factor 모두 N/A (field 자연 결손, §2.1 Fidelity —
   security_type 분기 없음).
4. reference pack validate_custom_pack 통과 + content_hash 봉인 검증.
5. 빌트인 pack content_hash 불변 (리츠 factor 도입이 기존 run snapshot 재현 무관).

산식 (factor_evaluator EvaluatorPolicy v1.0):
- market_cap_ex_treasury = (shares_outstanding - shares_treasury) × close_price_adjusted
  = (1_000_000 - 0) × 5000 = 5_000_000_000 (보정 사건 없음 → adjusted = raw).
- FFO = net_income TTM (4 분기 strict 합) + depreciation TTM (4 분기 strict 합).
- ffo-multiple:reit = market_cap_ex_treasury / FFO.
- dividend-yield:reit = ratio_pct(dividends_paid_annual, market_cap_ex_treasury) =
  raw ratio (left / right) — ×100 은 표시 layer 책임 (factor_evaluator.py:880 정책).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeFinancialRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    FinancialRecord,
    MarketCapRecord,
    PriceRecord,
    StockMasterRecord,
    TreasurySharesRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.services.factor_pack import compute_pack_hash, validate_custom_pack

_AS_OF = "2024-05-07"
_CONSOLIDATED = "consolidated"
_CITATION = UUID("00000000-0000-0000-0000-0000000000ff")

_NI_ACCOUNT = "net_income_attributable_to_owners"
_DEPRECIATION_ACCOUNT = "depreciation_expense"
_DIVIDENDS_ACCOUNT = "dividends_paid_annual"

# 빌트인 pack content_hash — 리츠 factor 도입이 이 값을 절대 바꾸지 않아야 함
# (ADR-0023 D8: 변경 시 기존 run snapshot frozen hash 불일치 → §2.10 재현 붕괴).
_BUILTIN_HASH = "sha256:78e2d93f46700e475c56d17edc76faa8aa742406368b1b631a45b3908d5aa36d"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REIT_PACK_PATH = (
    _REPO_ROOT
    / "server"
    / "builtin-packs"
    / "reference"
    / "speculum-reit-reference-v1.0.0.json"
)


# =============================================================================
# Fixtures / helpers
# =============================================================================

def _load_reit_pack() -> dict[str, Any]:
    """reference pack JSON 로드 (evaluate body 의 pack 으로 사용)."""
    return json.loads(_REIT_PACK_PATH.read_text(encoding="utf-8"))


def _stock(code: str, name: str, market: str) -> StockMasterRecord:
    return StockMasterRecord(
        id=UUID(int=int(code)),
        current_code=code, current_name=name, market=market,
        listing_date=date(2000, 1, 1), delisting_date=None, fiscal_month=12,
        code_history=(CodeHistoryEntry(code, date(2000, 1, 1), None,
                                       "initial_listing"),),
    )


def _financial_quarters(
    code: str, account: str, per_quarter: str,
) -> list[FinancialRecord]:
    """code·account 의 4 분기 연결 재무 — TTM 합 = per_quarter × 4.

    effective_date 는 as_of (2024-05-07) 이전 고정 (PIT 통과). financial_annual /
    financial_series resolver 가 4 분기 strict 합으로 해소.
    """
    periods = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    eff = [date(2023, 5, 15), date(2023, 8, 14), date(2023, 11, 14),
           date(2024, 3, 30)]
    out: list[FinancialRecord] = []
    for fp, e in zip(periods, eff, strict=True):
        out.append(FinancialRecord(
            id=uuid4(),
            code=code,
            code_lineage_id=UUID(int=int(code)),
            effective_date=e,
            fiscal_period=fp,
            account=account,
            value=Decimal(per_quarter),
            unit="krw",
            ifrs_type=_CONSOLIDATED,
            citation_id=_CITATION,
            superseded_by=None,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        ))
    return out


def _dividends_scalar(code: str, annual: str) -> list[FinancialRecord]:
    """단일 fiscal_period 의 연 배당금 (financial_scalar) — latest active value.

    dividends_paid_annual 은 DART 현금흐름표 재무활동 배당금 지급 (연 누적값) 이라
    db_field_provider 가 financial_scalar (최신 fiscal_period value) 로 해소.
    """
    return [FinancialRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=UUID(int=int(code)),
        effective_date=date(2024, 3, 30),
        fiscal_period="2023Q4",
        account=_DIVIDENDS_ACCOUNT,
        value=Decimal(annual),
        unit="krw",
        ifrs_type=_CONSOLIDATED,
        citation_id=_CITATION,
        superseded_by=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )]


def _price(code: str, close: str) -> PriceRecord:
    """단일 일자 가격 — as_of 직전 영업일. 보정 사건 없음 → adjusted = raw."""
    return PriceRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=UUID(int=int(code)),
        effective_date=date(2024, 5, 3),
        open_raw=Decimal(close),
        high_raw=Decimal(close),
        low_raw=Decimal(close),
        close_raw=Decimal(close),
        volume=1000,
        trading_value=Decimal("1000000"),
        close_adjusted=Decimal(close),
        citation_id=_CITATION,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _market_cap(code: str, shares_outstanding: int) -> MarketCapRecord:
    """발행주식수 fact — market_cap_ex_treasury 의 shares_issued 입력."""
    return MarketCapRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=UUID(int=int(code)),
        effective_date=date(2024, 5, 3),
        market_cap=Decimal(shares_outstanding) * Decimal("5000"),
        shares_outstanding=shares_outstanding,
        shares_treasury=None,
        citation_id=_CITATION,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _treasury(code: str, shares_treasury: int) -> TreasurySharesRecord:
    """자사주 fact — market_cap_ex_treasury 의 shares_treasury 입력."""
    return TreasurySharesRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=UUID(int=int(code)),
        effective_date=date(2024, 3, 30),
        fiscal_period="2023Q4",
        shares_treasury=shares_treasury,
        citation_id=_CITATION,
        superseded_by=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


# 종목코드: 리츠 완비(379780)·감가상각 결손 리츠(330590)·보통주(005930).
_REIT_FULL = "379780"
_REIT_NO_DEP = "330590"
_COMMON = "005930"


@pytest.fixture
def client() -> Iterator[TestClient]:
    """리츠 완비 + 감가상각 결손 리츠 + 보통주 (리츠 계정 전무) 의 3 종목 환경.

    - 379780 (리츠 완비): net_income TTM=1e9, depreciation TTM=1e9, dividends=2e8,
      shares_outstanding=1_000_000, shares_treasury=0, close=5000.
      → market_cap_ex_treasury = 5e9, FFO = 2e9, ffo-multiple = 2.5,
        dividend-yield = 2e8 / 5e9 = 0.04 (raw ratio).
    - 330590 (감가상각 결손 리츠): depreciation 미주입 → FFO 구성 결손 → ffo N/A.
      net_income + dividends + 시총 입력은 존재 → dividend-yield 는 실값.
    - 005930 (보통주): 리츠 계정 (depreciation / dividends) 전무 → 리츠 factor N/A.
      net_income + 시총 입력은 존재 (그래도 ffo 는 depreciation 결손, dividend 는
      dividends 결손으로 N/A).
    """
    stocks = FakeStocksMasterRepository(records=[
        _stock(_REIT_FULL, "신한알파리츠", "KOSPI"),
        _stock(_REIT_NO_DEP, "롯데리츠", "KOSPI"),
        _stock(_COMMON, "삼성전자", "KOSPI"),
    ])

    financials: list[FinancialRecord] = []
    # 379780 — 리츠 완비.
    financials += _financial_quarters(_REIT_FULL, _NI_ACCOUNT, "250000000")
    financials += _financial_quarters(_REIT_FULL, _DEPRECIATION_ACCOUNT, "250000000")
    financials += _dividends_scalar(_REIT_FULL, "200000000")
    # 330590 — 감가상각만 결손 (net_income + dividends 존재).
    financials += _financial_quarters(_REIT_NO_DEP, _NI_ACCOUNT, "100000000")
    financials += _dividends_scalar(_REIT_NO_DEP, "150000000")
    # 005930 — 보통주: net_income 만 (리츠 전용 계정 depreciation / dividends 없음).
    financials += _financial_quarters(_COMMON, _NI_ACCOUNT, "500000000")

    prices = [
        _price(_REIT_FULL, "5000"),
        _price(_REIT_NO_DEP, "5000"),
        _price(_COMMON, "5000"),
    ]
    market_caps = [
        _market_cap(_REIT_FULL, 1_000_000),
        _market_cap(_REIT_NO_DEP, 1_000_000),
        _market_cap(_COMMON, 1_000_000),
    ]
    treasuries = [
        _treasury(_REIT_FULL, 0),
        _treasury(_REIT_NO_DEP, 0),
        _treasury(_COMMON, 0),
    ]

    app = create_app(
        stocks_repository=stocks,
        financial_repository=FakeFinancialRepository(records=financials),
        price_repository=FakePriceRepository(prices),
        market_cap_repository=FakeMarketCapRepository(market_caps),
        treasury_repository=FakeTreasurySharesRepository(treasuries),
        corporate_action_repository=FakeCorporateActionRepository([]),
    )
    with TestClient(app) as c:
        yield c


def _post(client: TestClient, codes: list[str]) -> Any:
    return client.post("/api/factor-packs/evaluate", json={
        "pack": _load_reit_pack(),
        "as_of": _AS_OF,
        "codes": codes,
    })


def _factor_of(stock: dict, canonical_id: str) -> dict:
    return next(f for f in stock["factors"] if f["canonical_id"] == canonical_id)


# =============================================================================
# 1. 리츠 완비 → ffo-multiple / dividend-yield 실값
# =============================================================================

def test_reit_full_real_values(client: TestClient) -> None:
    """리츠 재무 완비 → ffo-multiple=2.5, dividend-yield=0.04 (raw ratio)."""
    res = _post(client, [_REIT_FULL])
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True, body["issues"]
    stock = body["results"][0]
    assert stock["code"] == _REIT_FULL

    ffo = _factor_of(stock, "ffo-multiple:reit")
    assert ffo["is_na"] is False
    assert ffo["na_reason"] is None
    # market_cap_ex_treasury 5e9 / FFO 2e9 = 2.5.
    assert Decimal(ffo["value"]) == Decimal("2.5")
    assert ffo["unit"] == "ratio"

    dy = _factor_of(stock, "dividend-yield:reit")
    assert dy["is_na"] is False
    assert dy["na_reason"] is None
    # ratio_pct = raw ratio (×100 은 표시 layer) = 2e8 / 5e9 = 0.04.
    assert Decimal(dy["value"]) == Decimal("0.04")
    assert dy["unit"] == "percent"


# =============================================================================
# 2. 감가상각 미보고 리츠 → ffo N/A, dividend-yield 실값
# =============================================================================

def test_reit_missing_depreciation_ffo_na_dividend_value(client: TestClient) -> None:
    """감가상각 결손 → ffo-multiple N/A (FFO 구성 결손), dividend-yield 는 실값."""
    res = _post(client, [_REIT_NO_DEP])
    assert res.status_code == 200
    body = res.json()
    stock = body["results"][0]

    ffo = _factor_of(stock, "ffo-multiple:reit")
    # depreciation_expense_annual 결손 → add (net_income, depreciation) missing_input
    # → div N/A (FFO 구성 결손, §2.1 Fidelity 자연 N/A).
    assert ffo["is_na"] is True
    assert ffo["value"] is None
    assert ffo["na_reason"] is not None

    dy = _factor_of(stock, "dividend-yield:reit")
    # net_income / depreciation 무관 — dividends + 시총 입력 존재 → 실값.
    assert dy["is_na"] is False
    # 1.5e8 / 5e9 = 0.03.
    assert Decimal(dy["value"]) == Decimal("0.03")


# =============================================================================
# 3. 보통주 (리츠 계정 전무) → 리츠 factor 모두 N/A
# =============================================================================

def test_common_stock_reit_factors_all_na(client: TestClient) -> None:
    """보통주 → depreciation / dividends 계정 결손 → 두 리츠 factor 모두 N/A.

    security_type 분기 코드 없이 field 자연 결손이 N/A 를 만든다 (§2.1 Fidelity).
    """
    res = _post(client, [_COMMON])
    assert res.status_code == 200
    body = res.json()
    stock = body["results"][0]

    ffo = _factor_of(stock, "ffo-multiple:reit")
    assert ffo["is_na"] is True
    assert ffo["value"] is None

    dy = _factor_of(stock, "dividend-yield:reit")
    assert dy["is_na"] is True
    assert dy["value"] is None


# =============================================================================
# 4. 다중 code — 각 종목 독립 산출 (완비 / 결손 혼합)
# =============================================================================

def test_multiple_codes_independent(client: TestClient) -> None:
    """3 종목 동시 — 완비 실값 + 결손 N/A 독립, 요청 순서 보존."""
    res = _post(client, [_REIT_FULL, _REIT_NO_DEP, _COMMON])
    assert res.status_code == 200
    body = res.json()
    assert [s["code"] for s in body["results"]] == [
        _REIT_FULL, _REIT_NO_DEP, _COMMON,
    ]
    by_code = {s["code"]: s for s in body["results"]}

    assert _factor_of(by_code[_REIT_FULL], "ffo-multiple:reit")["is_na"] is False
    assert _factor_of(by_code[_REIT_NO_DEP], "ffo-multiple:reit")["is_na"] is True
    assert _factor_of(by_code[_COMMON], "dividend-yield:reit")["is_na"] is True


# =============================================================================
# 5. reference pack 무결성 — validate_custom_pack 통과 + content_hash 봉인
# =============================================================================

def test_reference_pack_validates_clean() -> None:
    """reference pack 이 validate_custom_pack 전 단계 통과 (issues 빈)."""
    body = _load_reit_pack()
    issues = validate_custom_pack(body)
    assert issues == [], issues


def test_reference_pack_content_hash_sealed() -> None:
    """content_hash 가 compute_pack_hash 결과와 일치 (봉인)."""
    body = _load_reit_pack()
    assert body["content_hash"] == compute_pack_hash(body)


def test_reference_pack_slug_and_factors() -> None:
    """reference tier slug + 리츠 factor 2 개 + self-contained 시총 base 포함."""
    body = _load_reit_pack()
    assert body["pack_slug"] == "community/speculum-reit-reference"
    cids = {f["canonical_id"] for f in body["factors"]}
    assert "ffo-multiple:reit" in cids
    assert "dividend-yield:reit" in cids
    # 파생 입력 market_cap_ex_treasury 해소용 base factor self-contained.
    assert "market-cap:ex-treasury" in cids


# =============================================================================
# 6. 빌트인 pack content_hash 불변 — ADR-0023 D8 재현성 (리츠 도입 무영향)
# =============================================================================

def test_builtin_pack_hash_unchanged() -> None:
    """빌트인 pack content_hash 가 리츠 factor 도입 후에도 불변 (재현성 봉인).

    리츠 factor 는 별도 reference pack 이고 빌트인 JSON / DEFAULT_PACK 무변경이므로
    기존 저장 run 의 snapshot_versions frozen hash 와 계속 일치 (ADR-0023 D8).
    """
    from app.services.factor_pack import DEFAULT_PACK

    assert DEFAULT_PACK.pack_slug == "speculum-builtin"
    assert DEFAULT_PACK.computed_hash == _BUILTIN_HASH


def test_deterministic_repeat(client: TestClient) -> None:
    """동일 요청 반복 → byte-동일 응답 (결정적 평가)."""
    r1 = _post(client, [_REIT_FULL, _REIT_NO_DEP])
    r2 = _post(client, [_REIT_FULL, _REIT_NO_DEP])
    assert r1.text == r2.text


def test_dividend_yield_abs_normalizes_negative_dividends() -> None:
    """dividends_paid 음수(현금유출 보고) → dividend-yield 양수(abs 정규화, M2.1 이슈 1).

    K-IFRS 현금흐름표 "재무활동 배당금 지급"은 현금유출이라 음수(-2e8)로 보고될 수
    있으나 dividend-yield 분자는 배당 *규모*다. db_field_provider 의 magnitude(abs)
    정규화로 0.04(양수) 산출 — 음수 yield(§2.1 거울 왜곡, conformance M2.1 이슈 1)
    차단. (양수 케이스는 test_reit_full_real_values 가 커버 — 부호 무관 동일 값.)
    """
    code = _REIT_FULL
    financials = (
        _financial_quarters(code, _NI_ACCOUNT, "250000000")
        + _financial_quarters(code, _DEPRECIATION_ACCOUNT, "250000000")
        + _dividends_scalar(code, "-200000000")  # 음수 — 현금유출 보고.
    )
    app = create_app(
        stocks_repository=FakeStocksMasterRepository([_stock(code, "리츠", "KOSPI")]),
        financial_repository=FakeFinancialRepository(records=financials),
        price_repository=FakePriceRepository([_price(code, "5000")]),
        market_cap_repository=FakeMarketCapRepository([_market_cap(code, 1_000_000)]),
        treasury_repository=FakeTreasurySharesRepository([_treasury(code, 0)]),
        corporate_action_repository=FakeCorporateActionRepository([]),
    )
    with TestClient(app) as c:
        res = c.post("/api/factor-packs/evaluate", json={
            "pack": _load_reit_pack(), "as_of": _AS_OF, "codes": [code],
        })
    assert res.status_code == 200
    stock = res.json()["results"][0]
    dy = _factor_of(stock, "dividend-yield:reit")
    assert dy["is_na"] is False, dy
    # abs(-2e8) / 5e9 = 0.04 (양수) — 음수 yield 차단(magnitude 정규화).
    assert Decimal(dy["value"]) == Decimal("0.04")
    assert Decimal(dy["value"]) > 0
