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
    IfrsType,
)
from app.adapters.dart_account_mapper import (
    UNMAPPED_PREFIX,
    is_unmapped,
    map_ifrs_account,
)
from app.adapters.dart_adapter import (
    CompanyInfo,
    DartAdapter,
    _disclosure_deadline,
    _rcept_date,
)
from app.models.source_citation import SourceKind

# =============================================================================
# 0. _rcept_date — rcept_no → 정밀 공시일 도출 (ADR-0012 D6)
# =============================================================================

@pytest.mark.parametrize(
    "rcept_no,expected",
    [
        # 정상 14자리 — 앞 8자리 YYYYMMDD = 접수일자 (공시일).
        ("20240501000123", date(2024, 5, 1)),
        ("20231114000001", date(2023, 11, 14)),
        # 윤년 2월 29일.
        ("20240229999999", date(2024, 2, 29)),
        # 8자리 비정상 date (2월 30일 부재) → None.
        ("20240230000001", None),
        # 13 월 → None.
        ("20241301000001", None),
        # 연도 범위 밖 (1999) → None (오염 방어).
        ("19990101000001", None),
        # 연도 범위 밖 (2101) → None.
        ("21010101000001", None),
        # 14자리이나 비숫자 → None.
        ("BADRCEPTNO123", None),
        ("2024050100012X", None),
        # 길이 != 14 → None (13자리).
        ("2024050100012", None),
        # 길이 != 14 → None (15자리).
        ("202405010001234", None),
        # placeholder identifier (treasury fallback) → None.
        ("stockTotqySttus:005930:2024Q1", None),
        # 빈 string → None.
        ("", None),
    ],
)
def test_rcept_date_derivation(rcept_no: str, expected: date | None) -> None:
    """ADR-0012 D6 — rcept_no 앞 8자리 (YYYYMMDD) 직접 도출 + 방어 fallback."""
    assert _rcept_date(rcept_no) == expected


def test_rcept_date_non_str_returns_none() -> None:
    """str 외 입력은 None (방어 — schema 예상 외)."""
    assert _rcept_date(None) is None  # type: ignore[arg-type]
    assert _rcept_date(20240501000123) is None  # type: ignore[arg-type]


# =============================================================================
# 0b. T55 무회귀 — 정밀값 ≤ 보수추정값 (early disclosure 불변식)
# =============================================================================
#
# m1-milestone.md T55: "정밀값 ≤ 보수추정값 (early disclosure, 늦으면 V1 회귀)".
# 정밀화 (rcept_no 도출 실 공시일) 가 보수추정 (자본시장법 제160조 신고기한)
# 보다 늦은 effective_date 를 만들면, 정밀화 이전보다 factor 가 더 늦게 PIT 를
# 통과 → V1 (DART PIT) 회귀 + look-ahead 안전성은 유지되나 정밀화 이득 소멸.
# 정상 (기한 내) 공시는 항상 precise <= deadline 이어야 하며, 이를 회귀 게이트로
# 박는다. (지각 공시 = precise > deadline 는 비정상 데이터로, 정밀값을 그대로
# 쓰면 PIT 안전 — 그러나 그 경우 effective_date_precise=True 로 명시 노출됨.)

