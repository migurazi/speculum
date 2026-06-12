"""FscDividendAdapter 단위 테스트 — mock transport (ADR-0003 D8) + Fake calendar.

M7 #2 (ADR-0035 D3/D6) adapter 코어 검증.

테스트 매트릭스:
    1. getDiviInfo 정상 응답 (보통주 현금배당 1건) → CorporateActionRecord 필드
       (cash_amount / effective_date=배당기준일 직전거래일 / announced=effective /
        payment_date / details JSON-scalar / action_type / superseded_by=None)
    2. 단건 object vs 다건 array items.item 파싱
    3. 주당현금배당금 0 / 빈값 skip
    4. 우선주 skip + warning
    5. dvdnBasDt 없음 skip + warning
    6. 캘린더 범위 밖 (직전 거래일 산출 실패) skip + warning
    7. 페이징 (totalCount > numOfRows 다페이지)
    8. resultCode 비정상 (rate-limit retry vs auth error)
    9. 빈 items 정상 빈 결과
    10. params 캡처 (crno / beginBasDt / serviceKey 전달)
    11. HTTP 5xx → retry / 4xx → error / ConnectError → retry
    12. API key env fallback + 미설정 error
    13. citation 7-tuple (source=FSC, url=getDiviInfo)
    14. 입력 검증 (code / crno / 기간)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.adapters.base import AdapterError, AdapterRetryError, FetchResult
from app.adapters.fsc_dividend_adapter import DATA_GAP_PREFIX, FscDividendAdapter
from app.models.source_citation import SourceKind
from app.services.krx_calendar import CalendarRangeError

# =============================================================================
# Fake trading calendar — 직전 거래일 반환 (주입)
# =============================================================================

class _FakeCalendar:
    """주입용 Fake — `previous_business_day` 만 구현.

    `known` 에 등록된 일자만 직전 거래일을 반환하고, 미등록 일자는 캘린더 verified
    범위 밖을 모사하여 `CalendarRangeError` 를 raise (adapter 가 H2 narrow except
    로 skip + [DATA_GAP] warning 처리하는지 검증). 실제 TradingCalendar 가 던지는
    예외 타입과 일치 (H2 — 그 외 예외는 propagate 되어야 하므로 타입이 load-bearing).
    """

    def __init__(self, mapping: dict[date, date]) -> None:
        self._mapping = mapping

    def previous_business_day(self, d: date) -> date:
        if d not in self._mapping:
            # 실제 TradingCalendar 의 verified 범위 밖 신호와 동일 타입.
            raise CalendarRangeError(d, date(2024, 1, 1), date(2024, 12, 31))
        return self._mapping[d]


class _BuggyCalendar:
    """H2 — CalendarRangeError 가 아닌 예외 (프로그래밍 버그) 를 던지는 Fake.

    adapter 가 이를 silent 손실로 숨기지 않고 propagate 하는지 검증.
    """

    def previous_business_day(self, d: date) -> date:
        raise AttributeError("buggy calendar — should propagate, not be swallowed")


class _LookAheadCalendar:
    """X1 — 미래 날짜 (배당기준일 이상) 를 반환하는 buggy 캘린더.

    배당락일이 배당기준일보다 이르지 않으면 look-ahead 위험 → adapter 가
    invariant assert 로 fail-loud 하는지 검증.
    """

    def __init__(self, result: date) -> None:
        self._result = result

    def previous_business_day(self, d: date) -> date:
        return self._result


# 배당기준일 2024-12-31 → 직전 거래일 2024-12-30 (실 KRX: 12-31 휴장 가정).
_BAS_DT = date(2024, 12, 31)
_EX_DATE = date(2024, 12, 30)
_DEFAULT_CAL = _FakeCalendar({_BAS_DT: _EX_DATE})


# =============================================================================
# getDiviInfo JSON fixture builder
# =============================================================================

def _divi_item(
    *,
    stck_knd_nm: str = "보통주",
    stck_gnrl_dvdn_amt: str = "500",
    dvdn_bas_dt: str = "20241231",
    csh_dvdn_pay_dt: str = "20250401",
    dvdn_rcd_nm: str = "결산배당",
) -> dict[str, Any]:
    """getDiviInfo items.item 의 1 entry."""
    return {
        "crno": "1301110006246",
        "stckIssuCmpyNm": "테스트전자",
        "stckKndNm": stck_knd_nm,
        "stckGnrlDvdnAmt": stck_gnrl_dvdn_amt,
        "dvdnBasDt": dvdn_bas_dt,
        "cshDvdnPayDt": csh_dvdn_pay_dt,
        "dvdnRcdNm": dvdn_rcd_nm,
    }


def _divi_response(
    *,
    items: list[dict[str, Any]] | None = None,
    single_item: dict[str, Any] | None = None,
    total_count: int | None = None,
    result_code: str = "00",
    result_msg: str = "NORMAL SERVICE.",
    no_items_container: bool = False,
) -> dict[str, Any]:
    """공공데이터포털 getDiviInfo 응답 JSON 모사.

    single_item 주입 시 items.item 을 단일 object 로 (다건 array 와 구별).
    no_items_container 시 items 를 빈 string 으로 (無자료 관행).
    """
    if no_items_container:
        items_value: Any = ""
    elif single_item is not None:
        items_value = {"item": single_item}
    else:
        items_value = {"item": items or []}

    count = total_count
    if count is None:
        if single_item is not None:
            count = 1
        else:
            count = len(items or [])

    return {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": result_msg},
            "body": {
                "items": items_value,
                "numOfRows": 100,
                "pageNo": 1,
                "totalCount": count,
            },
        }
    }


def _adapter_with_response(
    response_json: dict[str, Any],
    *,
    status_code: int = 200,
) -> FscDividendAdapter:
    """fixed JSON 응답 mock client adapter."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=response_json)

    client = httpx.Client(transport=httpx.MockTransport(_handler), timeout=5.0)
    return FscDividendAdapter(http_client=client, api_key="test_key")


