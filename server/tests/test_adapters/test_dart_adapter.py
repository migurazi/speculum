"""DartAdapter 단위 테스트 — fixture 기반 (ADR-0003 D8) + dart_account_mapper.

테스트 매트릭스:
    1. fetch_financial_statement — 정상 path + canonical 변환
    2. CFS / OFS 분리 fs_div 파라미터
    3. 분기 → reprt_code 매핑
    4. dart_account_mapper — 매핑된 계정 / 미매핑 prefix
    5. 미매핑 row 도 보존 + warning 카운트
    6. API key 미설정 → AdapterError
    7. DART status="020" (rate limit) → AdapterRetryError
    8. DART status="010" (auth fail) → AdapterError
    9. HTTP 5xx → AdapterRetryError
    10. httpx ConnectError → AdapterRetryError
    11. 빈 list → AdapterError
    12. schema drift (account_id 누락) → AdapterError
    13. thstrm_amount 빈 string / "-" → row skip + warning 카운트
    14. Citation 7-tuple (source=DART, url=DART viewer)
    15. 입력 검증 (code / corp_code / quarter)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.adapters.base import (
    AdapterError,
    AdapterRetryError,
    FetchResult,
    FinancialStatementRow,
    IfrsType,
)
from app.adapters.dart_account_mapper import (
    UNMAPPED_PREFIX,
    is_unmapped,
    map_ifrs_account,
)
from app.adapters.dart_adapter import DartAdapter
from app.models.source_citation import SourceKind


# =============================================================================
# DART JSON fixture builder
# =============================================================================

def _dart_response(
    *,
    rows: list[dict[str, Any]] | None = None,
    status: str = "000",
    message: str = "정상",
) -> dict[str, Any]:
    """DART fnlttSinglAcntAll 응답 JSON 모사."""
    return {
        "status": status,
        "message": message,
        "list": rows or [],
    }


def _dart_row(
    *,
    account_id: str = "ifrs-full_Assets",
    account_nm: str = "자산총계",
    thstrm_amount: str = "455905830000000",
    rcept_no: str = "20240501000123",
    currency: str = "KRW",
    sj_div: str = "BS",
) -> dict[str, Any]:
    """단일 row dict (DART fnlttSinglAcntAll list 의 1 entry)."""
    return {
        "account_id": account_id,
        "account_nm": account_nm,
        "thstrm_amount": thstrm_amount,
        "rcept_no": rcept_no,
        "currency": currency,
        "sj_div": sj_div,
        "bsns_year": "2023",
        "reprt_code": "11011",
    }


def _mock_transport(handler: Any) -> httpx.MockTransport:
    """httpx.MockTransport — request callback 으로 응답 반환."""
    return httpx.MockTransport(handler)


def _adapter_with_response(
    response_json: dict[str, Any],
    *,
    status_code: int = 200,
) -> DartAdapter:
    """fixed JSON 응답을 반환하는 mock client adapter."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=response_json)

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    return DartAdapter(http_client=client, api_key="test_key")


# =============================================================================
# 1. 정상 path
# =============================================================================

def test_fetch_financial_statement_returns_canonical_rows() -> None:
    rows_raw = [
        _dart_row(
            account_id="ifrs-full_Assets",
            thstrm_amount="455905830000000",
        ),
        _dart_row(
            account_id="ifrs-full_Liabilities",
            thstrm_amount="100000000000000",
        ),
    ]
    adapter = _adapter_with_response(_dart_response(rows=rows_raw))

    result = adapter.fetch_financial_statement(
        code="005930",
        corp_code="00126380",
        fiscal_year=2023,
        fiscal_quarter=4,
        ifrs_type=IfrsType.CFS,
        batch_id=uuid4(),
    )
    assert isinstance(result, FetchResult)
    assert len(result.data) == 2
    # canonical key 변환.
    assets = next(r for r in result.data if r.account == "total_assets")
    assert assets.code == "005930"
    assert assets.value == Decimal("455905830000000")
    assert assets.ifrs_type == IfrsType.CFS
    assert assets.fiscal_year == 2023
    assert assets.fiscal_quarter == 4
    # effective_date = 분기말 (Q=4 → 12-31).
    assert assets.effective_date == date(2023, 12, 31)


