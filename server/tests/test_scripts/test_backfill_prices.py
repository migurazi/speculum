"""scripts.backfill_prices 단위 테스트 — OHLCVRow→PriceRecord 변환 + main() 적재.

`_to_price_records`(순수 변환) + main() 의 fake PykrxAdapter → tmp 파일 SQLite
적재 흐름. 네트워크 무관(PykrxAdapter monkeypatch). SPECULUM_DATABASE_URL 을 tmp
파일로 주입해 스크립트의 create_engine_from_url 실경로를 그대로 검증.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.adapters.base import FetchResult, OHLCVRow
from app.db.base import Base
from app.db.orm import PriceDailyORM  # noqa: F401  (metadata 등록)
from app.models.source_citation import SourceCitation, SourceKind
from scripts import backfill_prices as mod

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


def _ohlcv(code: str, d: date, close: str) -> OHLCVRow:
    c = Decimal(close)
    return OHLCVRow(
        code=code, trade_date=d, open=c, high=c, low=c, close=c,
        volume=1000, value=Decimal("1000000"),
    )


def _citation(batch_id: UUID) -> SourceCitation:
    return SourceCitation(
        id=uuid4(),
        source=SourceKind.PYKRX,
        identifier="ohlcv|005930|2024",
        retrieved_at=_NOW,
        effective_date=date(2024, 6, 28),
        adapter_version="1.0.0",
        batch_id=batch_id,
        url="https://data.krx.co.kr/ohlcv",
    )


# =============================================================================
# _to_price_records — 순수 변환
# =============================================================================

def test_to_price_records_maps_fields() -> None:
    """OHLCVRow → PriceRecord. close_adjusted == close_raw(T20 미적용)."""
    cid = uuid4()
    rows = [
        _ohlcv("005930", date(2024, 6, 27), "80000"),
        _ohlcv("005930", date(2024, 6, 28), "81500"),
    ]
    recs = mod._to_price_records(rows, cid)
    assert len(recs) == 2
    r = recs[1]
    assert r.code == "005930"
    assert r.effective_date == date(2024, 6, 28)
    assert r.close_raw == Decimal("81500")
    # T20 미적용 — 보정 종가는 raw 동일.
    assert r.close_adjusted == r.close_raw
    assert r.citation_id == cid
    assert r.trading_value == Decimal("1000000")


# =============================================================================
# main() — fake adapter + in-memory SQLite
# =============================================================================

class _FakePykrx:
    """fetch_ohlcv_by_date_range 만 구현. raise_for 등록 code 는 AdapterError."""

    def __init__(self, *, by_code: dict[str, list[OHLCVRow]], raise_for=None) -> None:
        self._by_code = by_code
        self._raise_for = raise_for or {}

    def fetch_ohlcv_by_date_range(self, code, *, fromdate, todate, batch_id):
        if code in self._raise_for:
            raise self._raise_for[code]
        rows = self._by_code.get(code, [])
        cits = (_citation(batch_id),) if rows else ()
        return FetchResult(
            data=tuple(rows), citations=cits, warnings=(),
            estimated_fields=frozenset(),
        )


@pytest.fixture
def _db_url(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    """tmp 파일 SQLite(schema 생성) + SPECULUM_DATABASE_URL 주입.

    스크립트는 get_database_url → create_engine_from_url(url) 로 자체 engine 을
    만들어 적재 후 dispose 한다. in-memory 는 dispose 시 DB 가 소멸하므로 **파일
    DB** 를 써서 검증이 같은 데이터를 본다(운영 경로 그대로 — create_engine_from_url
    실호출). FK PRAGMA 는 운영 동일(미강제, citation.batch_id orphan 허용).
    """
    db_path = tmp_path / "test_backfill.db"
    url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    engine.dispose()
    monkeypatch.setenv("SPECULUM_DATABASE_URL", url)
    return url


def _count_prices(url: str, code: str | None = None) -> int:
    engine = create_engine(url, future=True)
    try:
        with sessionmaker(bind=engine, future=True)() as s:
            stmt = select(func.count()).select_from(PriceDailyORM)
            if code is not None:
                stmt = stmt.where(PriceDailyORM.code == code)
            return s.scalar(stmt)
    finally:
        engine.dispose()


def test_main_backfills_prices(
    _db_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fake OHLCV 2종목 → main() 이 prices_daily 에 실 적재."""
    fake = _FakePykrx(by_code={
        "005930": [
            _ohlcv("005930", date(2024, 6, 27), "80000"),
            _ohlcv("005930", date(2024, 6, 28), "81500"),
        ],
        "000660": [_ohlcv("000660", date(2024, 6, 28), "236500")],
    })
    monkeypatch.setattr(mod, "PykrxAdapter", lambda: fake)

    rc = mod.main(["005930", "000660", "--as-of", "2024-06-28", "--days", "365"])
    assert rc == 0
    assert _count_prices(_db_url) == 3
    assert _count_prices(_db_url, "005930") == 2


def test_main_skips_adapter_error(
    _db_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한 종목 AdapterError → skip, 다른 종목 적재(전체 abort X)."""
    from app.adapters.base import AdapterError
    fake = _FakePykrx(
        by_code={"005930": [_ohlcv("005930", date(2024, 6, 28), "81500")]},
        raise_for={"000660": AdapterError("pykrx down")},
    )
    monkeypatch.setattr(mod, "PykrxAdapter", lambda: fake)

    rc = mod.main(["005930", "000660", "--as-of", "2024-06-28"])
    assert rc == 0  # 1종목이라도 성공 → 0.
    assert _count_prices(_db_url) == 1


def test_main_idempotent_rerun(
    _db_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재실행 → save_prices first-wins(ON CONFLICT) 멱등, 중복 0."""
    fake = _FakePykrx(by_code={
        "005930": [_ohlcv("005930", date(2024, 6, 28), "81500")],
    })
    monkeypatch.setattr(mod, "PykrxAdapter", lambda: fake)

    mod.main(["005930", "--as-of", "2024-06-28"])
    mod.main(["005930", "--as-of", "2024-06-28"])  # 2nd run.
    assert _count_prices(_db_url) == 1  # 2건 아님(멱등).
