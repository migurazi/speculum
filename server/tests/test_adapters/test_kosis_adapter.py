"""KosisAdapter 단위 테스트 — fixture 기반 (ADR-0036 D2/D3/D4).

httpx.MockTransport 를 사용하여 실 KOSIS API 호출 없이 테스트.
EcosAdapter 테스트 패턴(_adapter_with_response, MockTransport)을 그대로 모방.

테스트 매트릭스:
    1.  정상 파싱 — 월별(M), MacroIndicatorRow 변환 + indicator_id + reference_date.
    2.  정상 파싱 — 연간(Y), reference_date = 1월 1일.
    3.  정상 파싱 — 분기(Q), YYYYQQ 6자리, reference_date = 분기 시작월 1일.
    4.  정상 파싱 — 반기(H), YYYYHH 6자리, HH=01→1월/HH=02→7월.
    5.  정상 파싱 — 일별(D), YYYYMMDD 파싱.
    6.  DT 결측("-") skip + warning.
    7.  DT 결측(빈 string) skip + warning.
    8.  음수 DT 허용 (거시지표 음수 정상).
    9.  PRD_DE 파싱 실패 skip + warning.
    10. vintage_date = observed_date 주입 확인.
    11. err 응답(err 10/13/20/기타) → 전부 AdapterError (KOSIS err 전부 영구).
    12. err "10" → AdapterError (재시도 아님 명시).
    13. 빈 array → 빈 FetchResult (warning 포함).
    14. citation (source=KOSIS, identifier, url=뷰어, apiKey 미포함).
    15. params 캡처 (apiKey·orgId·tblId·format=json 전달 확인).
    16. api_key 없음 → AdapterError.
    17. api_key env var 사용.
    18. HTTP 5xx → AdapterRetryError.
    19. HTTP 4xx → AdapterError.
    20. httpx.ConnectError → AdapterRetryError.
    21. httpx.TimeoutException → AdapterRetryError.
    22. health_check — key 설정/미설정.
    23. _parse_prd_de 직접 단위 테스트 (공식 YYYYHH/YYYYQQ + 방어 케이스).
    24. DT 천단위 콤마 → 정상 파싱.
    25. 입력 검증 — 알 수 없는 prd_se → AdapterError.
    26. 입력 검증 — 빈 org_id/tbl_id/itm_id → AdapterError.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.adapters.base import (
    AdapterError,
    AdapterRetryError,
    FetchResult,
    MacroIndicatorRow,
)
from app.adapters.kosis_adapter import KosisAdapter, _parse_prd_de
from app.models.source_citation import SourceKind

# =============================================================================
# Fixture builders — KOSIS 응답 JSON 모사
# =============================================================================

_DEFAULT_ORG_ID = "101"
_DEFAULT_TBL_ID = "DT_1B040A3"
_DEFAULT_ITM_ID = "T10"
_DEFAULT_OBJ_L = "ALL"
_DEFAULT_PRD_SE = "M"
_DEFAULT_START = "202401"
_DEFAULT_END = "202406"


def _kosis_item(
    *,
    org_id: str = _DEFAULT_ORG_ID,
    tbl_id: str = _DEFAULT_TBL_ID,
    tbl_nm: str = "소비자물가지수",
    itm_id: str = _DEFAULT_ITM_ID,
    itm_nm: str = "전체",
    unit_nm: str = "지수",
    prd_se: str = "M",
    prd_de: str = "202401",
    dt: str = "110.5",
    lst_chn_de: str = "20240201",
) -> dict[str, Any]:
    """단일 KOSIS statisticsData item dict."""
    return {
        "ORG_ID": org_id,
        "TBL_ID": tbl_id,
        "TBL_NM": tbl_nm,
        "ITM_ID": itm_id,
        "ITM_NM": itm_nm,
        "UNIT_NM": unit_nm,
        "PRD_SE": prd_se,
        "PRD_DE": prd_de,
        "DT": dt,
        "LST_CHN_DE": lst_chn_de,
    }


def _kosis_error_response(err: str, err_msg: str) -> dict[str, Any]:
    """KOSIS 에러 응답 JSON 모사 — dict with `err` key."""
    return {"err": err, "errMsg": err_msg}


def _mock_transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _adapter_with_response(
    response_json: Any,
    *,
    status_code: int = 200,
) -> KosisAdapter:
    """fixed JSON 응답을 반환하는 mock client adapter."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=response_json)

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    return KosisAdapter(http_client=client, api_key="test_kosis_key")