# =============================================================================
# 2. CFS / OFS 분리 fs_div
# =============================================================================

@pytest.mark.parametrize(
    "ifrs_type,expected_fs_div",
    [
        # oracle 리뷰 C1 회귀 — IfrsType.value 가 "consolidated" / "separate"
        # 인데 DART wire format 은 "CFS" / "OFS". 매핑 테이블이 두 라벨 모두
        # 정확히 변환하는지 검증 (한쪽만 테스트 시 회귀 위험).
        (IfrsType.CFS, "CFS"),
        (IfrsType.OFS, "OFS"),
    ],
)
def test_fs_div_param_maps_ifrs_type_to_dart_wire_format(
    ifrs_type: IfrsType, expected_fs_div: str,
) -> None:
    """fs_div 파라미터가 IfrsType enum 의 wire format 으로 전달.

    enum.value 와 DART wire format 의 불일치를 _FS_DIV_BY_IFRS_TYPE 매핑이
    교정. 본 매핑 제거 시 silent 빈 응답 (오해석된 fs_div).
    """
    captured_params: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        return httpx.Response(
            200,
            json=_dart_response(rows=[_dart_row()]),
        )

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = DartAdapter(http_client=client, api_key="test_key")
    adapter.fetch_financial_statement(
        code="005930",
        corp_code="00126380",
        fiscal_year=2023,
        fiscal_quarter=4,
        ifrs_type=ifrs_type,
        batch_id=uuid4(),
    )
    # DART wire format = "CFS" / "OFS" (enum.value 의 "consolidated" /
    # "separate" 가 아님).
    assert captured_params["fs_div"] == expected_fs_div
    # enum.value 가 직접 전달되면 안 됨 (regression 차단).
    assert captured_params["fs_div"] != ifrs_type.value
    assert captured_params["bsns_year"] == "2023"
    assert captured_params["reprt_code"] == "11011"
    assert captured_params["corp_code"] == "00126380"
    assert captured_params["crtfc_key"] == "test_key"


# =============================================================================
# 3. 분기 → reprt_code 매핑
# =============================================================================

@pytest.mark.parametrize(
    "quarter,expected_reprt_code",
    [
        (1, "11013"),  # 1분기보고서.
        (2, "11012"),  # 반기보고서.
        (3, "11014"),  # 3분기보고서.
        (4, "11011"),  # 사업보고서 (연간).
    ],
)
def test_quarter_to_reprt_code_mapping(
    quarter: int, expected_reprt_code: str,
) -> None:
    captured_params: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        return httpx.Response(200, json=_dart_response(rows=[_dart_row()]))

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = DartAdapter(http_client=client, api_key="test_key")
    adapter.fetch_financial_statement(
        code="005930",
        corp_code="00126380",
        fiscal_year=2023,
        fiscal_quarter=quarter,
        ifrs_type=IfrsType.CFS,
        batch_id=uuid4(),
    )
    assert captured_params["reprt_code"] == expected_reprt_code


# =============================================================================
# 4. dart_account_mapper 단위
# =============================================================================

def test_account_mapper_known_mapping() -> None:
    assert map_ifrs_account("ifrs-full_Assets") == "total_assets"
    assert map_ifrs_account("ifrs-full_Equity") == "total_equity"
    assert map_ifrs_account("ifrs-full_Revenue") == "revenue"
    assert map_ifrs_account("dart_OperatingIncomeLoss") == "operating_income"
    assert (
        map_ifrs_account("ifrs-full_ProfitLossAttributableToOwnersOfParent")
        == "net_income_attributable_to_owners"
    )


