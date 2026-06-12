"""POST /api/backtest route 통합 테스트 — pack resolve·freeze·디스클로저 (ADR-0027).

검증:
- 빌트인 pack 으로 백테스트 실행 → 200 + freeze(result_hash) + 사실 통계 wire schema.
- disclaimer_required=true 고정 (D4 디스클레이머 게이트).
- survivorship 디스클로저 — survivorship_complete / missing_price_ratio 노출 (D3).
- 거래비용 가정 노출 + override (D2).
- 미존재 custom pack → 404. 기간 역전(end<start) → 422.
- 동일 입력 두 번 → result_hash byte 동일 (재현 §2.10).
- 평가 라벨/등급 0 — 응답에 판단 어휘 없음 (D6).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.fakes import FakeMarketCapRepository, FakePriceRepository
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    MarketCapRecord,
    PriceRecord,
    StockMasterRecord,
)
from app.repositories.stocks_master_repository import FakeStocksMasterRepository

_CODE = "005930"
_BUILTIN_FACTOR = "market-cap:krx-official"

# 분기말 영업일 가격 — 단조 증가 (양의 수익률).
_Q_DATES = [
    date(2022, 3, 31),
    date(2022, 6, 30),
    date(2022, 9, 30),
    date(2022, 12, 31),
]
_PRICES = [Decimal(100), Decimal(110), Decimal(121), Decimal("133.1")]


def _stock(code: str, *, security_type: str = "common") -> StockMasterRecord:
    return StockMasterRecord(
        id=UUID(int=int(code)),
        current_code=code,
        current_name=f"종목{code}",
        market="KOSPI",
        listing_date=date(2018, 1, 1),
        delisting_date=None,
        fiscal_month=12,
        code_history=(
            CodeHistoryEntry(code, date(2018, 1, 1), None, "initial_listing"),
        ),
        security_type=security_type,
    )


def _records() -> tuple[list[PriceRecord], list[MarketCapRecord]]:
    prices: list[PriceRecord] = []
    mcaps: list[MarketCapRecord] = []
    for d, close in zip(_Q_DATES, _PRICES, strict=True):
        prices.append(PriceRecord(
            id=uuid4(), code=_CODE, code_lineage_id=UUID(int=int(_CODE)),
            effective_date=d, open_raw=close, high_raw=close, low_raw=close,
            close_raw=close, volume=1000, trading_value=close * Decimal(1000),
            close_adjusted=close, citation_id=uuid4(),
            created_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
        ))
        mcaps.append(MarketCapRecord(
            id=uuid4(), code=_CODE, code_lineage_id=UUID(int=int(_CODE)),
            effective_date=d, market_cap=close * Decimal(1_000_000),
            shares_outstanding=1_000_000, shares_treasury=None,
            citation_id=uuid4(),
            created_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
        ))
    return prices, mcaps


@pytest.fixture
def client() -> Iterator[TestClient]:
    prices, mcaps = _records()
    app = create_app(
        stocks_repository=FakeStocksMasterRepository(records=[_stock(_CODE)]),
        price_repository=FakePriceRepository(records=prices),
        market_cap_repository=FakeMarketCapRepository(records=mcaps),
    )
    with TestClient(app) as c:
        yield c


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "pack_slug": "speculum-builtin",
        "pack_version": "1.0.0",
        "conditions": [{"factor": _BUILTIN_FACTOR, "op": ">", "value": "0"}],
        "start": "2022-01-01",
        "end": "2022-12-31",
        "rebalance": "quarterly",
    }
    body.update(overrides)
    return body


# =============================================================================
# 1. 기본 실행 + wire schema
# =============================================================================

def test_backtest_returns_freeze_stats_and_disclaimer(client: TestClient) -> None:
    """빌트인 pack 백테스트 → 200 + freeze + stats + 디스클레이머 게이트."""
    res = client.post("/api/backtest", json=_body())
    assert res.status_code == 200, res.text
    body = res.json()

    # freeze 블록 — 재현 식별자.
    freeze = body["freeze"]
    assert freeze["result_hash"].startswith("sha256:")
    assert freeze["pack_content_hash"].startswith("sha256:")
    assert freeze["rebalance"] == "quarterly"
    assert freeze["start"] == "2022-01-01"
    assert freeze["end"] == "2022-12-31"
    # 거래비용 가정 노출 (D2).
    assert freeze["cost_assumptions"]["tax_bps"] == "15"
    assert freeze["cost_assumptions"]["commission_bps"] == "1.5"

    # stats — 사실 통계 (D6). 가격 단조 증가 → 누적수익률 양수.
    stats = body["stats"]
    assert Decimal(stats["cumulative_return"]) > 0
    assert "mdd" in stats and "volatility" in stats and "turnover" in stats

    # equity curve — 분기말 4 점.
    assert len(body["equity_curve"]) == 4
    assert body["equity_curve"][0]["date"] == "2022-03-31"

    # 디스클레이머 게이트 (D4) + survivorship 디스클로저 (D3).
    assert body["disclaimer_required"] is True
    assert body["survivorship_complete"] is True
    assert body["missing_price_ratio"] == "0"


def test_backtest_freeze_is_self_contained(client: TestClient) -> None:
    """ADR-0033 D2 — freeze 가 reproduce 재현 입력을 self-contained 운반.

    result_hash 식별자 외에 pack 식별(slug/version)·universe conditions(canonical
    {factor,op,value})·전체 data_versions(정책+batch_id)가 응답 freeze 에 포함돼야
    한다(M5 #3a-0 — backtest reproduce 의 전제). screen ScreenRunSnapshotOut 동형.
    """
    res = client.post("/api/backtest", json=_body())
    assert res.status_code == 200, res.text
    freeze = res.json()["freeze"]
    # pack 정본 재로드 식별자.
    assert freeze["pack_slug"]
    assert freeze["pack_version"]
    # universe 필터 conditions — canonical, {factor, op, value} 키.
    conds = freeze["conditions"]
    assert isinstance(conds, list) and len(conds) >= 1
    assert set(conds[0].keys()) == {"factor", "op", "value"}
    # 전체 data_versions(정책 + batch_id) — 재실행 데이터 시점 freeze.
    dv = freeze["data_versions"]
    assert isinstance(dv, dict) and len(dv) >= 1


def test_backtest_self_contained_fields_do_not_change_result_hash(
    client: TestClient,
) -> None:
    """ADR-0033 D3 — self-contained 필드 추가가 result_hash 입력과 무관(frozen 호환).

    freeze 의 conditions/data_versions/pack_slug 는 응답 노출용이며 result_hash
    (BacktestSnapshotBuilder hash_input)와 분리돼야 한다 — 같은 입력 두 번이 동일
    hash(결정성)이고, freeze 가 conditions 를 담아도 hash 는 그 값들의 함수일 뿐
    스키마 추가로 바뀌지 않는다. (입력 스키마 불변 — 기존 frozen backtest 호환.)
    """
    first = client.post("/api/backtest", json=_body()).json()
    second = client.post("/api/backtest", json=_body()).json()
    assert first["freeze"]["result_hash"] == second["freeze"]["result_hash"]
    # self-contained 필드도 동일 입력엔 동일(결정성).
    assert first["freeze"]["conditions"] == second["freeze"]["conditions"]
    assert first["freeze"]["data_versions"] == second["freeze"]["data_versions"]


def test_backtest_no_evaluation_labels(client: TestClient) -> None:
    """응답에 평가 라벨/등급 어휘 0 — 사실 통계만 (D6)."""
    res = client.post("/api/backtest", json=_body())
    text = res.text
    # 등급/평가 어휘가 응답에 없어야 한다 (No Advice §2.2 / D6).
    for word in ["우수", "양호", "추천", "최고", "rating", "grade", "score"]:
        assert word not in text


# =============================================================================
# 2. survivorship 디스클로저 (D3)
# =============================================================================

def test_backtest_survivorship_incomplete_when_price_missing() -> None:
    """universe 에 있으나 가격 부재 종목 → survivorship_complete False, ratio>0."""
    prices, mcaps = _records()
    # B 종목을 universe(stocks)에만 추가 — price/market_cap 미제공 → 가격 누락.
    app = create_app(
        stocks_repository=FakeStocksMasterRepository(
            records=[_stock(_CODE), _stock("000002")],
        ),
        price_repository=FakePriceRepository(records=prices),
        market_cap_repository=FakeMarketCapRepository(records=mcaps),
    )
    with TestClient(app) as c:
        res = c.post("/api/backtest", json=_body())
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["survivorship_complete"] is False
        assert Decimal(body["missing_price_ratio"]) > 0


# =============================================================================
# 3. 거래비용 override (D2)
# =============================================================================

def test_backtest_cost_override_exposed(client: TestClient) -> None:
    """거래비용 override → 결과 freeze 에 그 값 노출 (0 도 숨김 아닌 노출)."""
    res = client.post(
        "/api/backtest",
        json=_body(cost_tax_bps=0, cost_commission_bps=0),
    )
    assert res.status_code == 200, res.text
    cost = res.json()["freeze"]["cost_assumptions"]
    # float 0 입력 → Decimal("0.0") str (값 0 의 명시 노출 — D2). 숨김 아님.
    assert Decimal(cost["tax_bps"]) == 0
    assert Decimal(cost["commission_bps"]) == 0


# =============================================================================
# 4. pack resolve 실패 + 입력 검증
# =============================================================================

def test_backtest_unknown_custom_pack_404(client: TestClient) -> None:
    """미존재 custom pack slug → 404."""
    res = client.post(
        "/api/backtest", json=_body(pack_slug="user/nonexistent"),
    )
    assert res.status_code == 404


def test_backtest_end_before_start_422(client: TestClient) -> None:
    """end < start → 422 (기간 검증)."""
    res = client.post(
        "/api/backtest", json=_body(start="2022-12-31", end="2022-01-01"),
    )
    assert res.status_code == 422


# =============================================================================
# 5. 재현 — 동일 입력 동일 result_hash (§2.10)
# =============================================================================

def test_backtest_deterministic_result_hash(client: TestClient) -> None:
    """동일 입력 두 번 → result_hash byte 동일 (재현성 §2.10)."""
    r1 = client.post("/api/backtest", json=_body()).json()
    r2 = client.post("/api/backtest", json=_body()).json()
    assert r1["freeze"]["result_hash"] == r2["freeze"]["result_hash"]
    assert r1["stats"] == r2["stats"]
    assert r1["equity_curve"] == r2["equity_curve"]


def test_backtest_different_cost_different_hash(client: TestClient) -> None:
    """거래비용 가정이 다르면 result_hash 도 다름 (비용이 freeze 입력 — D5)."""
    base = client.post("/api/backtest", json=_body()).json()
    cheap = client.post(
        "/api/backtest", json=_body(cost_tax_bps=0, cost_commission_bps=0),
    ).json()
    assert base["freeze"]["result_hash"] != cheap["freeze"]["result_hash"]


# =============================================================================
# 6. reproduce — frozen backtest 재실행 + equity_curve 동일 검증 (M5 #3a-2, ADR-0033)
# =============================================================================

def test_backtest_reproduce_round_trip_matches(client: TestClient) -> None:
    """backtest 실행 → freeze+equity_curve 로 reproduce → matches=True (결정성).

    ADR-0033 D2~D4 — self-contained freeze + baseline equity_curve 를 그대로
    reproduce 에 넘기면, 같은 입력·데이터(동일 client/repository)라 재실행
    equity_curve 가 baseline 과 byte-동일해야 한다.
    """
    run = client.post("/api/backtest", json=_body()).json()
    repro = client.post(
        "/api/backtest/reproduce",
        json={"freeze": run["freeze"], "equity_curve": run["equity_curve"]},
    )
    assert repro.status_code == 200, repro.text
    out = repro.json()
    assert out["matches"] is True
    # 재실행 equity_curve 가 원본과 동일 (byte-동일 재현).
    assert out["equity_curve"] == run["equity_curve"]
    assert "재현 성공" in out["reproduce_note"]


def test_backtest_reproduce_different_baseline_no_match(client: TestClient) -> None:
    """baseline equity_curve 가 재실행 결과와 다르면 matches=False.

    baseline 의 값을 임의로 변조 → 재실행 결과(원본)와 불일치 → matches=False.
    재실행 equity_curve 자체는 결정적으로 산출되므로 응답에 노출된다.
    """
    run = client.post("/api/backtest", json=_body()).json()
    tampered = [dict(p) for p in run["equity_curve"]]
    # 첫 점 value 를 변조 — baseline 과 재실행이 달라짐.
    tampered[0]["value"] = str(Decimal(tampered[0]["value"]) + Decimal("0.5"))
    repro = client.post(
        "/api/backtest/reproduce",
        json={"freeze": run["freeze"], "equity_curve": tampered},
    )
    assert repro.status_code == 200, repro.text
    out = repro.json()
    assert out["matches"] is False
    # 재실행 결과는 원본(변조 전)과 동일 — 결정성 보존.
    assert out["equity_curve"] == run["equity_curve"]


def test_backtest_reproduce_pack_hash_mismatch_note(client: TestClient) -> None:
    """frozen data_versions 의 pack content_hash 변조 → matches=False + note.

    ADR-0025 D5 — 재로드 pack 의 computed_hash 가 frozen hash 와 불일치하면
    fail-loud (pack 변조 의심). 빈 equity_curve + 구체 reproduce_note.
    """
    run = client.post("/api/backtest", json=_body()).json()
    freeze = dict(run["freeze"])
    dv = dict(freeze["data_versions"])
    # frozen content_hash 를 가짜값으로 변조 — 재로드 pack 과 불일치.
    dv["factor_pack_content_hash"] = "sha256:" + "f" * 64
    freeze["data_versions"] = dv
    repro = client.post(
        "/api/backtest/reproduce",
        json={"freeze": freeze, "equity_curve": run["equity_curve"]},
    )
    assert repro.status_code == 200, repro.text
    out = repro.json()
    assert out["matches"] is False
    assert out["equity_curve"] == []
    assert "content_hash 불일치" in out["reproduce_note"]


def test_backtest_reproduce_after_price_change_no_match() -> None:
    """frozen freeze 를 다른 가격 데이터 instance 로 reproduce → matches=False.

    baseline 은 원본 가격으로 산출됐는데, 재실행 instance 의 가격이 다르면
    equity_curve 가 달라져 matches=False (데이터 변화 감지 — §2.10).
    """
    # 1. 원본 instance — 단조 증가 가격으로 백테스트 + freeze.
    prices, mcaps = _records()
    app1 = create_app(
        stocks_repository=FakeStocksMasterRepository(records=[_stock(_CODE)]),
        price_repository=FakePriceRepository(records=prices),
        market_cap_repository=FakeMarketCapRepository(records=mcaps),
    )
    with TestClient(app1) as c1:
        run = c1.post("/api/backtest", json=_body()).json()

    # 2. 가격을 변경한 다른 instance 로 reproduce — 동일 freeze, 다른 데이터.
    #    마지막 분기 종가만 낮춰 수익률 곡선 자체를 바꾼다(비례 스케일링은 수익률
    #    불변이라 matches 유지 — 수익률을 바꾸려면 비비례 변경 필요).
    changed_prices: list[PriceRecord] = []
    for p in prices:
        factor = (
            Decimal("0.5") if p.effective_date == _Q_DATES[-1] else Decimal(1)
        )
        changed_prices.append(PriceRecord(
            id=p.id, code=p.code, code_lineage_id=p.code_lineage_id,
            effective_date=p.effective_date,
            open_raw=p.close_raw * factor, high_raw=p.close_raw * factor,
            low_raw=p.close_raw * factor, close_raw=p.close_raw * factor,
            volume=p.volume, trading_value=p.trading_value,
            close_adjusted=p.close_adjusted * factor, citation_id=p.citation_id,
            created_at=p.created_at,
        ))
    app2 = create_app(
        stocks_repository=FakeStocksMasterRepository(records=[_stock(_CODE)]),
        price_repository=FakePriceRepository(records=changed_prices),
        market_cap_repository=FakeMarketCapRepository(records=mcaps),
    )
    with TestClient(app2) as c2:
        repro = c2.post(
            "/api/backtest/reproduce",
            json={"freeze": run["freeze"], "equity_curve": run["equity_curve"]},
        )
    assert repro.status_code == 200, repro.text
    out = repro.json()
    assert out["matches"] is False


# =============================================================================
# 6b. engine_version 세대 진단 (M1) — benign 세대 bump vs data-drift 구분
# =============================================================================

def test_backtest_freeze_includes_engine_version(client: TestClient) -> None:
    """freeze 에 산식 세대 식별자(engine_version)가 self-contained 운반됨(M1)."""
    from app.services.backtest_engine import BACKTEST_ENGINE_VERSION

    run = client.post("/api/backtest", json=_body()).json()
    assert run["freeze"]["engine_version"] == BACKTEST_ENGINE_VERSION


def test_backtest_reproduce_engine_version_superseded_note(client: TestClient) -> None:
    """freeze 세대가 현재와 다르고 결과 불일치 → engine_version_superseded=True +
    benign 세대 note(데이터 드리프트 note 아님). M1 핵심 — 산식 수정으로 인한
    불일치를 변조/드리프트와 구분.
    """
    run = client.post("/api/backtest", json=_body()).json()
    freeze = dict(run["freeze"])
    freeze["engine_version"] = "0.9-superseded"  # 현재(1.1)와 다른 옛 세대.
    # baseline 변조로 matches=False 강제(세대 다른 옛 run 재현 상황 시뮬).
    tampered = [dict(p) for p in run["equity_curve"]]
    tampered[0]["value"] = str(Decimal(tampered[0]["value"]) + Decimal("0.5"))
    repro = client.post(
        "/api/backtest/reproduce",
        json={"freeze": freeze, "equity_curve": tampered},
    )
    assert repro.status_code == 200, repro.text
    out = repro.json()
    assert out["matches"] is False
    assert out["engine_version_superseded"] is True
    # benign 세대 note — "산식 세대" 언급(데이터 드리프트 note 와 구분).
    assert "산식 세대" in out["reproduce_note"]


def test_backtest_reproduce_same_version_mismatch_is_data_drift_note(
    client: TestClient,
) -> None:
    """세대 동일 + 결과 불일치 → engine_version_superseded=False + 데이터 드리프트
    note(세대 note 아님). 분기 구분이 정확함을 대조 검증.
    """
    run = client.post("/api/backtest", json=_body()).json()
    # engine_version 은 그대로(현재와 동일) 두고 baseline 만 변조.
    tampered = [dict(p) for p in run["equity_curve"]]
    tampered[0]["value"] = str(Decimal(tampered[0]["value"]) + Decimal("0.5"))
    repro = client.post(
        "/api/backtest/reproduce",
        json={"freeze": run["freeze"], "equity_curve": tampered},
    )
    assert repro.status_code == 200, repro.text
    out = repro.json()
    assert out["matches"] is False
    assert out["engine_version_superseded"] is False
    # 세대 동일 → 데이터 변화 note(기존), 세대 note 아님.
    assert "frozen batch 이후 데이터" in out["reproduce_note"]


def test_backtest_reproduce_legacy_freeze_without_engine_version(
    client: TestClient,
) -> None:
    """engine_version 필드 부재(구 freeze) → superseded=False(하위호환)."""
    run = client.post("/api/backtest", json=_body()).json()
    freeze = dict(run["freeze"])
    freeze.pop("engine_version", None)  # 구 freeze 시뮬 — 필드 제거.
    repro = client.post(
        "/api/backtest/reproduce",
        json={"freeze": freeze, "equity_curve": run["equity_curve"]},
    )
    assert repro.status_code == 200, repro.text
    out = repro.json()
    # 동일 데이터라 matches=True, 세대 미상이라 superseded=False.
    assert out["matches"] is True
    assert out["engine_version_superseded"] is False


def test_backtest_reproduce_match_wins_over_superseded_note(client: TestClient) -> None:
    """matches=True + 세대 다름 → 성공 note 우선(note 분기), superseded=True 동반(독립).

    note 텍스트 우선순위(성공 > 세대) 검증 + 구조화 flag 가 matches 와 독립으로
    truthful 함을 엔드포인트 레벨에서 확인(oracle L-1).
    """
    run = client.post("/api/backtest", json=_body()).json()
    freeze = dict(run["freeze"])
    freeze["engine_version"] = "0.9-superseded"  # 세대 다름.
    # baseline 그대로(동일 데이터) → 분할 없어 matches=True.
    repro = client.post(
        "/api/backtest/reproduce",
        json={"freeze": freeze, "equity_curve": run["equity_curve"]},
    )
    assert repro.status_code == 200, repro.text
    out = repro.json()
    assert out["matches"] is True
    # note 는 성공(우선), flag 는 세대 다름(독립 truthful).
    assert "재현 성공" in out["reproduce_note"]
    assert out["engine_version_superseded"] is True


def test_backtest_reproduce_tamper_note_wins_over_superseded(client: TestClient) -> None:
    """pack 변조 + 세대 다름 → 변조 note 우선(alarming 가림 방지), superseded 동반(L-2)."""
    run = client.post("/api/backtest", json=_body()).json()
    freeze = dict(run["freeze"])
    freeze["engine_version"] = "0.9-superseded"  # 세대 다름.
    dv = dict(freeze["data_versions"])
    dv["factor_pack_content_hash"] = "sha256:" + "f" * 64  # 변조.
    freeze["data_versions"] = dv
    repro = client.post(
        "/api/backtest/reproduce",
        json={"freeze": freeze, "equity_curve": run["equity_curve"]},
    )
    assert repro.status_code == 200, repro.text
    out = repro.json()
    assert out["matches"] is False
    assert out["pack_tampered"] is True
    # 변조 note 우선(세대 note 가 alarming 변조 경고를 가리면 안 됨).
    assert "content_hash 불일치" in out["reproduce_note"]
    # 세대 flag 도 함께 truthful 하게 전달.
    assert out["engine_version_superseded"] is True


# =============================================================================
# 7. pack_tampered 구조화 bool 필드 — M5 #3b / ADR-0033
# =============================================================================

def test_backtest_reproduce_pack_tampered_false_on_success(
    client: TestClient,
) -> None:
    """정상 round-trip reproduce → pack_tampered=False (변조 없음, M5 #3b)."""
    run = client.post("/api/backtest", json=_body()).json()
    repro = client.post(
        "/api/backtest/reproduce",
        json={"freeze": run["freeze"], "equity_curve": run["equity_curve"]},
    )
    assert repro.status_code == 200, repro.text
    out = repro.json()
    assert out["matches"] is True
    # 정상 재현 → pack_tampered=False.
    assert out["pack_tampered"] is False


def test_backtest_reproduce_pack_tampered_true_on_hash_mismatch(
    client: TestClient,
) -> None:
    """frozen content_hash 변조 → pack_tampered=True + matches=False (M5 #3b).

    pack_tampered=True 는 content_hash 불일치(변조)만 — 단순 부재와 구분.
    client #6 use-시점 표시가 "변조" 배지를 정확히 렌더하는 전제(ADR-0033).
    """
    run = client.post("/api/backtest", json=_body()).json()
    freeze = dict(run["freeze"])
    dv = dict(freeze["data_versions"])
    # frozen content_hash 변조 → 재로드 pack 과 불일치.
    dv["factor_pack_content_hash"] = "sha256:" + "f" * 64
    freeze["data_versions"] = dv
    repro = client.post(
        "/api/backtest/reproduce",
        json={"freeze": freeze, "equity_curve": run["equity_curve"]},
    )
    assert repro.status_code == 200, repro.text
    out = repro.json()
    assert out["matches"] is False
    # content_hash 불일치 = 변조 → pack_tampered=True.
    assert out["pack_tampered"] is True


def test_backtest_reproduce_pack_tampered_false_on_data_change(
    client: TestClient,
) -> None:
    """데이터 변화로 인한 matches=False → pack_tampered=False (변조 아님, M5 #3b).

    pack 자체는 정상 재로드됐으나 equity_curve baseline 이 다름 — 변조 아님.
    """
    run = client.post("/api/backtest", json=_body()).json()
    tampered_curve = [dict(p) for p in run["equity_curve"]]
    tampered_curve[0]["value"] = str(Decimal(tampered_curve[0]["value"]) + Decimal("0.5"))
    repro = client.post(
        "/api/backtest/reproduce",
        json={"freeze": run["freeze"], "equity_curve": tampered_curve},
    )
    assert repro.status_code == 200, repro.text
    out = repro.json()
    assert out["matches"] is False
    # equity_curve 불일치(데이터 변화)는 pack 변조 아님 — pack_tampered=False.
    assert out["pack_tampered"] is False