@pytest.mark.parametrize(
    "fiscal_year,fiscal_quarter,rcept_no",
    [
        # Q1 신고기한 = 2023-05-15. 기한 내 조기 공시 (5-10) → precise < deadline.
        (2023, 1, "20230510000001"),
        # Q1 기한 당일 공시 (5-15) → precise == deadline (경계, <= 성립).
        (2023, 1, "20230515000001"),
        # Q2 신고기한 = 2023-08-14. 반기보고서 조기 공시 (8-01).
        (2023, 2, "20230801000001"),
        # Q3 신고기한 = 2023-11-14. 3분기보고서 조기 공시 (11-01).
        (2023, 3, "20231101000001"),
        # Q4 신고기한 = 2024-03-30 (2023 사업연도 +90d). 사업보고서 조기 공시 (3-14).
        (2023, 4, "20240314000001"),
        # Q4 기한 당일 공시 (3-30) → precise == deadline (경계).
        (2023, 4, "20240330000001"),
    ],
)
def test_precise_disclosure_not_later_than_deadline(
    fiscal_year: int,
    fiscal_quarter: int,
    rcept_no: str,
) -> None:
    """정상 (기한 내) 공시는 정밀 공시일 ≤ 보수 신고기한 (T55 early disclosure).

    정밀화가 effective_date 를 보수추정보다 늦추면 안 됨 (늦추면 V1 회귀).
    기한 내 공시 rcept_no 들에 대해 `_rcept_date <= _disclosure_deadline` 검증.
    """
    precise = _rcept_date(rcept_no)
    deadline = _disclosure_deadline(fiscal_year, fiscal_quarter)
    assert precise is not None
    # early disclosure 불변식 — 정밀값이 보수추정값을 초과하지 않음.
    assert precise <= deadline


def test_precise_effective_date_not_later_than_conservative_fallback() -> None:
    """통합 경로 무회귀 — 정밀 effective_date ≤ 보수 fallback effective_date.

    같은 분기 (Q4 2023) 를 (a) 정상 조기 공시 rcept_no (정밀 경로) 와 (b) 비숫자
    rcept_no (보수 fallback) 로 각각 fetch → 정밀 경로의 effective_date 가
    fallback 의 신고기한보다 이르거나 같음. adapter 가 정밀값을 채택해도 PIT 가
    더 늦어지지 않음 (T55 — 정밀화는 effective_date 를 앞당기거나 유지).
    """
    # (a) 정밀 경로 — Q4 사업보고서 3-14 조기 공시 (기한 3-30 전).
    precise_adapter = _adapter_with_response(_dart_response(rows=[
        _dart_row(thstrm_amount="100", rcept_no="20240314000001"),
    ]))
    precise_result = precise_adapter.fetch_financial_statement(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4,
        ifrs_type=IfrsType.CFS, batch_id=uuid4(),
    )
    # (b) 보수 fallback — 비숫자 rcept_no → 신고기한 (3-30).
    fallback_adapter = _adapter_with_response(_dart_response(rows=[
        _dart_row(thstrm_amount="100", rcept_no="NOTADATE00123"),
    ]))
    fallback_result = fallback_adapter.fetch_financial_statement(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4,
        ifrs_type=IfrsType.CFS, batch_id=uuid4(),
    )

    assert precise_result.data[0].effective_date_precise is True
    assert fallback_result.data[0].effective_date_precise is False
    # 정밀값 ≤ 보수추정값 — 정밀화가 PIT 통과 시점을 늦추지 않음.
    assert (
        precise_result.data[0].effective_date
        <= fallback_result.data[0].effective_date
    )


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
    # effective_date = rcept_no 도출 정밀 공시일 (ADR-0012 D6). fixture default
    # rcept_no="20240501000123" → 앞 8자리 2024-05-01 = 접수일자 (공시일).
    assert assets.effective_date == date(2024, 5, 1)
    assert assets.effective_date_precise is True


# =============================================================================
# 1-bis. sj_div 재무제표 dedup + 자본변동표(SCE) 제외
# =============================================================================

