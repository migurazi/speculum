"""V0 관통 e2e — 캡처 실 OHLCV → lazy-fetch → 실 SQLite → 차트 serve (ROADMAP_v2 V0).

이 프로젝트의 기존 단위 테스트는 전부 합성 Fake fixture 위에서 producer↔consumer 를
자기검증(tautology)해, schema drift·실데이터 결손이 green 안에 은폐됐다(oracle 기획
검토 §4). 본 테스트는 그 tautology 를 깨는 V0 acceptance gate 다:

  실제 KRX 응답을 캡처한 fixture(`tests/fixtures/captured/krx_ohlcv_005930.json`,
  삼성전자 2026-06 실 OHLCV — 손제작 아님) → `ensure_prices_cached`(lazy-fetch 서비스)
  → **실 in-memory SQLite**(SqlPriceRepository/SqlCitationRepository, Fake 아님)
  → `fetch_prices` 로 차트가 보는 bars 를 serve → **bars≥1 + 실값** 검증.

즉 "거울이 비춘다" — 캡처 실데이터가 적재→저장→serve 를 관통해 차트 bars 로 나오는지를
**실 DB 경로**로 잠근다. fixture 를 consumer 기대값이 아니라 **adapter 출력(실 캡처)**
에서 유도하므로 tautology 가 아니다. 외부 네트워크 호출 0(캡처본 재생).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.adapters.base import FetchResult, OHLCVRow
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.batch_run_repository import SqlBatchRunRepository
from app.repositories.citation_repository import SqlCitationRepository
from app.repositories.sql_repositories import SqlPriceRepository
from app.services.lazy_price_fetch import ensure_prices_cached

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "captured" / "krx_ohlcv_005930.json"
)


def _load_captured_rows() -> tuple[OHLCVRow, ...]:
    """캡처 fixture JSON → OHLCVRow tuple (adapter 출력 형태 그대로)."""
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return tuple(
        OHLCVRow(
            code=r["code"],
            trade_date=date.fromisoformat(r["trade_date"]),
            open=Decimal(r["open"]),
            high=Decimal(r["high"]),
            low=Decimal(r["low"]),
            close=Decimal(r["close"]),
            volume=int(r["volume"]),
            value=Decimal(r["value"]),
        )
        for r in payload["rows"]
    )


class _ReplayKrxAdapter:
    """캡처된 OHLCVRow 를 [fromdate,todate] 로 필터해 반환하는 재생 어댑터.

    실 PykrxAdapter 와 동일 인터페이스(`fetch_ohlcv_by_date_range`). 실 네트워크
    대신 캡처본을 재생 — V0 e2e 가 외부 의존 없이 default suite 에서 green.
    """

    def __init__(self, rows: tuple[OHLCVRow, ...]) -> None:
        self._rows = rows

    def fetch_ohlcv_by_date_range(
        self,
        code: str,  # noqa: ARG002
        *,
        fromdate: date,
        todate: date,
        batch_id: UUID,
    ) -> FetchResult[tuple[OHLCVRow, ...]]:
        rows = tuple(r for r in self._rows if fromdate <= r.trade_date <= todate)
        citation = SourceCitation(
            id=uuid4(),
            source=SourceKind.PYKRX,
            identifier=f"005930|{fromdate:%Y%m%d}|{todate:%Y%m%d}|ohlcv",
            retrieved_at=datetime(2026, 6, 25, tzinfo=UTC),
            effective_date=todate,
            adapter_version="1.0.0",
            batch_id=batch_id,
            url=None,
        )
        return FetchResult(data=rows, citations=(citation,))


def test_v0_captured_ohlcv_flows_to_chart_bars(db_session: Session) -> None:
    """캡처 실 OHLCV → lazy-fetch → 실 SQLite → fetch_prices bars≥1 + 실값.

    V0 acceptance — "거울이 비춘다". 실 DB 경로(Fake 아님)로 적재→serve 관통.
    """
    rows = _load_captured_rows()
    assert rows, "캡처 fixture 가 비어있음 — ground truth 부재"
    captured_last = rows[-1]  # 2026-06-25 실 종가(삼성전자 ~359,000원대)

    # 빈 DB → lazy-fetch 가 캡처 구간 전체를 적재(실 SqlPriceRepository/SqlCitationRepository).
    start = rows[0].trade_date
    end = rows[-1].trade_date
    ensure_prices_cached(
        "005930",
        start=start,
        end=end,
        price_repo=SqlPriceRepository(db_session),
        citation_repo=SqlCitationRepository(db_session),
        krx_adapter=_ReplayKrxAdapter(rows),
        batch_run_repo=SqlBatchRunRepository(db_session),
    )
    db_session.commit()  # DEFERRABLE FK(citation) 최종 검사 — flush 만으론 부족.

    # serve — 차트 endpoint 의 데이터 소스(SqlPriceRepository.fetch_prices).
    served = SqlPriceRepository(db_session).fetch_prices(
        "005930", as_of=end, start=start,
    )

    # AC: 캡처 실데이터가 bars 로 관통(거울이 비춤).
    assert len(served) == len(rows), "적재된 bars 수가 캡처와 불일치"
    assert len(served) >= 1
    # 실값 보존 — 손제작이 아닌 캡처 실 종가가 그대로 serve.
    served_last = served[-1]
    assert served_last.effective_date == captured_last.trade_date
    assert served_last.close_raw == captured_last.close
    assert served_last.close_raw > Decimal("0")
    # close_adjusted=raw(T20 미적용, read-time PriceAdjuster 가 보정).
    assert served_last.close_adjusted == served_last.close_raw
    # citation FK 연결(실 DB FK 통과).
    assert served_last.citation_id is not None


def test_v0_lazy_fetch_idempotent_on_real_db(db_session: Session) -> None:
    """두 번 적재해도 실 DB 에서 중복 없음(save_prices ON CONFLICT first-wins)."""
    rows = _load_captured_rows()
    start, end = rows[0].trade_date, rows[-1].trade_date

    for _ in range(2):
        ensure_prices_cached(
            "005930",
            start=start,
            end=end,
            price_repo=SqlPriceRepository(db_session),
            citation_repo=SqlCitationRepository(db_session),
            krx_adapter=_ReplayKrxAdapter(rows),
            batch_run_repo=SqlBatchRunRepository(db_session),
        )
        db_session.commit()

    served = SqlPriceRepository(db_session).fetch_prices(
        "005930", as_of=end, start=start,
    )
    assert len(served) == len(rows)  # 중복 적재 0.
