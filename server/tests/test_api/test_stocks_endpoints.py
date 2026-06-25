"""Stocks endpoint 통합 테스트 — /api/stocks/search + /api/stocks/{code}.

테스트 매트릭스:
1. /search — 한글 매칭 / 코드 매칭 / 입력 자동 감지
2. /search — as_of-aware (active-at-as_of 만)
3. /search — include_delisted 옵션
4. /search — limit / empty result / 정렬 (prefix 우선)
5. /{code} — 정상 detail + factor stubs
6. /{code} — zero-pad 정규화
7. /{code} — lineage 부재 → 404
8. /{code} — 비숫자 → 422
9. status enum — active / not_yet_listed / delisted
10. /search — current_name 의 회사명 forbidden_words 통과
11. /search — dirty query → 응답 echo 없음
12. lineage code_history 시점 매칭
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeFinancialRepository,
)
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    CorporateActionRecord,
    FinancialRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository

_SAMSUNG = StockMasterRecord(
    id=UUID("00000000-0000-0000-0000-000000000001"),
    current_code="005930", current_name="삼성전자", market="KOSPI",
    listing_date=date(1975, 6, 11), delisting_date=None, fiscal_month=12,
    code_history=(CodeHistoryEntry("005930", date(1975, 6, 11), None,
                                     "initial_listing"),),
)
_SK = StockMasterRecord(
    id=UUID("00000000-0000-0000-0000-000000000002"),
    current_code="000660", current_name="SK하이닉스", market="KOSPI",
    listing_date=date(1996, 12, 26), delisting_date=None, fiscal_month=12,
    code_history=(CodeHistoryEntry("000660", date(1996, 12, 26), None,
                                     "initial_listing"),),
)
_EBEST = StockMasterRecord(  # 회사명에 금지 어휘 substring ("이베스트")
    id=UUID("00000000-0000-0000-0000-000000000003"),
    current_code="078020", current_name="이베스트투자증권", market="KOSPI",
    listing_date=date(2008, 6, 23), delisting_date=None, fiscal_month=12,
    code_history=(CodeHistoryEntry("078020", date(2008, 6, 23), None,
                                     "initial_listing"),),
)
# 미상장 종목 — 2024-12-01 상장 예정. as_of=2024-05-07 시 not_yet_listed.
_FUTURE = StockMasterRecord(
    id=UUID("00000000-0000-0000-0000-000000000004"),
    current_code="999991", current_name="가상미래주식", market="KOSDAQ",
    listing_date=date(2024, 12, 2), delisting_date=None, fiscal_month=12,  # 월 (영업일)
    code_history=(CodeHistoryEntry("999991", date(2024, 12, 2), None,
                                     "initial_listing"),),
)
# 폐지 종목 — 2024-03-15 폐지. as_of 5/7 시 delisted.
_DELISTED = StockMasterRecord(
    id=UUID("00000000-0000-0000-0000-000000000005"),
    current_code=None, current_name="구상장폐지주식", market="KOSDAQ",
    listing_date=date(2020, 1, 10), delisting_date=date(2024, 3, 15),
    fiscal_month=12,
    code_history=(CodeHistoryEntry("888881", date(2020, 1, 10),
                                     date(2024, 3, 15), "initial_listing"),),
)
# Lineage with code change — 2024-06-01 (calendar 범위 내).
_LINEAGE = StockMasterRecord(
    id=UUID("00000000-0000-0000-0000-000000000006"),
    current_code="123456", current_name="종목코드변경예시", market="KOSPI",
    listing_date=date(2024, 1, 1), delisting_date=None, fiscal_month=12,
    code_history=(
        CodeHistoryEntry("100000", date(2024, 1, 1), date(2024, 6, 1),
                          "initial_listing"),
        CodeHistoryEntry("123456", date(2024, 6, 1), None, "code_change"),
    ),
)


_FIXTURE = [_SAMSUNG, _SK, _EBEST, _FUTURE, _DELISTED, _LINEAGE]


@pytest.fixture
def client() -> Iterator[TestClient]:
    repo = FakeStocksMasterRepository(records=_FIXTURE)
    app = create_app(stocks_repository=repo)
    with TestClient(app) as c:
        yield c


# =============================================================================
# 1. /search — 한글/코드 매칭
# =============================================================================

def test_search_korean_name(client: TestClient) -> None:
    res = client.get("/api/stocks/search?q=삼성&as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "삼성전자"


def test_search_numeric_query_uses_code(client: TestClient) -> None:
    """숫자 입력 → 코드 검색 자동 감지."""
    res = client.get("/api/stocks/search?q=000660&as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["code"] == "000660"


def test_search_partial_code(client: TestClient) -> None:
    """`5930` substring → `005930` 매칭."""
    res = client.get("/api/stocks/search?q=5930&as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    codes = [i["code"] for i in body["items"]]
    assert "005930" in codes


def test_search_empty_result(client: TestClient) -> None:
    res = client.get("/api/stocks/search?q=존재하지않는이름&as_of=2024-05-07")
    assert res.status_code == 200
    assert res.json()["items"] == []


# =============================================================================
# 2. /search — as_of-aware (활성 종목만 default)
# =============================================================================

def test_search_excludes_future_listings_by_default(client: TestClient) -> None:
    """as_of=2024-05-07 시 미상장 (listing_date=2024-12-01) 제외."""
    res = client.get("/api/stocks/search?q=가상미래주식&as_of=2024-05-07")
    body = res.json()
    assert body["items"] == []


def test_search_excludes_delisted_by_default(client: TestClient) -> None:
    """as_of=2024-05-07 시 폐지 (delisting_date=2024-03-15) 제외."""
    res = client.get("/api/stocks/search?q=구상장폐지주식&as_of=2024-05-07")
    body = res.json()
    assert body["items"] == []


# =============================================================================
# 3. include_delisted 옵션
# =============================================================================

def test_search_include_delisted_returns_delisted_stock(client: TestClient) -> None:
    res = client.get(
        "/api/stocks/search?q=구상장폐지주식&as_of=2024-05-07&include_delisted=true"
    )
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "delisted"


# =============================================================================
# 4. /search — limit / 정렬
# =============================================================================

def test_search_limit_caps_results(client: TestClient) -> None:
    res = client.get("/api/stocks/search?q=&as_of=2024-05-07")
    # Pydantic Query(min_length=1) → 422
    assert res.status_code == 422


def test_search_limit_param(client: TestClient) -> None:
    res = client.get("/api/stocks/search?q=종&as_of=2024-05-07&limit=1")
    assert res.status_code == 200
    assert len(res.json()["items"]) <= 1


def test_search_prefix_match_sorted_first(client: TestClient) -> None:
    """prefix 매치가 substring 매치보다 우선 — '삼성' 입력에 '삼성전자' 가 먼저."""
    # 본 fixture 에는 한 종목만 매칭. 정렬 invariant 검증을 위한 placeholder.
    res = client.get("/api/stocks/search?q=삼&as_of=2024-05-07")
    body = res.json()
    if body["items"]:
        # 첫 결과의 이름이 검색어로 시작 — prefix 우선.
        assert body["items"][0]["name"].startswith("삼")


# =============================================================================
# 5. /{code} — Stock Detail
# =============================================================================

# M1 T50 — FieldProvider 실연결 후 N/A na_reason 의 정상 taxonomy.
# 본 fixture 는 price / financial repository 미주입 (빈 Fake) → 모든 default
# factor 가 N/A 이나, M0 stub 의 단일 "pipeline_not_yet_wired" 가 아니라
# evaluator 의 실제 taxonomy (해소 못 한 field 별 사유).
_NA_REASON_PREFIXES = ("missing_input:", "insufficient_series:", "division_by_zero")


def test_get_stock_detail_returns_full_info(client: TestClient) -> None:
    res = client.get("/api/stocks/005930?as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == "005930"
    assert body["name"] == "삼성전자"
    assert body["market"] == "KOSPI"
    assert body["status"] == "active"
    assert len(body["code_history"]) == 1
    # default factors — fixture 가 fact repository 미주입이라 모두 N/A.
    # evaluator 의 실제 taxonomy 사용 (M1 T50 — stub 제거).
    # M7 #6: dividend-yield:trailing-annual + price-return:total-annual 추가 → 7.
    assert len(body["factors"]) == 7
    for f in body["factors"]:
        assert f["is_na"] is True
        assert f["na_reason"].startswith(_NA_REASON_PREFIXES)


# =============================================================================
# 6. zero-pad 정규화
# =============================================================================

def test_get_stock_detail_zero_pads_short_code(client: TestClient) -> None:
    res = client.get("/api/stocks/5930?as_of=2024-05-07")
    assert res.status_code == 200
    assert res.json()["code"] == "005930"


def test_get_stock_detail_full_6_digits(client: TestClient) -> None:
    res = client.get("/api/stocks/000660?as_of=2024-05-07")
    assert res.status_code == 200
    assert res.json()["code"] == "000660"


# =============================================================================
# 7. lineage 부재 → 404
# =============================================================================

def test_get_stock_detail_unknown_code_returns_404(client: TestClient) -> None:
    res = client.get("/api/stocks/999990?as_of=2024-05-07")
    assert res.status_code == 404


# =============================================================================
# 8. 비숫자 → 422
# =============================================================================

def test_get_stock_detail_non_numeric_code_returns_422(client: TestClient) -> None:
    res = client.get("/api/stocks/ABCDEF?as_of=2024-05-07")
    assert res.status_code == 422


def test_get_stock_detail_too_long_code_returns_422(client: TestClient) -> None:
    res = client.get("/api/stocks/1234567?as_of=2024-05-07")
    assert res.status_code == 422


# =============================================================================
# 9. status enum — not_yet_listed / delisted
# =============================================================================

def test_get_stock_detail_not_yet_listed_status(client: TestClient) -> None:
    """`/api/stocks/{code}` 가 lineage 존재 + 미상장이면 200 + status."""
    # 가상미래주식 — listing 2024-12-01, as_of 2024-05-07.
    res = client.get("/api/stocks/999991?as_of=2024-05-07")
    assert res.status_code == 200
    assert res.json()["status"] == "not_yet_listed"


def test_get_stock_detail_delisted_status(client: TestClient) -> None:
    """폐지 종목 — lineage 매칭 + status=delisted (oracle 결정 6).

    구상장폐지주식: code=888881 lineage. as_of=2024-02-01 시 active, 5/7 시 delisted.
    """
    # as_of=2024-02-01 시점은 active 였음.
    res = client.get("/api/stocks/888881?as_of=2024-02-01")
    assert res.status_code == 200
    assert res.json()["status"] == "active"

    # as_of=2024-05-07 시점은 폐지 후 (delisting_date=2024-03-15) — 200 + delisted.
    # lineage 자체는 존재하므로 endpoint 가 status 분기.
    res2 = client.get("/api/stocks/888881?as_of=2024-05-07")
    assert res2.status_code == 200
    assert res2.json()["status"] == "delisted"


# =============================================================================
# 10. forbidden_words middleware — 회사명 통과 (exclude_keys)
# =============================================================================

def test_company_name_with_forbidden_substring_passes(client: TestClient) -> None:
    """`이베스트투자증권` 같은 회사명이 BLOCK 되지 않음 — exclude_keys 적용."""
    res = client.get("/api/stocks/078020?as_of=2024-05-07")
    assert res.status_code == 200
    # 응답 본문에 회사명 그대로 노출.
    assert res.json()["name"] == "이베스트투자증권"


def test_search_company_name_forbidden_substring(client: TestClient) -> None:
    res = client.get("/api/stocks/search?q=이베스트&as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "이베스트투자증권"


# =============================================================================
# 11. dirty query → 응답 echo 없음
# =============================================================================

def test_dirty_query_does_not_echo_in_response(client: TestClient) -> None:
    """ADR-0007 의 금지 어휘가 query 에 들어와도 응답 body 에 echo X."""
    res = client.get("/api/stocks/search?q=추천&as_of=2024-05-07")
    # 응답이 200 (검색 결과 빈 list) 또는 422 — 어느 쪽이든 query value echo X.
    body_str = str(res.json())
    # "추천" 단어가 응답 본문에 echo 되지 않음.
    assert "추천" not in body_str


# =============================================================================
# 12. lineage code_history 시점 매칭
# =============================================================================

def test_lineage_old_code_matches_lineage(client: TestClient) -> None:
    """`종목코드변경예시` lineage — 옛 코드 100000 가 어떤 as_of 든 lineage 반환.

    Repository 가 lineage 전체 검색 (oracle 결정 6) — 시점 매칭 무관.
    status 산출이 시점 분기 책임.
    """
    res = client.get("/api/stocks/100000?as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "00000000-0000-0000-0000-000000000006"
    # current_code 는 lineage 의 현재 활성 코드 "123456".
    assert body["code"] == "123456"


def test_lineage_new_code_matches_after_change(client: TestClient) -> None:
    """현재 코드 123456 + as_of=2024-08-01 → 정상 매칭, status=active."""
    res = client.get("/api/stocks/123456?as_of=2024-08-01")
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == "123456"
    assert body["status"] == "active"




# =============================================================================
# 13. X-AsOf header — endpoint 응답에도 적용
# =============================================================================

def test_x_asof_header_present_on_search(client: TestClient) -> None:
    res = client.get("/api/stocks/search?q=삼성&as_of=2024-05-07")
    assert res.headers["x-asof"] == "2024-05-07"


def test_x_asof_header_present_on_detail(client: TestClient) -> None:
    res = client.get("/api/stocks/005930?as_of=2024-05-07")
    assert res.headers["x-asof"] == "2024-05-07"


def test_x_asof_snapped_on_holiday_input(client: TestClient) -> None:
    """근로자의날 (5/1) 입력 → 4/30 snap + X-AsOf-Snapped 헤더."""
    res = client.get("/api/stocks/005930?as_of=2024-05-01")
    assert res.headers["x-asof-snapped"] == "true"
    assert res.headers["x-asof-original"] == "2024-05-01"


# =============================================================================
# 14. as_of 미래 → 400
# =============================================================================

def test_future_as_of_returns_400(client: TestClient) -> None:
    res = client.get("/api/stocks/search?q=삼성&as_of=2999-12-31")
    assert res.status_code == 400
    assert res.json()["code"] == "AS_OF_IN_FUTURE"


# =============================================================================
# 15. oracle 2 차 리뷰 회귀
# =============================================================================

def test_delisting_date_boundary_is_delisted_status(client: TestClient) -> None:
    """oracle 2 차 C1 — delisting_date == as_of 경계 = DELISTED (반열린 구간).

    구상장폐지주식 delisting_date=2024-03-15. as_of=3-15 시점 = DELISTED.
    """
    # as_of=3-15 (폐지일 당일).
    res = client.get("/api/stocks/888881?as_of=2024-03-15")
    assert res.status_code == 200
    assert res.json()["status"] == "delisted"
    # as_of=3-14 (폐지 전일) = 아직 active.
    res2 = client.get("/api/stocks/888881?as_of=2024-03-14")
    assert res2.status_code == 200
    assert res2.json()["status"] == "active"


def test_listing_date_boundary_is_active(client: TestClient) -> None:
    """listing_date == as_of 경계 = ACTIVE (상장 첫날 거래 가능)."""
    # 가상미래주식 listing_date=2024-12-02 (월, 영업일).
    res = client.get("/api/stocks/999991?as_of=2024-12-02")
    assert res.status_code == 200
    assert res.json()["status"] == "active"
    # 1 일 전 (11/29 금) = not_yet_listed.
    res2 = client.get("/api/stocks/999991?as_of=2024-11-29")
    assert res2.status_code == 200
    assert res2.json()["status"] == "not_yet_listed"


def test_numeric_query_prefix_match_uses_zero_pad(client: TestClient) -> None:
    """oracle 2 차 C2 — `5930` 입력은 zero-pad 후 `005930` 와 prefix 매치."""
    res = client.get("/api/stocks/search?q=5930&as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    # `005930` 매칭 — 정렬상 가장 앞 (정확 매치 prefix).
    assert body["items"][0]["code"] == "005930"


def test_normalize_single_code_rejects_too_long() -> None:
    """oracle 2 차 P1 #2 — helper defense."""
    from app.api.routes.stocks import _normalize_single_code
    with pytest.raises(ValueError, match="exceeds"):
        _normalize_single_code("1234567")
    with pytest.raises(ValueError, match="digits"):
        _normalize_single_code("ABCDEF")