def _fetch(
    adapter: KosisAdapter,
    *,
    org_id: str = _DEFAULT_ORG_ID,
    tbl_id: str = _DEFAULT_TBL_ID,
    itm_id: str = _DEFAULT_ITM_ID,
    obj_l: str = _DEFAULT_OBJ_L,
    prd_se: str = _DEFAULT_PRD_SE,
    start_prd: str = _DEFAULT_START,
    end_prd: str = _DEFAULT_END,
    batch_id: UUID | None = None,
    observed_date: date = date(2024, 7, 1),
) -> FetchResult[tuple[MacroIndicatorRow, ...]]:
    """fetch_statistic 헬퍼 — 공통 인자 기본값."""
    return adapter.fetch_statistic(
        org_id=org_id,
        tbl_id=tbl_id,
        itm_id=itm_id,
        obj_l=obj_l,
        prd_se=prd_se,
        start_prd=start_prd,
        end_prd=end_prd,
        batch_id=batch_id or uuid4(),
        observed_date=observed_date,
    )


# =============================================================================
# _parse_prd_de 단위 테스트 (#23)
# =============================================================================

@pytest.mark.parametrize(
    "prd_de,prd_se,expected",
    [
        # Y (연간): YYYY → 1월 1일.
        ("2024", "Y", date(2024, 1, 1)),
        ("2023", "Y", date(2023, 1, 1)),
        # H (반기): YYYYHH 6자리, HH=01→1월 1일, 02→7월 1일 (공식 형식).
        ("202401", "H", date(2024, 1, 1)),  # 2024 1반기.
        ("202402", "H", date(2024, 7, 1)),  # 2024 2반기.
        ("202301", "H", date(2023, 1, 1)),
        ("202302", "H", date(2023, 7, 1)),
        # H — 허용 범위 밖 HH → None.
        ("202403", "H", None),              # HH=03 없음.
        ("202400", "H", None),              # HH=00 없음.
        # "S" — KOSIS 공식 명세에 없음 → None.
        ("202401", "S", None),
        # Q (분기): YYYYQQ 6자리, QQ=01~04 (공식 형식).
        ("202401", "Q", date(2024, 1, 1)),  # Q1 → 1월.
        ("202402", "Q", date(2024, 4, 1)),  # Q2 → 4월.
        ("202403", "Q", date(2024, 7, 1)),  # Q3 → 7월.
        ("202404", "Q", date(2024, 10, 1)), # Q4 → 10월.
        # Q — 허용 범위 밖 QQ → None.
        ("202405", "Q", None),              # QQ=05 없음.
        ("202400", "Q", None),              # QQ=00 없음.
        # M (월별): YYYYMM → 해당 월 1일.
        ("202401", "M", date(2024, 1, 1)),
        ("202312", "M", date(2023, 12, 1)),
        # D (일별): YYYYMMDD → 해당 일.
        ("20240115", "D", date(2024, 1, 15)),
        ("20231231", "D", date(2023, 12, 31)),
        # 방어 — 빈 string.
        ("", "M", None),
        # 방어 — 길이 불일치.
        ("20240", "M", None),    # M은 6자리인데 5자리.
        ("2024", "M", None),
        # 방어 — 알 수 없는 prd_se.
        ("202401", "X", None),
        # 방어 — 알파 혼입 ("2024H1" 등) → 전체 isdigit 검증.
        ("2024H1", "H", None),
        ("2024Q1", "Q", None),
        # 방어 — 연도 범위 밖.
        ("18990101", "D", None),
        # 방어 — 2월 30일 (존재하지 않는 날짜).
        ("20240230", "D", None),
        # 윤년 2월 29일 — 허용.
        ("20240229", "D", date(2024, 2, 29)),
    ],
)
def test_parse_prd_de_parametrized(
    prd_de: str, prd_se: str, expected: date | None
) -> None:
    """PRD_DE prd_se 별 파싱 정확성 + 방어 케이스 (공식 YYYYHH/YYYYQQ 형식)."""
    assert _parse_prd_de(prd_de, prd_se) == expected


# =============================================================================
# 1. 정상 파싱 — 월별(M)
# =============================================================================