def _fetch(
    adapter: FscDividendAdapter,
    *,
    calendar: Any = _DEFAULT_CAL,
    code: str = "005930",
    crno: str = "1301110006246",
    begin: str = "20240101",
    end: str = "20241231",
    batch_id: UUID | None = None,
) -> FetchResult[Any]:
    return adapter.fetch_cash_dividends(
        code=code,
        crno=crno,
        begin_bas_dt=begin,
        end_bas_dt=end,
        batch_id=batch_id or uuid4(),
        trading_calendar=calendar,
    )


# =============================================================================
# 1. 정상 path — 보통주 현금배당 1건 → CorporateActionRecord 필드
# =============================================================================

def test_single_common_dividend_returns_record() -> None:
    adapter = _adapter_with_response(
        _divi_response(items=[_divi_item(stck_gnrl_dvdn_amt="500")]),
    )
    result = _fetch(adapter)

    assert isinstance(result, FetchResult)
    assert len(result.data) == 1
    rec = result.data[0]
    assert rec.action_type == "cash_dividend"
    assert rec.code == "005930"
    assert rec.cash_amount == Decimal("500")
    assert rec.ratio is None
    # effective_date = 배당기준일(2024-12-31) 직전 거래일 (2024-12-30 = 배당락).
    assert rec.effective_date == _EX_DATE
    # announced_date = effective_date (ADR-0035 D6).
    assert rec.announced_date == _EX_DATE
    # payment_date = cshDvdnPayDt 파싱.
    assert rec.payment_date == date(2025, 4, 1)
    # details JSON-scalar only — per_share 문자열.
    assert rec.details["per_share"] == "500"
    assert rec.details["type"] == "결산배당"
    assert rec.details["stock_knd"] == "보통주"
    assert all(isinstance(v, str) for v in rec.details.values())
    # superseded_by = None (insert NULL — ADR-0035 D2.2).
    assert rec.superseded_by is None
    # citation 동반 (record 있으면 비면 안 됨).
    assert len(result.citations) == 1
    assert result.citations[0].id == rec.citation_id
    assert result.estimated_fields == frozenset()


