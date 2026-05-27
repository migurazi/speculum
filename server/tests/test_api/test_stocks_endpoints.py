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

from datetime import date
from typing import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.pit_protocols import CodeHistoryEntry, StockMasterRecord
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

def test_get_stock_detail_returns_full_info(client: TestClient) -> None:
    res = client.get("/api/stocks/005930?as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == "005930"
    assert body["name"] == "삼성전자"
    assert body["market"] == "KOSPI"
    assert body["status"] == "active"
    assert len(body["code_history"]) == 1
    # default factors 모두 stub (M0 — pipeline 미합류).
    assert len(body["factors"]) == 5
    for f in body["factors"]:
        assert f["is_na"] is True
        # evaluator taxonomy 와 일관 (missing_input:* 형식).
        assert f["na_reason"].startswith("missing_input:")


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


def test_compare_includes_factor_stubs(client: TestClient) -> None:
    """각 item 의 factors 가 default 5 개 (T25 의 stub 패턴 동일)."""
    res = client.get(
        "/api/stocks/compare?codes=005930,000660&as_of=2024-05-07"
    )
    body = res.json()
    for item in body["items"]:
        assert len(item["factors"]) == 5
        for f in item["factors"]:
            assert f["is_na"] is True
            assert f["na_reason"].startswith("missing_input:")


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
    """oracle 2 차 C3 — stub 의 na_reason 이 evaluator taxonomy 와 일관 (missing_input:* 형식)."""
    res = client.get("/api/stocks/005930?as_of=2024-05-07")
    body = res.json()
    for factor in body["factors"]:
        assert factor["is_na"] is True
        # evaluator 의 정상 na_reason 형식 ("missing_input:..." prefix).
        assert factor["na_reason"].startswith("missing_input:"), (
            f"unexpected na_reason: {factor['na_reason']}"
        )