def test_multi_statement_dedup_and_sce_excluded() -> None:
    """fnlttSinglAcntAll 의 5 재무제표 중복을 dedup + 자본변동표(SCE) 제외.

    실데이터 회귀: 삼성 등 실 응답은 같은 보고서의 BS/IS/CIS/CF/SCE 를 한 list 로
    반환하며, net_income(ifrs-full_ProfitLoss)이 IS/CIS/CF/SCE 에 반복·total_equity
    (ifrs-full_Equity)가 BS/SCE 에 반복된다. dedup 없으면 단일 fetch 내 canonical
    중복 → 저장 시 PITDataCorruptionError. 본 테스트가 그 회귀를 가드한다.
    """
    rows_raw = [
        _dart_row(account_id="ifrs-full_Assets", thstrm_amount="100", sj_div="BS"),
        # total_equity — BS(정본 잔액) + SCE(기말/기초 반복). BS 채택, SCE 제외.
        _dart_row(account_id="ifrs-full_Equity", thstrm_amount="60", sj_div="BS"),
        _dart_row(account_id="ifrs-full_Equity", thstrm_amount="55", sj_div="SCE"),
        _dart_row(account_id="ifrs-full_Equity", thstrm_amount="60", sj_div="SCE"),
        # net_income — IS(정본) + CIS + CF 동일 값 반복. IS 채택, 나머지 dedup.
        _dart_row(account_id="ifrs-full_ProfitLoss", thstrm_amount="10", sj_div="IS"),
        _dart_row(account_id="ifrs-full_ProfitLoss", thstrm_amount="10", sj_div="CIS"),
        _dart_row(account_id="ifrs-full_ProfitLoss", thstrm_amount="10", sj_div="CF"),
        # SCE 전용 반복 항목 — 전부 제외돼야 함.
        _dart_row(account_id="ifrs-full_ProfitLoss", thstrm_amount="10", sj_div="SCE"),
    ]
    adapter = _adapter_with_response(_dart_response(rows=rows_raw))
    result = adapter.fetch_financial_statement(
        code="005930", corp_code="00126380", fiscal_year=2024,
        fiscal_quarter=1, ifrs_type=IfrsType.CFS, batch_id=uuid4(),
    )
    accounts = [r.account for r in result.data]
    # canonical 중복 0 (PIT 무결성 가드 충족).
    assert len(accounts) == len(set(accounts)), f"중복 canonical: {accounts}"
    by_acc = {r.account: r.value for r in result.data}
    assert by_acc["total_assets"] == Decimal("100")
    # total_equity 는 BS(60) — SCE(55/60) 미채택.
    assert by_acc["total_equity"] == Decimal("60")
    # net_income 은 IS 1건만 (CIS/CF/SCE 중복 제거).
    assert by_acc["net_income"] == Decimal("10")
    assert accounts.count("net_income") == 1


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


def test_account_mapper_reit_ffo_accounts() -> None:
    """리츠 FFO/배당 구성 계정 매핑 — ADR-0023 D8 (P0 실 DART 검증).

    FFO = net_income(ifrs-full_ProfitLoss, 기존) + depreciation. 배당/투자부동산은
    dividend-yield:reit·NAV 구성. 데이터 정규화(canonical key)이지 factor 정의가
    아니므로 factor pack content_hash 무변경(재현성 무관).
    """
    assert (
        map_ifrs_account("ifrs-full_AdjustmentsForDepreciationExpense")
        == "depreciation_expense"
    )
    assert (
        map_ifrs_account("ifrs-full_DividendsPaidClassifiedAsFinancingActivities")
        == "dividends_paid_annual"
    )
    assert map_ifrs_account("ifrs-full_InvestmentProperty") == "investment_property"
    # FFO 분자의 순이익은 기존 net_income 매핑 재사용(중복 등록 아님).
    assert map_ifrs_account("ifrs-full_ProfitLoss") == "net_income"


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


def test_fetch_precise_effective_date_clears_estimated_marker() -> None:
    """ADR-0012 D6 — 유효 rcept_no 면 정밀 공시일 + estimated_fields 비움.

    rcept_no 앞 8자리 (YYYYMMDD = 접수일자 = 공시일) 직접 도출 → effective_date
    정밀 → marker 불요 (effective_date_precise=True 컬럼으로 영속화).
    """
    rows_raw = [
        _dart_row(
            account_id="ifrs-full_Assets",
            thstrm_amount="100",
            rcept_no="20240314000456",
        )
    ]
    adapter = _adapter_with_response(_dart_response(rows=rows_raw))
    result = adapter.fetch_financial_statement(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4,
        ifrs_type=IfrsType.CFS, batch_id=uuid4(),
    )
    assert result.estimated_fields == frozenset()
    assert result.data[0].effective_date == date(2024, 3, 14)
    assert result.data[0].effective_date_precise is True
    assert result.citations[0].effective_date == date(2024, 3, 14)


