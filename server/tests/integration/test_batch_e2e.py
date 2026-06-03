"""KrxDailyBatch / DartDailyBatch e2e 통합 test (M0_PLAN T43).

본 test 는 ADR-0003 D8 의 "integration marker = 외부 호출 only" 와는 별개의
의미. M0_PLAN T43 의 산출물 path = `server/tests/integration/`. 본 파일은:

    - 외부 호출 X (Fake adapter / SQLite in-memory).
    - 그러나 다중 모듈 (batch + SQL repository + ORM + DB schema) 의 합류점
      e2e 검증. unit test (test_batch/) 는 in-memory Fake repo 라 SQL FK /
      transactional 제약을 검증 안 함.

테스트 매트릭스:
    E1. dry_run=True + SqlPriceRepository → price/citation 테이블 row 0.
        실 SQL FK 가 정의된 환경에서 dry_run 의 의도 (DB unchanged) 가
        repository 추상화를 우회하지 않는지 검증.
    E2. commit path → price 테이블에 row 영구화 + alert.on_complete 호출.
    E3. conflict path (FDR mock close ≠ pykrx mock close) → alert.on_conflict
        호출 + price 영구화 + summary.conflicts.
    E4. DART dry_run → financial 테이블 row 0.
    E5. DART commit → financial 영구화 + citation FK 만족.

본 test 는 default suite (`-m "not integration"`) 에서 실행됨 — pytest 의
marker filter 가 `tests/integration/` 경로 자체를 차단하지 않으므로 (실제로
경로가 아닌 marker 만 filter). 단 외부 호출 test (test_pykrx_real /
test_fdr_real / test_dart_real) 는 pytestmark = pytest.mark.integration 명시
→ default deselect. 본 파일은 marker 미부여 → default 포함.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.fdr_adapter import FdrAdapter
from app.adapters.pykrx_adapter import PykrxAdapter
from app.db.base import Base
from app.db.orm import (  # noqa: F401 — Base.metadata 등록 side-effect
    CorporateActionORM,
    FinancialORM,
    MarketCapDailyORM,
    PriceDailyORM,
    ScreenerSetORM,
    ScreenRunSnapshotORM,
    SourceCitationORM,
    StocksMasterORM,
    WatchlistFolderORM,
    WatchlistItemORM,
)
from app.repositories.citation_repository import SqlCitationRepository
from app.repositories.sql_repositories import (
    SqlFinancialRepository,
    SqlMarketCapRepository,
    SqlPriceRepository,
)
from app.services.corp_code_mapping import CorpCodeMapping
from app.services.krx_calendar import DEFAULT_CALENDAR
from batch.alerts import BatchAlertHandler
from batch.dart_daily import DartDailyBatch
from batch.krx_daily import KrxDailyBatch

# =============================================================================
# 공통 fixture (tests/test_db/conftest.py 와 같은 패턴이지만 본 디렉토리
# scope. integration/ 의 conftest 는 외부 호출 가용성 검사 전용이라 별도)
# =============================================================================


@pytest.fixture
def db_engine() -> Any:
    """SQLite in-memory engine — tests/test_db/conftest.py 와 동일 패턴.

    별도 conftest 를 만들지 않고 본 파일 fixture 로 두는 이유: tests/integration/
    하위 conftest 는 외부 호출 가용성 검사 (network / DART_API_KEY) 전용이라
    본 e2e test 와 무관. 본 fixture 가 conftest 에 있으면 외부 호출 test 들도
    의도치 않게 SQLite engine 을 setup → 불필요한 비용.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _conn_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine):
    Sessionmaker = sessionmaker(
        bind=db_engine, expire_on_commit=False, autoflush=True,
    )
    with Sessionmaker() as session:
        try:
            yield session
        finally:
            session.rollback()


@pytest.fixture
def business_day() -> date:
    """v1.0.0 calendar 의 verified 영업일."""
    return date(2024, 5, 7)


# =============================================================================
# Mock helpers — test_batch_alerts 와 동형 (KRX OHLCV / market_cap / FDR)
# =============================================================================


def _ohlcv_df(*, day: date, close: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "시가": [close], "고가": [close], "저가": [close],
            "종가": [close], "거래량": [1_000_000], "거래대금": [close * 1_000_000],
        },
        index=pd.to_datetime([day]),
    )


