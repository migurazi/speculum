"""M7 #6 — `GET /api/stocks/{code}/total-return` 엔드포인트 테스트.

세전 Total Return 시계열 backend(가격 차트 "배당재투자" 토글)의 동작 검증.

핵심 invariant:
    - 배당 0 + corporate action 0 → total-return value == 보정 종가(= raw, T20
      미적용) 라인 (Decimal 정밀도 내). 첫 point index == "1", value == close[0].
    - 배당 존재 → 배당락일 재투자분만큼 total-return 이 무배당 baseline 위로 발산.
    - 이중 PIT (announced<=as_of AND effective<=as_of) — repo 가 강제. as_of 이후
      배당은 재투자 제외.
    - 배당락일이 거래일 시계열에 부재(but <= as_of) → §2.1 warning + 해당 배당 skip.
    - 보정 invariant 위반(미지원 action_type) → fail-soft(points=[] + warning),
      502 아님 (데이터 결함이지 외부 출처 장애 아님).
    - 종목코드 zero-pad (5930 → 005930).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeDividendRepository,
    FakePriceRepository,
)
from app.repositories.pit_protocols import CorporateActionRecord, PriceRecord

_CODE: Final[str] = "005930"
_LINEAGE: Final[UUID] = UUID(int=int(_CODE))
_CITATION: Final[UUID] = UUID("00000000-0000-0000-0000-0000000000ce")
_AS_OF: Final[date] = date(2024, 5, 7)


def _price(*, d: date, close: str) -> PriceRecord:
    """단일 일봉 — OHLC 동일가, close_adjusted == close_raw (보정 미적용 가정).

    test_total_return_display_percent 의 `_price` 헬퍼 동형.
    """
    return PriceRecord(
        id=uuid4(), code=_CODE, code_lineage_id=_LINEAGE, effective_date=d,
        open_raw=Decimal(close), high_raw=Decimal(close), low_raw=Decimal(close),
        close_raw=Decimal(close), volume=1000, trading_value=Decimal("1000000"),
        close_adjusted=Decimal(close), citation_id=_CITATION,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _div(
    *,
    effective_date: date,
    cash_amount: str,
    announced_date: date | None = None,
) -> CorporateActionRecord:
    """현금배당 record — announced 미지정 시 effective 와 동일(어댑터 산출 동형)."""
    return CorporateActionRecord(
        id=uuid4(), code=_CODE, code_lineage_id=_LINEAGE,
        action_type="cash_dividend",
        announced_date=announced_date or effective_date,
        effective_date=effective_date, payment_date=None, ratio=None,
        cash_amount=Decimal(cash_amount), details={}, citation_id=_CITATION,
        superseded_by=None, created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _bogus_action(*, effective_date: date) -> CorporateActionRecord:
    """미지원 action_type — PriceAdjuster.adjust 가 AdjusterDataError raise (fail-soft 유발)."""
    return CorporateActionRecord(
        id=uuid4(), code=_CODE, code_lineage_id=_LINEAGE,
        action_type="bogus_unsupported_type",
        announced_date=effective_date, effective_date=effective_date,
        payment_date=None, ratio=Decimal("2"), cash_amount=None, details={},
        citation_id=_CITATION, superseded_by=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


# 1 년치 가격(min-coverage 무관 — 엔드포인트는 trailing-1y guard 없음). 단조 상승.
_PRICES: Final[tuple[PriceRecord, ...]] = (
    _price(d=date(2023, 5, 10), close="100"),
    _price(d=date(2024, 1, 15), close="110"),
    _price(d=_AS_OF, close="121"),
)


def _make_client(
    *,
    prices: tuple[PriceRecord, ...] = _PRICES,
    dividends: tuple[CorporateActionRecord, ...] = (),
    actions: tuple[CorporateActionRecord, ...] = (),
) -> Iterator[TestClient]:
    app = create_app(
        price_repository=FakePriceRepository(records=prices),
        corporate_action_repository=FakeCorporateActionRepository(records=actions),
        dividend_repository=FakeDividendRepository(records=dividends),
    )
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client() -> Iterator[TestClient]:
    """기본 — 가격만(배당·CA 0)."""
    yield from _make_client()


def _get(client: TestClient, code: str = _CODE) -> dict:
    res = client.get(f"/api/stocks/{code}/total-return?as_of={_AS_OF.isoformat()}")
    assert res.status_code == 200, res.text
    return res.json()


# =============================================================================
# 빈 시계열 / 기본 구조
# =============================================================================

def test_no_prices_returns_empty_points() -> None:
    """가격 결손 → points=[] (200). 차트가 "데이터 없음" overlay."""
    for c in _make_client(prices=()):
        body = _get(c)
        assert body["points"] == []
        assert body["warnings"] == []
        assert body["code"] == _CODE


def test_response_shape(client: TestClient) -> None:
    """code / as_of / points / warnings 필드 + point 구조(date/value/index)."""
    body = _get(client)
    assert body["code"] == _CODE
    assert body["as_of"] == _AS_OF.isoformat()
    assert len(body["points"]) == len(_PRICES)
    first = body["points"][0]
    assert set(first.keys()) == {"date", "value", "index"}
    assert first["date"] == "2023-05-10"


# =============================================================================
# 배당 0 — total-return == 보정 종가(rebase invariant)
# =============================================================================

def test_no_dividends_first_point_index_one_value_base_close(
    client: TestClient,
) -> None:
    """배당 0: 첫 point index==1.0, value==close[0] (rebase 기준점)."""
    body = _get(client)
    first = body["points"][0]
    assert Decimal(first["index"]) == Decimal(1)
    assert Decimal(first["value"]) == Decimal("100")


def test_no_dividends_value_tracks_adjusted_close(client: TestClient) -> None:
    """배당 0 + CA 0 → value[t] ≈ close[t] (보정==raw), index[t] ≈ close[t]/close[0].

    TRI 는 prec=28 chained division 이라 telescoping 에 ~1e-26 상대오차가 있을 수
    있어 정확 동치 대신 상대 허용오차로 검증.
    """
    body = _get(client)
    expected_close = [Decimal("100"), Decimal("110"), Decimal("121")]
    for point, close in zip(body["points"], expected_close, strict=True):
        value = Decimal(point["value"])
        # 상대오차 1e-9 이내 — 배당 0 이면 value == 보정 종가.
        assert abs(value - close) <= close * Decimal("1e-9")
        index = Decimal(point["index"])
        assert abs(index - close / Decimal("100")) <= Decimal("1e-9")


# =============================================================================
# 배당 존재 — 무배당 baseline 위로 발산
# =============================================================================

def test_dividend_increases_total_return() -> None:
    """배당락일(거래일) 재투자 → 마지막 value 가 무배당 baseline(121) 초과.

    배당 5 원을 2024-01-15(거래일, 보정계수 1)에 재투자 → 그 시점 이후 TRI 가
    (110+5)/110 배 추가 상승 → 마지막 value > 121.
    """
    dividends = (_div(effective_date=date(2024, 1, 15), cash_amount="5"),)
    for c in _make_client(dividends=dividends):
        body = _get(c)
        last_value = Decimal(body["points"][-1]["value"])
        # 무배당이면 121. 배당 재투자로 그 이상.
        assert last_value > Decimal("121")
        # 첫 point 는 여전히 base close (재투자 전).
        assert Decimal(body["points"][0]["value"]) == Decimal("100")
        assert body["warnings"] == []


# =============================================================================
# PIT — as_of 이후 배당 제외 (이중 PIT, repo 강제)
# =============================================================================

def test_dividend_after_as_of_excluded() -> None:
    """effective_date > as_of 배당 → 재투자 제외 (이중 PIT). value == 무배당 baseline."""
    # as_of(2024-05-07) 이후 배당 — repo 가 effective<=as_of 로 필터.
    dividends = (_div(effective_date=date(2024, 6, 1), cash_amount="50"),)
    for c in _make_client(dividends=dividends):
        body = _get(c)
        # 재투자 0 → 마지막 value == 보정 종가(121, 상대오차 내).
        last_value = Decimal(body["points"][-1]["value"])
        assert abs(last_value - Decimal("121")) <= Decimal("121") * Decimal("1e-9")


def test_dividend_announced_after_as_of_excluded() -> None:
    """announced_date > as_of 배당 → 재투자 제외 (announced 축 PIT, look-ahead 차단)."""
    # effective 는 as_of 이전이나 announced(공시)가 as_of 이후 — 아직 알 수 없음.
    dividends = (
        _div(
            effective_date=date(2024, 1, 15),
            announced_date=date(2024, 6, 1),
            cash_amount="50",
        ),
    )
    for c in _make_client(dividends=dividends):
        body = _get(c)
        last_value = Decimal(body["points"][-1]["value"])
        assert abs(last_value - Decimal("121")) <= Decimal("121") * Decimal("1e-9")


# =============================================================================
# §2.1 — 조용한 손실 금지 (warning 노출)
# =============================================================================

def test_dividend_ex_date_not_trading_day_emits_warning() -> None:
    """배당락일이 거래일 시계열에 부재(but <= as_of) → warning + 해당 배당 skip.

    배당락일 2024-03-03 은 가격 시계열에 없음 → 재투자 불가 → §2.1 warning.
    points 는 정상 반환(다른 배당/가격 영향 없음), 마지막 value == 무배당 baseline.
    """
    dividends = (_div(effective_date=date(2024, 3, 3), cash_amount="5"),)
    for c in _make_client(dividends=dividends):
        body = _get(c)
        assert body["warnings"], "배당락일 부재 시 warning 이 노출되어야 함"
        assert any("not in price series" in w for w in body["warnings"])
        # 배당 skip → value == 무배당 baseline.
        last_value = Decimal(body["points"][-1]["value"])
        assert abs(last_value - Decimal("121")) <= Decimal("121") * Decimal("1e-9")


# =============================================================================
# fail-soft — 보정 invariant 위반 (미지원 action_type)
# =============================================================================

def test_unsupported_action_type_fails_soft() -> None:
    """미지원 action_type → AdjusterError → points=[] + warning (502 아님, 200)."""
    actions = (_bogus_action(effective_date=date(2024, 1, 15)),)
    for c in _make_client(actions=actions):
        body = _get(c)
        assert body["points"] == []
        assert body["warnings"]
        assert any("invariant 위반" in w for w in body["warnings"])


def test_zero_base_close_emits_warning_not_silent_flatten() -> None:
    """첫 거래일 보정 종가 0 → points=[] + warning (§2.1 조용한 0 평탄화 금지).

    단일 record(close 0)면 compute 의 0-division guard(≥2 record 전제)를 피해
    series 가 1건으로 통과하므로, 엔드포인트의 base_close==0 guard 가 잡아야 함.
    """
    prices = (_price(d=_AS_OF, close="0"),)
    for c in _make_client(prices=prices):
        body = _get(c)
        assert body["points"] == []
        assert any("rebase 불가" in w for w in body["warnings"])


# =============================================================================
# 종목코드 정규화
# =============================================================================

def test_zero_pads_short_code(client: TestClient) -> None:
    """5930 → 005930 zero-pad 후 동일 데이터 반환."""
    body = _get(client, code="5930")
    assert body["code"] == _CODE
    assert len(body["points"]) == len(_PRICES)


def test_non_numeric_code_returns_422(client: TestClient) -> None:
    """비숫자 code → Path regex 가 422 (다른 stocks 라우트 동형)."""
    res = client.get(f"/api/stocks/ABCDEF/total-return?as_of={_AS_OF.isoformat()}")
    assert res.status_code == 422
