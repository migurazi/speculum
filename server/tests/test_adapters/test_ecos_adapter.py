"""EcosAdapter 단위 테스트 — fixture 기반 (ADR-0003 D8).

httpx.MockTransport 를 사용하여 실 ECOS API 호출 없이 테스트 (ADR-0003 D8).
DartAdapter 테스트 패턴(_adapter_with_response, MockTransport)을 그대로 모방.

테스트 매트릭스:
    1.  정상 파싱 — 기준금리 일별(D), TIME 형식 + reference_date 정확성.
    2.  정상 파싱 — CPI 월별(M), reference_date = 해당 월 1일.
    3.  정상 파싱 — 분기(Q), reference_date = 분기 시작월 1일.
    4.  정상 파싱 — 연간(A), reference_date = 연도 1월 1일.
    5.  반기(S) TIME 파싱 — S1=1월 1일, S2=7월 1일.
    6.  DATA_VALUE 결측(빈 string) skip + warning 카운트.
    7.  DATA_VALUE null skip + warning 카운트.
    8.  vintage_date = observed_date 주입 확인.
    9.  INFO-200 에러 응답 — 빈 FetchResult (raise 아님).
    10. ERROR-100 에러 응답 — AdapterError (인증 오류).
    11. ERROR-500 에러 응답 — AdapterRetryError (서버 장애).
    12. ERROR-600 에러 응답 — AdapterRetryError (DB 장애).
    13. 그 외 ERROR-* — AdapterError.
    14. Citation 7-tuple (source=ECOS, identifier=indicator_id 규약).
    15. indicator_id 조합 규약 (STAT_CODE/ITEM_CODE1).
    16. 입력 검증 (stat_code, item_code, cycle).
    17. API key 미설정 → AdapterError.
    18. API key env var 사용.
    19. HTTP 5xx → AdapterRetryError.
    20. httpx ConnectError → AdapterRetryError.
    21. httpx TimeoutException → AdapterRetryError.
    22. TIME 파싱 실패 skip + warning.
    23. DATA_VALUE 비숫자 skip + warning.
    24. StatisticSearch key 없는 응답 schema drift → AdapterError.
    25. health_check — key 설정/미설정.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
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
from app.adapters.ecos_adapter import (
    EcosAdapter,
    _parse_ecos_time,
    make_indicator_id,
)
from app.models.source_citation import SourceKind

# =============================================================================
# Fixture builders — ECOS 응답 JSON 모사
# =============================================================================

def _ecos_response(
    *,
    rows: list[dict[str, Any]] | None = None,
    total_count: int | None = None,
) -> dict[str, Any]:
    """ECOS StatisticSearch 정상 응답 JSON 모사."""
    actual_rows = rows or []
    return {
        "StatisticSearch": {
            "list_total_count": total_count if total_count is not None else len(actual_rows),
            "row": actual_rows,
        }
    }


def _ecos_error_response(code: str, message: str) -> dict[str, Any]:
    """ECOS 에러 응답 JSON 모사."""
    return {
        "RESULT": {
            "CODE": code,
            "MESSAGE": message,
        }
    }


def _ecos_row(
    *,
    stat_code: str = "722Y001",
    stat_name: str = "기준금리",
    item_code1: str = "0101000",
    item_name1: str = "한국은행 기준금리",
    unit_name: str = "연%",
    time: str = "20240101",
    data_value: str = "3.5",
) -> dict[str, Any]:
    """단일 ECOS StatisticSearch row dict."""
    return {
        "STAT_CODE": stat_code,
        "STAT_NAME": stat_name,
        "ITEM_CODE1": item_code1,
        "ITEM_NAME1": item_name1,
        "ITEM_CODE2": None,
        "UNIT_NAME": unit_name,
        "TIME": time,
        "DATA_VALUE": data_value,
    }


def _mock_transport(handler: Any) -> httpx.MockTransport:
    """httpx.MockTransport — request callback 으로 응답 반환."""
    return httpx.MockTransport(handler)


def _adapter_with_response(
    response_json: dict[str, Any],
    *,
    status_code: int = 200,
) -> EcosAdapter:
    """fixed JSON 응답을 반환하는 mock client adapter."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=response_json)

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    return EcosAdapter(http_client=client, api_key="test_ecos_key")