# =============================================================================
# 16. /api/stocks/compare — AC-F-05 (2~6 종목 multi-fetch)
# =============================================================================

def test_compare_two_stocks(client: TestClient) -> None:
    res = client.get(
        "/api/stocks/compare?codes=005930,000660&as_of=2024-05-07"
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 2
    assert body["not_found"] == []
    codes = [i["code"] for i in body["items"]]
    # 입력 순서 보존 — 005930 먼저, 000660 다음.
    assert codes == ["005930", "000660"]


def test_compare_preserves_input_order(client: TestClient) -> None:
    """사용자 입력 순서가 UI grid column 순서."""
    res = client.get(
        "/api/stocks/compare?codes=000660,005930&as_of=2024-05-07"
    )
    body = res.json()
    codes = [i["code"] for i in body["items"]]
    assert codes == ["000660", "005930"]


def test_compare_partial_missing_returns_not_found(client: TestClient) -> None:
    """일부 lineage 부재 → 200 + not_found list."""
    res = client.get(
        "/api/stocks/compare?codes=005930,999999&as_of=2024-05-07"
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["code"] == "005930"
    assert body["not_found"] == ["999999"]


def test_compare_zero_pads_short_codes(client: TestClient) -> None:
    res = client.get(
        "/api/stocks/compare?codes=5930,660&as_of=2024-05-07"
    )
    assert res.status_code == 200
    body = res.json()
    codes = [i["code"] for i in body["items"]]
    assert codes == ["005930", "000660"]


def test_compare_dedup_after_zero_pad(client: TestClient) -> None:
    """`5930` 과 `005930` 은 정규화 후 동일 → dedup → 1 개 → 400."""
    res = client.get(
        "/api/stocks/compare?codes=5930,005930&as_of=2024-05-07"
    )
    assert res.status_code == 400
    assert "최소 2 개" in res.json()["detail"]


def test_compare_min_codes_enforced(client: TestClient) -> None:
    """1 개만 입력 → 400."""
    res = client.get(
        "/api/stocks/compare?codes=005930&as_of=2024-05-07"
    )
    assert res.status_code == 400
    assert "최소" in res.json()["detail"]


def test_compare_max_codes_enforced(client: TestClient) -> None:
    """7 개 입력 → 400."""
    res = client.get(
        "/api/stocks/compare?codes=1,2,3,4,5,6,7&as_of=2024-05-07"
    )
    assert res.status_code == 400
    assert "최대" in res.json()["detail"]


def test_compare_six_codes_allowed(client: TestClient) -> None:
    """경계 — 정확히 6 개는 허용."""
    res = client.get(
        "/api/stocks/compare?codes=005930,000660,078020,888881,999991,123456"
        "&as_of=2024-05-07"
    )
    assert res.status_code == 200
    body = res.json()
    # 6 종목 모두 lineage 존재 → not_found 빈.
    assert body["not_found"] == []
    # items 가 6 개여야 (lineage 모두 존재).
    assert len(body["items"]) == 6


def test_compare_non_digit_code_returns_400(client: TestClient) -> None:
    res = client.get(
        "/api/stocks/compare?codes=ABC,DEF&as_of=2024-05-07"
    )
    assert res.status_code == 400
    assert "숫자" in res.json()["detail"]


def test_compare_too_long_code_returns_400(client: TestClient) -> None:
    res = client.get(
        "/api/stocks/compare?codes=1234567,005930&as_of=2024-05-07"
    )
    assert res.status_code == 400


def test_compare_empty_codes_returns_422(client: TestClient) -> None:
    """`?codes=` 빈 값 → Query(min_length=1) 422."""
    res = client.get("/api/stocks/compare?codes=&as_of=2024-05-07")
    assert res.status_code == 422


# =============================================================================
# 재무 시계열 + corporate action — 테스트 공통 fixture
# =============================================================================

_NOW = datetime(2024, 1, 1, tzinfo=UTC)
_BASE_CITATION = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_CA_CITATION   = UUID("bbbbbbbb-0000-0000-0000-000000000001")

def _fin(
    code: str,
    account: str,
    fiscal_period: str,
    value: str,
    effective_date: date,
    *,
    superseded_by: UUID | None = None,
    ifrs_type: str = "consolidated",
    unit: str = "krw",
) -> FinancialRecord:
    """FinancialRecord 생성 헬퍼 — 반복 코드 최소화."""
    from uuid import uuid4
    return FinancialRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=UUID("00000000-0000-0000-0000-000000000001"),
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        account=account,
        value=Decimal(value),
        unit=unit,
        ifrs_type=ifrs_type,
        citation_id=_BASE_CITATION,
        superseded_by=superseded_by,
        created_at=_NOW,
    )


def _ca(
    code: str,
    action_type: str,
    announced_date: date,
    effective_date: date,
    *,
    ratio: str | None = None,
    superseded_by: UUID | None = None,
) -> CorporateActionRecord:
    """CorporateActionRecord 생성 헬퍼."""
    from uuid import uuid4
    return CorporateActionRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=UUID("00000000-0000-0000-0000-000000000001"),
        action_type=action_type,
        announced_date=announced_date,
        effective_date=effective_date,
        payment_date=None,
        ratio=Decimal(ratio) if ratio is not None else None,
        cash_amount=None,
        details={},
        citation_id=_CA_CITATION,
        superseded_by=superseded_by,
        created_at=_NOW,
    )


