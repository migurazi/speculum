"""POST /api/screen + /api/runs 의 자산군 사전 필터 (ADR-0023 D7) + 우선주 N/A (D3-b).

M2 T78 우선주 트랙 backend:

1. **D7 — security_types 사전 필터** — screen endpoint 의 `security_types` 가 active
   universe 를 자산군으로 사전 필터. default `["common"]` (보통주만, universe 확장이
   기본 동작 불변). 미지원 값 → 422.
2. **D7 — result_hash freeze** — 선택된 security_types 가 ScreenRunQuery 일부 →
   result_hash 입력. 다른 선택 → 다른 hash, 같은 선택 → byte 동일.
3. **D3-b — 발행사-귀속 factor N/A** — 우선주 종목은 자체 재무 fact 가 없어 EPS
   (발행사-귀속) 가 missing_input 으로 자연 N/A. 보통주 EPS 차용 0.

end-to-end factor 선택: `eps:basic-ttm-consolidated-ifrs` (단일 field 4 분기 strict
합) — 보통주에만 재무를 주입하면 우선주는 자연 N/A 가 명확히 드러남.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.fakes import FakeFinancialRepository
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    FinancialRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository

_EPS_ACCOUNT = "basic_eps"
_EPS_FACTOR_ID = "eps:basic-ttm-consolidated-ifrs"
_CONSOLIDATED = "consolidated"
_CITATION = UUID("00000000-0000-0000-0000-0000000000ee")
_AS_OF = "2024-05-07"


# =============================================================================
# Builders
# =============================================================================

def _eps_quarters(code: str, values: Sequence[str]) -> list[FinancialRecord]:
    """code 의 4 분기 basic_eps consolidated 재무 — TTM 합 = sum(values)."""
    periods = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    eff = [date(2023, 5, 15), date(2023, 8, 14), date(2023, 11, 14),
           date(2024, 3, 30)]
    out: list[FinancialRecord] = []
    for fp, v, e in zip(periods, values, eff, strict=True):
        out.append(FinancialRecord(
            id=uuid4(),
            code=code,
            code_lineage_id=UUID(int=hash(code) & ((1 << 128) - 1)),
            effective_date=e,
            fiscal_period=fp,
            account=_EPS_ACCOUNT,
            value=Decimal(v),
            unit="krw",
            ifrs_type=_CONSOLIDATED,
            citation_id=_CITATION,
            superseded_by=None,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        ))
    return out


def _stock(code: str, name: str, *, security_type: str = "common") -> StockMasterRecord:
    return StockMasterRecord(
        id=UUID(int=int(code)),
        current_code=code, current_name=name, market="KOSPI",
        listing_date=date(2000, 1, 1), delisting_date=None, fiscal_month=12,
        code_history=(CodeHistoryEntry(code, date(2000, 1, 1), None,
                                       "initial_listing"),),
        security_type=security_type,
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    """보통주 005930 (EPS=4000) + 우선주 005935 (재무 없음 → 자연 N/A).

    005935 은 005930 (삼성전자) 의 우선주 — 발행사 재무는 005930 코드에만 적재.
    우선주 코드 005935 로는 EPS fact 가 없어 (보통주 차용 안 함) 자연 N/A (D3-b).
    """
    stocks = FakeStocksMasterRepository(records=[
        _stock("005930", "삼성전자", security_type="common"),
        _stock("005935", "삼성전자우", security_type="preferred"),
    ])
    # 보통주 005930 만 4 분기 EPS — 우선주 005935 코드로는 재무 fact 부재.
    financials = FakeFinancialRepository(
        records=_eps_quarters("005930", ["1000", "1000", "1000", "1000"]),
    )
    app = create_app(
        stocks_repository=stocks,
        financial_repository=financials,
    )
    with TestClient(app) as c:
        yield c


def _body(value: str, security_types: list[str] | None = None) -> dict:
    body: dict = {
        "conditions": [{"factor": _EPS_FACTOR_ID, "op": "<", "value": value}],
        "selected_factors": [_EPS_FACTOR_ID],
    }
    if security_types is not None:
        body["security_types"] = security_types
    return body


# =============================================================================
# D7 — security_types 사전 필터 (기본 common)
# =============================================================================

def test_default_security_types_excludes_preferred(client: TestClient) -> None:
    """security_types 미지정 → default ["common"] → 우선주 005935 제외.

    EPS < 999999 (사실상 항상 참) — 보통주 005930 (EPS=4000) 통과. 우선주는
    자산군 필터로 사전 제외 (default common). result_codes 에 005935 없음.
    """
    res = client.post(f"/api/screen?as_of={_AS_OF}", json=_body("999999"))
    assert res.status_code == 200
    assert res.json()["result_codes"] == ["005930"]


def test_explicit_common_only_excludes_preferred(client: TestClient) -> None:
    """security_types=["common"] 명시 → 우선주 제외 (default 와 동일)."""
    res = client.post(
        f"/api/screen?as_of={_AS_OF}", json=_body("999999", ["common"]),
    )
    assert res.status_code == 200
    assert res.json()["result_codes"] == ["005930"]


def test_preferred_selected_but_na_still_excluded(client: TestClient) -> None:
    """security_types=["common","preferred"] → 우선주 모집단 포함되나 EPS 조건
    에서 N/A (자체 재무 부재, D3-b) 로 탈락. 보통주만 통과.

    모집단 사전 필터 (포함) 와 factor N/A 탈락은 별개 — 우선주가 모집단에
    들어와도 EPS 가 N/A 라 조건 불충족 (Fidelity §2.1).
    """
    res = client.post(
        f"/api/screen?as_of={_AS_OF}",
        json=_body("999999", ["common", "preferred"]),
    )
    assert res.status_code == 200
    # 우선주 005935 는 모집단 포함되나 EPS N/A → 조건 탈락. 005930 만.
    assert res.json()["result_codes"] == ["005930"]


def test_preferred_only_no_match_when_factor_na(client: TestClient) -> None:
    """security_types=["preferred"] → 우선주만 모집단. EPS N/A 라 전부 탈락 (빈)."""
    res = client.post(
        f"/api/screen?as_of={_AS_OF}", json=_body("999999", ["preferred"]),
    )
    assert res.status_code == 200
    # 우선주만 모집단 — EPS 조건이 N/A 로 탈락 → 빈 결과.
    assert res.json()["result_codes"] == []


def test_unsupported_security_type_returns_422(client: TestClient) -> None:
    """미지원 security_type ("bond") → 422 (validate_security_type)."""
    res = client.post(
        f"/api/screen?as_of={_AS_OF}", json=_body("999999", ["bond"]),
    )
    assert res.status_code == 422


def test_empty_security_types_returns_422(client: TestClient) -> None:
    """빈 security_types list → 422 (최소 1 자산군 명시 강제)."""
    res = client.post(
        f"/api/screen?as_of={_AS_OF}", json=_body("999999", []),
    )
    assert res.status_code == 422


# =============================================================================
# D7 — result_hash freeze (security_types 가 hash 입력)
# =============================================================================

def _save_run(client: TestClient, security_types: list[str] | None) -> dict:
    res = client.post(
        f"/api/runs?as_of={_AS_OF}", json=_body("999999", security_types),
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_result_hash_depends_on_security_types(client: TestClient) -> None:
    """다른 security_types 선택 → 다른 result_hash (자산군이 hash 입력, D7)."""
    common_run = _save_run(client, ["common"])
    both_run = _save_run(client, ["common", "preferred"])
    assert common_run["result_hash"] != both_run["result_hash"]


def test_result_hash_same_for_same_security_types(client: TestClient) -> None:
    """같은 security_types 선택 → byte 동일 result_hash (재현 freeze)."""
    run_a = _save_run(client, ["common"])
    run_b = _save_run(client, ["common"])
    assert run_a["result_hash"] == run_b["result_hash"]


def test_default_equals_explicit_common_hash(client: TestClient) -> None:
    """security_types 미지정 (default) 과 ["common"] 명시는 같은 hash (canonical
    default = ("common",) — 기본 동작 = 보통주, 회귀 무영향)."""
    default_run = _save_run(client, None)
    explicit_run = _save_run(client, ["common"])
    assert default_run["result_hash"] == explicit_run["result_hash"]


def test_security_types_order_irrelevant_to_hash(client: TestClient) -> None:
    """security_types 순서 무관 — canonical 정렬 → 같은 hash."""
    run_ab = _save_run(client, ["common", "preferred"])
    run_ba = _save_run(client, ["preferred", "common"])
    assert run_ab["result_hash"] == run_ba["result_hash"]


def test_saved_run_exposes_security_types(client: TestClient) -> None:
    """저장된 run 의 응답에 canonical security_types 노출."""
    run = _save_run(client, ["preferred", "common"])
    assert run["security_types"] == ["common", "preferred"]
    default_run = _save_run(client, None)
    assert default_run["security_types"] == ["common"]


# =============================================================================
# D3-b — 발행사-귀속 factor 우선주 N/A (보통주 EPS 차용 0)
# =============================================================================

def test_preferred_eps_is_na_not_borrowed(client: TestClient) -> None:
    """우선주 005935 의 EPS 는 N/A — 보통주 005930 의 EPS(4000) 차용 안 함 (D3-b).

    우선주만 모집단 (security_types=["preferred"]) 으로 EPS > 0 조건 → 우선주 EPS
    가 보통주에서 차용됐다면 4000>0 으로 통과했을 것. N/A 라 탈락 (빈 결과) =
    차용 안 함 증명.
    """
    body = {
        "conditions": [{"factor": _EPS_FACTOR_ID, "op": ">", "value": "0"}],
        "selected_factors": [_EPS_FACTOR_ID],
        "security_types": ["preferred"],
    }
    res = client.post(f"/api/screen?as_of={_AS_OF}", json=body)
    assert res.status_code == 200
    # 우선주 EPS N/A → EPS>0 불충족 → 빈 결과 (보통주 4000 차용 시 통과했을 것).
    assert res.json()["result_codes"] == []


def test_preferred_eps_na_via_field_provider() -> None:
    """우선주 코드로 DbFieldProvider EPS 평가 → N/A (missing_input). 단위 격리.

    우선주 005935 코드로는 financial fact 가 없으므로 EPS factor 가 missing_input
    으로 자연 N/A. provider 가 발행사(005930) 재무를 끌어오지 않음을 직접 검증.
    """
    from app.repositories.fakes import (
        FakeCorporateActionRepository,
        FakeMarketCapRepository,
        FakePriceRepository,
        FakeTreasurySharesRepository,
    )
    from app.services.db_field_provider import DbFieldProvider
    from app.services.factor_evaluator import FactorEvaluator
    from app.services.factor_pack import DEFAULT_PACK

    # 보통주 005930 만 재무 적재 — 우선주 005935 는 fact 부재.
    fin = FakeFinancialRepository(
        records=_eps_quarters("005930", ["1000"] * 4),
    )
    provider = DbFieldProvider(
        code="005935",  # 우선주 코드.
        as_of=date(2024, 5, 7),
        price_repo=FakePriceRepository(records=()),
        financial_repo=fin,
        corporate_action_repo=FakeCorporateActionRepository(records=()),
        market_cap_repo=FakeMarketCapRepository(records=()),
        treasury_repo=FakeTreasurySharesRepository(records=()),
        factor_pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
    )
    factor = next(
        f for f in DEFAULT_PACK.body["factors"]
        if f["canonical_id"] == _EPS_FACTOR_ID
    )
    result = FactorEvaluator().evaluate(
        factor, provider, as_of=date(2024, 5, 7),
    )
    # 우선주 코드 → 자체 재무 부재 → EPS 자연 N/A (보통주 차용 0).
    # EPS = sum_last_n_quarters(4) strict 합이라 series 결손은 insufficient_series
    # N/A (missing_input 의 quarterly 변형) — 어느 쪽이든 "값 없음" 이지 보통주
    # 4000 차용이 아님 (D3-b). 핵심: 발행사 재무를 우선주 코드로 끌어오지 않음.
    assert result.is_na
    assert result.value is None
    assert result.na_reason is not None
    assert result.na_reason.startswith(
        ("missing_input:", "insufficient_series:"),
    )