def test_amount_with_comma_parses() -> None:
    """주당현금배당금에 콤마 사용 (drift 대비)."""
    adapter = _adapter_with_response(
        _divi_response(items=[_divi_item(stck_gnrl_dvdn_amt="1,250")]),
    )
    result = _fetch(adapter)
    assert result.data[0].cash_amount == Decimal("1250")


# =============================================================================
# 2. 단건 object vs 다건 array 파싱
# =============================================================================

def test_single_item_object_parsed() -> None:
    """items.item 이 단일 object (1 건이면 array 아님) → 1 record."""
    adapter = _adapter_with_response(
        _divi_response(single_item=_divi_item(stck_gnrl_dvdn_amt="700")),
    )
    result = _fetch(adapter)
    assert len(result.data) == 1
    assert result.data[0].cash_amount == Decimal("700")


def test_multiple_items_array_parsed() -> None:
    """items.item 이 array (다건) → 여러 record."""
    adapter = _adapter_with_response(
        _divi_response(items=[
            _divi_item(stck_gnrl_dvdn_amt="500"),
            _divi_item(stck_gnrl_dvdn_amt="600"),
        ]),
    )
    result = _fetch(adapter)
    assert len(result.data) == 2
    amounts = {r.cash_amount for r in result.data}
    assert amounts == {Decimal("500"), Decimal("600")}
    # record 마다 citation 1개.
    assert len(result.citations) == 2


# =============================================================================
# 3. 주당현금배당금 0 / 빈값 skip
# =============================================================================

@pytest.mark.parametrize("amt", ["0", "", "-"])
def test_zero_or_empty_amount_skipped(amt: str) -> None:
    """주당현금배당금 0/빈값/'-' → 현금배당 아님 → skip + warning."""
    adapter = _adapter_with_response(
        _divi_response(items=[_divi_item(stck_gnrl_dvdn_amt=amt)]),
    )
    result = _fetch(adapter)
    assert len(result.data) == 0
    assert len(result.warnings) >= 1
    # record 0 → citation 비어있음 (정상).
    assert len(result.citations) == 0


# =============================================================================
# 4. 우선주 skip + warning
# =============================================================================

def test_preferred_stock_skipped_with_warning() -> None:
    """우선주는 본 cycle 범위 외 → skip + warning (보통주만)."""
    adapter = _adapter_with_response(
        _divi_response(items=[
            _divi_item(stck_knd_nm="우선주", stck_gnrl_dvdn_amt="550"),
        ]),
    )
    result = _fetch(adapter)
    assert len(result.data) == 0
    assert any("non-common" in w or "보통주" in w for w in result.warnings)


def test_mixed_common_and_preferred() -> None:
    """보통주 + 우선주 혼재 → 보통주만 record, 우선주 warning."""
    adapter = _adapter_with_response(
        _divi_response(items=[
            _divi_item(stck_knd_nm="보통주", stck_gnrl_dvdn_amt="500"),
            _divi_item(stck_knd_nm="우선주", stck_gnrl_dvdn_amt="550"),
        ]),
    )
    result = _fetch(adapter)
    assert len(result.data) == 1
    assert result.data[0].details["stock_knd"] == "보통주"
    assert any("non-common" in w or "보통주" in w for w in result.warnings)


# =============================================================================
# 5. dvdnBasDt 없음 skip + warning
# =============================================================================

@pytest.mark.parametrize("bas_dt", ["", "NOTADATE", "2024"])
def test_missing_or_invalid_bas_dt_skipped(bas_dt: str) -> None:
    """배당기준일 부재/비정상 → effective_date 산출 불가 → skip + warning."""
    adapter = _adapter_with_response(
        _divi_response(items=[_divi_item(dvdn_bas_dt=bas_dt)]),
    )
    result = _fetch(adapter)
    assert len(result.data) == 0
    assert any("dvdnBasDt" in w for w in result.warnings)


# =============================================================================
# 6. 캘린더 범위 밖 — 직전 거래일 산출 실패 skip + warning
# =============================================================================