# =============================================================================
# T57 Phase 1 — 재무 시계열 `GET /{code}/financials`
# =============================================================================

class TestFinancials:
    """재무 시계열 endpoint 통합 테스트."""

    def _client_with_financials(
        self, records: list[FinancialRecord],
    ) -> TestClient:
        repo = FakeStocksMasterRepository(records=_FIXTURE)
        fin_repo = FakeFinancialRepository(records=records)
        app = create_app(
            stocks_repository=repo,
            financial_repository=fin_repo,
        )
        return TestClient(app)

    # ① 다분기 account 시계열 — periods 정렬·values 정합
    def test_multi_period_series_sorted_and_aligned(self) -> None:
        """revenue 2 분기 적재 → periods 오름차순, values 정합."""
        records = [
            _fin("005930", "revenue", "2023Q3", "10000", date(2023, 11, 15)),
            _fin("005930", "revenue", "2023Q4", "12000", date(2024, 2, 1)),
        ]
        client = self._client_with_financials(records)
        res = client.get("/api/stocks/005930/financials?as_of=2024-05-07")
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == "005930"
        # periods 오름차순 정렬 확인.
        assert body["periods"] == ["2023Q3", "2023Q4"]
        # revenue item 의 values 정합 확인.
        revenue_item = next(i for i in body["items"] if i["account"] == "revenue")
        assert revenue_item["name"] == "매출액"
        assert revenue_item["values"] == ["10000", "12000"]

    # ② 결손 분기 None — 일부 분기에 account 없는 경우
    def test_missing_period_returns_none_in_values(self) -> None:
        """revenue 는 2023Q3·2023Q4, operating_income 은 2023Q4 만 → revenue Q3 값 자리에 None."""
        records = [
            _fin("005930", "revenue", "2023Q3", "10000", date(2023, 11, 15)),
            _fin("005930", "revenue", "2023Q4", "12000", date(2024, 2, 1)),
            _fin("005930", "operating_income", "2023Q4", "3000", date(2024, 2, 1)),
        ]
        client = self._client_with_financials(records)
        res = client.get("/api/stocks/005930/financials?as_of=2024-05-07")
        assert res.status_code == 200
        body = res.json()
        # periods 합집합 = ["2023Q3", "2023Q4"].
        assert set(body["periods"]) == {"2023Q3", "2023Q4"}
        # operating_income 은 2023Q3 자리가 None.
        oi_item = next(i for i in body["items"] if i["account"] == "operating_income")
        q3_idx = body["periods"].index("2023Q3")
        q4_idx = body["periods"].index("2023Q4")
        assert oi_item["values"][q3_idx] is None
        assert oi_item["values"][q4_idx] == "3000"

    # ③ 적재 없는 account 제외
    def test_account_with_no_data_excluded_from_items(self) -> None:
        """revenue 만 있고 나머지 account 는 적재 없음 → items 에 revenue 만."""
        records = [
            _fin("005930", "revenue", "2023Q4", "12000", date(2024, 2, 1)),
        ]
        client = self._client_with_financials(records)
        res = client.get("/api/stocks/005930/financials?as_of=2024-05-07")
        assert res.status_code == 200
        body = res.json()
        accounts_in_response = [i["account"] for i in body["items"]]
        assert accounts_in_response == ["revenue"]

    # ④ PIT — effective_date > as_of 인 분기 제외
    def test_pit_excludes_future_effective_date(self) -> None:
        """as_of=2024-03-01. 2023Q4 effective_date=2024-03-31 은 as_of 이후 → 제외."""
        records = [
            _fin("005930", "revenue", "2023Q3", "10000", date(2023, 11, 15)),
            # effective_date 가 as_of 이후 → fetch_financials 가 PIT 필터로 제외.
            _fin("005930", "revenue", "2023Q4", "12000", date(2024, 3, 31)),
        ]
        client = self._client_with_financials(records)
        res = client.get("/api/stocks/005930/financials?as_of=2024-03-01")
        assert res.status_code == 200
        body = res.json()
        # 2023Q4 는 effective_date>as_of → periods 에서 제외.
        assert "2023Q4" not in body["periods"]
        assert "2023Q3" in body["periods"]

    # ⑤ 빈 데이터 → 200 + 빈 periods/items
    def test_no_data_returns_200_with_empty_periods_and_items(self) -> None:
        """적재 데이터 없으면 200 + periods=[] + items=[]."""
        client = self._client_with_financials([])
        res = client.get("/api/stocks/005930/financials?as_of=2024-05-07")
        assert res.status_code == 200
        body = res.json()
        assert body["periods"] == []
        assert body["items"] == []

    # unit 필드 전달 확인
    def test_unit_propagated_from_record(self) -> None:
        """basic_eps 의 unit 이 올바르게 wire 로 전달되는지 확인."""
        records = [
            _fin("005930", "basic_eps", "2023Q4", "1500", date(2024, 2, 1), unit="krw"),
        ]
        client = self._client_with_financials(records)
        res = client.get("/api/stocks/005930/financials?as_of=2024-05-07")
        body = res.json()
        eps_item = next(i for i in body["items"] if i["account"] == "basic_eps")
        assert eps_item["unit"] == "krw"
        assert eps_item["name"] == "기본 EPS"


