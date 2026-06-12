"""EcosDailyBatch 단위 테스트 — adapter mock + FakeSession.

KosisDailyBatch 테스트와 동형 매트릭스 (ECOS fetch_statistic 시그니처 차이만):
    1. 정상 run — 지표별 macro_indicator 적재 + batch_run success.
    2. per-indicator failure isolation — 한 지표 AdapterError → 나머지 적재.
    3. idempotent — 같은 observed_date 재run → UNIQUE → skip.
    4. citation → record FK 순서 (citation save 가 먼저).
    5. reference_date > observed_date 미래 row skip + warning (F-08).
    6. 빈 결과 (INFO-200 무자료) — 정상 처리, rows_saved=0.
    7. fetch_statistic citations=() + data 있음 → AdapterError (invariant).
    8. _period_range helper 검증 (월별 + 연경계 + 단일월 + 비월별 ValueError).
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

from app.adapters.base import AdapterError, FetchResult, MacroIndicatorRow
from app.repositories.batch_run_repository import FakeBatchRunRepository
from app.repositories.citation_repository import FakeCitationRepository
from batch.ecos_daily import (
    EcosDailyBatch,
    _EcosIndicator,
    _period_range,
)

# =============================================================================
# Helpers — Fake Session + Fake Adapter
# =============================================================================


class _FakeNestedContext:
    """SAVEPOINT 흉내 — 항상 성공. IntegrityError 재현은 FakeSession.add 에서."""

    def __enter__(self) -> _FakeNestedContext:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class FakeSession:
    """session.add / flush / rollback / begin_nested 구현 (kosis 테스트 동형).

    added: 추가된 ORM 객체 목록.
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
        key = (
            getattr(obj, "indicator_id", None),
            getattr(obj, "reference_date", None),
            getattr(obj, "vintage_date", None),
        )
        if key in self._raise_on:
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


def _make_indicator(stat_code: str, item_code: str) -> _EcosIndicator:
    """테스트용 ECOS 지표 항목 생성 (월별 cycle)."""
    return _EcosIndicator(
        stat_code=stat_code,
        item_code=item_code,
        cycle="M",
        label=f"{stat_code}/{item_code}",
    )


def _make_row(
    *,
    indicator_id: str = "722Y001/0101000",
    reference_date: date = date(2024, 5, 1),
    value: Decimal = Decimal("3.5"),
    observed_date: date = date(2024, 6, 1),
) -> MacroIndicatorRow:
    return MacroIndicatorRow(
        indicator_id=indicator_id,
        reference_date=reference_date,
        value=value,
        unit="연%",
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
            source=SourceKind.ECOS,
            identifier="722Y001/0101000",
            retrieved_at=datetime.now(UTC),
            effective_date=observed_date,
            adapter_version="1.0.0",
            batch_id=batch_id,
            url="https://ecos.bok.or.kr/#/통계검색/722Y001",
        ),
    ) if rows else ()
    return FetchResult(data=rows, citations=citations, warnings=())


def _make_batch(
    *,
    adapter: Any,
    citation_repo: Any,
    session: FakeSession | None = None,
    indicators: list[_EcosIndicator] | None = None,
    throttle_seconds: float = 0,
) -> EcosDailyBatch:
    return EcosDailyBatch(
        adapter=adapter,  # type: ignore[arg-type]
        citation_repo=citation_repo,
        session=session,  # type: ignore[arg-type]
        throttle_seconds=throttle_seconds,
        indicators=indicators,
        batch_run_repo=FakeBatchRunRepository(),  # Sql auto-create 방지
    )


# =============================================================================
# 1. 정상 run
# =============================================================================

def test_run_normal_path_saves_macro_indicators() -> None:
    """정상 run: 지표 2개 × 2 rows = 4 rows_saved, success=2."""
    observed = date(2024, 6, 1)

    ind_a = _make_indicator("722Y001", "0101000")
    ind_b = _make_indicator("901Y009", "0")

    class _Adapter:
        SOURCE_KIND = "ECOS"

        def fetch_statistic(
            self, *, stat_code: str, item_code: str, batch_id: UUID,
            observed_date: date, **kw: Any,
        ) -> FetchResult:
            row = _make_row(
                indicator_id=f"{stat_code}/{item_code}",
                reference_date=date(2024, 5, 1),
                observed_date=observed_date,
            )
            return _make_fetch_result(
                rows=(row, row), batch_id=batch_id, observed_date=observed_date,
            )

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
    assert summary.total_rows_saved == 4
    assert summary.observed_date == observed
    assert session.flush_count == 4