def test_account_mapper_unmapped_preserves_id() -> None:
    """미매핑 ID 는 UNMAPPED_PREFIX 로 보존."""
    result = map_ifrs_account("ifrs-full_SomeRareAccount")
    assert result == f"{UNMAPPED_PREFIX}ifrs-full_SomeRareAccount"
    assert is_unmapped(result) is True


def test_account_mapper_empty_input() -> None:
    """빈/공백 입력 → 명시 unmapped."""
    assert map_ifrs_account("") == f"{UNMAPPED_PREFIX}<empty>"
    assert is_unmapped(map_ifrs_account("")) is True


# =============================================================================
# 5. 미매핑 row 보존 + warning 카운트
# =============================================================================

def test_fetch_preserves_unmapped_rows_with_warning() -> None:
    """oracle 리뷰 M4 회귀 — unmapped 계정 row 보존 + warning 메시지."""
    rows_raw = [
        _dart_row(account_id="ifrs-full_Assets", thstrm_amount="100"),
        _dart_row(account_id="ifrs-full_UnknownXYZ", thstrm_amount="200"),
    ]
    adapter = _adapter_with_response(_dart_response(rows=rows_raw))
    result = adapter.fetch_financial_statement(
        code="005930",
        corp_code="00126380",
        fiscal_year=2023,
        fiscal_quarter=4,
        ifrs_type=IfrsType.CFS,
        batch_id=uuid4(),
    )
    # 2 row 보존 — 미매핑 도 canonical 의 unmapped prefix 로 살아남음.
    assert len(result.data) == 2
    accounts = {r.account for r in result.data}
    assert "total_assets" in accounts
    assert f"{UNMAPPED_PREFIX}ifrs-full_UnknownXYZ" in accounts
    # warning 메시지 — unmapped accounts.
    unmapped_warning = next(
        w for w in result.warnings if "unmapped IFRS accounts" in w
    )
    assert "1 unmapped" in unmapped_warning


def test_fetch_estimated_fields_marks_effective_date() -> None:
    """oracle 리뷰 M3 회귀 — effective_date 분기말 근사를 estimated 로 명시.

    T18 ConflictDetector / PIT enforcer 가 본 metadata 로 비교 제외 / 보강.
    """
    rows_raw = [_dart_row(account_id="ifrs-full_Assets", thstrm_amount="100")]
    adapter = _adapter_with_response(_dart_response(rows=rows_raw))
    result = adapter.fetch_financial_statement(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4,
        ifrs_type=IfrsType.CFS, batch_id=uuid4(),
    )
    assert result.estimated_fields == frozenset({"effective_date"})


# =============================================================================
# 6. API key 미설정
# =============================================================================

def test_api_key_missing_raises_adapter_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DART_API_KEY", raising=False)
    adapter = DartAdapter()  # 명시 인자 X + env var 미설정.
    with pytest.raises(AdapterError, match="API key not configured"):
        adapter.fetch_financial_statement(
            code="005930",
            corp_code="00126380",
            fiscal_year=2023,
            fiscal_quarter=4,
            ifrs_type=IfrsType.CFS,
            batch_id=uuid4(),
        )


def test_api_key_from_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DART_API_KEY", "env_test_key")
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json=_dart_response(rows=[_dart_row()]))

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = DartAdapter(http_client=client)  # api_key 명시 X — env 사용.
    adapter.fetch_financial_statement(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4,
        ifrs_type=IfrsType.CFS, batch_id=uuid4(),
    )
    assert captured["crtfc_key"] == "env_test_key"


# =============================================================================
# 7. status="020" (rate limit) → AdapterRetryError
# =============================================================================

def test_status_020_maps_to_retry_error() -> None:
    adapter = _adapter_with_response(
        _dart_response(status="020", message="요청 한도 초과"),
    )
    with pytest.raises(AdapterRetryError, match="rate limit"):
        adapter.fetch_financial_statement(
            code="005930", corp_code="00126380",
            fiscal_year=2023, fiscal_quarter=4,
            ifrs_type=IfrsType.CFS, batch_id=uuid4(),
        )