def test_calendar_out_of_range_skipped_with_warning() -> None:
    """배당기준일이 캘린더 범위 밖이면 직전 거래일 산출 실패 → skip (역산 금지).

    H1 — benign skip 과 구별되는 [DATA_GAP] prefix + 드롭 카운트 요약.
    """
    # Fake 캘린더에 미등록 → previous_business_day 가 CalendarRangeError.
    empty_cal = _FakeCalendar({})
    adapter = _adapter_with_response(
        _divi_response(items=[_divi_item(dvdn_bas_dt="20241231")]),
    )
    result = _fetch(adapter, calendar=empty_cal)
    assert len(result.data) == 0
    # H1 — 기계 판독 가능한 gap 신호 prefix.
    gap_warnings = [w for w in result.warnings if DATA_GAP_PREFIX in w]
    assert any("캘린더 범위 밖" in w and "재수집 필요" in w for w in gap_warnings)
    # H1 — 드롭 카운트 요약 1줄.
    assert any("out-of-range 드롭 1건" in w for w in gap_warnings)


def test_calendar_range_error_does_not_drop_valid_dividends() -> None:
    """다년 backfill 모사 — 일부만 캘린더 보유. 미보유는 [DATA_GAP] 드롭,
    보유분은 정상 record (silent vanish 방지, §2.1)."""
    in_range = date(2024, 12, 31)
    out_range = date(2099, 12, 31)
    cal = _FakeCalendar({in_range: date(2024, 12, 30)})
    adapter = _adapter_with_response(
        _divi_response(items=[
            _divi_item(stck_gnrl_dvdn_amt="500", dvdn_bas_dt="20241231"),
            _divi_item(stck_gnrl_dvdn_amt="600", dvdn_bas_dt="20991231"),
        ]),
    )
    result = _fetch(adapter, calendar=cal)
    assert {r.cash_amount for r in result.data} == {Decimal("500")}
    assert any(
        DATA_GAP_PREFIX in w and "20991231" in w for w in result.warnings
    )
    _ = out_range  # 가독성용 라벨.


def test_non_range_calendar_error_propagates() -> None:
    """H2 — CalendarRangeError 가 아닌 예외 (프로그래밍 버그) 는 silent 손실로
    숨기지 않고 propagate (범위밖 오라벨 금지)."""
    adapter = _adapter_with_response(
        _divi_response(items=[_divi_item(dvdn_bas_dt="20241231")]),
    )
    with pytest.raises(AttributeError, match="buggy calendar"):
        _fetch(adapter, calendar=_BuggyCalendar())


# =============================================================================
# 7. 페이징 — totalCount > numOfRows 다페이지
# =============================================================================

def test_pagination_fetches_all_pages() -> None:
    """full 페이지(numOfRows=100) 가 오면 다음 페이지 요청 + 누적.

    M3 — 종료는 totalCount 가 아니라 short/empty 페이지로 판정 (full 페이지면
    tail 이 더 있을 수 있으므로 계속). page1 = full 100건, page2 = short 1건.
    """
    captured_pages: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        page = dict(request.url.params)["pageNo"]
        captured_pages.append(page)
        if page == "1":
            items = [_divi_item(stck_gnrl_dvdn_amt="100") for _ in range(99)]
            items.append(_divi_item(stck_gnrl_dvdn_amt="200"))
            body = _divi_response(items=items, total_count=101)
        else:
            body = _divi_response(
                items=[_divi_item(stck_gnrl_dvdn_amt="300")],
                total_count=101,
            )
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(_handler), timeout=5.0)
    adapter = FscDividendAdapter(http_client=client, api_key="test_key")
    result = _fetch(adapter)

    assert captured_pages == ["1", "2"]
    assert len(result.data) == 101
    amounts = {r.cash_amount for r in result.data}
    assert amounts == {Decimal("100"), Decimal("200"), Decimal("300")}