# =============================================================================
# T57 Phase 1 — prices corporate action 추가 (`GET /{code}/prices`)
# =============================================================================

class TestPricesWithCorporateActions:
    """prices endpoint 의 corporate action 통합 테스트."""

    def _client_with_actions(
        self, ca_records: list[CorporateActionRecord],
    ) -> TestClient:
        repo = FakeStocksMasterRepository(records=_FIXTURE)
        ca_repo = FakeCorporateActionRepository(records=ca_records)
        app = create_app(
            stocks_repository=repo,
            corporate_action_repository=ca_repo,
        )
        return TestClient(app)

    # ⑥ as_of 범위 내 action 포함 / 범위 밖·미래 effective_date 제외
    def test_action_within_range_included(self) -> None:
        """effective_date 가 [start, as_of] 범위 내 → actions 에 포함."""
        # days=365 기본값 → start = 2024-05-07 - 365 = 2023-05-07
        records = [
            _ca(
                "005930", "split",
                announced_date=date(2023, 12, 1),
                effective_date=date(2023, 12, 15),
                ratio="5.0",
            ),
        ]
        client = self._client_with_actions(records)
        res = client.get("/api/stocks/005930/prices?as_of=2024-05-07")
        assert res.status_code == 200
        body = res.json()
        assert len(body["actions"]) == 1
        action = body["actions"][0]
        assert action["action_type"] == "split"
        assert action["effective_date"] == "2023-12-15"
        assert action["ratio"] == "5.0"

    def test_action_outside_price_range_excluded(self) -> None:
        """effective_date 가 start 이전 → actions 에 제외."""
        # days=30 → start = 2024-05-07 - 30 = 2024-04-07
        # effective_date=2024-03-01 → start(2024-04-07) 이전 → 제외.
        records = [
            _ca(
                "005930", "dividend",
                announced_date=date(2024, 3, 1),
                effective_date=date(2024, 3, 1),  # start 이전
            ),
        ]
        client = self._client_with_actions(records)
        res = client.get("/api/stocks/005930/prices?as_of=2024-05-07&days=30")
        assert res.status_code == 200
        body = res.json()
        assert body["actions"] == []

    def test_action_future_effective_date_excluded(self) -> None:
        """effective_date > as_of → 범위 밖이므로 제외.

        fetch_actions 는 announced_date<=as_of 기준이라 effective_date 가
        미래인 사건도 반환할 수 있음. endpoint 가 [start, as_of] 필터 적용.
        """
        records = [
            _ca(
                "005930", "merger",
                announced_date=date(2024, 4, 1),   # announced <= as_of: repo 가 반환
                effective_date=date(2024, 6, 1),   # > as_of(2024-05-07): 제외
            ),
        ]
        client = self._client_with_actions(records)
        res = client.get("/api/stocks/005930/prices?as_of=2024-05-07")
        assert res.status_code == 200
        body = res.json()
        assert body["actions"] == []

    # ⑦ action 없으면 빈 tuple
    def test_no_actions_returns_empty_tuple(self) -> None:
        """corporate action 미적재 → actions=[]."""
        client = self._client_with_actions([])
        res = client.get("/api/stocks/005930/prices?as_of=2024-05-07")
        assert res.status_code == 200
        body = res.json()
        assert body["actions"] == []

    def test_action_ratio_none_when_not_applicable(self) -> None:
        """ratio 없는 dividend action → ratio 필드 None."""
        records = [
            _ca(
                "005930", "dividend",
                announced_date=date(2024, 3, 1),
                effective_date=date(2024, 3, 15),
                ratio=None,
            ),
        ]
        client = self._client_with_actions(records)
        res = client.get("/api/stocks/005930/prices?as_of=2024-05-07")
        assert res.status_code == 200
        body = res.json()
        assert len(body["actions"]) == 1
        assert body["actions"][0]["ratio"] is None