def test_fetch_invalid_rcept_no_falls_back_to_deadline() -> None:
    """ADR-0012 D6 방어 — rcept_no 가 14자리 숫자 schema 외면 보수 fallback.

    rcept_no 도출 실패 시 자본시장법 제160조 신고기한 보수값 (ADR-0012 D1) +
    estimated_fields={"effective_date"} + effective_date_precise=False (look-ahead
    0 보장). 본 fixture 의 rcept_no="BADRCEPTNO123" 는 14자리이나 비숫자.
    """
    rows_raw = [
        _dart_row(
            account_id="ifrs-full_Assets",
            thstrm_amount="100",
            rcept_no="BADRCEPTNO123",
        )
    ]
    adapter = _adapter_with_response(_dart_response(rows=rows_raw))
    result = adapter.fetch_financial_statement(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4,
        ifrs_type=IfrsType.CFS, batch_id=uuid4(),
    )
    assert result.estimated_fields == frozenset({"effective_date"})
    # Q4 신고기한 = 2023-12-31 + 90d = 2024-03-30 (보수값).
    assert result.data[0].effective_date == date(2024, 3, 30)
    assert result.data[0].effective_date_precise is False
    assert result.citations[0].effective_date == date(2024, 3, 30)


# =============================================================================
# 5.1 ADR-0012 D1 신고기한 정책 — 분기별 effective_date 매트릭스
# =============================================================================


@pytest.mark.parametrize(
    "fiscal_year,fiscal_quarter,expected_date",
    [
        # Q1 (1분기 보고서): 분기 종료 (3-31) + 45일 = 5-15.
        (2023, 1, date(2023, 5, 15)),
        # Q2 (반기 보고서): 분기 종료 (6-30) + 45일 = 8-14.
        (2023, 2, date(2023, 8, 14)),
        # Q3 (3분기 보고서): 분기 종료 (9-30) + 45일 = 11-14.
        (2023, 3, date(2023, 11, 14)),
        # Q4 (사업 보고서): 2023-12-31 + 90일 (캘린더 산술, 2024 윤년 영향)
        # = 31 + 29 + 30 = 90 → 2024-03-30.
        (2023, 4, date(2024, 3, 30)),
        # 비윤년 다음해 — 2025-12-31 + 90d = 31 + 28 + 31 = 90 → 2026-03-31.
        (2025, 4, date(2026, 3, 31)),
    ],
)
def test_disclosure_deadline_matrix(
    fiscal_year: int,
    fiscal_quarter: int,
    expected_date: date,
) -> None:
    """ADR-0012 D1 의 신고기한 매트릭스 — 자본시장법 제160조 보수 fallback.

    rcept_no 도출 실패 (비숫자 14자리) 시 신고기한 보수 정책으로 fallback 함을
    검증 (ADR-0012 D6 의 방어 경로). 정밀 도출 경로는
    test_fetch_precise_effective_date_clears_estimated_marker 가 담당.
    """
    rows_raw = [
        _dart_row(
            account_id="ifrs-full_Assets",
            thstrm_amount="100",
            rcept_no="NOTADATE00123",  # 14자리이나 비숫자 → 보수 fallback.
        )
    ]
    adapter = _adapter_with_response(_dart_response(rows=rows_raw))
    result = adapter.fetch_financial_statement(
        code="005930", corp_code="00126380",
        fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter,
        ifrs_type=IfrsType.CFS, batch_id=uuid4(),
    )
    assert len(result.data) == 1
    assert result.data[0].effective_date == expected_date
    assert result.data[0].effective_date_precise is False
    # citation 의 effective_date 도 동일.
    assert result.citations[0].effective_date == expected_date


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
        2024, 1, 1, tzinfo=UTC,
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


# =============================================================================
# 자사주 fetch — stockTotqySttus.json (주식의 총수 현황)
# =============================================================================

def _totqy_response(
    *,
    rows: list[dict[str, Any]] | None = None,
    status: str = "000",
) -> dict[str, Any]:
    """stockTotqySttus.json 응답 JSON 모사."""
    return {
        "status": status,
        "message": "정상" if status == "000" else "에러",
        "list": rows or [],
    }


