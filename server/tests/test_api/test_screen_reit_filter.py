"""POST /api/screen + /api/runs 의 리츠(REIT) 자산군 필터 + 일반 재무 factor 값 보존.

M2 T79 리츠 트랙 backend — **production 코드 delta 0 의 회귀 가드**.

T78(우선주)이 security_type partition(D5)·screener 필터(D7)·소표본 디스클로저를
security_type-generic 으로 구현했고 `reit` 가 이미 enum/validator(stocks_master.py
`_VALID_SECURITY_TYPES`)에 포함되므로, 리츠는 **이미 자동 동작**한다. 본 파일은 그
자동 동작이 (1) 기본 동작 불변(default common → 리츠 제외)과 (2) ADR-0023 D8 결정
을 깨지 않음을 회귀로 박는다.

**ADR-0023 D8 결정 (본 사이클 확정) — 리츠 일반 재무 factor = 값 + Fidelity 라벨**:

  리츠 종목에 일반 PER/EPS 등 발행사-귀속 factor 를 "N/A 강제" 하지 않는다.

  근거: 리츠는 DART 법인이 존재하고 순이익 fact 가 *실재*하므로 PER/EPS 가 **값을
  산출**한다(N/A 아님). 이는 우선주(D3-b)와 결정적으로 다르다 —
  - 우선주 PER = 우선주가격 ÷ 보통주 EPS → 분자/분모 모집단 *불일치*(정의 불명)
    → N/A 가 정직(`test_screen_security_types.py`).
  - 리츠 PER = 리츠 순이익 기반 → 분자/분모 *일관*(정의 명확). "부동산 감가상각으로
    왜곡, 업계는 FFO 선호"는 정의 불명이 아니라 **해석 한계**다.
  해석 한계를 사실 은폐(N/A 강제)로 다루면 §2.3 Active Inspection("리츠 PER 은 보면
  안 됨"을 시스템이 정함 = 의제 설정) + §2.1 Fidelity + ADR-0024 디스클로저 정신을
  삼중으로 위반한다. 해석 한계는 ADR-0024 가 확립한 패턴대로 **사실 + 소표본
  디스클로저**(리츠 모집단은 작아 percentile 류에서 small_sample 자동 부착)로 다룬다.
  FFO 기반 지표(`ffo-multiple:reit`)는 데이터 합류(R4) 시 *추가* canonical_id 로
  PER 과 병존(ADR-0002 multi-id) — PER 을 지우는 게 아니라 FFO 를 더한다.

  → 코드 무변경이 정답. N/A 강제 분기를 신설하지 않는다. 아래 테스트가 "리츠 일반
  재무 factor 가 값을 산출(N/A 아님)" 을 회귀로 고정해, 추후 N/A 강제 분기 유입을 차단.

end-to-end factor: `eps:basic-ttm-consolidated-ifrs` — 우선주 트랙과 동일 factor 를
써서 우선주(N/A) vs 리츠(값) 대비가 같은 측정축에서 드러나게 한다. 리츠는 *자체*
코드(법인)로 EPS fact 를 적재하므로 값을 낸다.
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

# 리츠 종목 코드 — 6 자리 숫자(_stock 의 id=UUID(int=int(code)) 제약). 롯데리츠
# 실코드(330590)를 fixture 값으로 차용(외부 사실, EXTERNAL_QUOTE scope).
_REIT_CODE = "330590"
_COMMON_CODE = "005930"


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
    """보통주 005930 (EPS=4000) + 리츠 330590 (EPS=2000, *자체* 법인 재무).

    우선주(`test_screen_security_types.py`)와 달리 리츠는 DART 법인이 직접 재무를
    공시하므로 리츠 코드 330590 자체에 EPS fact 가 적재된다 → EPS 값 산출(N/A 아님).
    이것이 ADR-0023 D8 "리츠 일반 재무 factor = 값" 의 데이터 전제다.
    """
    stocks = FakeStocksMasterRepository(records=[
        _stock(_COMMON_CODE, "삼성전자", security_type="common"),
        _stock(_REIT_CODE, "롯데리츠", security_type="reit"),
    ])
    # 보통주와 리츠 *모두* 자체 코드로 4 분기 EPS 적재 — 리츠는 법인 자체 공시.
    financials = FakeFinancialRepository(
        records=[
            *_eps_quarters(_COMMON_CODE, ["1000", "1000", "1000", "1000"]),
            *_eps_quarters(_REIT_CODE, ["500", "500", "500", "500"]),
        ],
    )
    app = create_app(
        stocks_repository=stocks,
        financial_repository=financials,
    )
    with TestClient(app) as c:
        yield c


def _body(value: str, op: str = "<",
          security_types: list[str] | None = None) -> dict:
    body: dict = {
        "conditions": [{"factor": _EPS_FACTOR_ID, "op": op, "value": value}],
        "selected_factors": [_EPS_FACTOR_ID],
    }
    if security_types is not None:
        body["security_types"] = security_types
    return body


# =============================================================================
# D7 — security_types 사전 필터: 리츠는 default(common) 에서 제외 (기본 동작 불변)
# =============================================================================

def test_default_security_types_excludes_reit(client: TestClient) -> None:
    """security_types 미지정 → default ["common"] → 리츠 330590 제외 (회귀 가드).

    universe 에 리츠가 합류해도 기본 동작은 보통주만 — 기존 run 영향 0 (D4/D7).
    """
    res = client.post(f"/api/screen?as_of={_AS_OF}", json=_body("999999"))
    assert res.status_code == 200
    assert res.json()["result_codes"] == [_COMMON_CODE]


def test_explicit_common_only_excludes_reit(client: TestClient) -> None:
    """security_types=["common"] 명시 → 리츠 제외 (default 와 동일)."""
    res = client.post(
        f"/api/screen?as_of={_AS_OF}", json=_body("999999", security_types=["common"]),
    )
    assert res.status_code == 200
    assert res.json()["result_codes"] == [_COMMON_CODE]


def test_reit_selected_includes_in_population(client: TestClient) -> None:
    """security_types=["reit"] → 리츠만 모집단. EPS < 999999 (항상 참) → 리츠 통과.

    리츠가 자산군 사전 필터로 명시 선택되면 모집단에 포함됨 (D7).
    """
    res = client.post(
        f"/api/screen?as_of={_AS_OF}", json=_body("999999", security_types=["reit"]),
    )
    assert res.status_code == 200
    assert res.json()["result_codes"] == [_REIT_CODE]


def test_common_and_reit_both_selected(client: TestClient) -> None:
    """security_types=["common","reit"] → 두 자산군 모두 모집단 (각자 자체 EPS 보유)."""
    res = client.post(
        f"/api/screen?as_of={_AS_OF}",
        json=_body("999999", security_types=["common", "reit"]),
    )
    assert res.status_code == 200
    assert sorted(res.json()["result_codes"]) == [_COMMON_CODE, _REIT_CODE]


# =============================================================================
# ADR-0023 D8 — 리츠 일반 재무 factor = 값 (N/A 강제 안 함). 핵심 회귀 가드.
# =============================================================================

def test_reit_general_financial_factor_yields_value_not_na(
    client: TestClient,
) -> None:
    """리츠 종목의 일반 재무 factor(EPS)는 **값을 산출**한다 — N/A 강제 안 함 (D8).

    security_types=["reit"], EPS > 0 조건. 리츠 330590 의 EPS=2000(자체 법인 재무)
    가 N/A 로 강제됐다면 빈 결과였을 것. 값이 나오므로 EPS>0 통과 → 리츠 포함.

    우선주(`test_screen_security_types.test_preferred_only_no_match_when_factor_na`)는
    같은 EPS>0 조건에서 자체 재무 *부재*로 빈 결과였다. 리츠는 자체 재무가 *실재*
    하므로 값 — 이 대비가 D3-b(우선주 N/A) vs D8(리츠 값+라벨) 결정의 코드 증거다.
    추후 누군가 security_type 기반 factor N/A 강제 분기를 넣으면 이 테스트가 깨진다.
    """
    res = client.post(
        f"/api/screen?as_of={_AS_OF}",
        json=_body("0", op=">", security_types=["reit"]),
    )
    assert res.status_code == 200
    # 리츠 EPS=2000 > 0 → 통과 (N/A 강제됐다면 빈 결과였을 것).
    assert res.json()["result_codes"] == [_REIT_CODE]


def test_reit_eps_value_via_field_provider() -> None:
    """리츠 코드로 DbFieldProvider EPS 평가 → **실값**(N/A 아님). 단위 격리 (D8).

    우선주(`test_preferred_eps_na_via_field_provider`)는 자체 재무 부재로 N/A 였다.
    리츠는 자체 법인 재무가 있어 같은 경로에서 값을 낸다 — provider 에 자산군 특수
    N/A 분기가 없음을 직접 검증.
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

    # 리츠 330590 자체에 4 분기 EPS 적재 (TTM 합 = 2000).
    fin = FakeFinancialRepository(
        records=_eps_quarters(_REIT_CODE, ["500"] * 4),
    )
    provider = DbFieldProvider(
        code=_REIT_CODE,  # 리츠 코드.
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
    # 리츠 코드 → 자체 재무 실재 → EPS 값 산출 (N/A 강제 0, D8).
    assert not result.is_na
    assert result.na_reason is None
    assert result.value == Decimal("2000")  # TTM = 500×4.


# =============================================================================
# D7 — result_hash freeze (reit 선택이 hash 입력)
# =============================================================================

def _save_run(client: TestClient, security_types: list[str] | None) -> dict:
    res = client.post(
        f"/api/runs?as_of={_AS_OF}",
        json=_body("999999", security_types=security_types),
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_result_hash_depends_on_reit_selection(client: TestClient) -> None:
    """reit 포함 여부가 result_hash 를 바꾼다 (자산군 선택이 hash 입력, D7)."""
    common_run = _save_run(client, ["common"])
    with_reit_run = _save_run(client, ["common", "reit"])
    assert common_run["result_hash"] != with_reit_run["result_hash"]


def test_result_hash_same_for_same_reit_selection(client: TestClient) -> None:
    """같은 reit 선택 → byte 동일 result_hash (재현 freeze)."""
    run_a = _save_run(client, ["reit"])
    run_b = _save_run(client, ["reit"])
    assert run_a["result_hash"] == run_b["result_hash"]


def test_reit_security_types_order_irrelevant_to_hash(client: TestClient) -> None:
    """security_types 순서 무관 — canonical 정렬 → 같은 hash."""
    run_cr = _save_run(client, ["common", "reit"])
    run_rc = _save_run(client, ["reit", "common"])
    assert run_cr["result_hash"] == run_rc["result_hash"]


def test_saved_run_exposes_canonical_reit_security_types(client: TestClient) -> None:
    """저장된 run 응답에 canonical 정렬된 security_types 노출."""
    run = _save_run(client, ["reit", "common"])
    assert run["security_types"] == ["common", "reit"]


# =============================================================================
# 미지원 값 방어 (회귀)
# =============================================================================

def test_unsupported_security_type_still_422(client: TestClient) -> None:
    """리츠 도입 후에도 미지원 값("bond")은 422 (validate_security_type 회귀)."""
    res = client.post(
        f"/api/screen?as_of={_AS_OF}", json=_body("999999", security_types=["bond"]),
    )
    assert res.status_code == 422