def _market_cap_df(*, day: date) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "시가총액": [400_000_000_000_000],
            "거래량": [1_000_000],
            "거래대금": [400_000_000_000_000 * 1_000],
            "상장주식수": [5_000_000_000],
        },
        index=pd.to_datetime([day]),
    )


def _fdr_ohlcv_df(*, day: date, close: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [close], "High": [close], "Low": [close],
            "Close": [close], "Volume": [1_000_000], "Change": [0.0],
        },
        index=pd.to_datetime([day]),
    )


def _make_pykrx_mock(
    *,
    universe: list[str],
    ohlcv_close_by_code: dict[str, float] | None = None,
) -> Any:
    ohlcv_close_by_code = ohlcv_close_by_code or {}
    mock = MagicMock()

    def _ohlcv(fromdate: str, todate: str, code: str) -> pd.DataFrame:
        close = ohlcv_close_by_code.get(code, 70000.0)
        d = date.fromisoformat(f"{fromdate[:4]}-{fromdate[4:6]}-{fromdate[6:8]}")
        return _ohlcv_df(day=d, close=close)

    def _mc(fromdate: str, todate: str, code: str) -> pd.DataFrame:
        d = date.fromisoformat(f"{fromdate[:4]}-{fromdate[4:6]}-{fromdate[6:8]}")
        return _market_cap_df(day=d)

    mock.get_market_ticker_list = MagicMock(return_value=list(universe))
    mock.get_market_ohlcv = MagicMock(side_effect=_ohlcv)
    mock.get_market_cap_by_date = MagicMock(side_effect=_mc)
    return mock


def _make_fdr_mock(*, ohlcv_close_by_code: dict[str, float]) -> Any:
    def _data_reader(code: str, fromdate: str, todate: str) -> pd.DataFrame:
        close = ohlcv_close_by_code.get(code, 70000.0)
        return _fdr_ohlcv_df(day=date.fromisoformat(fromdate), close=close)

    mock = MagicMock()
    mock.DataReader = MagicMock(side_effect=_data_reader)
    return mock


class _CapturingHandler:
    """e2e test 의 alert 수신 — test_batch_alerts.CapturingAlertHandler 와 동일."""

    def __init__(self) -> None:
        self.conflicts: list[Any] = []
        self.failures: list[tuple[str, str]] = []
        self.completions: list[object] = []

    def on_conflict(self, conflict: Any) -> None:
        self.conflicts.append(conflict)

    def on_failure(self, code: str, reason: str) -> None:
        self.failures.append((code, reason))

    def on_complete(self, summary: object) -> None:
        self.completions.append(summary)


# =============================================================================
# E1. KRX dry_run → SQL price / citation 테이블 row 0
# =============================================================================


def test_krx_e2e_dry_run_persists_nothing(
    db_session: Session,
    business_day: date,
) -> None:
    handler = _CapturingHandler()
    pykrx_mock = _make_pykrx_mock(universe=["005930"])
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=SqlCitationRepository(db_session),
        price_repo=SqlPriceRepository(db_session),
        throttle_seconds=0,
        session=db_session,
        alert_handler=handler,
    )
    summary = batch.run(as_of=business_day, market="KOSPI", dry_run=True)

    assert summary.dry_run is True
    assert summary.success_count == 1
    # SQL 테이블 row 0 — dry_run 이 repository 추상화를 우회하지 않음.
    assert db_session.query(PriceDailyORM).count() == 0
    assert db_session.query(SourceCitationORM).count() == 0
    # alert 는 정상 발송 (검증 의도 보존).
    assert len(handler.completions) == 1


# =============================================================================
# E2. KRX commit → price 영구화 + alert.on_complete
# =============================================================================