def test_fetch_statistic_monthly_canonical_rows() -> None:
    """정상 path — M cycle 월별, canonical MacroIndicatorRow 변환."""
    items = [
        _kosis_item(prd_de="202401", dt="110.5", unit_nm="지수"),
        _kosis_item(prd_de="202402", dt="111.0", unit_nm="지수"),
    ]
    adapter = _adapter_with_response(items)

    result = _fetch(adapter, prd_se="M", start_prd="202401", end_prd="202402")

    assert isinstance(result, FetchResult)
    assert len(result.data) == 2

    row0 = result.data[0]
    assert isinstance(row0, MacroIndicatorRow)
    # indicator_id 규약 확인.
    assert row0.indicator_id == f"kosis/{_DEFAULT_ORG_ID}/{_DEFAULT_TBL_ID}/{_DEFAULT_ITM_ID}"
    # M cycle PRD_DE 파싱 — YYYYMM → 해당 월 1일.
    assert row0.reference_date == date(2024, 1, 1)
    assert row0.value == Decimal("110.5")
    assert row0.unit == "지수"
    # vintage_date = observed_date.
    assert row0.vintage_date == date(2024, 7, 1)

    assert result.data[1].reference_date == date(2024, 2, 1)


# =============================================================================
# 2. 정상 파싱 — 연간(Y)
# =============================================================================

def test_fetch_statistic_annual_reference_date() -> None:
    """Y cycle — reference_date = 1월 1일."""
    items = [_kosis_item(prd_de="2023", prd_se="Y", dt="3.2", unit_nm="연%")]
    adapter = _adapter_with_response(items)

    result = _fetch(adapter, prd_se="Y", start_prd="2023", end_prd="2024")

    assert len(result.data) == 1
    assert result.data[0].reference_date == date(2023, 1, 1)
    assert result.data[0].value == Decimal("3.2")


# =============================================================================
# 3. 정상 파싱 — 분기(Q)
# =============================================================================

def test_fetch_statistic_quarterly_reference_date() -> None:
    """Q cycle — YYYYQQ 6자리, reference_date = 분기 시작월 1일."""
    items = [
        _kosis_item(prd_de="202401", prd_se="Q", dt="2.1"),  # Q1 → 1월.
        _kosis_item(prd_de="202402", prd_se="Q", dt="2.3"),  # Q2 → 4월.
        _kosis_item(prd_de="202403", prd_se="Q", dt="2.5"),  # Q3 → 7월.
        _kosis_item(prd_de="202404", prd_se="Q", dt="2.7"),  # Q4 → 10월.
    ]
    adapter = _adapter_with_response(items)

    result = _fetch(adapter, prd_se="Q", start_prd="202401", end_prd="202404")

    assert len(result.data) == 4
    assert result.data[0].reference_date == date(2024, 1, 1)
    assert result.data[1].reference_date == date(2024, 4, 1)
    assert result.data[2].reference_date == date(2024, 7, 1)
    assert result.data[3].reference_date == date(2024, 10, 1)


# =============================================================================
# 4. 정상 파싱 — 반기(H)
# =============================================================================

def test_fetch_statistic_semiannual_parsing() -> None:
    """H cycle — YYYYHH 6자리, HH=01→1월 1일, HH=02→7월 1일 (공식 형식)."""
    items = [
        _kosis_item(prd_de="202401", prd_se="H", dt="2.0"),  # 2024 1반기.
        _kosis_item(prd_de="202402", prd_se="H", dt="2.5"),  # 2024 2반기.
    ]
    adapter = _adapter_with_response(items)

    result = _fetch(adapter, prd_se="H", start_prd="202401", end_prd="202402")

    assert len(result.data) == 2
    assert result.data[0].reference_date == date(2024, 1, 1)
    assert result.data[1].reference_date == date(2024, 7, 1)


# =============================================================================
# 5. 정상 파싱 — 일별(D)
# =============================================================================

def test_fetch_statistic_daily_parsing() -> None:
    """D cycle — YYYYMMDD 파싱."""
    items = [_kosis_item(prd_de="20240115", prd_se="D", dt="105.3")]
    adapter = _adapter_with_response(items)

    result = _fetch(adapter, prd_se="D", start_prd="20240115", end_prd="20240115")

    assert len(result.data) == 1
    assert result.data[0].reference_date == date(2024, 1, 15)


# =============================================================================
# 6~7. DT 결측 skip + warning
# =============================================================================

def test_dt_dash_skipped_with_warning() -> None:
    """DT="-" 결측 — row skip + warning."""
    items = [
        _kosis_item(prd_de="202401", dt="110.5"),
        _kosis_item(prd_de="202402", dt="-"),   # 결측.
        _kosis_item(prd_de="202403", dt="112.0"),
    ]
    adapter = _adapter_with_response(items)

    result = _fetch(adapter)

    assert len(result.data) == 2
    skip_warning = next(w for w in result.warnings if "DT 결측" in w)
    assert "1 rows skipped" in skip_warning


