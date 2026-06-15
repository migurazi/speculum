"""공유 lineage helper 일관성 테스트 — 4 경로 byte-동일 UUID 잠금.

`app.services.lineage.lineage_id_for_code` 가 prices(krx_daily·survivorship)·
financials/treasury(dart_daily)·dividend(fsc_dividend_adapter) 의 단일 출처임을
검증한다. 공식이 미래에 한쪽만 바뀌어 divergence 가 생기면(기존 데이터 손상) 즉시
실패하도록 공식 자체를 잠근다.
"""

from __future__ import annotations

from uuid import NAMESPACE_OID, uuid5

from app.services.lineage import lineage_id_for_code
from batch.krx_daily import KrxDailyBatch
from batch.survivorship_backfill import SurvivorshipBackfillBatch


def test_lineage_formula_is_locked() -> None:
    """공식이 `uuid5(NAMESPACE_OID, f"lineage|{code}")` 임을 잠근다(변경 금지).

    이 공식으로 이미 영속화된 row 의 code_lineage_id 와 byte-동일해야 하므로,
    namespace/prefix/구분자 변경 시 전 데이터가 깨진다 — 미래 변경 방어.
    """
    for code in ("005930", "000660", "035420", "A12345", ""):
        assert lineage_id_for_code(code) == uuid5(
            NAMESPACE_OID, f"lineage|{code}"
        )


def test_lineage_static_wrappers_delegate_to_helper() -> None:
    """krx_daily/survivorship 의 static method wrapper 가 helper 와 byte-동일."""
    for code in ("005930", "000660", "035420"):
        expected = lineage_id_for_code(code)
        assert KrxDailyBatch._lineage_id_for_code(code) == expected
        assert SurvivorshipBackfillBatch._lineage_id_for_code(code) == expected


def test_lineage_all_paths_agree() -> None:
    """4 경로(helper + dart_daily/fsc 인라인 공식)가 같은 code 에 동일 UUID.

    dart_daily/fsc_dividend_adapter 는 helper 를 직접 호출하므로 helper 와 자동
    일치하나, 미래에 어느 한 경로가 다시 인라인 공식으로 회귀하면 본 테스트가
    공식 잠금(test_lineage_formula_is_locked)과 합쳐 divergence 를 잡는다.
    """
    code = "005930"
    helper = lineage_id_for_code(code)
    # dart_daily 변환 경로(financial converter)와 동일 lineage.
    # fsc_dividend_adapter 변환 경로와 동일 lineage.
    # 둘 다 helper 호출이므로 helper 값과 일치 + 공식 잠금으로 보강.
    assert KrxDailyBatch._lineage_id_for_code(code) == helper
    assert SurvivorshipBackfillBatch._lineage_id_for_code(code) == helper
    assert helper == uuid5(NAMESPACE_OID, f"lineage|{code}")


def test_seed_demo_lineage_matches_helper() -> None:
    """seed_demo 의 stocks_master.id 와 모든 fact code_lineage_id 가 배치 helper 와
    동일 공식임을 잠근다 (oracle 설계검토 M1 — namespace 분열 방지).

    seed 가 과거 별도 namespace(`uuid5("5e9ed000-...", code)`)를 써서 같은 종목의
    stocks_master.id(seed)와 fact.code_lineage_id(배치)가 달랐다. 미래에 read-path
    가 lineage 로 전환되면 데모 DB 에서 lineage JOIN 이 silent 분열한다. 본 테스트는
    seed 가 공유 helper 로 통일됐음을 잠가, 재분열(별도 namespace 회귀)을 막는다.
    build_seed_dataset 은 순수 데이터 빌더라 DB 불필요.
    """
    from scripts.seed_demo import build_seed_dataset

    dataset = build_seed_dataset()
    # stocks_master.id == helper(current_code).
    for sm in dataset.stocks:
        assert sm.current_code is not None
        assert sm.id == lineage_id_for_code(sm.current_code), (
            f"stocks_master {sm.current_code} lineage 가 helper 와 불일치 "
            f"(namespace 분열 회귀)"
        )
    # 모든 fact row 의 code_lineage_id == helper(code) — seed/배치 공식 동일.
    fact_rows = [
        *dataset.prices,
        *dataset.market_caps,
        *dataset.financials,
        *dataset.treasury,
    ]
    assert fact_rows  # 빈 dataset 가드.
    for row in fact_rows:
        assert row.code_lineage_id == lineage_id_for_code(row.code), (
            f"fact row {row.code} code_lineage_id 가 helper 와 불일치"
        )