# =============================================================================
# 2. per-indicator failure isolation
# =============================================================================

def test_per_indicator_failure_isolation() -> None:
    """ind_a 실패, ind_b 성공 — ind_b 는 정상 저장."""
    observed = date(2024, 6, 1)
    ind_a = _make_indicator("722Y001", "0101000")
    ind_b = _make_indicator("901Y009", "0")

    call_order: list[str] = []

    class _Adapter:
        SOURCE_KIND = "ECOS"

        def fetch_statistic(
            self, *, stat_code: str, item_code: str, batch_id: UUID,
            observed_date: date, **kw: Any,
        ) -> FetchResult:
            call_order.append(stat_code)
            if stat_code == "722Y001":
                raise AdapterError("722Y001 fetch 실패 — 테스트")
            row = _make_row(
                indicator_id=f"{stat_code}/{item_code}",
                observed_date=observed_date,
            )
            return _make_fetch_result(
                rows=(row,), batch_id=batch_id, observed_date=observed_date,
            )

    session = FakeSession()
    citation_repo = FakeCitationRepository()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=[ind_a, ind_b],
    )
    summary = batch.run(observed_date=observed)

    assert summary.success_count == 1
    assert summary.failure_count == 1
    assert len(summary.failures) == 1
    assert "722Y001" in summary.failures[0][0]
    assert summary.total_rows_saved == 1
    assert call_order == ["722Y001", "901Y009"]


# =============================================================================
# 3. idempotent — UNIQUE 충돌 → skip
# =============================================================================

def test_idempotent_same_observed_date_skip() -> None:
    """같은 (indicator_id, reference_date, vintage_date) UNIQUE 충돌 → skip."""
    observed = date(2024, 6, 1)
    ind = _make_indicator("722Y001", "0101000")
    row = _make_row(
        indicator_id="722Y001/0101000",
        reference_date=date(2024, 5, 1),
        observed_date=observed,
    )

    class _Adapter:
        SOURCE_KIND = "ECOS"

        def fetch_statistic(
            self, *, batch_id: UUID, observed_date: date, **kw: Any,
        ) -> FetchResult:
            return _make_fetch_result(
                rows=(row,), batch_id=batch_id, observed_date=observed_date,
            )

    raise_key = ("722Y001/0101000", date(2024, 5, 1), observed)
    session = FakeSession(raise_integrity_on={raise_key})
    citation_repo = FakeCitationRepository()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=[ind],
    )
    summary = batch.run(observed_date=observed)

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
    ind = _make_indicator("722Y001", "0101000")
    row = _make_row(
        indicator_id="722Y001/0101000",
        reference_date=date(2024, 5, 1),
        observed_date=observed,
    )

    class _Adapter:
        SOURCE_KIND = "ECOS"

        def fetch_statistic(
            self, *, batch_id: UUID, observed_date: date, **kw: Any,
        ) -> FetchResult:
            return _make_fetch_result(
                rows=(row,), batch_id=batch_id, observed_date=observed_date,
            )

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
    assert save_order[0] == "citation"
    assert "macro" in save_order


# =============================================================================
# 5. reference_date > observed_date 미래 row skip + warning (F-08)
# =============================================================================

def test_future_reference_date_skip(caplog: pytest.LogCaptureFixture) -> None:
    """reference_date > observed_date → skip + WARNING 로그."""
    observed = date(2024, 6, 1)
    ind = _make_indicator("722Y001", "0101000")

    future_row = _make_row(
        indicator_id="722Y001/0101000",
        reference_date=date(2024, 7, 1),
        observed_date=observed,
    )
    past_row = _make_row(
        indicator_id="722Y001/0101000",
        reference_date=date(2024, 5, 1),
        observed_date=observed,
    )

    class _Adapter:
        SOURCE_KIND = "ECOS"

        def fetch_statistic(
            self, *, batch_id: UUID, observed_date: date, **kw: Any,
        ) -> FetchResult:
            return _make_fetch_result(
                rows=(future_row, past_row), batch_id=batch_id,
                observed_date=observed_date,
            )

    session = FakeSession()
    citation_repo = FakeCitationRepository()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=[ind],
    )

    with caplog.at_level(logging.WARNING, logger="batch.ecos_daily"):
        summary = batch.run(observed_date=observed)

    assert summary.success_count == 1
    assert summary.total_rows_saved == 1
    assert any("미래 reference_date skip" in r.message for r in caplog.records)