def test_dt_empty_string_skipped_with_warning() -> None:
    """DT=빈 string 결측 — row skip + warning."""
    items = [
        _kosis_item(prd_de="202401", dt="110.5"),
        _kosis_item(prd_de="202402", dt=""),    # 빈 string.
    ]
    adapter = _adapter_with_response(items)

    result = _fetch(adapter)

    assert len(result.data) == 1
    assert any("DT 결측" in w for w in result.warnings)


# =============================================================================
# 8. 음수 DT 허용
# =============================================================================

def test_negative_dt_allowed() -> None:
    """음수 DT — 거시지표 음수(성장률 등) 정상 허용."""
    items = [_kosis_item(prd_de="202401", dt="-1.5")]
    adapter = _adapter_with_response(items)

    result = _fetch(adapter)

    assert len(result.data) == 1
    assert result.data[0].value == Decimal("-1.5")
    assert len(result.warnings) == 0


# =============================================================================
# 9. PRD_DE 파싱 실패 skip + warning
# =============================================================================

def test_prd_de_parse_failure_skips_row_with_warning() -> None:
    """PRD_DE 형식 불일치 — row skip + warning."""
    items = [
        _kosis_item(prd_de="202401", dt="110.5"),
        _kosis_item(prd_de="BADDE", dt="110.0"),   # 파싱 실패.
        _kosis_item(prd_de="202403", dt="111.0"),
    ]
    adapter = _adapter_with_response(items)

    result = _fetch(adapter)

    assert len(result.data) == 2
    parse_warning = next(w for w in result.warnings if "PRD_DE 파싱 실패" in w)
    assert "1 rows skipped" in parse_warning


# =============================================================================
# 10. vintage_date = observed_date
# =============================================================================

def test_vintage_date_equals_observed_date() -> None:
    """vintage_date = observed_date (관측 시점 근사) 주입 확인."""
    observed = date(2024, 8, 15)
    items = [_kosis_item(prd_de="202401", dt="110.5")]
    adapter = _adapter_with_response(items)

    result = _fetch(adapter, observed_date=observed)

    for row in result.data:
        assert row.vintage_date == observed


# =============================================================================
# 11. KOSIS JSON err → 전부 AdapterError (영구 설정·요청 오류)
# =============================================================================

@pytest.mark.parametrize("err_code", ["10", "13", "15", "20", "30", "99"])
def test_err_json_all_raises_adapter_error(err_code: str) -> None:
    """KOSIS JSON err 코드(10/13/15/20/30/기타) → 전부 AdapterError (영구).

    KOSIS 공식 명세상 err 는 전부 설정·인증·요청 오류 — 재시도 무의미.
    트래픽 초과 공식 코드 미확인이라 retry set 비움(보수적).
    """
    adapter = _adapter_with_response(
        _kosis_error_response(err_code, "오류 메시지")
    )
    with pytest.raises(AdapterError) as exc_info:
        _fetch(adapter)
    # AdapterRetryError 는 AdapterError 의 subclass — 명시적으로 아님을 검증.
    assert not isinstance(exc_info.value, AdapterRetryError)


# =============================================================================
# 12. err "10" (인증) → AdapterError (재시도 아님 명시)
# =============================================================================

def test_err_10_auth_raises_adapter_error_not_retry() -> None:
    """KOSIS err 10(인증키 오류) → AdapterError (AdapterRetryError 아님).

    인증 오류는 재시도해도 무의미 — 영구 오류로 분류.
    """
    adapter = _adapter_with_response(
        _kosis_error_response("10", "인증키 오류")
    )
    with pytest.raises(AdapterError) as exc_info:
        _fetch(adapter)
    assert not isinstance(exc_info.value, AdapterRetryError)
    assert "err=10" in str(exc_info.value)


# =============================================================================
# 13. 빈 array → 빈 FetchResult + warning
# =============================================================================

def test_empty_array_returns_empty_fetch_result() -> None:
    """빈 JSON array → 빈 FetchResult (raise 아님), warning 포함."""
    adapter = _adapter_with_response([])

    result = _fetch(adapter)

    assert isinstance(result, FetchResult)
    assert result.data == ()
    assert len(result.citations) == 0
    assert len(result.warnings) >= 1
    assert any("빈 array" in w for w in result.warnings)