# =============================================================================
# 8. status="010" (auth) → AdapterError
# =============================================================================

def test_status_010_maps_to_adapter_error() -> None:
    adapter = _adapter_with_response(
        _dart_response(status="010", message="등록되지 않은 키"),
    )
    with pytest.raises(AdapterError, match="auth failure"):
        adapter.fetch_financial_statement(
            code="005930", corp_code="00126380",
            fiscal_year=2023, fiscal_quarter=4,
            ifrs_type=IfrsType.CFS, batch_id=uuid4(),
        )


# =============================================================================
# 9. HTTP 5xx → AdapterRetryError
# =============================================================================

def test_http_5xx_maps_to_retry_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = DartAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterRetryError, match="503"):
        adapter.fetch_financial_statement(
            code="005930", corp_code="00126380",
            fiscal_year=2023, fiscal_quarter=4,
            ifrs_type=IfrsType.CFS, batch_id=uuid4(),
        )


def test_http_4xx_maps_to_adapter_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = DartAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterError, match="403"):
        adapter.fetch_financial_statement(
            code="005930", corp_code="00126380",
            fiscal_year=2023, fiscal_quarter=4,
            ifrs_type=IfrsType.CFS, batch_id=uuid4(),
        )


# =============================================================================
# 10. httpx ConnectError → AdapterRetryError
# =============================================================================

def test_connect_error_maps_to_retry_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("opendart.fss.or.kr down")

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = DartAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterRetryError, match="connection failure"):
        adapter.fetch_financial_statement(
            code="005930", corp_code="00126380",
            fiscal_year=2023, fiscal_quarter=4,
            ifrs_type=IfrsType.CFS, batch_id=uuid4(),
        )


# =============================================================================
# 11. 빈 list → AdapterError
# =============================================================================

def test_empty_list_raises_adapter_error() -> None:
    adapter = _adapter_with_response(_dart_response(rows=[]))
    with pytest.raises(AdapterError, match="empty 'list'"):
        adapter.fetch_financial_statement(
            code="005930", corp_code="00126380",
            fiscal_year=2023, fiscal_quarter=4,
            ifrs_type=IfrsType.CFS, batch_id=uuid4(),
        )


# =============================================================================
# 12. schema drift — account_id 누락
# =============================================================================

def test_schema_drift_missing_account_id() -> None:
    # account_id 가 없는 row — schema drift.
    bad_row: dict[str, Any] = {
        "account_nm": "자산총계",
        "thstrm_amount": "100",
        "rcept_no": "20240501000123",
    }
    adapter = _adapter_with_response(_dart_response(rows=[bad_row]))
    with pytest.raises(AdapterError, match="schema drift"):
        adapter.fetch_financial_statement(
            code="005930", corp_code="00126380",
            fiscal_year=2023, fiscal_quarter=4,
            ifrs_type=IfrsType.CFS, batch_id=uuid4(),
        )


# =============================================================================
# 13. thstrm_amount 빈/"-" → row skip + warning
# =============================================================================

def test_empty_amount_skipped_with_warning() -> None:
    """oracle 리뷰 M4 회귀 — skipped row vs unmapped account 분리 warning."""
    rows_raw = [
        _dart_row(account_id="ifrs-full_Assets", thstrm_amount="100"),
        _dart_row(account_id="ifrs-full_Liabilities", thstrm_amount=""),
        _dart_row(account_id="ifrs-full_Equity", thstrm_amount="-"),
    ]
    adapter = _adapter_with_response(_dart_response(rows=rows_raw))
    result = adapter.fetch_financial_statement(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4,
        ifrs_type=IfrsType.CFS, batch_id=uuid4(),
    )
    # 1 row 만 보존 (Assets).
    assert len(result.data) == 1
    assert result.data[0].account == "total_assets"
    # 2 row skip — warning 에 "rows skipped" 메시지.
    skip_warning = next(w for w in result.warnings if "rows skipped" in w)
    assert "2 rows skipped" in skip_warning