# =============================================================================
# _parse_ecos_time 단위 테스트 — TIME 주기별 파싱
# =============================================================================

@pytest.mark.parametrize(
    "time_str,cycle,expected",
    [
        # D (일별): YYYYMMDD → 해당 일.
        ("20240101", "D", date(2024, 1, 1)),
        ("20231231", "D", date(2023, 12, 31)),
        # 윤년 2월 29일.
        ("20240229", "D", date(2024, 2, 29)),
        # M (월별): YYYYMM → 해당 월 1일.
        ("202401", "M", date(2024, 1, 1)),
        ("202312", "M", date(2023, 12, 1)),
        # Q (분기별): YYYYQ (5자리) → 분기 시작월 1일.
        ("20241", "Q", date(2024, 1, 1)),   # Q1 → 1월
        ("20242", "Q", date(2024, 4, 1)),   # Q2 → 4월
        ("20243", "Q", date(2024, 7, 1)),   # Q3 → 7월
        ("20244", "Q", date(2024, 10, 1)),  # Q4 → 10월
        # A (연간): YYYY → 1월 1일.
        ("2024", "A", date(2024, 1, 1)),
        ("2023", "A", date(2023, 1, 1)),
        # S (반기): 6자리, 끝자리가 반기 번호 (1=상반기, 2=하반기).
        # 형식: YYYY + 2자리(앞 자리 무시, 끝 자리만 반기 번호) — 실제 ECOS TIME은
        # "202311"(2023 상반기=1), "202312"(2023 하반기=2) 형태.
        ("20241S", "S", None),   # 길이 불일치 (7자리) → None.
        ("202411", "S", date(2024, 1, 1)),  # 끝자리 1 = S1 → 1월 1일.
        ("202412", "S", date(2024, 7, 1)),  # 끝자리 2 = S2 → 7월 1일.
    ],
)
def test_parse_ecos_time_parametrized(
    time_str: str, cycle: str, expected: date | None,
) -> None:
    """TIME 주기별 파싱 정확성 — reference_date."""
    assert _parse_ecos_time(time_str, cycle) == expected


def test_parse_ecos_time_daily_normal() -> None:
    """D cycle — YYYYMMDD 정상 파싱."""
    assert _parse_ecos_time("20240115", "D") == date(2024, 1, 15)


def test_parse_ecos_time_monthly_normal() -> None:
    """M cycle — YYYYMM → 월 1일."""
    assert _parse_ecos_time("202406", "M") == date(2024, 6, 1)


def test_parse_ecos_time_quarterly_all_quarters() -> None:
    """Q cycle — Q1/Q2/Q3/Q4 → 분기 시작월 1일 매트릭스."""
    assert _parse_ecos_time("20231", "Q") == date(2023, 1, 1)   # Q1
    assert _parse_ecos_time("20232", "Q") == date(2023, 4, 1)   # Q2
    assert _parse_ecos_time("20233", "Q") == date(2023, 7, 1)   # Q3
    assert _parse_ecos_time("20234", "Q") == date(2023, 10, 1)  # Q4


def test_parse_ecos_time_annual_normal() -> None:
    """A cycle — YYYY → 1월 1일."""
    assert _parse_ecos_time("2023", "A") == date(2023, 1, 1)


def test_parse_ecos_time_semiannual_s1_and_s2() -> None:
    """S cycle — S1=1월 1일, S2=7월 1일."""
    # 반기 TIME 형식: YYYYS (6자리, 마지막 자리 1 또는 2).
    assert _parse_ecos_time("202311", "S") == date(2023, 1, 1)
    assert _parse_ecos_time("202312", "S") == date(2023, 7, 1)


def test_parse_ecos_time_invalid_cases() -> None:
    """파싱 실패 방어 케이스 — 모두 None."""
    # 빈 string.
    assert _parse_ecos_time("", "D") is None
    # 길이 불일치 (D 는 8자리인데 7자리).
    assert _parse_ecos_time("2024010", "D") is None
    # 연도 범위 밖 (1899년).
    assert _parse_ecos_time("18990101", "D") is None
    # 알 수 없는 CYCLE.
    assert _parse_ecos_time("20240101", "X") is None
    # 분기 번호 5 (범위 밖).
    assert _parse_ecos_time("20245", "Q") is None
    # 반기 번호 3 (범위 밖).
    assert _parse_ecos_time("202413", "S") is None
    # 2월 30일 — 존재하지 않는 날.
    assert _parse_ecos_time("20240230", "D") is None
    # 13월.
    assert _parse_ecos_time("20241301", "D") is None