# =============================================================================
# 14. citation 검증 (source=KOSIS, identifier, url, apiKey 미포함)
# =============================================================================

def test_citation_source_kind_and_url() -> None:
    """citation — source=KOSIS, identifier, url=뷰어 링크, apiKey 미포함."""
    items = [_kosis_item(prd_de="202401", dt="110.5")]
    adapter = _adapter_with_response(items)
    batch_id = UUID("00000000-0000-0000-0000-000000000042")

    result = _fetch(
        adapter,
        org_id="101",
        tbl_id="DT_1B040A3",
        itm_id="T10",
        batch_id=batch_id,
        observed_date=date(2024, 7, 1),
    )

    assert len(result.citations) == 1
    cit = result.citations[0]

    # source = KOSIS.
    assert cit.source == SourceKind.KOSIS
    assert cit.adapter_version == "1.0.0"
    assert cit.batch_id == batch_id

    # identifier = org_id/tbl_id/itm_id.
    assert cit.identifier == "101/DT_1B040A3/T10"

    # url = 뷰어 URL (kosis.kr 도메인, orgId+tblId 포함).
    assert cit.url is not None
    assert cit.url.startswith("https://kosis.kr/")
    assert "orgId=101" in cit.url
    assert "tblId=DT_1B040A3" in cit.url

    # 보안 — apiKey 미포함.
    assert "test_kosis_key" not in cit.url
    assert "apiKey" not in cit.url

    # retrieved_at = UTC tz-aware.
    assert cit.retrieved_at.tzinfo is not None
    assert cit.retrieved_at.utcoffset() is not None


# =============================================================================
# 15. params 캡처 — apiKey·orgId·tblId·format=json 전달 확인
# =============================================================================

def test_params_include_required_keys() -> None:
    """apiKey·orgId·tblId·itmId·format=json 쿼리 파라미터 전달 확인."""
    captured_requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json=[_kosis_item()])

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = KosisAdapter(http_client=client, api_key="my_kosis_key")

    adapter.fetch_statistic(
        org_id="101",
        tbl_id="DT_1B040A3",
        itm_id="T10",
        obj_l="ALL",
        prd_se="M",
        start_prd="202401",
        end_prd="202406",
        batch_id=uuid4(),
        observed_date=date(2024, 7, 1),
    )

    assert len(captured_requests) == 1
    req = captured_requests[0]
    query = str(req.url)

    assert "apiKey=my_kosis_key" in query
    assert "orgId=101" in query
    assert "tblId=DT_1B040A3" in query
    assert "format=json" in query
    assert "method=getList" in query


# =============================================================================
# 16. api_key 없음 → AdapterError
# =============================================================================

def test_api_key_missing_raises_adapter_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API key 미설정 → AdapterError."""
    monkeypatch.delenv("KOSIS_API_KEY", raising=False)
    adapter = KosisAdapter()
    with pytest.raises(AdapterError, match="API key not configured"):
        _fetch(adapter)


# =============================================================================
# 17. api_key env var 사용
# =============================================================================

def test_api_key_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """KOSIS_API_KEY env var — query param 에 key 포함 확인."""
    monkeypatch.setenv("KOSIS_API_KEY", "env_kosis_key")
    captured_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(200, json=[_kosis_item()])

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = KosisAdapter(http_client=client)  # api_key 명시 X — env 사용.

    _fetch(adapter)

    assert len(captured_urls) == 1
    assert "env_kosis_key" in captured_urls[0]


# =============================================================================
# 18~21. HTTP/네트워크 에러 분류
# =============================================================================

def test_http_5xx_raises_retry_error() -> None:
    """HTTP 5xx → AdapterRetryError (상태 코드 포함, response.text 미포함)."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = KosisAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterRetryError) as exc_info:
        _fetch(adapter)
    assert "503" in str(exc_info.value)
    # 보안 — response.text 미포함 (apiKey echo 방지).
    assert "Service Unavailable" not in str(exc_info.value)


