"""DartDailyBatch 단위 테스트 + CorpCodeMapping 검증.

테스트 매트릭스:
    1. corp_code 매핑 성공 path — CFS+OFS save
    2. corp_code 매핑 누락 → skip
    3. DART fetch 실패 → failure (회사 단위 isolation)
    4. CFS+OFS 분리 fetch — 양쪽 모두 영구화
    5. fetch-then-save: CFS 성공 + OFS 실패 → 어떤 row 도 저장 안 됨 (T18 C1 패턴)
    6. CorpCodeMapping format 검증 + 양방향 lookup
    7. _convert_to_financial_records — fiscal_period 변환 + lineage_id placeholder
    8. citation → financial 순서 (citation_id FK 만족)
    9. ifrs_types 옵션 — CFS only 가능
    10. universe 빈 list → 정상 종료
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.adapters.base import (
    FinancialStatementRow,
    IfrsType,
)
from app.adapters.dart_adapter import DartAdapter
from app.repositories.citation_repository import FakeCitationRepository
from app.repositories.fakes import FakeFinancialRepository
from app.services.corp_code_mapping import (
    CorpCodeMapping,
    CorpCodeMappingError,
)
from batch.dart_daily import (
    DartDailyBatch,
    _convert_to_financial_records,
)

# =============================================================================
# Helpers
# =============================================================================

def _dart_row(
    *,
    account_id: str = "ifrs-full_Assets",
    thstrm_amount: str = "455905830000000",
    rcept_no: str = "20240501000123",
    sj_div: str = "BS",
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "account_nm": "자산총계",
        "thstrm_amount": thstrm_amount,
        "rcept_no": rcept_no,
        "currency": "KRW",
        "sj_div": sj_div,
        "bsns_year": "2023",
        "reprt_code": "11011",
    }


def _dart_response(
    *,
    rows: list[dict[str, Any]] | None = None,
    status: str = "000",
) -> dict[str, Any]:
    return {
        "status": status,
        "message": "정상" if status == "000" else "에러",
        "list": rows or [],
    }


def _adapter_with_handler(
    handler: Any,
    *,
    api_key: str = "test_key",
) -> DartAdapter:
    """httpx.MockTransport handler 로 DartAdapter 생성."""
    client = httpx.Client(
        transport=httpx.MockTransport(handler), timeout=5.0,
    )
    return DartAdapter(http_client=client, api_key=api_key)


def _fixed_response_handler(payload: dict[str, Any]) -> Any:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)
    return _handler


def _fs_div_response_handler(
    cfs_payload: dict[str, Any] | None = None,
    ofs_payload: dict[str, Any] | None = None,
) -> Any:
    """fs_div param 에 따라 CFS / OFS 응답 분기."""
    def _handler(request: httpx.Request) -> httpx.Response:
        fs_div = request.url.params.get("fs_div", "")
        if fs_div == "CFS":
            return httpx.Response(
                200,
                json=cfs_payload or _dart_response(rows=[]),
            )
        if fs_div == "OFS":
            return httpx.Response(
                200,
                json=ofs_payload or _dart_response(rows=[]),
            )
        return httpx.Response(400, text=f"unknown fs_div: {fs_div}")
    return _handler


# =============================================================================
# 1. 정상 path — CFS+OFS 모두 save
# =============================================================================

def test_run_normal_path_saves_cfs_and_ofs() -> None:
    cfs_rows = [_dart_row(thstrm_amount="100", rcept_no="C001")]
    ofs_rows = [_dart_row(thstrm_amount="200", rcept_no="O001")]
    adapter = _adapter_with_handler(
        _fs_div_response_handler(
            cfs_payload=_dart_response(rows=cfs_rows),
            ofs_payload=_dart_response(rows=ofs_rows),
        ),
    )
    mapping = CorpCodeMapping.from_dict({"005930": "00126380"})
    citation_repo = FakeCitationRepository()
    financial_repo = FakeFinancialRepository(records=())

    batch = DartDailyBatch(
        adapter=adapter,
        corp_mapping=mapping,
        citation_repo=citation_repo,
        financial_repo=financial_repo,
        throttle_seconds=0,
    )
    summary = batch.run(
        stock_codes=["005930"], fiscal_year=2023, fiscal_quarter=4,
    )
    assert summary.target_count == 1
    assert summary.success_count == 1
    assert summary.failure_count == 0
    assert summary.skipped_count == 0
    # CFS + OFS 두 row save.
    assert summary.total_rows_saved == 2
    # Citation 두 개 영구화.
    cited = citation_repo.fetch_by_batch(summary.batch_id)
    assert len(cited) == 2
    # rcept_no 별로 구분.
    identifiers = {c.identifier for c in cited}
    assert identifiers == {"C001", "O001"}


# =============================================================================
# 2. corp_code 매핑 누락 → skip
# =============================================================================

def test_run_skips_unmapped_stock_codes() -> None:
    adapter = _adapter_with_handler(_fs_div_response_handler())
    mapping = CorpCodeMapping.from_dict({})  # 빈 매핑.
    batch = DartDailyBatch(
        adapter=adapter,
        corp_mapping=mapping,
        citation_repo=FakeCitationRepository(),
        financial_repo=FakeFinancialRepository(records=()),
        throttle_seconds=0,
    )
    summary = batch.run(
        stock_codes=["005930", "000660"],
        fiscal_year=2023, fiscal_quarter=4,
    )
    assert summary.target_count == 2
    assert summary.success_count == 0
    assert summary.failure_count == 0
    assert summary.skipped_count == 2
    assert summary.skipped_codes == ("000660", "005930")


# =============================================================================
# 3. DART fetch 실패 → failure isolation
# =============================================================================

def test_run_isolates_failed_companies() -> None:
    # 회사 1 (005930) 성공, 회사 2 (000660) 실패.
    def _handler(request: httpx.Request) -> httpx.Response:
        corp = request.url.params.get("corp_code", "")
        fs_div = request.url.params.get("fs_div", "")
        if corp == "00164779":  # 000660 = SK하이닉스.
            # 모든 fetch 실패.
            return httpx.Response(503, text="server error")
        # 다른 회사 정상.
        return httpx.Response(
            200,
            json=_dart_response(rows=[_dart_row(rcept_no=f"R-{fs_div}")]),
        )

    adapter = _adapter_with_handler(_handler)
    mapping = CorpCodeMapping.from_dict({
        "005930": "00126380",
        "000660": "00164779",
    })
    batch = DartDailyBatch(
        adapter=adapter,
        corp_mapping=mapping,
        citation_repo=FakeCitationRepository(),
        financial_repo=FakeFinancialRepository(records=()),
        throttle_seconds=0,
    )
    summary = batch.run(
        stock_codes=["005930", "000660"],
        fiscal_year=2023, fiscal_quarter=4,
    )
    assert summary.success_count == 1
    assert summary.failure_count == 1
    assert summary.failures[0][0] == "000660"


# =============================================================================
# 4. fetch-then-save: CFS 성공 + OFS 실패 → 어떤 row 도 저장 안 됨
# =============================================================================

def test_partial_fetch_failure_writes_nothing() -> None:
    """oracle T18 C1 패턴 — CFS 성공 + OFS 실패 시 어떤 row 도 영구화 안 됨."""
    def _handler(request: httpx.Request) -> httpx.Response:
        fs_div = request.url.params.get("fs_div", "")
        if fs_div == "CFS":
            return httpx.Response(
                200,
                json=_dart_response(rows=[_dart_row(rcept_no="C001")]),
            )
        # OFS 실패.
        return httpx.Response(503, text="ofs failure")

    adapter = _adapter_with_handler(_handler)
    mapping = CorpCodeMapping.from_dict({"005930": "00126380"})
    citation_repo = FakeCitationRepository()
    financial_repo = FakeFinancialRepository(records=())

    batch = DartDailyBatch(
        adapter=adapter,
        corp_mapping=mapping,
        citation_repo=citation_repo,
        financial_repo=financial_repo,
        throttle_seconds=0,
    )
    summary = batch.run(
        stock_codes=["005930"], fiscal_year=2023, fiscal_quarter=4,
    )
    # 회사 단위 실패.
    assert summary.success_count == 0
    assert summary.failure_count == 1
    # citation 도 financial 도 영구화 안 됨.
    cited = citation_repo.fetch_by_batch(summary.batch_id)
    assert len(cited) == 0
    assert summary.total_rows_saved == 0


# =============================================================================
# 5. ifrs_types 옵션 — CFS only
# =============================================================================

def test_ifrs_types_filter_to_cfs_only() -> None:
    """ifrs_types=(IfrsType.CFS,) 만 영구화."""
    cfs_only_call_count = {"count": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        cfs_only_call_count["count"] += 1
        fs_div = request.url.params.get("fs_div", "")
        assert fs_div == "CFS"
        return httpx.Response(
            200,
            json=_dart_response(rows=[_dart_row(rcept_no="C001")]),
        )

    adapter = _adapter_with_handler(_handler)
    mapping = CorpCodeMapping.from_dict({"005930": "00126380"})
    batch = DartDailyBatch(
        adapter=adapter,
        corp_mapping=mapping,
        citation_repo=FakeCitationRepository(),
        financial_repo=FakeFinancialRepository(records=()),
        ifrs_types=(IfrsType.CFS,),  # OFS skip.
        throttle_seconds=0,
    )
    summary = batch.run(
        stock_codes=["005930"], fiscal_year=2023, fiscal_quarter=4,
    )
    assert summary.success_count == 1
    # CFS 한 번만 호출.
    assert cfs_only_call_count["count"] == 1


# =============================================================================
# 6. 빈 universe → 정상 종료
# =============================================================================

def test_empty_universe_returns_zero_summary() -> None:
    adapter = _adapter_with_handler(_fs_div_response_handler())
    mapping = CorpCodeMapping.from_dict({})
    batch = DartDailyBatch(
        adapter=adapter,
        corp_mapping=mapping,
        citation_repo=FakeCitationRepository(),
        financial_repo=FakeFinancialRepository(records=()),
        throttle_seconds=0,
    )
    summary = batch.run(
        stock_codes=[], fiscal_year=2023, fiscal_quarter=4,
    )
    assert summary.target_count == 0
    assert summary.total_rows_saved == 0


# =============================================================================
# 7. CorpCodeMapping format 검증 + 양방향
# =============================================================================

def test_corp_mapping_bidirectional_lookup() -> None:
    m = CorpCodeMapping.from_dict({"005930": "00126380", "000660": "00164779"})
    assert m.to_corp_code("005930") == "00126380"
    assert m.to_corp_code("000660") == "00164779"
    assert m.to_stock_code("00126380") == "005930"
    assert m.to_stock_code("00164779") == "000660"
    assert m.to_corp_code("999999") is None
    assert m.to_stock_code("99999999") is None
    assert m.size == 2


def test_corp_mapping_rejects_invalid_stock_code() -> None:
    with pytest.raises(CorpCodeMappingError, match="6-digit numeric"):
        CorpCodeMapping.from_dict({"abc": "00126380"})


def test_corp_mapping_rejects_invalid_corp_code() -> None:
    with pytest.raises(CorpCodeMappingError, match="8-digit numeric"):
        CorpCodeMapping.from_dict({"005930": "123"})


def test_corp_mapping_rejects_duplicate_corp_code() -> None:
    """양방향 1:1 위반 — 같은 corp_code 가 두 stock_code 에서 매핑되면 raise."""
    with pytest.raises(CorpCodeMappingError, match="duplicate corp_code"):
        CorpCodeMapping.from_dict({
            "005930": "00126380",
            "000660": "00126380",  # 중복.
        })


# =============================================================================
# 8. _convert_to_financial_records — fiscal_period 변환
# =============================================================================

def test_convert_to_financial_records_fiscal_period() -> None:
    citation_id = uuid4()
    rows = (
        FinancialStatementRow(
            code="005930",
            fiscal_year=2023,
            fiscal_quarter=4,
            effective_date=date(2023, 12, 31),
            account="total_assets",
            value=Decimal("100"),
            unit="krw",
            ifrs_type=IfrsType.CFS,
            rcept_no="C001",
            currency="KRW",
        ),
    )
    records = _convert_to_financial_records(rows=rows, citation_id=citation_id)
    assert len(records) == 1
    r = records[0]
    assert r.code == "005930"
    assert r.fiscal_period == "2023Q4"
    assert r.account == "total_assets"
    assert r.value == Decimal("100")
    # IfrsType.CFS.value = "consolidated" — DB schema 일관.
    assert r.ifrs_type == "consolidated"
    assert r.citation_id == citation_id
    # superseded_by 본 cycle 미설정.
    assert r.superseded_by is None


def test_convert_to_financial_records_ofs_value() -> None:
    citation_id = uuid4()
    rows = (
        FinancialStatementRow(
            code="005930",
            fiscal_year=2023,
            fiscal_quarter=2,
            effective_date=date(2023, 6, 30),
            account="net_income",
            value=Decimal("50"),
            unit="krw",
            ifrs_type=IfrsType.OFS,
            rcept_no="O001",
            currency="KRW",
        ),
    )
    records = _convert_to_financial_records(rows=rows, citation_id=citation_id)
    assert records[0].fiscal_period == "2023Q2"
    assert records[0].ifrs_type == "separate"


# =============================================================================
# M1 회귀 — citations 빈 list guard
# =============================================================================

def test_empty_citations_raises_adapter_error() -> None:
    """oracle T19 M1 회귀 — fetch result 의 citations 빈 list 시 명시 raise.

    DartAdapter 직접 호출 시 invariant 보장이지만 mock / test double 이 빈
    citations 반환하면 명시 AdapterError + 운영 alert 명확화.
    """
    from unittest.mock import MagicMock

    from app.adapters.base import FetchResult
    from app.adapters.dart_adapter import DartAdapter

    # adapter 의 fetch_financial_statement 만 직접 mock — citations=() 반환.
    mock_adapter = MagicMock(spec=DartAdapter)
    mock_adapter.fetch_financial_statement = MagicMock(
        return_value=FetchResult(
            data=(),
            citations=(),  # 빈 list — invariant 위반.
            warnings=(),
            estimated_fields=frozenset(),
        ),
    )

    mapping = CorpCodeMapping.from_dict({"005930": "00126380"})
    batch = DartDailyBatch(
        adapter=mock_adapter,
        corp_mapping=mapping,
        citation_repo=FakeCitationRepository(),
        financial_repo=FakeFinancialRepository(records=()),
        throttle_seconds=0,
    )
    summary = batch.run(
        stock_codes=["005930"], fiscal_year=2023, fiscal_quarter=4,
    )
    # 회사 단위 failure — 명시 AdapterError 메시지.
    assert summary.success_count == 0
    assert summary.failure_count == 1
    failure_reason = summary.failures[0][1]
    assert "no citation" in failure_reason
    assert "invariant violation" in failure_reason


# =============================================================================
# 9. citation 이 financial 보다 먼저 save 됨
# =============================================================================

def test_citation_saved_before_financial() -> None:
    """citation_id FK 만족 — orchestrator 가 순서 보장."""
    cfs_rows = [_dart_row(rcept_no="C001")]
    ofs_rows = [_dart_row(rcept_no="O001")]
    adapter = _adapter_with_handler(
        _fs_div_response_handler(
            cfs_payload=_dart_response(rows=cfs_rows),
            ofs_payload=_dart_response(rows=ofs_rows),
        ),
    )
    mapping = CorpCodeMapping.from_dict({"005930": "00126380"})

    # citation save call 을 추적 — financial save 전에 호출돼야 함.
    save_order: list[str] = []
    citation_repo = FakeCitationRepository()
    financial_repo = FakeFinancialRepository(records=())

    orig_cit_save = citation_repo.save
    orig_fin_save = financial_repo.save_financials

    def _track_cit(c: Any) -> Any:
        save_order.append("citation")
        return orig_cit_save(c)

    def _track_fin(records: Any) -> Any:
        save_order.append("financial")
        return orig_fin_save(records)

    citation_repo.save = _track_cit  # type: ignore[method-assign]
    financial_repo.save_financials = _track_fin  # type: ignore[method-assign]

    batch = DartDailyBatch(
        adapter=adapter,
        corp_mapping=mapping,
        citation_repo=citation_repo,
        financial_repo=financial_repo,
        throttle_seconds=0,
    )
    summary = batch.run(
        stock_codes=["005930"], fiscal_year=2023, fiscal_quarter=4,
    )
    assert summary.success_count == 1
    # 매 IFRS type 마다 citation → financial 순서. CFS+OFS 두 짝.
    assert save_order == ["citation", "financial", "citation", "financial"]