# =============================================================================
# make_indicator_id 단위 테스트
# =============================================================================

def test_make_indicator_id_combines_with_slash() -> None:
    """indicator_id = STAT_CODE + "/" + ITEM_CODE1."""
    assert make_indicator_id("722Y001", "0101000") == "722Y001/0101000"
    assert make_indicator_id("901Y013", "AA") == "901Y013/AA"


# =============================================================================
# 1~4. 정상 파싱 — 기준금리(D), CPI(M), 분기(Q), 연간(A)
# =============================================================================

def test_fetch_statistic_daily_returns_canonical_rows() -> None:
    """정상 path — D cycle 기준금리 일별, canonical MacroIndicatorRow 변환."""
    rows_raw = [
        _ecos_row(time="20240101", data_value="3.5"),
        _ecos_row(time="20240201", data_value="3.5"),
    ]
    adapter = _adapter_with_response(_ecos_response(rows=rows_raw))

    result = adapter.fetch_statistic(
        stat_code="722Y001",
        item_code="0101000",
        cycle="D",
        start="20240101",
        end="20240229",
        batch_id=uuid4(),
        observed_date=date(2024, 3, 1),
    )
    assert isinstance(result, FetchResult)
    assert len(result.data) == 2

    row0 = result.data[0]
    assert isinstance(row0, MacroIndicatorRow)
    # indicator_id 규약 확인.
    assert row0.indicator_id == "722Y001/0101000"
    # D cycle TIME 파싱 — YYYYMMDD → 해당 일.
    assert row0.reference_date == date(2024, 1, 1)
    assert row0.value == Decimal("3.5")
    assert row0.unit == "연%"
    # vintage_date = observed_date (관측 시점 근사).
    assert row0.vintage_date == date(2024, 3, 1)


def test_fetch_statistic_monthly_reference_date_is_first_of_month() -> None:
    """M cycle CPI 월별 — reference_date = 해당 월 1일."""
    rows_raw = [_ecos_row(time="202401", data_value="110.5", unit_name="지수")]
    adapter = _adapter_with_response(_ecos_response(rows=rows_raw))

    result = adapter.fetch_statistic(
        stat_code="901Y009",
        item_code="0",
        cycle="M",
        start="202401",
        end="202406",
        batch_id=uuid4(),
        observed_date=date(2024, 7, 1),
    )
    assert len(result.data) == 1
    assert result.data[0].reference_date == date(2024, 1, 1)
    assert result.data[0].value == Decimal("110.5")


def test_fetch_statistic_quarterly_reference_date() -> None:
    """Q cycle — reference_date = 분기 시작월 1일."""
    rows_raw = [
        _ecos_row(time="20241", data_value="2.1"),  # Q1 → 1월
        _ecos_row(time="20242", data_value="2.3"),  # Q2 → 4월
    ]
    adapter = _adapter_with_response(_ecos_response(rows=rows_raw))

    result = adapter.fetch_statistic(
        stat_code="111Y002",
        item_code="10101",
        cycle="Q",
        start="20241",
        end="20242",
        batch_id=uuid4(),
        observed_date=date(2024, 8, 1),
    )
    assert len(result.data) == 2
    assert result.data[0].reference_date == date(2024, 1, 1)
    assert result.data[1].reference_date == date(2024, 4, 1)


def test_fetch_statistic_annual_reference_date() -> None:
    """A cycle — reference_date = 연도 1월 1일."""
    rows_raw = [_ecos_row(time="2023", data_value="3.2", unit_name="연%")]
    adapter = _adapter_with_response(_ecos_response(rows=rows_raw))

    result = adapter.fetch_statistic(
        stat_code="722Y001",
        item_code="0101000",
        cycle="A",
        start="2023",
        end="2024",
        batch_id=uuid4(),
        observed_date=date(2024, 1, 15),
    )
    assert len(result.data) == 1
    assert result.data[0].reference_date == date(2023, 1, 1)