def test_single_page_no_extra_request() -> None:
    """totalCount <= 누적 → 추가 페이지 요청 없음."""
    captured_pages: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_pages.append(dict(request.url.params)["pageNo"])
        return httpx.Response(
            200,
            json=_divi_response(
                items=[_divi_item(stck_gnrl_dvdn_amt="500")], total_count=1,
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(_handler), timeout=5.0)
    adapter = FscDividendAdapter(http_client=client, api_key="test_key")
    _fetch(adapter)
    assert captured_pages == ["1"]


# =============================================================================
# 8. resultCode 비정상 — rate-limit retry vs auth error
# =============================================================================

def test_result_code_rate_limit_raises_retry() -> None:
    """resultCode 22 (트래픽 초과) → AdapterRetryError."""
    adapter = _adapter_with_response(
        _divi_response(result_code="22", result_msg="LIMITED NUMBER"),
    )
    with pytest.raises(AdapterRetryError, match="rate limit"):
        _fetch(adapter)


def test_result_code_auth_failure_raises_error() -> None:
    """resultCode 30 (서비스키 미등록) → AdapterError."""
    adapter = _adapter_with_response(
        _divi_response(result_code="30", result_msg="SERVICE KEY NOT REGISTERED"),
    )
    with pytest.raises(AdapterError, match="auth failure"):
        _fetch(adapter)


def test_result_code_unknown_raises_error() -> None:
    """resultCode 그 외 비정상 → AdapterError."""
    adapter = _adapter_with_response(_divi_response(result_code="99"))
    with pytest.raises(AdapterError, match="non-OK resultCode"):
        _fetch(adapter)


# =============================================================================
# 9. 빈 items — 정상 빈 결과
# =============================================================================

def test_empty_items_returns_empty_result() -> None:
    """resultCode 00 인데 빈 items → 빈 결과 (에러 아님)."""
    adapter = _adapter_with_response(
        _divi_response(items=[], total_count=0),
    )
    result = _fetch(adapter)
    assert len(result.data) == 0
    assert len(result.citations) == 0


def test_no_items_container_returns_empty() -> None:
    """items 가 빈 string (無자료 관행) → 빈 결과."""
    adapter = _adapter_with_response(
        _divi_response(no_items_container=True, total_count=0),
    )
    result = _fetch(adapter)
    assert len(result.data) == 0


# =============================================================================
# 10. params 캡처 — crno / beginBasDt / serviceKey 전달
# =============================================================================

def test_request_params_captured() -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json=_divi_response(items=[_divi_item()], total_count=1),
        )

    client = httpx.Client(transport=httpx.MockTransport(_handler), timeout=5.0)
    adapter = FscDividendAdapter(http_client=client, api_key="svc_key_xyz")
    _fetch(adapter, crno="1301110006246", begin="20240101", end="20241231")

    assert captured["crno"] == "1301110006246"
    assert captured["beginBasDt"] == "20240101"
    assert captured["endBasDt"] == "20241231"
    assert captured["serviceKey"] == "svc_key_xyz"
    assert captured["resultType"] == "json"
    assert captured["numOfRows"] == "100"


# =============================================================================
# 11. HTTP 에러 분류
# =============================================================================

def test_http_5xx_maps_to_retry() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = httpx.Client(transport=httpx.MockTransport(_handler), timeout=5.0)
    adapter = FscDividendAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterRetryError, match="503"):
        _fetch(adapter)


def test_http_4xx_maps_to_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    client = httpx.Client(transport=httpx.MockTransport(_handler), timeout=5.0)
    adapter = FscDividendAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterError, match="403"):
        _fetch(adapter)


def test_connect_error_maps_to_retry() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("apis.data.go.kr down")

    client = httpx.Client(transport=httpx.MockTransport(_handler), timeout=5.0)
    adapter = FscDividendAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterRetryError, match="connection failure"):
        _fetch(adapter)


# =============================================================================
# 12. API key — env fallback + 미설정
# =============================================================================

def test_api_key_missing_raises_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FSC_API_KEY", raising=False)
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)
    adapter = FscDividendAdapter()
    with pytest.raises(AdapterError, match="API key not configured"):
        _fetch(adapter)


def test_api_key_from_fsc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FSC_API_KEY", "fsc_env_key")
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200, json=_divi_response(items=[_divi_item()], total_count=1),
        )

    client = httpx.Client(transport=httpx.MockTransport(_handler), timeout=5.0)
    adapter = FscDividendAdapter(http_client=client)
    _fetch(adapter)
    assert captured["serviceKey"] == "fsc_env_key"


