"""T18/T19/KOSIS M1 통합 테스트 — SQLite 위에서 종목/회사/지표별 SAVEPOINT rollback.

oracle T18/T19 M1 — 일배치의 종목/회사 처리 중 DB write 실패 시 partial
commit 차단. session.begin_nested() SAVEPOINT 가 정확히 그 종목/회사의
write 만 rollback.

테스트 매트릭스:
    1. KrxDailyBatch — 종목 1 성공 + 종목 2 의 save 단계 실패 → 종목 1 만
       DB 에 영구화 (종목 2 의 in-flight citation 도 rollback).
    2. DartDailyBatch — 회사 1 성공 + 회사 2 의 financial save 실패 → 회사 1
       만 영구화.
    3. session=None (Fake mode) — savepoint 없이 기존 동작 (역호환).
    4. KosisDailyBatch idempotent SAVEPOINT — 같은 observed_date 2회 run:
       1차 적재 성공, 2차는 지표별 UNIQUE IntegrityError → skip, PendingRollbackError
       없이 지표 2·3 정상 처리 (연쇄 실패 0). Critical 버그 재현·검증.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import httpx
import pandas as pd
from sqlalchemy.orm import Session

from app.adapters.dart_adapter import DartAdapter
from app.adapters.pykrx_adapter import PykrxAdapter
from app.repositories.citation_repository import SqlCitationRepository
from app.repositories.sql_repositories import (
    SqlFinancialRepository,
    SqlPriceRepository,
)
from app.services.corp_code_mapping import CorpCodeMapping
from app.services.krx_calendar import DEFAULT_CALENDAR
from batch.dart_daily import DartDailyBatch
from batch.krx_daily import KrxDailyBatch

# =============================================================================
# T18 — KrxDailyBatch savepoint
# =============================================================================

def _ohlcv_df(*, day: date, close: float) -> pd.DataFrame:
    """pykrx OHLCV 한 row DataFrame."""
    return pd.DataFrame(
        {
            "시가": [close], "고가": [close], "저가": [close],
            "종가": [close], "거래량": [1_000_000],
            "거래대금": [close * 1_000_000],
        },
        index=pd.to_datetime([day]),
    )


def _market_cap_df(*, day: date) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "시가총액": [400_000_000_000_000],
            "거래량": [1_000_000],
            "거래대금": [400_000_000_000],
            "상장주식수": [5_000_000_000],
        },
        index=pd.to_datetime([day]),
    )


def test_krx_savepoint_rolls_back_failed_code_only(
    db_session: Session,
) -> None:
    """oracle T18 M1 — 종목 2 save 실패 시 종목 1 의 citation/price 는 보존.

    종목 2 는 fetch 까지 성공한 후 save_prices 가 IntegrityError. SAVEPOINT
    ROLLBACK 으로 종목 2 의 citation 도 rollback. 최종 citation_repo 에는
    종목 1 의 citation 만.
    """
    # pykrx mock — 두 종목 모두 fetch 성공.
    pykrx_mock = MagicMock()
    pykrx_mock.get_market_ticker_list = MagicMock(
        return_value=["005930", "000660"],
    )
    pykrx_mock.get_market_ohlcv = MagicMock(
        side_effect=lambda fromdate, todate, code: _ohlcv_df(
            day=date(2024, 5, 7), close=70000.0,
        ),
    )
    pykrx_mock.get_market_cap_by_date = MagicMock(
        side_effect=lambda fromdate, todate, code: _market_cap_df(
            day=date(2024, 5, 7),
        ),
    )

    citation_repo = SqlCitationRepository(db_session)
    price_repo = SqlPriceRepository(db_session)

    # save_prices 가 종목 000660 일 때만 raise — 종목 1 (005930) 성공 후 종목 2
    # 의 save 단계에서 실패.
    original_save = price_repo.save_prices

    def _failing_save(records: Any) -> None:
        if records and records[0].code == "000660":
            raise RuntimeError("simulated DB write failure on 000660")
        original_save(records)

    price_repo.save_prices = _failing_save  # type: ignore[method-assign]

    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=citation_repo,
        price_repo=price_repo,
        throttle_seconds=0,
        session=db_session,  # SAVEPOINT 활성.
    )
    summary = batch.run(as_of=date(2024, 5, 7), market="KOSPI")

    # 종목 1 만 성공.
    assert summary.success_count == 1
    assert summary.failure_count == 1
    assert summary.failures[0][0] == "000660"

    # 영구화된 price = 종목 005930 만.
    prices_005930 = price_repo.fetch_prices(
        "005930", as_of=date(2024, 5, 7), start=date(2024, 5, 7),
    )
    assert len(prices_005930) == 1
    prices_000660 = price_repo.fetch_prices(
        "000660", as_of=date(2024, 5, 7), start=date(2024, 5, 7),
    )
    assert len(prices_000660) == 0  # rollback 됨.

    # 영구화된 citation = universe 1 + 종목 005930 의 OHLCV+market_cap 2 = 3.
    # 종목 000660 의 in-flight citation 은 rollback (SAVEPOINT 효과).
    cited = citation_repo.fetch_by_batch(summary.batch_id)
    assert len(cited) == 3
    identifiers = {c.identifier for c in cited}
    assert any("005930" in i for i in identifiers)
    assert not any("000660" in i for i in identifiers)


def test_krx_full_rerun_idempotent_no_partial(
    db_session: Session,
) -> None:
    """KRX 일배치 전체 재실행 회귀가드 — 재실행이 PARTIAL 로 오분류되지 않음.

    현 버그 재현·수정 검증: 같은 (code, effective_date) 재insert 시 PK 충돌
    IntegrityError → `except Exception` 이 failure 로 오분류 → BATCH_STATUS_PARTIAL
    로 저장 → data_freshness latest_batch_partial=True 왜곡.

    repository-level ON CONFLICT DO NOTHING 으로 충돌이 흡수되어 재실행도
    failure_count=0, status=SUCCESS, latest_batch_partial=False 유지(이게 핵심).
    """
    from app.repositories.batch_run_repository import (
        BATCH_STATUS_SUCCESS,
        SqlBatchRunRepository,
    )
    from app.repositories.sql_repositories import SqlMarketCapRepository
    from app.services.data_freshness import assess_data_freshness

    as_of = date(2024, 5, 7)
    pykrx_mock = MagicMock()
    pykrx_mock.get_market_ticker_list = MagicMock(
        return_value=["005930", "000660"],
    )
    pykrx_mock.get_market_ohlcv = MagicMock(
        side_effect=lambda fromdate, todate, code: _ohlcv_df(
            day=as_of, close=70000.0,
        ),
    )
    pykrx_mock.get_market_cap_by_date = MagicMock(
        side_effect=lambda fromdate, todate, code: _market_cap_df(day=as_of),
    )

    def _make_batch() -> KrxDailyBatch:
        return KrxDailyBatch(
            primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
            verify_adapter=None,
            conflict_detector=None,
            calendar=DEFAULT_CALENDAR,
            citation_repo=SqlCitationRepository(db_session),
            price_repo=SqlPriceRepository(db_session),
            market_cap_repo=SqlMarketCapRepository(db_session),
            throttle_seconds=0,
            session=db_session,  # SAVEPOINT + SqlBatchRunRepository auto-create.
        )

    # ── 1차 적재 ─────────────────────────────────────────────────────────────
    summary1 = _make_batch().run(as_of=as_of, market="KOSPI")
    assert summary1.failure_count == 0
    assert summary1.success_count == 2

    # ── 2차 동일 run() 재실행 (현 버그 회귀 가드) ────────────────────────────
    # PK 중복이 ON CONFLICT DO NOTHING 으로 흡수 → failure 0, SUCCESS 유지.
    summary2 = _make_batch().run(as_of=as_of, market="KOSPI")
    assert summary2.failure_count == 0, (
        f"재실행 failure 0 이어야 함 (got {summary2.failure_count}, "
        f"failures={summary2.failures}) — PK 충돌이 failure 로 오분류됨."
    )
    assert summary2.success_count == 2

    # 가격/시총은 (code,date) 당 1건만 (중복 insert 안 됨, first-wins).
    price_repo = SqlPriceRepository(db_session)
    for code in ("005930", "000660"):
        assert len(price_repo.fetch_prices(code, as_of=as_of, start=as_of)) == 1

    # batch_runs 최신 = SUCCESS (PARTIAL 아님).
    run_repo = SqlBatchRunRepository(db_session)
    latest = run_repo.latest_successful("KRX")
    assert latest is not None
    assert latest.status == BATCH_STATUS_SUCCESS, (
        f"재실행 batch status SUCCESS 이어야 함 (got {latest.status})."
    )

    # data_freshness 진단 — latest_batch_partial=False (왜곡 0, 핵심 회귀 가드).
    freshness = assess_data_freshness(
        now=datetime(2024, 5, 8, 9, 0, tzinfo=UTC),
        batch_run_repo=run_repo,
        calendar=DEFAULT_CALENDAR,
    )
    assert freshness.krx.latest_batch_partial is False


# =============================================================================
# T19 — DartDailyBatch savepoint
# =============================================================================

def _dart_row(*, account_id: str = "ifrs-full_Assets", amount: str = "100") -> dict[str, Any]:
    return {
        "account_id": account_id,
        "account_nm": "자산총계",
        "thstrm_amount": amount,
        "rcept_no": "20240501000123",
        "currency": "KRW",
        "sj_div": "BS",
        "bsns_year": "2023",
        "reprt_code": "11011",
    }


def test_dart_savepoint_rolls_back_failed_company_only(
    db_session: Session,
) -> None:
    """oracle T19 M1 — 회사 2 의 financial save 실패 시 회사 1 의 citation/
    financial 만 보존.
    """
    def _handler(request: httpx.Request) -> httpx.Response:
        # 모든 회사 모든 IFRS type 정상 응답.
        return httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": [_dart_row()],
            },
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(_handler), timeout=5.0,
    )
    dart_adapter = DartAdapter(
        http_client=http_client, api_key="test_key",
    )
    citation_repo = SqlCitationRepository(db_session)
    financial_repo = SqlFinancialRepository(db_session)

    # oracle T19 M2 — code 기반 분기 (call_count 보다 견고). 회사 2 (000660) 의
    # FinancialRecord 가 save_financials 에 도달하면 raise. IFRS type 수 변경
    # 에도 안전.
    original_save = financial_repo.save_financials

    def _code_failing_save(records: Any) -> None:
        records_list = list(records)
        if records_list and records_list[0].code == "000660":
            raise RuntimeError("simulated save failure on company 2")
        original_save(records_list)

    financial_repo.save_financials = _code_failing_save  # type: ignore[method-assign]

    mapping = CorpCodeMapping.from_dict({
        "005930": "00126380",
        "000660": "00164779",
    })
    batch = DartDailyBatch(
        adapter=dart_adapter,
        corp_mapping=mapping,
        citation_repo=citation_repo,
        financial_repo=financial_repo,
        throttle_seconds=0,
        session=db_session,
    )
    summary = batch.run(
        stock_codes=["005930", "000660"],
        fiscal_year=2023, fiscal_quarter=4,
    )

    # 회사 1 성공 + 회사 2 실패.
    assert summary.success_count == 1
    assert summary.failure_count == 1
    assert summary.failures[0][0] == "000660"

    # 영구화된 citation — 회사 1 의 CFS+OFS citation 2 개만.
    cited = citation_repo.fetch_by_batch(summary.batch_id)
    assert len(cited) == 2

    # oracle T19 L2 — financial row 직접 검증. 회사 1 영구화 + 회사 2 rollback.
    # _dart_row 의 default account_id = "ifrs-full_Assets" → canonical
    # "total_assets". effective_date 는 rcept_no="20240501000123" 도출 정밀
    # 공시일 (ADR-0012 D6) = 2024-05-01. as_of=2024-05-15 으로 안전 마진.
    fin_005930 = financial_repo.fetch_financials(
        "005930", as_of=date(2024, 5, 15),
        account="total_assets",
    )
    assert len(fin_005930) > 0
    fin_000660 = financial_repo.fetch_financials(
        "000660", as_of=date(2024, 5, 15),
        account="total_assets",
    )
    assert fin_000660 == ()


# =============================================================================
# Backward compat — session=None (Fake mode)
# =============================================================================

def test_krx_session_none_uses_nullcontext() -> None:
    """session=None 일 때 savepoint 없이 기존 동작 유지."""
    from app.repositories.citation_repository import FakeCitationRepository
    from app.repositories.fakes import FakePriceRepository

    pykrx_mock = MagicMock()
    pykrx_mock.get_market_ticker_list = MagicMock(return_value=["005930"])
    pykrx_mock.get_market_ohlcv = MagicMock(
        return_value=_ohlcv_df(day=date(2024, 5, 7), close=70000.0),
    )
    pykrx_mock.get_market_cap_by_date = MagicMock(
        return_value=_market_cap_df(day=date(2024, 5, 7)),
    )

    batch = KrxDailyBatch(
        primary_adapter=PykrxAdapter(pykrx_module=pykrx_mock),
        verify_adapter=None,
        conflict_detector=None,
        calendar=DEFAULT_CALENDAR,
        citation_repo=FakeCitationRepository(),
        price_repo=FakePriceRepository(records=()),
        throttle_seconds=0,
        # session 미주입 — default None.
    )
    summary = batch.run(as_of=date(2024, 5, 7), market="KOSPI")
    assert summary.success_count == 1


# =============================================================================
# T-KOSIS — KosisDailyBatch idempotent SAVEPOINT (Critical 버그 재현·검증)
# =============================================================================

def test_kosis_savepoint_idempotent_no_cascading_failure(
    db_session: Session,
) -> None:
    """KOSIS idempotent 재수집 — 지표 1 IntegrityError 가 지표 2·3 막지 않음.

    Critical 버그 재현 + 수정 검증:
        구버전: _process_indicator 내 session.rollback() 호출 → 외부 트랜잭션
        abort → 지표 2·3 PendingRollbackError 연쇄 실패.
        수정 후: begin_nested() SAVEPOINT 자동 rollback → 지표 1 skip,
        지표 2·3 정상 처리, session 상태 정상 유지.

    시나리오:
        1차 run: 지표 3개 × 2 rows 적재 → total_rows_saved=6.
        2차 run (same observed_date): UNIQUE IntegrityError → 지표 3개 모두
        skip, success_count=0, skipped_count=3, failure_count=0.
        PendingRollbackError 발생 시 테스트 실패 (연쇄 실패 재현).
    """
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import uuid4

    from app.adapters.base import FetchResult, MacroIndicatorRow
    from app.models.source_citation import SourceCitation, SourceKind
    from app.repositories.citation_repository import SqlCitationRepository
    from batch.kosis_daily import KosisDailyBatch, _KosisIndicator

    observed = date(2024, 6, 1)

    # 지표 3개 정의 — 실제 _KOSIS_INDICATORS 와 유사한 구조.
    indicators = [
        _KosisIndicator(
            org_id="101", tbl_id="DT_A", itm_id="T10",
            obj_l="ALL", prd_se="M", label="ind_a",
        ),
        _KosisIndicator(
            org_id="101", tbl_id="DT_B", itm_id="T20",
            obj_l="ALL", prd_se="M", label="ind_b",
        ),
        _KosisIndicator(
            org_id="101", tbl_id="DT_C", itm_id="T30",
            obj_l="ALL", prd_se="M", label="ind_c",
        ),
    ]

    def _make_kosis_fetch_result(
        tbl_id: str, batch_id: object, observed_date: date,
    ) -> FetchResult:
        """지표별 2 rows FetchResult 생성 — indicator_id 를 tbl_id 로 구분."""
        citation = SourceCitation(
            id=uuid4(),
            source=SourceKind.KOSIS,
            identifier=f"101/{tbl_id}/T10",
            retrieved_at=datetime.now(UTC),
            effective_date=observed_date,
            adapter_version="1.0.0",
            batch_id=batch_id,  # type: ignore[arg-type]
            url=f"https://kosis.kr/test/{tbl_id}",
        )
        rows = (
            MacroIndicatorRow(
                indicator_id=f"kosis/101/{tbl_id}/T10",
                reference_date=date(2024, 4, 1),
                value=Decimal("3.5"),
                unit="%",
                vintage_date=observed_date,
            ),
            MacroIndicatorRow(
                indicator_id=f"kosis/101/{tbl_id}/T10",
                reference_date=date(2024, 5, 1),
                value=Decimal("3.6"),
                unit="%",
                vintage_date=observed_date,
            ),
        )
        return FetchResult(data=rows, citations=(citation,), warnings=())

    class _RealKosisAdapter:
        """실 DB 통합 테스트용 KosisAdapter stub."""

        SOURCE_KIND = "KOSIS"

        def fetch_statistic(
            self,
            *,
            tbl_id: str,
            batch_id: object,
            observed_date: date,
            **_kw: object,
        ) -> FetchResult:
            return _make_kosis_fetch_result(tbl_id, batch_id, observed_date)

    citation_repo = SqlCitationRepository(db_session)

    # FakeBatchRunRepository — SqlBatchRunRepository auto-creation 방지 아님.
    # 실 batch_runs 영속화가 필요하므로 session 을 사용하는 SqlBatchRunRepository
    # 가 자동 생성되도록 batch_run_repo=None (session 주입 시 default).
    batch = KosisDailyBatch(
        adapter=_RealKosisAdapter(),  # type: ignore[arg-type]
        citation_repo=citation_repo,
        session=db_session,
        throttle_seconds=0,
        indicators=indicators,
        # batch_run_repo=None → SqlBatchRunRepository(session) auto-create.
    )

    # ── 1차 run ──────────────────────────────────────────────────────────────
    summary1 = batch.run(observed_date=observed)

    assert summary1.success_count == 3, (
        f"1차 run: 지표 3개 모두 성공해야 함 (got {summary1.success_count})"
    )
    assert summary1.failure_count == 0
    assert summary1.skipped_count == 0
    assert summary1.total_rows_saved == 6  # 3 지표 × 2 rows

    # ── 2차 run (same observed_date) ─────────────────────────────────────────
    # 지표별 UNIQUE IntegrityError → SAVEPOINT 자동 rollback → skip.
    # Critical 검증: PendingRollbackError 없이 지표 2·3 정상 처리.
    summary2 = batch.run(observed_date=observed)

    # 지표 3개 모두 UNIQUE 충돌 → skip (실패 아님).
    assert summary2.skipped_count == 3, (
        f"2차 run: 지표 3개 모두 UNIQUE skip 이어야 함 (got {summary2.skipped_count}). "
        f"failure_count={summary2.failure_count}, failures={summary2.failures} — "
        "PendingRollbackError 연쇄 실패 의심."
    )
    assert summary2.success_count == 0
    assert summary2.failure_count == 0, (
        f"2차 run: failure 는 0 이어야 함 (got {summary2.failure_count}). "
        f"failures={summary2.failures} — "
        "지표 1 IntegrityError 가 지표 2·3 PendingRollbackError 로 연쇄 실패."
    )
    assert summary2.total_rows_saved == 0
