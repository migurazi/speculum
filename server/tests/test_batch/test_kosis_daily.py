"""KosisDailyBatch 단위 테스트 — adapter mock + FakeSession.

테스트 매트릭스:
    1. 정상 run — 지표별 macro_indicator 적재 + batch_run success.
    2. per-indicator failure isolation — 한 지표 AdapterError → 나머지 적재.
    3. idempotent — 같은 observed_date 재run → UNIQUE → skip.
    4. citation → record FK 순서 (citation save 가 먼저).
    5. reference_date > observed_date 미래 row skip + warning (F-08).
    6. 빈 결과 (지표 무자료) — 정상 처리, rows_saved=0.
    7. fetch_statistic citations=() + data 있음 → AdapterError (invariant).
    8. _month_range helper 검증.
    9. throttle_seconds=0 path.
    10. 빈 indicators 목록 → 즉시 성공 summary.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.adapters.base import (
    AdapterError,
    FetchResult,
    MacroIndicatorRow,
)
from app.repositories.batch_run_repository import FakeBatchRunRepository
from app.repositories.citation_repository import FakeCitationRepository
from batch.kosis_daily import (
    KosisDailyBatch,
    _KosisIndicator,
    _month_range,
)

# =============================================================================
# Helpers — Fake Session + Fake Adapter
# =============================================================================


class _FakeNestedContext:
    """SAVEPOINT 흉내 — 항상 성공(commit). IntegrityError 재현은 FakeSession 에서."""

    def __enter__(self) -> _FakeNestedContext:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class FakeSession:
    """session.add / session.flush / session.rollback / session.begin_nested 구현.

    added: 추가된 ORM 객체 목록 (macro_indicator_record_to_orm 결과).
    raise_integrity_on: (indicator_id, reference_date, vintage_date) set —
        해당 tuple add 시 IntegrityError 발생.
    """

    def __init__(
        self,
        raise_integrity_on: set[tuple[str, date, date]] | None = None,
    ) -> None:
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        self._IntegrityError = SAIntegrityError
        self._raise_on: set[tuple[str, date, date]] = raise_integrity_on or set()
        self.added: list[Any] = []
        self.flush_count = 0
        self.rollback_count = 0

    def add(self, obj: Any) -> None:
        # indicator_id/reference_date/vintage_date 가 있으면 UNIQUE 시뮬레이션.
        key = (
            getattr(obj, "indicator_id", None),
            getattr(obj, "reference_date", None),
            getattr(obj, "vintage_date", None),
        )
        if key in self._raise_on:
            # 실제 SA IntegrityError 생성은 복잡하므로 IntegrityError 서브클래스 사용.
            raise self._IntegrityError(
                statement=None, params=None, orig=Exception("UNIQUE constraint"),
            )
        self.added.append(obj)

    def flush(self) -> None:
        self.flush_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def begin_nested(self) -> _FakeNestedContext:
        return _FakeNestedContext()


class FakeKosisAdapter:
    """KosisAdapter 의 fetch_statistic 을 제어하는 test double.

    responses: indicator label → FetchResult 반환값. 없으면 빈 FetchResult.
    errors:    indicator label → raise 할 AdapterError.
    """

    SOURCE_KIND = "KOSIS"

    def __init__(
        self,
        responses: dict[str, FetchResult[tuple[MacroIndicatorRow, ...]]] | None = None,
        errors: dict[str, AdapterError] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._errors = errors or {}
        self.calls: list[dict[str, Any]] = []

    def fetch_statistic(
        self,
        *,
        org_id: str,
        tbl_id: str,
        itm_id: str,
        obj_l: str,
        prd_se: str,
        start_prd: str,
        end_prd: str,
        batch_id: UUID,
        observed_date: date,
    ) -> FetchResult[tuple[MacroIndicatorRow, ...]]:
        label = f"{tbl_id}/{itm_id}"
        self.calls.append({"tbl_id": tbl_id, "itm_id": itm_id, "observed_date": observed_date})
        if label in self._errors:
            raise self._errors[label]
        return self._responses.get(
            label,
            FetchResult(data=(), citations=(), warnings=()),
        )


def _make_indicator(tbl_id: str, itm_id: str) -> _KosisIndicator:
    """테스트용 지표 항목 생성."""
    return _KosisIndicator(
        org_id="101",
        tbl_id=tbl_id,
        itm_id=itm_id,
        obj_l="ALL",
        prd_se="M",
        label=f"{tbl_id}/{itm_id}",
    )


def _make_citation(batch_id: UUID, observed_date: date = date(2024, 6, 1)):
    """테스트용 SourceCitation 생성."""
    from app.models.source_citation import SourceCitation, SourceKind

    return SourceCitation(
        id=uuid4(),
        source=SourceKind.KOSIS,
        identifier="101/DT_TEST/T10",
        retrieved_at=datetime.now(UTC),
        effective_date=observed_date,
        adapter_version="1.0.0",
        batch_id=batch_id,
        url="https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=DT_TEST",
    )


def _make_row(
    *,
    indicator_id: str = "kosis/101/DT_TEST/T10",
    reference_date: date = date(2024, 5, 1),
    value: Decimal = Decimal("3.5"),
    observed_date: date = date(2024, 6, 1),
) -> MacroIndicatorRow:
    return MacroIndicatorRow(
        indicator_id=indicator_id,
        reference_date=reference_date,
        value=value,
        unit="%",
        vintage_date=observed_date,
    )


def _make_fetch_result(
    *,
    rows: tuple[MacroIndicatorRow, ...],
    batch_id: UUID,
    observed_date: date = date(2024, 6, 1),
) -> FetchResult[tuple[MacroIndicatorRow, ...]]:
    from app.models.source_citation import SourceCitation, SourceKind

    citations = (
        SourceCitation(
            id=uuid4(),
            source=SourceKind.KOSIS,
            identifier="101/DT_TEST/T10",
            retrieved_at=datetime.now(UTC),
            effective_date=observed_date,
            adapter_version="1.0.0",
            batch_id=batch_id,
            url="https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=DT_TEST",
        ),
    ) if rows else ()
    return FetchResult(data=rows, citations=citations, warnings=())


# =============================================================================
# 공통 factory — batch_run_repo 를 항상 Fake 로 명시해 SqlBatchRunRepository
# auto-creation (FakeSession.get 미구현) 을 차단.
# =============================================================================

def _make_batch(
    *,
    adapter: Any,
    citation_repo: Any,
    session: FakeSession | None = None,
    indicators: list[_KosisIndicator] | None = None,
    throttle_seconds: float = 0,
) -> KosisDailyBatch:
    return KosisDailyBatch(
        adapter=adapter,  # type: ignore[arg-type]
        citation_repo=citation_repo,
        session=session,  # type: ignore[arg-type]
        throttle_seconds=throttle_seconds,
        indicators=indicators,
        batch_run_repo=FakeBatchRunRepository(),  # Sql auto-create 방지
    )


# =============================================================================
# 1. 정상 run — 지표별 macro_indicator 적재 + batch_run success
# =============================================================================

def test_run_normal_path_saves_macro_indicators() -> None:
    """정상 run: 지표 2개 × 2 rows = 4 rows_saved, success=2."""
    observed = date(2024, 6, 1)
    batch_id_holder: list[UUID] = []

    ind_a = _make_indicator("DT_A", "T10")
    ind_b = _make_indicator("DT_B", "T20")

    # adapter 내부에서 batch_id 를 받아야 하므로 dynamic FetchResult.
    class _Adapter:
        SOURCE_KIND = "KOSIS"
        calls: list[str] = []

        def fetch_statistic(self, *, tbl_id: str, itm_id: str, batch_id: UUID, observed_date: date, **kw: Any) -> FetchResult:
            batch_id_holder.append(batch_id)
            row = _make_row(
                indicator_id=f"kosis/101/{tbl_id}/{itm_id}",
                reference_date=date(2024, 5, 1),
                observed_date=observed_date,
            )
            return _make_fetch_result(rows=(row, row), batch_id=batch_id, observed_date=observed_date)

    session = FakeSession()
    citation_repo = FakeCitationRepository()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=[ind_a, ind_b],
    )
    summary = batch.run(observed_date=observed)

    assert summary.target_count == 2
    assert summary.success_count == 2
    assert summary.failure_count == 0
    assert summary.skipped_count == 0
    assert summary.total_rows_saved == 4  # 2 지표 × 2 rows
    assert summary.observed_date == observed
    # 각 지표마다 flush 2번 (rows 수) → 총 4.
    assert session.flush_count == 4


# =============================================================================
# 2. per-indicator failure isolation — 한 지표 AdapterError → 나머지 적재
# =============================================================================

def test_per_indicator_failure_isolation() -> None:
    """ind_a 실패, ind_b 성공 — ind_b 는 정상 저장."""
    observed = date(2024, 6, 1)
    ind_a = _make_indicator("DT_A", "T10")
    ind_b = _make_indicator("DT_B", "T20")

    call_order: list[str] = []

    class _Adapter:
        SOURCE_KIND = "KOSIS"

        def fetch_statistic(self, *, tbl_id: str, itm_id: str, batch_id: UUID, observed_date: date, **kw: Any) -> FetchResult:
            call_order.append(tbl_id)
            if tbl_id == "DT_A":
                raise AdapterError("DT_A fetch 실패 — 테스트")
            row = _make_row(
                indicator_id=f"kosis/101/{tbl_id}/{itm_id}",
                reference_date=date(2024, 5, 1),
                observed_date=observed_date,
            )
            return _make_fetch_result(rows=(row,), batch_id=batch_id, observed_date=observed_date)

    session = FakeSession()
    citation_repo = FakeCitationRepository()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=[ind_a, ind_b],
    )
    summary = batch.run(observed_date=observed)

    assert summary.success_count == 1   # ind_b 성공
    assert summary.failure_count == 1   # ind_a 실패
    assert len(summary.failures) == 1
    assert "DT_A" in summary.failures[0][0]
    assert summary.total_rows_saved == 1  # ind_b 1 row
    # 두 지표 모두 시도됨 — 순서 보장.
    assert call_order == ["DT_A", "DT_B"]


# =============================================================================
# 3. idempotent — UNIQUE 충돌 → skip (중복 INSERT 안 됨)
# =============================================================================

def test_idempotent_same_observed_date_skip() -> None:
    """같은 (indicator_id, reference_date, vintage_date) UNIQUE 충돌 → skip."""
    observed = date(2024, 6, 1)
    ind = _make_indicator("DT_A", "T10")
    row = _make_row(
        indicator_id="kosis/101/DT_A/T10",
        reference_date=date(2024, 5, 1),
        observed_date=observed,
    )

    class _Adapter:
        SOURCE_KIND = "KOSIS"

        def fetch_statistic(self, *, batch_id: UUID, observed_date: date, **kw: Any) -> FetchResult:
            return _make_fetch_result(rows=(row,), batch_id=batch_id, observed_date=observed_date)

    # UNIQUE 충돌 시뮬레이션 — (indicator_id, reference_date, vintage_date).
    raise_key = ("kosis/101/DT_A/T10", date(2024, 5, 1), observed)
    session = FakeSession(raise_integrity_on={raise_key})
    citation_repo = FakeCitationRepository()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=[ind],
    )
    summary = batch.run(observed_date=observed)

    # UNIQUE 충돌 → skipped (실패 아님).
    assert summary.skipped_count == 1
    assert summary.success_count == 0
    assert summary.failure_count == 0
    assert summary.total_rows_saved == 0


# =============================================================================
# 4. citation → record FK 순서
# =============================================================================

def test_citation_saved_before_macro_indicator() -> None:
    """citation save 가 session.add(macro_indicator) 보다 먼저 호출돼야 함."""
    observed = date(2024, 6, 1)
    ind = _make_indicator("DT_A", "T10")
    row = _make_row(
        indicator_id="kosis/101/DT_A/T10",
        reference_date=date(2024, 5, 1),
        observed_date=observed,
    )

    class _Adapter:
        SOURCE_KIND = "KOSIS"

        def fetch_statistic(self, *, batch_id: UUID, observed_date: date, **kw: Any) -> FetchResult:
            return _make_fetch_result(rows=(row,), batch_id=batch_id, observed_date=observed_date)

    save_order: list[str] = []

    class _TrackingCitationRepo(FakeCitationRepository):
        def save(self, citation: Any) -> None:
            save_order.append("citation")
            super().save(citation)

    class _TrackingSession(FakeSession):
        def add(self, obj: Any) -> None:
            save_order.append("macro")
            super().add(obj)

    session = _TrackingSession()
    citation_repo = _TrackingCitationRepo()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=[ind],
    )
    summary = batch.run(observed_date=observed)

    assert summary.success_count == 1
    # citation 이 macro add 보다 먼저.
    assert save_order[0] == "citation"
    assert "macro" in save_order


# =============================================================================
# 5. reference_date > observed_date 미래 row skip + warning (F-08)
# =============================================================================

def test_future_reference_date_skip(caplog: pytest.LogCaptureFixture) -> None:
    """reference_date > observed_date → skip + WARNING 로그."""
    observed = date(2024, 6, 1)
    ind = _make_indicator("DT_A", "T10")

    future_row = _make_row(
        indicator_id="kosis/101/DT_A/T10",
        reference_date=date(2024, 7, 1),  # 미래
        observed_date=observed,
    )
    past_row = _make_row(
        indicator_id="kosis/101/DT_A/T10",
        reference_date=date(2024, 5, 1),  # 과거 — 정상
        observed_date=observed,
    )

    class _Adapter:
        SOURCE_KIND = "KOSIS"

        def fetch_statistic(self, *, batch_id: UUID, observed_date: date, **kw: Any) -> FetchResult:
            # 미래 row + 정상 row 혼합.
            return _make_fetch_result(rows=(future_row, past_row), batch_id=batch_id, observed_date=observed_date)

    session = FakeSession()
    citation_repo = FakeCitationRepository()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=[ind],
    )

    with caplog.at_level(logging.WARNING, logger="batch.kosis_daily"):
        summary = batch.run(observed_date=observed)

    assert summary.success_count == 1
    # 미래 row skip → 1 row 만 저장.
    assert summary.total_rows_saved == 1
    # WARNING 로그 발생 확인.
    assert any("미래 reference_date skip" in r.message for r in caplog.records)


# =============================================================================
# 6. 빈 결과 — 정상 처리, rows_saved=0
# =============================================================================

def test_empty_fetch_result_is_success_zero_rows() -> None:
    """지표 무자료 (빈 FetchResult) → success=1, total_rows_saved=0."""
    observed = date(2024, 6, 1)
    ind = _make_indicator("DT_A", "T10")

    class _Adapter:
        SOURCE_KIND = "KOSIS"

        def fetch_statistic(self, **kw: Any) -> FetchResult:
            return FetchResult(data=(), citations=(), warnings=("no data",))

    session = FakeSession()
    citation_repo = FakeCitationRepository()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=[ind],
    )
    summary = batch.run(observed_date=observed)

    assert summary.success_count == 1
    assert summary.total_rows_saved == 0
    assert summary.failure_count == 0
    # session.add 미호출 (macro row 없음).
    assert len(session.added) == 0


# =============================================================================
# 7. citations=() + data 있음 → AdapterError (invariant violation)
# =============================================================================

def test_data_without_citation_raises_adapter_error() -> None:
    """data rows 있으나 citations 비어있음 → 지표 단위 AdapterError."""
    observed = date(2024, 6, 1)
    ind = _make_indicator("DT_A", "T10")
    row = _make_row(observed_date=observed)

    class _Adapter:
        SOURCE_KIND = "KOSIS"

        def fetch_statistic(self, **kw: Any) -> FetchResult:
            # data 있으나 citations 없는 invariant 위반.
            return FetchResult(data=(row,), citations=(), warnings=())

    session = FakeSession()
    citation_repo = FakeCitationRepository()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=[ind],
    )
    summary = batch.run(observed_date=observed)

    assert summary.failure_count == 1
    assert "invariant violation" in summary.failures[0][1]


# =============================================================================
# 8. _month_range helper 검증
# =============================================================================

def test_month_range_basic() -> None:
    """2024-06-15 기준 24개월 범위 → end=202406, start=202207 (24개월 포함)."""
    start, end = _month_range(date(2024, 6, 15), 24)
    assert end == "202406"
    assert start == "202207"


def test_month_range_year_boundary() -> None:
    """2024-01-01 기준 3개월 → end=202401, start=202311 (3개월 포함: 11,12,01)."""
    start, end = _month_range(date(2024, 1, 1), 3)
    assert end == "202401"
    assert start == "202311"


def test_month_range_single_month() -> None:
    """months_back=1 → start == end."""
    observed = date(2024, 6, 1)
    start, end = _month_range(observed, 1)
    assert start == end == "202406"


# =============================================================================
# 9. throttle_seconds 에 따른 지표 loop 완료 (무한 루프 없음)
# =============================================================================

def test_run_completes_with_throttle_zero() -> None:
    """throttle=0 + 3 지표 — 모두 처리 후 summary 반환."""
    observed = date(2024, 6, 1)
    indicators = [
        _make_indicator("DT_A", "T10"),
        _make_indicator("DT_B", "T20"),
        _make_indicator("DT_C", "T30"),
    ]

    class _Adapter:
        SOURCE_KIND = "KOSIS"
        call_count = 0

        def fetch_statistic(self, *, batch_id: UUID, observed_date: date, **kw: Any) -> FetchResult:
            _Adapter.call_count += 1
            return FetchResult(data=(), citations=(), warnings=())

    session = FakeSession()
    citation_repo = FakeCitationRepository()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=indicators,
    )
    summary = batch.run(observed_date=observed)

    assert summary.target_count == 3
    assert _Adapter.call_count == 3


# =============================================================================
# 10. 빈 indicators 목록 → 즉시 성공 summary
# =============================================================================

def test_empty_indicators_returns_zero_summary() -> None:
    """indicators=[] → target_count=0, success=0, rows=0."""

    class _Adapter:
        SOURCE_KIND = "KOSIS"

        def fetch_statistic(self, **kw: Any) -> FetchResult:
            raise AssertionError("fetch_statistic 호출되면 안 됨")

    session = FakeSession()
    citation_repo = FakeCitationRepository()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=[],
    )
    summary = batch.run(observed_date=date(2024, 6, 1))

    assert summary.target_count == 0
    assert summary.success_count == 0
    assert summary.total_rows_saved == 0
    assert summary.failure_count == 0
