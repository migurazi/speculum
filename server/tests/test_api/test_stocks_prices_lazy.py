"""GET /api/stocks/{code}/prices — on-demand lazy fetch + 캘린더 cap 우회 API 테스트.

`app.state.pykrx_adapter_override` 로 FakeKrxAdapter 를 주입(실 네트워크 0). 검증:
- 2026 날짜 요청이 400 아님(캘린더 cap 우회) + lazy fetch 후 bars 반환.
- 미래 날짜(kst_today()+1) → 400.
- override 미설정(기본 create_app) → lazy 미발동, 기존 동작(DB 만).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.adapters.base import FetchResult, OHLCVRow
from app.main import create_app
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.fakes import FakePriceRepository
from app.services.krx_calendar import kst_today

_CODE: Final[str] = "005930"
_NOW: Final[datetime] = datetime(2024, 1, 1, tzinfo=UTC)


class FakeKrxAdapter:
    """fetch_ohlcv_by_date_range 가 todate 기준 OHLCVRow 1건을 반환하는 stub."""

    def __init__(self, *, close: str = "90000") -> None:
        self._close = close

    def fetch_ohlcv_by_date_range(
        self,
        code: str,
        *,
        fromdate: date,
        todate: date,
        batch_id: UUID,  # noqa: ARG002
    ) -> FetchResult[tuple[OHLCVRow, ...]]:
        row = OHLCVRow(
            code=code,
            trade_date=todate,
            open=Decimal(self._close),
            high=Decimal(self._close),
            low=Decimal(self._close),
            close=Decimal(self._close),
            volume=1000,
            value=Decimal("90000000"),
        )
        citation = SourceCitation(
            id=uuid4(),
            source=SourceKind.PYKRX,
            identifier=f"{code}|{todate.strftime('%Y%m%d')}",
            retrieved_at=_NOW,
            effective_date=todate,
            adapter_version="1.0.0",
            batch_id=uuid4(),
            url="https://example/pykrx",
        )
        return FetchResult(data=(row,), citations=(citation,))


def test_2026_date_bypasses_calendar_cap_and_lazy_fetches() -> None:
    """2026 날짜 요청 → 400 아님(캘린더 우회) + lazy fetch 후 bars 반환."""
    price_repo = FakePriceRepository(records=())
    app = create_app(price_repository=price_repo)
    app.state.pykrx_adapter_override = FakeKrxAdapter(close="90000")
    with TestClient(app) as c:
        res = c.get(f"/api/stocks/{_CODE}/prices?as_of=2026-06-20&days=5")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["as_of"] == "2026-06-20"
        # lazy fetch 가 todate(2026-06-20) bar 를 적재 → 반환.
        dates = [b["date"] for b in body["bars"]]
        assert "2026-06-20" in dates
        bar = next(b for b in body["bars"] if b["date"] == "2026-06-20")
        assert Decimal(bar["close"]) == Decimal("90000")
        # close_adjusted=raw.
        assert Decimal(bar["close_adjusted"]) == Decimal("90000")


def test_future_date_rejected_400() -> None:
    """미래 날짜(kst_today()+1) → 400."""
    price_repo = FakePriceRepository(records=())
    app = create_app(price_repository=price_repo)
    app.state.pykrx_adapter_override = FakeKrxAdapter()
    future = (kst_today() + timedelta(days=1)).isoformat()
    with TestClient(app) as c:
        res = c.get(f"/api/stocks/{_CODE}/prices?as_of={future}")
        assert res.status_code == 400
        assert "미래" in res.json()["detail"]


def test_no_override_no_lazy_fetch_db_only() -> None:
    """override 미설정(기본 create_app) → lazy 미발동, DB 에 있는 것만 반환."""
    lineage = uuid4()
    cid = uuid4()
    existing = (
        _price_record(d=date(2024, 6, 27), lineage=lineage, cid=cid),
    )
    price_repo = FakePriceRepository(records=existing)
    app = create_app(price_repository=price_repo)  # pykrx adapter override 없음.
    with TestClient(app) as c:
        res = c.get(f"/api/stocks/{_CODE}/prices?as_of=2024-06-28")
        assert res.status_code == 200
        body = res.json()
        dates = [b["date"] for b in body["bars"]]
        assert dates == ["2024-06-27"]  # DB 에 있던 것만 — 새 적재 없음.


def test_none_as_of_defaults_to_kst_today() -> None:
    """as_of 미지정 → KST 오늘로 default(미래 거부에 안 걸림)."""
    price_repo = FakePriceRepository(records=())
    app = create_app(price_repository=price_repo)
    app.state.pykrx_adapter_override = FakeKrxAdapter()
    with TestClient(app) as c:
        res = c.get(f"/api/stocks/{_CODE}/prices?days=5")
        assert res.status_code == 200
        assert res.json()["as_of"] == kst_today().isoformat()


def _price_record(*, d: date, lineage: UUID, cid: UUID) -> object:
    from app.repositories.pit_protocols import PriceRecord
    return PriceRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=lineage,
        effective_date=d,
        open_raw=Decimal("80000"),
        high_raw=Decimal("80000"),
        low_raw=Decimal("80000"),
        close_raw=Decimal("80000"),
        volume=500,
        trading_value=Decimal("40000000"),
        close_adjusted=Decimal("80000"),
        citation_id=cid,
        created_at=_NOW,
    )