# =============================================================================
# 6. 빈 결과 (INFO-200) — 정상 처리, rows_saved=0
# =============================================================================

def test_empty_fetch_result_is_success_zero_rows() -> None:
    """지표 무자료 (빈 FetchResult) → success=1, total_rows_saved=0."""
    observed = date(2024, 6, 1)
    ind = _make_indicator("722Y001", "0101000")

    class _Adapter:
        SOURCE_KIND = "ECOS"

        def fetch_statistic(self, **kw: Any) -> FetchResult:
            return FetchResult(data=(), citations=(), warnings=("INFO-200",))

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
    assert len(session.added) == 0


# =============================================================================
# 7. citations=() + data 있음 → AdapterError (invariant violation)
# =============================================================================

def test_data_without_citation_raises_adapter_error() -> None:
    """data rows 있으나 citations 비어있음 → 지표 단위 AdapterError."""
    observed = date(2024, 6, 1)
    ind = _make_indicator("722Y001", "0101000")
    row = _make_row(observed_date=observed)

    class _Adapter:
        SOURCE_KIND = "ECOS"

        def fetch_statistic(self, **kw: Any) -> FetchResult:
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
# 8. _period_range helper 검증
# =============================================================================

def test_period_range_basic() -> None:
    """2024-06-15 기준 24개월 → end=202406, start=202207."""
    start, end = _period_range(date(2024, 6, 15), 24, "M")
    assert end == "202406"
    assert start == "202207"


def test_period_range_year_boundary() -> None:
    """2024-01-01 기준 3개월 → end=202401, start=202311."""
    start, end = _period_range(date(2024, 1, 1), 3, "M")
    assert end == "202401"
    assert start == "202311"


def test_period_range_single_month() -> None:
    """months_back=1 → start == end."""
    start, end = _period_range(date(2024, 6, 1), 1, "M")
    assert start == end == "202406"


def test_months_back_below_one_rejected() -> None:
    """months_back < 1 → __init__ ValueError (silent 0-row skip 방지, C-1/L-5)."""
    for bad in (0, -1):
        with pytest.raises(ValueError, match="months_back must be >= 1"):
            EcosDailyBatch(
                adapter=object(),  # type: ignore[arg-type]
                citation_repo=FakeCitationRepository(),
                months_back=bad,
            )


def test_period_range_non_monthly_raises() -> None:
    """비월별 cycle (D/Q/A/S) → ValueError (현 미지원)."""
    for cycle in ("D", "Q", "A", "S"):
        with pytest.raises(ValueError, match="미지원 cycle"):
            _period_range(date(2024, 6, 1), 12, cycle)


def test_non_monthly_indicator_isolated_as_failure() -> None:
    """비월별 cycle 지표는 _period_range ValueError → 지표 단위 failure 격리."""
    observed = date(2024, 6, 1)
    daily_ind = _EcosIndicator(
        stat_code="722Y001", item_code="0101000", cycle="D",
        label="ecos_daily_cycle",
    )

    class _Adapter:
        SOURCE_KIND = "ECOS"

        def fetch_statistic(self, **kw: Any) -> FetchResult:
            raise AssertionError("fetch 도달 전에 _period_range 가 실패해야 함")

    session = FakeSession()
    citation_repo = FakeCitationRepository()

    batch = _make_batch(
        adapter=_Adapter(),
        citation_repo=citation_repo,
        session=session,
        indicators=[daily_ind],
    )
    summary = batch.run(observed_date=observed)

    assert summary.failure_count == 1
    assert summary.success_count == 0
    assert "ValueError" in summary.failures[0][1]


# =============================================================================
# 9. throttle_seconds=0 path — 무한 루프 없음
# =============================================================================

def test_run_completes_with_throttle_zero() -> None:
    """throttle=0 + 3 지표 — 모두 처리 후 summary 반환."""
    observed = date(2024, 6, 1)
    indicators = [
        _make_indicator("722Y001", "0101000"),
        _make_indicator("901Y009", "0"),
        _make_indicator("731Y001", "0000001"),
    ]

    class _Adapter:
        SOURCE_KIND = "ECOS"
        call_count = 0

        def fetch_statistic(self, **kw: Any) -> FetchResult:
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
        SOURCE_KIND = "ECOS"

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