def test_krx_e2e_commit_persists_price_and_citation(
    db_session: Session,
    business_day: date,
) -> None:
    handler = _CapturingHandler()
    pykrx_mock = _make_pykrx_mock(
        universe=["005930"],
        ohlcv_close_by_code={"005930": 70000.0},
    )
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=SqlCitationRepository(db_session),
        price_repo=SqlPriceRepository(db_session),
        throttle_seconds=0,
        session=db_session,
        alert_handler=handler,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    db_session.commit()  # SAVEPOINT 종료 후 outer commit 으로 영구화.

    assert summary.dry_run is False
    assert summary.success_count == 1
    # Price 1 row (005930, 2024-05-07).
    assert db_session.query(PriceDailyORM).count() == 1
    # Citation: universe(1) + ohlcv(1) + market_cap(1) = 3.
    assert db_session.query(SourceCitationORM).count() == 3
    assert len(handler.completions) == 1
    assert handler.conflicts == []
    assert handler.failures == []


# =============================================================================
# E2b. Phase B — SQL market_cap 영구화 + 실 FK (citation) 강제
# =============================================================================


def test_krx_e2e_commit_persists_market_cap(
    db_session: Session,
    business_day: date,
) -> None:
    """market_cap_repo (SQL) 주입 시 market_caps row 영구화 + citation FK 강제.

    SQLite PRAGMA foreign_keys=ON 으로 운영 PG 와 동일 FK behavior — citation 이
    먼저 save 돼야 market_cap FK 만족 (batch 의 citation → market_cap 순서 검증).
    """
    pykrx_mock = _make_pykrx_mock(
        universe=["005930"],
        ohlcv_close_by_code={"005930": 70000.0},
    )
    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=SqlCitationRepository(db_session),
        price_repo=SqlPriceRepository(db_session),
        market_cap_repo=SqlMarketCapRepository(db_session),
        throttle_seconds=0,
        session=db_session,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    db_session.commit()

    assert summary.success_count == 1
    # market_caps 1 row (005930, 2024-05-07).
    assert db_session.query(MarketCapDailyORM).count() == 1

    # fetch_latest 로 PIT round-trip — citation FK 만족 (commit 통과 = FK OK).
    from decimal import Decimal

    repo = SqlMarketCapRepository(db_session)
    record = repo.fetch_latest("005930", as_of=business_day)
    assert record is not None
    assert record.market_cap == Decimal("400000000000000")
    assert record.shares_outstanding == 5_000_000_000
    # pykrx 자사주 미제공 → None 보존.
    assert record.shares_treasury is None
    # citation_id 가 실재 (FK).
    assert db_session.get(SourceCitationORM, record.citation_id) is not None


# =============================================================================
# E3. conflict — FDR mismatch → alert.on_conflict + price persist
# =============================================================================


def test_krx_e2e_conflict_triggers_alert_and_still_persists(
    db_session: Session,
    business_day: date,
) -> None:
    """0.7% diff → on_conflict 호출. primary 데이터는 영구화 (운영 기본)."""
    handler = _CapturingHandler()
    pykrx_mock = _make_pykrx_mock(
        universe=["005930"],
        ohlcv_close_by_code={"005930": 70000.0},
    )
    fdr_mock = _make_fdr_mock(ohlcv_close_by_code={"005930": 70500.0})

    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=FdrAdapter(fdr_module=fdr_mock),
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=SqlCitationRepository(db_session),
        price_repo=SqlPriceRepository(db_session),
        throttle_seconds=0,
        session=db_session,
        alert_handler=handler,
    )
    summary = batch.run(as_of=business_day, market="KOSPI")
    db_session.commit()

    assert summary.success_count == 1
    # primary (pykrx) 데이터 영구화 — ADR-0003 D4 의 출처 우선순위 (1차 자료).
    assert db_session.query(PriceDailyORM).count() == 1
    # Conflict alert 1 회.
    assert len(handler.conflicts) == 1
    assert len(handler.conflicts[0].conflicts) > 0


# =============================================================================
# E4. DART dry_run → financial 테이블 row 0
# =============================================================================


