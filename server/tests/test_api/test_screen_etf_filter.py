"""POST /api/screen + /api/runs 의 ETF 자산군 필터 + 재무 factor 자연 N/A.

M2 T77 ETF 트랙 backend — **production 코드 delta 0 의 회귀 가드**.

T78(우선주)/T79(리츠)가 security_type partition(D5)·screener 필터(D7)·소표본
디스클로저를 security_type-generic 으로 구현했고 `etf` 가 이미 enum/validator
(stocks_master.py `_VALID_SECURITY_TYPES`)에 포함되므로, ETF 는 **이미 자동 동작**
한다. 본 파일은 그 자동 동작이 (1) 기본 동작 불변(default common → ETF 제외)과
(2) ADR-0023 D3-a 결정을 깨지 않음을 회귀로 박는다.

**ADR-0023 D3-a — ETF 는 universe 포함, 재무 factor 자연 N/A (추가 코드 0)**:

  ETF 는 재무제표가 *무의미*하다(펀드 — DART 법인 재무 fact 부재). 따라서 PER/EPS
  등 재무 factor 는 `missing_input` 으로 **자연 N/A**(N/A 인프라 재사용, 추가 코드 0).
  단 **universe 에서 빼지 않는다**(§2.3 Active Inspection — "ETF 는 보면 안 됨"을
  시스템이 정하면 의제 설정). ETF 는 자산군 사전 필터로 명시 선택 시 모집단에
  포함되며, 재무 factor 만 N/A 일 뿐이다. ETF 전용 factor(NAV/괴리율/AUM)는 별도
  canonical_id(`nav-premium:krx` 등)로 **데이터 합류(R4) 시** 추가 — 본 사이클 미포함
  (T78 우선주·T79 리츠 전용 factor 미포함과 동형).

**우선주(D3-b)와의 구분 — 같은 missing_input N/A, 다른 근거**:
  - 우선주: 발행사 재무는 *보통주 코드*에 있으나 우선주 코드로 **차용하지 않음**
    (분자/분모 모집단 불일치 회피). N/A 는 "정의 불명" 방어.
  - ETF: 재무제표 *자체가 무의미*. N/A 는 "해당 없음" — 차용할 발행사 재무도 없음.
  두 경우 모두 result.na_reason 이 missing_input 계열이지만 의미가 다르다.

end-to-end factor: `eps:basic-ttm-consolidated-ifrs` — 우선주/리츠 트랙과 동일 factor
로 자산군별 거동(우선주 N/A·리츠 값·ETF N/A)을 같은 측정축에서 대비한다. ETF 의
"시총은 값이나 분포는 partition 분리"(D5 "구멍은 N/A 아닌 field")는
`test_db_universe_distribution.py` §13 에서 별도 검증.
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

# ETF 종목 코드 — KODEX 200 실코드(069500)를 fixture 값으로 차용(외부 사실,
# EXTERNAL_QUOTE scope). _stock 의 id=UUID(int=int(code)) 제약상 6 자리 숫자.
_ETF_CODE = "069500"
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
    """보통주 005930 (EPS=4000) + ETF 069500 (재무 fact 부재 → 자연 N/A).

    ETF 069500 은 펀드라 DART 법인 재무제표가 무의미 — 코드 069500 으로는 EPS fact
    가 없어 재무 factor 가 missing_input 자연 N/A (D3-a, 추가 코드 0).
    """
    stocks = FakeStocksMasterRepository(records=[
        _stock(_COMMON_CODE, "삼성전자", security_type="common"),
        _stock(_ETF_CODE, "KODEX 200", security_type="etf"),
    ])
    # 보통주 005930 만 4 분기 EPS — ETF 069500 코드로는 재무 fact 부재.
    financials = FakeFinancialRepository(
        records=_eps_quarters(_COMMON_CODE, ["1000", "1000", "1000", "1000"]),
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
# D7 — security_types 사전 필터: ETF 는 default(common) 에서 제외 (기본 동작 불변)
# =============================================================================

def test_default_security_types_excludes_etf(client: TestClient) -> None:
    """security_types 미지정 → default ["common"] → ETF 069500 제외 (회귀 가드).

    universe 에 ETF 가 합류해도 기본 동작은 보통주만 — 기존 run 영향 0 (D4/D7).
    """
    res = client.post(f"/api/screen?as_of={_AS_OF}", json=_body("999999"))
    assert res.status_code == 200
    assert res.json()["result_codes"] == [_COMMON_CODE]


def test_explicit_common_only_excludes_etf(client: TestClient) -> None:
    """security_types=["common"] 명시 → ETF 제외 (default 와 동일)."""
    res = client.post(
        f"/api/screen?as_of={_AS_OF}", json=_body("999999", security_types=["common"]),
    )
    assert res.status_code == 200
    assert res.json()["result_codes"] == [_COMMON_CODE]


# =============================================================================
# D3-a — ETF universe 포함하되 재무 factor 자연 N/A (universe 제외 아님)
# =============================================================================

def test_etf_selected_financial_factor_natural_na(client: TestClient) -> None:
    """security_types=["etf"] → ETF 모집단 포함되나 재무 factor(EPS) 자연 N/A (D3-a).

    EPS < 999999 (사실상 항상 참) 이지만 ETF 069500 의 EPS 는 missing_input N/A
    (펀드 — 재무제표 무의미) → 조건 불충족 → 빈 결과. ETF 가 *모집단에는 포함*되되
    재무 factor 가 N/A 일 뿐 — universe 에서 제외(§2.3 위반)한 게 아니다. 모집단
    포함 + 시총 partition 의 직접 증거는 distribution §13.
    """
    res = client.post(
        f"/api/screen?as_of={_AS_OF}", json=_body("999999", security_types=["etf"]),
    )
    assert res.status_code == 200
    # ETF EPS N/A → EPS 조건 탈락 → 빈 결과 (재무 자연 N/A, D3-a).
    assert res.json()["result_codes"] == []


def test_common_and_etf_only_common_passes_financial(client: TestClient) -> None:
    """security_types=["common","etf"] → 두 자산군 모집단. EPS 조건은 보통주만 통과.

    ETF 는 모집단에 들어오나 재무 N/A 로 탈락, 보통주 005930 만 EPS 값(4000)으로
    통과. 모집단 포함(필터)과 factor N/A 탈락은 별개 (Fidelity §2.1).
    """
    res = client.post(
        f"/api/screen?as_of={_AS_OF}",
        json=_body("999999", security_types=["common", "etf"]),
    )
    assert res.status_code == 200
    assert res.json()["result_codes"] == [_COMMON_CODE]


def test_etf_eps_is_natural_na_via_field_provider() -> None:
    """ETF 코드로 DbFieldProvider EPS 평가 → N/A (missing_input). 단위 격리 (D3-a).

    ETF 069500 은 재무제표가 무의미해 자체 재무 fact 가 없으므로 EPS factor 가
    자연 N/A. provider 에 자산군 특수 분기가 없음(추가 코드 0) — 우선주와 동일
    경로지만 근거는 "재무 무의미"(차용할 발행사 재무조차 없음).
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

    # ETF 069500 은 재무 fact 부재 (펀드).
    fin = FakeFinancialRepository(records=())
    provider = DbFieldProvider(
        code=_ETF_CODE,  # ETF 코드.
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
    # ETF 코드 → 자체 재무 부재 → EPS 자연 N/A (추가 코드 0, D3-a).
    assert result.is_na
    assert result.value is None
    assert result.na_reason is not None
    assert result.na_reason.startswith(
        ("missing_input:", "insufficient_series:"),
    )


# =============================================================================
# D7 — result_hash freeze (etf 선택이 hash 입력)
# =============================================================================

def _save_run(client: TestClient, security_types: list[str] | None) -> dict:
    res = client.post(
        f"/api/runs?as_of={_AS_OF}",
        json=_body("999999", security_types=security_types),
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_result_hash_depends_on_etf_selection(client: TestClient) -> None:
    """etf 포함 여부가 result_hash 를 바꾼다 (자산군 선택이 hash 입력, D7)."""
    common_run = _save_run(client, ["common"])
    with_etf_run = _save_run(client, ["common", "etf"])
    assert common_run["result_hash"] != with_etf_run["result_hash"]


def test_result_hash_same_for_same_etf_selection(client: TestClient) -> None:
    """같은 etf 선택 → byte 동일 result_hash (재현 freeze)."""
    run_a = _save_run(client, ["etf"])
    run_b = _save_run(client, ["etf"])
    assert run_a["result_hash"] == run_b["result_hash"]


def test_etf_security_types_order_irrelevant_to_hash(client: TestClient) -> None:
    """security_types 순서 무관 — canonical 정렬 → 같은 hash."""
    run_ce = _save_run(client, ["common", "etf"])
    run_ec = _save_run(client, ["etf", "common"])
    assert run_ce["result_hash"] == run_ec["result_hash"]


def test_saved_run_exposes_canonical_etf_security_types(client: TestClient) -> None:
    """저장된 run 응답에 canonical 정렬된 security_types 노출."""
    run = _save_run(client, ["etf", "common"])
    assert run["security_types"] == ["common", "etf"]


# =============================================================================
# 미지원 값 방어 (회귀)
# =============================================================================

def test_unsupported_security_type_still_422(client: TestClient) -> None:
    """ETF 도입 후에도 미지원 값("bond")은 422 (validate_security_type 회귀)."""
    res = client.post(
        f"/api/screen?as_of={_AS_OF}", json=_body("999999", security_types=["bond"]),
    )
    assert res.status_code == 422