def _totqy_row(
    *,
    se: str,
    istc_totqy: str = "5,969,782,550",
    tesstk_co: str = "0",
    distb_stock_co: str = "5,969,782,550",
    rcept_no: str = "20240501000123",
    stlm_dt: str = "2023-12-31",
) -> dict[str, Any]:
    """stockTotqySttus list 의 1 entry (se 구분별 행)."""
    return {
        "se": se,
        "isu_stock_totqy": "10,000,000,000",
        "now_to_isu_stock_totqy": istc_totqy,
        "istc_totqy": istc_totqy,
        "tesstk_co": tesstk_co,
        "distb_stock_co": distb_stock_co,
        "rcept_no": rcept_no,
        "stlm_dt": stlm_dt,
    }


def test_fetch_treasury_shares_extracts_common_stock_tesstk_co() -> None:
    """보통주 행의 tesstk_co (자기주식수) 추출 + 발행주식총수."""
    rows = [
        _totqy_row(
            se="보통주",
            istc_totqy="5,969,782,550",
            tesstk_co="538,000,000",
        ),
        _totqy_row(
            se="우선주",
            istc_totqy="822,886,700",
            tesstk_co="100,000,000",
        ),
    ]
    adapter = _adapter_with_response(_totqy_response(rows=rows))

    result = adapter.fetch_treasury_shares(
        code="005930",
        corp_code="00126380",
        fiscal_year=2023,
        fiscal_quarter=4,
        batch_id=uuid4(),
    )
    assert isinstance(result, FetchResult)
    # 보통주 행 tesstk_co 사용 (우선주 100,000,000 이 아닌 538,000,000).
    assert result.data.shares_treasury == 538_000_000
    assert result.data.shares_issued_total == 5_969_782_550
    # effective_date = rcept_no 도출 정밀 공시일 (ADR-0012 D6). fixture default
    # rcept_no="20240501000123" → 2024-05-01. precise marker 제거.
    assert result.citations[0].effective_date == date(2024, 5, 1)
    assert result.data.effective_date == date(2024, 5, 1)
    assert result.data.effective_date_precise is True
    assert result.citations[0].source == SourceKind.DART
    assert result.estimated_fields == frozenset()


def test_fetch_treasury_shares_falls_back_to_total_row() -> None:
    """보통주 행 부재 시 합계 행 fallback + warning."""
    rows = [
        _totqy_row(se="합계", istc_totqy="6,792,669,250", tesstk_co="438,000,000"),
    ]
    adapter = _adapter_with_response(_totqy_response(rows=rows))

    result = adapter.fetch_treasury_shares(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4, batch_id=uuid4(),
    )
    assert result.data.shares_treasury == 438_000_000
    assert any("합계" in w for w in result.warnings)


def test_fetch_treasury_shares_invalid_rcept_no_falls_back() -> None:
    """ADR-0012 D6 방어 — treasury rcept_no 도출 실패 시 신고기한 보수 fallback.

    rcept_no 가 14자리 숫자 schema 외면 자본시장법 제160조 신고기한 (Q4 = +90일)
    보수값 + effective_date_precise=False + estimated_fields={"effective_date"}.
    """
    rows = [
        _totqy_row(
            se="보통주", istc_totqy="5,969,782,550",
            tesstk_co="538,000,000", rcept_no="BADRCEPTNO123",
        ),
    ]
    adapter = _adapter_with_response(_totqy_response(rows=rows))
    result = adapter.fetch_treasury_shares(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4, batch_id=uuid4(),
    )
    assert result.data.shares_treasury == 538_000_000
    # Q4 신고기한 = 2024-03-30 (보수값).
    assert result.data.effective_date == date(2024, 3, 30)
    assert result.data.effective_date_precise is False
    assert result.citations[0].effective_date == date(2024, 3, 30)
    assert result.estimated_fields == frozenset({"effective_date"})