# =============================================================================
# 5. 반기(S) TIME 파싱
# =============================================================================

def test_fetch_statistic_semiannual_time_parsing() -> None:
    """S cycle — S1=1월 1일, S2=7월 1일."""
    rows_raw = [
        _ecos_row(time="202311", data_value="2.0"),  # 2023 상반기
        _ecos_row(time="202312", data_value="2.5"),  # 2023 하반기
    ]
    adapter = _adapter_with_response(_ecos_response(rows=rows_raw))

    result = adapter.fetch_statistic(
        stat_code="111Y002",
        item_code="10101",
        cycle="S",
        start="202311",
        end="202312",
        batch_id=uuid4(),
        observed_date=date(2024, 1, 10),
    )
    assert len(result.data) == 2
    assert result.data[0].reference_date == date(2023, 1, 1)
    assert result.data[1].reference_date == date(2023, 7, 1)


# =============================================================================
# 6~7. DATA_VALUE 결측 skip + warning
# =============================================================================

def test_missing_data_value_skipped_with_warning() -> None:
    """빈 string DATA_VALUE — row skip + warning 카운트."""
    rows_raw = [
        _ecos_row(time="20240101", data_value="3.5"),
        _ecos_row(time="20240201", data_value=""),   # 결측.
        _ecos_row(time="20240301", data_value="3.5"),
    ]
    adapter = _adapter_with_response(_ecos_response(rows=rows_raw))

    result = adapter.fetch_statistic(
        stat_code="722Y001",
        item_code="0101000",
        cycle="D",
        start="20240101",
        end="20240301",
        batch_id=uuid4(),
        observed_date=date(2024, 4, 1),
    )
    # 1 row skip → 2 row 보존.
    assert len(result.data) == 2
    # warning 에 skip 카운트 포함.
    skip_warning = next(w for w in result.warnings if "DATA_VALUE 결측" in w)
    assert "1 rows skipped" in skip_warning


def test_null_data_value_skipped_with_warning() -> None:
    """null DATA_VALUE — row skip + warning."""
    rows_raw = [
        _ecos_row(time="20240101", data_value="3.5"),
        {**_ecos_row(time="20240201"), "DATA_VALUE": None},  # null.
    ]
    adapter = _adapter_with_response(_ecos_response(rows=rows_raw))

    result = adapter.fetch_statistic(
        stat_code="722Y001",
        item_code="0101000",
        cycle="D",
        start="20240101",
        end="20240201",
        batch_id=uuid4(),
        observed_date=date(2024, 3, 1),
    )
    assert len(result.data) == 1
    assert any("DATA_VALUE 결측" in w for w in result.warnings)


# =============================================================================
# 8. vintage_date = observed_date 주입 확인
# =============================================================================

def test_vintage_date_equals_observed_date() -> None:
    """vintage_date = observed_date (관측 시점 근사) 주입 확인."""
    observed = date(2024, 5, 15)
    rows_raw = [_ecos_row(time="20240101", data_value="3.5")]
    adapter = _adapter_with_response(_ecos_response(rows=rows_raw))

    result = adapter.fetch_statistic(
        stat_code="722Y001",
        item_code="0101000",
        cycle="D",
        start="20240101",
        end="20240101",
        batch_id=uuid4(),
        observed_date=observed,
    )
    # 모든 row 의 vintage_date 가 observed_date 와 동일해야 함.
    for row in result.data:
        assert row.vintage_date == observed


# =============================================================================
# 9. INFO-200 에러 응답 — 빈 FetchResult (raise 아님)
# =============================================================================

def test_info_200_returns_empty_fetch_result() -> None:
    """INFO-200 (데이터없음) — raise 아닌 빈 FetchResult 반환."""
    adapter = _adapter_with_response(
        _ecos_error_response("INFO-200", "해당하는 통계자료가 없습니다.")
    )
    result = adapter.fetch_statistic(
        stat_code="722Y001",
        item_code="0101000",
        cycle="D",
        start="19000101",
        end="19001231",
        batch_id=uuid4(),
        observed_date=date(2024, 1, 1),
    )
    assert isinstance(result, FetchResult)
    assert len(result.data) == 0
    # warnings 에 INFO-200 언급.
    assert any("INFO-200" in w for w in result.warnings)