def test_http_4xx_raises_adapter_error() -> None:
    """HTTP 4xx → AdapterError (상태 코드 포함, response.text 미포함)."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = KosisAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterError) as exc_info:
        _fetch(adapter)
    assert "403" in str(exc_info.value)
    assert "Forbidden" not in str(exc_info.value)


def test_connect_error_raises_retry_error() -> None:
    """httpx.ConnectError → AdapterRetryError."""
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("kosis.kr down")

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = KosisAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterRetryError, match="connection failure"):
        _fetch(adapter)


def test_timeout_raises_retry_error() -> None:
    """httpx.TimeoutException → AdapterRetryError."""
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout")

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = KosisAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterRetryError, match="timeout"):
        _fetch(adapter)


# =============================================================================
# 22. health_check
# =============================================================================

def test_health_check_returns_true_when_key_configured() -> None:
    """API key 설정 시 health_check = True."""
    adapter = KosisAdapter(api_key="test_key")
    assert adapter.health_check() is True


def test_health_check_returns_false_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API key 미설정 시 health_check = False."""
    monkeypatch.delenv("KOSIS_API_KEY", raising=False)
    adapter = KosisAdapter()
    assert adapter.health_check() is False


# =============================================================================
# 추가 — indicator_id 규약, citation row 없을 때 empty citations
# =============================================================================

def test_indicator_id_format() -> None:
    """indicator_id = kosis/{org_id}/{tbl_id}/{itm_id} 형태."""
    items = [_kosis_item(prd_de="202401", dt="110.5")]
    adapter = _adapter_with_response(items)

    result = adapter.fetch_statistic(
        org_id="101",
        tbl_id="DT_1B040A3",
        itm_id="T10",
        obj_l="ALL",
        prd_se="M",
        start_prd="202401",
        end_prd="202401",
        batch_id=uuid4(),
        observed_date=date(2024, 7, 1),
    )

    for row in result.data:
        assert row.indicator_id == "kosis/101/DT_1B040A3/T10"


def test_citation_absent_when_all_rows_skipped() -> None:
    """모든 row 가 skip 될 때 citations 빈 tuple (citation 고아 방지)."""
    items = [
        _kosis_item(prd_de="202401", dt="-"),   # 결측 skip.
        _kosis_item(prd_de="202402", dt=""),    # 결측 skip.
    ]
    adapter = _adapter_with_response(items)

    result = _fetch(adapter)

    # rows 없으면 citation 도 없어야 함.
    assert result.data == ()
    assert len(result.citations) == 0


# =============================================================================
# 24. DT 천단위 콤마 → 정상 파싱 (F-05)
# =============================================================================

def test_dt_comma_thousands_parsed_correctly() -> None:
    """DT 천단위 콤마 ('1,234.5') → Decimal("1234.5") 정상 파싱."""
    items = [
        _kosis_item(prd_de="202401", dt="1,234.5"),   # 천단위 콤마.
        _kosis_item(prd_de="202402", dt="12,345"),     # 정수 천단위 콤마.
        _kosis_item(prd_de="202403", dt="1,234,567"),  # 복합 콤마.
    ]
    adapter = _adapter_with_response(items)

    result = _fetch(adapter)

    assert len(result.data) == 3
    assert result.data[0].value == Decimal("1234.5")
    assert result.data[1].value == Decimal("12345")
    assert result.data[2].value == Decimal("1234567")
    assert len(result.warnings) == 0


# =============================================================================
# 25. 입력 검증 — 알 수 없는 prd_se → AdapterError (F-09)
# =============================================================================

@pytest.mark.parametrize("invalid_prd_se", ["S", "X", "W", ""])
def test_invalid_prd_se_raises_adapter_error(invalid_prd_se: str) -> None:
    """알 수 없는 prd_se → AdapterError (설정 오류 silent skip 방지)."""
    adapter = _adapter_with_response([_kosis_item()])
    with pytest.raises(AdapterError, match="prd_se"):
        _fetch(adapter, prd_se=invalid_prd_se)


# =============================================================================
# 26. 입력 검증 — 빈 org_id/tbl_id/itm_id → AdapterError (F-09)
# =============================================================================

def test_empty_org_id_raises_adapter_error() -> None:
    """org_id 빈 문자열 → AdapterError."""
    adapter = _adapter_with_response([_kosis_item()])
    with pytest.raises(AdapterError, match="org_id"):
        _fetch(adapter, org_id="")


def test_empty_tbl_id_raises_adapter_error() -> None:
    """tbl_id 빈 문자열 → AdapterError."""
    adapter = _adapter_with_response([_kosis_item()])
    with pytest.raises(AdapterError, match="tbl_id"):
        _fetch(adapter, tbl_id="")


def test_empty_itm_id_raises_adapter_error() -> None:
    """itm_id 빈 문자열 → AdapterError."""
    adapter = _adapter_with_response([_kosis_item()])
    with pytest.raises(AdapterError, match="itm_id"):
        _fetch(adapter, itm_id="")
