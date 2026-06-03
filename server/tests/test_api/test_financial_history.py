"""정정공시 history view — GET /api/stocks/{code}/financials/history 테스트.

테스트 매트릭스:
1. 정정 chain history: 원본(D1) + 정정본(D2, 원본 superseded) → 2 vintage
   effective_date 순 반환, 원본 is_active=False / 정정본 is_active=True.
2. 다중 정정: 2회 정정(D1→D2→D3) → 3 vintage chain 순서 + vintage_seq 1/2/3.
3. PIT look-ahead 차단: as_of=D1 → 정정본(D2) 미포함(D1 vintage 만).
   as_of=D2 → 둘 다 포함.
4. fiscal_period 필터: 특정 분기만 반환.
5. 미존재 code → 빈 vintages(200).
6. endpoint 응답 구조/필드 검증 — code / as_of / fiscal_period_filter / vintages.
7. read-only 보장: history 호출이 DB 상태(Fake store) 불변 — save 도 supersede 도 없음.
8. 복수 account, 복수 fiscal_period 혼재 시 올바른 vintage_seq(account 별 독립).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.fakes import FakeFinancialRepository
from app.repositories.pit_protocols import FinancialRecord

# =============================================================================
# 공통 픽스처 상수
# =============================================================================

_DUMMY_CITATION_ID: UUID = UUID("00000000-0000-0000-0000-00000000cccc")
_DUMMY_LINEAGE_ID: UUID = UUID("00000000-0000-0000-0000-000000001111")
_CODE = "005930"


def _fin(
    *,
    fiscal_period: str,
    account: str = "revenue",
    code: str = _CODE,
    value: float = 1000.0,
    effective_date: date,
    superseded_by: UUID | None = None,
    record_id: UUID | None = None,
) -> FinancialRecord:
    """테스트용 FinancialRecord builder."""
    return FinancialRecord(
        id=record_id or uuid4(),
        code=code,
        code_lineage_id=_DUMMY_LINEAGE_ID,
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        account=account,
        value=Decimal(str(value)),
        unit="krw",
        ifrs_type="consolidated",
        citation_id=_DUMMY_CITATION_ID,
        superseded_by=superseded_by,
        created_at=datetime(
            effective_date.year, effective_date.month, effective_date.day,
            tzinfo=UTC,
        ),
        effective_date_precise=True,
    )


# =============================================================================
# 1. 정정 chain history — 원본 + 1회 정정 (2 vintage)
# =============================================================================

def test_restatement_history_two_vintages() -> None:
    """원본(D1) + 정정본(D2, 원본 superseded) → 2 vintage, effective_date 순 반환.

    원본: is_active=False (superseded_by = 정정본 id).
    정정본: is_active=True (superseded_by None).
    """
    d1 = date(2024, 5, 15)
    d2 = date(2024, 6, 10)
    original_id = uuid4()
    successor_id = uuid4()

    original = _fin(
        fiscal_period="2024Q1", effective_date=d1,
        superseded_by=successor_id, record_id=original_id,
        value=900.0,
    )
    corrected = _fin(
        fiscal_period="2024Q1", effective_date=d2,
        superseded_by=None, record_id=successor_id,
        value=950.0,
    )
    repo = FakeFinancialRepository(records=[original, corrected])
    app = create_app(financial_repository=repo)
    with TestClient(app) as client:
        res = client.get(f"/api/stocks/{_CODE}/financials/history")

    assert res.status_code == 200
    body = res.json()
    assert body["code"] == _CODE
    vintages = body["vintages"]
    assert len(vintages) == 2
    # effective_date 오름차순 — 원본이 먼저.
    assert vintages[0]["effective_date"] == d1.isoformat()
    assert vintages[0]["is_active"] is False
    assert str(vintages[0]["superseded_by"]) == str(successor_id)
    assert vintages[0]["vintage_seq"] == 1

    assert vintages[1]["effective_date"] == d2.isoformat()
    assert vintages[1]["is_active"] is True
    assert vintages[1]["superseded_by"] is None
    assert vintages[1]["vintage_seq"] == 2


# =============================================================================
# 2. 다중 정정 — 2회 정정 (3 vintage chain, vintage_seq 1/2/3)
# =============================================================================

def test_restatement_history_triple_chain() -> None:
    """2회 정정(D1→D2→D3) → 3 vintage chain, vintage_seq 순서."""
    d1 = date(2024, 5, 15)
    d2 = date(2024, 6, 10)
    d3 = date(2024, 7, 5)
    id1, id2, id3 = uuid4(), uuid4(), uuid4()

    r1 = _fin(
        fiscal_period="2024Q1", effective_date=d1,
        superseded_by=id2, record_id=id1, value=800.0,
    )
    r2 = _fin(
        fiscal_period="2024Q1", effective_date=d2,
        superseded_by=id3, record_id=id2, value=850.0,
    )
    r3 = _fin(
        fiscal_period="2024Q1", effective_date=d3,
        superseded_by=None, record_id=id3, value=870.0,
    )
    repo = FakeFinancialRepository(records=[r1, r2, r3])
    app = create_app(financial_repository=repo)
    with TestClient(app) as client:
        res = client.get(f"/api/stocks/{_CODE}/financials/history")

    assert res.status_code == 200
    vintages = res.json()["vintages"]
    assert len(vintages) == 3
    # effective_date 오름차순 — D1, D2, D3.
    assert vintages[0]["vintage_seq"] == 1
    assert vintages[1]["vintage_seq"] == 2
    assert vintages[2]["vintage_seq"] == 3
    # 마지막만 is_active.
    assert vintages[0]["is_active"] is False
    assert vintages[1]["is_active"] is False
    assert vintages[2]["is_active"] is True
    # 값 확인 — Decimal(str(800.0)) = Decimal("800.0"), str() = "800.0".
    assert vintages[0]["value"] == "800.0"
    assert vintages[2]["value"] == "870.0"


# =============================================================================
# 3. PIT look-ahead 차단 — as_of 쿼리 파라미터
# =============================================================================

def test_pit_lookahead_blocked_by_as_of_d1() -> None:
    """as_of=D1 → D2 vintage(공시일 D2) 미포함 — look-ahead 0 보장."""
    d1 = date(2024, 5, 15)
    d2 = date(2024, 6, 10)
    original_id = uuid4()
    successor_id = uuid4()

    original = _fin(
        fiscal_period="2024Q1", effective_date=d1,
        superseded_by=successor_id, record_id=original_id,
    )
    corrected = _fin(
        fiscal_period="2024Q1", effective_date=d2,
        superseded_by=None, record_id=successor_id,
    )
    repo = FakeFinancialRepository(records=[original, corrected])
    app = create_app(financial_repository=repo)
    with TestClient(app) as client:
        # as_of=D1 — D2 vintage 는 아직 공시 안 됨.
        res = client.get(
            f"/api/stocks/{_CODE}/financials/history",
            params={"as_of": d1.isoformat()},
        )

    assert res.status_code == 200
    body = res.json()
    vintages = body["vintages"]
    # D1 vintage 만 반환.
    assert len(vintages) == 1
    assert vintages[0]["effective_date"] == d1.isoformat()
    assert body["as_of"] == d1.isoformat()


def test_pit_both_vintages_after_d2() -> None:
    """as_of=D2 → D1 + D2 vintage 둘 다 포함."""
    d1 = date(2024, 5, 15)
    d2 = date(2024, 6, 10)
    original_id = uuid4()
    successor_id = uuid4()

    original = _fin(
        fiscal_period="2024Q1", effective_date=d1,
        superseded_by=successor_id, record_id=original_id,
    )
    corrected = _fin(
        fiscal_period="2024Q1", effective_date=d2,
        superseded_by=None, record_id=successor_id,
    )
    repo = FakeFinancialRepository(records=[original, corrected])
    app = create_app(financial_repository=repo)
    with TestClient(app) as client:
        res = client.get(
            f"/api/stocks/{_CODE}/financials/history",
            params={"as_of": d2.isoformat()},
        )

    assert res.status_code == 200
    vintages = res.json()["vintages"]
    assert len(vintages) == 2


# =============================================================================
# 4. fiscal_period 필터
# =============================================================================

def test_fiscal_period_filter_returns_only_matching_period() -> None:
    """fiscal_period=2024Q1 필터 → 다른 분기 제외."""
    q1 = _fin(fiscal_period="2024Q1", effective_date=date(2024, 5, 15))
    q2 = _fin(fiscal_period="2024Q2", effective_date=date(2024, 8, 14))
    q3 = _fin(fiscal_period="2024Q3", effective_date=date(2024, 11, 14))

    repo = FakeFinancialRepository(records=[q1, q2, q3])
    app = create_app(financial_repository=repo)
    with TestClient(app) as client:
        res = client.get(
            f"/api/stocks/{_CODE}/financials/history",
            params={"fiscal_period": "2024Q1"},
        )

    assert res.status_code == 200
    body = res.json()
    vintages = body["vintages"]
    # 2024Q1 만.
    assert len(vintages) == 1
    assert vintages[0]["effective_date"] == "2024-05-15"
    assert body["fiscal_period_filter"] == "2024Q1"


def test_fiscal_period_filter_no_match_returns_empty() -> None:
    """존재하지 않는 분기 필터 → 빈 vintages."""
    r = _fin(fiscal_period="2024Q1", effective_date=date(2024, 5, 15))
    repo = FakeFinancialRepository(records=[r])
    app = create_app(financial_repository=repo)
    with TestClient(app) as client:
        res = client.get(
            f"/api/stocks/{_CODE}/financials/history",
            params={"fiscal_period": "2099Q4"},
        )

    assert res.status_code == 200
    assert res.json()["vintages"] == []


# =============================================================================
# 5. 미존재 code → 빈 vintages (200)
# =============================================================================

def test_unknown_code_returns_empty_vintages() -> None:
    """적재 데이터 없는 code → vintages=[] 로 200 반환."""
    repo = FakeFinancialRepository(records=[])
    app = create_app(financial_repository=repo)
    with TestClient(app) as client:
        res = client.get("/api/stocks/999999/financials/history")

    assert res.status_code == 200
    body = res.json()
    assert body["code"] == "999999"
    assert body["vintages"] == []


# =============================================================================
# 6. 응답 구조/필드 검증
# =============================================================================

def test_response_schema_fields_present() -> None:
    """응답 top-level 필드: code / as_of / fiscal_period_filter / vintages."""
    r = _fin(fiscal_period="2024Q1", effective_date=date(2024, 5, 15))
    repo = FakeFinancialRepository(records=[r])
    app = create_app(financial_repository=repo)
    with TestClient(app) as client:
        res = client.get(f"/api/stocks/{_CODE}/financials/history")

    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"code", "as_of", "fiscal_period_filter", "vintages"}
    assert body["as_of"] is None
    assert body["fiscal_period_filter"] is None


def test_vintage_schema_fields_present() -> None:
    """vintage 요소 필드: vintage_seq / effective_date / account / value /
    unit / ifrs_type / is_active / superseded_by."""
    r = _fin(fiscal_period="2024Q1", effective_date=date(2024, 5, 15), value=123.0)
    repo = FakeFinancialRepository(records=[r])
    app = create_app(financial_repository=repo)
    with TestClient(app) as client:
        res = client.get(f"/api/stocks/{_CODE}/financials/history")

    v = res.json()["vintages"][0]
    expected_keys = {
        "vintage_seq", "effective_date", "account", "value",
        "unit", "ifrs_type", "is_active", "superseded_by",
    }
    assert set(v.keys()) == expected_keys
    assert v["account"] == "revenue"
    assert v["unit"] == "krw"
    assert v["ifrs_type"] == "consolidated"
    assert v["is_active"] is True


# =============================================================================
# 7. read-only 보장 — history 호출이 DB 상태 불변
# =============================================================================

def test_read_only_history_does_not_mutate_store() -> None:
    """fetch_restatement_history 가 Fake store 를 변경하지 않음 — ADR-0020.

    history 호출 전후 fetch_financials 결과가 동일해야 함.
    """
    r = _fin(fiscal_period="2024Q1", effective_date=date(2024, 5, 15))
    repo = FakeFinancialRepository(records=[r])

    # history 호출 전 재무 조회 결과 스냅샷.
    before = repo.fetch_financials(
        _CODE, as_of=date(2025, 1, 1),
        account="revenue", ifrs_type="consolidated",
    )
    before_ids = {rec.id for rec in before}

    # history 호출 (read-only).
    _ = repo.fetch_restatement_history(_CODE)

    # 호출 후 동일 결과.
    after = repo.fetch_financials(
        _CODE, as_of=date(2025, 1, 1),
        account="revenue", ifrs_type="consolidated",
    )
    after_ids = {rec.id for rec in after}
    assert before_ids == after_ids


# =============================================================================
# 8. 복수 account — vintage_seq 가 account 별 독립 카운터
# =============================================================================

def test_vintage_seq_is_per_account_per_period() -> None:
    """같은 fiscal_period 의 서로 다른 account 는 vintage_seq 가 독립적으로 1부터.

    revenue 정정(D1→D2) + operating_income 정정(D1→D2) → 각각 seq 1/2.
    """
    d1 = date(2024, 5, 15)
    d2 = date(2024, 6, 10)
    rev_id1, rev_id2 = uuid4(), uuid4()
    oi_id1, oi_id2 = uuid4(), uuid4()

    records = [
        _fin(fiscal_period="2024Q1", account="revenue",
             effective_date=d1, superseded_by=rev_id2, record_id=rev_id1),
        _fin(fiscal_period="2024Q1", account="revenue",
             effective_date=d2, superseded_by=None, record_id=rev_id2),
        _fin(fiscal_period="2024Q1", account="operating_income",
             effective_date=d1, superseded_by=oi_id2, record_id=oi_id1),
        _fin(fiscal_period="2024Q1", account="operating_income",
             effective_date=d2, superseded_by=None, record_id=oi_id2),
    ]
    repo = FakeFinancialRepository(records=records)
    app = create_app(financial_repository=repo)
    with TestClient(app) as client:
        res = client.get(f"/api/stocks/{_CODE}/financials/history")

    assert res.status_code == 200
    vintages = res.json()["vintages"]
    # 4 vintage 총합.
    assert len(vintages) == 4
    # vintage_seq 는 (fiscal_period, account) 그룹별 독립 1-based.
    by_account: dict[str, list[int]] = {}
    for v in vintages:
        by_account.setdefault(v["account"], []).append(v["vintage_seq"])
    assert sorted(by_account["revenue"]) == [1, 2]
    assert sorted(by_account["operating_income"]) == [1, 2]


# =============================================================================
# 9. 복수 fiscal_period — 정렬 검증 (fiscal_period asc, effective_date asc)
# =============================================================================

def test_sort_order_multi_period() -> None:
    """복수 fiscal_period — (fiscal_period asc, effective_date asc) 정렬 확인."""
    q2 = _fin(fiscal_period="2024Q2", effective_date=date(2024, 8, 14))
    q1 = _fin(fiscal_period="2024Q1", effective_date=date(2024, 5, 15))
    q1_restatement = _fin(fiscal_period="2024Q1", effective_date=date(2024, 6, 1))

    repo = FakeFinancialRepository(records=[q2, q1, q1_restatement])
    app = create_app(financial_repository=repo)
    with TestClient(app) as client:
        res = client.get(f"/api/stocks/{_CODE}/financials/history")

    vintages = res.json()["vintages"]
    # 3 vintage: 2024Q1 (두 vintage) 먼저, 그 다음 2024Q2.
    assert len(vintages) == 3
    assert vintages[0]["effective_date"] == "2024-05-15"  # 2024Q1 원본
    assert vintages[1]["effective_date"] == "2024-06-01"  # 2024Q1 정정
    assert vintages[2]["effective_date"] == "2024-08-14"  # 2024Q2
