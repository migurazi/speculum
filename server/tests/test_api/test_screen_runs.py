"""Screen + Runs endpoint 통합 테스트 — POST /api/screen + /api/runs + GET.

테스트 매트릭스:
1. POST /api/screen — body 검증, result_codes, data_versions freeze
2. POST /api/screen — ConditionIn / factor canonical_id regex
3. POST /api/screen — OpEnum coercion (json string)
4. POST /api/screen — Op 잘못된 enum 값
5. POST /api/screen — factor canonical_id 형식 위반 (text injection)
6. POST /api/screen — extra field forbidden
7. POST /api/runs — Save Run + result_hash + computed_at
8. POST /api/runs — result_hash determinism
9. GET /api/runs — user_id IDOR 차단
10. GET /api/runs/{id}
11. GET /api/runs/{id}/diff
12. as_of integration (X-AsOf header, 미래 거부)
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.pit_protocols import CodeHistoryEntry, StockMasterRecord
from app.repositories.screen_run_repository import FakeScreenRunRepository
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
_FIXTURE = [_SAMSUNG, _SK]

_VALID_BODY = {
    "conditions": [
        {"factor": "per:ttm-consolidated-ifrs", "op": "<", "value": "10"},
    ],
    "selected_factors": ["per:ttm-consolidated-ifrs"],
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    repo = FakeStocksMasterRepository(records=_FIXTURE)
    runs = FakeScreenRunRepository()
    app = create_app(stocks_repository=repo, runs_repository=runs)
    with TestClient(app) as c:
        yield c


# =============================================================================
# 1. POST /api/screen
# =============================================================================

def test_screen_no_backing_data_excludes_all(client: TestClient) -> None:
    """조건 매칭 (M1) — fact (price/financial) 미주입 fixture 면 모든 factor 가
    N/A → 모든 종목이 조건 (PER<10) 불충족으로 제외.

    M0 stub 시절엔 active universe 전체를 무조건 반환했으나, M1 은 condition 을
    실평가하며 N/A 종목을 정직하게 제외한다 (값 없는 종목이 'PER<10' 을
    만족한다고 주장하지 않음 — Fidelity §2.1).
    """
    res = client.post("/api/screen?as_of=2024-05-07", json=_VALID_BODY)
    assert res.status_code == 200
    body = res.json()
    assert body["result_codes"] == []
    assert body["total"] == 0


def test_screen_includes_data_versions(client: TestClient) -> None:
    res = client.post("/api/screen?as_of=2024-05-07", json=_VALID_BODY)
    body = res.json()
    assert "data_versions" in body
    assert "factor_pack_content_hash" in body["data_versions"]
    assert "pit_policy_version" in body["data_versions"]


def test_screen_x_asof_header(client: TestClient) -> None:
    res = client.post("/api/screen?as_of=2024-05-07", json=_VALID_BODY)
    assert res.headers["x-asof"] == "2024-05-07"


# =============================================================================
# 2. ConditionIn 검증
# =============================================================================

def test_screen_rejects_text_injection_in_factor(client: TestClient) -> None:
    """ADR-0007 AC-P-02 — factor field 의 임의 text 차단 (forbidden_words 회피)."""
    body = {
        "conditions": [{"factor": "추천종목", "op": "<", "value": "10"}],
        "selected_factors": ["per:ttm-consolidated-ifrs"],
    }
    res = client.post("/api/screen?as_of=2024-05-07", json=body)
    assert res.status_code == 422


def test_screen_op_enum_string_coercion(client: TestClient) -> None:
    """OpEnum 의 모든 값 (`<`, `<=`, `=`, `>=`, `>`, `!=`) 통과."""
    for op in ("<", "<=", "=", ">=", ">", "!="):
        body = {
            "conditions": [{"factor": "per:ttm-consolidated-ifrs",
                             "op": op, "value": "10"}],
            "selected_factors": ["per:ttm-consolidated-ifrs"],
        }
        res = client.post("/api/screen?as_of=2024-05-07", json=body)
        assert res.status_code == 200, f"op={op!r} 실패"


def test_screen_rejects_invalid_op(client: TestClient) -> None:
    body = {
        "conditions": [{"factor": "per:ttm-consolidated-ifrs",
                         "op": "INVALID_OP", "value": "10"}],
        "selected_factors": ["per:ttm-consolidated-ifrs"],
    }
    res = client.post("/api/screen?as_of=2024-05-07", json=body)
    assert res.status_code == 422


def test_screen_rejects_extra_fields(client: TestClient) -> None:
    body = {
        "conditions": [{
            "factor": "per:ttm-consolidated-ifrs", "op": "<", "value": "10",
            "surprise_field": "foo",
        }],
        "selected_factors": ["per:ttm-consolidated-ifrs"],
    }
    res = client.post("/api/screen?as_of=2024-05-07", json=body)
    assert res.status_code == 422


def test_screen_rejects_empty_conditions(client: TestClient) -> None:
    body = {"conditions": [], "selected_factors": ["per:ttm-consolidated-ifrs"]}
    res = client.post("/api/screen?as_of=2024-05-07", json=body)
    assert res.status_code == 422


def test_screen_rejects_invalid_selected_factor_id(client: TestClient) -> None:
    body = {
        "conditions": _VALID_BODY["conditions"],
        "selected_factors": ["matt시가총액"],  # canonical_id 형식 위반
    }
    res = client.post("/api/screen?as_of=2024-05-07", json=body)
    assert res.status_code == 422


# =============================================================================
# 3. POST /api/runs — Save Run
# =============================================================================

def test_save_run_returns_201(client: TestClient) -> None:
    res = client.post("/api/runs?as_of=2024-05-07", json=_VALID_BODY)
    assert res.status_code == 201
    body = res.json()
    assert body["result_hash"].startswith("sha256:")
    assert body["user_id"] == "00000000-0000-0000-0000-000000000001"  # SYSTEM_USER_ID


def test_save_run_result_hash_determinism(client: TestClient) -> None:
    """oracle Risk-X3 — 같은 query + as_of → 같은 result_hash."""
    res1 = client.post("/api/runs?as_of=2024-05-07", json=_VALID_BODY)
    res2 = client.post("/api/runs?as_of=2024-05-07", json=_VALID_BODY)
    assert res1.status_code == 201
    assert res2.status_code == 201
    # 두 Run id 는 다르나 hash 는 같음.
    assert res1.json()["id"] != res2.json()["id"]
    assert res1.json()["result_hash"] == res2.json()["result_hash"]


def test_save_run_different_as_of_different_hash(client: TestClient) -> None:
    res1 = client.post("/api/runs?as_of=2024-05-07", json=_VALID_BODY)
    res2 = client.post("/api/runs?as_of=2024-05-08", json=_VALID_BODY)
    assert res1.json()["result_hash"] != res2.json()["result_hash"]


def test_save_run_preserves_conditions(client: TestClient) -> None:
    res = client.post("/api/runs?as_of=2024-05-07", json=_VALID_BODY)
    body = res.json()
    assert len(body["conditions"]) == 1
    assert body["conditions"][0]["factor"] == "per:ttm-consolidated-ifrs"
    assert body["conditions"][0]["op"] == "<"


# =============================================================================
# 4. GET /api/runs (list)
# =============================================================================

def test_list_runs_returns_empty_for_new_user(client: TestClient) -> None:
    res = client.get("/api/runs")
    assert res.status_code == 200
    assert res.json()["items"] == []


def test_list_runs_after_save(client: TestClient) -> None:
    client.post("/api/runs?as_of=2024-05-07", json=_VALID_BODY)
    client.post("/api/runs?as_of=2024-05-08", json=_VALID_BODY)
    res = client.get("/api/runs")
    body = res.json()
    assert body["total"] == 2


def test_list_runs_respects_limit(client: TestClient) -> None:
    for d in (1, 2, 3, 6, 7, 8, 9, 10):
        client.post(f"/api/runs?as_of=2024-05-0{d}", json=_VALID_BODY)
    res = client.get("/api/runs?limit=3")
    assert res.status_code == 200
    assert len(res.json()["items"]) == 3


def test_list_runs_idor_protection(client: TestClient, monkeypatch) -> None:
    """oracle 결정 5 — CurrentUserDep 만, query param X.

    다른 SPECULUM_USER_ID 로 fetch 시 새 user 의 빈 list 만 보임.
    """
    # User A 의 Run 저장.
    client.post("/api/runs?as_of=2024-05-07", json=_VALID_BODY)
    assert len(client.get("/api/runs").json()["items"]) == 1

    # SPECULUM_USER_ID 변경 — User B 의 fetch.
    monkeypatch.setenv("SPECULUM_USER_ID", "00000000-0000-0000-0000-000000000099")
    res = client.get("/api/runs")
    # User B 의 runs — 빈 list (A 의 데이터 미노출).
    assert res.json()["items"] == []


# =============================================================================
# 5. GET /api/runs/{id}
# =============================================================================

def test_get_run_by_id(client: TestClient) -> None:
    res = client.post("/api/runs?as_of=2024-05-07", json=_VALID_BODY)
    run_id = res.json()["id"]
    res2 = client.get(f"/api/runs/{run_id}")
    assert res2.status_code == 200
    assert res2.json()["id"] == run_id


def test_get_run_unknown_id_returns_404(client: TestClient) -> None:
    res = client.get("/api/runs/99999999-9999-9999-9999-999999999999")
    assert res.status_code == 404


def test_get_run_other_user_returns_404(client: TestClient, monkeypatch) -> None:
    """다른 user 의 Run 조회 시 404 (IDOR 차단)."""
    res = client.post("/api/runs?as_of=2024-05-07", json=_VALID_BODY)
    run_id = res.json()["id"]
    monkeypatch.setenv("SPECULUM_USER_ID", "00000000-0000-0000-0000-000000000099")
    res2 = client.get(f"/api/runs/{run_id}")
    assert res2.status_code == 404


# =============================================================================
# 6. GET /api/runs/{id}/diff
# =============================================================================

def test_get_run_diff_returns_empty_when_no_change(client: TestClient) -> None:
    """방금 저장한 Run vs 현재 active = 같은 정책 → 빈 diff."""
    res = client.post("/api/runs?as_of=2024-05-07", json=_VALID_BODY)
    run_id = res.json()["id"]
    res2 = client.get(f"/api/runs/{run_id}/diff")
    assert res2.status_code == 200
    assert res2.json()["diff"] == {}


def test_get_run_diff_unknown_id_returns_404(client: TestClient) -> None:
    res = client.get("/api/runs/99999999-9999-9999-9999-999999999999/diff")
    assert res.status_code == 404


# =============================================================================
# 7. as_of integration
# =============================================================================

def test_screen_future_as_of_returns_400(client: TestClient) -> None:
    res = client.post("/api/screen?as_of=2999-12-31", json=_VALID_BODY)
    assert res.status_code == 400
    assert res.json()["code"] == "AS_OF_IN_FUTURE"


def test_save_run_future_as_of_returns_400(client: TestClient) -> None:
    res = client.post("/api/runs?as_of=2999-12-31", json=_VALID_BODY)
    assert res.status_code == 400


def test_screen_uses_repository_list_active_protocol(client: TestClient) -> None:
    """oracle 2 차 C1 — Repository.list_active Protocol 호출 (Fake 의 _records
    직접 접근 X).

    M1 조건 매칭 — fact 미주입 fixture 라 결과는 빈 list 이나, list_active 가
    universe 진입점으로 호출됨 (200 정상 응답). 조건 통과 종목의 실제 매칭은
    test_screen_conditions.py 의 fact-backed 종목으로 검증.
    """
    res = client.post("/api/screen?as_of=2024-05-07", json=_VALID_BODY)
    assert res.status_code == 200
    assert res.json()["result_codes"] == []


def test_repo_list_active_directly() -> None:
    """oracle 2 차 C1 — FakeStocksMasterRepository.list_active 단위 테스트."""
    repo = FakeStocksMasterRepository(records=_FIXTURE)
    active = repo.list_active(as_of=date(2024, 5, 7))
    assert len(active) == 2
    # current_code asc 정렬.
    assert active[0].current_code == "000660"
    assert active[1].current_code == "005930"


def test_repo_list_active_excludes_not_yet_listed() -> None:
    """as_of < listing_date 종목 제외."""
    future = StockMasterRecord(
        id=UUID("00000000-0000-0000-0000-000000000003"),
        current_code="888888", current_name="미상장", market="KOSDAQ",
        listing_date=date(2024, 12, 1), delisting_date=None, fiscal_month=12,
        code_history=(CodeHistoryEntry("888888", date(2024, 12, 1), None,
                                         "initial_listing"),),
    )
    repo = FakeStocksMasterRepository(records=_FIXTURE + [future])
    active = repo.list_active(as_of=date(2024, 5, 7))
    codes = [r.current_code for r in active]
    assert "888888" not in codes


def test_screen_dirty_query_no_echo_in_validation_error(client: TestClient) -> None:
    """ADR-0007 — 422 sanitize handler 가 query / body echo 차단."""
    body = {
        "conditions": [{"factor": "추천", "op": "<", "value": "10"}],
        "selected_factors": ["per:ttm-consolidated-ifrs"],
    }
    res = client.post("/api/screen?as_of=2024-05-07", json=body)
    assert res.status_code == 422
    body_str = str(res.json())
    # factor value "추천" 가 응답에 echo 되지 X.
    assert "추천" not in body_str