def test_api_key_from_data_go_kr_env_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FSC_API_KEY 부재 시 DATA_GO_KR_SERVICE_KEY fallback."""
    monkeypatch.delenv("FSC_API_KEY", raising=False)
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "portal_key")
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200, json=_divi_response(items=[_divi_item()], total_count=1),
        )

    client = httpx.Client(transport=httpx.MockTransport(_handler), timeout=5.0)
    adapter = FscDividendAdapter(http_client=client)
    _fetch(adapter)
    assert captured["serviceKey"] == "portal_key"


def test_health_check_true_with_key() -> None:
    assert FscDividendAdapter(api_key="k").health_check() is True


def test_health_check_false_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FSC_API_KEY", raising=False)
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)
    assert FscDividendAdapter().health_check() is False


# =============================================================================
# 13. citation 7-tuple
# =============================================================================

def test_citation_seven_tuple() -> None:
    adapter = _adapter_with_response(
        _divi_response(items=[_divi_item(dvdn_bas_dt="20241231")]),
    )
    batch_id = UUID("00000000-0000-0000-0000-0000000000bb")
    result = _fetch(adapter, batch_id=batch_id)
    cit = result.citations[0]
    assert cit.source == SourceKind.FSC
    assert cit.adapter_version == "1.0.0"
    assert cit.batch_id == batch_id
    # 안정 식별자 = crno|배당기준일|배당종류 (C1 — 다종 배당 충돌 방지).
    assert cit.identifier == "1301110006246|20241231|결산배당"
    # effective_date = announced_date = 배당락일.
    assert cit.effective_date == _EX_DATE
    assert cit.url is not None
    assert "getDiviInfo" in cit.url


# =============================================================================
# 13b. C1 — 같은 crno·배당기준일 다종 배당 → 유일 identifier
# =============================================================================

def test_same_basdt_different_dvdn_kind_distinct_identifiers() -> None:
    """같은 crno·같은 dvdnBasDt 에 결산배당 + 중간배당 (다른 dvdnRcdNm) 공존
    → 서로 다른 identifier 의 citation 2건 (supersede chain 충돌 방지, C1)."""
    adapter = _adapter_with_response(
        _divi_response(items=[
            _divi_item(
                stck_gnrl_dvdn_amt="500",
                dvdn_bas_dt="20241231",
                dvdn_rcd_nm="결산배당",
            ),
            _divi_item(
                stck_gnrl_dvdn_amt="300",
                dvdn_bas_dt="20241231",
                dvdn_rcd_nm="중간배당",
            ),
        ]),
    )
    result = _fetch(adapter)
    assert len(result.data) == 2
    identifiers = {c.identifier for c in result.citations}
    assert identifiers == {
        "1301110006246|20241231|결산배당",
        "1301110006246|20241231|중간배당",
    }
    # 두 citation 의 identifier 가 서로 다름 (충돌 없음).
    assert len(identifiers) == 2


def test_empty_dvdn_rcd_nm_warns_non_unique() -> None:
    """dvdnRcdNm 이 빈 문자열 → identifier 비유일 가능 → warning (silent merge
    금지, §2.1)."""
    adapter = _adapter_with_response(
        _divi_response(items=[
            _divi_item(stck_gnrl_dvdn_amt="500", dvdn_rcd_nm=""),
        ]),
    )
    result = _fetch(adapter)
    assert len(result.data) == 1
    assert result.citations[0].identifier == "1301110006246|20241231|"
    assert any("비유일" in w or "dvdnRcdNm" in w for w in result.warnings)


# =============================================================================
# 13c. 음수 배당 거부 (Low fix)
# =============================================================================

def test_negative_amount_skipped() -> None:
    """음수 주당현금배당금 → 무의미 → skip + warning (dart_adapter 음수 거부 일관)."""
    adapter = _adapter_with_response(
        _divi_response(items=[_divi_item(stck_gnrl_dvdn_amt="-500")]),
    )
    result = _fetch(adapter)
    assert len(result.data) == 0
    assert any("negative" in w or "음수" in w for w in result.warnings)


# =============================================================================
# 13d. X1 — sanity invariant assert (look-ahead 방지)
# =============================================================================

def test_lookahead_effective_date_raises() -> None:
    """buggy 캘린더가 배당기준일 이상 (미래) 의 배당락일 반환 → AdapterError
    (effective_date < bas_date 불변식 위반, §2.4 look-ahead 방지)."""
    # 배당락일 == 배당기준일 (strictly 이르지 않음) → 위반.
    cal = _LookAheadCalendar(date(2024, 12, 31))
    adapter = _adapter_with_response(
        _divi_response(items=[_divi_item(dvdn_bas_dt="20241231")]),
    )
    with pytest.raises(AdapterError, match="invariant"):
        _fetch(adapter, calendar=cal)


def test_payment_before_effective_raises() -> None:
    """지급일이 배당락일보다 이르면 AdapterError (payment_date >= effective_date
    불변식 위반)."""
    cal = _FakeCalendar({date(2024, 12, 31): date(2024, 12, 30)})
    adapter = _adapter_with_response(
        _divi_response(items=[_divi_item(
            dvdn_bas_dt="20241231",
            csh_dvdn_pay_dt="20241201",  # effective(12-30) 보다 이름.
        )]),
    )
    with pytest.raises(AdapterError, match="invariant"):
        _fetch(adapter, calendar=cal)


# =============================================================================
# 13e. M3 — totalCount drift tail-drop 고지 / 견고한 페이징
# =============================================================================

def test_total_count_undercount_continues_paging() -> None:
    """서버 totalCount 가 실제보다 작아도 (drift) full 페이지가 오면 계속 페이징
    하여 tail 을 드롭하지 않고 [DATA_GAP] 로 의심 고지 (M3, 견고한 후자)."""
    # numOfRows=100. page1 = full 100건 (totalCount=1 로 under-count 주장),
    # page2 = short (1건) → 종료. tail 누락 없이 101건 수신.
    def _handler(request: httpx.Request) -> httpx.Response:
        page = dict(request.url.params)["pageNo"]
        if page == "1":
            items = [_divi_item(stck_gnrl_dvdn_amt="500") for _ in range(100)]
            body = _divi_response(items=items, total_count=1)
        else:
            body = _divi_response(
                items=[_divi_item(stck_gnrl_dvdn_amt="600")], total_count=1,
            )
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(_handler), timeout=5.0)
    adapter = FscDividendAdapter(http_client=client, api_key="test_key")
    result = _fetch(adapter)
    # tail 드롭 없음 — 101건 전부 record.
    assert len(result.data) == 101
    assert any(
        DATA_GAP_PREFIX in w and "totalCount drift" in w
        for w in result.warnings
    )


# =============================================================================
# 14. 입력 검증
# =============================================================================

def test_invalid_code_raises() -> None:
    adapter = _adapter_with_response(_divi_response(items=[_divi_item()]))
    with pytest.raises(AdapterError, match="6-digit numeric"):
        _fetch(adapter, code="abc")


def test_invalid_crno_raises() -> None:
    adapter = _adapter_with_response(_divi_response(items=[_divi_item()]))
    with pytest.raises(AdapterError, match="crno"):
        _fetch(adapter, crno="   ")


def test_invalid_begin_date_raises() -> None:
    adapter = _adapter_with_response(_divi_response(items=[_divi_item()]))
    with pytest.raises(AdapterError, match="begin_bas_dt"):
        _fetch(adapter, begin="2024")


def test_invalid_end_date_raises() -> None:
    adapter = _adapter_with_response(_divi_response(items=[_divi_item()]))
    with pytest.raises(AdapterError, match="end_bas_dt"):
        _fetch(adapter, end="20241350")  # 13월 50일 — invalid date.


# =============================================================================
# 15. schema drift — response/body 부재
# =============================================================================

def test_missing_response_body_raises() -> None:
    """response.body 구조 부재 → AdapterError (schema drift)."""
    adapter = _adapter_with_response(
        {"response": {"header": {"resultCode": "00", "resultMsg": "OK"}}},
    )
    with pytest.raises(AdapterError, match="schema drift"):
        _fetch(adapter)
