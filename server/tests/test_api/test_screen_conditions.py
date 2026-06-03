"""POST /api/screen 조건 매칭 (M1) — factor/op/value 실평가 필터.

M0 stub (active universe 전체 반환) 제거 후의 조건 매칭 검증. 두 layer:

1. **pure helper 단위 테스트** — `_passes_all_conditions` / `_compile_conditions`
   / `_parse_decimal_value` 를 FakeFieldProvider 로 직접 검증 (op Decimal 비교
   정확성, N/A 제외, AND 결합, value/factor 400).
2. **end-to-end** — fully-wired create_app 에 fact-backed FinancialRepository 를
   주입하고 POST /api/screen 으로 조건을 보내 result_codes 검증.

end-to-end 의 factor 선택 — `eps:basic-ttm-consolidated-ifrs` 는 단일 field
`basic_eps_consolidated_ifrs` (account=basic_eps, ifrs_type=consolidated) 의
최근 4 분기 strict 합. 4 분기 재무만 주입하면 결정적 EPS 산출 → op 비교 검증에
이상적 (가격 / 시가총액 fetch 불필요).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.routes.screen import (
    _compile_conditions,
    _parse_decimal_value,
    _passes_all_conditions,
)
from app.main import create_app
from app.repositories.fakes import FakeFinancialRepository
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    FinancialRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.schemas.screen import ConditionIn, OpEnum
from app.services.factor_evaluator import (
    EVALUATOR_POLICY_VERSION,
    EvaluationResult,
    FactorEvaluator,
)
from app.services.factor_pack import DEFAULT_PACK

_EPS_ACCOUNT = "basic_eps"
_EPS_FACTOR_ID = "eps:basic-ttm-consolidated-ifrs"
_CONSOLIDATED = "consolidated"
_CITATION = UUID("00000000-0000-0000-0000-0000000000ff")


# =============================================================================
# Pure helper 단위 테스트 — FakeFieldProvider 로 op / N/A / AND 검증
# =============================================================================

class _FakeFieldProvider:
    """FieldProvider Protocol 의 테스트용 stub — factor canonical_id → 고정값.

    evaluator.evaluate 가 실제로는 AST 를 visit 하나, 본 단위 테스트는
    `_passes_all_conditions` 의 op 비교 / N/A 분기만 검증하므로 evaluator 도
    stub (canonical_id 기준 사전 정의 EvaluationResult 반환).
    """

    as_of: date = date(2024, 5, 7)


class _StubEvaluator:
    """canonical_id → EvaluationResult 매핑 stub — pure helper 검증용.

    실제 FactorEvaluator 대신 사전 정의된 값/N/A 를 반환하여 op 비교 / N/A 제외
    로직만 격리 검증.
    """

    def __init__(self, results: dict[str, EvaluationResult]) -> None:
        self._results = results

    def evaluate(self, factor: dict, provider, *, as_of: date) -> EvaluationResult:
        return self._results[factor["canonical_id"]]


def _result(canonical_id: str, value: Decimal | None) -> EvaluationResult:
    """EvaluationResult 빌더 — value None 이면 N/A."""
    is_na = value is None
    return EvaluationResult(
        value=value,
        is_na=is_na,
        na_reason="missing_input:test" if is_na else None,
        factor_uuid=uuid4(),
        factor_canonical_id=canonical_id,
        inputs_used={},
        evaluator_version=EVALUATOR_POLICY_VERSION,
    )


def _factor(canonical_id: str) -> dict:
    """최소 factor dict — _StubEvaluator 가 canonical_id 만 본다."""
    return {"canonical_id": canonical_id}


def _compile(canonical_id: str, op: OpEnum, value: str):
    """단일 condition compile — pure helper 용."""
    factors_by_id = {canonical_id: _factor(canonical_id)}
    cond = ConditionIn(factor=canonical_id, op=op, value=value)
    return _compile_conditions([cond], factors_by_id)


@pytest.mark.parametrize(
    ("op", "threshold", "actual", "expected"),
    [
        # op, value(str), factor 산출값(Decimal), 통과 여부
        (OpEnum.LT, "10", Decimal("9"), True),
        (OpEnum.LT, "10", Decimal("10"), False),
        (OpEnum.LE, "10", Decimal("10"), True),
        (OpEnum.LE, "10", Decimal("11"), False),
        (OpEnum.EQ, "10", Decimal("10"), True),
        (OpEnum.EQ, "10", Decimal("10.0"), True),  # Decimal 동치
        (OpEnum.EQ, "10", Decimal("10.5"), False),
        (OpEnum.GE, "10", Decimal("10"), True),
        (OpEnum.GE, "10", Decimal("9"), False),
        (OpEnum.GT, "10", Decimal("11"), True),
        (OpEnum.GT, "10", Decimal("10"), False),
        (OpEnum.NE, "10", Decimal("11"), True),
        (OpEnum.NE, "10", Decimal("10"), False),
    ],
)
def test_op_decimal_comparison(
    op: OpEnum, threshold: str, actual: Decimal, expected: bool,
) -> None:
    """각 op 의 Decimal 비교 정확성 (float 금지 — Decimal 동치 포함)."""
    cid = "per:ttm-consolidated-ifrs"
    compiled = _compile(cid, op, threshold)
    evaluator = _StubEvaluator({cid: _result(cid, actual)})
    passed = _passes_all_conditions(
        _FakeFieldProvider(), compiled, evaluator, as_of=date(2024, 5, 7),
    )
    assert passed is expected


def test_na_factor_excludes_stock() -> None:
    """N/A factor 종목은 조건 불충족으로 제외 (값 없는 종목이 만족 주장 안 함)."""
    cid = "per:ttm-consolidated-ifrs"
    compiled = _compile(cid, OpEnum.LT, "10")
    evaluator = _StubEvaluator({cid: _result(cid, None)})  # N/A
    passed = _passes_all_conditions(
        _FakeFieldProvider(), compiled, evaluator, as_of=date(2024, 5, 7),
    )
    assert passed is False


def test_multiple_conditions_and_combination() -> None:
    """다중 조건 AND — 모두 통과해야 True. 하나라도 불충족이면 False."""
    cid_a = "per:ttm-consolidated-ifrs"
    cid_b = "pbr:consolidated-ifrs"
    factors_by_id = {cid_a: _factor(cid_a), cid_b: _factor(cid_b)}
    conditions = [
        ConditionIn(factor=cid_a, op=OpEnum.LT, value="10"),
        ConditionIn(factor=cid_b, op=OpEnum.LT, value="1"),
    ]
    compiled = _compile_conditions(conditions, factors_by_id)

    # 둘 다 통과 → True.
    ev_both = _StubEvaluator({
        cid_a: _result(cid_a, Decimal("8")),
        cid_b: _result(cid_b, Decimal("0.9")),
    })
    assert _passes_all_conditions(
        _FakeFieldProvider(), compiled, ev_both, as_of=date(2024, 5, 7),
    ) is True

    # 두 번째 불충족 → False (AND).
    ev_one = _StubEvaluator({
        cid_a: _result(cid_a, Decimal("8")),
        cid_b: _result(cid_b, Decimal("2")),  # PBR < 1 불충족
    })
    assert _passes_all_conditions(
        _FakeFieldProvider(), compiled, ev_one, as_of=date(2024, 5, 7),
    ) is False

    # 두 번째가 N/A → False (AND + N/A 제외).
    ev_na = _StubEvaluator({
        cid_a: _result(cid_a, Decimal("8")),
        cid_b: _result(cid_b, None),
    })
    assert _passes_all_conditions(
        _FakeFieldProvider(), compiled, ev_na, as_of=date(2024, 5, 7),
    ) is False


def test_parse_decimal_value_accepts_numeric() -> None:
    """정상 숫자 string → Decimal (float 경유 없음)."""
    assert _parse_decimal_value("10") == Decimal("10")
    assert _parse_decimal_value("0.5") == Decimal("0.5")
    assert _parse_decimal_value("-3.14") == Decimal("-3.14")


def test_parse_decimal_value_rejects_non_numeric() -> None:
    """비숫자 value → 400."""
    with pytest.raises(HTTPException) as exc:
        _parse_decimal_value("abc")
    assert exc.value.status_code == 400


def test_parse_decimal_value_rejects_non_finite() -> None:
    """NaN / Infinity → 400 (Decimal 은 파싱하나 비교 불가)."""
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(HTTPException) as exc:
            _parse_decimal_value(bad)
        assert exc.value.status_code == 400


def test_compile_conditions_rejects_unknown_factor() -> None:
    """pack 에 없는 factor 참조 → 400."""
    # 형식상 valid canonical_id 이나 pack 에 없는 것.
    cid = "nonexistent:made-up-factor"
    cond = ConditionIn(factor=cid, op=OpEnum.LT, value="10")
    with pytest.raises(HTTPException) as exc:
        _compile_conditions([cond], {})  # 빈 pack
    assert exc.value.status_code == 400


def test_compile_conditions_value_echo_sanitized() -> None:
    """400 detail 이 사용자 입력 value 를 echo 하지 않음 (ADR-0007 sanitize)."""
    cond = ConditionIn(factor="per:ttm-consolidated-ifrs", op=OpEnum.LT,
                       value="weird_value_xyz")
    with pytest.raises(HTTPException) as exc:
        _compile_conditions([cond], {"per:ttm-consolidated-ifrs": _factor(
            "per:ttm-consolidated-ifrs")})
    # value 가 비숫자 → 400 이며 detail 에 value 미포함.
    assert exc.value.status_code == 400
    assert "weird_value_xyz" not in str(exc.value.detail)


def test_real_evaluator_integration_with_provider() -> None:
    """실제 FactorEvaluator + DEFAULT_PACK 으로 _passes_all_conditions 통합.

    stub evaluator 가 아닌 진짜 evaluator 로 eps factor 를 평가 — provider 가
    4 분기 EPS 를 제공하면 합산값과 op 비교가 일관.
    """
    from app.repositories.fakes import (
        FakeCorporateActionRepository,
        FakeMarketCapRepository,
        FakePriceRepository,
        FakeTreasurySharesRepository,
    )
    from app.services.db_field_provider import DbFieldProvider

    code = "005930"
    fin = FakeFinancialRepository(records=_eps_quarters(code, ["1000"] * 4))
    provider = DbFieldProvider(
        code=code,
        as_of=date(2024, 5, 7),
        price_repo=FakePriceRepository(records=()),
        financial_repo=fin,
        corporate_action_repo=FakeCorporateActionRepository(records=()),
        market_cap_repo=FakeMarketCapRepository(records=()),
        treasury_repo=FakeTreasurySharesRepository(records=()),
        factor_pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
    )
    factors_by_id = {f["canonical_id"]: f for f in DEFAULT_PACK.body["factors"]}
    # EPS = 1000*4 = 4000. EPS > 3999 → 통과, EPS > 4000 → 탈락.
    evaluator = FactorEvaluator()
    pass_cond = _compile_conditions(
        [ConditionIn(factor=_EPS_FACTOR_ID, op=OpEnum.GT, value="3999")],
        factors_by_id,
    )
    assert _passes_all_conditions(
        provider, pass_cond, evaluator, as_of=date(2024, 5, 7),
    ) is True

    # 새 provider (캐시 회피) — EPS > 4000 불충족.
    provider2 = DbFieldProvider(
        code=code, as_of=date(2024, 5, 7),
        price_repo=FakePriceRepository(records=()),
        financial_repo=fin,
        corporate_action_repo=FakeCorporateActionRepository(records=()),
        market_cap_repo=FakeMarketCapRepository(records=()),
        treasury_repo=FakeTreasurySharesRepository(records=()),
        factor_pack=DEFAULT_PACK, evaluator=FactorEvaluator(),
    )
    fail_cond = _compile_conditions(
        [ConditionIn(factor=_EPS_FACTOR_ID, op=OpEnum.GT, value="4000")],
        factors_by_id,
    )
    assert _passes_all_conditions(
        provider2, fail_cond, evaluator, as_of=date(2024, 5, 7),
    ) is False


# =============================================================================
# End-to-end — fully-wired create_app + fact-backed FinancialRepository
# =============================================================================

def _eps_quarters(code: str, values: Sequence[str]) -> list[FinancialRecord]:
    """code 의 4 분기 basic_eps consolidated 재무 — TTM 합 = sum(values).

    fiscal_period 2023Q1~Q4. effective_date 는 as_of(2024-05-07) 이전이도록 분기
    신고기한 보수값 (+45/90 일) 보다 충분히 이전인 고정 일자.
    """
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


def _stock(code: str, name: str) -> StockMasterRecord:
    return StockMasterRecord(
        id=UUID(int=int(code)),
        current_code=code, current_name=name, market="KOSPI",
        listing_date=date(2000, 1, 1), delisting_date=None, fiscal_month=12,
        code_history=(CodeHistoryEntry(code, date(2000, 1, 1), None,
                                       "initial_listing"),),
    )


@pytest.fixture
def e2e_client() -> Iterator[TestClient]:
    """두 종목 — 005930 (EPS=4000), 000660 (재무 없음 → N/A)."""
    stocks = FakeStocksMasterRepository(records=[
        _stock("005930", "삼성전자"),
        _stock("000660", "SK하이닉스"),
    ])
    # 005930 만 4 분기 EPS — TTM = 4000. 000660 은 재무 없음 → EPS N/A.
    financials = FakeFinancialRepository(
        records=_eps_quarters("005930", ["1000", "1000", "1000", "1000"]),
    )
    app = create_app(
        stocks_repository=stocks,
        financial_repository=financials,
    )
    with TestClient(app) as c:
        yield c


def _body(op: str, value: str) -> dict:
    return {
        "conditions": [{"factor": _EPS_FACTOR_ID, "op": op, "value": value}],
        "selected_factors": [_EPS_FACTOR_ID],
    }


def test_e2e_condition_passes_only_matching_stock(e2e_client: TestClient) -> None:
    """EPS > 3999 → 005930 (EPS=4000) 통과, 000660 (N/A) 제외."""
    res = e2e_client.post("/api/screen?as_of=2024-05-07", json=_body(">", "3999"))
    assert res.status_code == 200
    body = res.json()
    assert body["result_codes"] == ["005930"]
    assert body["total"] == 1


def test_e2e_condition_excludes_all_when_threshold_unmet(
    e2e_client: TestClient,
) -> None:
    """EPS > 5000 → 005930 (EPS=4000) 도 탈락. 결과 빈 list."""
    res = e2e_client.post("/api/screen?as_of=2024-05-07", json=_body(">", "5000"))
    assert res.status_code == 200
    assert res.json()["result_codes"] == []


def test_e2e_na_stock_excluded(e2e_client: TestClient) -> None:
    """EPS < 999999 (사실상 항상 참) — N/A 종목 (000660) 은 여전히 제외."""
    res = e2e_client.post("/api/screen?as_of=2024-05-07",
                          json=_body("<", "999999"))
    assert res.status_code == 200
    # 005930 (EPS=4000 < 999999) 통과, 000660 (N/A) 제외.
    assert res.json()["result_codes"] == ["005930"]


def test_e2e_eq_decimal(e2e_client: TestClient) -> None:
    """EPS = 4000 정확 동치 (Decimal)."""
    res = e2e_client.post("/api/screen?as_of=2024-05-07", json=_body("=", "4000"))
    assert res.status_code == 200
    assert res.json()["result_codes"] == ["005930"]


def test_e2e_unknown_factor_returns_400(e2e_client: TestClient) -> None:
    """pack 에 없는 (그러나 형식상 valid) factor → 400."""
    body = {
        "conditions": [{"factor": "made-up:factor-x", "op": "<", "value": "10"}],
        "selected_factors": [_EPS_FACTOR_ID],
    }
    res = e2e_client.post("/api/screen?as_of=2024-05-07", json=body)
    assert res.status_code == 400


def test_e2e_non_numeric_value_returns_400(e2e_client: TestClient) -> None:
    """비숫자 비교값 → 400."""
    res = e2e_client.post("/api/screen?as_of=2024-05-07",
                          json=_body("<", "notanumber"))
    assert res.status_code == 400


def test_e2e_deterministic_order(e2e_client: TestClient) -> None:
    """결정적 결과 순서 — normalize_stock_codes (6 자리·정렬·dedup).

    두 종목 모두 통과하는 조건에서 result_codes 가 항상 정렬된 동일 순서.
    """
    # 두 종목 모두 EPS 4 분기 주입한 별도 client.
    stocks = FakeStocksMasterRepository(records=[
        _stock("005930", "A"), _stock("000660", "B"),
    ])
    financials = FakeFinancialRepository(records=(
        _eps_quarters("005930", ["1000"] * 4)
        + _eps_quarters("000660", ["1000"] * 4)
    ))
    app = create_app(stocks_repository=stocks, financial_repository=financials)
    with TestClient(app) as c:
        res1 = c.post("/api/screen?as_of=2024-05-07", json=_body(">", "0"))
        res2 = c.post("/api/screen?as_of=2024-05-07", json=_body(">", "0"))
    assert res1.json()["result_codes"] == ["000660", "005930"]  # 정렬 asc
    assert res1.json()["result_codes"] == res2.json()["result_codes"]


def test_e2e_save_run_matches_screen_result(e2e_client: TestClient) -> None:
    """Save Run (/api/runs) result_codes == /api/screen 조건 매칭 결과.

    이전 stub 은 Save Run 이 active universe 전체 (005930+000660) 를 저장해 screen
    의 조건 매칭 결과와 불일치했음 (재현성 §2.10 위반 — 저장된 run ≠ 사용자가 본
    스크린). `screen_active_codes` 단일 경로 통합으로 두 endpoint 결과 일치 보장.
    """
    body = _body(">", "3999")  # 005930 (EPS=4000) 통과, 000660 (N/A) 제외.
    screen = e2e_client.post("/api/screen?as_of=2024-05-07", json=body)
    run = e2e_client.post("/api/runs?as_of=2024-05-07", json=body)
    assert screen.status_code == 200
    assert run.status_code == 201
    # screen = 조건 매칭 (005930 만). Save Run 이 active 전체가 아닌 동일 결과 저장.
    assert screen.json()["result_codes"] == ["005930"]
    assert run.json()["result_codes"] == screen.json()["result_codes"]
