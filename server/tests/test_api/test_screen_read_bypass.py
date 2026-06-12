"""Screen precompute read-bypass 테스트 (ⓓ slice 1b).

`screen_active_codes` 가 종목별 live factor 평가 대신 precompute snapshot value 로
조건을 판정하는 read-bypass 경로 + 공유 helper `try_serve_snapshot_values` 를 검증.

핵심 불변 (oracle 설계검토 GO 조건):
    - **byte-동일**: serve 경로 result_codes == live 경로(snapshot_repo None) result_codes.
    - **C1**: current_data_versions 가 pack-aware — custom/다른 pack snapshot 은 stale
      → 전부 live(여기선 data_versions mismatch 로 대표).
    - **C2-a**: dividend-의존 factor 조건이 섞이면 serve 비활성(전부 live).
    - **reproduce bypass**: batch_cutoff 주입 시 serve 비활성(전부 live, byte-동일 재현).
    - **회귀 0**: snapshot_repo/current_dv None → 기존 live 동작.
    - **all-or-nothing**: 일부 factor miss / data_versions stale → 그 종목 통째 live.
    - snapshot value None(미산정) → 조건 불충족(live is_na 와 동일).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.api.routes.screen import screen_active_codes
from app.repositories.batch_run_repository import EXCLUDE_ALL_CUTOFF
from app.repositories.fakes import (
    FakeCorporateActionRepository,
    FakeFinancialRepository,
    FakeMarketCapRepository,
    FakePriceRepository,
    FakeStockSnapshotRepository,
    FakeTreasurySharesRepository,
)
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    FinancialRecord,
    StockMasterRecord,
    StockSnapshotRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository
from app.schemas.screen import ConditionIn, OpEnum
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK
from app.services.snapshot_serve import try_serve_snapshot_values

_AS_OF = date(2024, 5, 7)
_CITATION = UUID("00000000-0000-0000-0000-0000000000ff")
_EPS_FACTOR_ID = "eps:basic-ttm-consolidated-ifrs"
_EPS_ACCOUNT = "basic_eps"
_CONSOLIDATED = "consolidated"

_FACTORS_BY_ID = {f["canonical_id"]: f for f in DEFAULT_PACK.body["factors"]}
_EPS_UUID = UUID(_FACTORS_BY_ID[_EPS_FACTOR_ID]["uuid"])
_DIV_YIELD_ID = "dividend-yield:trailing-annual"
_DIV_YIELD_UUID = UUID(_FACTORS_BY_ID[_DIV_YIELD_ID]["uuid"])

# pack-aware fingerprint 대표 — 실제 키 set 과 무관히 equality 만 검사하므로
# 테스트는 임의 dict 를 current_dv 와 snapshot.data_versions 양쪽에 동일 주입.
_DV = {"factor_pack_content_hash": "sha256:builtin", "krx_batch_id": "k1",
       "dart_batch_id": "d1", "evaluator_version": "1.0"}


def _snap(
    *, code: str, factor_uuid: UUID, value: Decimal | None,
    data_versions: dict | None = None,
) -> StockSnapshotRecord:
    return StockSnapshotRecord(
        stock_code=code, as_of_date=_AS_OF, factor_uuid=factor_uuid,
        value=value, value_unit="krw", inputs={}, citation_id=_CITATION,
        computed_at=datetime(2024, 5, 7, 18, 0, tzinfo=UTC),
        data_versions=data_versions if data_versions is not None else dict(_DV),
    )


def _stock(code: str) -> StockMasterRecord:
    return StockMasterRecord(
        id=UUID(int=int(code)), current_code=code, current_name=code,
        market="KOSPI", listing_date=date(2000, 1, 1), delisting_date=None,
        fiscal_month=12,
        code_history=(CodeHistoryEntry(code, date(2000, 1, 1), None,
                                       "initial_listing"),),
    )


def _eps_quarters(code: str, total: str) -> list[FinancialRecord]:
    """code 의 4 분기 basic_eps — TTM 합 = total (분기별 total/4)."""
    each = str(Decimal(total) / 4)
    periods = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    eff = [date(2023, 5, 15), date(2023, 8, 14), date(2023, 11, 14),
           date(2024, 3, 30)]
    out: list[FinancialRecord] = []
    for fp, e in zip(periods, eff, strict=True):
        out.append(FinancialRecord(
            id=uuid4(), code=code,
            code_lineage_id=UUID(int=int(code)),
            effective_date=e, fiscal_period=fp, account=_EPS_ACCOUNT,
            value=Decimal(each), unit="krw", ifrs_type=_CONSOLIDATED,
            citation_id=_CITATION, superseded_by=None,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        ))
    return out


def _cond(op: OpEnum, value: str, factor: str = _EPS_FACTOR_ID) -> list[ConditionIn]:
    return [ConditionIn(factor=factor, op=op, value=value)]


def _kwargs(stocks, financials, snapshot_repo=None, current_dv=None, **extra):
    """screen_active_codes 의 공통 kwargs (Fake repo 일괄 주입)."""
    base = dict(
        as_of=_AS_OF,
        stocks_repo=stocks,
        pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        price_repo=FakePriceRepository(records=()),
        financial_repo=financials,
        corporate_action_repo=FakeCorporateActionRepository(records=()),
        market_cap_repo=FakeMarketCapRepository(records=()),
        treasury_repo=FakeTreasurySharesRepository(records=()),
        snapshot_repo=snapshot_repo,
        current_data_versions=current_dv,
    )
    base.update(extra)
    return base


# =============================================================================
# try_serve_snapshot_values — 공유 순수 predicate 단위
# =============================================================================

def test_serve_all_hit_returns_values() -> None:
    """전 uuid hit + data_versions 일치 → {uuid: value}."""
    repo = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_EPS_UUID, value=Decimal("4000")),
    ])
    served = try_serve_snapshot_values(
        "005930", as_of=_AS_OF, factor_uuids=frozenset({_EPS_UUID}),
        snapshot_repo=repo, current_data_versions=dict(_DV),
    )
    assert served == {_EPS_UUID: Decimal("4000")}


def test_serve_value_none_preserved() -> None:
    """미산정(value None)도 serve — None 보존(호출자가 조건 불충족 처리)."""
    repo = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_EPS_UUID, value=None),
    ])
    served = try_serve_snapshot_values(
        "005930", as_of=_AS_OF, factor_uuids=frozenset({_EPS_UUID}),
        snapshot_repo=repo, current_data_versions=dict(_DV),
    )
    assert served == {_EPS_UUID: None}


def test_serve_miss_returns_none() -> None:
    """요청 uuid 일부 부재 → None(부분 serve 안 함)."""
    repo = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_EPS_UUID, value=Decimal("4000")),
    ])
    served = try_serve_snapshot_values(
        "005930", as_of=_AS_OF,
        factor_uuids=frozenset({_EPS_UUID, _DIV_YIELD_UUID}),  # div miss
        snapshot_repo=repo, current_data_versions=dict(_DV),
    )
    assert served is None


def test_serve_stale_returns_none() -> None:
    """data_versions 불일치 → None(stale, recompute)."""
    repo = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_EPS_UUID, value=Decimal("4000"),
              data_versions={"krx_batch_id": "OLD"}),
    ])
    served = try_serve_snapshot_values(
        "005930", as_of=_AS_OF, factor_uuids=frozenset({_EPS_UUID}),
        snapshot_repo=repo, current_data_versions=dict(_DV),
    )
    assert served is None


def test_serve_no_repo_or_dv_returns_none() -> None:
    """repo·current_dv 미주입 → None(live, 회귀 0)."""
    repo = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_EPS_UUID, value=Decimal("4000")),
    ])
    assert try_serve_snapshot_values(
        "005930", as_of=_AS_OF, factor_uuids=frozenset({_EPS_UUID}),
        snapshot_repo=None, current_data_versions=dict(_DV),
    ) is None
    assert try_serve_snapshot_values(
        "005930", as_of=_AS_OF, factor_uuids=frozenset({_EPS_UUID}),
        snapshot_repo=repo, current_data_versions=None,
    ) is None


# =============================================================================
# screen serve — eval skip / fallback / C1 / C2-a / reproduce / byte-동일
# =============================================================================

class _RaisingEvaluator:
    """evaluate 호출 시 raise — serve 가 평가를 skip 했음을 입증하는 spy."""

    def evaluate(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("evaluate 가 호출됨 — snapshot serve 실패(평가 skip 안 됨)")


def test_serve_hit_skips_live_evaluation() -> None:
    """전 종목 hit → live evaluate 미호출(RaisingEvaluator 미발동) + result 정확.

    005930 EPS=4000(통과, > 3999), 000660 EPS=1000(탈락). 조건 EPS > 3999.
    """
    stocks = FakeStocksMasterRepository(records=[_stock("005930"), _stock("000660")])
    snaps = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_EPS_UUID, value=Decimal("4000")),
        _snap(code="000660", factor_uuid=_EPS_UUID, value=Decimal("1000")),
    ])
    result = screen_active_codes(
        **_kwargs(stocks, FakeFinancialRepository(records=()),
                  snapshot_repo=snaps, current_dv=dict(_DV),
                  evaluator=_RaisingEvaluator()),  # serve 실패 시 raise.
        conditions=_cond(OpEnum.GT, "3999"),
    )
    assert result == ("005930",)


def test_serve_value_none_excluded() -> None:
    """snapshot value None(미산정) → 조건 불충족(live is_na 동일)."""
    stocks = FakeStocksMasterRepository(records=[_stock("005930")])
    snaps = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_EPS_UUID, value=None),
    ])
    result = screen_active_codes(
        **_kwargs(stocks, FakeFinancialRepository(records=()),
                  snapshot_repo=snaps, current_dv=dict(_DV),
                  evaluator=_RaisingEvaluator()),
        conditions=_cond(OpEnum.LT, "999999"),
    )
    assert result == ()


def test_stale_dv_falls_back_to_live() -> None:
    """data_versions stale → live 평가(snapshot 무시). live EPS 로 판정.

    snapshot value=99999(통과할 값)이나 stale → live 평가(EPS=4000) 사용 →
    EPS > 3999 통과. snapshot 값을 썼다면 stale 무시가 안 된 것.
    """
    stocks = FakeStocksMasterRepository(records=[_stock("005930")])
    snaps = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_EPS_UUID, value=Decimal("99999"),
              data_versions={"krx_batch_id": "OLD"}),
    ])
    financials = FakeFinancialRepository(records=_eps_quarters("005930", "4000"))
    result = screen_active_codes(
        **_kwargs(stocks, financials, snapshot_repo=snaps, current_dv=dict(_DV)),
        conditions=_cond(OpEnum.GT, "3999"),  # live EPS=4000 통과.
    )
    assert result == ("005930",)


def test_custom_pack_mismatch_all_live() -> None:
    """current_dv 가 snapshot.data_versions 와 다른 pack hash → 전부 live(C1).

    custom pack 은 factor_pack_content_hash 가 builtin snapshot 과 달라 stale →
    live. 여기선 current_dv 의 content_hash 를 다른 값으로 줘 mismatch 대표.
    snapshot(99999, builtin hash) 무시되고 live(EPS=4000) 평가 → 통과.
    """
    stocks = FakeStocksMasterRepository(records=[_stock("005930")])
    snaps = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_EPS_UUID, value=Decimal("99999")),
    ])
    financials = FakeFinancialRepository(records=_eps_quarters("005930", "4000"))
    custom_dv = dict(_DV)
    custom_dv["factor_pack_content_hash"] = "sha256:custom-other"
    result = screen_active_codes(
        **_kwargs(stocks, financials, snapshot_repo=snaps, current_dv=custom_dv),
        conditions=_cond(OpEnum.GT, "3999"),
    )
    assert result == ("005930",)


def test_dividend_factor_condition_disables_serve() -> None:
    """dividend-의존 factor 조건 → serve 비활성(전부 live, C2-a).

    조건에 dividend-yield factor 가 섞이면 RaisingEvaluator 가 발동해야 함
    (serve 했다면 평가 skip 으로 raise 안 됨). snapshot 은 양 factor hit 이지만
    C2-a 로 serve 자체가 비활성.
    """
    import pytest
    stocks = FakeStocksMasterRepository(records=[_stock("005930")])
    snaps = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_EPS_UUID, value=Decimal("4000")),
        _snap(code="005930", factor_uuid=_DIV_YIELD_UUID, value=Decimal("0.02")),
    ])
    conditions = [
        ConditionIn(factor=_EPS_FACTOR_ID, op=OpEnum.GT, value="0"),
        ConditionIn(factor=_DIV_YIELD_ID, op=OpEnum.GT, value="0"),
    ]
    with pytest.raises(AssertionError, match="evaluate 가 호출됨"):
        screen_active_codes(
            **_kwargs(stocks, FakeFinancialRepository(records=()),
                      snapshot_repo=snaps, current_dv=dict(_DV),
                      evaluator=_RaisingEvaluator()),
            conditions=conditions,
        )


def test_reproduce_cutoff_disables_serve() -> None:
    """batch_cutoff 주입(reproduce 모드) → serve 비활성(전부 live).

    RaisingEvaluator 발동해야 함 — cutoff 가 있으면 snapshot 적격이어도 serve 차단.
    """
    import pytest
    stocks = FakeStocksMasterRepository(records=[_stock("005930")])
    snaps = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_EPS_UUID, value=Decimal("4000")),
    ])
    cutoff = EXCLUDE_ALL_CUTOFF
    with pytest.raises(AssertionError, match="evaluate 가 호출됨"):
        screen_active_codes(
            **_kwargs(stocks, FakeFinancialRepository(records=()),
                      snapshot_repo=snaps, current_dv=dict(_DV),
                      evaluator=_RaisingEvaluator(),
                      krx_batch_cutoff=cutoff, dart_batch_cutoff=cutoff),
            conditions=_cond(OpEnum.GT, "3999"),
        )


def test_byte_identical_serve_vs_live() -> None:
    """serve 경로 result_codes == live 경로(snapshot_repo None) result_codes.

    경계 임계값(>= / < / =) 포함 — snapshot value 가 live evaluate 결과와 동일
    Decimal 이라 _OP_COMPARATORS 비교 결과 byte-동일(§2.10 불변).
    """
    codes = ["005930", "000660", "000123"]
    eps_map = {"005930": "4000", "000660": "1000", "000123": "3999"}
    stocks = FakeStocksMasterRepository(records=[_stock(c) for c in codes])
    financials = FakeFinancialRepository(records=[
        r for c in codes for r in _eps_quarters(c, eps_map[c])
    ])
    # snapshot 은 live 와 동일 값(byte-동일 producer 전제).
    snaps = FakeStockSnapshotRepository([
        _snap(code=c, factor_uuid=_EPS_UUID, value=Decimal(eps_map[c]))
        for c in codes
    ])

    for op, value in [(OpEnum.GE, "3999"), (OpEnum.LT, "4000"),
                      (OpEnum.EQ, "4000"), (OpEnum.GT, "1000")]:
        live = screen_active_codes(
            **_kwargs(stocks, financials, snapshot_repo=None, current_dv=None),
            conditions=_cond(op, value),
        )
        served = screen_active_codes(
            **_kwargs(stocks, financials, snapshot_repo=snaps, current_dv=dict(_DV)),
            conditions=_cond(op, value),
        )
        assert served == live, f"op={op} value={value}: {served} != {live}"


def test_partial_miss_falls_back_to_live() -> None:
    """다종목 — 일부만 snapshot hit. miss 종목은 live, hit 종목은 serve.

    005930 snapshot 있음(4000), 000660 snapshot 없음 → live(4000). 둘 다 통과.
    byte-동일성을 위해 live 값도 동일 4000.
    """
    stocks = FakeStocksMasterRepository(records=[_stock("005930"), _stock("000660")])
    financials = FakeFinancialRepository(records=(
        _eps_quarters("005930", "4000") + _eps_quarters("000660", "4000")
    ))
    snaps = FakeStockSnapshotRepository([
        _snap(code="005930", factor_uuid=_EPS_UUID, value=Decimal("4000")),
        # 000660 snapshot 없음 → live fallback.
    ])
    result = screen_active_codes(
        **_kwargs(stocks, financials, snapshot_repo=snaps, current_dv=dict(_DV)),
        conditions=_cond(OpEnum.GE, "4000"),
    )
    assert result == ("000660", "005930")


def test_no_snapshot_repo_regression() -> None:
    """snapshot_repo None → 기존 live 동작(회귀 0)."""
    stocks = FakeStocksMasterRepository(records=[_stock("005930")])
    financials = FakeFinancialRepository(records=_eps_quarters("005930", "4000"))
    result = screen_active_codes(
        **_kwargs(stocks, financials, snapshot_repo=None, current_dv=None),
        conditions=_cond(OpEnum.GT, "3999"),
    )
    assert result == ("005930",)


# =============================================================================
# C2-a invariant 잠금 (oracle Q3) + uuid 부재 serve 비활성 (oracle Q5)
# =============================================================================

def test_dividend_dependency_classification_locks_builtin_pack() -> None:
    """빌트인 pack 의 dividend-의존 factor 는 serve-ineligible, 나머지는 eligible.

    oracle Q3 — `_conditions_serve_eligible` 의 `_DIVIDEND_DEPENDENT_INPUTS` 가 현
    빌트인 pack 의 FSC-dividend-sourced terminal field 를 완전 커버함을 잠근다(향후
    pack 이 dividend factor 를 추가/변경하면 이 테스트가 회귀로 잡음). inputs 는
    1-level leaf list 라 transitive 의존은 미탐 — 빌트인 변경 시 본 테스트 갱신 필요.
    """
    from app.api.routes.screen import (
        _compile_conditions,
        _conditions_serve_eligible,
    )

    # dividend-의존 factor → serve 비활성.
    for div_fid in ("dividend-yield:trailing-annual", "price-return:total-annual"):
        compiled = _compile_conditions(
            [ConditionIn(factor=div_fid, op=OpEnum.GT, value="0")],
            _FACTORS_BY_ID,
        )
        assert _conditions_serve_eligible(compiled) is False, div_fid
    # dividend-비의존(financial/market_cap) factor → serve 적격.
    for ok_fid in (
        "eps:basic-ttm-consolidated-ifrs",
        "roe:ttm-avg-equity-consolidated-ifrs",
        "per:ttm-consolidated-ifrs",
        "pbr:consolidated-ifrs",
    ):
        compiled = _compile_conditions(
            [ConditionIn(factor=ok_fid, op=OpEnum.GT, value="0")],
            _FACTORS_BY_ID,
        )
        assert _conditions_serve_eligible(compiled) is True, ok_fid


def test_missing_factor_uuid_disables_serve() -> None:
    """factor dict 에 uuid 부재 → factor_uuid None → serve-ineligible(결정적, Q5).

    uuid4 fallback(비결정·silent permanent-live masking) 대신 None → 명시 serve
    비활성(live). pack body factor 는 항상 uuid 보유라 운영 무영향, 최소 factor
    dict 만 해당.
    """
    from app.api.routes.screen import (
        _compile_conditions,
        _CompiledCondition,
        _conditions_serve_eligible,
    )

    # uuid 없는 최소 factor dict.
    factors_no_uuid = {
        "test:no-uuid": {
            "canonical_id": "test:no-uuid",
            "formula": {"inputs": ["basic_eps_consolidated_ifrs"]},
        }
    }
    compiled = _compile_conditions(
        [ConditionIn(factor="test:no-uuid", op=OpEnum.GT, value="0")],
        factors_no_uuid,
    )
    assert compiled[0].factor_uuid is None
    assert _conditions_serve_eligible(compiled) is False
    # 명시 타입: _CompiledCondition.factor_uuid 는 UUID | None.
    assert isinstance(compiled[0], _CompiledCondition)