# =============================================================================
# 10~13. 에러 분류 — ERROR-100, ERROR-500, ERROR-600, 그 외 ERROR-*
# =============================================================================

def test_error_100_raises_adapter_error() -> None:
    """ERROR-100 (인증키 오류) → AdapterError."""
    adapter = _adapter_with_response(
        _ecos_error_response("ERROR-100", "등록되지 않은 인증키입니다.")
    )
    with pytest.raises(AdapterError, match="auth failure"):
        adapter.fetch_statistic(
            stat_code="722Y001",
            item_code="0101000",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


def test_error_500_raises_adapter_retry_error() -> None:
    """ERROR-500 (서버 장애) → AdapterRetryError."""
    adapter = _adapter_with_response(
        _ecos_error_response("ERROR-500", "서버 내부 오류")
    )
    with pytest.raises(AdapterRetryError, match="server error"):
        adapter.fetch_statistic(
            stat_code="722Y001",
            item_code="0101000",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


def test_error_600_raises_adapter_retry_error() -> None:
    """ERROR-600 (DB 장애) → AdapterRetryError."""
    adapter = _adapter_with_response(
        _ecos_error_response("ERROR-600", "DB 오류")
    )
    with pytest.raises(AdapterRetryError, match="server error"):
        adapter.fetch_statistic(
            stat_code="722Y001",
            item_code="0101000",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


def test_unknown_error_code_raises_adapter_error() -> None:
    """그 외 ERROR-* (예: ERROR-101 형식오류) → AdapterError."""
    adapter = _adapter_with_response(
        _ecos_error_response("ERROR-101", "인자 형식 오류")
    )
    with pytest.raises(AdapterError):
        adapter.fetch_statistic(
            stat_code="722Y001",
            item_code="0101000",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


# =============================================================================
# 14. Citation 7-tuple — source=ECOS, identifier=indicator_id 규약
# =============================================================================

def test_citation_seven_tuple() -> None:
    """Citation 7-tuple — source=ECOS, identifier=indicator_id, url 포함."""
    rows_raw = [_ecos_row(time="20240101", data_value="3.5")]
    adapter = _adapter_with_response(_ecos_response(rows=rows_raw))
    batch_id = UUID("00000000-0000-0000-0000-0000000000bb")

    result = adapter.fetch_statistic(
        stat_code="722Y001",
        item_code="0101000",
        cycle="D",
        start="20240101",
        end="20240131",
        batch_id=batch_id,
        observed_date=date(2024, 2, 1),
    )
    cit = result.citations[0]
    assert cit.source == SourceKind.ECOS
    assert cit.adapter_version == "1.0.0"
    assert cit.batch_id == batch_id
    # identifier = indicator_id 규약 (STAT_CODE/ITEM_CODE1).
    assert cit.identifier == "722Y001/0101000"
    # url = 키 없는 공개 ECOS 뷰어 링크.
    assert cit.url is not None
    assert cit.url.startswith("https://ecos.bok.or.kr/")
    # 보안 — citation.url 에 API 키 절대 미포함. 호출 URL 은 path 에 키를 포함하지만
    # citation 은 키 없는 뷰어 링크만 저장 → source_citations DB 키 노출 방지.
    assert "test_ecos_key" not in cit.url
    # retrieved_at = UTC tz-aware.
    assert cit.retrieved_at.utcoffset() == datetime(
        2024, 1, 1, tzinfo=UTC
    ).utcoffset()


# =============================================================================
# 15. indicator_id 조합 규약
# =============================================================================

def test_indicator_id_combination_in_rows() -> None:
    """모든 row 의 indicator_id = STAT_CODE/ITEM_CODE1 조합."""
    rows_raw = [
        _ecos_row(stat_code="722Y001", item_code1="0101000", time="20240101", data_value="3.5"),
        _ecos_row(stat_code="722Y001", item_code1="0101000", time="20240201", data_value="3.5"),
    ]
    adapter = _adapter_with_response(_ecos_response(rows=rows_raw))

    result = adapter.fetch_statistic(
        stat_code="722Y001",
        item_code="0101000",
        cycle="D",
        start="20240101",
        end="20240229",
        batch_id=uuid4(),
        observed_date=date(2024, 3, 1),
    )
    for row in result.data:
        assert row.indicator_id == "722Y001/0101000"


# =============================================================================
# 16. 입력 검증 (stat_code, item_code, cycle)
# =============================================================================

def test_empty_stat_code_raises() -> None:
    """stat_code 빈 string → AdapterError."""
    adapter = _adapter_with_response(_ecos_response())
    with pytest.raises(AdapterError, match="stat_code must not be empty"):
        adapter.fetch_statistic(
            stat_code="",
            item_code="0101000",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


def test_non_str_stat_code_raises() -> None:
    """stat_code 가 str 아님 → AdapterError."""
    adapter = _adapter_with_response(_ecos_response())
    with pytest.raises(AdapterError, match="stat_code must be str"):
        adapter.fetch_statistic(
            stat_code=722001,  # type: ignore[arg-type]
            item_code="0101000",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


def test_empty_item_code_raises() -> None:
    """item_code 빈 string → AdapterError."""
    adapter = _adapter_with_response(_ecos_response())
    with pytest.raises(AdapterError, match="item_code must not be empty"):
        adapter.fetch_statistic(
            stat_code="722Y001",
            item_code="",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


def test_invalid_cycle_raises() -> None:
    """지원하지 않는 cycle → AdapterError."""
    adapter = _adapter_with_response(_ecos_response())
    with pytest.raises(AdapterError, match="cycle must be one of"):
        adapter.fetch_statistic(
            stat_code="722Y001",
            item_code="0101000",
            cycle="X",  # 지원 안 함.
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


# =============================================================================
# 17. API key 미설정 → AdapterError
# =============================================================================

def test_api_key_missing_raises_adapter_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API key 미설정 — AdapterError (명시 인자 X + env var 미설정)."""
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    adapter = EcosAdapter()
    with pytest.raises(AdapterError, match="API key not configured"):
        adapter.fetch_statistic(
            stat_code="722Y001",
            item_code="0101000",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


# =============================================================================
# 18. API key env var 사용
# =============================================================================

def test_api_key_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """ECOS_API_KEY env var — URL path 에 key 포함 확인."""
    monkeypatch.setenv("ECOS_API_KEY", "env_ecos_key")
    captured_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(200, json=_ecos_response(rows=[_ecos_row()]))

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = EcosAdapter(http_client=client)  # api_key 명시 X — env 사용.
    adapter.fetch_statistic(
        stat_code="722Y001",
        item_code="0101000",
        cycle="D",
        start="20240101",
        end="20240131",
        batch_id=uuid4(),
        observed_date=date(2024, 2, 1),
    )
    assert len(captured_urls) == 1
    # URL path 에 env key 가 포함돼야 함.
    assert "env_ecos_key" in captured_urls[0]


# =============================================================================
# 19~21. HTTP/네트워크 에러 분류
# =============================================================================

def test_http_5xx_raises_retry_error() -> None:
    """HTTP 5xx → AdapterRetryError."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = EcosAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterRetryError, match="503"):
        adapter.fetch_statistic(
            stat_code="722Y001",
            item_code="0101000",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


def test_http_4xx_raises_adapter_error() -> None:
    """HTTP 4xx → AdapterError."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = EcosAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterError, match="403"):
        adapter.fetch_statistic(
            stat_code="722Y001",
            item_code="0101000",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


def test_connect_error_raises_retry_error() -> None:
    """httpx.ConnectError → AdapterRetryError."""
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("ecos.bok.or.kr down")

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = EcosAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterRetryError, match="connection failure"):
        adapter.fetch_statistic(
            stat_code="722Y001",
            item_code="0101000",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


def test_timeout_raises_retry_error() -> None:
    """httpx.TimeoutException → AdapterRetryError."""
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout")

    client = httpx.Client(transport=_mock_transport(_handler), timeout=5.0)
    adapter = EcosAdapter(http_client=client, api_key="test_key")
    with pytest.raises(AdapterRetryError, match="timeout"):
        adapter.fetch_statistic(
            stat_code="722Y001",
            item_code="0101000",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


# =============================================================================
# 22. TIME 파싱 실패 — row skip + warning
# =============================================================================

def test_time_parse_failure_skips_row_with_warning() -> None:
    """TIME 형식 불일치 (D cycle 인데 5자리 TIME) — row skip + warning."""
    rows_raw = [
        _ecos_row(time="20240101", data_value="3.5"),
        _ecos_row(time="BADTM", data_value="3.5"),   # 파싱 실패.
        _ecos_row(time="20240301", data_value="3.5"),
    ]
    adapter = _adapter_with_response(_ecos_response(rows=rows_raw))

    result = adapter.fetch_statistic(
        stat_code="722Y001",
        item_code="0101000",
        cycle="D",
        start="20240101",
        end="20240301",
        batch_id=uuid4(),
        observed_date=date(2024, 4, 1),
    )
    # 1 row skip → 2 row 보존.
    assert len(result.data) == 2
    parse_warning = next(w for w in result.warnings if "TIME 파싱 실패" in w)
    assert "1 rows skipped" in parse_warning


# =============================================================================
# 23. DATA_VALUE 비숫자 skip + warning
# =============================================================================

def test_non_numeric_data_value_skipped_with_warning() -> None:
    """DATA_VALUE 비숫자 (N/A 등) — 결측 처리, row skip + warning."""
    rows_raw = [
        _ecos_row(time="20240101", data_value="3.5"),
        _ecos_row(time="20240201", data_value="N/A"),  # 비숫자.
    ]
    adapter = _adapter_with_response(_ecos_response(rows=rows_raw))

    result = adapter.fetch_statistic(
        stat_code="722Y001",
        item_code="0101000",
        cycle="D",
        start="20240101",
        end="20240229",
        batch_id=uuid4(),
        observed_date=date(2024, 3, 1),
    )
    assert len(result.data) == 1
    assert any("DATA_VALUE 결측" in w for w in result.warnings)


# =============================================================================
# 24. StatisticSearch key 없는 schema drift → AdapterError
# =============================================================================

def test_schema_drift_missing_statistic_search_key() -> None:
    """StatisticSearch key 없는 정상 JSON 응답 → AdapterError."""
    # RESULT key 도 없고 StatisticSearch key 도 없는 예상 외 응답.
    adapter = _adapter_with_response({"unexpected": "response"})
    with pytest.raises(AdapterError, match="'StatisticSearch' key 없음"):
        adapter.fetch_statistic(
            stat_code="722Y001",
            item_code="0101000",
            cycle="D",
            start="20240101",
            end="20240131",
            batch_id=uuid4(),
            observed_date=date(2024, 2, 1),
        )


# =============================================================================
# 25. health_check
# =============================================================================

def test_health_check_returns_true_when_key_configured() -> None:
    """API key 설정 시 health_check = True."""
    adapter = EcosAdapter(api_key="test_key")
    assert adapter.health_check() is True


def test_health_check_returns_false_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API key 미설정 시 health_check = False."""
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    adapter = EcosAdapter()
    assert adapter.health_check() is False


# =============================================================================
# 추가 — frozen dataclass 불변식, empty rows list
# =============================================================================

def test_macro_indicator_row_is_frozen() -> None:
    """MacroIndicatorRow 는 frozen — in-process 불변성."""
    row = MacroIndicatorRow(
        indicator_id="722Y001/0101000",
        reference_date=date(2024, 1, 1),
        value=Decimal("3.5"),
        unit="연%",
        vintage_date=date(2024, 3, 1),
    )
    with pytest.raises((AttributeError, TypeError)):
        row.value = Decimal("4.0")  # type: ignore[misc]


def test_empty_row_list_returns_empty_tuple() -> None:
    """row 가 빈 list 인 정상 응답 — 빈 tuple 반환 (raise 아님)."""
    adapter = _adapter_with_response(_ecos_response(rows=[]))

    result = adapter.fetch_statistic(
        stat_code="722Y001",
        item_code="0101000",
        cycle="D",
        start="20240101",
        end="20240131",
        batch_id=uuid4(),
        observed_date=date(2024, 2, 1),
    )
    assert result.data == ()
