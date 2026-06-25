"""ensure_prices_cached — 주가 on-demand lazy fetch + 캐시 단위 테스트.

Fake price_repo + Fake citation_repo + Fake krx_adapter 로 외부 호출·DB 를 격리.
핵심 검증 = 갭만 fetch + PriceRecord 필드 매핑 + AdapterError degrade + 멱등.
실 네트워크 호출 0 (FakeKrxAdapter override).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

import pytest

from app.adapters.base import AdapterError, FetchResult, OHLCVRow
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.batch_run_repository import FakeBatchRunRepository
from app.repositories.citation_repository import FakeCitationRepository
from app.repositories.fakes import FakePriceRepository
from app.repositories.pit_protocols import PriceRecord
from app.services.lazy_price_fetch import ensure_prices_cached
from app.services.lineage import lineage_id_for_code

_CODE: Final[str] = "005930"
_NOW: Final[datetime] = datetime(2024, 1, 1, tzinfo=UTC)


def _citation(*, batch_id: UUID, effective_date: date) -> SourceCitation:
    return SourceCitation(
        id=uuid4(),
        source=SourceKind.PYKRX,
        identifier=f"{_CODE}|{effective_date.strftime('%Y%m%d')}",
        retrieved_at=_NOW,
        effective_date=effective_date,
        adapter_version="1.0.0",
        batch_id=batch_id,
        url="https://example/pykrx",
    )


def _ohlcv(*, trade_date: date, close: str = "80000") -> OHLCVRow:
    return OHLCVRow(
        code=_CODE,
        trade_date=trade_date,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=1000,
        value=Decimal("80000000"),
    )


class FakeKrxAdapter:
    """fetch_ohlcv_by_date_range 가 주어진 rows + citation 을 반환하는 stub.

    `raise_error=True` 면 AdapterError raise(갭에 거래일 없음/일시 오류 모사).
    호출 인자(fromdate/todate)를 기록해 갭 fetch 검증에 사용.
    """

    def __init__(
        self,
        *,
        rows: tuple[OHLCVRow, ...] = (),
        raise_error: bool = False,
    ) -> None:
        self._rows = rows
        self._raise_error = raise_error
        self.calls: list[tuple[date, date]] = []

    def fetch_ohlcv_by_date_range(
        self,
        code: str,  # noqa: ARG002
        *,
        fromdate: date,
        todate: date,
        batch_id: UUID,  # noqa: ARG002
    ) -> FetchResult[tuple[OHLCVRow, ...]]:
        self.calls.append((fromdate, todate))
        if self._raise_error:
            raise AdapterError("no trading day in gap")
        rows = tuple(r for r in self._rows if fromdate <= r.trade_date <= todate)
        citation = _citation(batch_id=uuid4(), effective_date=todate)
        return FetchResult(data=rows, citations=(citation,))


def _price_record(*, d: date, close: str = "70000") -> PriceRecord:
    return PriceRecord(
        id=uuid4(),
        code=_CODE,
        code_lineage_id=lineage_id_for_code(_CODE),
        effective_date=d,
        open_raw=Decimal(close),
        high_raw=Decimal(close),
        low_raw=Decimal(close),
        close_raw=Decimal(close),
        volume=500,
        trading_value=Decimal("35000000"),
        close_adjusted=Decimal(close),
        citation_id=uuid4(),
        created_at=_NOW,
    )


def test_empty_db_fetches_and_saves_mapped_records() -> None:
    """DB 비어있음 → fetch 호출되고 변환된 PriceRecord 가 저장(필드 매핑 정확)."""
    price_repo = FakePriceRepository(records=())
    citation_repo = FakeCitationRepository()
    rows = (
        _ohlcv(trade_date=date(2024, 6, 26), close="80000"),
        _ohlcv(trade_date=date(2024, 6, 27), close="80500"),
    )
    adapter = FakeKrxAdapter(rows=rows)

    ensure_prices_cached(
        _CODE,
        start=date(2024, 6, 25),
        end=date(2024, 6, 27),
        price_repo=price_repo,
        citation_repo=citation_repo,
        krx_adapter=adapter,
        batch_run_repo=FakeBatchRunRepository(),
    )

    # 갭 전체(start~end) fetch.
    assert adapter.calls == [(date(2024, 6, 25), date(2024, 6, 27))]
    # 변환·저장 확인 (PIT 필터 위해 넉넉한 as_of/start).
    saved = price_repo.fetch_prices(
        _CODE, as_of=date(2024, 6, 27), start=date(2024, 6, 1),
    )
    assert [r.effective_date for r in saved] == [
        date(2024, 6, 26), date(2024, 6, 27),
    ]
    first = saved[0]
    assert first.code == _CODE
    assert first.code_lineage_id == lineage_id_for_code(_CODE)
    assert first.open_raw == Decimal("80000")
    assert first.close_raw == Decimal("80000")
    # close_adjusted=raw (T20 미적용).
    assert first.close_adjusted == first.close_raw
    assert first.volume == 1000
    assert first.trading_value == Decimal("80000000")
    # citation 저장됨 + FK 연결.
    assert citation_repo.fetch_by_id(first.citation_id) is not None


def test_already_covered_skips_fetch() -> None:
    """DB 가 end 까지 커버 → fetch_from > end early return (fetch 미호출)."""
    price_repo = FakePriceRepository(
        records=(
            _price_record(d=date(2024, 6, 26)),
            _price_record(d=date(2024, 6, 27)),
        ),
    )
    citation_repo = FakeCitationRepository()
    adapter = FakeKrxAdapter(rows=())

    ensure_prices_cached(
        _CODE,
        start=date(2024, 6, 25),
        end=date(2024, 6, 27),
        price_repo=price_repo,
        citation_repo=citation_repo,
        krx_adapter=adapter,
        batch_run_repo=FakeBatchRunRepository(),
    )

    assert adapter.calls == []  # 외부 호출 없음.


def test_partial_db_fetches_only_gap() -> None:
    """DB 가 end 이전까지만 → db_max+1 부터 end 까지 갭만 fetch."""
    price_repo = FakePriceRepository(
        records=(_price_record(d=date(2024, 6, 25)),),
    )
    citation_repo = FakeCitationRepository()
    rows = (_ohlcv(trade_date=date(2024, 6, 27)),)
    adapter = FakeKrxAdapter(rows=rows)

    ensure_prices_cached(
        _CODE,
        start=date(2024, 6, 20),
        end=date(2024, 6, 27),
        price_repo=price_repo,
        citation_repo=citation_repo,
        krx_adapter=adapter,
        batch_run_repo=FakeBatchRunRepository(),
    )

    # db_max=2024-06-25 → fetch_from=2024-06-26.
    assert adapter.calls == [(date(2024, 6, 26), date(2024, 6, 27))]


def test_adapter_error_degrades_without_raise() -> None:
    """AdapterError → 예외 전파 안 함(degrade), DB 무변경."""
    price_repo = FakePriceRepository(records=())
    citation_repo = FakeCitationRepository()
    adapter = FakeKrxAdapter(raise_error=True)

    # raise 하지 않아야 함.
    ensure_prices_cached(
        _CODE,
        start=date(2024, 6, 25),
        end=date(2024, 6, 27),
        price_repo=price_repo,
        citation_repo=citation_repo,
        krx_adapter=adapter,
        batch_run_repo=FakeBatchRunRepository(),
    )

    saved = price_repo.fetch_prices(
        _CODE, as_of=date(2024, 6, 27), start=date(2024, 6, 1),
    )
    assert saved == []  # 무변경.


def test_empty_result_no_save() -> None:
    """빈 data → citation/record 저장 없이 조용히 return."""
    price_repo = FakePriceRepository(records=())
    citation_repo = FakeCitationRepository()
    adapter = FakeKrxAdapter(rows=())  # 갭에 매칭 row 없음.

    ensure_prices_cached(
        _CODE,
        start=date(2024, 6, 25),
        end=date(2024, 6, 27),
        price_repo=price_repo,
        citation_repo=citation_repo,
        krx_adapter=adapter,
        batch_run_repo=FakeBatchRunRepository(),
    )

    saved = price_repo.fetch_prices(
        _CODE, as_of=date(2024, 6, 27), start=date(2024, 6, 1),
    )
    assert saved == []


def test_idempotent_double_call() -> None:
    """두 번 호출해도 안전(save_prices ON CONFLICT first-wins, 중복 없음)."""
    price_repo = FakePriceRepository(records=())
    citation_repo = FakeCitationRepository()
    rows = (
        _ohlcv(trade_date=date(2024, 6, 26)),
        _ohlcv(trade_date=date(2024, 6, 27)),
    )

    # 1차: 빈 DB → 둘 다 fetch·save.
    ensure_prices_cached(
        _CODE,
        start=date(2024, 6, 25),
        end=date(2024, 6, 27),
        price_repo=price_repo,
        citation_repo=citation_repo,
        krx_adapter=FakeKrxAdapter(rows=rows),
        batch_run_repo=FakeBatchRunRepository(),
    )
    # 2차: 이미 end 까지 커버 → fetch skip(early return), 중복 없음.
    second_adapter = FakeKrxAdapter(rows=rows)
    ensure_prices_cached(
        _CODE,
        start=date(2024, 6, 25),
        end=date(2024, 6, 27),
        price_repo=price_repo,
        citation_repo=citation_repo,
        krx_adapter=second_adapter,
        batch_run_repo=FakeBatchRunRepository(),
    )

    assert second_adapter.calls == []  # 2차는 갭 없음.
    saved = price_repo.fetch_prices(
        _CODE, as_of=date(2024, 6, 27), start=date(2024, 6, 1),
    )
    assert len(saved) == 2  # 중복 적재 없음.


@pytest.mark.parametrize("close", ["12345.67", "0.01"])
def test_decimal_values_preserved(close: str) -> None:
    """Decimal 가격이 손실 없이 PriceRecord 로 전달."""
    price_repo = FakePriceRepository(records=())
    citation_repo = FakeCitationRepository()
    rows = (_ohlcv(trade_date=date(2024, 6, 26), close=close),)
    ensure_prices_cached(
        _CODE,
        start=date(2024, 6, 26),
        end=date(2024, 6, 26),
        price_repo=price_repo,
        citation_repo=citation_repo,
        krx_adapter=FakeKrxAdapter(rows=rows),
        batch_run_repo=FakeBatchRunRepository(),
    )
    saved = price_repo.fetch_prices(
        _CODE, as_of=date(2024, 6, 26), start=date(2024, 6, 26),
    )
    assert saved[0].close_raw == Decimal(close)