def test_fetch_treasury_shares_placeholder_rcept_no_falls_back() -> None:
    """rcept_no 부재 → placeholder identifier (비-14자리) → 보수 fallback.

    _parse_treasury_response 가 rcept_no 부재 시 placeholder 를 생성하는데
    (e.g. 'stockTotqySttus:005930:2023Q4'), 이는 14자리 숫자가 아니므로
    _rcept_date 가 None → 신고기한 보수값 fallback (precise=False).
    """
    rows = [
        {
            "se": "보통주",
            "isu_stock_totqy": "10,000,000,000",
            "now_to_isu_stock_totqy": "5,969,782,550",
            "istc_totqy": "5,969,782,550",
            "tesstk_co": "538,000,000",
            "distb_stock_co": "5,969,782,550",
            "rcept_no": "",  # 부재 → placeholder 생성.
            "stlm_dt": "2023-12-31",
        }
    ]
    adapter = _adapter_with_response(_totqy_response(rows=rows))
    result = adapter.fetch_treasury_shares(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4, batch_id=uuid4(),
    )
    assert result.data.effective_date == date(2024, 3, 30)
    assert result.data.effective_date_precise is False
    assert result.estimated_fields == frozenset({"effective_date"})


def test_fetch_treasury_shares_dash_is_missing() -> None:
    """tesstk_co '-' 는 결측 (None) — 0 으로 가정하지 않음."""
    rows = [_totqy_row(se="보통주", tesstk_co="-")]
    adapter = _adapter_with_response(_totqy_response(rows=rows))

    result = adapter.fetch_treasury_shares(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4, batch_id=uuid4(),
    )
    assert result.data.shares_treasury is None
    assert any("결측" in w or "N/A" in w for w in result.warnings)


def test_fetch_treasury_shares_empty_tesstk_co_is_missing() -> None:
    """tesstk_co 빈값은 결측 (None)."""
    rows = [_totqy_row(se="보통주", tesstk_co="")]
    adapter = _adapter_with_response(_totqy_response(rows=rows))

    result = adapter.fetch_treasury_shares(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4, batch_id=uuid4(),
    )
    assert result.data.shares_treasury is None


def test_fetch_treasury_shares_missing_tesstk_field_is_missing() -> None:
    """tesstk_co 필드 자체 누락은 결측 (None) — schema drift fail 아님."""
    row = _totqy_row(se="보통주")
    del row["tesstk_co"]
    adapter = _adapter_with_response(_totqy_response(rows=[row]))

    result = adapter.fetch_treasury_shares(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4, batch_id=uuid4(),
    )
    assert result.data.shares_treasury is None


def test_fetch_treasury_shares_zero_is_valid() -> None:
    """tesstk_co '0' 은 유효 (자사주 없음 명시) — None 과 구별."""
    rows = [_totqy_row(se="보통주", tesstk_co="0")]
    adapter = _adapter_with_response(_totqy_response(rows=rows))

    result = adapter.fetch_treasury_shares(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4, batch_id=uuid4(),
    )
    assert result.data.shares_treasury == 0


def test_fetch_treasury_shares_rate_limit_raises_retry() -> None:
    """status 020 (요청 제한) → AdapterRetryError (재무 fetch 와 동일)."""
    adapter = _adapter_with_response(_totqy_response(status="020"))
    with pytest.raises(AdapterRetryError):
        adapter.fetch_treasury_shares(
            code="005930", corp_code="00126380",
            fiscal_year=2023, fiscal_quarter=4, batch_id=uuid4(),
        )


def test_fetch_treasury_shares_no_data_status_raises() -> None:
    """status 013 (데이터없음) → AdapterError (_call_dart 비OK status)."""
    adapter = _adapter_with_response(_totqy_response(status="013"))
    with pytest.raises(AdapterError):
        adapter.fetch_treasury_shares(
            code="005930", corp_code="00126380",
            fiscal_year=2023, fiscal_quarter=4, batch_id=uuid4(),
        )