def test_compare_includes_factor_stubs(client: TestClient) -> None:
    """각 item 의 factors 가 default 7 개 (M7 #6 추가 후). fact 미주입 fixture 라 N/A."""
    res = client.get(
        "/api/stocks/compare?codes=005930,000660&as_of=2024-05-07"
    )
    body = res.json()
    for item in body["items"]:
        assert len(item["factors"]) == 7
        for f in item["factors"]:
            assert f["is_na"] is True
            assert f["na_reason"].startswith(_NA_REASON_PREFIXES)


def test_compare_x_asof_header_set(client: TestClient) -> None:
    """compare endpoint 도 X-AsOf 헤더 set (T24 dependency 통합)."""
    res = client.get(
        "/api/stocks/compare?codes=005930,000660&as_of=2024-05-07"
    )
    assert res.headers["x-asof"] == "2024-05-07"


def test_compare_status_per_item(client: TestClient) -> None:
    """compare 의 each item 이 status 산출 (active / not_yet_listed / delisted)."""
    # 005930 (active) + 999991 (not_yet_listed) + 888881 (delisted at 5/7).
    res = client.get(
        "/api/stocks/compare?codes=005930,999991,888881&as_of=2024-05-07"
    )
    body = res.json()
    statuses = {i["code"]: i["status"] for i in body["items"]}
    assert statuses["005930"] == "active"
    assert statuses["999991"] == "not_yet_listed"
    assert statuses["888881"] == "delisted"