def test_amount_with_comma_parses_correctly() -> None:
    """DART 가 일부 응답에서 thstrm_amount 에 콤마 사용 (drift 대비)."""
    rows_raw = [
        _dart_row(account_id="ifrs-full_Assets", thstrm_amount="455,905,830,000,000"),
    ]
    adapter = _adapter_with_response(_dart_response(rows=rows_raw))
    result = adapter.fetch_financial_statement(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4,
        ifrs_type=IfrsType.CFS, batch_id=uuid4(),
    )
    assert result.data[0].value == Decimal("455905830000000")


# =============================================================================
# 14. Citation 7-tuple
# =============================================================================

def test_citation_seven_tuple() -> None:
    rows_raw = [_dart_row(rcept_no="20240501000123")]
    adapter = _adapter_with_response(_dart_response(rows=rows_raw))
    batch_id = UUID("00000000-0000-0000-0000-0000000000aa")
    result = adapter.fetch_financial_statement(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4,
        ifrs_type=IfrsType.CFS, batch_id=batch_id,
    )
    cit = result.citations[0]
    assert cit.source == SourceKind.DART
    assert cit.adapter_version == "1.0.0"
    assert cit.batch_id == batch_id
    assert cit.identifier == "20240501000123"
    # DART viewer URL 포함.
    assert cit.url is not None
    assert "rcpNo=20240501000123" in cit.url
    # retrieved_at = UTC tz-aware.
    assert cit.retrieved_at.utcoffset() == datetime(
        2024, 1, 1, tzinfo=timezone.utc,
    ).utcoffset()


# =============================================================================
# 15. 입력 검증
# =============================================================================

def test_invalid_code_raises() -> None:
    adapter = _adapter_with_response(_dart_response(rows=[_dart_row()]))
    with pytest.raises(AdapterError, match="6-digit numeric"):
        adapter.fetch_financial_statement(
            code="abc", corp_code="00126380",
            fiscal_year=2023, fiscal_quarter=4,
            ifrs_type=IfrsType.CFS, batch_id=uuid4(),
        )


def test_invalid_corp_code_raises() -> None:
    adapter = _adapter_with_response(_dart_response(rows=[_dart_row()]))
    with pytest.raises(AdapterError, match="8-digit numeric"):
        adapter.fetch_financial_statement(
            code="005930", corp_code="123",  # 8자리 아님.
            fiscal_year=2023, fiscal_quarter=4,
            ifrs_type=IfrsType.CFS, batch_id=uuid4(),
        )


def test_invalid_quarter_raises() -> None:
    adapter = _adapter_with_response(_dart_response(rows=[_dart_row()]))
    with pytest.raises(AdapterError, match="fiscal_quarter must be 1..4"):
        adapter.fetch_financial_statement(
            code="005930", corp_code="00126380",
            fiscal_year=2023, fiscal_quarter=5,  # invalid.
            ifrs_type=IfrsType.CFS, batch_id=uuid4(),
        )


def test_invalid_year_raises() -> None:
    adapter = _adapter_with_response(_dart_response(rows=[_dart_row()]))
    with pytest.raises(AdapterError, match="fiscal_year"):
        adapter.fetch_financial_statement(
            code="005930", corp_code="00126380",
            fiscal_year=1990, fiscal_quarter=4,  # < 2000.
            ifrs_type=IfrsType.CFS, batch_id=uuid4(),
        )


# =============================================================================
# 추가 — health_check, close
# =============================================================================

def test_health_check_returns_true_when_key_configured() -> None:
    adapter = DartAdapter(api_key="test_key")
    assert adapter.health_check() is True


def test_health_check_returns_false_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DART_API_KEY", raising=False)
    adapter = DartAdapter()
    assert adapter.health_check() is False
