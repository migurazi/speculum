"""ETF NAV adapter 단위 테스트 — fixture 기반 (ADR-0023 D3-a).

테스트 매트릭스:
    1. fetch_etf_nav — 정상 path: NavRow 변환 + trade_date 오름차순 + citation
    2. 괴리율 산출 — pykrx 제공 deviation DataFrame join 경로
    3. 괴리율 fallback — deviation DataFrame 부재 시 직접 산출
    4. AUM None — 순자산총액 컬럼 부재 시 None 처리
    5. AdapterError — 빈 DataFrame (ETF 미존재)
    6. AdapterError — NAV 컬럼 누락 이상 응답
    7. AdapterRetryError — 네트워크 오류
    8. FakeNavRepository PIT 조회 — effective_date <= as_of 최신 반환
    9. FakeNavRepository save_navs — 같은 (code, effective_date) overwrite
    10. canonical_id 미등록 확인 — builtin/reference pack 에 nav 부재 (R4 deferred)

모든 테스트는 pykrx 미설치 환경에서도 통과 — mock module 주입.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pandas as pd
import pytest

from app.adapters.base import AdapterError, AdapterRetryError, FetchResult
from app.adapters.pykrx_adapter import PykrxAdapter
from app.repositories.fakes import FakeNavRepository
from app.repositories.pit_protocols import NavRecord

# =============================================================================
# Helpers — fixture DataFrame 생성
# =============================================================================

def _etf_ohlcv_df(
    *,
    days: list[date],
    navs: list[float],
    closes: list[float],
    aums: list[float] | None = None,
) -> pd.DataFrame:
    """pykrx `get_etf_ohlcv_by_date` DataFrame 모사 — 한글 컬럼 + NAV."""
    data: dict[str, Any] = {
        "NAV": navs,
        "시가": closes,
        "고가": closes,
        "저가": closes,
        "종가": closes,
        "거래량": [100_000] * len(days),
        "거래대금": [c * 100_000 for c in closes],
        "기초지수": [1000.0] * len(days),
    }
    if aums is not None:
        data["순자산총액"] = aums
    return pd.DataFrame(data, index=pd.to_datetime(days))


def _deviation_df(
    *,
    days: list[date],
    deviations: list[float],
) -> pd.DataFrame:
    """pykrx `get_etf_price_deviation` DataFrame 모사."""
    return pd.DataFrame(
        {"괴리율": deviations},
        index=pd.to_datetime(days),
    )


def _make_nav_mock(
    *,
    nav_df: pd.DataFrame | None = None,
    dev_df: pd.DataFrame | None = None,
) -> Any:
    """pykrx.stock 모듈 mock — ETF NAV 관련 함수 포함."""
    mock = MagicMock()
    mock.get_etf_ohlcv_by_date = MagicMock(return_value=nav_df)
    mock.get_etf_price_deviation = MagicMock(return_value=dev_df)
    return mock


def _make_nav_record(
    *,
    code: str = "069500",
    effective_date: date = date(2024, 1, 2),
    nav: Decimal = Decimal("31000"),
    market_price: Decimal = Decimal("31050"),
    premium_discount_rate: Decimal = Decimal("0.001613"),
    aum: Decimal | None = Decimal("5000000000000"),
    citation_id: UUID | None = None,
    created_at: datetime | None = None,
) -> NavRecord:
    return NavRecord(
        id=uuid4(),
        code=code,
        effective_date=effective_date,
        nav=nav,
        market_price=market_price,
        premium_discount_rate=premium_discount_rate,
        aum=aum,
        citation_id=citation_id or uuid4(),
        created_at=created_at or datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
    )


# =============================================================================
# 1. fetch_etf_nav — 정상 path
# =============================================================================

def test_fetch_etf_nav_returns_canonical_rows() -> None:
    """ETF NAV DataFrame → tuple[NavRow] 변환 + trade_date 오름차순 + Decimal."""
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    navs = [31000.0, 31200.0, 31500.0]
    closes = [31050.0, 31150.0, 31600.0]
    df = _etf_ohlcv_df(days=days, navs=navs, closes=closes)
    adapter = PykrxAdapter(pykrx_module=_make_nav_mock(nav_df=df, dev_df=None))

    result = adapter.fetch_etf_nav(
        "069500",
        start=date(2024, 1, 2),
        end=date(2024, 1, 4),
        batch_id=uuid4(),
    )

    assert isinstance(result, FetchResult)
    assert len(result.data) == 3
    # trade_date 오름차순 보장.
    assert [r.trade_date for r in result.data] == days
    # NAV Decimal 정확성.
    assert result.data[0].nav == Decimal("31000.0")
    assert result.data[2].nav == Decimal("31500.0")
    # market_price.
    assert result.data[0].market_price == Decimal("31050.0")
    # citation 1 개.
    assert len(result.citations) == 1
    cit = result.citations[0]
    assert "069500" in cit.identifier
    assert "etf_nav" in cit.identifier


def test_fetch_etf_nav_citation_fields() -> None:
    """Citation 7-tuple: source=PYKRX, batch_id 전달, effective_date = 마지막 trade_date."""
    days = [date(2024, 1, 2), date(2024, 1, 3)]
    df = _etf_ohlcv_df(days=days, navs=[31000.0, 31200.0], closes=[31050.0, 31150.0])
    batch_id = UUID("00000000-0000-0000-0000-0000000000bb")
    adapter = PykrxAdapter(pykrx_module=_make_nav_mock(nav_df=df))

    result = adapter.fetch_etf_nav(
        "069500", start=date(2024, 1, 2), end=date(2024, 1, 3), batch_id=batch_id,
    )
    cit = result.citations[0]
    assert cit.batch_id == batch_id
    assert cit.effective_date == date(2024, 1, 3)  # 마지막 trade_date
    assert cit.url is None


# =============================================================================
# 2. 괴리율 산출 — deviation DataFrame join 경로
# =============================================================================

def test_fetch_etf_nav_deviation_from_pykrx() -> None:
    """pykrx 괴리율 DataFrame 이 가용한 경우 join → NavRow.premium_discount_rate."""
    days = [date(2024, 1, 2)]
    nav_df = _etf_ohlcv_df(days=days, navs=[31000.0], closes=[31050.0])
    dev_df = _deviation_df(days=days, deviations=[0.0016129])

    adapter = PykrxAdapter(pykrx_module=_make_nav_mock(nav_df=nav_df, dev_df=dev_df))
    result = adapter.fetch_etf_nav(
        "069500", start=date(2024, 1, 2), end=date(2024, 1, 2), batch_id=uuid4(),
    )

    row = result.data[0]
    # pykrx 제공 괴리율 사용 — 직접 계산값과 근사.
    assert abs(float(row.premium_discount_rate) - 0.0016129) < 1e-6


# =============================================================================
# 3. 괴리율 fallback — deviation DataFrame 부재 시 직접 산출
# =============================================================================

def test_fetch_etf_nav_deviation_fallback_calculation() -> None:
    """deviation DataFrame 없을 때 (market_price - nav) / nav 직접 산출."""
    days = [date(2024, 1, 2)]
    # nav=31000, market_price=31310 → 괴리율 = (31310-31000)/31000 = 0.01
    nav_df = _etf_ohlcv_df(days=days, navs=[31000.0], closes=[31310.0])
    # dev_df=None — fallback 경로.
    adapter = PykrxAdapter(pykrx_module=_make_nav_mock(nav_df=nav_df, dev_df=None))

    result = adapter.fetch_etf_nav(
        "069500", start=date(2024, 1, 2), end=date(2024, 1, 2), batch_id=uuid4(),
    )
    row = result.data[0]
    expected = (Decimal("31310.0") - Decimal("31000.0")) / Decimal("31000.0")
    assert abs(float(row.premium_discount_rate) - float(expected)) < 1e-9


# =============================================================================
# 4. AUM None — 순자산총액 컬럼 부재
# =============================================================================

def test_fetch_etf_nav_aum_none_when_column_missing() -> None:
    """순자산총액 컬럼이 없을 때 NavRow.aum = None."""
    days = [date(2024, 1, 2)]
    df = _etf_ohlcv_df(days=days, navs=[31000.0], closes=[31050.0], aums=None)
    assert "순자산총액" not in df.columns  # fixture 검증

    adapter = PykrxAdapter(pykrx_module=_make_nav_mock(nav_df=df))
    result = adapter.fetch_etf_nav(
        "069500", start=date(2024, 1, 2), end=date(2024, 1, 2), batch_id=uuid4(),
    )
    assert result.data[0].aum is None


def test_fetch_etf_nav_aum_present() -> None:
    """순자산총액 컬럼이 있을 때 NavRow.aum = Decimal."""
    days = [date(2024, 1, 2)]
    df = _etf_ohlcv_df(
        days=days, navs=[31000.0], closes=[31050.0], aums=[5_000_000_000_000.0],
    )
    adapter = PykrxAdapter(pykrx_module=_make_nav_mock(nav_df=df))
    result = adapter.fetch_etf_nav(
        "069500", start=date(2024, 1, 2), end=date(2024, 1, 2), batch_id=uuid4(),
    )
    assert result.data[0].aum == Decimal("5000000000000.0")


# =============================================================================
# 5. AdapterError — 빈 DataFrame
# =============================================================================

def test_fetch_etf_nav_empty_df_raises() -> None:
    """pykrx 가 빈 DataFrame 반환 → AdapterError."""
    adapter = PykrxAdapter(
        pykrx_module=_make_nav_mock(nav_df=pd.DataFrame(), dev_df=None),
    )
    with pytest.raises(AdapterError, match="empty ETF NAV"):
        adapter.fetch_etf_nav(
            "069500",
            start=date(2024, 1, 2),
            end=date(2024, 1, 3),
            batch_id=uuid4(),
        )


# =============================================================================
# 6. AdapterError — NAV 컬럼 누락 이상 응답
# =============================================================================

def test_fetch_etf_nav_missing_nav_column_raises() -> None:
    """NAV 컬럼 없는 DataFrame → AdapterError (컬럼명 확인 필요 메시지)."""
    # NAV 컬럼 없는 DataFrame.
    df = pd.DataFrame(
        {"종가": [31050.0]},
        index=pd.to_datetime([date(2024, 1, 2)]),
    )
    adapter = PykrxAdapter(pykrx_module=_make_nav_mock(nav_df=df))
    with pytest.raises(AdapterError, match="NAV"):
        adapter.fetch_etf_nav(
            "069500",
            start=date(2024, 1, 2),
            end=date(2024, 1, 2),
            batch_id=uuid4(),
        )


# =============================================================================
# 7. AdapterRetryError — 네트워크 오류
# =============================================================================

def test_fetch_etf_nav_connection_error_raises_retry() -> None:
    """pykrx 네트워크 오류 → AdapterRetryError."""
    mock = MagicMock()
    mock.get_etf_ohlcv_by_date = MagicMock(side_effect=ConnectionError("net down"))
    adapter = PykrxAdapter(pykrx_module=mock)
    with pytest.raises(AdapterRetryError, match="network error"):
        adapter.fetch_etf_nav(
            "069500",
            start=date(2024, 1, 2),
            end=date(2024, 1, 3),
            batch_id=uuid4(),
        )


def test_fetch_etf_nav_invalid_code_raises() -> None:
    """잘못된 ticker 형식 → AdapterError."""
    adapter = PykrxAdapter(pykrx_module=_make_nav_mock())
    with pytest.raises(AdapterError, match="6-digit numeric"):
        adapter.fetch_etf_nav(
            "ETF01",
            start=date(2024, 1, 2),
            end=date(2024, 1, 3),
            batch_id=uuid4(),
        )


# =============================================================================
# 8. FakeNavRepository PIT 조회
# =============================================================================

def test_fake_nav_repository_pit_fetch() -> None:
    """effective_date <= as_of 중 가장 최신 record 반환."""
    rec_jan2 = _make_nav_record(code="069500", effective_date=date(2024, 1, 2))
    rec_jan3 = _make_nav_record(code="069500", effective_date=date(2024, 1, 3))
    rec_jan5 = _make_nav_record(code="069500", effective_date=date(2024, 1, 5))

    repo = FakeNavRepository([rec_jan2, rec_jan3, rec_jan5])

    # as_of=2024-01-04 → jan2, jan3 candidate → jan3 반환.
    result = repo.get_nav("069500", as_of=date(2024, 1, 4))
    assert result is not None
    assert result.effective_date == date(2024, 1, 3)


def test_fake_nav_repository_pit_no_record_before_asof() -> None:
    """as_of 이전 record 없으면 None."""
    rec = _make_nav_record(code="069500", effective_date=date(2024, 1, 5))
    repo = FakeNavRepository([rec])

    result = repo.get_nav("069500", as_of=date(2024, 1, 4))
    assert result is None


def test_fake_nav_repository_returns_none_for_unknown_code() -> None:
    """등록되지 않은 code → None."""
    repo = FakeNavRepository([])
    assert repo.get_nav("999999", as_of=date(2024, 1, 2)) is None


def test_fake_nav_repository_exact_date_match() -> None:
    """effective_date == as_of 인 경우 포함 (inclusive)."""
    rec = _make_nav_record(code="069500", effective_date=date(2024, 1, 3))
    repo = FakeNavRepository([rec])

    result = repo.get_nav("069500", as_of=date(2024, 1, 3))
    assert result is not None
    assert result.effective_date == date(2024, 1, 3)


# =============================================================================
# 9. FakeNavRepository save_navs — overwrite
# =============================================================================

def test_fake_nav_repository_save_navs_overwrite() -> None:
    """같은 (code, effective_date) 의 두 번째 save 가 첫 번째를 overwrite."""
    original = _make_nav_record(
        code="069500",
        effective_date=date(2024, 1, 2),
        nav=Decimal("31000"),
    )
    updated = _make_nav_record(
        code="069500",
        effective_date=date(2024, 1, 2),
        nav=Decimal("31100"),  # 수정된 값.
    )
    repo = FakeNavRepository([original])
    repo.save_navs([updated])

    result = repo.get_nav("069500", as_of=date(2024, 1, 2))
    assert result is not None
    assert result.nav == Decimal("31100")


def test_fake_nav_repository_save_navs_bulk_append() -> None:
    """다른 날짜 record 를 save 하면 모두 보관."""
    rec_jan2 = _make_nav_record(code="069500", effective_date=date(2024, 1, 2))
    repo = FakeNavRepository([])
    repo.save_navs([rec_jan2])

    rec_jan3 = _make_nav_record(code="069500", effective_date=date(2024, 1, 3))
    repo.save_navs([rec_jan3])

    # jan3 as_of → jan3 반환.
    assert repo.get_nav("069500", as_of=date(2024, 1, 3)) is not None
    # jan2 as_of → jan2 반환.
    result = repo.get_nav("069500", as_of=date(2024, 1, 2))
    assert result is not None
    assert result.effective_date == date(2024, 1, 2)


# =============================================================================
# 10. canonical_id 미등록 확인 — builtin/reference pack 에 nav 부재 (R4 deferred)
# =============================================================================

def test_builtin_factor_pack_has_no_nav_field() -> None:
    """builtin factor pack 에 ETF NAV 관련 field/factor 가 없음 (R4 deferred).

    speculum-builtin-v1.0.0.json 의 factors 목록에 'nav', 'etf_nav',
    'premium_discount' 등 ETF NAV 관련 identifier 가 부재 — canonical_id 미등록
    상태 확인 (ADR-0023 D8 원칙).
    """
    import pathlib

    pack_path = (
        pathlib.Path(__file__).parents[2]
        / "builtin-packs"
        / "factors"
        / "speculum-builtin-v1.0.0.json"
    )
    assert pack_path.exists(), f"builtin pack 파일 없음: {pack_path}"
    pack = json.loads(pack_path.read_text(encoding="utf-8"))

    nav_keywords = {"nav", "etf_nav", "premium_discount", "aum"}
    factor_ids: set[str] = set()
    for factor in pack.get("factors", []):
        fid = factor.get("id", "").lower()
        factor_ids.add(fid)
        slug = factor.get("slug", "").lower()
        factor_ids.add(slug)

    for kw in nav_keywords:
        matched = [fid for fid in factor_ids if kw in fid]
        assert not matched, (
            f"builtin factor pack 에 ETF NAV 관련 id '{kw}' 발견 — R4 deferred 위반: "
            f"{matched}"
        )
