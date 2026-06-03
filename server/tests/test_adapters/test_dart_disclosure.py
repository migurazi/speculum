"""DartAdapter.fetch_disclosure_list 단위 테스트 — ADR-0026 공시 metadata.

테스트 매트릭스:
    1. 정상 path — DisclosureItem 추출 (report_nm / rcept_dt / rcept_no / dart_url)
    2. 최신순 (rcept_date 역순) 정렬
    3. status "013" (데이터 없음) → 빈 결과 (에러 아님)
    4. 빈 list (status 000) → 빈 결과
    5. rcept_dt 파싱 실패 row → skip + warning
    6. 필수 필드 (rcept_no / report_nm) 부재 row → skip
    7. citation = 대표 1 개 (최신 공시 기준)
    8. status "010" (auth) → AdapterError (013 만 빈 결과로 흡수)
    9. status "020" (rate limit) → AdapterRetryError
    10. 입력 검증 (corp_code / bgn_de / end_de / page_count)
    11. page_count 파라미터 전달
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from app.adapters.base import (
    AdapterError,
    AdapterRetryError,
    FetchResult,
)
from app.adapters.dart_adapter import DartAdapter, DisclosureItem
from app.models.source_citation import SourceKind

# =============================================================================
# Fixture builders
# =============================================================================

def _disclosure_row(
    *,
    report_nm: str = "분기보고서 (2024.03)",
    rcept_dt: str = "20240515",
    rcept_no: str = "20240515000123",
    corp_name: str = "삼성전자",
) -> dict[str, Any]:
    """DART list.json list 의 1 entry (불필요 필드 포함 — route 가 무시)."""
    return {
        "corp_code": "00126380",
        "corp_name": corp_name,
        "stock_code": "005930",
        "report_nm": report_nm,
        "rcept_no": rcept_no,
        "flr_nm": corp_name,
        "rcept_dt": rcept_dt,
        "rm": "",
    }


def _list_response(
    *,
    rows: list[dict[str, Any]] | None = None,
    status: str = "000",
    message: str = "정상",
) -> dict[str, Any]:
    """DART list.json 응답 JSON 모사."""
    payload: dict[str, Any] = {"status": status, "message": message}
    if rows is not None:
        payload["list"] = rows
    elif status == "000":
        payload["list"] = []
    return payload


def _adapter_with_response(
    response_json: dict[str, Any],
    *,
    status_code: int = 200,
    capture: dict[str, Any] | None = None,
) -> DartAdapter:
    """fixed JSON 응답 반환 mock adapter. capture 에 마지막 request params 기록."""
    def _handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["params"] = dict(request.url.params)
            capture["url"] = str(request.url)
        return httpx.Response(status_code, json=response_json)

    client = httpx.Client(transport=httpx.MockTransport(_handler), timeout=5.0)
    return DartAdapter(http_client=client, api_key="test_key")


# =============================================================================
# 1. 정상 path — 추출
# =============================================================================

def test_fetch_disclosure_list_extracts_fields() -> None:
    adapter = _adapter_with_response(_list_response(rows=[
        _disclosure_row(
            report_nm="분기보고서 (2024.03)",
            rcept_dt="20240515",
            rcept_no="20240515000123",
        ),
    ]))
    result = adapter.fetch_disclosure_list(
        corp_code="00126380", bgn_de="20240101", end_de="20241231",
    )
    assert isinstance(result, FetchResult)
    assert len(result.data) == 1
    item = result.data[0]
    assert isinstance(item, DisclosureItem)
    assert item.report_name == "분기보고서 (2024.03)"
    assert item.rcept_date == date(2024, 5, 15)
    assert item.rcept_no == "20240515000123"
    assert item.dart_url == (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20240515000123"
    )


# =============================================================================
# 2. 최신순 정렬
# =============================================================================

def test_fetch_disclosure_list_sorts_latest_first() -> None:
    # 응답 순서는 뒤섞여 있어도 rcept_date 역순으로 정렬되어야 함.
    adapter = _adapter_with_response(_list_response(rows=[
        _disclosure_row(report_nm="A", rcept_dt="20240301", rcept_no="20240301000001"),
        _disclosure_row(report_nm="B", rcept_dt="20240515", rcept_no="20240515000001"),
        _disclosure_row(report_nm="C", rcept_dt="20240110", rcept_no="20240110000001"),
    ]))
    result = adapter.fetch_disclosure_list(
        corp_code="00126380", bgn_de="20240101", end_de="20241231",
    )
    dates = [d.rcept_date for d in result.data]
    assert dates == [date(2024, 5, 15), date(2024, 3, 1), date(2024, 1, 10)]
    assert [d.report_name for d in result.data] == ["B", "A", "C"]


# =============================================================================
# 3. status "013" (데이터 없음) → 빈 결과
# =============================================================================

def test_fetch_disclosure_list_no_data_returns_empty() -> None:
    """공시 없는 종목 — status "013" 은 에러 아닌 정상 빈 결과 (ADR-0026)."""
    adapter = _adapter_with_response(
        _list_response(status="013", message="조회된 데이타가 없습니다.")
    )
    result = adapter.fetch_disclosure_list(
        corp_code="00126380", bgn_de="20240101", end_de="20241231",
    )
    assert result.data == ()
    assert result.citations == ()


# =============================================================================
# 4. 빈 list (status 000) → 빈 결과
# =============================================================================

def test_fetch_disclosure_list_empty_list_returns_empty() -> None:
    adapter = _adapter_with_response(_list_response(rows=[]))
    result = adapter.fetch_disclosure_list(
        corp_code="00126380", bgn_de="20240101", end_de="20241231",
    )
    assert result.data == ()
    assert result.citations == ()


# =============================================================================
# 5. rcept_dt 파싱 실패 → skip + warning
# =============================================================================

def test_fetch_disclosure_list_bad_rcept_dt_skipped() -> None:
    adapter = _adapter_with_response(_list_response(rows=[
        _disclosure_row(report_nm="OK", rcept_dt="20240515", rcept_no="20240515000001"),
        _disclosure_row(report_nm="BAD", rcept_dt="2024-05-15", rcept_no="20240515000002"),
        _disclosure_row(report_nm="EMPTY", rcept_dt="", rcept_no="20240515000003"),
        _disclosure_row(report_nm="INVALID", rcept_dt="20240230", rcept_no="20240515000004"),
    ]))
    result = adapter.fetch_disclosure_list(
        corp_code="00126380", bgn_de="20240101", end_de="20241231",
    )
    # 유효 1 개만, 3 개 skip.
    assert len(result.data) == 1
    assert result.data[0].report_name == "OK"
    assert any("skipped" in w for w in result.warnings)


# =============================================================================
# 6. 필수 필드 부재 → skip
# =============================================================================

def test_fetch_disclosure_list_missing_required_fields_skipped() -> None:
    adapter = _adapter_with_response(_list_response(rows=[
        {"report_nm": "no-rcept-no", "rcept_dt": "20240515"},  # rcept_no 부재
        {"rcept_no": "20240515000002", "rcept_dt": "20240515"},  # report_nm 부재
        _disclosure_row(report_nm="GOOD", rcept_dt="20240515", rcept_no="20240515000003"),
    ]))
    result = adapter.fetch_disclosure_list(
        corp_code="00126380", bgn_de="20240101", end_de="20241231",
    )
    assert len(result.data) == 1
    assert result.data[0].report_name == "GOOD"


# =============================================================================
# 7. citation — 대표 1 개 (최신 공시)
# =============================================================================

def test_fetch_disclosure_list_citation_latest() -> None:
    adapter = _adapter_with_response(_list_response(rows=[
        _disclosure_row(rcept_dt="20240110", rcept_no="20240110000001"),
        _disclosure_row(rcept_dt="20240515", rcept_no="20240515000099"),
    ]))
    result = adapter.fetch_disclosure_list(
        corp_code="00126380", bgn_de="20240101", end_de="20241231",
    )
    assert len(result.citations) == 1
    cit = result.citations[0]
    assert cit.source == SourceKind.DART
    # 최신 공시 (5-15) 의 rcept_no 가 identifier.
    assert cit.identifier == "20240515000099"
    assert cit.effective_date == date(2024, 5, 15)


# =============================================================================
# 8. status "010" (auth) → AdapterError (013 만 흡수)
# =============================================================================

def test_fetch_disclosure_list_auth_failure_raises() -> None:
    adapter = _adapter_with_response(
        _list_response(status="010", message="등록되지 않은 키입니다.")
    )
    with pytest.raises(AdapterError):
        adapter.fetch_disclosure_list(
            corp_code="00126380", bgn_de="20240101", end_de="20241231",
        )


# =============================================================================
# 9. status "020" (rate limit) → AdapterRetryError
# =============================================================================

def test_fetch_disclosure_list_rate_limit_raises_retry() -> None:
    adapter = _adapter_with_response(
        _list_response(status="020", message="요청 제한을 초과하였습니다.")
    )
    with pytest.raises(AdapterRetryError):
        adapter.fetch_disclosure_list(
            corp_code="00126380", bgn_de="20240101", end_de="20241231",
        )


# =============================================================================
# 10. 입력 검증
# =============================================================================

@pytest.mark.parametrize("corp_code", ["123", "abcdefgh", "0012638", "001263800"])
def test_fetch_disclosure_list_invalid_corp_code(corp_code: str) -> None:
    adapter = _adapter_with_response(_list_response(rows=[]))
    with pytest.raises(AdapterError):
        adapter.fetch_disclosure_list(
            corp_code=corp_code, bgn_de="20240101", end_de="20241231",
        )


@pytest.mark.parametrize("bad_date", ["2024-01-01", "20241301", "abc", "2024011"])
def test_fetch_disclosure_list_invalid_date(bad_date: str) -> None:
    adapter = _adapter_with_response(_list_response(rows=[]))
    with pytest.raises(AdapterError):
        adapter.fetch_disclosure_list(
            corp_code="00126380", bgn_de=bad_date, end_de="20241231",
        )


@pytest.mark.parametrize("page_count", [0, 101, -1])
def test_fetch_disclosure_list_invalid_page_count(page_count: int) -> None:
    adapter = _adapter_with_response(_list_response(rows=[]))
    with pytest.raises(AdapterError):
        adapter.fetch_disclosure_list(
            corp_code="00126380", bgn_de="20240101", end_de="20241231",
            page_count=page_count,
        )


# =============================================================================
# 11. params 전달 — corp_code / 기간 / page_count
# =============================================================================

def test_fetch_disclosure_list_sends_params() -> None:
    capture: dict[str, Any] = {}
    adapter = _adapter_with_response(
        _list_response(rows=[
            _disclosure_row(rcept_dt="20240515", rcept_no="20240515000001"),
        ]),
        capture=capture,
    )
    adapter.fetch_disclosure_list(
        corp_code="00126380", bgn_de="20240101", end_de="20240601",
        page_count=50,
    )
    params = capture["params"]
    assert params["corp_code"] == "00126380"
    assert params["bgn_de"] == "20240101"
    assert params["end_de"] == "20240601"
    assert params["page_count"] == "50"
    assert "list.json" in capture["url"]