def test_fetch_treasury_shares_empty_list_raises() -> None:
    """빈 list → AdapterError."""
    adapter = _adapter_with_response(_totqy_response(rows=[]))
    with pytest.raises(AdapterError):
        adapter.fetch_treasury_shares(
            code="005930", corp_code="00126380",
            fiscal_year=2023, fiscal_quarter=4, batch_id=uuid4(),
        )


def test_fetch_treasury_shares_only_preferred_stock_is_missing() -> None:
    """우선주만 있는 경우 (보통주/합계 부재) → 자사주 결측 + warning."""
    rows = [_totqy_row(se="우선주", tesstk_co="100,000,000")]
    adapter = _adapter_with_response(_totqy_response(rows=rows))

    result = adapter.fetch_treasury_shares(
        code="005930", corp_code="00126380",
        fiscal_year=2023, fiscal_quarter=4, batch_id=uuid4(),
    )
    assert result.data.shares_treasury is None


# =============================================================================
# fetch_company_info — crno(jurir_no) 매핑 source (M7 #2)
# =============================================================================

def _company_response(
    *,
    status: str = "000",
    jurir_no: str = "1301110006246",
    corp_name: str = "삼성전자",
    stock_code: str = "005930",
) -> dict[str, Any]:
    """DART company.json 정상 응답 mock (필요 필드만)."""
    return {
        "status": status,
        "message": "정상",
        "corp_name": corp_name,
        "stock_code": stock_code,
        "jurir_no": jurir_no,
    }


def test_fetch_company_info_returns_jurir_no() -> None:
    """정상 — 13자리 jurir_no + corp_name/stock_code 추출."""
    adapter = _adapter_with_response(_company_response())
    info = adapter.fetch_company_info(corp_code="00126380")
    assert isinstance(info, CompanyInfo)
    assert info.corp_code == "00126380"
    assert info.jurir_no == "1301110006246"
    assert info.corp_name == "삼성전자"
    assert info.stock_code == "005930"


def test_fetch_company_info_normalizes_hyphenated_jurir() -> None:
    """하이픈 포함 jurir_no("130111-0006246") → 비숫자 제거 후 13자리."""
    adapter = _adapter_with_response(
        _company_response(jurir_no="130111-0006246")
    )
    info = adapter.fetch_company_info(corp_code="00126380")
    assert info.jurir_no == "1301110006246"


def test_fetch_company_info_empty_jurir_when_absent() -> None:
    """jurir_no 부재 → 빈 문자열(raise X — 호출자 skip)."""
    adapter = _adapter_with_response(_company_response(jurir_no=""))
    info = adapter.fetch_company_info(corp_code="00126380")
    assert info.jurir_no == ""


def test_fetch_company_info_empty_jurir_when_wrong_length() -> None:
    """jurir_no 가 13자리 아님(자릿수 위반) → 빈 문자열."""
    adapter = _adapter_with_response(_company_response(jurir_no="12345"))
    info = adapter.fetch_company_info(corp_code="00126380")
    assert info.jurir_no == ""


def test_fetch_company_info_auth_failure_raises() -> None:
    """status 010(인증 실패) → AdapterError."""
    adapter = _adapter_with_response(_company_response(status="010"))
    with pytest.raises(AdapterError):
        adapter.fetch_company_info(corp_code="00126380")


def test_fetch_company_info_rate_limit_raises_retry() -> None:
    """status 020(요청 제한) → AdapterRetryError."""
    adapter = _adapter_with_response(_company_response(status="020"))
    with pytest.raises(AdapterRetryError):
        adapter.fetch_company_info(corp_code="00126380")


def test_fetch_company_info_no_data_raises() -> None:
    """status 013(데이터 없음) → AdapterError(유효 corp_code 는 개황 必)."""
    adapter = _adapter_with_response(_company_response(status="013"))
    with pytest.raises(AdapterError):
        adapter.fetch_company_info(corp_code="00126380")


def test_fetch_company_info_invalid_corp_code_raises() -> None:
    """corp_code 8자리 numeric 아님 → AdapterError(입력 검증)."""
    adapter = _adapter_with_response(_company_response())
    with pytest.raises(AdapterError):
        adapter.fetch_company_info(corp_code="123")