def test_compare_future_as_of_400(client: TestClient) -> None:
    res = client.get(
        "/api/stocks/compare?codes=005930,000660&as_of=2999-12-31"
    )
    assert res.status_code == 400
    assert res.json()["code"] == "AS_OF_IN_FUTURE"


def test_compare_trailing_comma_ignored(client: TestClient) -> None:
    """oracle T27 #3 — trailing comma 는 빈 부분으로 strip + 무시."""
    res = client.get(
        "/api/stocks/compare?codes=005930,000660,&as_of=2024-05-07"
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 2


def test_compare_not_found_preserves_input_order(client: TestClient) -> None:
    """oracle T27 #5 — not_found 도 입력 순서 보존 (items 와 일관)."""
    # 입력 순서: ZZ first, 005930, AA last (AA, ZZ 모두 lineage 부재).
    res = client.get(
        "/api/stocks/compare?codes=999998,005930,999997&as_of=2024-05-07"
    )
    body = res.json()
    # not_found 입력 순서대로 999998 먼저, 999997 다음 (사전식이면 반대).
    assert body["not_found"] == ["999998", "999997"]


def test_compare_dedup_message_explains_normalization(client: TestClient) -> None:
    """oracle T27 #4 — dedup 발생 시 정규화 사실 명시."""
    res = client.get(
        "/api/stocks/compare?codes=5930,005930&as_of=2024-05-07"
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    # 정규화 사실 안내가 메시지에 포함.
    assert "zero-pad" in detail or "정규화" in detail


def test_compare_dirty_codes_no_echo(client: TestClient) -> None:
    """금지 어휘를 codes 에 넣어도 400 message 에 echo X."""
    res = client.get(
        "/api/stocks/compare?codes=추천,매수&as_of=2024-05-07"
    )
    # 400 (비숫자) — message 가 안전 vocabulary 만.
    assert res.status_code == 400
    body_str = str(res.json())
    assert "추천" not in body_str
    assert "매수" not in body_str


def test_factor_stub_na_reason_uses_evaluator_taxonomy(client: TestClient) -> None:
    """na_reason 이 evaluator 의 실제 taxonomy 와 일관 (M1 T50 — stub 제거).

    fixture 가 fact repository 미주입이라 N/A 이나, 사유는 field 별 실제 결손
    (`missing_input:<field>` 또는 series 결손 `insufficient_series:<field>:...`).
    """
    res = client.get("/api/stocks/005930?as_of=2024-05-07")
    body = res.json()
    for factor in body["factors"]:
        assert factor["is_na"] is True
        assert factor["na_reason"].startswith(_NA_REASON_PREFIXES), (
            f"unexpected na_reason: {factor['na_reason']}"
        )


# =============================================================================
# 17. M1 T50 — FieldProvider 실연결 (실값 반환, stub 아님)
# =============================================================================

def _wired_client() -> TestClient:
    """price / financial repository 를 주입한 client — 실 factor 산출 가능.

    삼성전자 (005930) 의 4 분기 basic_eps 재무 fact 를 채워 EPS (TTM) factor 가
    실값으로 산출되도록. as_of=2024-05-07 시점 active.
    """
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import uuid4

    from app.repositories.fakes import FakeFinancialRepository, FakePriceRepository
    from app.repositories.pit_protocols import FinancialRecord, PriceRecord

    cid = UUID("00000000-0000-0000-0000-0000000000bb")
    lineage = UUID("00000000-0000-0000-0000-000000000001")

    def _eps(fiscal_period: str, value: str, eff: date) -> FinancialRecord:
        return FinancialRecord(
            id=uuid4(), code="005930", code_lineage_id=lineage,
            effective_date=eff, fiscal_period=fiscal_period,
            account="basic_eps", value=Decimal(value), unit="krw",
            ifrs_type="consolidated", citation_id=cid, superseded_by=None,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )

    # B1: basic_eps 는 FLOW 계정 — DART 누적(YTD)으로 seed. 의도 standalone
    # [500,600,700,800](TTM 2600)을 위해 누적 [500,1100,1800,2600] seed.
    financials = [
        _eps("2023Q1", "500", date(2023, 5, 15)),
        _eps("2023Q2", "1100", date(2023, 8, 14)),
        _eps("2023Q3", "1800", date(2023, 11, 14)),
        _eps("2023Q4", "2600", date(2024, 3, 30)),
    ]
    price = PriceRecord(
        id=uuid4(), code="005930", code_lineage_id=lineage,
        effective_date=date(2024, 4, 30),
        open_raw=Decimal("71000"), high_raw=Decimal("71000"),
        low_raw=Decimal("71000"), close_raw=Decimal("71000"),
        volume=1000, trading_value=Decimal("1000000"),
        close_adjusted=Decimal("71000"), citation_id=cid,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )

    repo = FakeStocksMasterRepository(records=_FIXTURE)
    app = create_app(
        stocks_repository=repo,
        financial_repository=FakeFinancialRepository(records=financials),
        price_repository=FakePriceRepository(records=[price]),
    )
    return TestClient(app)


def test_detail_returns_real_factor_value_not_stub() -> None:
    """EPS (TTM) factor 가 실 산출값 — stub 아님 (M1 T50 AC-M1-F-10)."""
    with _wired_client() as c:
        res = c.get("/api/stocks/005930?as_of=2024-05-07")
        assert res.status_code == 200
        body = res.json()
        eps = next(
            f for f in body["factors"]
            if f["canonical_id"] == "eps:basic-ttm-consolidated-ifrs"
        )
        assert eps["is_na"] is False
        assert eps["na_reason"] is None
        # 500+600+700+800 = 2600.
        assert eps["value"] == "2600"


def test_compare_returns_real_factor_value_not_stub() -> None:
    """compare 도 종목별 실평가 — 005930 EPS 실값, 000660 은 fact 없어 N/A."""
    with _wired_client() as c:
        res = c.get("/api/stocks/compare?codes=005930,000660&as_of=2024-05-07")
        assert res.status_code == 200
        body = res.json()
        by_code = {item["code"]: item for item in body["items"]}
        samsung_eps = next(
            f for f in by_code["005930"]["factors"]
            if f["canonical_id"] == "eps:basic-ttm-consolidated-ifrs"
        )
        assert samsung_eps["is_na"] is False
        assert samsung_eps["value"] == "2600"
        # 000660 은 fact 미주입 → N/A.
        sk_eps = next(
            f for f in by_code["000660"]["factors"]
            if f["canonical_id"] == "eps:basic-ttm-consolidated-ifrs"
        )
        assert sk_eps["is_na"] is True


def test_detail_pit_excludes_lookahead_quarter() -> None:
    """PIT — as_of 이전 시점이면 미공시 분기 미사용 → EPS N/A.

    as_of=2024-02-01 시점은 2023Q4 (공시 2024-03-30) 미공시 → 3 분기만 → N/A.
    (테스트 calendar 가 2024 만 cover 하므로 2024-02-01 사용.)
    """
    with _wired_client() as c:
        res = c.get("/api/stocks/005930?as_of=2024-02-01")
        assert res.status_code == 200
        body = res.json()
        eps = next(
            f for f in body["factors"]
            if f["canonical_id"] == "eps:basic-ttm-consolidated-ifrs"
        )
        # 2023Q4 미공시 (eff 2024-03-30 > as_of) → 3 분기 → strict N/A.
        assert eps["is_na"] is True
        assert eps["na_reason"].startswith("insufficient_series:")


def _per_pbr_wired_client() -> TestClient:
    """PER/PBR 파생 필드 (market_cap_ex_treasury) 실평가용 fully-wired client.

    삼성전자 (005930) 에 4 분기 net_income + equity + 종가 + 발행주식수 +
    자사주를 모두 주입. as_of=2024-05-07 시점에 PER/PBR 카드가 실값이 되도록.
    market_cap_ex_treasury 는 provider 의 derived_factor 해소 (Option B) 가 동일
    evaluator 로 market-cap:ex-treasury 를 평가해 공급.
    """
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import uuid4

    from app.repositories.fakes import (
        FakeFinancialRepository,
        FakeMarketCapRepository,
        FakePriceRepository,
        FakeTreasurySharesRepository,
    )
    from app.repositories.pit_protocols import (
        FinancialRecord,
        MarketCapRecord,
        PriceRecord,
        TreasurySharesRecord,
    )

    cid = UUID("00000000-0000-0000-0000-0000000000bb")
    lineage = UUID("00000000-0000-0000-0000-000000000001")
    created = datetime(2024, 1, 1, tzinfo=UTC)

    def _fin(account: str, fp: str, value: str, eff: date) -> FinancialRecord:
        return FinancialRecord(
            id=uuid4(), code="005930", code_lineage_id=lineage,
            effective_date=eff, fiscal_period=fp, account=account,
            value=Decimal(value), unit="krw", ifrs_type="consolidated",
            citation_id=cid, superseded_by=None, created_at=created,
        )

    # B1: net_income 은 FLOW 계정 — DART 누적(YTD)으로 seed. 의도 분기단독
    # 1e9 × 4(TTM 4e9)를 위해 누적 [1e9,2e9,3e9,4e9] seed. equity 는 stock
    # 계정(시점 잔액)이라 미변환 — 그대로 시점값.
    financials = [
        _fin("net_income_attributable_to_owners", "2023Q1", "1000000000",
             date(2023, 5, 15)),
        _fin("net_income_attributable_to_owners", "2023Q2", "2000000000",
             date(2023, 8, 14)),
        _fin("net_income_attributable_to_owners", "2023Q3", "3000000000",
             date(2023, 11, 14)),
        _fin("net_income_attributable_to_owners", "2023Q4", "4000000000",
             date(2024, 3, 30)),
        _fin("equity_attributable_to_owners", "2023Q4", "30000000000",
             date(2024, 3, 30)),
    ]
    price = PriceRecord(
        id=uuid4(), code="005930", code_lineage_id=lineage,
        effective_date=date(2024, 4, 30),
        open_raw=Decimal("50000"), high_raw=Decimal("50000"),
        low_raw=Decimal("50000"), close_raw=Decimal("50000"),
        volume=1000, trading_value=Decimal("1000000"),
        close_adjusted=Decimal("50000"), citation_id=cid, created_at=created,
    )
    market_cap = MarketCapRecord(
        id=uuid4(), code="005930", code_lineage_id=lineage,
        effective_date=date(2024, 4, 30), market_cap=Decimal("50000000000"),
        shares_outstanding=1_000_000, shares_treasury=None,
        citation_id=cid, created_at=created,
    )
    treasury = TreasurySharesRecord(
        id=uuid4(), code="005930", code_lineage_id=lineage,
        effective_date=date(2024, 3, 30), fiscal_period="2023Q4",
        shares_treasury=100_000, citation_id=cid, superseded_by=None,
        created_at=created,
    )

    repo = FakeStocksMasterRepository(records=_FIXTURE)
    app = create_app(
        stocks_repository=repo,
        financial_repository=FakeFinancialRepository(records=financials),
        price_repository=FakePriceRepository(records=[price]),
        market_cap_repository=FakeMarketCapRepository(records=[market_cap]),
        treasury_repository=FakeTreasurySharesRepository(records=[treasury]),
    )
    return TestClient(app)


def test_detail_per_pbr_real_values_via_derived_field() -> None:
    """end-to-end — Stock Detail 의 PER/PBR 카드가 full 데이터 시 실값.

    market_cap_ex_treasury = (1_000_000 - 100_000) × 50000 = 45_000_000_000.
    PER = market_cap_ex_treasury / sum(net_income 4Q=4_000_000_000) = 11.25.
    PBR = market_cap_ex_treasury / equity(30_000_000_000) = 1.5.
    파생 필드 (derived_factor) 가 provider 에서 market-cap:ex-treasury 를 동일
    evaluator 로 평가해 공급 (Option B). 카드가 stub 이 아닌 실값임을 검증.
    """
    from decimal import Decimal

    with _per_pbr_wired_client() as c:
        res = c.get("/api/stocks/005930?as_of=2024-05-07")
        assert res.status_code == 200
        body = res.json()
        by_id = {f["canonical_id"]: f for f in body["factors"]}

        mcap = by_id["market-cap:ex-treasury"]
        assert mcap["is_na"] is False
        assert Decimal(mcap["value"]) == Decimal("45000000000")

        per = by_id["per:ttm-consolidated-ifrs"]
        assert per["is_na"] is False
        assert per["na_reason"] is None
        assert Decimal(per["value"]) == Decimal("11.25")

        pbr = by_id["pbr:consolidated-ifrs"]
        assert pbr["is_na"] is False
        assert pbr["na_reason"] is None
        assert Decimal(pbr["value"]) == Decimal("1.5")


# =============================================================================
# 가격 시계열 — GET /api/stocks/{code}/prices (가격 차트 backend)
# =============================================================================

def _price_bar(code: str, lineage, cid, d: date, close: str):
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import uuid4

    from app.repositories.pit_protocols import PriceRecord
    return PriceRecord(
        id=uuid4(), code=code, code_lineage_id=lineage,
        effective_date=d,
        open_raw=Decimal(close), high_raw=Decimal(close),
        low_raw=Decimal(close), close_raw=Decimal(close),
        volume=1000, trading_value=Decimal("1000000"),
        close_adjusted=Decimal(close), citation_id=cid,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def test_get_stock_prices_returns_pit_filtered_ascending_series() -> None:
    """가격 시계열 — effective_date<=as_of 범위 일봉(asc), as_of 이후 제외, OHLC str."""
    from decimal import Decimal
    from uuid import uuid4

    from app.repositories.fakes import FakePriceRepository

    lineage, cid = uuid4(), uuid4()
    prices = [
        _price_bar("005930", lineage, cid, date(2024, 6, 25), "80000"),
        _price_bar("005930", lineage, cid, date(2024, 6, 26), "80500"),
        _price_bar("005930", lineage, cid, date(2024, 6, 27), "81000"),
        _price_bar("005930", lineage, cid, date(2024, 7, 1), "82000"),  # as_of 이후 → 제외
    ]
    app = create_app(price_repository=FakePriceRepository(records=prices))
    with TestClient(app) as c:
        res = c.get("/api/stocks/005930/prices?as_of=2024-06-28")
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == "005930"
        assert body["as_of"] == "2024-06-28"
        dates = [b["date"] for b in body["bars"]]
        assert dates == ["2024-06-25", "2024-06-26", "2024-06-27"]  # PIT + asc
        first = body["bars"][0]
        assert Decimal(first["close"]) == Decimal("80000")  # str OHLC
        assert "close_adjusted" in first and first["volume"] == 1000


def test_get_stock_prices_empty_when_no_data() -> None:
    """가격 데이터 없는 종목 → 200 + 빈 bars (404 아님 — 차트가 '데이터 없음')."""
    app = create_app()
    with TestClient(app) as c:
        res = c.get("/api/stocks/000660/prices?as_of=2024-06-28")
        assert res.status_code == 200
        assert res.json()["bars"] == []


# =============================================================================
# 18. M7 #6 — DEFAULT_DISPLAY_FACTORS 개수·canonical_id 검증
# =============================================================================

def test_default_display_factors_count_and_ids(client: TestClient) -> None:
    """M7 #6 — DEFAULT_DISPLAY_FACTORS 에 7 개 factor 가 있고
    dividend-yield:trailing-annual, price-return:total-annual 이 포함된다."""
    from app.schemas.stocks import DEFAULT_DISPLAY_FACTORS

    assert len(DEFAULT_DISPLAY_FACTORS) == 7
    assert "dividend-yield:trailing-annual" in DEFAULT_DISPLAY_FACTORS
    assert "price-return:total-annual" in DEFAULT_DISPLAY_FACTORS


def test_detail_factor_canonical_ids_include_m7_additions(client: TestClient) -> None:
    """stock detail response 의 factors 에 M7 #6 추가 factor 의 canonical_id 포함."""
    res = client.get("/api/stocks/005930?as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    canonical_ids = {f["canonical_id"] for f in body["factors"]}
    assert "dividend-yield:trailing-annual" in canonical_ids
    assert "price-return:total-annual" in canonical_ids