def _make_dart_adapter_mock(*, account_count: int = 3) -> Any:
    """DartAdapter mock — test_batch_alerts 와 동형."""
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import uuid4

    from app.adapters.base import (
        FetchResult,
        FinancialStatementRow,
        IfrsType,
    )
    from app.adapters.dart_adapter import DartAdapter
    from app.models.source_citation import SourceCitation, SourceKind

    def _fetch(
        *, code: str, corp_code: str, fiscal_year: int,
        fiscal_quarter: int, ifrs_type: IfrsType, batch_id,
    ) -> FetchResult[tuple[FinancialStatementRow, ...]]:
        rows = tuple(
            FinancialStatementRow(
                code=code,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                effective_date=date(2024, 3, 31),
                account=f"account_{i}",
                value=Decimal("1000000"),
                unit="krw",
                ifrs_type=ifrs_type,
                rcept_no=f"{fiscal_year}0331{i:06d}",
                currency="KRW",
                effective_date_precise=True,
            )
            for i in range(account_count)
        )
        citation = SourceCitation(
            id=uuid4(),
            source=SourceKind.DART,
            identifier=f"{corp_code}|{fiscal_year}|{fiscal_quarter}|{ifrs_type.value}",
            url="https://opendart.fss.or.kr/",
            effective_date=date(2024, 3, 31),
            adapter_version="1.0.0",
            batch_id=batch_id,
            retrieved_at=datetime.now(UTC),
        )
        return FetchResult(data=rows, citations=(citation,))

    adapter = MagicMock(spec=DartAdapter)
    adapter.fetch_financial_statement = MagicMock(side_effect=_fetch)
    return adapter


def test_dart_e2e_dry_run_persists_nothing(db_session: Session) -> None:
    handler = _CapturingHandler()
    mapping = CorpCodeMapping.from_dict({"005930": "00126380"})
    adapter = _make_dart_adapter_mock(account_count=3)

    batch = DartDailyBatch(
        adapter=adapter,
        corp_mapping=mapping,
        citation_repo=SqlCitationRepository(db_session),
        financial_repo=SqlFinancialRepository(db_session),
        throttle_seconds=0,
        session=db_session,
        alert_handler=handler,
    )
    summary = batch.run(
        stock_codes=["005930"],
        fiscal_year=2023,
        fiscal_quarter=4,
        dry_run=True,
    )

    assert summary.dry_run is True
    assert summary.success_count == 1
    # CFS + OFS × 3 accounts = 6 row "fetched" but not persisted.
    assert summary.total_rows_saved == 6
    assert db_session.query(FinancialORM).count() == 0
    assert db_session.query(SourceCitationORM).count() == 0
    assert len(handler.completions) == 1


# =============================================================================
# E5. DART commit → financial + citation 영구화 + FK 만족
# =============================================================================


def test_dart_e2e_commit_persists_financial_with_citation_fk(
    db_session: Session,
) -> None:
    handler = _CapturingHandler()
    mapping = CorpCodeMapping.from_dict({"005930": "00126380"})
    adapter = _make_dart_adapter_mock(account_count=2)

    batch = DartDailyBatch(
        adapter=adapter,
        corp_mapping=mapping,
        citation_repo=SqlCitationRepository(db_session),
        financial_repo=SqlFinancialRepository(db_session),
        throttle_seconds=0,
        session=db_session,
        alert_handler=handler,
    )
    summary = batch.run(
        stock_codes=["005930"],
        fiscal_year=2023,
        fiscal_quarter=4,
    )
    db_session.commit()

    assert summary.dry_run is False
    assert summary.success_count == 1
    # CFS + OFS × 2 = 4 row 영구화.
    assert db_session.query(FinancialORM).count() == 4
    # Citation 2 개 (CFS 1 + OFS 1).
    assert db_session.query(SourceCitationORM).count() == 2
    # FK 만족 — SQLite PRAGMA foreign_keys=ON 환경에서 영구화 성공 자체가 FK 검증.
    # 추가 가드 — financial.citation_id 가 source_citations.id 와 매칭.
    citation_ids = {
        row.id for row in db_session.query(SourceCitationORM).all()
    }
    financial_citation_ids = {
        row.citation_id for row in db_session.query(FinancialORM).all()
    }
    assert financial_citation_ids.issubset(citation_ids)
    assert len(handler.completions) == 1


# =============================================================================
# E6. BatchAlertHandler Protocol — _CapturingHandler 가 contract 통과
# =============================================================================


def test_capturing_handler_satisfies_protocol() -> None:
    """runtime_checkable Protocol — duck typing 가능성 검증."""
    handler = _CapturingHandler()
    assert isinstance(handler, BatchAlertHandler)
